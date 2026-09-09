"""The Delivery page is owner-editable, not frozen in the repo.

delivery.html used to be static markup: adding a town meant a code change and
a redeploy. The page is now a document the owner edits in Admin -> Delivery,
stored in Supabase (growth_settings key ``delivery_page_json``) and served on
the site row as ``delivery_page``. The static markup stays in delivery.html as
the fallback for a static host, an offline phone or a failed fetch.

These tests pin: the route is admin-only and CSRF-protected, a save is
re-read before it is reported back, the sanitiser refuses an empty document
and strips markup, /api/site serves what was saved, and the front-end wiring
(root marker, render hook, admin tab) is actually present.

Run with:  python3 -m pytest tests/test_delivery_page.py -q
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
import app as appmod  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = "jaurastore@gmail.com"

PAGE = {
    "title": "Delivery Locations",
    "lead": "Curated coverage across West Africa",
    "blocks": [
        {"heading": "Nigeria", "locations": [
            {"name": "Lagos Mainland", "detail": "Festac, Yaba, Gbagada"},
            {"name": "Lagos Island", "detail": "Lekki, Ikoyi, Ajah"},
        ]},
        {"heading": "Benin Republic", "locations": [
            {"name": "Cotonou", "detail": "Akpakpa, Calavi, PK10"},
        ]},
    ],
}


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture(autouse=True)
def _own_files(monkeypatch, tmp_path):
    """This module owns its site-config and delivery-page files."""
    site = tmp_path / "site.json"
    site.write_text("{}")
    monkeypatch.setenv("SITE_CONFIG_PATH", str(site))
    monkeypatch.setenv("DELIVERY_PAGE_PATH", str(tmp_path / "delivery_page.json"))


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


# ------------------------------------------------------------ the route is safe
def test_delivery_page_route_is_admin_only(client):
    """A signed-out visitor must not be able to rewrite the Delivery page."""
    r = client.post("/api/admin/delivery-page", json={"page": PAGE})
    assert r.status_code in (401, 403), r.data


def test_delivery_page_route_requires_csrf(client):
    tok = _login(client)
    assert tok
    r = client.post("/api/admin/delivery-page", json={"page": PAGE})
    assert r.status_code in (400, 403), "a POST without the CSRF header must fail"


# ------------------------------------------------------- save, re-read, serve
def test_saving_the_page_re_reads_and_returns_what_is_stored(client):
    tok = _login(client)
    r = client.post("/api/admin/delivery-page",
                    headers={"X-CSRF-Token": tok}, json={"page": PAGE})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    # the answer is the RE-READ document, so the portal repaints from the store
    page = body["page"]
    assert page["title"] == "Delivery Locations"
    assert [b["heading"] for b in page["blocks"]] == ["Nigeria", "Benin Republic"]
    assert page["blocks"][0]["locations"][0]["name"] == "Lagos Mainland"


def test_the_saved_page_is_served_on_the_site_row(client):
    tok = _login(client)
    client.post("/api/admin/delivery-page",
                headers={"X-CSRF-Token": tok}, json={"page": PAGE})
    site = client.get("/api/site").get_json()["site"]
    assert site["delivery_page"]["blocks"][1]["locations"][0]["name"] == "Cotonou"


def test_site_row_carries_no_page_until_one_is_saved(client):
    """No saved page means the storefront keeps the static fallback."""
    site = client.get("/api/site").get_json()["site"]
    assert site.get("delivery_page") is None


# --------------------------------------------------------------- sanitisation
def test_markup_is_stripped_and_lengths_are_capped(client):
    tok = _login(client)
    nasty = {
        "title": "<script>alert(1)</script>Delivery",
        "lead": "x" * 400,
        "blocks": [{"heading": "<b>Nigeria</b>",
                    "locations": [{"name": "<i>Lagos</i>", "detail": "y" * 500}]}],
    }
    r = client.post("/api/admin/delivery-page",
                    headers={"X-CSRF-Token": tok}, json={"page": nasty})
    assert r.status_code == 200, r.data
    page = r.get_json()["page"]
    assert "<" not in page["title"] and "script" not in page["title"].lower()
    assert page["blocks"][0]["heading"] == "Nigeria"
    assert page["blocks"][0]["locations"][0]["name"] == "Lagos"
    assert len(page["lead"]) <= 240
    assert len(page["blocks"][0]["locations"][0]["detail"]) <= 300


def test_at_most_twelve_blocks_are_kept(client):
    tok = _login(client)
    many = {"title": "T", "lead": "", "blocks": [
        {"heading": f"B{i}", "locations": [{"name": f"L{i}", "detail": ""}]}
        for i in range(30)]}
    r = client.post("/api/admin/delivery-page",
                    headers={"X-CSRF-Token": tok}, json={"page": many})
    assert r.status_code == 200, r.data
    assert len(r.get_json()["page"]["blocks"]) <= 12


@pytest.mark.parametrize("bad", [
    {},
    {"title": "T", "lead": "", "blocks": []},
    {"title": "T", "lead": "", "blocks": [{"heading": "Nigeria", "locations": []}]},
    {"title": "T", "lead": "", "blocks": [{"heading": "N", "locations": [{"name": "  "}]}]},
])
def test_a_page_with_no_named_location_is_rejected(client, bad):
    """An empty document would blank the page for every customer."""
    tok = _login(client)
    r = client.post("/api/admin/delivery-page",
                    headers={"X-CSRF-Token": tok}, json={"page": bad})
    assert r.status_code == 400, r.data
    assert r.get_json()["ok"] is False


def test_a_rejected_save_leaves_the_previous_page_untouched(client):
    tok = _login(client)
    client.post("/api/admin/delivery-page",
                headers={"X-CSRF-Token": tok}, json={"page": PAGE})
    client.post("/api/admin/delivery-page",
                headers={"X-CSRF-Token": tok}, json={"page": {"blocks": []}})
    site = client.get("/api/site").get_json()["site"]
    assert site["delivery_page"]["blocks"][0]["locations"][0]["name"] == "Lagos Mainland"


# ------------------------------------------------------------ front-end wiring
def test_delivery_html_keeps_its_static_content_as_the_fallback():
    html = open(os.path.join(ROOT, "delivery.html"), encoding="utf-8").read()
    assert "data-delivery-root" in html, "app.js paints into [data-delivery-root]"
    # the static list stays: it is what an offline phone or a static host shows
    for town in ("Lagos Mainland", "Cotonou", "Lom"):
        assert town in html


def test_app_js_renders_the_delivery_page_from_the_site_row():
    app_js = open(os.path.join(ROOT, "js", "app.js"), encoding="utf-8").read()
    assert "function renderDeliveryPage()" in app_js
    assert 'if (page === "delivery") renderDeliveryPage();' in app_js
    fn = app_js[app_js.index("function renderDeliveryPage()"):]
    fn = fn[:fn.index("\nfunction ")]
    assert "[data-delivery-root]" in fn
    # a missing/empty page must leave the static markup alone
    assert "return" in fn and "delivery_page" in fn
    # and the paint is re-run when the site row lands, bound only once
    assert 'document.addEventListener("ja:site", paint)' in fn
    assert "siteBound" in fn


def test_admin_portal_has_a_delivery_tab_with_page_and_zones():
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert 'delivery: "Delivery"' in admin_js, "the tab must be named in TAB_TITLES"
    assert "ADX_ICONS.delivery" in admin_js and "delivery: `<svg" in admin_js
    assert 'id="panel-delivery"' in admin_js
    assert "function deliveryDesk()" in admin_js
    # the desk is the editable page PLUS the zone table moved out of Settings
    desk = admin_js[admin_js.index("function deliveryDesk()"):]
    desk = desk[:desk.index("}") + 1]
    assert "deliveryPagePanel()" in desk and "deliveryZonesPanel()" in desk
    assert "api/admin/delivery-page" in admin_js
    # Settings no longer renders the zones
    settings = admin_js[admin_js.index("function settingsForm()"):]
    settings = settings[:settings.index("\nfunction ")]
    assert "deliveryZonesPanel()" not in settings
    # a zone save repaints the Delivery tab, not Settings
    assert 'paintDesk("delivery")' in admin_js


def test_store_js_exports_apply_site_config_and_stock_translation():
    store_js = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    assert "function applySiteConfig(site)" in store_js
    assert "function normalizeServerProduct(p)" in store_js
    assert "applySiteConfig, normalizeServerProduct," in store_js, \
        "both must be exported on JA"
    # the public catalogue is translated on the way in
    assert "d.products.map(normalizeServerProduct)" in store_js
    assert "p = normalizeServerProduct(p);" in store_js
