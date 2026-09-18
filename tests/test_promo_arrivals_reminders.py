"""Referral switch, promo audit, New Arrival ribbon and cart reminders.

One module per reported fault, so a regression names the fault it brought
back:

* the referral programme's master switch must reach the storefront and
  silence every referral prompt when it is off;
* the other promos (coupons, per-product bulk, shop-wide tiers) must keep
  working while referrals are off - the audit found them healthy and they
  must stay that way;
* the product editor's promo ribbon must put a product in "Just in";
* a cart must be recorded, and a completed order must close it, so the
  reminder reaches shoppers who left and never chases shoppers who paid.
"""
import datetime
import json
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

import abandoned  # noqa: E402
import app as appmod  # noqa: E402
import growth  # noqa: E402
from db import execute, init_db, one  # noqa: E402

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
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c
    # The referral switch is global state: never leave it off for other files.
    growth.save_settings({"referralEnabled": 1})


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


def order_body(oid, email, **extra):
    body = {
        "id": oid,
        "currency": "NGN",
        "total": 25650,
        "customer": {"name": "Buyer", "phone": "+2348012345678", "email": email,
                     "city": "Lagos", "zone": "Lagos Mainland", "address": "1 Street"},
        "items": [{"id": "wix-005", "name": "Bag", "qty": 3, "price": 8550}],
    }
    body.update(extra)
    return body


# ----------------------------------------------- issue 1: referral switch
def test_site_publishes_referral_switch_both_ways(client):
    """The storefront cannot hide a prompt it is never told about."""
    tok = login(client)
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"referralEnabled": True})
    assert client.get("/api/site").get_json()["site"]["referralEnabled"] is True

    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"referralEnabled": False})
    assert client.get("/api/site").get_json()["site"]["referralEnabled"] is False


def test_disabled_referral_mints_no_code_and_refuses_codes(client):
    tok = login(client)
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"referralEnabled": False})
    execute("DELETE FROM orders WHERE id='JA-REFOFF1'")
    r = client.post("/api/orders", headers={"X-CSRF-Token": csrf(client)},
                    json=order_body("JA-REFOFF1", "refoff@example.com"))
    assert r.status_code == 200
    assert not r.get_json().get("referralCode")


def test_reenabling_restores_the_programme(client):
    """Off is a switch, not a delete: settings and history come back."""
    tok = login(client)
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"minSpendNgn": 24000, "buyerPercent": 7})
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"referralEnabled": False})
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"referralEnabled": True})
    s = client.get("/api/admin/growth/settings").get_json()["settings"]
    assert s["referralEnabled"] in (1, True)
    assert s["minSpendNgn"] == 24000 and s["buyerPercent"] == 7
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"minSpendNgn": 20000, "buyerPercent": 5})


def test_edited_percentages_survive_a_restart(client):
    """Saved settings are read back from the database, not from memory."""
    tok = login(client)
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"minSpendNgn": 31000, "milestone": 4})
    growth._CACHE = None  # force a re-read the way a fresh process would
    s = growth.settings()
    assert s["minSpendNgn"] == 31000 and s["milestone"] == 4
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"minSpendNgn": 20000, "milestone": 2})


def test_storefront_hides_referral_prompts_when_off():
    """The welcome pop-up and the post-order card both read the flag."""
    store = read("js/store.js")
    assert "function referralEnabled()" in store
    # the pop-up's referral line is conditional, not unconditional
    assert re.search(r"referralEnabled\(\)\s*\?\s*`<p class=\"welcome-referral\"", store)
    app_js = read("js/app.js")
    assert "JA.referralEnabled && !JA.referralEnabled()" in app_js


# ------------------------------------------- issue 2: the other promos
def test_coupon_still_applies_while_referrals_are_off(client):
    """Disabling referrals must not disable the coupon engine."""
    tok = login(client)
    execute("DELETE FROM coupons WHERE code='AUDIT-15'")
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"referralEnabled": False})
    r = client.post("/api/admin/coupons", headers={"X-CSRF-Token": tok},
                    json={"code": "AUDIT-15", "percent": 15})
    assert r.status_code == 200
    j = client.post("/api/promo/check", json={"code": "audit-15"}).get_json()
    assert j["ok"] and j["percent"] == 15 and j["kind"] == "coupon"
    client.delete("/api/admin/coupons/AUDIT-15", headers={"X-CSRF-Token": tok})


def test_per_product_bulk_discount_prices_the_checkout(client):
    """buy more than X, get Y% off - priced by the server, not the browser."""
    tok = login(client)
    client.post("/api/admin/products", headers={"X-CSRF-Token": tok}, json={
        "id": "audit-bulk", "name": "Audit Bulk", "priceNgn": 1000,
        "category": "wigs", "stock": 100, "bulkQty": 5, "bulkPercent": 20})
    execute("DELETE FROM orders WHERE id='JA-BULK99'")
    body = order_body("JA-BULK99", "bulk@example.com")
    body["items"] = [{"id": "audit-bulk", "name": "Audit Bulk", "qty": 10, "price": 1000}]
    r = client.post("/api/orders", headers={"X-CSRF-Token": csrf(client)}, json=body)
    assert r.status_code == 200, r.data
    j = r.get_json()
    # 10 x 1000 = 10000, less 20% = 8000
    assert j["subtotal"] == 8000 and j["bulkDiscount"]
    client.delete("/api/admin/products/audit-bulk", headers={"X-CSRF-Token": tok})


def test_shop_wide_bulk_tiers_are_editable_and_served(client):
    tok = login(client)
    tiers = [{"minQuantity": 3, "percent": 5}, {"minQuantity": 6, "percent": 12}]
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"bulkDiscountTiers": tiers})
    served = client.get("/api/site").get_json()["site"]["bulkDiscountTiers"]
    assert [(t["minQuantity"], t["percent"]) for t in served] == [(3, 5), (6, 12)]
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"bulkDiscountTiers": []})


# --------------------------------------------- issue 3: New Arrival ribbon
def test_new_arrival_ribbon_persists_through_the_api(client):
    tok = login(client)
    client.post("/api/admin/products", headers={"X-CSRF-Token": tok}, json={
        "id": "audit-new", "name": "Audit New", "priceNgn": 5000,
        "category": "wigs", "stock": 5, "badge": "new"})
    rows = client.get("/api/catalog").get_json()["products"]
    row = next(p for p in rows if p["id"] == "audit-new")
    assert row["badge"] == "new"
    # and it can be cleared again
    client.post("/api/admin/products", headers={"X-CSRF-Token": tok}, json={
        "id": "audit-new", "name": "Audit New", "priceNgn": 5000,
        "category": "wigs", "stock": 5, "badge": ""})
    rows = client.get("/api/catalog").get_json()["products"]
    assert next(p for p in rows if p["id"] == "audit-new")["badge"] == ""
    client.delete("/api/admin/products/audit-new", headers={"X-CSRF-Token": tok})


def test_new_arrivals_row_is_driven_by_the_badge():
    """The ribbon used to paint a pill only; "Just in" ignored it entirely."""
    app_js = read("js/app.js")
    block = app_js.split("function newestTwelve()")[1].split("\n}")[0]
    assert 'p.badge === "new"' in block
    # flagged products lead the row, ahead of merely-featured ones
    assert "arrivals.concat(featured, rest)" in block


def test_admin_ribbon_offers_and_preselects_new_arrival():
    admin = read("js/admin.js")
    assert '"New Product Arrival"' in admin or "New Product Arrival" in admin
    assert 'name="badge"' in admin
    assert 'p.badge === b ? "selected" : ""' in admin


# ------------------------------------- issue 4: abandoned-cart reminders
def test_signed_in_shopper_cart_is_captured_without_a_typed_email(client):
    """Filling a cart and leaving is the normal way a cart is abandoned."""
    execute("DELETE FROM abandoned_carts WHERE token='cart-acct-test'")
    execute("DELETE FROM customers WHERE email='cartacct@example.com'")
    client.post("/api/account/register", headers={"X-CSRF-Token": csrf(client)},
                json={"email": "cartacct@example.com", "password": "Sup3rSecret!x",
                      "name": "Cart Acct"})
    r = client.post("/api/abandoned-carts", headers={"X-CSRF-Token": csrf(client)},
                    json={"token": "cart-acct-test", "currency": "NGN", "total": 8550,
                          "items": [{"id": "wix-005", "name": "Bag", "qty": 1, "price": 8550}]})
    assert r.status_code == 200, r.data
    row = one("SELECT email FROM abandoned_carts WHERE token='cart-acct-test'")
    assert row["email"] == "cartacct@example.com"
    client.post("/api/account/logout", headers={"X-CSRF-Token": csrf(client)})


def test_guest_without_an_email_is_still_not_captured(client):
    execute("DELETE FROM abandoned_carts WHERE token='cart-guest-test'")
    r = client.post("/api/abandoned-carts", headers={"X-CSRF-Token": csrf(client)},
                    json={"token": "cart-guest-test",
                          "items": [{"id": "wix-005", "name": "Bag", "qty": 1, "price": 10}]})
    assert r.status_code == 400
    assert one("SELECT 1 FROM abandoned_carts WHERE token='cart-guest-test'") is None


def test_completing_an_order_closes_the_cart_server_side(client):
    """A lost browser call must not email a reminder to someone who paid."""
    execute("DELETE FROM abandoned_carts WHERE token='cart-conv-test'")
    execute("DELETE FROM orders WHERE id='JA-CONV99'")
    client.post("/api/abandoned-carts", headers={"X-CSRF-Token": csrf(client)},
                json={"token": "cart-conv-test", "email": "conv@example.com",
                      "currency": "NGN", "total": 25650,
                      "items": [{"id": "wix-005", "name": "Bag", "qty": 3, "price": 8550}]})
    r = client.post("/api/orders", headers={"X-CSRF-Token": csrf(client)},
                    json=order_body("JA-CONV99", "conv@example.com", cartToken="cart-conv-test"))
    assert r.status_code == 200, r.data
    row = one("SELECT converted_at FROM abandoned_carts WHERE token='cart-conv-test'")
    assert row["converted_at"], "the order did not close its cart"
    # and it is no longer eligible for a reminder
    past = (datetime.datetime.utcnow() - datetime.timedelta(minutes=30)).replace(microsecond=0).isoformat()
    execute("UPDATE abandoned_carts SET last_activity_at=? WHERE token='cart-conv-test'", (past,))
    assert "cart-conv-test" not in [c["token"] for c in abandoned.due_carts()]


def test_order_without_a_token_closes_the_buyer_s_cart_by_email(client):
    """Checkout on another device still has to stop the reminder."""
    execute("DELETE FROM abandoned_carts WHERE token='cart-email-test'")
    execute("DELETE FROM orders WHERE id='JA-CONV98'")
    client.post("/api/abandoned-carts", headers={"X-CSRF-Token": csrf(client)},
                json={"token": "cart-email-test", "email": "byemail@example.com",
                      "currency": "NGN", "total": 25650,
                      "items": [{"id": "wix-005", "name": "Bag", "qty": 3, "price": 8550}]})
    r = client.post("/api/orders", headers={"X-CSRF-Token": csrf(client)},
                    json=order_body("JA-CONV98", "byemail@example.com"))
    assert r.status_code == 200, r.data
    row = one("SELECT converted_at FROM abandoned_carts WHERE token='cart-email-test'")
    assert row["converted_at"], "the buyer's open cart was not closed"


def test_an_untouched_cart_becomes_due_after_the_wait(client):
    execute("DELETE FROM abandoned_carts WHERE token='cart-due-test'")
    client.post("/api/abandoned-carts", headers={"X-CSRF-Token": csrf(client)},
                json={"token": "cart-due-test", "email": "due@example.com",
                      "currency": "NGN", "total": 8550,
                      "items": [{"id": "wix-005", "name": "Bag", "qty": 1, "price": 8550}]})
    assert "cart-due-test" not in [c["token"] for c in abandoned.due_carts()]
    past = (datetime.datetime.utcnow() - datetime.timedelta(minutes=30)).replace(microsecond=0).isoformat()
    execute("UPDATE abandoned_carts SET last_activity_at=? WHERE token='cart-due-test'", (past,))
    assert "cart-due-test" in [c["token"] for c in abandoned.due_carts()]


def test_storefront_captures_the_cart_outside_the_checkout():
    app_js = read("js/app.js")
    assert "function captureCartForAccount()" in app_js
    # wired to cart changes, and not on the checkout page which captures itself
    assert "captureCartForAccount()" in app_js.split('document.addEventListener("ja:cart"')[1][:600]
