"""ADMIN_MASTER_PASSWORD: the primary admin-portal credential.

Pins the contract of the dedicated master password:

  * when configured it logs in to the admin portal (password-only body and
    explicit email) without any database hash being involved;
  * a NEW value saved in the host dashboard works on the very next attempt -
    no restart, and provably no database write (the stored hashes do not
    move);
  * wrong attempts keep the identical no-enumeration 401 and stay
    rate-limited;
  * the master password also satisfies the "current password" check of the
    change-password flow (session validation across admin operations);
  * the account's own database password keeps working alongside it (the
    fallback), and ADMIN_BOOTSTRAP_PASSWORD's one-shot recovery flow is
    untouched.

Run with:  python3 -m pytest tests/test_admin_master_password.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")
# make sure no master password leaks in from the outer environment
os.environ.pop("ADMIN_MASTER_PASSWORD", None)

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
from db import execute, init_db, one  # noqa: E402

EMAIL = "jaurastore@gmail.com"
DB_PW = "DbSharedPass1"
MASTER = "MasterPass2026"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    authmod.ensure_seed_admins()
    authmod.set_password(EMAIL, DB_PW)
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def _login(client, password, email=None):
    body = {"password": password}
    if email:
        body["email"] = email
    return client.post("/api/admin/login", json=body)


def _hashes():
    rows = one("SELECT group_concat(email || ':' || password_hash) AS h "
               "FROM admins ORDER BY email")
    return rows["h"] if rows else ""


# ------------------------------------------------------------------- login
def test_master_password_logs_in_without_any_database_hash(client, monkeypatch):
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    before = _hashes()
    r = _login(client, MASTER)                    # password-only body
    assert r.status_code == 200, r.data
    assert r.get_json()["email"] == EMAIL
    assert client.get("/api/admin/session").get_json()["authenticated"] is True
    assert _hashes() == before, "a master-password login must not rewrite the DB hashes"


def test_master_password_works_with_explicit_email(client, monkeypatch):
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    assert _login(client, MASTER, email=EMAIL).status_code == 200


def test_unknown_email_is_rejected_even_with_the_master_password(client, monkeypatch):
    """The master password opens a CONFIGURED admin account only - it is
    never an account-creation or enumeration vector."""
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    r = _login(client, MASTER, email="attacker@example.com")
    assert r.status_code == 401
    assert r.get_json()["error"] == "Invalid email or password."


def test_wrong_password_keeps_the_identical_no_enumeration_401(client, monkeypatch):
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    wrong_db = _login(client, "NotAPassword1")
    wrong_master = _login(client, "WrongMaster9")
    assert wrong_db.status_code == wrong_master.status_code == 401
    assert (wrong_db.get_json()["error"] ==
            wrong_master.get_json()["error"] ==
            "Invalid email or password.")


def test_master_password_attempts_are_rate_limited(client, monkeypatch):
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    execute("DELETE FROM rate_limits")
    codes = [_login(client, "WrongMasterGuess%d" % i).status_code for i in range(7)]
    assert 429 in codes, f"brute force was not throttled: {codes}"


# ------------------------------------------- immediate effect, no DB updates
def test_changing_the_master_password_takes_effect_immediately(client, monkeypatch):
    """The whole point of ADMIN_MASTER_PASSWORD: update it in the Render
    dashboard and the new value works on the next attempt - no database
    update. Proven here by the stored hashes never moving."""
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", "FirstMaster77")
    before = _hashes()
    assert _login(client, "FirstMaster77").status_code == 200

    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", "SecondMaster88")
    assert _login(client, "FirstMaster77").status_code == 401, \
        "the OLD master password still worked after the variable changed"
    assert _login(client, "SecondMaster88").status_code == 200
    assert _hashes() == before, "changing the master password must never touch the DB"


def test_unset_master_password_keeps_the_normal_database_login(client):
    assert "ADMIN_MASTER_PASSWORD" not in os.environ
    assert _login(client, DB_PW).status_code == 200
    assert _login(client, MASTER).status_code == 401


def test_database_password_still_works_alongside_the_master(client, monkeypatch):
    """The master password is the PRIMARY credential, not the only one: the
    account's own database password (the fallback) keeps signing in."""
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    assert _login(client, DB_PW).status_code == 200
    assert _login(client, MASTER).status_code == 200


# --------------------------------------------------- admin portal operations
def test_master_password_unlocks_the_change_password_flow(client, monkeypatch):
    """Session validation across admin operations: the master password is
    accepted as the 'current password' when changing the shared DB password -
    and changing that password never disables the master password."""
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    assert _login(client, MASTER).status_code == 200
    tok = client.get("/api/config").get_json()["csrf"]
    r = client.post("/api/admin/password",
                    json={"currentPassword": MASTER, "newPassword": "FreshDbPass7"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True
    # the session was cleared: sign back in with the master password...
    assert _login(client, MASTER).status_code == 200
    # ...and the new database password works too
    assert _login(client, "FreshDbPass7").status_code == 200


def test_change_password_rejects_a_wrong_current_password(client, monkeypatch):
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    assert _login(client, MASTER).status_code == 200
    tok = client.get("/api/config").get_json()["csrf"]
    r = client.post("/api/admin/password",
                    json={"currentPassword": "Nope12345", "newPassword": "Whatever9A"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 403


# ---------------------------------------------------- fallbacks stay intact
def test_bootstrap_recovery_flow_is_untouched(client, monkeypatch):
    """ADMIN_BOOTSTRAP_PASSWORD keeps its one-shot behaviour: it forces the
    shared DB password once (when none was chosen), and both the resulting DB
    password and the master password then sign in."""
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    # simulate a database with no password chosen yet
    execute("DELETE FROM growth_settings WHERE key IN "
            "('admin_bootstrap_applied', 'admin_password_set')")
    execute("UPDATE admins SET password_hash='x' WHERE email=?", (EMAIL,))
    assert authmod.apply_bootstrap_password("Bootstrapped9") is True
    assert _login(client, "Bootstrapped9").status_code == 200      # DB password
    assert _login(client, MASTER).status_code == 200               # master password
    # one-shot: a second call changes nothing
    assert authmod.apply_bootstrap_password("TryAgain999") is False


def test_unit_level_master_password_helpers(monkeypatch):
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", "  SpacedMaster1  ")
    assert authmod.master_password() == "SpacedMaster1"    # trimmed
    assert authmod.master_password_matches("SpacedMaster1") is True
    assert authmod.master_password_matches("spacedmaster1") is False
    assert authmod.master_password_matches("") is False
    monkeypatch.delenv("ADMIN_MASTER_PASSWORD")
    assert authmod.master_password() == ""
    assert authmod.master_password_matches("anything") is False
