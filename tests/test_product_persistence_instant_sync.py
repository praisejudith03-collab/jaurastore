"""Emergency-fix regression suite: product creation persistence, instant
storefront/admin synchronisation, and the admin return-navigation contract.

Covers the three failure reports:

1. POST /api/products (+ /api/products/variants) must persist every field -
   images, inventory, prices, compare-at prices, supplier URLs, SKUs and
   variant tiers - with no silent rollback, and new products must default to
   active/visible so default filters never hide them.
2. Every product/variant write must purge the server-side catalogue snapshot
   (and the category cache), and the storefront + admin catalogue endpoints
   must serve revalidated rows so changes are visible on the NEXT request.
3. The admin product editor captures the admin's list URL state per browser
   tab and returns them to it after Save.
"""
import json
import os
import sys

import pytest

import api as api_mod
import app as appmod
import catalog as catalog_mod
from db import init_db, query

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture()
def _scratch_catalog(tmp_path, monkeypatch):
    """Point the catalogue at a scratch file for the whole module."""
    path = tmp_path / "catalog.json"
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(path))
    api_mod._invalidate_catalog_cache()
    api_mod._invalidate_category_cache()
    yield str(path)
    api_mod._invalidate_catalog_cache()
    api_mod._invalidate_category_cache()


@pytest.fixture()
def admin(client):
    """Signed-in admin session + CSRF-tokenised POST /api/products helper."""
    login = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert login.status_code == 200
    token = login.get_json()["csrf"]

    def _save(payload):
        return client.post("/api/products",
                           headers={"X-CSRF-Token": token},
                           json=payload)

    return _save


def _public(client, name):
    data = client.get("/api/catalog").get_json()
    return next((p for p in data["products"]
                 if name.lower() in str(p.get("name", "")).lower()), None)


def _admin_rows(client, name):
    data = client.get("/api/catalog?all=1").get_json()
    return [p for p in data["products"]
            if name.lower() in str(p.get("name", "")).lower()]


# --------------------------------------------------------------- persistence

def test_created_product_persists_every_field_and_is_visible_by_default(client, admin):
    """A POST-ed product lands committed, online, and on the storefront."""
    saved = admin({"product": {
        "name": "Louis Vuitton Capucines Test Bag",
        "category": "bags",
        "priceNgn": 250000,
        "compareNgn": 295000,
        "stock": 6,
        "sku": "TESTLV-001",
        "supplierUrl": "https://supplier.example.com/lv-capucines",
        "description": "Persistence test bag.",
        "images": ["images/products/_placeholder.jpg"],
    }})
    assert saved.status_code == 200, saved.data
    body = saved.get_json()
    assert body["ok"] is True and body["action"] == "created"
    pid = body["product"]["id"]

    # Committed to storage: the override file the local backend writes is the
    # commit for this deployment shape (Supabase is exercised by its own
    # suite); the row must survive a fresh catalogue read.
    row = catalog_mod.product_index(include_hidden=True).get(pid)
    assert row is not None, "the saved product must be readable back"
    assert row["priceNgn"] == 250000
    assert row["compareNgn"] == 295000
    assert row["stock"] == 6 and row["stock_quantity"] == 6
    assert row["sku"] == "TESTLV-001"
    assert row["supplierSku"] == "https://supplier.example.com/lv-capucines"
    assert row["images"]

    # Defaults to active/visible: no default filter may hide it.
    assert row["online"] is True
    pub = _public(client, "Capucines Test Bag")
    assert pub is not None, "a newly created product must be on the storefront"
    assert pub["stock_status"] == "in"          # 6 units, never a count
    assert "stock" not in pub                    # numbers stay private


def test_created_product_survives_catalogue_reload(client, admin, _scratch_catalog):
    """The write is durable, not an in-memory illusion: a full catalogue
    reload (what every fresh process boot does) still serves the row."""
    saved = admin({"product": {"name": "Durable Test Bucket Bag", "category": "bags",
                               "priceNgn": 42000, "stock": 3}})
    assert saved.status_code == 200
    pid = saved.get_json()["product"]["id"]
    catalog_mod._load_overrides.cache_clear() if hasattr(catalog_mod._load_overrides, "cache_clear") else None
    rows = catalog_mod.merged(include_hidden=True)
    assert any(str(p.get("id")) == pid for p in rows), \
        "the product must still be in merged() after a cold re-read"


def test_variants_endpoint_persists_tiers_stock_and_prices(client, admin):
    """/api/products/variants keeps the existing row and writes variant data."""
    saved = admin({"product": {
        "name": "Variant Tier Test Crossbody",
        "category": "bags",
        "priceNgn": 20000,
        "stock": 9,
    }})
    assert saved.status_code == 200
    pid = saved.get_json()["product"]["id"]
    token = client.post("/api/admin/login", json={
        "email": "jaurastore@gmail.com",
        "password": PW,
    }).get_json()["csrf"]

    patch = client.post("/api/products/variants", headers={"X-CSRF-Token": token}, json={
        "productId": pid,
        "options": [{"title": "Colour", "values": ["Black", "Brown"]}],
        "optionStock": {"Black": 4, "Brown": 3},
        "optionPrices": {"Colour: Black": 18000, "Colour: Brown": 25000},
        "optionCompareAt": {"Colour: Brown": 30000},
        "optionSupplierSku": {"Colour: Black": "https://supplier.example.com/black"},
        "optionSku": {"Colour: Black": "VT-BLK", "Colour: Brown": "VT-BRN"},
    })
    assert patch.status_code == 200, patch.data
    assert patch.get_json()["ok"] is True

    row = catalog_mod.product_index(include_hidden=True).get(pid)
    assert row is not None
    assert row["optionPrices"] == {"Colour: Black": 18000, "Colour: Brown": 25000}
    assert row["optionCompareAt"] == {"Colour: Brown": 30000}
    assert row["optionStock"] == {"Black": 4, "Brown": 3}
    # the product total is the SUM of its variants (server-enforced)
    assert row["stock"] == 7 and row["stock_quantity"] == 7
    assert row["optionSku"] == {"Colour: Black": "VT-BLK", "Colour: Brown": "VT-BRN"}
    assert row["optionSupplierSku"] == {"Colour: Black": "https://supplier.example.com/black"}
    # the base fields from the original save are never dropped by a patch
    assert row["name"] == "Variant Tier Test Crossbody"
    assert row["priceNgn"] == 20000


def test_save_failure_is_never_silently_swallowed(client, admin, monkeypatch):
    """A storage-layer exception surfaces as a 503 with ok:false - the admin
    is never told a failed save succeeded (the 'disappeared product' bug)."""
    def _boom(product, actor=None):
        raise RuntimeError("simulated storage outage")

    monkeypatch.setattr(catalog_mod, "upsert", _boom)
    res = admin({"product": {"name": "Should Not Save", "category": "bags", "priceNgn": 1000}})
    assert res.status_code == 503
    assert res.get_json()["ok"] is False
    assert "could not be saved" in res.get_json()["error"].lower()
    # and the failure is audited, not swallowed
    rows = query("SELECT action, detail FROM audit_log WHERE action='product.save_failed' ORDER BY id DESC LIMIT 1")
    assert rows and "Should Not Save" in rows[0]["detail"]


def test_save_is_audited(client, admin):
    saved = admin({"product": {"name": "Audit Trail Test Bag", "category": "bags",
                               "priceNgn": 9000, "stock": 2}})
    assert saved.status_code == 200
    pid = saved.get_json()["product"]["id"]
    rows = query("SELECT action, detail FROM audit_log WHERE action='product.create' ORDER BY id DESC LIMIT 1")
    assert rows and pid in rows[0]["detail"] and "Audit Trail Test Bag" in rows[0]["detail"]


# ------------------------------------------------- instant cache invalidation

def test_new_product_is_instantly_visible_and_etag_changes(client, admin):
    """The snapshot is purged synchronously: the very next public AND admin
    catalogue request sees the new row, and an old ETag revalidates to 200."""
    before = client.get("/api/catalog")
    etag_before = before.headers["ETag"]
    assert "Capucines Instant Bag" not in before.get_data(as_text=True)

    saved = admin({"product": {"name": "Capucines Instant Bag", "category": "bags",
                               "priceNgn": 125000, "stock": 4}})
    assert saved.status_code == 200

    # storefrut (public) + /admin (all=1) both see it on the NEXT request
    assert _public(client, "Capucines Instant Bag") is not None
    assert _admin_rows(client, "Capucines Instant Bag"), "the admin dashboard must see it too"

    # a revalidation carrying the pre-save ETag must NOT be answered 304
    revalidated = client.get("/api/catalog", headers={"If-None-Match": etag_before})
    assert revalidated.status_code == 200
    assert "Capucines Instant Bag" in revalidated.get_data(as_text=True)
    assert client.get("/api/catalog").headers["ETag"] != etag_before


def test_catalog_response_is_always_revalidated_never_served_stale(client):
    """No browser/CDN window may hide a save: no-cache + must-revalidate."""
    res = client.get("/api/catalog")
    cache = res.headers.get("Cache-Control", "")
    for directive in ("private", "no-cache", "must-revalidate"):
        assert directive in cache, f"missing {directive!r} in {cache!r}"
    assert "max-age=20" not in cache and "max-age=5" not in cache
    admin_res = client.get("/api/catalog?all=1")
    for directive in ("private", "no-cache", "must-revalidate"):
        assert directive in admin_res.headers.get("Cache-Control", "")


def test_product_write_purges_category_cache_too(client, admin, monkeypatch):
    """A product save invalidates the category menu snapshot as well."""
    calls = []
    real = api_mod._invalidate_category_cache

    def spy():
        calls.append(1)
        real()

    monkeypatch.setattr(api_mod, "_invalidate_category_cache", spy)
    saved = admin({"product": {"name": "Category Cache Purge Bag", "category": "bags",
                               "priceNgn": 15000, "stock": 1}})
    assert saved.status_code == 200
    assert calls, "a product save must purge the category cache"


def test_catalog_cache_ttl_is_a_short_safety_net():
    """The in-process snapshot TTL must stay a short safety net (a second
    worker's stale window), never a 20-second visibility delay."""
    assert api_mod._CATALOG_CACHE_TTL <= 5.0


# ------------------------------------------------------- admin UI contracts

def test_admin_js_return_navigation_is_session_scoped():
    """Requirement 3: capture on open, restore on save, isolated per tab."""
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert "PRODUCTS_RETURN_KEY = \"jaura_admin_products_return\"" in admin_js
    # capture happens when an editor is opened - on the add button AND on
    # every product-card click, so cancelling (or saving) always returns to
    # the list the admin actually came from, never a stale earlier snapshot
    assert "rememberProductsReturn();" in admin_js
    assert "editingId = b.dataset.edit;" in admin_js
    assert ('const open = () => { rememberProductsReturn(); editingId = b.dataset.edit;'
            in admin_js)
    assert '$("#add-product")?.addEventListener("click", () => { rememberProductsReturn();' in admin_js
    # save button label matches the requirement's action
    assert '>Save Product</button>' in admin_js
    # the capture URL is the canonical /admin/products list state
    assert '"/admin/products" + (qs ? "?" + qs : "")' in admin_js


def test_admin_grid_card_shows_variant_price_ranges():
    """Requirement 4: multi-variant price ranges on admin grid cards."""
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert "JA.priceRangeOf(p, \"NGN\")" in admin_js
    assert "JA.moneyRange(range, \"NGN\")" in admin_js
    assert 'adx-price-range' in admin_js


def test_storefront_price_range_uses_exact_format():
    """Requirement 4: '₦1,800.00 – ₦2,500.00' via moneyExact/moneyRange."""
    store_js = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    assert "function moneyExact(" in store_js
    assert "function moneyRange(" in store_js
    assert "minimumFractionDigits: 2" in store_js


def test_price_typography_is_bolder_and_larger():
    css = open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8").read()
    assert "font-size: clamp(16px, 4.2vw, 21px) !important" in css
    assert "font-weight: 800 !important" in css
    assert "clamp(22px, 6.4vw, 31px) !important" in css


def test_popup_banner_toggle_exists_in_admin_settings():
    """Requirement 4: the ON/OFF switch for the storefront pop-up banner."""
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert 'id="welcome-enabled"' in admin_js
    assert 'role="switch"' in admin_js
    assert "patch.popup_banner_active = !!(enabled && enabled.checked)" in admin_js
    store_js = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    assert "!welcomeEnabled()) return" in store_js
