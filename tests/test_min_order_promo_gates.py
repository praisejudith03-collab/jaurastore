"""Owner request 2026-09-28: the marketing master switches and the editable
Benin & Togo minimum-order rule.

Three promises pinned here:

* The PROMOTIONS master switch (growth setting ``promosEnabled``) completely
  disables coupon redemption and volume/bulk discounts while it is off -
  the checkout refuses every coupon, /api/site serves no discount tiers and
  an order's server total carries no bulk discount - and everything comes
  back untouched when the switch returns to ON. Nothing is ever deleted.
* The minimum-order rule is an ADMIN SETTING (``minOrderCfa``): it can be
  raised, lowered and read on /api/site (as minOrderCfa + its exact Naira
  equivalent), and a saved 0 switches the rule OFF entirely, server-side
  too.
* The storefront pins: the floating currency pill carries NO purple, the
  welcome pop-up entrance has NO rotation, and the promo field on the
  checkout is gated on both programme flags.

Run with:  python3 -m pytest tests/test_min_order_promo_gates.py -q
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

import pytest  # noqa: E402

import api  # noqa: E402
import app as appmod  # noqa: E402
import growth  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Switches are global state: every test hands the house defaults back to the
# files that run after this one (the floors module reads the LIVE setting).
HOUSE_DEFAULTS = {"referralEnabled": 1, "promosEnabled": 1, "minOrderCfa": 5000,
                  "minSpendNgn": 20000, "buyerPercent": 5, "milestone": 2,
                  "bulkDiscountTiers": []}


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c
    growth.save_settings(dict(HOUSE_DEFAULTS))


def csrf(client):
    execute("DELETE FROM rate_limits")
    return client.get("/api/config").get_json()["csrf"]


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def set_growth(client, patch):
    r = client.post("/api/admin/growth/settings",
                    headers={"X-CSRF-Token": login(client)}, json=patch)
    assert r.status_code == 200, r.data
    return r.get_json()["settings"]


def order_body(oid, email, **extra):
    body = {
        "id": oid,
        "currency": "NGN",
        "total": 8550,
        "customer": {"name": "Buyer", "phone": "+2348012345678", "email": email,
                     "city": "Cotonou", "zone": "Cotonou", "country": "Benin",
                     "address": "1 Street"},
        "items": [{"id": "wix-005", "name": "Bag", "qty": 1, "price": 8550}],
    }
    body.update(extra)
    return body


# ------------------------------------------------- the promotions switch
def test_site_publishes_the_promotions_switch_both_ways(client):
    set_growth(client, {"promosEnabled": False})
    site = client.get("/api/site").get_json()["site"]
    assert site["promosEnabled"] is False
    set_growth(client, {"promosEnabled": True})
    site = client.get("/api/site").get_json()["site"]
    assert site["promosEnabled"] is True


def test_coupons_are_refused_while_promotions_are_off(client):
    """ONE switch kills every code: saved coupons stay in the table, but the
    checkout and the promo checker both refuse them until it is ON again."""
    tok = login(client)
    execute("DELETE FROM coupons WHERE code='GATE-10'")
    r = client.post("/api/admin/coupons", headers={"X-CSRF-Token": tok},
                    json={"code": "GATE-10", "percent": 10})
    assert r.status_code == 200
    j = client.post("/api/promo/check", json={"code": "gate-10"}).get_json()
    assert j["ok"] and j["percent"] == 10

    set_growth(client, {"promosEnabled": False})
    j = client.post("/api/promo/check", json={"code": "gate-10"}).get_json()
    assert not j["ok"], "a coupon must not redeem while promotions are OFF"

    set_growth(client, {"promosEnabled": True})
    j = client.post("/api/promo/check", json={"code": "gate-10"}).get_json()
    assert j["ok"] and j["percent"] == 10, "switching back ON restores the coupon"
    client.delete("/api/admin/coupons/GATE-10", headers={"X-CSRF-Token": tok})


def test_discount_tiers_disappear_from_the_site_row_while_off(client):
    tiers = [{"minQuantity": 3, "percent": 5}]
    set_growth(client, {"bulkDiscountTiers": tiers})
    served = client.get("/api/site").get_json()["site"]["bulkDiscountTiers"]
    assert [(t["minQuantity"], t["percent"]) for t in served] == [(3, 5)]
    set_growth(client, {"promosEnabled": False})
    served = client.get("/api/site").get_json()["site"]["bulkDiscountTiers"]
    assert served == [], "promotions OFF must serve NO tier to the storefront"
    set_growth(client, {"promosEnabled": True})
    served = client.get("/api/site").get_json()["site"]["bulkDiscountTiers"]
    assert [(t["minQuantity"], t["percent"]) for t in served] == [(3, 5)], (
        "the tiers come back untouched when the switch returns to ON")
    set_growth(client, {"bulkDiscountTiers": []})


def test_bulk_discount_is_not_priced_while_promotions_are_off(client):
    """The SERVER total carries no per-product bulk discount while the
    switch is off - the browser is never trusted."""
    tok = login(client)
    client.post("/api/admin/products", headers={"X-CSRF-Token": tok}, json={
        "id": "gate-bulk", "name": "Gate Bulk", "priceNgn": 1000,
        "category": "wigs", "stock": 100, "bulkQty": 5, "bulkPercent": 20})
    set_growth(client, {"promosEnabled": False})
    execute("DELETE FROM orders WHERE id='JA-GATE99'")
    body = order_body("JA-MIN-BULK0", "gate@example.com")
    body["id"] = "JA-GATE99"
    # A LAGOS delivery: outside the Benin & Togo floor, so the only thing
    # that could change this total is the bulk discount under test.
    body["customer"].update({"zone": "Lagos Mainland", "city": "Lagos",
                             "country": "Nigeria"})
    body["items"] = [{"id": "gate-bulk", "name": "Gate Bulk", "qty": 10, "price": 1000}]
    r = client.post("/api/orders", headers={"X-CSRF-Token": csrf(client)}, json=body)
    assert r.status_code == 200, r.data
    j = r.get_json()
    assert j["subtotal"] == 10000, (
        f"promotions OFF must bill the full 10 x 1000, got {j['subtotal']}")
    client.delete("/api/admin/products/gate-bulk", headers={"X-CSRF-Token": tok})


def test_the_referral_switch_still_works_independently(client):
    """Referral OFF alone must NOT silence coupons (the pre-existing rule),
    and promotions OFF alone must not touch the referral switch's meaning."""
    set_growth(client, {"promosEnabled": True, "referralEnabled": False})
    execute("DELETE FROM coupons WHERE code='GATE-REF'")
    tok = login(client)
    client.post("/api/admin/coupons", headers={"X-CSRF-Token": tok},
                json={"code": "GATE-REF", "percent": 5})
    j = client.post("/api/promo/check", json={"code": "gate-ref"}).get_json()
    assert j["ok"], "referrals OFF alone must keep the coupon engine alive"
    client.delete("/api/admin/coupons/GATE-REF", headers={"X-CSRF-Token": tok})
    set_growth(client, {"referralEnabled": True})


# ---------------------------------------------- editable minimum order rule
def test_the_minimum_is_served_on_the_site_row(client):
    site = client.get("/api/site").get_json()["site"]
    assert site["minOrderCfa"] == 5000
    assert site["minOrderNgn"] > 0
    set_growth(client, {"minOrderCfa": 8500})
    site = client.get("/api/site").get_json()["site"]
    assert site["minOrderCfa"] == 8500
    assert site["minOrderNgn"] == api.benin_togo_min_ngn(None, 8500)
    set_growth(client, {"minOrderCfa": 0})
    site = client.get("/api/site").get_json()["site"]
    assert site["minOrderCfa"] == 0 and site["minOrderNgn"] == 0
    set_growth(client, {"minOrderCfa": 5000})


def test_the_minimum_can_be_raised_and_lowered(client):
    """8,550 NGN: under the default floor (~11,251 NGN), so the default
    blocks it; a lowered minimum accepts it; a raised one blocks it again."""
    execute("DELETE FROM orders WHERE id='JA-MIN01'")
    body = order_body("JA-MIN01", "min@example.com")
    r = client.post("/api/orders", headers={"X-CSRF-Token": csrf(client)}, json=body)
    assert r.status_code == 400, "8,550 NGN is under the default ~11,251 NGN floor"
    assert "minimum order" in r.get_json()["error"]

    set_growth(client, {"minOrderCfa": 1000})     # lowered: ~2,160 NGN floor
    execute("DELETE FROM orders WHERE id='JA-MIN01'")
    execute("DELETE FROM rate_limits WHERE action='order'")
    r = client.post("/api/orders", headers={"X-CSRF-Token": csrf(client)}, json=body)
    assert r.status_code == 200, f"a lowered minimum must accept the basket: {r.data}"

    set_growth(client, {"minOrderCfa": 20000})    # raised: ~45,341 NGN floor
    execute("DELETE FROM orders WHERE id='JA-MIN01'")
    execute("DELETE FROM rate_limits WHERE action='order'")
    r = client.post("/api/orders", headers={"X-CSRF-Token": csrf(client)}, json=body)
    assert r.status_code == 400, "a raised minimum must refuse the basket again"
    set_growth(client, {"minOrderCfa": 5000})


def test_the_minimum_can_be_disabled_completely(client):
    """0 means OFF: a basket far under the old floor sails through, and the
    direct helper stops emitting an error message."""
    set_growth(client, {"minOrderCfa": 0})
    assert api.benin_togo_min_cfa() == 0
    assert api._benin_togo_min("Cotonou", "Benin", "NGN", 1) is None
    execute("DELETE FROM orders WHERE id='JA-MIN00'")
    body = order_body("JA-MIN00", "minoff@example.com")
    r = client.post("/api/orders", headers={"X-CSRF-Token": csrf(client)}, json=body)
    assert r.status_code == 200, f"a disabled minimum must never block: {r.data}"
    set_growth(client, {"minOrderCfa": 5000})
    assert api._benin_togo_min("Cotonou", "Benin", "NGN", 1) is not None


def test_naira_floor_follows_an_admin_edited_minimum():
    """The derived Naira floor moves with the admin's CFA value, keeping the
    two currencies' thresholds exactly equivalent at every rate."""
    import currency as currency_mod
    for min_cfa in (1000, 2500, 5000, 8500):
        for rate in (0.44, 0.5, 1.0):
            floor = api.benin_togo_min_ngn(rate, min_cfa)
            for naira in range(max(0, floor - 150), floor + 150):
                in_ngn = naira >= floor
                in_cfa = currency_mod.to_cfa(naira, rate) >= min_cfa
                assert in_ngn == in_cfa, (min_cfa, rate, naira, floor)
    assert api.benin_togo_min_ngn(0.44, 0) == 0, "a 0 floor means OFF"


# ------------------------------------------------------- storefront pins
def test_the_currency_pill_is_nude_brown_never_purple():
    css = read("css/style.css")
    i = css.find(".cur-float {")
    j = css.find(".cur-float[hidden]", i)
    assert i != -1 and j != -1
    block = re.sub(r"/\*.*?\*/", "", css[i:j], flags=re.S).lower()
    assert "#e8cdac" in block, "the pill's nude tan border is missing"
    assert "#33251a" in block, "the active currency's espresso fill is missing"
    for purple in ("#7c3aed", "124, 58, 237", "#d8b4fe"):
        assert purple not in block, f"purple ({purple}) is back in the pill"


def test_the_welcome_popup_entrance_is_perfectly_straight():
    css = read("css/style.css")
    m = re.search(r"@keyframes welcomeIn\s*\{(.*?)\n\}", css, flags=re.S)
    assert m, "the welcomeIn keyframes are missing"
    assert "rotate" not in m.group(1), (
        "the welcome pop-up must not slant at any point of its entrance")


def test_the_welcome_popup_is_one_unified_box_and_toggleable():
    store = read("js/store.js")
    assert '<div class="welcome-panel">' in store, (
        "the pop-up contents must live in a single unified box")
    assert "function welcomeEnabled()" in store
    css = read("css/style.css")
    assert ".welcome-panel" in css
    # the admin toggle exists and drives welcome_enabled on/off
    admin = read("js/admin.js")
    assert 'id="welcome-enabled"' in admin
    assert 'patch.welcome_enabled = enabled && enabled.checked ? "1" : "0"' in admin


def test_the_checkout_reads_the_live_minimum_and_programme_flags():
    app_js = read("js/app.js")
    assert "minOrderCfa" in app_js, (
        "the checkout guard must read the admin-set minimum, not a constant")
    assert "minOrderCfa > 0" in app_js, (
        "a saved 0 must skip the under-minimum guard entirely")
    assert "JA.promosEnabled" in app_js, (
        "the promo field must hide when BOTH programmes are switched off")
    store = read("js/store.js")
    assert "function promosEnabled()" in store
    assert "referralEnabled, promosEnabled," in store
    assert "site.minOrderCfa" in store or "minOrderCfa" in store


def test_the_static_minimum_order_explainer_lines_are_repainted_live():
    """.ck-bj-min / .ck-pay-country-note used to hardcode "5,000 F CFA"
    forever, even after the owner raised or lowered minOrderCfa in Admin ->
    Marketing - only the under-minimum WARNING read the live figure. Both
    always-visible explainer lines must now be repainted from the same live
    setting, in both languages, whenever it is available; the HTML text is
    only the pre-JS/offline fallback.
    """
    app_js = read("js/app.js")
    assert "function paintMinOrderNotices()" in app_js
    assert "function minOrderFigures()" in app_js
    # Both lines are painted straight from the live minOrderCfa/minOrderNgn -
    # never a copy of the "5,000" fallback baked back in.
    assert '".ck-bj-min"' in app_js
    assert '".ck-pay-country-note"' in app_js
    assert "minOrderCfa <= 0" in app_js, (
        "a 0 (rule off) minimum must say so instead of quoting a stale floor")
    # Painted on checkout init, on every fresh site-settings push, and again
    # on a language switch - never only once at page load.
    assert "paintMinOrderNotices()" in app_js
    assert 'addEventListener("ja:lang"' in app_js

    checkout = read("checkout.html")
    # The static English copy stays as the offline/no-JS fallback, but it is
    # no longer wired to the i18n dictionary - the dictionary's hardcoded
    # number could disagree with a changed admin setting and would only be
    # repainted with ANOTHER hardcoded number on a language switch.
    # Note: .ck-bj-min banner has been removed from checkout UI.
    assert 'class="ck-bj-min"' not in checkout
    assert 'class="ck-pay-country-note" data-i18n=' not in checkout
    assert 'class="ck-pay-country-note"' in checkout
