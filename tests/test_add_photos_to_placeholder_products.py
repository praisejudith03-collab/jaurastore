"""The owner's workflow: add a photo to a product that says "photo coming soon".

Pieces of this were already covered in isolation (``normalize`` promotes a real
photo over the placeholder, the admin editor never keeps the placeholder as the
cover, an upload lands in /uploads/products/). This file drives the whole thing
through the HTTP API the admin portal actually calls, in the order it calls
them, because that is the path the owner uses:

    sign in -> POST /api/admin/uploads/product -> POST /api/admin/products
            -> (logged out) GET /api/catalog

and asserts the logged-out storefront serves the uploaded photo for a product
whose cover was the branded placeholder - then again from a fresh read, which is
what a redeploy does.

Run with:  python3 -m pytest tests/test_add_photos_to_placeholder_products.py -q
"""
import io
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

import catalog as catalog_mod  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_save_visibility import (  # noqa: E402
    MIGRATE_COLUMNS, _StrictSupabase, _row, app, client, iso_catalog, login)

# app/client/iso_catalog are imported pytest fixtures: binding them here is what
# registers them for the tests below.
_REGISTERED_FIXTURES = (app, client, iso_catalog)

PLACEHOLDER = catalog_mod.PLACEHOLDER_IMG          # images/products/_placeholder.jpg


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


def _placeholder_product(pid="jau-nophoto-1"):
    """A live product whose only picture is the branded placeholder."""
    return _row(pid, name="Woven storage basket", image=PLACEHOLDER,
                images=[PLACEHOLDER])


def _upload(client, tok, name="basket.png"):
    r = client.post("/api/admin/uploads/product",
                    data={"file": (io.BytesIO(_png()), name)},
                    headers={"X-CSRF-Token": tok},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] and body["kind"] == "image", body
    return body["url"]


def _public_product(pid):
    """What a logged-out visitor's /api/catalog receives for one product."""
    for p in catalog_mod.merged():
        if str(p.get("id")) == pid:
            return p
    raise AssertionError("product %s is not in the public catalogue" % pid)


def test_an_admin_upload_becomes_the_cover_of_a_placeholder_product(client, iso_catalog):
    tok = login(client)

    # 1. the product exists and the storefront shows the placeholder card
    r = client.post("/api/admin/products", json={"product": _placeholder_product()},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert _public_product("jau-nophoto-1")["image"] == PLACEHOLDER

    # 2. the owner picks a photo - exactly the request the media editor sends
    url = _upload(client, tok)
    assert url.startswith("/uploads/products/"), url

    # 3. saving the product with that photo as its cover
    saved = _placeholder_product()
    saved["image"] = url
    saved["images"] = [url]
    r = client.post("/api/admin/products", json={"product": saved},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data

    # 4. the logged-out storefront must serve the photo, not the placeholder
    live = _public_product("jau-nophoto-1")
    assert live["image"] == url, live["image"]
    assert PLACEHOLDER not in [live["image"], *(live.get("images") or [])]
    assert url in (live.get("images") or [])


def test_the_new_photo_survives_a_reload_and_a_placeholder_sibling_does_not_win(
        client, iso_catalog):
    """Two regressions in one: the photo comes back after a fresh read (a
    redeploy re-reads the same file), and a product whose gallery keeps the
    placeholder is still promoted to the real photo."""
    tok = login(client)
    url = _upload(client, tok, name="second.png")
    row = _placeholder_product("jau-nophoto-2")
    row["images"] = [PLACEHOLDER, url]          # placeholder first, photo second
    r = client.post("/api/admin/products", json={"product": row},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data

    catalog_mod._merged_cache = None            # a fresh process would re-read
    live = _public_product("jau-nophoto-2")
    assert live["image"] == url, live["image"]
    assert PLACEHOLDER not in (live.get("images") or [])


def test_an_uploaded_photo_is_not_replaced_by_a_stale_supabase_row(
        client, iso_catalog, monkeypatch):
    """The live shop reads Supabase. If that row still carries the placeholder,
    the admin's own edit has to win - otherwise the photo "disappears again"
    on the next deploy, which is the report this whole file comes from."""
    fake = _StrictSupabase(MIGRATE_COLUMNS)
    monkeypatch.setattr("supabase_store.client", lambda: fake)
    monkeypatch.setattr("supabase_store.enabled", lambda: True)
    monkeypatch.setattr(catalog_mod, "is_test_fixture", lambda row: False)
    tok = login(client)
    url = _upload(client, tok, name="third.png")

    row = _placeholder_product("jau-nophoto-3")
    row["image"] = url
    row["images"] = [url]
    r = client.post("/api/admin/products", json={"product": row},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data

    # the product table still holds the old placeholder row for that id
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: [
        _row("jau-nophoto-3", name="Woven storage basket", image=PLACEHOLDER,
             images=[PLACEHOLDER])])
    live = _public_product("jau-nophoto-3")
    assert live["image"] == url, (
        "a stale Supabase row put the placeholder back on top of the upload")
