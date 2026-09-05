"""Sync screen pings Supabase for real instead of trusting key presence."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    authmod.ensure_seed_admins()
    authmod.set_password(EMAIL, PW)
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


class _PingQuery:
    def __init__(self, boom=False):
        self.boom = boom

    def select(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def execute(self):
        if self.boom:
            raise RuntimeError("boom")
        return type("R", (), {"data": []})()


class _PingClient:
    def __init__(self, boom=False):
        self.boom = boom

    def table(self, n):
        return _PingQuery(self.boom)


def test_ping_not_configured(monkeypatch):
    import supabase_store as sb
    monkeypatch.setattr(sb, "enabled", lambda: False)
    assert sb.ping() == "not_configured"


def test_ping_ok(monkeypatch):
    import supabase_store as sb
    monkeypatch.setattr(sb, "enabled", lambda: True)
    monkeypatch.setattr(sb, "client", lambda: _PingClient())
    assert sb.ping() == "ok"


def test_ping_unreachable(monkeypatch):
    import supabase_store as sb
    monkeypatch.setattr(sb, "enabled", lambda: True)
    monkeypatch.setattr(sb, "client", lambda: None)
    assert sb.ping() == "unreachable"
    monkeypatch.setattr(sb, "client", lambda: _PingClient(boom=True))
    assert sb.ping() == "unreachable"


def test_sync_status_reports_health(client, monkeypatch):
    import supabase_store as sb
    login(client)
    monkeypatch.setattr(sb, "ping", lambda: "unreachable")
    body = client.get("/api/admin/sync/status").get_json()
    assert body["supabaseHealth"] == "unreachable"
    assert "supabase" in body


def test_admin_js_shows_ok_unreachable_not_configured():
    src = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert "UNREACHABLE" in src
    assert 'health === "ok" ? "OK"' in src
    assert "not configured" in src
