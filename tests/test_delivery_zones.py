"""Blocker 5 - delivery zones and fares are server-authoritative and
admin-editable.

Before this the fare list was hardcoded as <option> text in checkout.html and
POST /api/orders accepted ANY free-text zone, only regex-blocking the word
"pickup". So the customer-facing fare and the operator's idea of the fare
could drift apart, the server could not say what a zone costs, and an
invented zone silently became an order.

Now the zones live in a delivery_zones table, are edited in the Admin Portal,
are served to the storefront by GET /api/site, and the checkout resolves the
submitted zone against that table and records the fare it computed.

Run with:  python3 -m pytest tests/test_delivery_zones.py -q
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
os.environ.setdefault("SITE_CONFIG_PATH", "/tmp/jaura_test_site_zones.json")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
import delivery  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = "jaurastore@gmail.com"
PRODUCT = "wix-001"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture(autouse=True)
def _own_site_config(monkeypatch, tmp_path):
    path = tmp_path / "site.json"
    path.write_text("{}")
    monkeypatch.setenv("SITE_CONFIG_PATH", str(path))


@pytest.fixture(autouse=True)
def _fresh_zones():
    """Each test starts from the seeded zone list, and the cache is cleared so
    a previous test's edits cannot leak into the next one."""
    init_db()
    execute("DELETE FROM delivery_zones")
    from db import seed_delivery_zones
    seed_delivery_zones()
    delivery._invalidate()
    yield
    delivery._invalidate()


@pytest.fixture()
def client(app):
    init_db()
    authmod.ensure_seed_admins()
    authmod.set_password(EMAIL, PW)
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def _login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def _order(client, tok, oid, zone, currency="CFA", qty=1):
    # /tmp/jaura_test.db outlives the process, and a duplicate order id makes
    # create_order return 200 with duplicate=True WITHOUT inserting a row - so
    # a stale row from an earlier run would make these assertions pass or fail
    # for the wrong reason. Each test owns its own row.
    execute("DELETE FROM orders WHERE id=?", (oid,))
    return client.post("/api/orders", json={
        "id": oid, "currency": currency, "total": 999999,
        "customer": {"name": "Zone Tester", "email": "zone@example.com",
                     "phone": "+229 90 00 00 00", "city": "Cotonou",
                     "zone": zone},
        "items": [{"id": PRODUCT, "name": "x", "qty": qty, "price": 5000}],
    }, headers={"X-CSRF-Token": tok})


def _csrf(client):
    """The public CSRF token, fetched the way the other suites do it."""
    return client.get("/api/config").get_json()["csrf"]


# --------------------------------------------------------------------------
# the table and the public contract
# --------------------------------------------------------------------------

def test_the_default_zones_are_seeded():
    zones = delivery.zones()
    assert len(zones) == 10
    names = [z["name"] for z in zones]
    assert "Cotonou" in names and "Lagos Mainland" in names
    assert names[-1].startswith("Pickup in Cotonou")


def test_seeding_is_idempotent():
    """Re-running init_db must not duplicate or reset an admin's fares."""
    delivery.save_zone("cotonou", {"name": "Cotonou", "currency": "CFA",
                                   "kind": "delivery", "fare_min": 1234,
                                   "fare_max": 4321, "sort_order": 4})
    from db import seed_delivery_zones
    seed_delivery_zones()
    delivery._invalidate()
    z = delivery.zone_for("cotonou")
    assert z["fare_min"] == 1234 and z["fare_max"] == 4321
    assert len([x for x in delivery.zones() if x["name"] == "Cotonou"]) == 1


def test_api_site_serves_the_zones(client):
    site = client.get("/api/site").get_json()["site"]
    zones = site["delivery_zones"]
    assert len(zones) == 10
    cotonou = next(z for z in zones if z["name"] == "Cotonou")
    assert cotonou["currency"] == "CFA"
    assert (cotonou["fare_min"], cotonou["fare_max"]) == (1000, 3000)
    assert cotonou["kind"] == "delivery"


def test_the_zones_are_sorted_by_display_order(client):
    site = client.get("/api/site").get_json()["site"]
    orders = [z["sort_order"] for z in site["delivery_zones"]]
    assert orders == sorted(orders)


def test_an_inactive_zone_is_not_served(client):
    tok = _login(client)
    r = client.post("/api/admin/delivery-zones", headers={"X-CSRF-Token": tok},
                    json={"id": "cotonou", "name": "Cotonou", "currency": "CFA",
                          "kind": "delivery", "fare_min": 1000, "fare_max": 3000,
                          "sort_order": 4, "active": False})
    assert r.status_code == 200, r.data
    served = client.get("/api/site").get_json()["site"]["delivery_zones"]
    assert "Cotonou" not in [z["name"] for z in served]
    # ...but the admin list still shows it, or it could never be re-enabled
    admin = client.get("/api/admin/delivery-zones",
                       headers={"X-CSRF-Token": tok}).get_json()["zones"]
    assert "Cotonou" in [z["name"] for z in admin]


# --------------------------------------------------------------------------
# checkout enforcement
# --------------------------------------------------------------------------

def test_checkout_rejects_a_zone_the_server_does_not_know(client):
    tok = _csrf(client)
    r = _order(client, tok, "JA-DZUNK", "Atlantis")
    assert r.status_code == 400
    assert "Unknown delivery zone" in r.get_json()["error"]
    assert one("SELECT 1 FROM orders WHERE id='JA-DZUNK'") is None


def test_an_invented_pickup_zone_is_rejected(client):
    """The old regex only blocked the word 'pickup'; now any zone that is not
    in the table is refused, which subsumes the old heuristic."""
    tok = _csrf(client)
    for zone in ("Pick Up", "pick up", "Pickup in store", "Self collect",
                 "pickup at my house"):
        r = _order(client, tok, f"JA-DZPK{abs(hash(zone)) % 9999}", zone)
        assert r.status_code == 400, zone
        assert "Unknown delivery zone" in r.get_json()["error"], zone


def test_the_real_pickup_zone_is_accepted_with_zero_fare(client):
    tok = _csrf(client)
    r = _order(client, tok, "JA-DZPICK",
               "Pickup in Cotonou is free for lighter products")
    assert r.status_code == 200, r.data
    d = r.get_json()["delivery"]
    assert d["fare_status"] == "pickup"
    assert d["delivery_fee_min"] == 0 and d["delivery_fee_max"] == 0
    assert d["delivery_fee_confirmed"] is True


def test_a_delivery_zone_records_the_server_computed_range(client):
    tok = _csrf(client)
    r = _order(client, tok, "JA-DZCOT", "Cotonou")
    assert r.status_code == 200, r.data
    d = r.get_json()["delivery"]
    assert d["fare_status"] == "range"
    assert d["delivery_fee_min"] == 1000 and d["delivery_fee_max"] == 3000
    assert d["delivery_fee_currency"] == "CFA"
    assert d["zone_id"] == "cotonou"


def test_a_quote_zone_is_accepted_without_a_published_range(client):
    tok = _csrf(client)
    r = _order(client, tok, "JA-DZQUOTE", "Other Nigeria", currency="NGN")
    assert r.status_code == 200, r.data
    d = r.get_json()["delivery"]
    assert d["fare_status"] == "quote"
    assert d["delivery_fee_min"] == 0 and d["delivery_fee_max"] == 0


def test_the_zone_name_stored_is_the_canonical_one(client):
    """Analytics and the order list must group by real zones, so whatever the
    browser sent (odd spacing, wrong case) is normalised on the way in."""
    tok = _csrf(client)
    r = _order(client, tok, "JA-DZCASE", "  cotonou  ")
    assert r.status_code == 200, r.data
    assert r.get_json()["delivery"]["zone_name"] == "Cotonou"
    row = one("SELECT zone FROM orders WHERE id='JA-DZCASE'")
    assert row["zone"] == "Cotonou"


def test_a_currency_mismatch_is_flagged_not_rejected(client):
    """Currency and zone are chosen independently, so a naira checkout to
    Cotonou must still go through - the shop has always taken that sale. The
    fare is simply unpublished and flagged for the operator."""
    tok = _csrf(client)
    # qty 2 = 15,000 NGN, which clears the 12,000 NGN Benin/Togo minimum; at
    # qty 1 the minimum rule fires first and the mismatch is never reached.
    r = _order(client, tok, "JA-DZMIX", "Cotonou", currency="NGN", qty=2)
    assert r.status_code == 200, r.data
    d = r.get_json()["delivery"]
    assert d["fare_status"] == "currency_mismatch"
    assert d["delivery_fee_min"] == 0 and d["delivery_fee_max"] == 0
    assert "Confirm the fare" in d["fare_note"]


def test_the_benin_minimum_still_applies_with_a_valid_zone(client):
    """The zone change must not have displaced the existing minimum rule."""
    tok = _csrf(client)
    r = client.post("/api/orders", json={
        "id": "JA-DZMIN", "currency": "CFA", "total": 1,
        "customer": {"name": "Min", "email": "m@example.com", "zone": "Cotonou"},
        "items": [{"id": "wix-008", "name": "x", "qty": 1, "price": 1}],
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 400
    assert "5,000 F CFA" in r.get_json()["error"]


def test_the_delivery_snapshot_is_persisted_with_the_order(client):
    """The order row must carry the fare the server quoted, for the WhatsApp
    follow-up, long after this request ends."""
    tok = _csrf(client)
    r = _order(client, tok, "JA-DZSNAP", "Lagos Mainland", currency="NGN")
    assert r.status_code == 200, r.data
    row = one("SELECT payload FROM orders WHERE id='JA-DZSNAP'")
    import json
    payload = row["payload"] if isinstance(row["payload"], dict) \
        else json.loads(row["payload"])
    d = payload["delivery"]
    assert d["zone_id"] == "lagos-mainland"
    assert (d["delivery_fee_min"], d["delivery_fee_max"]) == (2000, 5000)


def test_deleting_a_zone_does_not_touch_stored_orders(client):
    tok = _csrf(client)
    assert _order(client, tok, "JA-DZKEEP", "Cotonou").status_code == 200
    admin = _login(client)
    r = client.delete("/api/admin/delivery-zones/cotonou",
                      headers={"X-CSRF-Token": admin})
    assert r.status_code == 200, r.data
    import json
    row = one("SELECT payload FROM orders WHERE id='JA-DZKEEP'")
    payload = row["payload"] if isinstance(row["payload"], dict) \
        else json.loads(row["payload"])
    assert payload["delivery"]["zone_id"] == "cotonou"
    # and the zone is now refused at checkout
    assert _order(client, _csrf(client), "JA-DZGONE", "Cotonou").status_code == 400


# --------------------------------------------------------------------------
# admin CRUD
# --------------------------------------------------------------------------

def test_zone_admin_routes_require_a_login(client):
    assert client.get("/api/admin/delivery-zones").status_code in (401, 403)
    r = client.post("/api/admin/delivery-zones",
                    json={"name": "Sneaky", "currency": "CFA"})
    assert r.status_code in (401, 403)
    r = client.delete("/api/admin/delivery-zones/cotonou")
    assert r.status_code in (401, 403)


def test_zone_writes_require_csrf(client):
    _login(client)
    r = client.post("/api/admin/delivery-zones",
                    json={"name": "No CSRF", "currency": "CFA",
                          "kind": "delivery", "fare_min": 1, "fare_max": 2})
    assert r.status_code in (400, 403)


def test_an_admin_can_create_a_zone_and_checkout_accepts_it(client):
    tok = _login(client)
    r = client.post("/api/admin/delivery-zones", headers={"X-CSRF-Token": tok},
                    json={"name": "Parakou", "currency": "CFA",
                          "kind": "delivery", "fare_min": 4000,
                          "fare_max": 7000, "sort_order": 11})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["zone"]["id"] == "parakou", "the id is derived from the name"
    assert body["zone"]["fare_max"] == 7000
    # the response carries the reloaded list, so the portal repaints from the
    # server rather than from the form
    assert "Parakou" in [z["name"] for z in body["zones"]]
    # ...and it is live at checkout immediately
    r2 = _order(client, _csrf(client), "JA-DZNEW", "Parakou")
    assert r2.status_code == 200, r2.data
    assert r2.get_json()["delivery"]["delivery_fee_max"] == 7000
    # ...and served to the storefront
    served = client.get("/api/site").get_json()["site"]["delivery_zones"]
    assert "Parakou" in [z["name"] for z in served]


def test_an_admin_edit_changes_the_fare_the_checkout_quotes(client):
    tok = _login(client)
    client.post("/api/admin/delivery-zones", headers={"X-CSRF-Token": tok},
                json={"id": "cotonou", "name": "Cotonou", "currency": "CFA",
                      "kind": "delivery", "fare_min": 2000, "fare_max": 4500,
                      "sort_order": 4})
    r = _order(client, _csrf(client), "JA-DZEDIT", "Cotonou")
    assert r.status_code == 200, r.data
    d = r.get_json()["delivery"]
    assert (d["delivery_fee_min"], d["delivery_fee_max"]) == (2000, 4500)


def test_deleting_an_unknown_zone_is_a_404(client):
    tok = _login(client)
    r = client.delete("/api/admin/delivery-zones/nope",
                      headers={"X-CSRF-Token": tok})
    assert r.status_code == 404


@pytest.mark.parametrize("payload,fragment", [
    ({"name": "", "currency": "CFA", "kind": "delivery",
      "fare_min": 1, "fare_max": 2}, "name"),
    ({"name": "X", "currency": "EUR", "kind": "delivery",
      "fare_min": 1, "fare_max": 2}, "currency"),
    ({"name": "X", "currency": "CFA", "kind": "teleport",
      "fare_min": 1, "fare_max": 2}, "kind"),
    ({"name": "X", "currency": "CFA", "kind": "delivery",
      "fare_min": 0, "fare_max": 0}, "maximum fare"),
    ({"name": "X", "currency": "CFA", "kind": "delivery",
      "fare_min": -1, "fare_max": 2}, "negative"),
    ({"name": "X", "currency": "CFA", "kind": "delivery",
      "fare_min": "abc", "fare_max": 2}, "whole numbers"),
])
def test_invalid_zones_are_rejected_with_a_real_message(client, payload, fragment):
    tok = _login(client)
    r = client.post("/api/admin/delivery-zones", headers={"X-CSRF-Token": tok},
                    json=payload)
    assert r.status_code == 400, r.data
    assert fragment.lower() in r.get_json()["error"].lower()


def test_a_reversed_range_is_corrected_rather_than_stored(client):
    """min > max would let a customer be quoted an inverted range."""
    clean, err = delivery.validate_zone_payload(
        {"name": "Flipped", "currency": "CFA", "kind": "delivery",
         "fare_min": 9000, "fare_max": 1000})
    assert err is None
    assert (clean["fare_min"], clean["fare_max"]) == (1000, 9000)


def test_a_zone_id_must_be_a_slug(client):
    tok = _login(client)
    r = client.post("/api/admin/delivery-zones", headers={"X-CSRF-Token": tok},
                    json={"id": "DROP TABLE", "name": "Evil",
                          "currency": "CFA", "kind": "delivery",
                          "fare_min": 1, "fare_max": 2})
    assert r.status_code == 400
    assert "slug" in r.get_json()["error"].lower()


def test_names_are_normalised_not_stored_verbatim():
    clean, err = delivery.validate_zone_payload(
        {"name": "  Lagos    Mainland  ", "currency": "cfa",
         "kind": "DELIVERY", "fare_min": "100", "fare_max": "200"})
    assert err is None
    assert clean["name"] == "Lagos Mainland"
    assert clean["currency"] == "CFA"
    assert clean["kind"] == "delivery"
    assert clean["fare_min"] == 100 and clean["fare_max"] == 200


def test_zone_lookup_accepts_id_or_name_case_insensitively():
    assert delivery.zone_for("cotonou")["id"] == "cotonou"
    assert delivery.zone_for("COTONOU")["id"] == "cotonou"
    assert delivery.zone_for("  Cotonou ")["id"] == "cotonou"
    assert delivery.zone_for("lagos-island")["name"] == "Lagos Island"
    assert delivery.zone_for("") is None
    assert delivery.zone_for(None) is None


# --------------------------------------------------------------------------
# the frontend reads the server, not a hardcoded list
# --------------------------------------------------------------------------

def test_the_storefront_renders_zones_from_the_server():
    app_js = open(os.path.join(ROOT, "js", "app.js"), encoding="utf-8").read()
    assert "function paintDeliveryZones(" in app_js
    assert "site.delivery_zones" in app_js
    # the old client-side guessing must be gone
    assert "collect\\s+in\\s+store" not in app_js
    assert "Ensure pickup free option exists" not in app_js


def test_checkout_html_does_not_pretend_to_be_the_source_of_truth():
    html = open(os.path.join(ROOT, "checkout.html"), encoding="utf-8").read()
    assert "do not edit fares here" in html.lower()


def test_the_admin_portal_has_a_zone_editor():
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert "function deliveryZonesPanel(" in admin_js
    assert "api/admin/delivery-zones" in admin_js
    assert "function bindDeliveryZones(" in admin_js
    # success only after the server confirms, then repaint from the response
    assert "dzCache = res.zones" in admin_js
    # no duplicate submission while in flight
    assert "btn.disabled = true" in admin_js
