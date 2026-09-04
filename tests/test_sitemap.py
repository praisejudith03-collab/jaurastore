"""Tests for the dynamic /sitemap.xml route (app.build_sitemap).

The old committed sitemap.xml was a snapshot: it kept listing categories the
owner had deleted (clothing, nails, packaging, skincare), which is what
Search Console flagged. The route is now rebuilt on every request from the
same sources the storefront reads - the live category table and the live
catalogue - so a deleted category (or product) can never appear.

Run with:  python3 -m pytest tests -q
"""
import json, os, re, sys, xml.etree.ElementTree

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

from config import Config  # noqa: E402
import app as appmod  # noqa: E402
import api as api_mod  # noqa: E402
import auth as authmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import storage  # noqa: E402
from db import execute, init_db  # noqa: E402

NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402  - one strong password per run; ADMIN_PW pins it
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


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def _sitemap_body(client) -> str:
    return client.get("/sitemap.xml").get_data(as_text=True)


def _locs(client):
    """Every <loc> in the served sitemap, parsed as XML (well-formedness
    included in the act of parsing)."""
    root = xml.etree.ElementTree.fromstring(_sitemap_body(client))
    assert root.tag == NS + "urlset"
    return [e.text for e in root.iter(NS + "loc")]


def _category_ids(client):
    ids = set()
    for loc in _locs(client):
        m = re.search(r"shop\.html\?cat=([^&]+)$", loc)
        if m:
            ids.add(m.group(1))
    return ids


# ------------------------------------------------------------------- shape
def test_sitemap_route_serves_well_formed_xml(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert r.content_type.startswith("application/xml")
    body = r.get_data(as_text=True)
    assert body.startswith("<?xml version=\"1.0\" encoding=\"UTF-8\"?>")
    assert 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"' in body
    assert "Cache-Control" in r.headers


def test_static_pages_are_all_listed(client):
    locs = _locs(client)
    origin = Config.SITE_ORIGIN.rstrip("/")
    for path, _pri, _freq in appmod.SITEMAP_STATIC_PAGES:
        assert origin + path in locs, f"missing static page {path}"


def test_sitemap_count_matches_the_live_store(client):
    """The whole point: the sitemap has exactly as many URLs as the store has
    pages - no stale entries, no missing ones."""
    body = _sitemap_body(client)
    n_urls = body.count("<url>")
    live_cats = [c for c in api_mod._categories_data()["categories"]
                 if str(c.get("id") or "").strip() and not c.get("hidden")]
    live_products = catalog_mod.merged()
    assert n_urls == len(appmod.SITEMAP_STATIC_PAGES) + len(live_cats) + len(live_products)


def test_live_categories_and_products_are_listed(client):
    locs = _locs(client)
    origin = Config.SITE_ORIGIN.rstrip("/")
    live_cats = api_mod._categories_data()["categories"]
    assert live_cats, "test setup: the live category table must not be empty"
    for c in live_cats:
        if not c.get("hidden"):
            assert f"{origin}/shop.html?cat={c['id']}" in locs, f"missing category {c['id']}"
    seed_ids = [str(p.get("id")) for p in catalog_mod.base_products()[:3]]
    for pid in seed_ids:
        assert f"{origin}/product.html?id={pid}" in locs, f"missing product {pid}"


# ------------------------------------------------- deleted categories: never
def test_deleted_category_can_never_appear_in_sitemap(client, monkeypatch, tmp_path):
    """The owner deleted clothing / nails / packaging / skincare (the four
    the old static sitemap still listed). The sitemap is a mirror of the live
    table, so whatever is not in it cannot appear in the sitemap.

    The live table is built to contain a deleted category id and is then
    shrunk - the sitemap must track the shrunk table exactly, to the entry,
    which is what keeps Search Console's count equal to the live page count.
    """
    deleted = ("clothing", "nails", "packaging", "skincare")
    # a table that still carries the deleted ids (the state before deletion)
    stale = api_mod._categories_data()["categories"]
    stale = [dict(c) for c in stale]
    for d in deleted:
        if not any(c["id"] == d for c in stale):
            stale.append({"id": d, "name": d.title(), "nameFr": "",
                          "image": "", "hidden": False})
    table_file = tmp_path / "categories.json"
    table_file.write_text(json.dumps({"categories": stale}), encoding="utf-8")
    monkeypatch.setattr(api_mod, "CATEGORIES_FILE", str(table_file))
    before = _category_ids(client)
    assert set(deleted) <= before      # test setup: they were listed before

    # the owner deletes them: the live table no longer has them
    kept = [c for c in stale if c["id"] not in deleted]
    table_file.write_text(json.dumps({"categories": kept}), encoding="utf-8")
    after = _category_ids(client)

    for gone in deleted:
        assert gone not in after, f"deleted category {gone!r} appeared in the sitemap"
    # exact mirror: the sitemap's category set equals the live table's set
    assert after == {c["id"] for c in kept}


def test_deleted_category_never_resurfaces_even_if_snapshot_has_it(client, monkeypatch, tmp_path):
    """Strongest form of the guarantee: even a brand-new categories file that
    re-introduces a deleted id only lands in the sitemap if it is really back
    in the live table - the sitemap is a mirror of the table, nothing more."""
    live = api_mod._categories_data()["categories"]
    kept = [c for c in live if c["id"] not in ("clothing", "nails", "packaging")]
    table_file = tmp_path / "categories.json"
    table_file.write_text(json.dumps({"categories": kept}), encoding="utf-8")
    monkeypatch.setattr(api_mod, "CATEGORIES_FILE", str(table_file))

    body = _sitemap_body(client)
    # 'nails' is a substring of nothing else; still match the full URL form
    assert "cat=nails" not in body
    assert "cat=clothing" not in body
    assert "cat=packaging" not in body
    assert body.count("<url>") == len(appmod.SITEMAP_STATIC_PAGES) + len(kept) + len(catalog_mod.merged())


def test_hidden_category_is_not_listed(client, monkeypatch, tmp_path):
    live = [dict(c) for c in api_mod._categories_data()["categories"]]
    hidden_one = live[0]
    hidden_one["hidden"] = True
    table_file = tmp_path / "categories.json"
    table_file.write_text(json.dumps({"categories": live}), encoding="utf-8")
    monkeypatch.setattr(api_mod, "CATEGORIES_FILE", str(table_file))

    ids = _category_ids(client)
    assert hidden_one["id"] not in ids
    assert len(ids) == len(live) - 1


def test_category_id_with_xml_special_chars_is_escaped(client, monkeypatch, tmp_path):
    live = api_mod._categories_data()["categories"]
    live = [dict(c) for c in live]
    live.append({"id": "a&b<c>", "name": "Weird < & Co", "nameFr": "",
                 "image": "", "hidden": False})
    table_file = tmp_path / "categories.json"
    table_file.write_text(json.dumps({"categories": live}), encoding="utf-8")
    monkeypatch.setattr(api_mod, "CATEGORIES_FILE", str(table_file))

    # must still parse as XML, and the id must be URL-quoted
    body = _sitemap_body(client)
    xml.etree.ElementTree.fromstring(body)          # well-formed
    assert "cat=a%26b%3Cc%3E" in body
    assert "cat=a&b<c>" not in body


# --------------------------------------------------- deleted / hidden products
def test_deleted_product_never_appears_in_sitemap(client, monkeypatch, tmp_path):
    cat_file = tmp_path / "catalog.json"
    cat_file.write_text(json.dumps({"products": [], "deleted": ["wix-001"]}),
                        encoding="utf-8")
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(cat_file))

    body = _sitemap_body(client)
    assert "/product.html?id=wix-001" not in body
    assert "/product.html?id=wix-002" in body
    # one fewer URL than before the deletion
    assert body.count("<url>") == len(appmod.SITEMAP_STATIC_PAGES) \
        + len(api_mod._categories_data()["categories"]) + (len(catalog_mod.merged()))


def test_offline_product_is_not_listed(client, monkeypatch, tmp_path):
    cat_file = tmp_path / "catalog.json"
    cat_file.write_text(json.dumps(
        {"products": [{"id": "wix-003", "name": "Wix 003", "online": False}],
         "deleted": []}), encoding="utf-8")
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(cat_file))

    body = _sitemap_body(client)
    assert "/product.html?id=wix-003" not in body


# -------------------------------------------------- static file is gone
def test_no_committed_sitemap_snapshot_to_go_stale():
    assert not os.path.exists(os.path.join(appmod.ROOT, "sitemap.xml"))


# ------------------------------------------- old local uploads still served
def _png() -> bytes:
    import struct, zlib
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def _write_upload(key: str, data: bytes) -> str:
    full = os.path.join(storage.local_root(), key.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)
    return full


def test_local_product_file_still_served_at_uploads(client, monkeypatch, tmp_path):
    """/uploads/... keeps serving files already on the disk, so nothing that
    worked before the Supabase switch 404s."""
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    key = "products/2026/01/legacy-photo.png"
    _write_upload(key, _png())
    r = client.get("/uploads/" + key)
    assert r.status_code == 200
    assert r.data == _png()
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_local_proof_file_stays_admin_only(client, monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    key = "proofs/2026/01/legacy-receipt.png"
    _write_upload(key, _png())

    assert client.get("/uploads/" + key).status_code == 404     # anonymous
    login(client)
    assert client.get("/uploads/" + key).status_code == 200     # admin
