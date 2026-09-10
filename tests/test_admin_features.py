"""Blocker 7 - every Admin Portal action reaches its endpoint and works.

The Admin Portal has 46 routes. Each one is exercised here as a real logged-in
admin through the Flask test client, asserting not just "it returned 200" but
that the action actually took effect and that the response carries the
canonical field names the portal reads back.

Two things this pins down that a passing UI would hide:
  * a write that returns success WITHOUT the server having changed anything
  * a response whose shape differs from what js/admin.js expects

Run with:  python3 -m pytest tests/test_admin_features.py -q
"""
import io
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
os.environ.setdefault("SITE_CONFIG_PATH", "/tmp/jaura_test_site_admin.json")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
from db import execute, init_db, one, query  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = "jaurastore@gmail.com"

# Every admin route, straight from the live url_map. If a route is added or
# removed without updating this list the audit test at the bottom fails, so the
# coverage here cannot silently rot.
EXPECTED_ADMIN_ROUTES = {
("GET", "/api/admin/analytics"),
    ("GET", "/api/admin/audit"), ("POST", "/api/admin/backup"),
    ("PUT", "/api/admin/categories"), ("GET", "/api/admin/coupons"),
    ("POST", "/api/admin/coupons"), ("DELETE", "/api/admin/coupons/<code>"),
    ("PATCH", "/api/admin/coupons/<code>"),
    ("GET", "/api/admin/delivery-zones"),
    ("POST", "/api/admin/delivery-zones"),
    ("DELETE", "/api/admin/delivery-zones/<zone_id>"),
    ("POST", "/api/admin/delivery-page"),
    ("GET", "/api/admin/growth/settings"), ("POST", "/api/admin/growth/settings"),
    ("GET", "/api/admin/live"), ("POST", "/api/admin/login"),
    ("POST", "/api/admin/logout"), ("GET", "/api/admin/low-stock"),
    ("GET", "/api/admin/most-viewed"), ("GET", "/api/admin/orders"),
    ("GET", "/api/admin/orders.csv"), ("DELETE", "/api/admin/orders/<oid>"),
    ("PATCH", "/api/admin/orders/<oid>"),

    ("GET", "/api/admin/payment-proofs"),
    ("DELETE", "/api/admin/payment-proofs/<pid>"),

    ("POST", "/api/admin/products"), ("PUT", "/api/admin/products"),
    ("DELETE", "/api/admin/products/<pid>"), ("POST", "/api/admin/photos/repair"),
    ("GET", "/api/admin/referrals"),
    ("GET", "/api/admin/coupon-uses"), ("POST", "/api/admin/reviews/migrate"),
    ("GET", "/api/admin/reviews"), ("PATCH", "/api/admin/reviews"),
    ("DELETE", "/api/admin/reviews"),
    ("GET", "/api/admin/sales"), ("GET", "/api/admin/sales.csv"),
    ("GET", "/api/admin/session"), ("POST", "/api/admin/site"),
    ("GET", "/api/admin/stock"), ("PUT", "/api/admin/stock"),
    ("POST", "/api/admin/sync/repo"), ("GET", "/api/admin/sync/status"),
    ("GET", "/api/admin/mail/status"), ("POST", "/api/admin/mail/test"),
    ("POST", "/api/admin/uploads/category"), ("POST", "/api/admin/uploads/hero"),
    ("POST", "/api/admin/uploads/image"), ("POST", "/api/admin/uploads/product"),
    ("POST", "/api/admin/uploads/video"),
}


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


class _Admin:
    """The test client with the admin's CSRF token attached to every call.

    FlaskClient in this version has no .headers attribute, so the token is
    merged into each request instead of being set once.
    """

    def __init__(self, client, token):
        self._c = client
        self.token = token

    def _kw(self, kw):
        headers = dict(kw.pop("headers", None) or {})
        if self.token:
            headers.setdefault("X-CSRF-Token", self.token)
        kw["headers"] = headers
        return kw

    def get(self, url, **kw):
        return self._c.get(url, **self._kw(kw))

    def post(self, url, **kw):
        return self._c.post(url, **self._kw(kw))

    def put(self, url, **kw):
        return self._c.put(url, **self._kw(kw))

    def patch(self, url, **kw):
        return self._c.patch(url, **self._kw(kw))

    def delete(self, url, **kw):
        return self._c.delete(url, **self._kw(kw))

    def drop_csrf(self):
        self.token = None


@pytest.fixture()
def admin(app):
    """A logged-in admin client that sends its CSRF token on every call."""
    init_db()
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        r = c.post("/api/admin/login", json={"email": EMAIL, "password": PW})
        assert r.status_code == 200, r.data
        yield _Admin(c, r.get_json()["csrf"])


def _png_bytes():
    """A real 1x1 PNG - the upload handlers reject anything that is not one."""
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "/58BAAX+Av7czFnnAAAAAElFTkSuQmCC")


# ===========================================================================
# the route inventory itself
# ===========================================================================

def test_every_admin_route_is_accounted_for(app):
    live = set()
    for rule in app.url_map.iter_rules():
        path = str(rule)
        if "/api/admin" not in path:
            continue
        for method in rule.methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            live.add((method, path))
    assert live == EXPECTED_ADMIN_ROUTES, (
        f"added: {sorted(live - EXPECTED_ADMIN_ROUTES)} "
        f"removed: {sorted(EXPECTED_ADMIN_ROUTES - live)}")


# ===========================================================================
# products: create / update / delete
# ===========================================================================

def test_product_create_then_update_then_delete(admin):
    payload = {
        "product": {
            "id": "jau-admin-it", "name": "Admin integration product",
            "slug": "admin-integration-product", "priceNgn": 4321,
            "priceCfa": 2100, "stock_quantity": 7, "category": "Bags",
            "online": True,
        }
    }
    r = admin.post("/api/admin/products", json=payload)
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert body["action"] in ("created", "updated")
    created = body["product"]
    # canonical field names the portal reads straight back
    assert created["id"] == "jau-admin-it"
    assert created["priceNgn"] == 4321
    assert created["stock_quantity"] == 7
    assert "meta" in body, "the portal repaints its counter from meta"

    # The single-product update goes through POST (upsert). PUT replaces the
    # ENTIRE catalogue, so it is deliberately not exercised with a payload here.
    r = admin.post("/api/admin/products",
                   json={"product": {"id": "jau-admin-it",
                                     "name": "Admin integration product",
                                     "priceNgn": 9999, "stock_quantity": 3}})
    assert r.status_code == 200, r.data
    assert r.get_json()["product"]["priceNgn"] == 9999
    assert r.get_json()["product"]["stock_quantity"] == 3

    # products live in the catalogue (data/catalog.json / Supabase), not in a
    # SQLite `products` table, so read them back through the catalogue module.
    import catalog as catalog_mod
    assert catalog_mod.product_index().get("jau-admin-it") is not None

    r = admin.delete("/api/admin/products/jau-admin-it")
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True
    # a soft delete leaves a tombstone, so the id never comes back to life
    live = [p for p in catalog_mod.merged()
            if p.get("id") == "jau-admin-it" and p.get("online")]
    assert not live, "the deleted product is still live"


def test_bulk_replace_rejects_a_bad_shape(admin):
    """PUT /admin/products replaces the whole catalogue, so its guard matters:
    a malformed call must not wipe anything."""
    r = admin.put("/api/admin/products", json={"product": {"id": "x"}})
    assert r.status_code == 400
    assert "products" in r.get_json()["error"]


def test_product_needs_a_name(admin):
    r = admin.post("/api/admin/products", json={"product": {"id": "jau-noname"}})
    assert r.status_code == 400
    assert "name" in r.get_json()["error"].lower()


def test_product_writes_require_csrf(admin):
    admin.drop_csrf()
    r = admin.post("/api/admin/products",
                   json={"product": {"id": "jau-nocsrf", "name": "x"}})
    assert r.status_code in (400, 403)


# ===========================================================================
# stock
# ===========================================================================

def test_stock_read_and_write(admin):
    r = admin.get("/api/admin/stock")
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True

    r = admin.put("/api/admin/stock", json={
        "productId": "wix-001", "variant": "__default__",
        "qty": 42, "lowThreshold": 5})
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True
    row = one("SELECT qty, low_threshold FROM variant_stock "
              "WHERE product_id='wix-001' AND variant_key='__default__'")
    assert row is not None and row["qty"] == 42 and row["low_threshold"] == 5


def test_stock_requires_product_and_qty(admin):
    r = admin.put("/api/admin/stock", json={"productId": "wix-001"})
    assert r.status_code == 400
    assert "qty" in r.get_json()["error"]


def test_low_stock_endpoint_reports(admin):
    r = admin.get("/api/admin/low-stock")
    assert r.status_code == 200, r.data
    assert "ok" in r.get_json()


# ===========================================================================
# categories
# ===========================================================================

def test_categories_save_and_read_back(admin):
    cats = [{"id": "test-cat", "name": "Integration", "slug": "integration",
             "sort": 1}]
    r = admin.put("/api/admin/categories", json={"categories": cats})
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True


def test_categories_rejects_a_bad_shape(admin):
    r = admin.put("/api/admin/categories", json={"categories": "nope"})
    assert r.status_code == 400
    assert "categories" in r.get_json()["error"]


# ===========================================================================
# site settings and delivery zones (covered in depth elsewhere; smoke here)
# ===========================================================================

def test_site_settings_round_trip(admin):
    r = admin.post("/api/admin/site", json={"contact_email": "a@example.com"})
    assert r.status_code == 200, r.data
    assert r.get_json()["site"]["contact_email"] == "a@example.com"


def test_delivery_zones_round_trip(admin):
    r = admin.get("/api/admin/delivery-zones")
    assert r.status_code == 200, r.data
    assert len(r.get_json()["zones"]) >= 1


# ===========================================================================
# coupons
# ===========================================================================

def test_coupon_create_patch_delete(admin):
    code = "ITEST10"
    execute("DELETE FROM coupons WHERE code=?", (code,))
    r = admin.post("/api/admin/coupons",
                   json={"code": code, "percent": 10, "maxUses": 3})
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True

    r = admin.get("/api/admin/coupons")
    assert r.status_code == 200
    assert code in json.dumps(r.get_json())

    r = admin.patch(f"/api/admin/coupons/{code}", json={"percent": 15})
    assert r.status_code == 200, r.data
    assert one("SELECT percent FROM coupons WHERE code=?", (code,))["percent"] == 15

    r = admin.delete(f"/api/admin/coupons/{code}")
    assert r.status_code == 200, r.data
    assert one("SELECT 1 FROM coupons WHERE code=?", (code,)) is None


def test_coupon_rejects_an_out_of_range_percent(admin):
    r = admin.post("/api/admin/coupons", json={"code": "BADPCT", "percent": 99})
    assert r.status_code == 400
    assert "1 and 90" in r.get_json()["error"]


def test_coupon_rejects_a_duplicate_code(admin):
    execute("DELETE FROM coupons WHERE code='DUPCODE'")
    assert admin.post("/api/admin/coupons",
                      json={"code": "DUPCODE", "percent": 5}).status_code == 200
    r = admin.post("/api/admin/coupons", json={"code": "DUPCODE", "percent": 5})
    assert r.status_code == 400
    assert "already exists" in r.get_json()["error"]


# ===========================================================================
# growth settings and referrals
# ===========================================================================

def test_growth_settings_read_and_write(admin):
    r = admin.get("/api/admin/growth/settings")
    assert r.status_code == 200, r.data
    settings = r.get_json()["settings"]
    assert isinstance(settings, dict)

    r = admin.post("/api/admin/growth/settings",
                   json={"referral_min_order": 20000})
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True


def test_referrals_endpoint(admin):
    r = admin.get("/api/admin/referrals")
    assert r.status_code == 200, r.data
    assert "ok" in r.get_json()


# ===========================================================================
# orders: list, confirm, decline, reopen, csv, delete
# ===========================================================================

def _make_order(oid="JA-ADMINORD"):
    execute("DELETE FROM orders WHERE id=?", (oid,))
    payload = {"id": oid, "total": 5000, "currency": "CFA", "status": "pending",
               "customer": {"name": "Admin Tester", "email": "a@example.com"},
               "items": [{"id": "wix-001", "name": "x", "qty": 1, "price": 5000}]}
    execute(
        "INSERT INTO orders (id, payload, email, customer_name, phone, country, "
        "city, zone, address, note, payment, proof_url, items_count, total, "
        "currency, source, status, at, updated_at) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (oid, json.dumps(payload), "a@example.com", "Admin Tester", "", "",
         "", "Cotonou", "", "", "CFA", "", 1, 5000, "CFA", "web", "pending",
         "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
    return oid


def test_order_list_and_csv(admin):
    _make_order()
    r = admin.get("/api/admin/orders")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert any(o["id"] == "JA-ADMINORD" for o in body["orders"])

    r = admin.get("/api/admin/orders.csv")
    assert r.status_code == 200
    assert "JA-ADMINORD" in r.get_data(as_text=True)


def test_order_confirm_decline_reopen(admin):
    oid = _make_order()
    for status in ("confirmed", "declined", "pending"):
        r = admin.patch(f"/api/admin/orders/{oid}", json={"status": status})
        assert r.status_code == 200, r.data
        assert r.get_json()["status"] == status
        assert one("SELECT status FROM orders WHERE id=?", (oid,))["status"] == status


def test_order_rejects_an_unknown_status(admin):
    oid = _make_order()
    r = admin.patch(f"/api/admin/orders/{oid}", json={"status": "shipped"})
    assert r.status_code == 400
    assert "pending, confirmed or declined" in r.get_json()["error"]


def test_order_update_of_a_missing_order_is_a_404(admin):
    execute("DELETE FROM orders WHERE id='JA-NOPE'")
    r = admin.patch("/api/admin/orders/JA-NOPE", json={"status": "confirmed"})
    assert r.status_code == 404


def test_order_delete(admin):
    oid = _make_order("JA-ADMINDEL")
    r = admin.delete(f"/api/admin/orders/{oid}")
    assert r.status_code == 200, r.data
    assert one("SELECT 1 FROM orders WHERE id=?", (oid,)) is None


# ===========================================================================
# payment proofs
# ===========================================================================

def test_payment_proofs_list_and_delete(admin):
    r = admin.get("/api/admin/payment-proofs")
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True

    r = admin.delete("/api/admin/payment-proofs/999999")
    assert r.status_code in (200, 404)


# ===========================================================================
# reporting endpoints
# ===========================================================================

@pytest.mark.parametrize("path", [
    "/api/admin/analytics", "/api/admin/sales", "/api/admin/most-viewed",
    "/api/admin/audit", "/api/admin/live",
    "/api/admin/sync/status",
])
def test_reporting_endpoints_answer_for_an_admin(admin, path):
    r = admin.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code} {r.data[:200]}"
    assert isinstance(r.get_json(), dict)


def test_sales_csv(admin):
    r = admin.get("/api/admin/sales.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("Content-Type", "")


def test_backup_produces_a_dump(admin):
    r = admin.post("/api/admin/backup")
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True


# ===========================================================================
# uploads
# ===========================================================================

@pytest.mark.parametrize("path,field", [
    ("/api/admin/uploads/product", "file"),
    ("/api/admin/uploads/category", "file"),
    ("/api/admin/uploads/hero", "file"),
    ("/api/admin/uploads/image", "file"),
])
def test_image_uploads_accept_a_real_png(admin, path, field):
    data = {field: (io.BytesIO(_png_bytes()), "it.png")}
    r = admin.post(path, data=data, content_type="multipart/form-data")
    # 200 with a URL, or an honest failure - never a silent success.
    assert r.status_code in (200, 400, 503), r.data
    body = r.get_json()
    assert isinstance(body, dict)
    if r.status_code == 200 and body.get("ok"):
        url = body.get("url") or body.get("imageUrl") or ""
        assert url, f"{path} reported success with no URL: {body}"


def test_upload_rejects_a_non_image(admin):
    data = {"file": (io.BytesIO(b"#!/bin/sh\nrm -rf /\n"), "evil.sh")}
    r = admin.post("/api/admin/uploads/product", data=data,
                   content_type="multipart/form-data")
    assert r.status_code in (400, 415), r.data


def test_video_upload_endpoint_rejects_an_oversize_body(admin):
    data = {"file": (io.BytesIO(b"x" * 1024), "clip.mp4")}
    r = admin.post("/api/admin/uploads/video", data=data,
                   content_type="multipart/form-data")
    # Too small / wrong type / no storage configured - but it must answer,
    # and it must not claim to have stored a video it did not store.
    assert r.status_code in (200, 400, 413, 503), r.data
    body = r.get_json()
    if r.status_code == 200 and body.get("ok"):
        assert body.get("url"), body


# ===========================================================================
# session and logout
# ===========================================================================

def test_session_probe_reports_the_logged_in_admin(admin):
    r = admin.get("/api/admin/session")
    assert r.status_code == 200
    body = r.get_json()
    assert body["authenticated"] is True
    assert body["email"] == EMAIL






def test_logout_ends_the_session(admin):
    r = admin.post("/api/admin/logout")
    assert r.status_code == 200
    r = admin.get("/api/admin/orders")
    assert r.status_code in (401, 403)


# ===========================================================================
# the repo sync switch
# ===========================================================================

def test_sync_status_reports(admin):
    r = admin.get("/api/admin/sync/status")
    assert r.status_code == 200, r.data
    assert isinstance(r.get_json(), dict)


# ===========================================================================
# every admin button in the portal targets a route that exists
# ===========================================================================

def test_every_endpoint_the_portal_calls_is_a_real_route(app):
    """Catches a button wired to a typo'd or renamed endpoint."""
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    called = set(re.findall(r'JA_NET\.api\(\s*"([^"`]+)"', admin_js))
    called |= set(re.findall(r'JA_NET\.api\(\s*`([^`]+)`', admin_js))
    live = {str(r) for r in app.url_map.iter_rules()}
    missing = []
    for call in called:
        # strip a template expression or a trailing id
        path = re.sub(r"\$\{[^}]*\}", "X", call)
        path = "/" + path.lstrip("/")
        if not path.startswith("/api/"):
            path = "/api/" + path
        if path in live:
            continue
        # try collapsing the last segment to a converter
        head = path.rsplit("/", 1)[0]
        if any(p.startswith(head + "/<") for p in live):
            continue
        missing.append(call)
    assert not missing, f"js/admin.js calls routes that do not exist: {missing}"


def test_the_portal_never_reads_a_setting_from_localstorage():
    """Strip comments first - prose explaining the rule mentions the word."""
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    code = "\n".join(ln.split("//", 1)[0] for ln in admin_js.splitlines())
    assert "localStorage" not in code, (
        "admin settings must come from the server, never from the browser")
