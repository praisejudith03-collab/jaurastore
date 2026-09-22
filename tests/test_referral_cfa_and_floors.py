"""Referral/discount correctness for F CFA orders + the derived delivery floor.

Two rules pinned here:

  * 5,000 F CFA is the SINGLE SOURCE OF TRUTH for the Benin/Togo delivery
    minimum. The Naira floor is derived from it at the live `cfaRate`, so a
    rate change in the admin panel can never leave a band where the same
    basket is accepted in one currency and refused in the other.
  * An order placed (or converted) in F CFA mints referral rewards and tracks
    its discount exactly like a Naira order, and the discounted CFA total is
    still a clean 50/100 amount.

Run with:  python3 -m pytest tests/test_referral_cfa_and_floors.py -q
"""
import os
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
import currency as currency_mod  # noqa: E402
import growth  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW


@pytest.fixture()
def client():
    init_db()
    execute("DELETE FROM rate_limits")
    application = appmod.create_app()
    application.config.update(TESTING=True)
    with application.test_client() as c:
        yield c


def _csrf(client):
    return client.get("/api/config").get_json()["csrf"]


def _order(client, oid, email, currency="CFA", pid="wix-005", qty=1,
           promo="", zone="Lagos Mainland"):
    execute("DELETE FROM orders WHERE id=?", (oid,))
    execute("DELETE FROM rate_limits WHERE action='order'")
    body = {
        "id": oid, "currency": currency, "total": 1,
        "customer": {"name": "CFA Buyer", "phone": "+2348012345678",
                     "email": email, "city": "Lagos", "zone": zone,
                     "address": "1 Test Street"},
        "items": [{"id": pid, "name": "Item", "qty": qty, "price": 1}],
    }
    if promo:
        body["promoCode"] = promo
    return client.post("/api/orders", json=body,
                       headers={"X-CSRF-Token": _csrf(client)})


# ------------------------------------------------- derived delivery floors
def test_the_cfa_floor_is_the_single_source_of_truth():
    assert api.BENIN_TOGO_MIN_CFA == 5000


def test_the_naira_floor_tracks_the_admin_rate():
    """Change the rate, and the Naira floor moves with it."""
    assert api.benin_togo_min_ngn(0.44) == pytest.approx(11251, abs=2)
    assert api.benin_togo_min_ngn(2.0) == pytest.approx(2476, abs=2)
    # A missing / nonsensical rate falls back to the house default.
    assert api.benin_togo_min_ngn(0) == api.benin_togo_min_ngn(0.44)
    assert api.benin_togo_min_ngn("banana") == api.benin_togo_min_ngn(0.44)


@pytest.mark.parametrize("rate", [0.44, 0.5, 1.0, 2.0])
def test_no_basket_is_accepted_in_one_currency_and_refused_in_the_other(rate):
    floor = api.benin_togo_min_ngn(rate)
    for naira in range(max(0, floor - 300), floor + 300):
        in_ngn = naira >= floor
        in_cfa = currency_mod.to_cfa(naira, rate) >= api.BENIN_TOGO_MIN_CFA
        assert in_ngn == in_cfa, (rate, naira, floor)


def test_the_quoted_naira_figure_matches_the_enforced_one(client):
    """The error message must quote the floor the server actually applies."""
    err = api._benin_togo_min("Cotonou", "", "NGN", 10, rate=0.44)
    assert f"{api.benin_togo_min_ngn(0.44):,} naira" in err
    err_cfa = api._benin_togo_min("Cotonou", "", "CFA", 10, rate=0.44)
    assert "5,000 F CFA" in err_cfa


def test_zones_outside_benin_and_togo_have_no_minimum():
    assert api._benin_togo_min("Lagos Mainland", "Nigeria", "NGN", 1) is None


# ------------------------------------------------- referral minting in CFA
def test_a_cfa_order_mints_a_referral_code(client):
    """wix-005 is 15,000 F CFA = ~34,000 NGN, over the 20,000 NGN threshold."""
    r = _order(client, "JA-RCFA1", "cfabuyer@example.com", currency="CFA")
    assert r.status_code == 200, r.data
    code = r.get_json().get("referralCode") or ""
    assert code.startswith("JA-"), r.get_json()
    row = one("SELECT email, uses FROM referral_codes WHERE code=?", (code,))
    assert row["email"] == "cfabuyer@example.com"
    assert row["uses"] == 0


def test_a_cfa_order_below_the_threshold_mints_nothing(client):
    """wix-008 is 1,000 F CFA = ~2,270 NGN, under the threshold."""
    r = _order(client, "JA-RCFA2", "smallcfa@example.com", currency="CFA",
               pid="wix-008", qty=1)
    assert r.status_code == 200, r.data
    assert r.get_json().get("referralCode") == ""


def test_cfa_and_naira_orders_of_equal_value_behave_identically(client):
    """The same basket must qualify whichever currency it is priced in."""
    a = _order(client, "JA-RCFA3", "equal-cfa@example.com", currency="CFA")
    b = _order(client, "JA-RNGN3", "equal-ngn@example.com", currency="NGN",
               qty=3)
    assert a.status_code == b.status_code == 200
    assert a.get_json().get("referralCode", "").startswith("JA-")
    assert b.get_json().get("referralCode", "").startswith("JA-")


def test_the_threshold_follows_the_admin_rate_for_cfa_orders(client):
    """Raising cfaRate makes the same CFA basket worth fewer naira."""
    cfa_total = 15000
    assert growth.total_in_ngn(cfa_total, "CFA", rate=0.44) > 20000
    assert growth.total_in_ngn(cfa_total, "CFA", rate=2.0) < 20000


# ------------------------------------------- discount tracking in CFA
def test_a_cfa_discount_is_tracked_and_leaves_a_clean_total(client):
    """A promo on a CFA order records the use AND keeps a 50-step total."""
    execute("DELETE FROM coupons WHERE code='CFAPROMO'")
    execute("DELETE FROM coupon_uses WHERE code='CFAPROMO'")
    execute("INSERT INTO coupons (code, percent, kind, active, max_uses) "
            "VALUES ('CFAPROMO', 7, 'promo', 1, NULL)")
    r = _order(client, "JA-RCFA4", "promo-cfa@example.com", currency="CFA",
               promo="CFAPROMO")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["promo"]["code"] == "CFAPROMO"
    # 7% of 15,000 = 1,050 -> 13,950, which is NOT a clean 50/100 step in the
    # naive maths; the server must snap it.
    assert body["total"] % currency_mod.CFA_STEP == 0, body
    # The shopper keeps at least the advertised discount.
    assert body["discount"] >= round(body["subtotal"] * 7 / 100)
    assert body["subtotal"] - body["discount"] == body["total"]
    used = one("SELECT COUNT(*) AS n FROM coupon_uses WHERE code='CFAPROMO'")
    assert used["n"] == 1
    assert one("SELECT uses FROM coupons WHERE code='CFAPROMO'")["uses"] == 1


def test_a_naira_discount_is_left_exact(client):
    """Naira is the base currency - a discounted total is never re-rounded."""
    execute("DELETE FROM coupons WHERE code='NGNPROMO'")
    execute("DELETE FROM coupon_uses WHERE code='NGNPROMO'")
    execute("INSERT INTO coupons (code, percent, kind, active, max_uses) "
            "VALUES ('NGNPROMO', 7, 'promo', 1, NULL)")
    r = _order(client, "JA-RNGN4", "promo-ngn@example.com", currency="NGN",
               promo="NGNPROMO")
    assert r.status_code == 200, r.data
    body = r.get_json()
    expected = round(body["subtotal"] * 7 / 100)
    assert body["discount"] == expected
    assert body["total"] == body["subtotal"] - expected


def test_floor_cfa_is_a_deprecated_alias_that_rounds_up():
    """floor_cfa used to round a discounted total DOWN. The single rounding
    contract now sends every F CFA amount UP to the next 50 step - no
    exceptions - and the old name is kept only as an alias for round_cfa so
    existing imports keep working."""
    assert currency_mod.floor_cfa(5580) == 5600
    assert currency_mod.floor_cfa(5000) == 5000
    assert currency_mod.floor_cfa(0) == 0
    assert currency_mod.floor_cfa(-10) == 0
    assert currency_mod.floor_cfa("x") == 0
    for value in range(0, 3000, 7):
        assert currency_mod.floor_cfa(value) >= value
        assert currency_mod.floor_cfa(value) % currency_mod.CFA_STEP == 0
        assert currency_mod.floor_cfa(value) == currency_mod.round_cfa(value)
