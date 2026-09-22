"""F CFA conversion rounding: Naira is exact, CFA is rounded UP to 50 / 100.

Naira (₦) is the BASE currency and is never modified. F CFA is CONVERTED from
Naira for display, cart totals and checkout, and every converted amount must
land on a clean 50 / 100 step:

    24 CFA -> 50 CFA     (anything below 50 rounds UP to 50)
    64 CFA -> 100 CFA    (anything above 50 rounds UP to the next 100)

The ceiling is not just a display rule - it is the whole money path:

  * catalog._clean_cfa_prices rounds every served priceCfa / compareCfa /
    optionPricesCfa UP (merged() AND base_products(), because GET
    /api/products bypasses merged()), while an independently priced CFA
    figure is only ever rounded, never re-derived from Naira;
  * api._checkout_items passes a bulk-discounted CFA unit through
    round_cfa, so the server bills the same 1,950 the browser shows for
    2,150 at -10% (the raw maths say 1,935);
  * js/store.js bulkUnit applies the identical roundCfa (proved by the
    Node simulation tests/_cfa_rounding_sim.mjs).

Run with:  python3 -m pytest tests/test_cfa_rounding.py -q
"""
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest  # noqa: E402

import currency  # noqa: E402
import catalog as catalog_mod  # noqa: E402


# --------------------------------------------------------------- round_cfa
@pytest.mark.parametrize("raw,expected", [
    (0, 0),
    (1, 50),
    (24, 50),          # the reported case: below 50 rounds UP to 50
    (49, 50),
    (50, 50),          # already a clean step - untouched
    (51, 100),
    (64, 100),         # the reported case: above 50 rounds UP to 100
    (99, 100),
    (100, 100),
    (101, 150),
    (149, 150),
    (150, 150),
    (1013, 1050),
    (4400, 4400),
])
def test_cfa_amounts_always_land_on_a_50_or_100_step(raw, expected):
    assert currency.round_cfa(raw) == expected


def test_round_cfa_never_raises_and_never_goes_negative():
    assert currency.round_cfa(None) == 0
    assert currency.round_cfa("not a number") == 0
    assert currency.round_cfa(-500) == 0


def test_every_rounded_amount_is_a_multiple_of_the_step():
    for naira in range(0, 5000, 7):
        amount = currency.to_cfa(naira)
        assert amount % currency.CFA_STEP == 0, naira


def test_conversion_only_ever_rounds_up():
    for naira in range(1, 3000, 13):
        exact = naira * currency.NGN_TO_CFA
        assert currency.to_cfa(naira) >= exact
        assert currency.to_cfa(naira) - exact < currency.CFA_STEP


# --------------------------------------------------------- Naira is exact
def test_naira_is_the_base_currency_and_is_never_rounded():
    """A Naira price is stored and served EXACTLY as the admin typed it."""
    for odd in (1, 37, 999, 1013, 24999):
        product = catalog_mod.normalize({"id": "cfa-test", "name": "Test",
                                         "priceNgn": odd})
        assert product["priceNgn"] == odd


def test_cfa_derived_from_naira_is_a_clean_step():
    product = catalog_mod.normalize({"id": "cfa-test", "name": "Test",
                                     "priceNgn": 1013})
    assert product["priceCfa"] % currency.CFA_STEP == 0
    assert product["priceCfa"] == currency.to_cfa(1013)


def test_an_explicit_odd_cfa_price_is_snapped_to_a_clean_step():
    product = catalog_mod.normalize({"id": "cfa-test", "name": "Test",
                                     "priceCfa": 24})
    assert product["priceCfa"] == 50


def test_compare_at_cfa_price_is_also_rounded():
    product = catalog_mod.normalize({"id": "cfa-test", "name": "Test",
                                     "priceNgn": 1013, "compareNgn": 2027})
    assert product["compareNgn"] == 2027            # Naira untouched
    assert product["compareCfa"] % currency.CFA_STEP == 0


# ------------------------------------------- checkout / cart server pricing
def test_server_unit_price_in_cfa_is_always_a_clean_step():
    import api

    product = {"id": "p1", "priceNgn": 1013}
    price = api._server_unit_price(product, "CFA")
    assert price % currency.CFA_STEP == 0
    assert price == currency.to_cfa(1013)


def test_server_unit_price_in_naira_is_the_exact_base_amount():
    import api

    assert api._server_unit_price({"id": "p1", "priceNgn": 1013}, "NGN") == 1013


def test_option_variation_prices_round_in_cfa_too():
    import api

    product = {"id": "p1", "priceNgn": 1000,
               "optionPrices": {"Large": 1013}}
    price = api._server_unit_price(product, "CFA", variant="Large")
    assert price % currency.CFA_STEP == 0
    assert price == currency.to_cfa(1013)


def test_the_browser_rounding_matches_the_server():
    """js/store.js must implement the identical 50/100 ceiling."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source = open(os.path.join(root, "js", "store.js"), encoding="utf-8").read()
    assert "function roundCfa" in source
    assert "Math.ceil(v / CFA_STEP) * CFA_STEP" in source
    assert "const CFA_STEP = 50;" in source


# ============================================== the catalogue read-time ceiling
# catalog.merged() and catalog.base_products() must serve every F CFA figure
# already ceiled: the browser ceilings whatever it renders (js/store.js
# roundCfa), so a raw odd amount makes the two sides disagree on the price of
# the same piece. GET /api/products serves base_products() directly and
# bypasses merged() entirely, so BOTH read paths clean.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_BY_ID = {p["id"]: p for p in json.load(
    open(os.path.join(ROOT, "data", "seed.json"), encoding="utf-8"))}


@pytest.fixture(scope="module")
def app():
    import app as appmod
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    from db import execute, init_db
    init_db()
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def _bulk_product(pid="jau-cfa-bulk"):
    """4,800 NGN -> 2,150 F CFA at the house rate; -10% above 2 units."""
    return {"id": pid, "sku": pid.upper().replace("-", ""), "slug": pid,
            "name": "CFA Bulk " + pid, "category": "beauty",
            "priceNgn": 4800, "stock": 50, "online": True,
            "bulkQty": 2, "bulkPercent": 10}


def _order(client, oid, pid, currency="CFA", qty=3, promo=""):
    from db import execute
    execute("DELETE FROM orders WHERE id=?", (oid,))
    execute("DELETE FROM rate_limits WHERE action='order'")
    body = {
        "id": oid, "currency": currency, "total": 1,
        "customer": {"name": "CFA Buyer", "phone": "+2348012345678",
                     "email": "ceil@example.com", "city": "Lagos",
                     "zone": "Lagos Mainland", "address": "1 Test Street"},
        "items": [{"id": pid, "name": "Item", "qty": qty, "price": 1}],
    }
    if promo:
        body["promoCode"] = promo
    csrf = client.get("/api/config").get_json()["csrf"]
    return client.post("/api/orders", json=body,
                       headers={"X-CSRF-Token": csrf})


# ------------------------------------------------- the reported odd amounts
@pytest.mark.parametrize("raw,expected", [
    (924, 950),      # 2,100 NGN at the house 0.44 rate
    (2112, 2150),    # 4,800 NGN
    (11440, 11450),  # 26,000 NGN
    (1935, 1950),    # 2,150 F CFA at -10%: the raw bulk maths
    (5580, 5600),    # a discounted total floor_cfa once took to 5,550
])
def test_the_reported_odd_amounts_all_round_up(raw, expected):
    assert currency.round_cfa(raw) == expected


# ------------------------------------------------- _clean_cfa_prices itself
@pytest.mark.parametrize("raw,expected", [
    (325, 350),   # wix-144 exactly as it ships in data/seed.json
    (680, 700),   # wix-212 exactly as it ships in data/seed.json
    (1013, 1050),
])
def test_clean_cfa_prices_rounds_an_odd_price_up(raw, expected):
    out = catalog_mod._clean_cfa_prices({"id": "p", "priceCfa": raw})
    assert out["priceCfa"] == expected


def test_clean_cfa_prices_rounds_the_compare_at_price_up():
    out = catalog_mod._clean_cfa_prices({"id": "p", "priceCfa": 1000,
                                         "compareCfa": 2112})
    assert out["compareCfa"] == 2150


def test_clean_cfa_prices_rounds_every_option_price_up():
    out = catalog_mod._clean_cfa_prices(
        {"id": "p", "priceCfa": 1000,
         "optionPricesCfa": {"Small": 924, "Large": 11440, "Odd": 325}})
    assert out["optionPricesCfa"] == {"Small": 950, "Large": 11450,
                                      "Odd": 350}


def test_clean_cfa_prices_never_touches_the_naira_fields():
    out = catalog_mod._clean_cfa_prices({"id": "p", "priceNgn": 1013,
                                         "compareNgn": 2027, "priceCfa": 924,
                                         "compareCfa": 2112})
    assert out["priceNgn"] == 1013
    assert out["compareNgn"] == 2027


def test_a_missing_cfa_price_is_derived_from_naira():
    out = catalog_mod._clean_cfa_prices({"id": "p", "priceNgn": 4800})
    assert out["priceCfa"] == 2150            # to_cfa(4800), a clean step


def test_an_existing_cfa_price_is_rounded_never_rederived():
    """wix-005 ships at 8,550 NGN but 15,000 F CFA - two independent price
    points. Re-deriving the CFA from the Naira base would re-price the shop
    downward (to_cfa(8550) = 3,800); an existing figure is only rounded."""
    out = catalog_mod._clean_cfa_prices({"id": "wix-005", "priceNgn": 8550,
                                         "priceCfa": 15000})
    assert out["priceCfa"] == 15000
    assert out["priceCfa"] != currency.to_cfa(8550)


def test_a_cfa_only_product_is_never_derived_from_a_missing_naira():
    out = catalog_mod._clean_cfa_prices({"id": "p", "priceCfa": 2112})
    assert out["priceCfa"] == 2150
    assert "priceNgn" not in out


def test_clean_cfa_prices_never_raises_on_odd_input():
    assert catalog_mod._clean_cfa_prices(None) == {}
    assert catalog_mod._clean_cfa_prices({}) == {}
    catalog_mod._clean_cfa_prices({"priceCfa": "x", "optionPricesCfa": None})


# ------------------------------------------- the served catalogue is ceiled
def test_merged_serves_only_clean_cfa_prices():
    for p in catalog_mod.merged(include_hidden=True):
        assert (p.get("priceCfa") or 0) % currency.CFA_STEP == 0, p["id"]


def test_merged_serves_only_clean_compare_at_prices():
    for p in catalog_mod.merged(include_hidden=True):
        was = p.get("compareCfa")
        assert was is None or was % currency.CFA_STEP == 0, p["id"]


def test_base_products_serves_only_clean_cfa_prices():
    products = catalog_mod.base_products()
    assert len(products) >= 250
    for p in products:
        assert (p.get("priceCfa") or 0) % currency.CFA_STEP == 0, p["id"]


def test_the_products_endpoint_serves_only_clean_cfa_prices(client):
    body = client.get("/api/products").get_json()
    assert body["ok"] is True
    for p in body["products"]:
        assert (p.get("priceCfa") or 0) % currency.CFA_STEP == 0, p["id"]


def test_the_products_endpoint_rounds_the_two_odd_seed_prices(client):
    """wix-144 ships as 325 and wix-212 as 680 in data/seed.json; this route
    bypasses merged(), so base_products() has to ceiling them itself."""
    body = client.get("/api/products").get_json()
    by_id = {p["id"]: p for p in body["products"]}
    assert by_id["wix-144"]["priceCfa"] == 350
    assert by_id["wix-212"]["priceCfa"] == 700


def test_wix_005_keeps_its_independent_cfa_price(client):
    """15,000 F CFA is a price point of its own, not to_cfa(8,550 NGN) -
    on the seed route AND in the live catalogue."""
    body = client.get("/api/products").get_json()
    by_id = {p["id"]: p for p in body["products"]}
    assert by_id["wix-005"]["priceNgn"] == 8550
    assert by_id["wix-005"]["priceCfa"] == 15000
    live = next(p for p in catalog_mod.merged(include_hidden=True)
                if p.get("id") == "wix-005")
    assert live["priceCfa"] == 15000


# ------------------------------------------------ the bulk-discount money path
def test_the_server_bills_the_ceiled_bulk_unit_in_cfa(monkeypatch):
    """2,150 F CFA at -10% is 1,935 in the raw maths. The browser shows
    1,950 (roundCfa), so the server must bill 1,950 - this is the money
    path, not just display."""
    import api
    monkeypatch.setattr(catalog_mod, "merged",
                        lambda **kw: [_bulk_product()])
    items, subtotal, err = api._checkout_items(
        [{"id": "jau-cfa-bulk", "qty": 3, "price": 1}], "CFA")
    assert err is None
    assert items[0]["price"] == 1950 * 3
    assert items[0]["bulkPercent"] == 10
    assert subtotal == 5850


def test_every_bulk_percentage_lands_on_a_clean_cfa_unit(monkeypatch):
    import api
    for percent in (5, 7, 10, 15, 20, 33):
        prod = _bulk_product()
        prod["bulkPercent"] = percent
        monkeypatch.setattr(catalog_mod, "merged", lambda **kw: [dict(prod)])
        items, subtotal, err = api._checkout_items(
            [{"id": "jau-cfa-bulk", "qty": 3, "price": 1}], "CFA")
        assert err is None, percent
        unit = items[0]["price"] // 3
        raw = 2150 * (100 - percent) / 100
        assert unit % currency.CFA_STEP == 0, percent
        assert unit >= raw - 1, percent        # never below the maths
        assert unit < raw + currency.CFA_STEP, percent
        assert subtotal == unit * 3


def test_a_naira_bulk_discount_keeps_ordinary_rounding(monkeypatch):
    import api
    monkeypatch.setattr(catalog_mod, "merged", lambda **kw: [_bulk_product()])
    items, _subtotal, err = api._checkout_items(
        [{"id": "jau-cfa-bulk", "qty": 3, "price": 1}], "NGN")
    assert err is None
    # 4,800 NGN at -10% = 4,320 exactly - and an odd base stays Math.round:
    # 1,013 at -10% = 911.7 -> 912, NOT a ceiled 950.
    assert items[0]["price"] == 4320 * 3
    prod = _bulk_product()
    prod["priceNgn"] = 1013
    monkeypatch.setattr(catalog_mod, "merged", lambda **kw: [prod])
    items, _subtotal, err = api._checkout_items(
        [{"id": "jau-cfa-bulk", "qty": 3, "price": 1}], "NGN")
    assert err is None
    assert items[0]["price"] == 912 * 3


def test_a_cfa_order_bills_the_ceiled_bulk_unit(client):
    """The whole money path: the saved product derives 2,150 F CFA from its
    Naira price, and the stored order bills 1,950 per unit over 3 units."""
    saved, _action, _mirrored = catalog_mod.upsert(
        _bulk_product("jau-cfa-bulk-live"), "tester")
    assert saved["priceCfa"] == 2150          # derived at save, clean step
    r = _order(client, "JA-CFACEIL1", "jau-cfa-bulk-live", "CFA", qty=3)
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["subtotal"] == 5850
    assert body["total"] == 5850
    assert body["total"] % currency.CFA_STEP == 0
    assert body["bulkDiscount"][0]["percent"] == 10


def test_a_cfa_order_without_a_bulk_discount_stays_exact(client):
    catalog_mod.upsert(_bulk_product("jau-cfa-bulk-live"), "tester")
    r = _order(client, "JA-CFACEIL2", "jau-cfa-bulk-live", "CFA", qty=2)
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["subtotal"] == 4300           # 2 x 2,150, already clean
    assert body["total"] == 4300
    assert not body.get("bulkDiscount")       # 2 is not MORE than bulkQty 2


def test_a_percentage_discount_lands_on_a_clean_cfa_total(client):
    """7% off a 5,850 F CFA basket: the raw maths leave 5,440, which is not
    a clean step - the ceiling sends the total to 5,450, and the stored
    discount is restated from it so the receipt adds up."""
    from db import execute
    execute("DELETE FROM coupons WHERE code='CFACEIL'")
    execute("DELETE FROM coupon_uses WHERE code='CFACEIL'")
    execute("INSERT INTO coupons (code, percent, kind, active, max_uses) "
            "VALUES ('CFACEIL', 7, 'promo', 1, NULL)")
    catalog_mod.upsert(_bulk_product("jau-cfa-bulk-live"), "tester")
    r = _order(client, "JA-CFACEIL3", "jau-cfa-bulk-live", "CFA", qty=3,
               promo="CFACEIL")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["promo"]["code"] == "CFACEIL"
    assert body["subtotal"] == 5850
    assert body["total"] == 5450
    assert body["total"] % currency.CFA_STEP == 0
    assert body["discount"] == 400
    assert body["subtotal"] - body["discount"] == body["total"]


# ------------------------------------------------------- deprecated alias
def test_floor_cfa_is_a_deprecated_alias_for_round_cfa():
    """The old name used to round discounted totals DOWN (floor_cfa(5580)
    was 5,550). There is one contract now: every F CFA amount goes UP."""
    assert currency.floor_cfa(5580) == 5600
    assert currency.floor_cfa(5000) == 5000
    assert currency.floor_cfa(0) == 0
    assert currency.floor_cfa(-10) == 0
    assert currency.floor_cfa("x") == 0
    for value in range(0, 3000, 7):
        assert currency.floor_cfa(value) == currency.round_cfa(value)
        assert currency.floor_cfa(value) >= value


# ------------------------------------------------ the browser side (Node sim)
def test_the_browser_simulation_agrees():
    """tests/_cfa_rounding_sim.mjs boots the real js/store.js and proves the
    browser ceilings the identical figures (roundCfa, toCfa, priceOf,
    compareOf, bulkUnit - Naira keeps Math.round)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    result = subprocess.run([node, "tests/_cfa_rounding_sim.mjs"], cwd=ROOT,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All F CFA ceiling checks passed" in result.stdout


# ------------------------------------------------------- Naira stays exact
def test_the_seed_naira_prices_survive_cleaning_untouched():
    for p in catalog_mod.base_products():
        seed = SEED_BY_ID[p["id"]]
        assert p["priceNgn"] == seed.get("priceNgn"), p["id"]
        assert p.get("compareNgn") == seed.get("compareNgn"), p["id"]
