"""Stock must survive an Admin save - the client sends both aliases.

The production defect: the admin product form used to send only the legacy
``stock`` key next to the row's STALE ``stock_quantity`` (the payload spread
``...(existing || {})``). The server prefers ``stock_quantity``
(catalog.normalize), so the freshly typed quantity was discarded and every
seed product reverted to 24 after a save. The fix is client-side only: the
payload now ships ``stock_quantity: stock`` next to ``stock`` (the server
preference is deliberately untouched - it is tested elsewhere), and
js/store.js upsertProduct keeps the two aliases in lock-step for every other
caller.

Run with:  python3 -m pytest tests/test_admin_stock_payload.py -q
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
os.environ.setdefault("SITE_CONFIG_PATH", "/tmp/jaura_test_site_stock.json")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = "jaurastore@gmail.com"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def admin(app):
    """A logged-in admin client with the CSRF token attached."""
    init_db()
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        r = c.post("/api/admin/login", json={"email": EMAIL, "password": PW})
        assert r.status_code == 200, r.data
        tok = r.get_json()["csrf"]

        class _A:
            def post(self, url, **kw):
                kw.setdefault("headers", {})
                kw["headers"].setdefault("X-CSRF-Token", tok)
                return c.post(url, **kw)

        yield _A()


def test_server_round_trip_keeps_the_typed_stock(admin):
    """The fixed admin-form payload: stock and stock_quantity travel TOGETHER
    (the server prefers stock_quantity), so the typed quantity must come back
    on both aliases and the next save starts from it too."""
    payload = {
        "product": {
            "id": "jau-stock-payload", "name": "Stock Payload Test",
            "priceNgn": 8000, "category": "beauty",
            # what the FIXED admin form sends: both aliases, in sync
            "stock": 7, "stock_quantity": 7,
        }
    }
    r = admin.post("/api/admin/products", json=payload)
    assert r.status_code == 200, r.data
    product = r.get_json()["product"]
    assert product["stock_quantity"] == 7, product.get("stock_quantity")
    assert product["stock"] == 7, product.get("stock")


def test_server_stock_quantity_preference_is_not_flipped(admin):
    """The bug lives in the CLIENT (stale stock_quantity travelled next to
    fresh stock), not in the server's preference. This pins the server
    behaviour the fix relies on: when the aliases disagree, stock_quantity
    wins - so shipping them in sync is what keeps the typed value."""
    payload = {
        "product": {
            "id": "jau-stock-pref", "name": "Stock Pref Test",
            "priceNgn": 8000, "category": "beauty",
            "stock": 7, "stock_quantity": 24,  # the OLD buggy payload shape
        }
    }
    r = admin.post("/api/admin/products", json=payload)
    assert r.status_code == 200, r.data
    product = r.get_json()["product"]
    assert product["stock_quantity"] == 24, product.get("stock_quantity")
    assert product["stock"] == 24, product.get("stock")


def test_admin_js_payload_ships_stock_quantity_next_to_stock():
    """Payload guard: js/admin.js handleProductSubmit must send
    stock_quantity: stock in the same object that carries stock, so the
    server's stock_quantity preference can never pick up the row's stale
    value again."""
    src = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    payload = re.search(
        r"JA\.upsertProduct\(\{(.*?)\n  \}\)", src, re.S)
    assert payload, "could not find the upsertProduct payload in js/admin.js"
    body = payload.group(1)
    assert re.search(r"\bstock,\s*\n\s*//", body) or "stock," in body, \
        "the payload must still carry `stock`"
    assert re.search(r"\bstock_quantity:\s*stock\b", body), \
        "the payload must carry `stock_quantity: stock` next to `stock`"


def test_store_js_upsert_keeps_both_stock_aliases_in_sync():
    """js/store.js upsertProduct mirrors catalog.normalize: whichever alias
    a caller sets, the other is written to the same value before the row is
    queued, so a partial payload can never smuggle a stale alias to the
    server."""
    src = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    m = re.search(
        r"function upsertProduct\(p\) \{(.*?)if \(Number\(next\.priceNgn\)",
        src, re.S)
    assert m, "could not find upsertProduct in js/store.js"
    body = m.group(1)
    assert "next.stock_quantity != null" in body and "next.stock = next.stock_quantity" in body, \
        "a caller-supplied stock_quantity must win (server preference)"
    assert "next.stock_quantity = next.stock" in body, \
        "a caller-supplied stock must be mirrored onto stock_quantity"
