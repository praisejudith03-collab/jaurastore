"""Local-only catalogue rows stay visible and remirror to Supabase.

A product saved on one phone (stretch marks oil) used to vanish from other
devices because merged() ignored local overrides once Supabase answered.
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


def test_merged_includes_local_only_row_when_supabase_configured(monkeypatch):
    import catalog as catalog_mod
    seed = catalog_mod._seed_products()
    sb = [dict(p) for p in seed[:3]]
    local = [{"id": "jau-stretch-oil", "sku": "JAUSTRETCH", "slug": "stretch-marks-oil",
              "name": "Stretch marks oil", "category": "beauty", "priceNgn": 5000, "stock": 8}]
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: sb)
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": local, "deleted": []})
    merged = catalog_mod.merged(include_hidden=True)
    ids = [p["id"] for p in merged]
    assert "jau-stretch-oil" in ids


def test_merged_still_hides_deleted_with_local_union(monkeypatch):
    import catalog as catalog_mod
    seed = catalog_mod._seed_products()
    sb = [dict(p) for p in seed[:4]]
    target = sb[0]["id"]
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: sb)
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [{"id": "jau-keep-me", "sku": "JAUKEEP",
                                               "slug": "keep-me", "name": "Keep me",
                                               "category": "beauty", "priceNgn": 1}],
                                 "deleted": [target]})
    merged = catalog_mod.merged(include_hidden=True)
    ids = [p["id"] for p in merged]
    assert target not in ids
    assert "jau-keep-me" in ids


def test_upsert_reports_mirrored_false_when_supabase_write_fails(monkeypatch):
    import catalog as catalog_mod
    import supabase_store as sb
    monkeypatch.setattr(sb, "enabled", lambda: True)
    monkeypatch.setattr(sb, "upsert_products", lambda rows: False)
    product, action, mirrored = catalog_mod.upsert(
        {"id": "jau-mirror-fail", "name": "X", "priceNgn": 1})
    assert product is not None
    assert mirrored is False


def test_upsert_reports_mirrored_true_when_supabase_not_configured(monkeypatch):
    import catalog as catalog_mod
    import supabase_store as sb
    monkeypatch.setattr(sb, "enabled", lambda: False)
    monkeypatch.setattr(sb, "upsert_products", lambda rows: True)
    product, action, mirrored = catalog_mod.upsert(
        {"id": "jau-mirror-ok", "name": "Y", "priceNgn": 1})
    assert product is not None
    assert mirrored is True


def test_admin_product_post_includes_mirrored(client, monkeypatch):
    import supabase_store as sb
    monkeypatch.setattr(sb, "enabled", lambda: False)
    tok = login(client)
    r = client.post("/api/admin/products", json={"product": {
        "id": "jau-mirror-post", "name": "Mirror Post", "priceNgn": 2000,
        "category": "beauty", "stock": 3,
    }}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert "mirrored" in body
    assert body["mirrored"] is True


def test_remirror_strays_pushes_local_only_products(monkeypatch):
    import catalog as catalog_mod
    import supabase_store as sb
    pushed = []
    seed = catalog_mod._seed_products()
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: [dict(seed[0])])
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [{"id": "jau-stray", "sku": "JAUSTRY",
                                               "slug": "stray-oil", "name": "Stray",
                                               "category": "beauty", "priceNgn": 1}],
                                 "deleted": []})
    monkeypatch.setattr(sb, "enabled", lambda: True)
    monkeypatch.setattr(sb, "upsert_products", lambda rows: pushed.extend(rows) or True)
    n = catalog_mod.remirror_strays()
    assert n == 1
    assert pushed[0]["id"] == "jau-stray"


def test_scheduler_remirrors_strays():
    src = open(os.path.join(ROOT, "scheduler.py"), encoding="utf-8").read()
    assert "remirror_strays" in src


def test_net_js_never_silently_refuses_a_full_queue():
    src = open(os.path.join(ROOT, "js", "net.js"), encoding="utf-8").read()
    enq = src[src.index("function enqueue"):src.index("function drop")]
    assert "return Promise.resolve({ queued: true, full: true })" not in enq
    assert "drop(" in enq


def test_net_js_never_drops_failed_saves_and_boot_keeps_dead():
    src = open(os.path.join(ROOT, "js", "net.js"), encoding="utf-8").read()
    assert "if (!err.retryable) { drop(rec); return null; }" not in src
    assert "rec.dead = true" in src
    boot = src[src.index("function boot()"):src.index("function paintPill()")]
    assert "lsAll" in boot
    assert "!j.dead" not in boot
