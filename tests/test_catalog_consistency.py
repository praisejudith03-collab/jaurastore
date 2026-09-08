"""Every storefront device must receive the same catalogue from the same
Supabase-backed public endpoint.

These tests pin the audit fixes from the "different phones, different
catalogues" incident:

  * in production (Supabase enabled, not the test suite) the products table
    is the ONLY source: neither the committed 258-row Wix seed nor a stale
    local overrides file may add rows to a live response;
  * a Supabase outage in production is a clear 503 - never a local JSON
    snapshot served as if it were live;
  * product API responses carry no-store plus a content-based ETag, so no
    browser / service worker / CDN can answer them from a stale cache;
  * the legacy /api/products route serves the SAME live feed as
    /api/catalog, so an old bundle cannot be pinned to the old seed;
  * the public view keeps hiding numeric stock (in/out flag only).

The test-suite path (FLASK_ENV=testing, no Supabase) keeps the historical
seed + overrides blend and is covered by the other catalogue tests.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
from config import Config  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
EMAIL = "jaurastore@gmail.com"


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
    with app.test_client() as c:
        yield c


_SB = [
    {"id": "wix-001", "name": "One", "slug": "one", "sku": "JAU1",
     "category": "beauty", "priceNgn": 1000, "online": True, "stock": 7,
     "image_url": "https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/uploads/products/wix-001/a.jpg"},
    {"id": "wix-002", "name": "Two", "slug": "two", "sku": "JAU2",
     "category": "bags", "priceNgn": 2000, "online": False, "stock": 0,
     "image_url": ""},
]


def _prod(monkeypatch):
    """Fake a production boot: Supabase reachable with two rows."""
    monkeypatch.setattr(Config, "ENV", "production")
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: [dict(r) for r in _SB])


def _prod_down(monkeypatch):
    monkeypatch.setattr(Config, "ENV", "production")
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: None)


def test_production_merged_is_supabase_only(monkeypatch):
    """No seed row, no stale-override row may leak into a production
    response: the Supabase table is the entire catalogue."""
    _prod(monkeypatch)
    # The committed seed (258 wix rows) and a stale overrides file both
    # contain ids Supabase does not have - neither may appear.
    all_rows = catalog_mod.merged(include_hidden=True)
    ids = [str(p.get("id")) for p in all_rows]
    assert ids == ["wix-001", "wix-002"], \
        f"production must serve exactly the Supabase rows, got {len(ids)} rows"
    # public view = only the online rows
    pub_ids = [str(p.get("id")) for p in catalog_mod.merged(include_hidden=False)]
    assert pub_ids == ["wix-001"]


def test_production_override_rows_only_enrich(monkeypatch):
    """A local file may fill keys a Supabase row is missing, never values it
    has, and never add rows it does not know."""
    _prod(monkeypatch)
    monkeypatch.setattr(catalog_mod, "overrides", lambda: {
        "products": [
            # enrichment source: multi-photo array the table cannot hold
            {"id": "wix-001", "name": "STALE NAME", "images": ["images/products/one.jpg"],
             "priceNgn": 9999},
            # a phone save that never reached Supabase: must NOT appear
            {"id": "jau-phone-save", "name": "Phone Save", "online": True},
        ],
        "deleted": [],
    })
    rows = catalog_mod.merged(include_hidden=True)
    by_id = {str(p.get("id")): p for p in rows}
    assert set(by_id) == {"wix-001", "wix-002"}, \
        "local rows added to the production catalogue"
    row = by_id["wix-001"]
    assert row["name"] == "One", "an override value overrode a Supabase value"
    assert row["priceNgn"] == 1000, "an override value overrode a Supabase value"
    assert row.get("images") == ["images/products/one.jpg"], \
        "a key the Supabase table cannot hold was not filled from the local row"


def test_production_outage_is_a_503_not_a_snapshot(client, monkeypatch):
    """An unreachable Supabase in production must be a clear 503 - serving
    the committed seed would be a wrong catalogue, not a degraded one."""
    _prod_down(monkeypatch)
    with pytest.raises(RuntimeError):
        catalog_mod.merged()
    for route in ("/api/catalog", "/api/products"):
        r = client.get(route)
        assert r.status_code == 503, f"{route} must 503, got {r.status_code}"
        body = r.get_json()
        assert body["ok"] is False
        assert "unavailable" in str(body.get("error", "")).lower()


def test_catalog_no_store_and_content_etag(client, monkeypatch):
    """no-store on the product feed + an ETag that only matches the exact
    body served (a 304 may never mean 'your copy is current' when it is
    not)."""
    # testing env: the local path (seed + overrides) - headers are identical
    r = client.get("/api/catalog")
    assert r.status_code == 200
    cc = r.headers.get("Cache-Control", "")
    assert "no-store" in cc, f"product feed must be no-store, got {cc!r}"
    etag = r.headers.get("ETag", "")
    assert etag.startswith('"sha256-'), f"content ETag expected, got {etag!r}"
    body1 = r.get_data(as_text=True)
    # revalidate with the same body -> 304
    r2 = client.get("/api/catalog", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.headers.get("ETag") == etag
    assert "no-store" in r2.headers.get("Cache-Control", "")
    # a real catalogue change must change the ETag (the old ETag was a hash
    # of a local file timestamp and never changed when only the store did)
    import catalog as _c
    path = _c._norm_filename(_c.CATALOG_FILE)
    data, _p = _c._load_overrides()
    prods = data.get("products") or []
    marker = {"id": "jau-etag-probe", "name": "Etag Probe", "online": True,
              "category": "beauty", "priceNgn": 10, "stock": 1}
    prods.append(marker)
    data["products"] = prods
    _c._write_overrides(data, path)
    try:
        r3 = client.get("/api/catalog")
        assert r3.status_code == 200
        assert r3.headers.get("ETag") != etag, \
            "the ETag did not change when the catalogue changed"
        assert r3.get_data(as_text=True) != body1
    finally:
        data, _p = _c._load_overrides()
        data["products"] = [p for p in data.get("products") or []
                            if p.get("id") != "jau-etag-probe"]
        _c._write_overrides(data, path)


def test_legacy_products_route_serves_the_same_live_feed(client):
    """/api/products (old bundles) must return the identical product list and
    the same no-store policy as /api/catalog - never the committed seed."""
    a = client.get("/api/products")
    b = client.get("/api/catalog")
    assert a.status_code == 200 and b.status_code == 200
    assert a.get_json()["products"] == b.get_json()["products"], \
        "the legacy route and the live route disagree"
    assert "no-store" in a.headers.get("Cache-Control", "")
    # and it is the live merged feed, not the raw seed file
    seed_ids = {str(p.get("id")) for p in catalog_mod._seed_products()}
    served_ids = {str(p.get("id")) for p in a.get_json()["products"]}
    assert served_ids != seed_ids, \
        "the legacy route still serves the raw committed seed"


def test_public_view_still_hides_numeric_stock(client):
    """The public feed carries only the in/out flag; the numbers stay for the
    admin session (the storefront normalises the flag, never a number)."""
    r = client.get("/api/catalog")
    for p in r.get_json()["products"]:
        assert "stock" not in p and "stock_quantity" not in p
        assert "optionStock" not in p and "inventory" not in p
        assert p.get("stock_status") in ("in", "out")
