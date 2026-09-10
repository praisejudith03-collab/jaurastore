"""Deleted test products stay gone, and a product's photo really sticks.

Two production complaints, one root cause each:

1. "I deleted some stock test products and if not all, they are back."
   The pytest suite (and e2e runs pointed at the live site) creates products
   for itself - ids ``jau-stock-*`` / ``jau-mirror-*``, sku ``JAUSTOCK*``,
   names "Stock Test <id>". Two code paths kept putting them back on the
   storefront:

     * ``catalog.upsert`` deliberately clears a product's durable tombstone
       when it is re-saved ("re-saving un-deletes"), which is right for a real
       piece the owner re-creates and wrong for a fixture a test re-created;
     * ``local_only_products()`` compared the live Supabase rows against the
       local overrides and treated a TOMBSTONED row as "not in Supabase", so
       the scheduler's remirror pass pushed the disk copy back as a live
       product a few minutes after the delete.

   The fix is threefold: the live environments never serve a fixture
   (``merged()`` filters them), never save one (``upsert`` refuses), and do not
   remirror one - plus a boot pass that tombstones what is already there.
   Under FLASK_ENV=testing the guard is off, because there the fixtures are
   the suite's own data.

2. "I added an image to [a product] and it isn't there again."
   A product with no photo carries the branded placeholder
   (``images/products/_placeholder.jpg``). The admin editor showed it as tile
   #1 ("Main"), so the owner's newly added photo became tile #2 and the save
   kept the placeholder in the cover slot - the card said "PHOTO COMING SOON"
   after every save. The cover is now the first REAL photo, placeholders are
   dropped from the gallery, and the storefront tells the server when one of
   our uploads 404s so the row can be re-pointed at a photo that exists.

Run with:  python3 -m pytest tests/test_test_products_and_photos.py -q
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

import catalog as catalog_mod  # noqa: E402
import storage as storage_mod  # noqa: E402
import supabase_store  # noqa: E402
from config import Config  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_save_visibility import (  # noqa: E402
    MIGRATE_COLUMNS, _StrictSupabase, _row, app, client, iso_catalog, login)

# app/client/iso_catalog are imported pytest fixtures: binding them here is
# what registers them for the tests below.
_REGISTERED_FIXTURES = (app, client, iso_catalog)

FAKE_PROJECT = "https://proj.supabase.co"


# ------------------------------------------------------------------- helpers
def _production(monkeypatch):
    """Run the fixture guard as the live shop does (ENV != testing)."""
    monkeypatch.setattr(Config, "ENV", "production")
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)


class _Bucket:
    """In-memory Supabase storage bucket that can answer existence checks."""

    def __init__(self, owner):
        self._o = owner

    def upload(self, path, file, file_options=None):
        self._o.objects[path] = (file.read() if hasattr(file, "read") else bytes(file),
                                 (file_options or {}).get("content-type", ""))
        return {"Key": "uploads/" + path}

    def remove(self, paths):
        removed = [p for p in (paths or []) if p in self._o.objects]
        for p in removed:
            del self._o.objects[p]
        return removed

    def info(self, path):
        if path not in self._o.objects:
            raise Exception("Object not found")
        return {"Key": path, "name": os.path.basename(path)}


class _Storage:
    def __init__(self, owner):
        self._o = owner

    def from_(self, name):
        return _Bucket(self._o)


def _fake_bucket(monkeypatch, objects=None):
    fake = _StrictSupabase(MIGRATE_COLUMNS)
    fake.objects = dict(objects or {})
    fake.storage = _Storage(fake)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(Config, "UPLOAD_MODE", "supabase")
    monkeypatch.setattr(Config, "SUPABASE_URL", FAKE_PROJECT)
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake")
    storage_mod._object_exists_cache.clear()
    return fake


def _fixture_row(pid="jau-stock-em2", **over):
    row = _row(pid, sku="JAUSTOCKEM2", name="Stock Test " + pid, online=True)
    row.update(over)
    return row


# ------------------------------------------------------- 1. fixtures stay gone
def test_a_fixture_is_recognised_by_id_sku_or_name():
    assert catalog_mod.is_test_fixture({"id": "jau-stock-a"})
    assert catalog_mod.is_test_fixture({"id": "jau-mirror-post"})
    assert catalog_mod.is_test_fixture({"sku": "JAUSTOCKDEC"})
    assert catalog_mod.is_test_fixture({"name": "Stock Test jau-stock-dec"})
    assert not catalog_mod.is_test_fixture({"id": "wix-002", "sku": "JAU002",
                                            "name": "100L storage bag"})


def test_merged_never_serves_a_fixture_on_a_live_shop(monkeypatch, iso_catalog):
    _production(monkeypatch)
    monkeypatch.setattr(catalog_mod, "_supabase_products",
                        lambda: [_fixture_row(), _row("jau-real", name="Real bag")])
    ids = {p["id"] for p in catalog_mod.merged(include_hidden=True)}
    assert "jau-stock-em2" not in ids
    assert "jau-real" in ids


def test_merged_keeps_fixtures_under_pytest(monkeypatch, iso_catalog):
    """The suite's own data must keep working (filters, dedupe, simulators)."""
    monkeypatch.setattr(catalog_mod, "_supabase_products",
                        lambda: [_fixture_row()])
    ids = {p["id"] for p in catalog_mod.merged(include_hidden=True)}
    assert "jau-stock-em2" in ids


def test_upsert_refuses_to_re_create_a_fixture(client, iso_catalog, monkeypatch):
    """The exact resurrection path: a test re-saves the fixture it just
    created, upsert clears the durable tombstone and the product is live
    again on the storefront."""
    fake = _fake_bucket(monkeypatch)
    _production(monkeypatch)
    tok = login(client)
    r = client.post("/api/admin/products", json={"product": _fixture_row()},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 400, r.data
    assert "test product" in r.get_json()["error"]
    assert fake.tables.get("products", []) == []


def test_live_products_still_save_normally(client, iso_catalog, monkeypatch):
    fake = _fake_bucket(monkeypatch)
    _production(monkeypatch)
    tok = login(client)
    r = client.post("/api/admin/products",
                    json={"product": _row("jau-real-2", name="Real piece")},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert any(x.get("id") == "jau-real-2" for x in fake.tables["products"])


def test_remirror_never_pushes_a_deleted_or_fixture_row(monkeypatch, iso_catalog):
    """A tombstoned row is absent from the live read - and that used to make
    the local copy look "local only", so the scheduler re-published it."""
    _production(monkeypatch)
    path = catalog_mod._norm_filename(catalog_mod.CATALOG_FILE)
    catalog_mod._write_overrides({
        "products": [_fixture_row("jau-stock-a"),
                     _row("jau-deleted", name="Gone piece")],
        "deleted": ["jau-deleted"], "updatedAt": "", "updatedBy": "",
    }, path)
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: [])
    monkeypatch.setattr(catalog_mod, "_durable_deleted_ids", lambda: {"jau-stock-b"})
    stray_ids = {p["id"] for p in catalog_mod.local_only_products()}
    assert stray_ids == set()


def test_purge_tombstones_what_is_already_there(monkeypatch, iso_catalog):
    _production(monkeypatch)
    fake = _fake_bucket(monkeypatch)
    fake.tables["products"] = [_fixture_row(), _row("jau-real", name="Real bag")]
    fake.tables["growth_settings"] = []
    report = catalog_mod.purge_test_fixtures()
    assert report["found"] == ["jau-stock-em2"]
    assert report["tombstoned"] == ["jau-stock-em2"]
    stored = [x for x in fake.tables["products"] if x["id"] == "jau-stock-em2"][0]
    assert stored["source"] == "deleted"
    assert "jau-stock-em2" in supabase_store.load_deleted_ids()


# ------------------------------------------------ 2. a photo beats a placeholder
def test_normalize_promotes_a_real_photo_over_the_placeholder():
    out = catalog_mod.normalize({
        "id": "jau-photo-1", "name": "Storage bag",
        "image": catalog_mod.PLACEHOLDER_IMG,
        "images": [catalog_mod.PLACEHOLDER_IMG, "/uploads/products/2026/09/real.jpg"],
    })
    assert out["image"] == "/uploads/products/2026/09/real.jpg"
    assert out["image_url"] == out["image"]
    assert out["images"] == ["/uploads/products/2026/09/real.jpg"]


def test_resolve_image_promotes_a_real_photo_over_the_placeholder():
    out = catalog_mod.resolve_image({
        "id": "jau-photo-2", "slug": "storage-bag", "name": "Storage bag",
        "image": catalog_mod.PLACEHOLDER_IMG,
        "images": ["/uploads/products/2026/09/real.jpg"],
    })
    assert out["image"] == "/uploads/products/2026/09/real.jpg"
    assert out["usesPlaceholder"] is False


def test_a_product_without_any_photo_still_gets_the_branded_card():
    out = catalog_mod.resolve_image({"id": "jau-3", "slug": "nothing-matched",
                                     "name": "Nothing", "image": "", "images": []})
    assert out["image"] == catalog_mod.PLACEHOLDER_IMG
    assert out["usesPlaceholder"] is True


# --------------------------------------------- 3. a missing upload is healed
def test_object_exists_reports_a_missing_key(monkeypatch):
    fake = _fake_bucket(monkeypatch, {"products/2026/09/here.jpg": (b"x", "image/jpeg")})
    assert storage_mod.object_exists("/uploads/products/2026/09/here.jpg") is True
    assert storage_mod.object_exists("/uploads/products/2026/09/gone.jpg") is False
    # a committed repo asset is not a stored object: nothing to check
    assert storage_mod.object_exists("images/products/_placeholder.jpg") is None


def test_repair_re_points_a_missing_cover_at_a_live_sibling(monkeypatch, iso_catalog):
    _production(monkeypatch)
    dead = "/uploads/products/2026/09/gone.jpg"
    alive = "/uploads/products/2026/09/here.jpg"
    fake = _fake_bucket(monkeypatch, {alive[9:]: (b"x", "image/jpeg")})
    fake.tables["products"] = [_row("jau-photo-3", name="Storage bag",
                                    image=dead, images=[dead, alive],
                                    placeholderImage=catalog_mod.PLACEHOLDER_IMG)]
    report = catalog_mod.repair_dead_photos()
    assert report["missing"] == ["jau-photo-3"]
    assert report["repaired"][0]["image"] == alive
    stored = [x for x in fake.tables["products"] if x["id"] == "jau-photo-3"][0]
    assert stored["image"] == alive, "the repair was not saved, so a deploy undoes it"
    assert stored["image_url"] == alive
    # the fake table has no `images` column (MIGRATE_COLUMNS); the resilient
    # writer drops it, exactly as it does against the real narrow table
    assert "images" not in stored or stored["images"][0] == alive


def test_repair_leaves_a_healthy_photo_alone(monkeypatch, iso_catalog):
    _production(monkeypatch)
    alive = "/uploads/products/2026/09/here.jpg"
    fake = _fake_bucket(monkeypatch, {alive[9:]: (b"x", "image/jpeg")})
    fake.tables["products"] = [_row("jau-photo-4", name="Bag", image=alive,
                                    images=[alive])]
    before = dict(fake.tables["products"][0])
    report = catalog_mod.repair_dead_photos()
    assert report["repaired"] == [] and report["missing"] == []
    assert fake.tables["products"][0] == before


def test_photo_missing_route_repairs_the_row(client, iso_catalog, monkeypatch):
    _production(monkeypatch)
    dead = "/uploads/products/2026/09/gone.jpg"
    alive = "/uploads/products/2026/09/here.jpg"
    fake = _fake_bucket(monkeypatch, {alive[9:]: (b"x", "image/jpeg")})
    fake.tables["products"] = [_row("jau-photo-5", name="Storage bag",
                                    image=dead, images=[dead, alive])]
    r = client.post("/api/photo-missing", json={"url": dead})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["repaired"] is True and body["image"] == alive
    stored = [x for x in fake.tables["products"] if x["id"] == "jau-photo-5"][0]
    assert stored["image"] == alive


def test_photo_missing_ignores_a_foreign_url(client, monkeypatch):
    _fake_bucket(monkeypatch)
    r = client.post("/api/photo-missing", json={"url": "https://evil.example/x.jpg"})
    assert r.status_code == 400


def test_a_referenced_product_photo_is_never_deleted(monkeypatch, iso_catalog):
    _production(monkeypatch)
    alive = "/uploads/products/2026/09/here.jpg"
    fake = _fake_bucket(monkeypatch, {alive[9:]: (b"x", "image/jpeg")})
    fake.tables["products"] = [_row("jau-photo-6", name="Bag", image=alive,
                                    images=[alive])]
    assert storage_mod.delete_upload(alive) is False
    assert alive[9:] in fake.objects, "the only copy of a shop photo was removed"
    # a receipt is still deleted normally
    proof = "proofs/2026/09/receipt.jpg"
    fake.objects[proof] = (b"x", "image/jpeg")
    assert storage_mod.delete_upload("/uploads/" + proof) is True
    assert proof not in fake.objects


def test_admin_photo_repair_endpoint_reports_what_it_did(client, iso_catalog, monkeypatch):
    _production(monkeypatch)
    dead = "/uploads/products/2026/09/gone.jpg"
    alive = "/uploads/products/2026/09/here.jpg"
    fake = _fake_bucket(monkeypatch, {alive[9:]: (b"x", "image/jpeg")})
    fake.tables["products"] = [_row("jau-photo-7", name="Bag", image=dead,
                                    images=[dead, alive])]
    tok = login(client)
    r = client.post("/api/admin/photos/repair", headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["repaired"] and body["repaired"][0]["id"] == "jau-photo-7"


def test_admin_photo_repair_needs_a_session(client, monkeypatch):
    _fake_bucket(monkeypatch)
    assert client.post("/api/admin/photos/repair").status_code in (401, 403)


def test_admin_editor_never_keeps_the_placeholder_as_the_cover():
    """The client half of the fix, pinned on the shipped source."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "js", "admin.js"), encoding="utf-8").read()
    assert "isPlaceholderPhoto" in src
    assert "realPhotos.length ? realPhotos.slice(0, 20) : placeholderPhotos.slice(0, 1)" in src
    # and the storefront reports a photo that 404ed, so the row can be healed
    store = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "js", "store.js"), encoding="utf-8").read()
    assert "api/photo-missing" in store
    assert "reportMissingPhoto" in store
