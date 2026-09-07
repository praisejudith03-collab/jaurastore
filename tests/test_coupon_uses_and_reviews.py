"""coupon_uses and product_reviews as real, queried tables.

Both tables existed in the schema while the application ignored them: coupon
redemptions only bumped a `coupons.uses` counter (so "which order used this
code?" was unanswerable, and a retried order could bump it twice), and product
reviews were mirrored into Supabase as one JSON blob in growth_settings, which
was last-write-wins.

These tests cover the behaviour that was missing, not the DDL:

  coupons  - success, retry idempotency, expiration, max uses, invalid code
  reviews  - create, list, update, delete, moderation, product filtering

Run with:  python3 -m pytest tests -q
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
import growth  # noqa: E402
from db import execute, init_db, one, query  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"

# Order ids used here. /tmp/jaura_test.db outlives the process, so every test
# clears its own ids first - a leftover row makes the checkout return
# duplicate=True and skips the coupon bookkeeping entirely.
_ORDER_IDS = ("JA-CU001", "JA-CU002", "JA-CU003", "JA-CU004", "JA-CU005",
              "JA-CU006", "JA-CU007", "JA-CU008")
_CODES = ("CU-TEST1", "CU-TEST2", "CU-EXPIRED", "CU-MAXED", "CU-GONE")


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
    _reset()
    with app.test_client() as c:
        yield c
    _reset()


def _reset():
    """Give every test in this module a clean slate of its own rows."""
    execute("DELETE FROM coupon_uses WHERE code IN (%s)"
            % ",".join("?" * len(_CODES)), _CODES)
    execute("DELETE FROM coupons WHERE code IN (%s)"
            % ",".join("?" * len(_CODES)), _CODES)
    execute("DELETE FROM orders WHERE id IN (%s)"
            % ",".join("?" * len(_ORDER_IDS)), _ORDER_IDS)
    execute("DELETE FROM product_reviews WHERE product_id LIKE 'cutest-%'")


def csrf(client):
    return client.get("/api/config").get_json()["csrf"]


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def _place_order(client, oid, email, promo="", pid="wix-005", total=25000):
    execute("DELETE FROM rate_limits WHERE action='order'")
    body = {
        "id": oid, "currency": "NGN", "total": total,
        "customer": {"name": "CU Tester", "phone": "+2348012345678",
                     "email": email, "city": "Lagos", "zone": "Lagos Mainland",
                     "address": "1 Test Street"},
        "items": [{"id": pid, "name": "Bag", "qty": 3, "price": total}],
    }
    if promo:
        body["promoCode"] = promo
    r = client.post("/api/orders", headers={"X-CSRF-Token": csrf(client)}, json=body)
    assert r.status_code == 200, r.data
    return r.get_json()


def _make_coupon(client, tok, code, **kw):
    payload = {"code": code, "percent": 10}
    payload.update(kw)
    r = client.post("/api/admin/coupons", headers={"X-CSRF-Token": tok}, json=payload)
    assert r.status_code == 200, r.data
    return r.get_json()


def _uses_for(code):
    return [dict(r) for r in query(
        "SELECT code, email, order_id, percent, used_at FROM coupon_uses WHERE code=?",
        (code,))]


# ====================================================== coupon redemption log
def test_a_successful_redemption_is_recorded(client):
    """The basic case the counter alone could not answer."""
    tok = login(client)
    _make_coupon(client, tok, "CU-TEST1", percent=10, maxUses=5)
    _place_order(client, "JA-CU001", "cu-buyer1@example.com", promo="CU-TEST1")

    uses = _uses_for("CU-TEST1")
    assert len(uses) == 1, f"expected one redemption row, got {uses}"
    assert uses[0]["order_id"] == "JA-CU001"
    assert uses[0]["email"] == "cu-buyer1@example.com"
    assert int(uses[0]["percent"]) == 10, "the discount applied must be stored"
    assert uses[0]["used_at"], "used_at must be set"
    # the counter still moves, but it is no longer the only record
    assert int(one("SELECT uses FROM coupons WHERE code='CU-TEST1'")["uses"]) == 1


def test_a_retried_order_does_not_count_twice(client):
    """The whole point of unique(code, order_id): a confirm/retry loop, a
    double-tapped button or a replayed webhook must not inflate the count."""
    tok = login(client)
    _make_coupon(client, tok, "CU-TEST2", percent=15, maxUses=10)

    assert growth.record_code_use("CU-TEST2", "cu-buyer2@example.com", "JA-CU002")["counted"]
    again = growth.record_code_use("CU-TEST2", "cu-buyer2@example.com", "JA-CU002")
    assert again["counted"] is False, "a replay must not be counted again"
    assert again["duplicate"] is True

    uses = _uses_for("CU-TEST2")
    assert len(uses) == 1, f"unique(code, order_id) did not hold: {uses}"
    assert int(one("SELECT uses FROM coupons WHERE code='CU-TEST2'")["uses"]) == 1


def test_two_different_orders_both_count(client):
    """Idempotency must not swallow genuine second uses."""
    tok = login(client)
    _make_coupon(client, tok, "CU-TEST2", percent=15, maxUses=10)
    growth.record_code_use("CU-TEST2", "a@example.com", "JA-CU002")
    growth.record_code_use("CU-TEST2", "b@example.com", "JA-CU003")
    assert len(_uses_for("CU-TEST2")) == 2
    assert int(one("SELECT uses FROM coupons WHERE code='CU-TEST2'")["uses"]) == 2


def test_the_same_order_cannot_use_two_different_codes_twice(client):
    tok = login(client)
    _make_coupon(client, tok, "CU-TEST1", percent=10, maxUses=5)
    _make_coupon(client, tok, "CU-TEST2", percent=15, maxUses=5)
    assert growth.record_code_use("CU-TEST1", "c@example.com", "JA-CU004")["counted"]
    assert growth.record_code_use("CU-TEST2", "c@example.com", "JA-CU004")["counted"]
    assert len(_uses_for("CU-TEST1")) == 1 and len(_uses_for("CU-TEST2")) == 1
    # a replay of either is still refused
    assert growth.record_code_use("CU-TEST1", "c@example.com", "JA-CU004")["counted"] is False
    assert growth.record_code_use("CU-TEST2", "c@example.com", "JA-CU004")["counted"] is False


def test_a_redemption_without_an_order_id_is_refused(client):
    """Without an order id there is nothing to be idempotent against, so the
    call is refused rather than silently double-counting on every retry."""
    tok = login(client)
    _make_coupon(client, tok, "CU-TEST1", percent=10, maxUses=5)
    for _ in range(3):
        r = growth.record_code_use("CU-TEST1", "d@example.com", "")
        assert r["counted"] is False
    assert _uses_for("CU-TEST1") == []
    assert int(one("SELECT uses FROM coupons WHERE code='CU-TEST1'")["uses"]) == 0


def test_an_expired_coupon_cannot_be_redeemed(client):
    tok = login(client)
    _make_coupon(client, tok, "CU-EXPIRED", percent=20,
                 expiresAt="2020-01-01 00:00:00")
    execute("DELETE FROM rate_limits WHERE action='promo-check'")
    r = client.post("/api/promo/check", json={"code": "CU-EXPIRED"})
    assert r.status_code == 404
    assert "expired" in r.get_json()["error"].lower()
    # and nothing was logged
    assert _uses_for("CU-EXPIRED") == []


def test_a_coupon_at_its_maximum_use_count_is_refused(client):
    tok = login(client)
    _make_coupon(client, tok, "CU-MAXED", percent=25, maxUses=2)
    growth.record_code_use("CU-MAXED", "e1@example.com", "JA-CU005")
    growth.record_code_use("CU-MAXED", "e2@example.com", "JA-CU006")
    c = one("SELECT uses, active, max_uses FROM coupons WHERE code='CU-MAXED'")
    assert int(c["uses"]) == 2 and int(c["max_uses"]) == 2
    assert int(c["active"]) == 0, "reaching max_uses must deactivate the code"

    execute("DELETE FROM rate_limits WHERE action='promo-check'")
    r = client.post("/api/promo/check", json={"code": "CU-MAXED"})
    assert r.status_code == 404, "an exhausted code must not validate"
    # the log shows exactly the two real redemptions
    assert len(_uses_for("CU-MAXED")) == 2


def test_an_invalid_coupon_is_rejected_and_logged_nowhere(client):
    execute("DELETE FROM rate_limits WHERE action='promo-check'")
    r = client.post("/api/promo/check", json={"code": "NO-SUCH-CODE"})
    assert r.status_code == 404
    assert _uses_for("NO-SUCH-CODE") == []
    # an unknown code passed straight to the recorder is also a no-op
    out = growth.record_code_use("NO-SUCH-CODE", "f@example.com", "JA-CU007")
    assert out["counted"] is False
    assert _uses_for("NO-SUCH-CODE") == []


def test_the_admin_redemption_log_answers_which_order_used_the_code(client):
    tok = login(client)
    _make_coupon(client, tok, "CU-TEST1", percent=10, maxUses=5)
    growth.record_code_use("CU-TEST1", "g1@example.com", "JA-CU008")
    r = client.get("/api/admin/coupon-uses?code=CU-TEST1",
                   headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and len(j["uses"]) == 1
    assert j["uses"][0]["order_id"] == "JA-CU008"


# =========================================================== product reviews
def _buy(client, oid, email, pid):
    """Reviews are purchase-verified, so the reviewer needs a real order."""
    execute("DELETE FROM orders WHERE id=?", (oid,))
    execute("INSERT INTO orders (id, payload, email, status, at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (oid, json.dumps({"items": [{"id": pid, "name": "P", "qty": 1}]}),
             email, "confirmed", "2026-09-01T00:00:00", "2026-09-01T00:00:00"))


def _review(client, pid, email, rating=4, body="Good.", name="Reviewer",
            title=None, **legacy):
    payload = {"productId": pid, "email": email, "name": name,
               "rating": rating, "body": body}
    if title is not None:
        payload["title"] = title
    payload.update(legacy)          # lets a test post the legacy stars/note keys
    return client.post("/api/reviews", headers={"X-CSRF-Token": csrf(client)},
                       json=payload)


def test_review_create_persists_and_reads_back(client):
    _buy(client, "JA-CU001", "rv1@example.com", "cutest-a")
    r = _review(client, "cutest-a", "rv1@example.com", 5, "Excellent.",
                title="Great buy")
    assert r.status_code == 200, r.data
    j = r.get_json()
    assert j["count"] == 1 and j["average"] == 5
    row = one("SELECT product_id, email, rating, title, body, created_at, "
              "updated_at FROM product_reviews WHERE product_id='cutest-a'")
    assert row["email"] == "rv1@example.com"
    assert int(row["rating"]) == 5
    assert row["title"] == "Great buy"
    assert row["body"] == "Excellent."
    assert row["created_at"] and row["updated_at"], "both timestamps must be set"
    # the public payload carries the new names, plus aliases for cached JS
    rev = j["reviews"][0]
    assert rev["rating"] == 5 and rev["body"] == "Excellent."
    assert rev["stars"] == 5 and rev["note"] == "Excellent.", \
        "the legacy aliases keep an old cached storefront bundle rendering"


def test_the_legacy_stars_and_note_keys_are_still_accepted(client):
    """A browser still running the previous js/app.js posts stars/note. It must
    not get a 400 or silently store an empty review."""
    _buy(client, "JA-CU001", "rv1@example.com", "cutest-a")
    r = _review(client, "cutest-a", "rv1@example.com",
                rating=None, body=None, stars=2, note="Old bundle.")
    assert r.status_code == 200, r.data
    row = one("SELECT rating, body FROM product_reviews WHERE product_id='cutest-a'")
    assert int(row["rating"]) == 2 and row["body"] == "Old bundle."


def test_review_list_is_filtered_by_product(client):
    _buy(client, "JA-CU001", "rv1@example.com", "cutest-a")
    _buy(client, "JA-CU002", "rv2@example.com", "cutest-b")
    assert _review(client, "cutest-a", "rv1@example.com", 5).status_code == 200
    assert _review(client, "cutest-b", "rv2@example.com", 3).status_code == 200

    a = client.get("/api/reviews/cutest-a").get_json()
    b = client.get("/api/reviews/cutest-b").get_json()
    assert a["count"] == 1 and b["count"] == 1, "a product must not see another's reviews"
    assert a["average"] == 5 and b["average"] == 3
    empty = client.get("/api/reviews/cutest-none").get_json()
    assert empty["count"] == 0 and empty["reviews"] == []


def test_review_update_targets_the_right_row(client):
    """Re-submitting changes that reviewer's review, and nothing else."""
    _buy(client, "JA-CU001", "rv1@example.com", "cutest-a")
    _buy(client, "JA-CU002", "rv2@example.com", "cutest-a")
    assert _review(client, "cutest-a", "rv1@example.com", 5, "Loved it.").status_code == 200
    assert _review(client, "cutest-a", "rv2@example.com", 2, "Not great.").status_code == 200

    r = _review(client, "cutest-a", "rv1@example.com", 3, "Changed my mind.")
    assert r.status_code == 200
    rows = query("SELECT email, rating, body, created_at, updated_at "
                 "FROM product_reviews WHERE product_id='cutest-a'")
    assert len(rows) == 2, "an update must not duplicate or drop a row"
    by_email = {x["email"]: dict(x) for x in rows}
    assert int(by_email["rv1@example.com"]["rating"]) == 3
    assert by_email["rv1@example.com"]["body"] == "Changed my mind."
    assert int(by_email["rv2@example.com"]["rating"]) == 2, "the other review is untouched"


def test_review_delete_removes_exactly_one_row(client):
    tok = login(client)
    _buy(client, "JA-CU001", "rv1@example.com", "cutest-a")
    _buy(client, "JA-CU002", "rv2@example.com", "cutest-a")
    _review(client, "cutest-a", "rv1@example.com", 5)
    _review(client, "cutest-a", "rv2@example.com", 4)

    r = client.delete("/api/admin/reviews?productId=cutest-a&email=rv1@example.com",
                      headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    left = query("SELECT email FROM product_reviews WHERE product_id='cutest-a'")
    assert [x["email"] for x in left] == ["rv2@example.com"]

    # deleting again is a 404, not a silent success
    r2 = client.delete("/api/admin/reviews?productId=cutest-a&email=rv1@example.com",
                       headers={"X-CSRF-Token": tok})
    assert r2.status_code == 404
    # and an address that does not exist deletes nothing
    r3 = client.delete("/api/admin/reviews?productId=cutest-a&email=nobody@example.com",
                       headers={"X-CSRF-Token": tok})
    assert r3.status_code == 404
    assert len(query("SELECT 1 FROM product_reviews WHERE product_id='cutest-a'")) == 1


def test_review_delete_requires_admin(client):
    r = client.delete("/api/admin/reviews?productId=cutest-a&email=rv1@example.com")
    assert r.status_code in (401, 403)
    assert one("SELECT 1 FROM product_reviews WHERE product_id='cutest-a' "
               "AND email='rv1@example.com'") is None


def test_review_moderation_hides_without_deleting(client):
    tok = login(client)
    _buy(client, "JA-CU001", "rv1@example.com", "cutest-a")
    _review(client, "cutest-a", "rv1@example.com", 1, "Rude content.")

    r = client.patch("/api/admin/reviews", headers={"X-CSRF-Token": tok},
                     json={"productId": "cutest-a", "email": "rv1@example.com",
                           "hidden": True})
    assert r.status_code == 200, r.data
    # the row survives
    row = one("SELECT hidden FROM product_reviews WHERE product_id='cutest-a' "
              "AND email='rv1@example.com'")
    assert row is not None, "moderation must not delete the review"
    assert int(row["hidden"]) == 1
    # and the storefront no longer shows it
    assert client.get("/api/reviews/cutest-a").get_json()["count"] == 0
    # the admin view still does
    listing = client.get("/api/admin/reviews?productId=cutest-a",
                         headers={"X-CSRF-Token": tok}).get_json()
    assert len(listing["reviews"]) == 1 and listing["reviews"][0]["hidden"] == 1

    # unhide restores it
    r2 = client.patch("/api/admin/reviews", headers={"X-CSRF-Token": tok},
                      json={"productId": "cutest-a", "email": "rv1@example.com",
                            "hidden": False})
    assert r2.status_code == 200
    assert client.get("/api/reviews/cutest-a").get_json()["count"] == 1


def test_review_moderation_targets_only_that_reviewer(client):
    tok = login(client)
    _buy(client, "JA-CU001", "rv1@example.com", "cutest-a")
    _buy(client, "JA-CU002", "rv2@example.com", "cutest-a")
    _review(client, "cutest-a", "rv1@example.com", 1)
    _review(client, "cutest-a", "rv2@example.com", 5)
    client.patch("/api/admin/reviews", headers={"X-CSRF-Token": tok},
                 json={"productId": "cutest-a", "email": "rv1@example.com",
                       "hidden": True})
    rows = {x["email"]: int(x["hidden"]) for x in query(
        "SELECT email, hidden FROM product_reviews WHERE product_id='cutest-a'")}
    assert rows == {"rv1@example.com": 1, "rv2@example.com": 0}


def test_review_moderation_of_an_unknown_review_is_a_404(client):
    tok = login(client)
    r = client.patch("/api/admin/reviews", headers={"X-CSRF-Token": tok},
                     json={"productId": "cutest-a", "email": "ghost@example.com",
                           "hidden": True})
    assert r.status_code == 404


def test_review_still_requires_a_verified_purchase(client):
    """The gate that keeps the review table trustworthy."""
    r = _review(client, "cutest-a", "never-bought@example.com", 5, "Amazing!")
    assert r.status_code == 403
    assert one("SELECT 1 FROM product_reviews WHERE product_id='cutest-a'") is None
