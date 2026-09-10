"""Absolute Supabase persistence for product saves + instant repaint.

The owner's complaints this file pins down (release 2026-09-10):

* a product saved from the admin portal ("Tumeric Clay Mask", Beauty &
  skincare) must land in the live Supabase PostgreSQL products table FIRST,
  and the save endpoint must answer with the confirmed Supabase row - never
  with a copy that only exists in a local SQLite/JSON mirror (those are wiped
  by every Render restart, which is how saved products "vanished");
  a fresh app instance (what a redeploy boots) must read the row back.
* what the owner saves as online must stay online across that redeploy;
* an uploaded photo must reach the admin screen immediately: the upload
  response URL - stamped with a cache-buster by js/admin.js - is what the
  DOM paints and what the saved row carries, so a second form save can never
  revert the picture and no phone serves the old one from cache;
* the storefront and the admin portal must agree with Supabase: the public
  catalogue is exactly the online rows, the admin catalogue is exactly the
  live rows, and neither ever shows a duplicate.

Run with:  python3 -m pytest tests/test_supabase_persistence_and_repaint.py -q
"""
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import supabase_store  # noqa: E402
from config import Config  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_save_visibility import (  # noqa: E402
    _FakeSupabase, _StrictSupabase, app, client, iso_catalog, login)

# app/client/iso_catalog are imported pytest fixtures: binding them here is
# what registers them for the tests below.
_REGISTERED_FIXTURES = (app, client, iso_catalog)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every column the production products table carries (supabase_schema.sql
# "create table products"). A save must survive against the full table.
FULL_PRODUCT_COLUMNS = {
    "id", "legacyId", "sku", "slug", "name", "nameFr", "descriptionFr",
    "category", "priceCfa", "compareCfa", "priceNgn", "compareNgn",
    "image", "image_url", "images", "description", "stock", "stock_quantity",
    "badge", "featured", "online", "colors", "options", "optionStock",
    "placeholderImage", "usesPlaceholder", "source", "updated_at",
}


def _png(width=24, height=24):
    """A real, tiny PNG - the upload route decides the type from the bytes."""
    raw = b"".join(b"\x00" + bytes([120, 90, 60]) * width for _ in range(height))

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _upload_photo(client, tok, name="tumeric.png"):
    """POST /api/admin/uploads/product exactly like the admin media editor."""
    r = client.post("/api/admin/uploads/product",
                    data={"file": (io.BytesIO(_png()), name)},
                    headers={"X-CSRF-Token": tok},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] and body["kind"] == "image", body
    return body["url"]


def _live_supabase(monkeypatch, allowed=FULL_PRODUCT_COLUMNS):
    """A reachable, full-shape Supabase; production saves must mirror there."""
    fake = _StrictSupabase(allowed)
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    # The production write path: catalog.upsert() must take the Supabase
    # branch (no local-override file write) for this save.
    monkeypatch.setattr(catalog_mod, "_prod_source", lambda: True)
    # A production-mode upsert schedules the async repo mirror
    # (_sync_repo_async -> repo_sync.regenerate). Its daemon thread re-checks
    # the repo-sync gate when it happens to run, which can land inside
    # ANOTHER test's monkeypatched window (pytest gate off, ENV=production)
    # and regenerate the REAL js/products-data.js / data/catalog.json with
    # the shared test catalogue. No test thread may ever do that.
    monkeypatch.setattr(catalog_mod, "_sync_repo_async", lambda: None)
    return fake


def _row_in(table, pid):
    for r in table:
        if str(r.get("id")) == str(pid):
            return r
    return None


# =========================================================== PART 3: the save
def test_save_lands_in_supabase_and_returns_the_confirmed_row(client, iso_catalog,
                                                              monkeypatch):
    """Save -> the row exists in the (faked) live products table with the
    right name / category / price / image_url, and the endpoint's answer IS
    the re-queried Supabase row - not the local payload."""
    fake = _live_supabase(monkeypatch)
    tok = login(client)
    product = {
        "id": "jau-tumeric-1",
        "name": "Tumeric Clay Mask",
        "category": "beauty",
        "priceNgn": 8500,
        "priceCfa": 3740,
        "image": "images/products/_placeholder.jpg",
        "images": ["images/products/_placeholder.jpg"],
        "stock": 12,
        "online": True,
    }
    r = client.post("/api/admin/products", json={"product": product},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True and body["mirrored"] is True, body
    assert body["action"] == "created"

    # the live PostgreSQL row (not a local file) carries the save
    row = _row_in(fake.tables["products"], "jau-tumeric-1")
    assert row is not None, "the saved product never reached the products table"
    assert row["name"] == "Tumeric Clay Mask"
    assert row["category"] == "beauty"
    assert int(row["priceNgn"]) == 8500
    assert row["image_url"], "the saved row carries no image_url"
    assert row["online"] is True, "an online save must be stored online"

    # the response is the confirmed Supabase row
    served = body["product"]
    assert served["name"] == "Tumeric Clay Mask"
    assert served["category"] == "beauty"
    assert int(served["priceNgn"]) == 8500
    assert served["image_url"] == row["image_url"]
    assert served["online"] is not False


def test_production_save_never_stops_at_the_local_catalog_file(client, iso_catalog,
                                                               monkeypatch):
    """In production runtime the local override file is a read-through mirror
    only: a save that stops there is wiped by the next Render restart. The
    production branch must leave data/catalog.json untouched."""
    fake = _live_supabase(monkeypatch)
    tok = login(client)
    r = client.post("/api/admin/products",
                    json={"product": {"id": "jau-tumeric-2",
                                      "name": "Tumeric Clay Mask 2",
                                      "category": "beauty", "priceNgn": 9000,
                                      "stock": 5, "online": True}},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert r.get_json()["mirrored"] is True
    # the row is in Supabase ...
    assert _row_in(fake.tables["products"], "jau-tumeric-2") is not None
    # ... and the local override file was never written
    overrides = json.load(open(iso_catalog, encoding="utf-8"))
    assert overrides.get("products") == [], (
        "a production save wrote the local catalog.json - that copy dies with "
        "the deploy and the product vanishes")


def test_saved_product_survives_a_redeploy(client, iso_catalog, monkeypatch):
    """Save -> redeploy (ephemeral disk wiped, fresh app instance booted) ->
    the product is still there, online, with the same photo. This is the
    exact 'products vanish on Render restarts' regression."""
    fake = _live_supabase(monkeypatch)
    tok = login(client)
    photo = _upload_photo(client, tok)
    # js/admin.js stamps every upload with a cache-buster before saving it;
    # the saved row must carry that exact URL.
    stamped = photo + "?v=m3d9x"
    r = client.post("/api/admin/products",
                    json={"product": {"id": "jau-tumeric-3",
                                      "name": "Tumeric Clay Mask",
                                      "category": "beauty", "priceNgn": 7800,
                                      "image": stamped, "images": [stamped],
                                      "stock": 9, "online": True}},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    served = r.get_json()["product"]
    assert photo in str(served.get("image") or served.get("image_url") or "")

    # --- the redeploy: local disk (the override file) is wiped ...
    catalog_mod._write_overrides(
        {"products": [], "deleted": [], "updatedAt": "", "updatedBy": ""},
        catalog_mod._norm_filename(catalog_mod.CATALOG_FILE))
    # ... a fresh app instance boots and reads the catalogue
    fresh_app = appmod.create_app()
    fresh_app.config.update(TESTING=True)
    with fresh_app.test_client() as fresh:
        body = fresh.get("/api/catalog").get_json()
        assert body["ok"] is True
        row = next((p for p in body["products"]
                    if str(p.get("id")) == "jau-tumeric-3"), None)
        assert row is not None, (
            "the saved product did not survive the redeploy - it was only "
            "ever in a local file")
        assert row["name"] == "Tumeric Clay Mask"
        assert row["category"] == "beauty"
        assert int(row["priceNgn"]) == 7800
        assert row["online"] is not False, "an online save came back offline"
        img = str(row.get("image") or row.get("image_url") or "")
        assert photo in img, "the uploaded photo URL did not survive the redeploy"


def test_an_offline_save_stays_offline_and_never_reaches_the_storefront(
        client, iso_catalog, monkeypatch):
    """The online flag the owner saves is the truth: an offline row is stored
    offline, stays offline after a redeploy, and never leaks to the public
    catalogue - while the admin portal still sees it."""
    fake = _live_supabase(monkeypatch)
    tok = login(client)
    r = client.post("/api/admin/products",
                    json={"product": {"id": "jau-tumeric-off",
                                      "name": "Tumeric Clay Mask (draft)",
                                      "category": "beauty", "priceNgn": 5000,
                                      "stock": 4, "online": False}},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert r.get_json()["product"]["online"] is False
    stored = _row_in(fake.tables["products"], "jau-tumeric-off")
    assert stored["online"] is False

    fresh_app = appmod.create_app()
    fresh_app.config.update(TESTING=True)
    with fresh_app.test_client() as fresh:
        ids = [str(p.get("id")) for p in fresh.get("/api/catalog").get_json()["products"]]
        assert "jau-tumeric-off" not in ids, "an offline row leaked to the storefront"


# ==================================================== PART 4: instant repaint
def test_bust_media_cache_appends_a_fresh_token():
    """js/admin.js bustMediaCache: every uploaded URL gains a per-upload
    token, data:/blob: previews are left alone, and an existing query is
    extended rather than broken."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    src = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    m = re.search(r"function bustMediaCache\(url\) \{.*?\n\}", src, re.S)
    assert m, "bustMediaCache is missing from js/admin.js"
    script = f"""
{m.group(0)}
const a = bustMediaCache("https://x.supabase.co/storage/v1/object/public/uploads/products/2026/09/a.jpg");
const b = bustMediaCache("/uploads/products/2026/09/a.jpg");
const c = bustMediaCache("https://x.supabase.co/object/public/a.jpg?sig=1");
const d = bustMediaCache("data:image/png;base64,AAA");
const e = bustMediaCache("");
if (!/[?&]v=[0-9a-z]+$/.test(a)) throw new Error("no token appended: " + a);
if (!b.includes("?v=")) throw new Error("relative upload not stamped: " + b);
if (!c.includes("&v=") || !c.includes("sig=1")) throw new Error("existing query broken: " + c);
if (d !== "data:image/png;base64,AAA") throw new Error("data: preview was stamped: " + d);
if (e !== "") throw new Error("empty input changed: " + e);
if (a.split("?v=")[0] !== "https://x.supabase.co/storage/v1/object/public/uploads/products/2026/09/a.jpg") throw new Error("base url damaged: " + a);
console.log("OK");
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True,
                            cwd=ROOT, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_every_admin_upload_handler_stamps_and_persists_the_fresh_url():
    """Audit of js/admin.js: product photos, category assets, hero, logo and
    banner uploads all (a) stamp the returned URL with the cache-buster and
    (b) repaint the screen / persist that SAME URL - no handler leaves a
    stale, local or data: copy behind."""
    src = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    # the helper itself
    assert "function bustMediaCache(url)" in src
    # product photos: the editor tile, the DOM strip and the save payload all
    # carry the stamped URL
    assert "window.__editImages[idx] = bustMediaCache(res.url);" in src
    # category assets
    assert "input.dataset.catUrl = freshUrl;" in src
    assert "pic.innerHTML = _catAssetHTML(freshUrl);" in src
    # hero / logo / banner
    assert "{ heroVideo: freshUrl," in src
    assert "saveSiteConfig({ logoUrl: freshUrl });" in src
    assert "saveSiteConfig({ shopBannerUrl: freshUrl });" in src
    # no upload handler still saves the raw response URL
    for raw in ("{ logoUrl: res.url }", "{ shopBannerUrl: res.url }",
                "heroVideo: res.url", "dataset.catUrl = res.url",
                "__editImages[idx] = res.url"):
        assert raw not in src, f"an upload handler still persists the raw URL: {raw}"


def test_upload_url_is_what_the_dom_paints_and_the_row_carries(client, iso_catalog,
                                                               monkeypatch):
    """upload -> response URL -> DOM src updated -> saved row carries the same
    URL. The DOM half is static (mediaTileHTML paints the very entry the
    editor holds); the save half is the real endpoint flow."""
    src = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    # the media strip paints the exact entries the editor holds ...
    assert 'const url = JA.asset(imgSrc(src) || (typeof src === "string" ? src : ""));' in src
    assert '<img src="${url}" alt="" />' in src
    # ... and the form posts those same entries as image/images
    m = re.search(r"async function handleProductSubmit\(e, existing\) \{(.*?)\n\}\n",
                  src, re.S)
    assert m, "handleProductSubmit not found"
    body = m.group(1)
    assert "let rawImages = (window.__editImages || []).filter(Boolean);" in body
    assert "const image = images[0] || \"\";" in body
    assert "image,\n      images," in body
    # a second save repaints from the server-confirmed row, never from a
    # stale local copy
    store = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    assert "if (d && d.product) applyServerProduct(d.product);" in store
    assert "paintDesk(\"products\");" in body

    # the endpoint flow: upload, stamp (exactly like the browser), save
    fake = _live_supabase(monkeypatch)
    tok = login(client)
    photo = _upload_photo(client, tok, name="paint.png")
    stamped = photo + "?v=k7fz2"
    r = client.post("/api/admin/products",
                    json={"product": {"id": "jau-repaint-1",
                                      "name": "Repaint Serum",
                                      "category": "beauty", "priceNgn": 6600,
                                      "image": stamped, "images": [stamped],
                                      "stock": 8, "online": True}},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    served = r.get_json()["product"]
    img = str(served.get("image") or served.get("image_url") or "")
    assert stamped.split("?")[0] in img and "v=k7fz2" in img, (
        f"the saved row does not carry the stamped upload URL: {img!r}")
    row = _row_in(fake.tables["products"], "jau-repaint-1")
    assert stamped.split("?")[0] in str(row.get("image_url") or row.get("image"))
    assert any("v=k7fz2" in str(g) for g in (row.get("images") or [])), (
        "the gallery in Supabase does not carry the stamped upload URL")


# ============================================== PART 6: counts always agree
class _PagedQuery:
    """Chainable PostgREST select stand-in (what products_table_rows uses)."""

    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, _cols, count=None):
        return self

    def order(self, _col, **_kw):
        return self

    def range(self, start, end):
        self._rows = self._rows[start:end + 1]
        return self

    def execute(self):
        res = type("Res", (), {"data": list(self._rows)})()
        res.count = len(self._rows)
        return res


class _PagedTable:
    def __init__(self, rows):
        self._rows = sorted(rows, key=lambda r: str(r.get("id") or ""))

    def select(self, cols, count=None):
        return _PagedQuery(self._rows).select(cols, count)


class _PagedClient:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _PagedTable(self._rows)


def _seed_wix_rows():
    seed = json.load(open(os.path.join(ROOT, "data", "seed.json"),
                          encoding="utf-8"))
    return [p for p in seed if str(p.get("id", "")).startswith("wix-")]


def _db_row(p, online=True, source="admin"):
    return {
        "id": p["id"], "sku": p.get("sku"), "slug": p.get("slug"),
        "name": p["name"], "nameFr": p.get("nameFr"),
        "category": p.get("category"),
        "priceCfa": p.get("priceCfa"), "priceNgn": p.get("priceNgn"),
        "image": p.get("image"), "image_url": p.get("image"),
        "images": p.get("images") or [],
        "description": p.get("description"),
        "stock": p.get("stock", 24), "stock_quantity": p.get("stock", 24),
        "badge": p.get("badge"), "featured": p.get("featured"),
        "online": online, "colors": p.get("colors"),
        "options": p.get("options"),
        "source": source, "updated_at": "2026-09-10T07:57:14+00:00",
    }


def test_storefront_count_equals_online_supabase_rows(client, iso_catalog,
                                                      monkeypatch):
    """A mixed online/offline dataset: the public storefront serves EXACTLY
    the online Supabase rows - offline rows never leak, nothing is served
    twice - and the admin portal's own count is exactly the live rows, so
    the two counts can never disagree with the database."""
    wix = _seed_wix_rows()
    rows = [_db_row(p, online=True) for p in wix]
    # a mixed dataset: some previously-online rows are taken offline ...
    for r in rows[:10]:
        r["online"] = False
    # ... an offline row is put back online ...
    rows[0]["online"] = True
    # ... and new admin rows join (one online, one offline)
    rows.append(_db_row({"id": "jau-count-on", "sku": "JAU-CNT1", "slug": "jau-count-on",
                         "name": "Counting Cream", "category": "beauty",
                         "priceNgn": 3000, "priceCfa": 1320,
                         "image": "images/products/_placeholder.jpg",
                         "stock": 6}, online=True))
    rows.append(_db_row({"id": "jau-count-off", "sku": "JAU-CNT2", "slug": "jau-count-off",
                         "name": "Counting Clay", "category": "beauty",
                         "priceNgn": 4000, "priceCfa": 1760,
                         "image": "images/products/_placeholder.jpg",
                         "stock": 6}, online=False))
    # a tombstoned row must not be counted anywhere even if still in the table
    rows.append(_db_row({"id": "jau-count-dead", "sku": "JAU-CNT3", "slug": "jau-count-dead",
                         "name": "Counting Dead", "category": "beauty",
                         "priceNgn": 5000, "priceCfa": 2200,
                         "image": "images/products/_placeholder.jpg",
                         "stock": 6}, online=True, source="deleted"))

    monkeypatch.setattr(supabase_store, "client", lambda: _PagedClient(rows))
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    # no local mirror may answer instead of Supabase
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [], "deleted": [],
                                 "updatedAt": "", "updatedBy": ""})

    online_ids = {str(r["id"]) for r in rows
                  if r["online"] is not False and r.get("source") not in ("deleted", "replaced")}
    live_ids = {str(r["id"]) for r in rows
                if r.get("source") not in ("deleted", "replaced")}

    # ---- the public storefront: exactly the online rows, no duplicates
    pub = client.get("/api/catalog").get_json()
    ids = [str(p.get("id")) for p in pub["products"]]
    assert len(ids) == len(set(ids)), "a product id is served twice to shoppers"
    assert set(ids) == online_ids, (
        f"storefront != online Supabase rows: missing "
        f"{sorted(online_ids - set(ids))[:5]}, leaked {sorted(set(ids) - online_ids)[:5]}")
    assert len(ids) == len(online_ids)
    assert all(p.get("online") is not False for p in pub["products"])
    assert "jau-count-dead" not in ids, "a tombstoned row reached the storefront"

    # ---- the admin portal: exactly the live rows (online AND offline)
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", "AdminMaster2026")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "AdminBootstrap7")
    r = client.post("/api/admin/login", json={"password": "AdminMaster2026"})
    assert r.status_code == 200, r.data
    adm = client.get("/api/catalog?all=1").get_json()
    admin_ids = [str(p.get("id")) for p in adm["products"]]
    assert len(admin_ids) == len(set(admin_ids)), "a product id is listed twice in the portal"
    assert set(admin_ids) == live_ids, (
        f"admin != live Supabase rows: missing "
        f"{sorted(live_ids - set(admin_ids))[:5]}, extra "
        f"{sorted(set(admin_ids) - live_ids)[:5]}")
    assert adm["meta"]["count"] == len(live_ids)
    # offline rows stay visible to the owner (that is how they get re-listed)
    assert "jau-count-off" in admin_ids
    assert "jau-count-off" not in ids
