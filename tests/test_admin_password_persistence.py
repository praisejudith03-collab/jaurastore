"""The admin password must survive a Render restart.

Render's disk is EPHEMERAL. The `admins` table holding password_hash lives
there, so before this change a redeploy wiped it and `ensure_seed_admins`
re-created the row with a deliberately unusable random hash - locking every
admin out until someone reached a shell. `set_password` mirrored to Supabase
Auth "best effort" and threw the result away, and `verify_login` never
consulted it.

These tests drive the real auth code against a FAKE Supabase (not
FLASK_ENV=testing shortcuts) and simulate the restart by dropping the local
rows, which is what a redeploy actually does.

Run with:  python3 -m pytest tests/test_admin_password_persistence.py -q
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

import auth as authmod  # noqa: E402
import supabase_store  # noqa: E402
from config import Config  # noqa: E402
from db import execute, init_db, one, query  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = "jaurastore@gmail.com"
PW = "Durable2026x"


class FakeAdminUsers:
    """Just enough of the PostgREST builder for the admin_users table."""

    def __init__(self, store, fail=False):
        self.store, self.fail = store, fail

    def upsert(self, payload, on_conflict=None):
        self._payload = payload
        return self

    def update(self, payload):
        self._payload = payload
        self._is_update = True
        return self

    def select(self, cols):
        self._is_select = True
        return self

    def eq(self, col, val):
        self._eq = (col, val)
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("supabase unreachable")
        if getattr(self, "_is_select", False):
            return type("R", (), {"data": list(self.store.values())})()
        if getattr(self, "_is_update", False):
            col, val = self._eq
            for row in self.store.values():
                if row.get(col) == val:
                    row.update(self._payload)
            return type("R", (), {"data": []})()
        email = self._payload["email"]
        self.store[email] = dict(self._payload)
        return type("R", (), {"data": [self.store[email]]})()


class FakeTable:
    def __init__(self, store, fail):
        self.store, self.fail = store, fail

    def upsert(self, *a, **k):
        return FakeAdminUsers(self.store, self.fail).upsert(*a, **k)

    def update(self, *a, **k):
        return FakeAdminUsers(self.store, self.fail).update(*a, **k)

    def select(self, *a, **k):
        return FakeAdminUsers(self.store, self.fail).select(*a, **k)


class FakeClient:
    def __init__(self, store, fail=False):
        self.store, self.fail = store, fail

    def table(self, name):
        assert name == "admin_users", f"unexpected table {name}"
        return FakeTable(self.store, self.fail)


@pytest.fixture()
def sb(monkeypatch):
    """A fake Supabase holding admin_users, with SUPABASE_ENABLED on."""
    store = {}
    fake = FakeClient(store)
    monkeypatch.setattr(Config, "SUPABASE_ENABLED", True)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    init_db()
    execute("DELETE FROM admins")
    authmod.ensure_seed_admins()
    return store


def _simulate_render_restart():
    """What a redeploy does: the ephemeral SQLite rows are gone."""
    execute("DELETE FROM admins")
    assert one("SELECT id FROM admins WHERE email=?", (EMAIL,)) is None


# ------------------------------------------------------------------- writes
def test_set_password_persists_the_hash_to_supabase(sb):
    assert authmod.set_shared_password(PW) is True
    assert EMAIL in sb, "the durable admin_users row was never written"
    stored = sb[EMAIL]["password_hash"]
    assert stored and stored != PW, "stored something that is not a hash"
    # it must be a werkzeug hash, and must verify
    from werkzeug.security import check_password_hash
    assert check_password_hash(stored, PW)


def test_only_a_hash_is_stored_never_the_plaintext(sb):
    authmod.set_shared_password(PW)
    blob = repr(sb)
    assert PW not in blob, "the plaintext password reached Supabase"
    assert "password_hash" in sb[EMAIL]
    assert sb[EMAIL]["password_hash"].startswith(("pbkdf2:", "scrypt:", "argon2"))


def test_a_durable_write_failure_is_reported_not_swallowed(sb, monkeypatch):
    """The change still applies locally, but the caller must be told."""
    monkeypatch.setattr(supabase_store, "client",
                        lambda: FakeClient(sb, fail=True))
    assert authmod.set_shared_password("Another2026x") is True   # old pw is dead
    assert authmod.password_durable_error(), \
        "a failed durable write was silently ignored"
    assert EMAIL not in sb or sb[EMAIL]["password_hash"] != "Another2026x"


def test_the_old_password_dies_even_when_the_durable_write_fails(sb, monkeypatch):
    """The security property the original regression test protects."""
    authmod.set_shared_password(PW)
    monkeypatch.setattr(supabase_store, "client",
                        lambda: FakeClient(sb, fail=True))
    authmod.set_shared_password("Rotated2026x")
    assert authmod.verify_login(EMAIL, "Rotated2026x") is True
    assert authmod.verify_login(EMAIL, PW) is False


# ------------------------------------------------------- surviving a restart
def test_login_survives_a_render_restart(sb):
    authmod.set_shared_password(PW)
    _simulate_render_restart()
    # the local row is gone, but the durable hash still authenticates
    assert authmod.verify_login(EMAIL, PW) is True
    # ... and the ephemeral copy was repaired for later logins
    row = one("SELECT password_hash FROM admins WHERE email=?", (EMAIL,))
    assert row is not None
    from werkzeug.security import check_password_hash
    assert check_password_hash(row["password_hash"], PW)


def test_boot_restores_the_durable_hash_instead_of_a_random_one(sb):
    authmod.set_shared_password(PW)
    _simulate_render_restart()
    authmod.ensure_seed_admins()          # what the app does on boot
    row = one("SELECT password_hash FROM admins WHERE email=?", (EMAIL,))
    from werkzeug.security import check_password_hash
    assert check_password_hash(row["password_hash"], PW), \
        "boot seeded an unusable random hash instead of restoring the password"


def test_a_wrong_password_is_still_rejected_after_a_restart(sb):
    authmod.set_shared_password(PW)
    _simulate_render_restart()
    assert authmod.verify_login(EMAIL, "Wrong2026xxx") is False
    assert authmod.verify_login("nobody@example.com", PW) is False
    assert authmod.verify_login(EMAIL, "") is False


def test_a_disabled_admin_cannot_sign_in(sb):
    authmod.set_shared_password(PW)
    sb[EMAIL]["enabled"] = False
    _simulate_render_restart()
    assert authmod.verify_login(EMAIL, PW) is False


def test_an_unreachable_supabase_does_not_crash_login(sb, monkeypatch):
    authmod.set_shared_password(PW)
    _simulate_render_restart()
    monkeypatch.setattr(supabase_store, "client",
                        lambda: FakeClient(sb, fail=True))
    assert authmod.verify_login(EMAIL, PW) is False   # no local hash, no raise


# ------------------------------------------------------------------- schema
def test_schema_defines_admin_users_with_the_required_columns():
    sql = open(os.path.join(ROOT, "supabase_schema.sql"), encoding="utf-8").read()
    m = re.search(r"create table if not exists admin_users \((.*?)\n\);", sql, re.S)
    assert m, "no admin_users table in supabase_schema.sql"
    cols = {ln.strip().split()[0] for ln in m.group(1).splitlines() if ln.strip()}
    for col in ("id", "email", "password_hash", "role", "enabled",
                "created_at", "updated_at", "last_login_at"):
        assert col in cols, f"admin_users is missing {col}"
    assert "plaintext" not in m.group(1).lower()
