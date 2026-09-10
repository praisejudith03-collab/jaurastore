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
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("SITE_CONFIG_PATH", "/tmp/jaura_test_site_zones.json")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
import delivery  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

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

    # Stale Update button regression. Saving a zone used to finish with a full
    # paintDesk("delivery"): the whole desk was rebuilt, which swallowed the
    # table repaint and left the button reading "Update zone" over an empty
    # form, so the owner could not tell whether the fare had stuck. A save (or
    # delete) now repaints the table body from the server's answer and clears
    # the editor in place.
    bind = admin_js[admin_js.index("function bindDeliveryZones("):]
    bind = bind[: bind.index("\n}\n")]
    assert 'paintDesk("delivery")' not in bind, (
        "a zone save or delete must not repaint the whole delivery desk - "
        "repaint the table body, or the Update button goes stale")
    assert "paintZoneTable()" in bind, "the table must refresh from the server list"
    assert "function resetZoneForm(" in admin_js, (
        "one shared reset keeps the button, the hidden id and Cancel honest")
    assert "resetZoneForm(form)" in bind
    # the label comes from the mode, never from a label captured before the
    # request, so a failed save cannot leave the button stuck on "Saving…"
    assert 'btn.textContent = isUpdate ? "Update zone" : "Save zone"' in bind
    # an edit fills the whole row, and a row the cache no longer has refetches
    assert "form.zone_sort.value = z.sort_order" in bind
    assert "loadDeliveryZones().then(() => paintZoneTable())" in bind
    # a delete shows progress and puts the row button back if it fails
    assert "Deleting…" in bind
    assert "del.textContent = origLabel || \"Delete\"" in bind


# --------------------------------------------------------------------------
# Supabase-first regression: the owner reported Lomé 1500-2500 does not stick
# after Update zone. That was a multi-worker cache bug: worker A saved to
# Supabase, invalidated its own _CACHE, but worker B kept serving the old
# cached 1000-3000. When Supabase is enabled there must be NO long-lived
# cache and the save must write Supabase FIRST then re-read it.
# --------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeDeliveryTable:
    def __init__(self, store, fail_upsert=False, fail_delete=False):
        self._store = store
        self._op = None
        self._filter_id = None
        self._limit = None
        self._upsert_row = None
        self._fail_upsert = fail_upsert
        self._fail_delete = fail_delete

    def select(self, *_a, **_kw):
        self._op = "select"
        return self

    def upsert(self, row, **_kw):
        self._op = "upsert"
        self._upsert_row = dict(row) if isinstance(row, dict) else row
        return self

    def delete(self, **_kw):
        self._op = "delete"
        return self

    def eq(self, col, val):
        if col == "id":
            self._filter_id = str(val or "").strip().lower()
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        if self._op == "upsert":
            if self._fail_upsert:
                raise RuntimeError("supabase down")
            row = dict(self._upsert_row)
            self._store[row["id"]] = row
            return _FakeResult([row])
        if self._op == "select":
            if self._filter_id:
                row = self._store.get(self._filter_id)
                data = [row] if row else []
                if self._limit:
                    data = data[: self._limit]
                return _FakeResult(data)
            return _FakeResult(list(self._store.values()))
        if self._op == "delete":
            if self._fail_delete:
                raise RuntimeError("supabase delete down")
            if self._filter_id and self._filter_id in self._store:
                del self._store[self._filter_id]
            return _FakeResult([])
        return _FakeResult([])


class _FakeSupabaseClient:
    def __init__(self, store, fail_upsert=False, fail_delete=False):
        self._store = store
        self._fail_upsert = fail_upsert
        self._fail_delete = fail_delete

    def table(self, name):
        if name == "delivery_zones":
            return _FakeDeliveryTable(
                self._store, fail_upsert=self._fail_upsert, fail_delete=self._fail_delete
            )
        raise AssertionError(f"unexpected table {name!r} in fake")


def test_supabase_save_is_source_of_truth_not_sqlite_cache(monkeypatch):
    """When Supabase is enabled the save writes Supabase FIRST, re-reads it,
    and zones() bypasses any long-lived cache.

    This is the Lomé 1500-2500 regression: the admin edits a fare, it looks
    saved, but a stale worker cache serves the old fare.
    """
    import supabase_store

    supa_store = {
        "lome": {
            "id": "lome",
            "name": "Lomé",
            "currency": "CFA",
            "fare_min": 1000,
            "fare_max": 3000,
            "kind": "delivery",
            "active": True,
            "sort_order": 5,
            "note": "",
        }
    }

    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: _FakeSupabaseClient(supa_store))

    delivery._invalidate()
    delivery._CACHE["zones"] = None

    zs = delivery.zones()
    lome = next((z for z in zs if z["id"] == "lome"), None)
    assert lome is not None
    assert (lome["fare_min"], lome["fare_max"]) == (1000, 3000)

    saved, err = delivery.save_zone(
        "lome",
        {
            "name": "Lomé",
            "currency": "CFA",
            "kind": "delivery",
            "fare_min": 1500,
            "fare_max": 2500,
            "sort_order": 5,
        },
    )
    assert err is None, err
    assert saved is not None
    assert (saved["fare_min"], saved["fare_max"]) == (1500, 2500)
    assert saved["name"] == "Lomé"

    assert supa_store["lome"]["fare_min"] == 1500
    assert supa_store["lome"]["fare_max"] == 2500

    from db import query as db_query

    row = db_query("SELECT fare_min, fare_max FROM delivery_zones WHERE id=?", ("lome",))
    assert row and row[0]["fare_min"] == 1500 and row[0]["fare_max"] == 2500

    execute("UPDATE delivery_zones SET fare_min=9999, fare_max=9999 WHERE id='lome'")
    delivery._CACHE["zones"] = [
        {
            "id": "lome",
            "name": "Lomé",
            "currency": "CFA",
            "fare_min": 1111,
            "fare_max": 2222,
            "kind": "delivery",
            "active": True,
            "sort_order": 5,
            "note": "",
        }
    ]

    zs2 = delivery.zones()
    lome2 = next((z for z in zs2 if z["id"] == "lome"), None)
    assert lome2 is not None, "Lomé must still come from Supabase"
    assert (lome2["fare_min"], lome2["fare_max"]) == (1500, 2500), (
        "zones() must bypass SQLite mirror and _CACHE when Supabase enabled - "
        "otherwise Lomé 1500-2500 looks like it never stuck"
    )

    delivery._invalidate()


def test_supabase_save_failure_does_not_touch_sqlite(monkeypatch):
    """If Supabase upsert fails, save_zone must return an error and leave
    SQLite untouched, so the admin can retry instead of seeing a local-only
    row that vanishes on the next worker/deploy.
    """
    import supabase_store

    supa_store = {}

    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(
        supabase_store, "client", lambda: _FakeSupabaseClient(supa_store, fail_upsert=True)
    )

    delivery._invalidate()

    execute("DELETE FROM delivery_zones WHERE id='parakou'")
    from db import query as db_query

    assert not db_query("SELECT id FROM delivery_zones WHERE id='parakou'")

    saved, err = delivery.save_zone(
        "parakou",
        {
            "name": "Parakou",
            "currency": "CFA",
            "kind": "delivery",
            "fare_min": 4000,
            "fare_max": 7000,
            "sort_order": 11,
        },
    )
    assert saved is None
    assert err is not None
    assert "Supabase" in err

    assert "parakou" not in supa_store

    assert not db_query("SELECT id FROM delivery_zones WHERE id='parakou'"), (
        "on Supabase failure SQLite must stay untouched"
    )

    monkeypatch.setattr(supabase_store, "client", lambda: _FakeSupabaseClient(supa_store))
    saved_ok, err_ok = delivery.save_zone(
        "parakou",
        {
            "name": "Parakou",
            "currency": "CFA",
            "kind": "delivery",
            "fare_min": 4000,
            "fare_max": 7000,
            "sort_order": 11,
        },
    )
    assert err_ok is None
    assert supa_store["parakou"]["fare_min"] == 4000
    row_before = db_query("SELECT fare_min, fare_max FROM delivery_zones WHERE id='parakou'")[0]
    assert (row_before["fare_min"], row_before["fare_max"]) == (4000, 7000)

    monkeypatch.setattr(
        supabase_store, "client", lambda: _FakeSupabaseClient(supa_store, fail_upsert=True)
    )
    saved2, err2 = delivery.save_zone(
        "parakou",
        {
            "name": "Parakou",
            "currency": "CFA",
            "kind": "delivery",
            "fare_min": 9999,
            "fare_max": 9999,
            "sort_order": 11,
        },
    )
    assert saved2 is None and err2 is not None
    row_after = db_query("SELECT fare_min, fare_max FROM delivery_zones WHERE id='parakou'")[0]
    assert (row_after["fare_min"], row_after["fare_max"]) == (4000, 7000)
    assert supa_store["parakou"]["fare_min"] == 4000

    delivery._invalidate()
    execute("DELETE FROM delivery_zones WHERE id='parakou'")


# --------------------------------------------------------------------------
# Legacy live-table regression: the PRODUCTION delivery_zones table predates
# the zone editor and still has `zone_name text not null` with no default
# (schema_sections/14_delivery_seeds.sql detects and populates it when it
# seeds). save_zone used to upsert only the current columns, so Postgres
# answered 23502 and EVERY admin save failed - and because Supabase is the
# source of truth, SQLite was never written either (the Lomé edit that never
# stuck). save_zone now repairs its payload against the live table: 23502 is
# answered by filling the named column from the zone, an unknown column is
# dropped, a wrong type is walked through 0/False/"" - bounded, one repair
# per attempt, the discovered columns and the fill values that finally
# satisfied them cached per worker.
#
# The fakes below enforce the legacy NOT NULL columns and raise the REAL
# postgrest APIError shapes; str(APIError(...)) is the exact dict the owner
# saw.
# --------------------------------------------------------------------------

from postgrest.exceptions import APIError  # noqa: E402


def _api_error(code, message, details=None):
    return APIError({"message": message, "code": code,
                     "details": details, "hint": None})


class _LegacyZoneTable(_FakeDeliveryTable):
    """delivery_zones as the LIVE table behaves: a schema check first
    (PGRST204 for columns the table lacks), then NOT NULL checks (23502 for
    required columns absent from the payload), then value coercion (22P02).

    `required` maps column -> a value checker: when the payload omits the
    column the upsert raises 23502; when it includes the column the checker
    receives the value and may raise 22P02 (a typed NOT NULL column the app
    cannot fill correctly) or accept the row.
    """

    def __init__(self, store, required=None, missing=(), **kw):
        super().__init__(store, **kw)
        self.required = dict(required or {})
        self.missing = tuple(missing)
        self.attempts = []

    def execute(self):
        if self._op == "upsert":
            self.attempts.append(dict(self._upsert_row or {}))
            row = dict(self._upsert_row or {})
            for col in self.missing:
                if col in row:
                    raise _api_error(
                        "PGRST204",
                        f"Could not find the '{col}' column of "
                        "'delivery_zones' in the schema cache")
            for col, accept in self.required.items():
                if col not in row:
                    raise _api_error(
                        "23502",
                        f'null value in column "{col}" of relation '
                        '"delivery_zones" violates not-null constraint',
                        details=f"Failing row contains ({row.get('id')}, ...)")
                problem = accept(row[col])
                if problem:
                    raise _api_error(
                        "22P02",
                        f"invalid input syntax for type {problem}: "
                        f"{row[col]!r}")
            row = dict(row)
            self._store[row["id"]] = row
            return _FakeResult([row])
        return super().execute()


class _LegacySupabaseClient(_FakeSupabaseClient):
    def __init__(self, store, table):
        super().__init__(store)
        self._table = table

    def table(self, name):
        if name == "delivery_zones":
            return self._table
        raise AssertionError(f"unexpected table {name!r} in fake")


@pytest.fixture()
def _blank_zone_shape():
    """The discovered-table-shape cache is per worker; a test must not
    inherit what an earlier test's fake table taught this worker. Tolerant
    of pre-fix code so the regressions fail there with the production
    23502 instead of an AttributeError."""
    shape = getattr(delivery, "_ZONE_SHAPE", None)
    if shape is not None:
        shape["fill"].clear()
        shape["drop"].clear()
        shape.get("values", {}).clear()
    yield
    if shape is not None:
        shape["fill"].clear()
        shape["drop"].clear()
        shape.get("values", {}).clear()


_LIVE_LIKE = {"zone_name": lambda v: None}  # text NOT NULL: anything is fine


def test_save_fills_the_legacy_zone_name_column(monkeypatch, _blank_zone_shape):
    """The production 23502: the editor's row has no zone_name, the live
    table requires it. The save must fill it from the zone and land."""
    import supabase_store

    supa_store = {}
    table = _LegacyZoneTable(supa_store, required=_LIVE_LIKE)
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: _LegacySupabaseClient(supa_store, table))

    saved, err = delivery.save_zone(
        "lagos-mainland",
        {"name": "Lagos Mainland", "currency": "NGN", "kind": "delivery",
         "fare_min": 2500, "fare_max": 5000, "sort_order": 1, "active": True},
    )
    assert err is None, err
    assert saved is not None and saved["id"] == "lagos-mainland"
    # the legacy column was filled from the zone name
    assert supa_store["lagos-mainland"]["zone_name"] == "Lagos Mainland"
    assert (supa_store["lagos-mainland"]["fare_min"],
            supa_store["lagos-mainland"]["fare_max"]) == (2500, 5000)
    # at least one repair attempt happened - the first was rejected
    assert len(table.attempts) >= 2
    assert "zone_name" not in table.attempts[0]
    assert "zone_name" in table.attempts[1]
    # Supabase confirmed, so the SQLite mirror is written too
    from db import query as db_query
    row = db_query("SELECT fare_min, fare_max FROM delivery_zones WHERE id=?",
                   ("lagos-mainland",))
    assert row and (row[0]["fare_min"], row[0]["fare_max"]) == (2500, 5000)


def test_save_remembers_the_legacy_column_for_the_next_save(monkeypatch, _blank_zone_shape):
    """The repaired shape is cached per worker: the next save lands on the
    first attempt instead of rediscovering zone_name the hard way."""
    import supabase_store

    supa_store = {}
    table = _LegacyZoneTable(supa_store, required=_LIVE_LIKE)
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: _LegacySupabaseClient(supa_store, table))

    first, err1 = delivery.save_zone(
        "lome", {"name": "Lomé", "currency": "CFA", "kind": "delivery",
                 "fare_min": 1500, "fare_max": 2500, "sort_order": 8})
    assert err1 is None, err1
    assert first["fare_min"] == 1500
    assert len(table.attempts) >= 2, "the first save must have been repaired"

    second, err2 = delivery.save_zone(
        "lome", {"name": "Lomé", "currency": "CFA", "kind": "delivery",
                 "fare_min": 1500, "fare_max": 2500, "sort_order": 8})
    assert err2 is None, err2
    assert len(table.attempts) == 3, (
        "the second save must land on the first attempt - the discovered "
        "zone_name column is cached per worker")
    assert supa_store["lome"]["zone_name"] == "Lomé"
    # the cache is per worker state on the module, not on the client
    assert "zone_name" in delivery._ZONE_SHAPE["fill"]


def test_save_repairs_the_confirmed_live_row_then_lands_first_try(monkeypatch, _blank_zone_shape):
    """Mirror of the CONFIRMED production row: behind zone_name the live
    table still carries fee numeric, pickup_available boolean and
    pickup_address text, all NOT NULL (checked from information_schema -
    plain types only). The first save repairs the whole row - a real bool
    for the flag, 0 for the numeric, the name for the text - and the
    constant that satisfied the typed column is cached next to the column
    list, so the next save (the owner's 1500-2500 edit) lands first try."""
    import supabase_store

    supa_store = {}
    required = {
        "zone_name": lambda v: None,          # text NOT NULL: anything
        "fee": lambda v: (None
                          if isinstance(v, (int, float))
                          and not isinstance(v, bool)
                          else "numeric"),    # numeric NOT NULL
        "pickup_available": lambda v: (
            None if isinstance(v, bool) else "boolean"),
        "pickup_address": lambda v: None,     # text NOT NULL: anything
    }
    table = _LegacyZoneTable(supa_store, required=required)
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: _LegacySupabaseClient(supa_store, table))

    first, err1 = delivery.save_zone(
        "lome", {"name": "Lomé", "currency": "CFA", "kind": "delivery",
                 "fare_min": 1000, "fare_max": 3000, "sort_order": 8,
                 "active": True})
    assert err1 is None, err1
    assert (first["fare_min"], first["fare_max"]) == (1000, 3000)
    # the whole legacy row was repaired with a value each column accepts
    live = supa_store["lome"]
    assert live["zone_name"] == "Lomé"
    assert live["fee"] == 0, "the numeric is satisfied by 0"
    assert live["pickup_available"] is True, (
        "the boolean must be filled with a real bool on the first try, "
        "not a string or 0")
    assert live["pickup_address"] == "Lomé"
    repaired_attempts = len(table.attempts)
    assert repaired_attempts >= 2, "the first save must have been repaired"

    # the constant that satisfied the typed column is cached per worker
    # next to the column list
    assert delivery._ZONE_SHAPE["values"]["fee"] == 0
    assert "pickup_available" in delivery._ZONE_SHAPE["fill"]

    # the owner's edit: 1500-2500. The cached fill values pre-apply the
    # repaired shape, so this save must land on the first attempt.
    second, err2 = delivery.save_zone(
        "lome", {"name": "Lomé", "currency": "CFA", "kind": "delivery",
                 "fare_min": 1500, "fare_max": 2500, "sort_order": 8,
                 "active": True})
    assert err2 is None, err2
    assert len(table.attempts) == repaired_attempts + 1, (
        "the next save must land first try - the cached values keep the "
        "repaired row instead of re-running the round trips")
    assert (supa_store["lome"]["fare_min"], supa_store["lome"]["fare_max"]) == (1500, 2500)
    assert supa_store["lome"]["pickup_available"] is True
    assert supa_store["lome"]["fee"] == 0


def test_save_drops_a_column_the_live_table_lacks(monkeypatch, _blank_zone_shape):
    """A table narrower than the row (the products-table problem): the named
    column is dropped and the save still lands, note or no note."""
    import supabase_store

    supa_store = {}
    table = _LegacyZoneTable(supa_store, required=_LIVE_LIKE, missing=("note",))
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: _LegacySupabaseClient(supa_store, table))

    saved, err = delivery.save_zone(
        "calavi", {"name": "Calavi", "currency": "CFA", "kind": "delivery",
                   "fare_min": 1500, "fare_max": 3500, "sort_order": 5,
                   "note": "shared taxi stop"})
    assert err is None, err
    assert saved is not None
    assert "note" not in supa_store["calavi"], (
        "the stored row must not contain the column the table lacks")
    assert supa_store["calavi"]["zone_name"] == "Calavi"
    assert "note" in delivery._ZONE_SHAPE["drop"]
    # ...and the next save does not offer note again
    table.attempts.clear()
    _, err2 = delivery.save_zone(
        "calavi", {"name": "Calavi", "currency": "CFA", "kind": "delivery",
                   "fare_min": 1600, "fare_max": 3600, "sort_order": 5})
    assert err2 is None, err2
    assert "note" not in table.attempts[0]
    assert "zone_name" in table.attempts[0], "both discoveries are pre-applied"


def test_save_names_the_column_when_the_table_cannot_be_satisfied(monkeypatch, _blank_zone_shape):
    """A legacy NOT NULL column no fill value can satisfy (a timestamp, say):
    the error names the column and the one statement that repairs it, and
    Supabase AND SQLite stay untouched. A required critical column (id) is
    likewise never dropped away."""
    import supabase_store
    from db import query as db_query

    supa_store = {}
    # text NOT NULL is satisfiable (zone_name) - a timestamp is not: the
    # name fill raises 22P02, the 0 retry raises 22P02 again.
    required = dict(_LIVE_LIKE)
    required["created_at"] = lambda v: (
        None if isinstance(v, str) and _looks_timestamp(v) else "timestamp with time zone")
    table = _LegacyZoneTable(supa_store, required=required)
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: _LegacySupabaseClient(supa_store, table))

    execute("DELETE FROM delivery_zones WHERE id='porto-novo'")
    saved, err = delivery.save_zone(
        "porto-novo", {"name": "Porto-Novo", "currency": "CFA",
                       "kind": "delivery", "fare_min": 1500, "fare_max": 3500,
                       "sort_order": 6})
    assert saved is None and err is not None
    assert '"created_at"' in err, err
    assert ("alter table delivery_zones alter column created_at drop not null"
            in err), err
    assert "No changes were made" in err
    assert supa_store == {}, "nothing may reach a table that cannot be satisfied"
    assert not db_query("SELECT id FROM delivery_zones WHERE id='porto-novo'"), (
        "on an unsatisfiable Supabase table SQLite must stay untouched")

    # A table without a critical column cannot be fixed by dropping it.
    supa_store2 = {}
    table2 = _LegacyZoneTable(supa_store2, missing=("id",))
    monkeypatch.setattr(
        supabase_store, "client",
        lambda: _LegacySupabaseClient(supa_store2, table2))
    saved2, err2 = delivery.save_zone(
        "porto-novo", {"name": "Porto-Novo", "currency": "CFA",
                       "kind": "delivery", "fare_min": 1500, "fare_max": 3500,
                       "sort_order": 6})
    assert saved2 is None and err2 is not None
    assert '"id"' in err2, err2
    assert supa_store2 == {}
    assert not db_query("SELECT id FROM delivery_zones WHERE id='porto-novo'")


def _looks_timestamp(value):
    """Only a genuine timestamp string would satisfy the fake's typed column."""
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}[T ]", str(value or "")))

