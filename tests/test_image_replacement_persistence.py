"""Regression: replacing a product image must persist, end to end.

The incident: in /admin, uploading a NEW photo for an existing product and
pressing Save left the row pinned to the OLD photo. The editor spread the
existing row into the payload (``{...existing, image: new}``), so the stale
``image_url`` alias travelled along - and catalog.normalize() read
``image_url`` FIRST, so the stale alias silently won. The new upload only
ever reached the gallery; ``image`` and ``image_url`` stayed old on the
save response, /api/catalog, /api/catalog?all=1 and every reload.

These tests freeze the fixed contract:

  * the explicit ``image`` field wins over a stale ``image_url`` alias;
  * ``image`` and ``image_url`` are always written to the SAME value;
  * the new upload becomes the cover AND stays in the gallery;
  * a placeholder never outranks a real photo;
  * re-saving without touching images never wipes them;
  * deleting one gallery item never touches another product's media;
  * a cache-buster query (?v=) can never confuse one stored object for two.

Run with:  python3 -m pytest tests/test_image_replacement_persistence.py -q
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
import storage  # noqa: E402
import supabase_store  # noqa: E402
from config import Config  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_save_visibility import (  # noqa: E402
    MIGRATE_COLUMNS, _StrictSupabase, _row, login, app, client, iso_catalog)
from test_uploaded_photos import _jpeg, _Storage  # noqa: E402

# Bind the imported fixtures into this module's namespace.
_REGISTERED_FIXTURES = (app, client, iso_catalog)

PLACEHOLDER = catalog_mod.PLACEHOLDER_IMG

fake = None


@pytest.fixture(autouse=True)
def _supabase_upload(monkeypatch):
    """Pretend Supabase is configured and hand the fake a storage bucket."""
    global fake
    fake = _StrictSupabase(MIGRATE_COLUMNS)
    fake.objects = {}
    fake.storage = _Storage(fake)
    monkeypatch.setattr(Config, "UPLOAD_MODE", "supabase")
    monkeypatch.setattr(Config, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake")
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    yield fake


def _upload(client, tok, name="photo.jpg"):
    r = client.post("/api/admin/uploads/product", data={"file": (_jpeg(), name)},
                    headers={"X-CSRF-Token": tok}, content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    return r.get_json()["url"]


def _save(client, tok, product):
    r = client.post("/api/admin/products", json={"product": product},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body.get("mirrored") is not False
    return body["product"]


def _supabase_row(pid):
    rows = [x for x in fake.tables["products"] if x.get("id") == pid]
    assert rows, f"{pid} never reached the products table"
    return rows[0]


# ---------------------------------------------------------------- unit level

def test_normalize_lets_the_fresh_image_win_over_a_stale_image_url():
    """The exact payload shape that produced the incident."""
    new = "/uploads/products/2026/new.jpg"
    old = "/uploads/products/2026/old.jpg"
    out = catalog_mod.normalize({
        "id": "jau-rep-1", "name": "Rep", "priceNgn": 1000,
        # ...existing spread: the row's previous aliases
        "image": new,            # the editor's fresh cover
        "image_url": old,        # stale alias carried by the spread
        "images": [new],
    })
    assert out["image"] == new
    assert out["image_url"] == new, "image_url must be re-synced to the fresh cover"
    assert out["images"] == [new]


def test_normalize_image_url_alias_used_only_when_image_blank():
    """Legacy mirror/import rows that carry ONLY image_url still work."""
    url = "/uploads/products/2026/only-alias.jpg"
    out = catalog_mod.normalize({"id": "jau-rep-1b", "name": "Rep", "priceNgn": 1000,
                                 "image_url": url, "images": [url]})
    assert out["image"] == url and out["image_url"] == url


def test_placeholder_never_wins_over_a_real_photo():
    real = "/uploads/products/2026/real.jpg"
    out = catalog_mod.normalize({
        "id": "jau-rep-2", "name": "Rep", "priceNgn": 1000,
        "image": PLACEHOLDER, "image_url": PLACEHOLDER, "images": [PLACEHOLDER, real]})
    assert out["image"] == real
    assert out["image_url"] == real
    assert out["images"] == [real]
    # ...and a real image_url outranks a placeholder cover (narrow mirror row)
    out2 = catalog_mod.normalize({
        "id": "jau-rep-2b", "name": "Rep", "priceNgn": 1000,
        "image": PLACEHOLDER, "image_url": real, "images": [real]})
    assert out2["image"] == real


def test_resolve_image_syncs_image_url_after_placeholder_promotion():
    real = "/uploads/products/2026/promoted.jpg"
    out = catalog_mod.resolve_image({"id": "jau-rep-3", "slug": "rep-3", "name": "Rep",
                                     "image": PLACEHOLDER, "image_url": PLACEHOLDER,
                                     "images": [real]})
    assert out["image"] == out["image_url"]
    assert out["usesPlaceholder"] is False


def test_resaving_details_without_touching_images_keeps_them():
    real = "/uploads/products/2026/keep.jpg"
    first = catalog_mod.normalize({"id": "jau-rep-4", "name": "Rep", "priceNgn": 1000,
                                   "image": real, "image_url": real, "images": [real]})
    again = catalog_mod.normalize({**first, "priceNgn": 2000})
    assert again["image"] == real
    assert again["image_url"] == real
    assert again["images"] == [real]
    assert again["priceNgn"] == 2000


def test_storage_key_ignores_the_cache_buster_query():
    """'/uploads/x.jpg' and '/uploads/x.jpg?v=9' are ONE object, never two."""
    plain = "/uploads/products/2026/same.jpg"
    busted = plain + "?v=abc123"
    assert storage._key_from_url(busted) == storage._key_from_url(plain) != ""
    assert storage._key_from_url(busted) == "products/2026/same.jpg"


def test_supabase_canonicalization_preserves_uploaded_urls():
    row = supabase_store._canonicalize_product({
        "id": "jau-rep-5", "name": "Rep",
        "image": "/uploads/products/2026/x.jpg?v=1",
        "image_url": "/uploads/products/2026/x.jpg?v=1",
        "images": ["/uploads/products/2026/x.jpg?v=1"],
        "stock": 3,
    })
    assert row["image"] == "/uploads/products/2026/x.jpg?v=1"
    assert row["image_url"] == "/uploads/products/2026/x.jpg?v=1"
    assert row["images"] == ["/uploads/products/2026/x.jpg?v=1"]


# ------------------------------------------------------- end-to-end API flow

def test_replacing_a_product_image_survives_every_readback(client, iso_catalog):
    """Upload new -> save with the legacy stale-alias payload -> every surface
    shows the NEW photo, and the old file is purged as intentionally replaced."""
    tok = login(client)
    old_url = _upload(client, tok, "old.jpg")
    new_url = _upload(client, tok, "new.jpg")

    created = _save(client, tok, _row("jau-replace-e2e", name="Replace Me",
                                      image=old_url, images=[old_url]))
    assert created["image"] == old_url

    # What a client whose bundle still spreads the row's aliases sends:
    # fresh image/images + the STALE image_url of the row it loaded.
    payload = dict(created)
    payload.update({"image": new_url, "images": [new_url],
                    "image_url": old_url, "imageUrl": old_url})
    saved = _save(client, tok, payload)

    # 1) the save RESPONSE carries the new photo, in sync
    assert saved["image"] == new_url
    assert saved["image_url"] == new_url
    assert saved["images"] == [new_url]

    # 2) the Supabase row itself (redeploy/read-back proof)
    row = _supabase_row("jau-replace-e2e")
    assert row["image"] == new_url
    assert row["image_url"] == new_url
    stored_gallery = row["images"]
    if isinstance(stored_gallery, str):
        import json as _json
        stored_gallery = _json.loads(stored_gallery)
    assert list(stored_gallery) == [new_url]

    # 3) admin catalogue (?all=1) and public catalogue agree
    admin_body = client.get("/api/catalog?all=1").get_json()
    admin_row = {p["id"]: p for p in admin_body["products"]}["jau-replace-e2e"]
    assert admin_row["image"].split("?")[0] == new_url.split("?")[0]
    public_body = client.get("/api/catalog").get_json()
    public_row = {p["id"]: p for p in public_body["products"]}["jau-replace-e2e"]
    assert public_row["image"].split("?")[0] == new_url.split("?")[0]
    assert public_row["images"] == admin_row["images"]

    # 4) the intentionally replaced object is gone; the new one is in storage
    assert old_url[len("/uploads/"):] not in fake.objects
    assert new_url[len("/uploads/"):] in fake.objects


def test_existing_product_media_replacement_publishes_and_purges_immediately(
        client, iso_catalog, monkeypatch):
    """The media-only edit is live before a separate Product Save is pressed."""
    tok = login(client)
    old_url = _upload(client, tok, "immediate-old.jpg")
    new_url = _upload(client, tok, "immediate-new.jpg")
    _save(client, tok, _row("jau-immediate-media", name="Immediate Media",
                            image=old_url, images=[old_url]))

    response = client.put(
        "/api/admin/products/jau-immediate-media/media",
        json={"images": [new_url]}, headers={"X-CSRF-Token": tok})
    assert response.status_code == 200, response.data
    saved = response.get_json()["product"]
    assert saved["image"] == saved["image_url"] == new_url
    assert saved["images"] == [new_url]
    assert old_url[len("/uploads/"):] not in fake.objects
    assert new_url[len("/uploads/"):] in fake.objects
    assert _supabase_row("jau-immediate-media")["image"] == new_url

    # A failed row save must not remove the only saved image. The new upload
    # remains an unreferenced orphan, eligible for the guarded cleanup later.
    newer_url = _upload(client, tok, "failed-replacement.jpg")
    monkeypatch.setattr(catalog_mod, "upsert",
                        lambda *_args, **_kwargs: (None, "error", False))
    failed = client.put(
        "/api/admin/products/jau-immediate-media/media",
        json={"images": [newer_url]}, headers={"X-CSRF-Token": tok})
    assert failed.status_code == 503
    assert new_url[len("/uploads/"):] in fake.objects
    assert _supabase_row("jau-immediate-media")["image"] == new_url


def test_admin_editor_autopublishes_existing_product_media_without_full_save():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "js" / "admin.js").read_text()
    assert '"/media"' in source
    assert 'method: "PUT", json: { images }, label: "Product photos"' in source
    assert "persistEditedMedia()" in source
    assert "Photo update is live; unused replaced files were deleted." in source
    assert "The previous saved photo is safe." in source
    assert "const editorStillCurrent = session === Number(window.__editMediaSession || 0)" in source
    assert "window.__editMediaSave = Promise.resolve();" not in source
    assert "Keep the file until Save is confirmed" not in source


def test_the_old_image_never_returns_after_a_third_save(client, iso_catalog):
    """Replace, then edit the price only: the OLD cover must not resurrect."""
    tok = login(client)
    old_url = _upload(client, tok, "o2.jpg")
    new_url = _upload(client, tok, "n2.jpg")
    _save(client, tok, _row("jau-replace-e2e3", name="Replace Then Edit",
                            image=old_url, images=[old_url]))
    loaded = client.get("/api/catalog?all=1").get_json()
    current = {p["id"]: p for p in loaded["products"]}["jau-replace-e2e3"]
    # replacement upload -> gallery [new]
    repl = dict(current)
    repl.update({"image": new_url, "images": [new_url]})
    _save(client, tok, repl)
    # price-only edit: payload is exactly the served row with a new price
    loaded2 = client.get("/api/catalog?all=1").get_json()
    current2 = {p["id"]: p for p in loaded2["products"]}["jau-replace-e2e3"]
    assert current2["image"] == new_url
    edit = dict(current2)
    edit["priceNgn"] = 12345
    saved = _save(client, tok, edit)
    assert saved["image"] == new_url
    assert saved["image_url"] == new_url
    assert saved["images"] == [new_url]


def test_deleting_one_gallery_image_keeps_other_products_media(client, iso_catalog):
    """Unrelated product photos are never purged by another product's edit,
    even across clean vs cache-busted spellings of the same key."""
    tok = login(client)
    shared = _upload(client, tok, "shared.jpg")
    solo_a = _upload(client, tok, "solo-a.jpg")
    cache_busted_shared = shared + "?v=zz99"

    _save(client, tok, _row("jau-purge-a", name="Has Shared Photo",
                            image=cache_busted_shared, images=[cache_busted_shared]))
    _save(client, tok, _row("jau-purge-b", name="Own Photos",
                            image=solo_a, images=[solo_a, shared]))

    # Product B drops the shared photo from its gallery: the purge must NOT
    # delete the file because product A still shows it (busted spelling).
    loaded = client.get("/api/catalog?all=1").get_json()
    b = {p["id"]: p for p in loaded["products"]}["jau-purge-b"]
    b = dict(b)
    b["images"] = [solo_a]
    b["image"] = solo_a
    _save(client, tok, b)
    assert shared[len("/uploads/"):] in fake.objects, \
        "a photo still referenced by another product was deleted"
    assert solo_a[len("/uploads/"):] in fake.objects

    # Product A drops its reference too: NOW the object may be purged.
    loaded2 = client.get("/api/catalog?all=1").get_json()
    a = {p["id"]: p for p in loaded2["products"]}["jau-purge-a"]
    a = dict(a)
    a["images"] = []
    a["image"] = PLACEHOLDER
    a["image_url"] = PLACEHOLDER
    _save(client, tok, a)
    assert shared[len("/uploads/"):] not in fake.objects, \
        "an intentionally replaced, now-unreferenced file should be purged"


def test_busted_upload_url_round_trips_through_save(client, iso_catalog):
    """The admin saves the cache-busted URL (?v=). It must survive normalize,
    the mirror write and both catalog surfaces - byte for byte."""
    tok = login(client)
    url = _upload(client, tok, "bust.jpg")
    busted = url + "?v=q1w2"
    saved = _save(client, tok, _row("jau-busted", name="Busted URL",
                                    image=busted, image_url=busted, images=[busted]))
    assert saved["image"] == busted
    assert saved["image_url"] == busted
    assert saved["images"] == [busted]
    row = _supabase_row("jau-busted")
    assert row["image"] == busted and row["image_url"] == busted
    # the object key resolves to the same file regardless of the token
    assert storage._key_from_url(row["image"]) == storage._key_from_url(url)


def test_supplier_link_survives_an_image_replacement(client, app, iso_catalog):
    """Some products carry the supplier link - a photo swap must never wipe it."""
    tok = login(client)
    old_url = _upload(client, tok, "sup-old.jpg")
    new_url = _upload(client, tok, "sup-new.jpg")
    supplier = "https://supplier.example/item/42"
    _save(client, tok, _row("jau-keep-supplier", name="Keep Supplier Link",
                            image=old_url, images=[old_url],
                            supplierSku=supplier, supplierUrl=supplier,
                            supplier_url=supplier))
    loaded = client.get("/api/catalog?all=1").get_json()
    current = {p["id"]: p for p in loaded["products"]}["jau-keep-supplier"]
    payload = dict(current)
    payload.update({"image": new_url, "images": [new_url]})
    saved = _save(client, tok, payload)
    assert saved["image"] == new_url
    # admin-only field: present in the save response and the admin catalogue
    assert saved.get("supplierSku") == supplier
    row = _supabase_row("jau-keep-supplier")
    assert row.get("supplierSku") == supplier
    # ...but never exposed publicly (a signed-OUT visitor gets the stripped row)
    anon = app.test_client()
    public = anon.get("/api/catalog").get_json()
    pub = {p["id"]: p for p in public["products"]}["jau-keep-supplier"]
    assert "supplierSku" not in pub and "supplier_url" not in pub
    assert "supplierUrl" not in pub and "optionSupplierSku" not in pub
    # stock numbers stay admin-only too
    assert "stock" not in pub and "stock_quantity" not in pub
    assert pub.get("stock_status") == "in"


# ------------------------------------------- replacement is a save, not a delete
def test_a_replacement_never_calls_the_hard_delete_rpc(client, iso_catalog, monkeypatch):
    """A gallery swap is an ordinary save - the delete RPC must never run.

    ``hard_delete_products`` writes a PERMANENT tombstone for the product id'd
    it removes. Calling it for a photo replacement would delete the product
    itself and then reject every future save under that id ("product id was
    permanently deleted"), so the replacement path has to stay a save:
    confirm the new gallery, then purge only the old unreferenced object.
    """
    tok = login(client)
    old_url = _upload(client, tok, "norpc-old.jpg")
    new_url = _upload(client, tok, "norpc-new.jpg")
    _save(client, tok, _row("jau-norpc", name="No RPC", image=old_url,
                            images=[old_url]))

    calls = []
    monkeypatch.setattr(supabase_store, "hard_delete_products",
                        lambda ids: calls.append(list(ids)) or
                        {"deleted": list(ids), "files": 0, "errors": []})

    r = client.put("/api/admin/products/jau-norpc/media",
                   json={"images": [new_url]}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert r.get_json()["product"]["image"] == new_url
    assert calls == [], "image replacement must never hard-delete the product"
    assert _supabase_row("jau-norpc")["image"] == new_url
    assert old_url[len("/uploads/"):] not in fake.objects
    assert new_url[len("/uploads/"):] in fake.objects


def test_the_new_gallery_is_saved_before_any_old_media_is_purged(
        client, iso_catalog, monkeypatch):
    """Save (and read back) the new gallery FIRST, purge the old one after.

    The purge is driven by catalog.upsert's production branch, which returns
    before it if the row was not confirmed written. That order is what makes
    "the previous saved photo is safe" true when the save fails.
    """
    tok = login(client)
    old_url = _upload(client, tok, "order-old.jpg")
    new_url = _upload(client, tok, "order-new.jpg")
    _save(client, tok, _row("jau-order-purge", name="Ordered Purge",
                            image=old_url, images=[old_url]))

    events = []
    real_upsert = catalog_mod.upsert

    def traced_upsert(product, actor=None):
        events.append("save")
        return real_upsert(product, actor)

    monkeypatch.setattr(catalog_mod, "upsert", traced_upsert)
    monkeypatch.setattr(catalog_mod, "_purge_removed_media",
                        lambda before, after=None: events.append("purge") or 0)

    r = client.put("/api/admin/products/jau-order-purge/media",
                   json={"images": [new_url]}, headers={"X-CSRF-Token": tok})

    assert r.status_code == 200, r.data
    assert events == ["save", "purge"], events


def test_a_failed_cloud_save_keeps_the_previous_photo_and_purges_nothing(
        client, iso_catalog, monkeypatch):
    """No confirmed row write -> no purge, and the old photo is still there.

    This is the failure the incident got wrong the other way round: an
    unconfirmed save must leave the saved reference untouched, and the newly
    uploaded file stays an unreferenced orphan for the guarded cleanup.
    """
    tok = login(client)
    old_url = _upload(client, tok, "keep-old.jpg")
    new_url = _upload(client, tok, "keep-new.jpg")
    _save(client, tok, _row("jau-keep-old", name="Keep Old", image=old_url,
                            images=[old_url]))

    purges = []
    monkeypatch.setattr(catalog_mod, "_purge_removed_media",
                        lambda before, after=None: purges.append((before, after)))
    monkeypatch.setattr(supabase_store, "upsert_products", lambda rows: False)

    r = client.put("/api/admin/products/jau-keep-old/media",
                   json={"images": [new_url]}, headers={"X-CSRF-Token": tok})

    assert r.status_code == 503, r.data
    assert purges == [], "an unconfirmed save must not remove the saved photo"
    assert old_url[len("/uploads/"):] in fake.objects
    assert _supabase_row("jau-keep-old")["image"] == old_url
