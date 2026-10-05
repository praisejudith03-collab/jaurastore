"""Stock availability persistence + the cleaned admin product editor.

An explicit whole-product OFF (stockStatus "out" / is_in_stock false /
a plain ``stock: 0`` patch) zeroes the row AND every variant. Variant-map
writes still determine availability per option. The admin form now has no
separate availability switch: blank, absent and zero quantities are zero, and
variant totals determine the product quantity.

These tests pin the backend availability contract, immediate public-catalog
visibility, and the reduced editor field set while preserving its custom-note,
supplier-link and variant-stock workflows.
"""
import os
import sys

import pytest

import api as api_mod
import app as appmod
import catalog as catalog_mod

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
    from db import init_db
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture()
def _scratch_catalog(tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(path))
    api_mod._invalidate_catalog_cache()
    api_mod._invalidate_category_cache()
    yield str(path)
    api_mod._invalidate_catalog_cache()
    api_mod._invalidate_category_cache()


@pytest.fixture()
def admin(client):
    login = client.post("/api/admin/login",
                        json={"email": EMAIL, "password": PW})
    assert login.status_code == 200
    token = login.get_json()["csrf"]

    def _save(payload):
        return client.post("/api/products",
                           headers={"X-CSRF-Token": token}, json=payload)

    def _variants(patch):
        return client.put("/api/products/variants",
                          headers={"X-CSRF-Token": token}, json=patch)

    return _save, _variants


def _admin_js():
    with open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8") as fh:
        return fh.read()


def _public(client, pid):
    data = client.get("/api/catalog").get_json()
    return next((p for p in data["products"] if p.get("id") == pid), None)


VARIANT_PRODUCT = {
    "id": "jau-oos-toggle",
    "name": "OOS Toggle Bag",
    "priceNgn": 9000,
    "stock": 5,
    "online": True,
    "category": "bags",
    "options": [{"title": "Colour", "values": ["Noir", "Rouge"]}],
    "optionStock": {"Noir": 2, "Rouge": 3},
    "optionPrices": {"Noir": 9000, "Rouge": 11000},
}


# ------------------------------------------------------- catalog.normalize

def test_explicit_out_of_stock_switch_beats_the_variant_sum():
    """stockStatus 'out' zeroes the row and every variant: the per-variant
    sum can never re-stock a row the admin switched off."""
    clean = catalog_mod.normalize({
        **VARIANT_PRODUCT,
        "stock": 0, "stock_quantity": 0, "stockStatus": "out"})
    assert clean["stock"] == 0 and clean["stock_quantity"] == 0
    assert clean["optionStock"] == {"Noir": 0, "Rouge": 0}


def test_api_spellings_of_the_availability_switch_are_recognised():
    for spelling in (
            {"stockStatus": "out"},
            {"stock_status": "out"},
            {"stockStatus": "Sold Out"},
            {"is_in_stock": False},
            {"in_stock": 0},
            {"is_in_stock": "false"},
    ):
        row = {**VARIANT_PRODUCT, "stock": 0, "stock_quantity": 0, **spelling}
        clean = catalog_mod.normalize(row)
        assert clean["stock"] == 0, spelling
        assert set(clean["optionStock"].values()) == {0}, spelling


def test_no_switch_means_no_opinion():
    """Absent / true / 'in' flags never force anything: availability keeps
    following the quantity rules (the variant sum, or the typed quantity)."""
    for spelling in ({}, {"stockStatus": "in"}, {"stock_status": "in"},
                     {"is_in_stock": True}, {"in_stock": 1}):
        row = {**VARIANT_PRODUCT, "stock": 0, "stock_quantity": 0, **spelling}
        clean = catalog_mod.normalize(row)
        # variant truth: 2 + 3
        assert clean["stock"] == 5, spelling
        assert clean["optionStock"] == {"Noir": 2, "Rouge": 3}, spelling


def test_variant_sum_invariant_is_untouched_without_the_switch():
    clean = catalog_mod.normalize({
        **VARIANT_PRODUCT,
        "stock": 0, "stock_quantity": 0,
        "optionStock": {"Noir": 0, "Rouge": 4}})
    assert clean["stock"] == 4


def test_plain_zero_quantity_without_variants_stays_zero():
    clean = catalog_mod.normalize({
        "id": "jau-plain-zero", "name": "Plain Zero", "priceNgn": 500,
        "stock": 0, "stock_quantity": 0})
    assert clean["stock"] == 0
    # an absent/unassigned quantity is also zero, never an invented default.
    fresh = catalog_mod.normalize({"name": "Fresh", "priceNgn": 500})
    assert fresh["stock"] == 0


# ------------------------------------------------- /api/products/variants

def test_variants_endpoint_stock_zero_switches_the_whole_product_off(
        client, _scratch_catalog, admin):
    _save, _variants = admin
    r = _save({"product": VARIANT_PRODUCT})
    assert r.status_code == 200, r.get_data(as_text=True)

    # THE reported bug: a plain stock: 0 patch must commit, not be re-summed
    r = _variants({"productId": "jau-oos-toggle", "stock": 0})
    assert r.status_code == 200, r.get_data(as_text=True)
    row = r.get_json()["product"]
    assert row["stock"] == 0 and row["stock_quantity"] == 0
    assert set(row["optionStock"].values()) == {0}

    # the public row flips to out (badge + buy button), instantly
    pub = _public(client, "jau-oos-toggle")
    assert pub["stock_status"] == "out"
    assert set(pub["option_stock_status"].values()) == {"out"}
    # the public row still never leaks the numbers
    for key in ("stock", "stock_quantity", "optionStock"):
        assert key not in pub


def test_variants_endpoint_is_in_stock_false_switches_off(
        client, _scratch_catalog, admin):
    _save, _variants = admin
    assert _save({"product": VARIANT_PRODUCT}).status_code == 200
    r = _variants({"productId": "jau-oos-toggle", "is_in_stock": False})
    assert r.status_code == 200, r.get_data(as_text=True)
    row = r.get_json()["product"]
    assert row["stock"] == 0
    assert set(row["optionStock"].values()) == {0}
    assert _public(client, "jau-oos-toggle")["stock_status"] == "out"


def test_variants_endpoint_stock_status_out_is_forwarded(
        client, _scratch_catalog, admin):
    _save, _variants = admin
    assert _save({"product": VARIANT_PRODUCT}).status_code == 200
    r = _variants({"productId": "jau-oos-toggle", "stockStatus": "out"})
    assert r.status_code == 200
    row = r.get_json()["product"]
    assert row["stock"] == 0
    assert _public(client, "jau-oos-toggle")["stock_status"] == "out"


def test_variants_endpoint_positive_stock_restores_an_all_sold_out_product(
        client, _scratch_catalog, admin):
    """The mirror image: after the switch-off, a plain positive quantity is
    the new availability - the all-zero variant map must not pin the row to
    'out' forever."""
    _save, _variants = admin
    assert _save({"product": VARIANT_PRODUCT}).status_code == 200
    assert _variants({"productId": "jau-oos-toggle", "stock": 0}).status_code == 200
    r = _variants({"productId": "jau-oos-toggle", "stock": 8})
    assert r.status_code == 200, r.get_data(as_text=True)
    row = r.get_json()["product"]
    assert row["stock"] == 8
    assert _public(client, "jau-oos-toggle")["stock_status"] == "in"


def test_variants_endpoint_variant_payload_still_wins(
        client, _scratch_catalog, admin):
    """A caller that manages the per-variant numbers itself decides
    availability per variant - the whole-product inference stays out."""
    _save, _variants = admin
    assert _save({"product": VARIANT_PRODUCT}).status_code == 200
    r = _variants({"productId": "jau-oos-toggle",
                   "stock": 0,
                   "optionStock": {"Noir": 0, "Rouge": 4}})
    assert r.status_code == 200, r.get_data(as_text=True)
    row = r.get_json()["product"]
    assert row["stock"] == 4
    pub = _public(client, "jau-oos-toggle")
    assert pub["stock_status"] == "in"
    assert pub["option_stock_status"] == {"Noir": "out", "Rouge": "in"}


def test_availability_change_is_visible_on_the_next_uncached_read(
        client, _scratch_catalog, admin):
    """The stock flip must be live for the very next visitor: the catalogue
    snapshot is purged and the answer is never cacheable by a CDN."""
    _save, _variants = admin
    assert _save({"product": VARIANT_PRODUCT}).status_code == 200
    assert _variants({"productId": "jau-oos-toggle", "stock": 0}).status_code == 200

    resp = client.get("/api/catalog")
    cc = resp.headers.get("Cache-Control", "")
    assert "no-cache" in cc or "no-store" in cc
    row = next(p for p in resp.get_json()["products"]
               if p.get("id") == "jau-oos-toggle")
    assert row["stock_status"] == "out"
    # the admin view still sees the real numbers it needs to manage stock
    admin_row = next(p for p in client.get("/api/catalog?all=1").get_json()["products"]
                     if p.get("id") == "jau-oos-toggle")
    assert admin_row["stock"] == 0
    assert set(admin_row["optionStock"].values()) == {0}


# ------------------------------------------------- admin editor (js pins)

def test_editor_uses_zero_for_blank_and_unassigned_stock():
    js = _admin_js()
    submit = js[js.index("async function handleProductSubmit"):]
    assert 'Math.max(0, parseInt(num("stock"), 10) || 0)' in submit
    assert 'Math.max(0, parseInt(typedStock[value], 10) || 0)' in submit
    # New products start at zero; editing an option product totals its
    # assigned values rather than inventing a stock default.
    form = js[js.index("function productForm(p = {})"):js.index("async function handleProductSubmit")]
    assert ': (p.id ? Math.max(0, Number(p.stock) || 0) : 0)' in form


def test_clean_editor_has_only_the_approved_product_controls():
    """The product form stays focused on shop essentials. The SKU is one of
    them again, at BOTH levels (one code for the product, one per variant);
    import, discount, translation, visibility, rating and review-entry
    controls are still not part of the editor, and verified reviews remain in
    their separate flow."""
    import re

    js = _admin_js()
    form = js[js.index("function productForm(p = {})"):js.index("async function handleProductSubmit")]
    names = set(re.findall(r'name="([^"]+)"', form))
    assert names == {
        "id", "name", "sku", "description", "category", "priceNgn", "supplierSku",
        "stock", "enableCustomNote", "customNotePrompt",
    }
    for required in (
        "Product title", "SKU (your code for this product)", "Description",
        "Category", "Price (Naira ₦)",
        "Main supplier URL", "Stock quantity", "Enable a note for this product",
        "Customer prompt", "id=\"media-box\"", "id=\"add-opt\"",
    ):
        assert required in form, required
    for removed in (
        "nameFr", "descriptionFr", "dimensions", "compareNgn", "bulkQty",
        "bulkPercent", "stock-status", "stockStatus", "featured", "badge",
        "Customer Name", "Customer Stars", "Customer Review",
    ):
        assert removed not in form, removed
    # Both SKU levels are deliberate: tests/test_editor_sku_and_note_prompt.py
    # pins their behaviour. The variant SKU is its own field, never the
    # supplier URL field that sits next to it.
    assert "function optionSkuHTML" in js
    assert "function currentOptionSku" in js
    assert "function variantPanelsHTML" not in js
    assert "data-opt-supplier" in js
    assert "data-opt-stock" in js


def test_editor_derives_availability_from_stock_and_keeps_variant_map():
    js = _admin_js()
    submit = js[js.index("async function handleProductSubmit"):]
    assert 'stockStatus: stock > 0 ? "in" : "out"' in submit
    assert "optionStock: payloadOptionStock," in submit
    assert "optionSupplierSku," in submit
    # No UI switch can restore stale quantity after an explicit zero.
    assert 'name="stockStatus"' not in js
    assert "window.__editPrevOptionStock" not in js


def test_fresh_admin_login_refetches_the_stock_numbers():
    """The boot fetch runs before the session exists, so a fresh admin first
    sees the public projection, not the complete editable product rows. The
    login handler must refetch the authenticated ?all=1 catalogue before
    painting the desk."""
    js = _admin_js()
    handler = js[js.index('await JA.loginAdmin(loginEmail'):]
    handler = handler[:handler.index("});") + 3]
    assert "await JA.reloadCatalog();" in handler
    assert handler.index("await JA.reloadCatalog();") < handler.index("paintDesk();")
    assert "try { await JA.reloadCatalog(); } catch (e) {}" in handler


def test_normalize_round_trips_every_admin_field():
    """Server side of the field audit: normalize keeps every editable field
    (so what the form ships is what the database stores)."""
    row = catalog_mod.normalize({
        "id": "jau-audit", "name": "Audit Bag", "nameFr": "Sac d'audit",
        "category": "bags", "priceNgn": 7000, "compareNgn": 9000,
        "description": "d", "descriptionFr": "d fr", "dimensions": "30 x 20 cm",
        "badge": "new", "featured": True, "online": True,
        "colors": ["Noir"], "options": [{"title": "Colour", "values": ["Noir"]}],
        "optionStock": {"Noir": 3}, "optionPrices": {"Colour: Noir": 7500},
        "optionCompareAt": {"Colour: Noir": 9500},
        "supplierSku": "https://supplier.example/x",
        "optionSupplierSku": {"Colour: Noir": "https://supplier.example/y"},
        "optionSku": {"Colour: Noir": "NOIR-1"},
        "bulkQty": 10, "bulkPercent": 15,
        "stock": 3, "stock_quantity": 3,
        "images": ["images/products/3in1-towel.jpg"],
    })
    assert row["nameFr"] == "Sac d'audit"
    assert row["category"] == "bags"
    assert row["compareNgn"] == 9000
    assert row["descriptionFr"] == "d fr"
    assert row["dimensions"] == "30 x 20 cm"
    assert row["badge"] == "new"
    assert row["featured"] is True
    assert row["online"] is True
    assert row["colors"] == ["Noir"]
    assert row["optionStock"] == {"Noir": 3}
    assert row["optionPrices"] == {"Colour: Noir": 7500}
    assert row["optionCompareAt"] == {"Colour: Noir": 9500}
    assert row["supplierSku"] == "https://supplier.example/x"
    assert row["optionSupplierSku"] == {"Colour: Noir": "https://supplier.example/y"}
    assert row["optionSku"] == {"Colour: Noir": "NOIR-1"}
    assert row["bulkQty"] == 10 and row["bulkPercent"] == 15
    assert row["stock"] == 3
