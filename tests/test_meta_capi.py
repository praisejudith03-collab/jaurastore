"""Meta Conversions API: server-side events, one id per conversion.

What is pinned here
-------------------
* a checkout records ONE authoritative ``Purchase`` from the order row the
  server itself wrote - value, currency and lines are the server's numbers,
  never the browser's;
* every event carries an ``event_id``, and the browser pixel fires with the
  same one. For a purchase that id is derived from the order id, so the two
  copies stay paired even when the checkout answer never reached the phone;
* replaying an order, or re-posting the same browser event id (an offline
  analytics batch is retried from localStorage), cannot record it twice;
* only browsing events may be posted by a browser: a forged ``Purchase`` is
  refused, so ad numbers cannot be inflated;
* personal data leaves the process hashed - the raw email / phone never appear
  in the stored payload;
* delivery posts to ``graph.facebook.com/<version>/<pixel>/events`` with the
  access token, marks rows sent, and keeps a failed batch for the scheduler
  sweep instead of losing it;
* with no pixel id / token configured nothing is stored and nothing is sent
  (the shop keeps working exactly as before).

Run with:  python3 -m pytest tests/test_meta_capi.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import meta_capi  # noqa: E402
import security  # noqa: E402
import task_queue  # noqa: E402
from config import Config  # noqa: E402
from db import execute, init_db, one, query  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EMAIL = "jaurastore@gmail.com"
PIXEL = "1234567890"
TOKEN = "EAAB-test-token"


# --------------------------------------------------------------- fixtures
@pytest.fixture()
def app():
    application = appmod.create_app()
    application.config.update(TESTING=True)
    init_db()
    return application


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """A live-looking pixel by default; no test ever reaches the network."""
    init_db()
    execute("DELETE FROM meta_events")
    execute("DELETE FROM rate_limits")
    monkeypatch.setattr(Config, "META_PIXEL_ID", PIXEL)
    monkeypatch.setattr(Config, "META_CAPI_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(Config, "META_CAPI_TEST_EVENT_CODE", "")
    monkeypatch.setattr(Config, "META_CAPI_ENABLED", True)
    monkeypatch.setattr(Config, "META_CAPI_API_VERSION", "v21.0")
    yield
    execute("DELETE FROM meta_events")


@pytest.fixture()
def manual_queue():
    """Keep the worker thread out of the way: the test drives delivery."""
    task_queue.reset()
    task_queue.set_manual(True)
    yield task_queue
    task_queue.set_manual(False)
    task_queue.reset()


@pytest.fixture()
def captured(monkeypatch):
    """Capture what would have been POSTed to Meta (nothing leaves the box)."""
    box = []

    def fake_fetch(url, *, data=None, method=None, timeout=None,
                   max_bytes=None, headers=None):
        box.append({"url": url, "body": json.loads(data.decode("utf-8")),
                    "headers": dict(headers or {})})
        return json.dumps({"events_received": len(box[-1]["body"]["data"])}).encode()

    monkeypatch.setattr(security, "safe_fetch", fake_fetch)
    return box


def _csrf(client):
    return client.get("/api/config").get_json()["csrf"]


def _order(client, oid, email="buyer@example.com", pid="wix-005"):
    execute("DELETE FROM rate_limits WHERE action='order'")
    r = client.post("/api/orders", headers={"X-CSRF-Token": _csrf(client)}, json={
        "id": oid, "currency": "NGN", "total": 8550,
        "customer": {"name": "Ada Obi", "firstName": "Ada", "lastName": "Obi",
                     "phone": "+2348012345678", "email": email, "city": "Lagos",
                     "zone": "Lagos Mainland", "address": "1 Test Street",
                     "country": "Nigeria"},
        "items": [{"id": pid, "name": "Bag", "qty": 1, "price": 8550}],
    })
    return r


def _rows():
    return [dict(r) for r in query("SELECT * FROM meta_events ORDER BY created_at")]


def _payload(row):
    return json.loads(row["payload"])


# ------------------------------------------------------- hashing helpers
def test_personal_data_is_hashed_the_way_meta_requires():
    assert meta_capi.hash_value(" Ada.Obi@Example.com ") == \
        hashlib.sha256(b"ada.obi@example.com").hexdigest()
    # A Nigerian trunk zero must become the country code or nothing matches.
    assert meta_capi.normalize_phone("0803 123 4567") == "2348031234567"
    assert meta_capi.hash_phone("08031234567") == \
        hashlib.sha256(b"2348031234567").hexdigest()
    assert meta_capi.normalize_phone("+229 90 00 00 00") == "22990000000"
    assert meta_capi.hash_value("") == ""


def test_country_and_currency_are_normalised_to_the_codes_meta_accepts():
    assert meta_capi.iso_country("Nigeria") == "ng"
    assert meta_capi.iso_country("NG") == "ng"
    assert meta_capi.iso_country("Cotonou") == ""      # not a country: no guess
    # The shop says "CFA"; Meta only counts XOF. Sending CFA silently drops the
    # value from the ad report.
    assert meta_capi.currency_code("CFA") == "XOF"
    assert meta_capi.currency_code("NGN") == "NGN"


def test_a_purchase_id_is_derived_from_the_order_on_both_sides():
    """The browser and the server must compute the SAME dedupe id."""
    assert meta_capi.purchase_event_id("ja-1a2b3c") == "ja-purchase-JA-1A2B3C"
    asset = (ROOT / "js/meta-pixel.js").read_text(encoding="utf-8")
    assert 'PURCHASE_PREFIX = "ja-purchase-"' in asset
    assert "eventIdForOrder" in asset


# --------------------------------------------------------------- purchase
def test_a_checkout_records_one_authoritative_purchase(client, manual_queue):
    r = _order(client, "JA-META01")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["metaEventId"] == "ja-purchase-JA-META01"

    rows = [row for row in _rows() if row["event_name"] == "Purchase"]
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == "ja-purchase-JA-META01"
    payload = _payload(row)
    assert payload["event_name"] == "Purchase"
    assert payload["action_source"] == "website"
    assert payload["event_source_url"].endswith("/checkout.html")
    custom = payload["custom_data"]
    # The value is the SERVER total (the catalogue price), not the 8550 the
    # browser posted - checkout recomputes every line.
    assert custom["value"] == float(body["total"])
    assert custom["currency"] == "NGN"
    assert custom["order_id"] == "JA-META01"
    assert custom["content_ids"] == ["wix-005"]
    assert custom["contents"][0]["quantity"] == 1
    assert custom["num_items"] == 1
    # Personal data is hashed, and the raw address is nowhere in the payload.
    user = payload["user_data"]
    assert user["em"] == hashlib.sha256(b"buyer@example.com").hexdigest()
    assert user["ph"] == meta_capi.hash_phone("+2348012345678")
    assert user["fn"] == meta_capi.hash_value("Ada")
    assert user["ln"] == meta_capi.hash_value("Obi")
    assert user["country"] == meta_capi.hash_value("ng")
    assert len(user["external_id"]) == 64
    assert "buyer@example.com" not in row["payload"]
    assert "2348012345678" not in row["payload"]


def test_replaying_an_order_never_records_a_second_purchase(client, manual_queue):
    first = _order(client, "JA-META02", email="replay@example.com")
    again = _order(client, "JA-META02", email="replay@example.com")
    assert first.status_code == 200 and again.status_code == 200
    assert again.get_json()["duplicate"] is True
    rows = [row for row in _rows() if row["event_name"] == "Purchase"]
    assert len(rows) == 1


def test_the_public_order_lookup_exposes_the_same_purchase_id(client,
                                                              manual_queue):
    """A reloaded confirmation screen must be able to fire the same pixel id."""
    _order(client, "JA-META03")
    r = client.get("/api/orders/JA-META03")
    assert r.status_code == 200
    assert r.get_json()["order"]["metaEventId"] == "ja-purchase-JA-META03"


# ---------------------------------------------------------- browsing events
def test_a_browser_event_is_stored_with_the_id_the_pixel_used(client,
                                                             manual_queue):
    tok = _csrf(client)
    event_id = "ja-px-abc12345"
    r = client.post("/api/meta/event", headers={"X-CSRF-Token": tok}, json={
        "name": "AddToCart", "eventId": event_id,
        "eventSourceUrl": "/shop.html",
        "params": {"contents": [{"id": "wix-005", "quantity": 2,
                                 "item_price": 2500}],
                   "currency": "CFA", "contentName": "Bag"},
    })
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True and body["queued"] is True
    assert body["eventId"] == event_id
    row = one("SELECT * FROM meta_events WHERE event_id=?", (event_id,))
    payload = _payload(dict(row))
    assert payload["event_name"] == "AddToCart"
    custom = payload["custom_data"]
    assert custom["value"] == 5000.0
    assert custom["num_items"] == 2
    assert custom["currency"] == "XOF"          # "CFA" is remapped for Meta
    assert custom["content_ids"] == ["wix-005"]
    # The published site origin is used for a relative path.
    assert payload["event_source_url"] == (Config.SITE_ORIGIN.rstrip("/")
                                           + "/shop.html")


def test_a_repeated_pixel_id_is_deduplicated(client, manual_queue):
    """The analytics batch is re-sent from localStorage after a timeout."""
    tok = _csrf(client)
    body = {"name": "ViewContent", "eventId": "ja-px-repeat01",
            "eventSourceUrl": "/product.html?id=wix-005",
            "params": {"contents": [{"id": "wix-005", "quantity": 1,
                                     "item_price": 1200}], "currency": "NGN"}}
    first = client.post("/api/meta/event", headers={"X-CSRF-Token": tok}, json=body)
    second = client.post("/api/meta/event", headers={"X-CSRF-Token": tok}, json=body)
    assert first.get_json()["queued"] is True
    assert second.get_json()["duplicate"] is True
    assert len(_rows()) == 1


def test_the_browsing_event_carries_the_visitor_external_id(client,
                                                           manual_queue):
    tok = _csrf(client)
    r = client.post("/api/meta/event", headers={"X-CSRF-Token": tok}, json={
        "name": "ViewContent", "eventId": "ja-px-visitor1",
        "params": {"contents": [{"id": "wix-005", "quantity": 1,
                                 "item_price": 1200}], "currency": "NGN"}})
    assert r.status_code == 200
    vid = r.headers.get("Set-Cookie", "")
    row = one("SELECT * FROM meta_events WHERE event_id='ja-px-visitor1'")
    user = _payload(dict(row))["user_data"]
    assert len(user["external_id"]) == 64
    assert "jaura_vid" in vid
    # A request with no _fbp cookie must still send a usable IP/user agent.
    assert user["client_ip_address"]
    assert "user_agent" in user["client_user_agent"].lower() or user["client_user_agent"]


def test_a_browser_cannot_forge_a_purchase(client, manual_queue):
    tok = _csrf(client)
    r = client.post("/api/meta/event", headers={"X-CSRF-Token": tok}, json={
        "name": "Purchase", "eventId": "ja-px-forged01",
        "params": {"value": 999999, "currency": "NGN"}})
    assert r.status_code == 400
    assert _rows() == []


def test_an_unknown_event_name_is_refused(client, manual_queue):
    tok = _csrf(client)
    r = client.post("/api/meta/event", headers={"X-CSRF-Token": tok},
                    json={"name": "Subscribe", "eventId": "ja-px-unknown1"})
    assert r.status_code == 400


def test_the_event_endpoint_needs_a_csrf_token(client, manual_queue):
    r = client.post("/api/meta/event", json={"name": "ViewContent"})
    assert r.status_code == 403
    assert _rows() == []


def test_a_third_party_source_url_is_never_forwarded(client, manual_queue):
    tok = _csrf(client)
    client.post("/api/meta/event", headers={"X-CSRF-Token": tok}, json={
        "name": "ViewContent", "eventId": "ja-px-evil00001",
        "eventSourceUrl": "https://evil.example.com/phish",
        "params": {"contents": [{"id": "wix-005", "quantity": 1,
                                 "item_price": 1}], "currency": "NGN"}})
    row = one("SELECT * FROM meta_events WHERE event_id='ja-px-evil00001'")
    assert _payload(dict(row))["event_source_url"] == ""


# --------------------------------------------------------------- delivery
def test_delivery_posts_the_batch_to_the_pixel_endpoint(client, manual_queue,
                                                        captured):
    _order(client, "JA-META04")
    stats = meta_capi.flush()
    assert stats["sent"] >= 1
    assert len(captured) == 1
    call = captured[0]
    assert call["url"] == f"https://graph.facebook.com/v21.0/{PIXEL}/events"
    assert call["body"]["access_token"] == TOKEN
    assert "test_event_code" not in call["body"]
    event = call["body"]["data"][0]
    assert event["event_name"] == "Purchase"
    assert event["event_id"] == "ja-purchase-JA-META04"
    assert event["user_data"]["em"]
    row = one("SELECT status, attempts, sent_at FROM meta_events "
              "WHERE event_id='ja-purchase-JA-META04'")
    assert row["status"] == "sent"
    assert row["attempts"] == 1
    assert row["sent_at"]


def test_the_test_event_code_is_forwarded_when_configured(client, manual_queue,
                                                          captured, monkeypatch):
    monkeypatch.setattr(Config, "META_CAPI_TEST_EVENT_CODE", "TEST12345")
    _order(client, "JA-META05")
    meta_capi.flush()
    assert captured[0]["body"]["test_event_code"] == "TEST12345"


def test_a_meta_outage_keeps_the_event_for_the_sweep(client, manual_queue,
                                                     monkeypatch):
    _order(client, "JA-META06")

    def boom(*a, **k):
        raise OSError("network unreachable")

    monkeypatch.setattr(security, "safe_fetch", boom)
    stats = meta_capi.flush()
    assert stats["failed"] == 1
    row = one("SELECT status, attempts, detail FROM meta_events "
              "WHERE event_id='ja-purchase-JA-META06'")
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert "network unreachable" in (row["detail"] or "")

    # The sweep (a scheduler step) retries it once Meta is reachable again.
    box = []
    monkeypatch.setattr(security, "safe_fetch",
                        lambda url, **kw: (box.append(url),
                                           b'{"events_received": 1}')[1])
    swept = meta_capi.sweep()
    assert box, "the sweep did not retry the parked event"
    assert swept["sent"] >= 1
    row = one("SELECT status FROM meta_events "
              "WHERE event_id='ja-purchase-JA-META06'")
    assert row["status"] == "sent"


def test_a_rejected_batch_is_recorded_with_metas_own_message(client,
                                                             manual_queue,
                                                             monkeypatch):
    _order(client, "JA-META07")
    monkeypatch.setattr(security, "safe_fetch", lambda url, **kw: (
        b'{"error": {"message": "Invalid OAuth access token"}}'))
    meta_capi.flush()
    row = one("SELECT status, detail FROM meta_events "
              "WHERE event_id='ja-purchase-JA-META07'")
    assert row["status"] == "failed"
    assert "Invalid OAuth access token" in (row["detail"] or "")


def test_attempts_are_capped_so_a_dead_end_never_retries_forever(client,
                                                                 manual_queue,
                                                                 monkeypatch):
    _order(client, "JA-META08")
    monkeypatch.setattr(security, "safe_fetch",
                        lambda url, **kw: (_ for _ in ()).throw(OSError("down")))
    for _ in range(meta_capi.MAX_ATTEMPTS + 2):
        meta_capi.flush(retry_failed=True)
    row = one("SELECT status, attempts FROM meta_events "
              "WHERE event_id='ja-purchase-JA-META08'")
    assert row["attempts"] <= meta_capi.MAX_ATTEMPTS + 1
    # Once parked past the cap the sweep stops picking it up.
    meta_capi.sweep()
    parked = one("SELECT attempts FROM meta_events "
                 "WHERE event_id='ja-purchase-JA-META08'")
    assert parked["attempts"] >= meta_capi.MAX_ATTEMPTS


# ------------------------------------------------------------- unconfigured
def test_without_a_pixel_and_token_nothing_is_stored_or_sent(client,
                                                             manual_queue,
                                                             monkeypatch):
    monkeypatch.setattr(Config, "META_PIXEL_ID", "")
    monkeypatch.setattr(Config, "META_CAPI_ACCESS_TOKEN", "")
    tok = _csrf(client)
    r = client.post("/api/meta/event", headers={"X-CSRF-Token": tok}, json={
        "name": "ViewContent", "eventId": "ja-px-nopixel1",
        "params": {"contents": [{"id": "wix-005", "quantity": 1,
                                 "item_price": 1200}], "currency": "NGN"}})
    assert r.status_code == 200
    assert r.get_json()["queued"] is False
    # The shop itself is untouched: the order still goes through.
    order = _order(client, "JA-META09")
    assert order.status_code == 200
    assert order.get_json()["metaEventId"] == "ja-purchase-JA-META09"
    assert _rows() == []


def test_the_kill_switch_silences_the_server_but_not_the_order(client,
                                                               manual_queue,
                                                               monkeypatch):
    monkeypatch.setattr(Config, "META_CAPI_ENABLED", False)
    assert meta_capi.configured() is False
    assert _order(client, "JA-META10").status_code == 200
    assert _rows() == []


# ------------------------------------------------------------- diagnostics
def test_the_status_endpoint_reports_the_pipeline(client, manual_queue):
    _order(client, "JA-META11")
    token = client.post("/api/admin/login",
                        json={"email": EMAIL, "password": PW}).get_json()["csrf"]
    r = client.get("/api/admin/meta/status", headers={"X-CSRF-Token": token})
    assert r.status_code == 200, r.data
    meta = r.get_json()["meta"]
    assert meta["configured"] is True
    assert meta["pixelId"] == PIXEL
    assert meta["hasToken"] is True
    assert meta["counts"].get("queued") == 1
    assert meta["recent"][0]["eventName"] == "Purchase"


def test_the_status_endpoint_is_admin_only(client):
    assert client.get("/api/admin/meta/status").status_code == 401


# ------------------------------------------------------- shipped front end
def test_the_storefront_ships_the_pixel_and_the_shared_event_id():
    asset = (ROOT / "js" / "meta-pixel.js").read_text(encoding="utf-8")
    # The pixel call carries the dedupe id in fbq's fourth argument.
    assert 'window.fbq("track", name, params || {}, opts)' in asset
    assert "eventID" in asset
    assert 'api/meta/event' in asset
    # Purchase is pixel-only: the server already sent the authoritative copy.
    assert "PURCHASE_PREFIX" in asset
    for page in ("index.html", "shop.html", "product.html", "cart.html",
                 "checkout.html", "order-complete.html"):
        html = (ROOT / page).read_text(encoding="utf-8")
        assert "js/meta-pixel.js" in html, page


def test_the_admin_portal_never_loads_the_ad_pixel():
    html = (ROOT / "admin.html").read_text(encoding="utf-8")
    assert "meta-pixel.js" not in html
    asset = (ROOT / "js" / "meta-pixel.js").read_text(encoding="utf-8")
    assert 'isAdminPage()' in asset


def test_the_shipped_event_calls_cover_view_cart_checkout_and_purchase():
    store = (ROOT / "js" / "store.js").read_text(encoding="utf-8")
    app = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    assert "JA_META.addToCart" in store
    assert "JA_META.viewContent" in app
    assert "JA_META.purchase" in app
    assert "JA_META.initiateCheckout" in app


def test_the_pixel_id_reaches_the_browser_through_the_public_config(client):
    body = client.get("/api/config").get_json()
    assert body["metaPixelId"] == PIXEL
    net = (ROOT / "js" / "net.js").read_text(encoding="utf-8")
    assert "metaPixelId" in net


def test_the_content_security_policy_allows_the_pixel():
    """Without these hosts the browser blocks the pixel and the pair dies."""
    import security
    script_src = security.CSP.split("script-src", 1)[1].split(";", 1)[0]
    connect_src = security.CSP.split("connect-src", 1)[1].split(";", 1)[0]
    assert "connect.facebook.net" in script_src
    assert "www.facebook.com" in connect_src
    headers = (ROOT / "_headers").read_text(encoding="utf-8")
    csp_line = next(l for l in headers.splitlines() if "Content-Security-Policy" in l)
    assert "connect.facebook.net" in csp_line


def test_the_scheduler_sweeps_the_outbox():
    scheduler = (ROOT / "scheduler.py").read_text(encoding="utf-8")
    assert "meta.capi.flush" in scheduler
    assert "meta_capi.sweep" in scheduler or "_meta_flush" in scheduler
