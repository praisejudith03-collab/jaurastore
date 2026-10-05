"""Per-product bulk discounts: configurable, automatic, and visible.

An optional bulkQty + bulkPercent on each product (admin editor, Inventory
section) takes that product's unit price down automatically at checkout when
the customer orders MORE than bulkQty units of that one product - all its
variants combined. Shop-wide tiers (growth settings) still apply to products
without their own discount. The customer must SEE that the discount applied:
the checkout summary, the order-complete screen and the stored order payload
all carry the lines.

Run with:  python3 -m pytest tests/test_bulk_discount.py -q
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

EMAIL = "jaurastore@gmail.com"
COLOR_OPTS = [{"title": "Colour", "type": "COLOR", "values": ["Red", "Black"]}]


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    execute("DELETE FROM rate_limits")
    execute("DELETE FROM orders WHERE id LIKE 'JA-BLK%'")
    with app.test_client() as c:
        yield c


def csrf(client):
    return client.get("/api/config").get_json()["csrf"]


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def make_product(pid, price=1000, stock=50, bulk_qty=None, bulk_percent=None,
                 option_stock=None):
    rec = {"id": pid, "sku": pid.upper().replace("-", ""), "slug": pid,
           "name": "Bulk Test " + pid, "category": "beauty", "priceNgn": price,
           "stock": stock, "online": True}
    if option_stock is not None:
        rec["options"] = COLOR_OPTS
        rec["optionStock"] = option_stock
        rec["stock"] = sum(option_stock.values())
    if bulk_qty is not None:
        rec["bulkQty"] = bulk_qty
    if bulk_percent is not None:
        rec["bulkPercent"] = bulk_percent
    saved, _action, _mirrored = catalog_mod.upsert(rec, "tester")
    assert saved is not None
    return saved


def place(client, oid, items, currency="NGN"):
    execute("DELETE FROM rate_limits WHERE action='order'")
    total = sum(int(i.get("price") or 1000) * int(i.get("qty") or 1) for i in items)
    body = {"id": oid, "currency": currency, "total": total,
            "customer": {"name": "Bulk Tester", "email": "bulk@example.com",
                         "phone": "+2348012345678", "city": "Lagos",
                         "zone": "Lagos Mainland", "address": "1 Test St"},
            "items": items}
    return client.post("/api/orders", json=body,
                       headers={"X-CSRF-Token": csrf(client)})


def _set_global_tiers(tiers):
    import growth
    s = growth.settings()
    s["bulkDiscountTiers"] = tiers
    growth.save_settings(s, "tester")


# ------------------------------------------------------------ normalisation
def test_bulk_fields_survive_a_save_and_clean_up():
    make_product("jau-blk-clean", bulk_qty=10, bulk_percent=15)
    row = next(p for p in catalog_mod.merged(include_hidden=True)
               if p["id"] == "jau-blk-clean")
    assert row["bulkQty"] == 10 and row["bulkPercent"] == 15
    # both values or neither
    make_product("jau-blk-half", bulk_qty=10)
    row = next(p for p in catalog_mod.merged(include_hidden=True)
               if p["id"] == "jau-blk-half")
    assert not row.get("bulkQty") and not row.get("bulkPercent")


def test_bulk_percent_is_capped_to_sane_values():
    make_product("jau-blk-cap", bulk_qty=10, bulk_percent=95)
    row = next(p for p in catalog_mod.merged(include_hidden=True)
               if p["id"] == "jau-blk-cap")
    assert row["bulkPercent"] == 90
    make_product("jau-blk-cap2", bulk_qty=0, bulk_percent=15)
    row = next(p for p in catalog_mod.merged(include_hidden=True)
               if p["id"] == "jau-blk-cap2")
    assert not row.get("bulkQty") and not row.get("bulkPercent")


def test_public_catalog_carries_the_bulk_offer(client):
    make_product("jau-blk-pub", bulk_qty=10, bulk_percent=15)
    r = client.get("/api/catalog")
    assert r.status_code == 200
    row = next(p for p in r.get_json()["products"] if p["id"] == "jau-blk-pub")
    # the OFFER is public (the customer must see it); the numbers here are
    # pricing, not stock counts.
    assert row["bulkQty"] == 10 and row["bulkPercent"] == 15


# ------------------------------------------------------------- enforcement
def test_discount_applies_only_above_the_threshold(client):
    make_product("jau-blk-th", price=1000, bulk_qty=10, bulk_percent=15)
    at = place(client, "JA-BLK001", [{"id": "jau-blk-th", "name": "X", "qty": 10,
                                      "price": 1000}])
    assert at.status_code == 200
    body = at.get_json()
    # exactly 10 is NOT more than 10: full price (response price = line total)
    assert body["items"][0]["price"] == 1000 * 10
    assert not body.get("bulkDiscount")

    over = place(client, "JA-BLK002", [{"id": "jau-blk-th", "name": "X", "qty": 11,
                                        "price": 1000}])
    assert over.status_code == 200
    body = over.get_json()
    assert body["items"][0]["price"] == 850 * 11   # 15% off 1000, per line
    assert body["items"][0]["bulkPercent"] == 15
    assert body["total"] == 850 * 11
    assert body["bulkDiscount"] == [{"id": "jau-blk-th", "name": "Bulk Test jau-blk-th",
                                     "qty": 11, "percent": 15}]


def test_variants_combined_cross_the_threshold_together(client):
    """6 Red + 6 Black of one product with bulkQty 10: both lines are
    discounted, because the customer ordered 12 of that PRODUCT."""
    make_product("jau-blk-var", price=2000, bulk_qty=10, bulk_percent=20,
                 option_stock={"Red": 30, "Black": 30})
    r = place(client, "JA-BLK003", [
        {"id": "jau-blk-var", "name": "X", "qty": 6, "price": 2000, "color": "Red"},
        {"id": "jau-blk-var", "name": "X", "qty": 6, "price": 2000, "color": "Black"},
    ])
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    # 20% off 2000 per unit, per line
    assert all(i["price"] == 1600 * 6 for i in body["items"])
    assert body["bulkDiscount"][0]["qty"] == 12


def test_per_product_discount_overrides_the_shop_wide_tiers(client):
    _set_global_tiers([{"minQuantity": 5, "percent": 10}])
    try:
        make_product("jau-blk-own", price=1000, bulk_qty=10, bulk_percent=15)
        make_product("jau-blk-global", price=1000)
        own = place(client, "JA-BLK004", [{"id": "jau-blk-own", "name": "X", "qty": 12,
                                           "price": 1000}])
        assert own.status_code == 200
        assert own.get_json()["items"][0]["price"] == 850 * 12    # 15, not 10
        assert own.get_json()["bulkDiscount"][0]["percent"] == 15

        glob = place(client, "JA-BLK005", [{"id": "jau-blk-global", "name": "X", "qty": 6,
                                            "price": 1000}])
        assert glob.status_code == 200
        assert glob.get_json()["items"][0]["price"] == 900 * 6    # shop-wide 10%
        assert glob.get_json()["bulkDiscount"][0]["percent"] == 10
    finally:
        _set_global_tiers([])


def test_order_payload_and_admin_view_carry_the_discount(client):
    make_product("jau-blk-payload", price=1000, bulk_qty=10, bulk_percent=15)
    r = place(client, "JA-BLK006", [{"id": "jau-blk-payload", "name": "X", "qty": 12,
                                     "price": 1000}])
    assert r.status_code == 200
    row = one("SELECT payload FROM orders WHERE id=?", ("JA-BLK006",))
    payload = json.loads(row["payload"])
    assert payload["bulkDiscount"] == [{"id": "jau-blk-payload",
                                        "name": "Bulk Test jau-blk-payload",
                                        "qty": 12, "percent": 15}]
    assert payload["items"][0]["bulkPercent"] == 15
    # the admin list folds the payload back out
    tok = login(client)
    admin = client.get("/api/admin/orders?q=JA-BLK006", headers={"X-CSRF-Token": tok})
    assert admin.status_code == 200
    orders = admin.get_json()["orders"]
    match = next(o for o in orders if o["id"] == "JA-BLK006")
    assert match["bulkDiscount"][0]["percent"] == 15


def test_discount_applies_at_exact_stock_and_over_limit_is_rejected(client):
    """Bulk discounts do not weaken exact product inventory limits."""
    make_product("jau-blk-stock", price=1000, stock=10, bulk_qty=5, bulk_percent=30)
    exact = place(client, "JA-BLK007", [{"id": "jau-blk-stock", "name": "X", "qty": 10,
                                         "price": 1000}])
    assert exact.status_code == 200, exact.get_json()
    assert exact.get_json()["items"][0]["bulkPercent"] == 30
    row = next(p for p in catalog_mod.merged(include_hidden=True)
               if str(p.get("id")) == "jau-blk-stock")
    assert max(0, int(row.get("stock") or 0)) == 0
    empty = place(client, "JA-BLK009", [{"id": "jau-blk-stock", "name": "X", "qty": 1,
                                         "price": 1000}])
    assert empty.status_code == 409, empty.get_json()
    assert empty.get_json()["error"] == "Out of Stock"

    make_product("jau-blk-over", price=1000, stock=10, bulk_qty=5, bulk_percent=30)
    over = place(client, "JA-BLK008", [{"id": "jau-blk-over", "name": "X", "qty": 11,
                                        "price": 1000}])
    assert over.status_code == 409
    assert over.get_json()["error"] == "Only 10 items remaining in stock"
    untouched = next(p for p in catalog_mod.merged(include_hidden=True)
                     if str(p.get("id")) == "jau-blk-over")
    assert untouched["stock"] == 10


# --------------------------------------------------------- storefront wiring
def test_store_js_prices_per_product_bulk_first():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "js", "store.js"), encoding="utf-8").read()
    assert "function bulkPercentFor(p, qty)" in src
    # the cart line carries the applied percentage so every screen can show it
    assert "bulkPercent: bulkPct" in src
    assert "bulkPercentFor(p, totalQty)" in src


def test_simplified_product_editor_hides_legacy_per_product_discount_fields():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "js", "admin.js"), encoding="utf-8").read()
    start = src.index("function productForm(p = {})")
    end = src.index("async function handleProductSubmit", start)
    product_form = src[start:end]
    assert 'name="bulkQty"' not in product_form
    assert 'name="bulkPercent"' not in product_form
    # Shop-wide bulk tiers remain a separate Growth setting, not a product
    # metadata field that makes the create/edit form harder to maintain.
    assert 'name="bulkMin"' in src and 'name="bulkPercent"' in src


def test_customer_screens_show_the_applied_discount():
    """Cart, checkout summary and the order-complete table label the discount
    when it applies - the customer must never wonder why the price dropped.

    The PDP no longer advertises the offer in an informational box (owner
    request 2026-10-05: the PDP carries title/price/gallery/qty/actions only),
    but the discount itself is untouched: it still lands on the cart line and
    on every money screen that shows the reduced price.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = open(os.path.join(root, "js", "app.js"), encoding="utf-8").read()
    assert "bulk-tag" in app                                    # cart + checkout + receipt
    assert "bulkPercent" in app
    assert "pdp-bulk" not in app, "the PDP bulk advert box must stay purged"
    store = open(os.path.join(root, "js", "store.js"), encoding="utf-8").read()
    assert "bulkDiscountTiers" in store                          # tiers still read live
