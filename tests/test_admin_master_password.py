"""ADMIN_MASTER_PASSWORD: the primary admin-portal credential.

Pins the contract of the dedicated master password on the environment-backed
auth model:

  * when configured it logs in to the admin portal (password-only body and
    explicit email) for any configured admin identity - no database involved;
  * a NEW value saved in the host dashboard works on the very next attempt
    (the environment is re-read on every attempt - no restart, no database
    update);
  * ADMIN_BOOTSTRAP_PASSWORD stays valid as the secondary/permanent
    fallback, on its own and alongside the master password;
  * wrong attempts keep the identical no-enumeration 401 and stay
    rate-limited;
  * the master password opens a session that passes require_admin gating
    (session validation across admin operations).

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
# no admin credential may leak in from the outer environment
os.environ.pop("ADMIN_MASTER_PASSWORD", None)
os.environ.pop("ADMIN_BOOTSTRAP_PASSWORD", None)

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402

EMAIL = "jaurastore@gmail.com"
MASTER = "MasterPass2026"
BOOTSTRAP = "BootPass2026x"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture(autouse=True)
def _clean_credentials(monkeypatch):
    """Every test starts with no configured credential unless it sets one."""
    monkeypatch.delenv("ADMIN_MASTER_PASSWORD", raising=False)
    monkeypatch.delenv("ADMIN_BOOTSTRAP_PASSWORD", raising=False)


@pytest.fixture()
def client(app):
    from db import execute
    execute("DELETE FROM rate_limits")   # login tests share the rate limiter
    with app.test_client() as c:
        yield c


def _login(client, password, email=None):
    body = {"password": password}
    if email:
        body["email"] = email
    return client.post("/api/admin/login", json=body)


# ------------------------------------------------------------------- login
def test_master_password_logs_in(client, monkeypatch):
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    r = _login(client, MASTER)                    # password-only body
    assert r.status_code == 200, r.data
    assert r.get_json()["email"] == EMAIL
    assert client.get("/api/admin/session").get_json()["authenticated"] is True


def test_master_password_works_with_explicit_email(client, monkeypatch):
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    assert _login(client, MASTER, email=EMAIL).status_code == 200


def test_master_password_session_passes_admin_gating(client, monkeypatch):
    """The session a master-password login opens is a fully privileged admin
    session: it reaches require_admin-protected operations."""
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    assert _login(client, MASTER).status_code == 200
    r = client.get("/api/admin/stock")
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True


def test_unknown_email_is_rejected_even_with_the_master_password(client, monkeypatch):
    """The master password opens a CONFIGURED admin account only - it is
    never an account-creation or enumeration vector."""
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    r = _login(client, MASTER, email="attacker@example.com")
    assert r.status_code == 401
    assert r.get_json()["error"] == "Invalid email or password."


def test_wrong_password_keeps_the_identical_no_enumeration_401(client, monkeypatch):
    """A wrong password and an unknown email answer identically - nothing
    about the response reveals which one was wrong."""
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", BOOTSTRAP)
    wrong_pw = _login(client, "WrongMaster9")
    unknown_email = _login(client, MASTER, email="nobody@example.com")
    assert wrong_pw.status_code == unknown_email.status_code == 401
    assert (wrong_pw.get_json()["error"] ==
            unknown_email.get_json()["error"] ==
            "Invalid email or password.")


def test_master_password_attempts_are_rate_limited(client, monkeypatch):
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    codes = [_login(client, "WrongMasterGuess%d" % i).status_code for i in range(7)]
    assert 429 in codes, f"brute force was not throttled: {codes}"


# ------------------------------------------- immediate effect, no DB updates
def test_changing_the_master_password_takes_effect_immediately(client, monkeypatch):
    """The whole point of ADMIN_MASTER_PASSWORD: update it in the Render
    dashboard and the new value works on the next attempt - no restart, no
    database update (the credential is read from the environment on every
    attempt)."""
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", "FirstMaster77")
    assert _login(client, "FirstMaster77").status_code == 200

    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", "SecondMaster88")
    assert _login(client, "FirstMaster77").status_code == 401, \
        "the OLD master password still worked after the variable changed"
    assert _login(client, "SecondMaster88").status_code == 200


def test_unset_master_password_rejects_it(client, monkeypatch):
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", BOOTSTRAP)
    assert _login(client, MASTER).status_code == 401
    assert _login(client, BOOTSTRAP).status_code == 200


# --------------------------------------------------- bootstrap fallback
def test_bootstrap_password_works_alone_and_alongside_the_master(client, monkeypatch):
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", BOOTSTRAP)
    assert _login(client, BOOTSTRAP).status_code == 200     # on its own

    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    assert _login(client, MASTER).status_code == 200        # primary
    assert _login(client, BOOTSTRAP).status_code == 200     # fallback intact


def test_master_password_beats_a_conflicting_bootstrap_value(client, monkeypatch):
    """When both are set, the master password is the primary credential."""
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", MASTER)
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", BOOTSTRAP)
    assert _login(client, MASTER).status_code == 200
    assert _login(client, BOOTSTRAP).status_code == 200


def test_no_credential_configured_rejects_everything(client):
    assert not authmod.admin_password_configured()
    assert _login(client, MASTER).status_code == 401
    assert _login(client, BOOTSTRAP).status_code == 401


# ------------------------------------------------------------- unit level
def test_unit_level_helpers(monkeypatch):
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", "  SpacedMaster1  ")
    assert authmod.master_password() == "SpacedMaster1"    # trimmed
    assert authmod.master_password_matches("SpacedMaster1") is True
    assert authmod.master_password_matches("spacedmaster1") is False
    assert authmod.master_password_matches("") is False
    monkeypatch.delenv("ADMIN_MASTER_PASSWORD")
    assert authmod.master_password() == ""
    assert authmod.master_password_matches("anything") is False
    assert authmod.bootstrap_password() == ""
    # verify_login never leaves the identity gate
    assert authmod.verify_login(EMAIL, "SpacedMaster1") is False
    assert authmod.verify_login("attacker@example.com", "SpacedMaster1") is False
