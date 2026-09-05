"""An uploaded product photo must reach the product - same-origin only.

The incident: the Supabase back end handed out absolute bucket URLs at upload
time, catalog.resolve_image stripped every http(s) value as third-party (the
anti-Wix policy) and the photo collapsed to the branded placeholder on every
device, while the bytes sat fine in the bucket. TASK 5 makes product rows
carry the same-origin /uploads/<key> shape, lets /uploads/ reach the bucket
for objects this server does not hold on disk, and rewrites our own bucket
URLs at read time - without ever relaxing the external-host policy that
tests/test_api.py guards.

Run with:  python3 -m pytest tests -q
No real Supabase is needed: supabase_store.client() is monkeypatched to an
in-memory fake that also carries a fake storage bucket.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

import catalog as catalog_mod  # noqa: E402
import storage  # noqa: E402
import supabase_store  # noqa: E402
from config import Config  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_save_visibility import (  # noqa: E402
    MIGRATE_COLUMNS, _StrictSupabase, _row, login, iso_catalog, app, client)

# app/client/iso_catalog are imported pytest fixtures: binding them in this
# module's namespace is what registers them for the tests below.
_REGISTERED_FIXTURES = (app, client, iso_catalog)


def _jpeg():
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (40, 30), (200, 60, 90)).save(buf, format="JPEG")
    buf.seek(0)
    buf.name = "dudu-lotion.jpg"
    return buf


class _Bucket:
    """In-memory stand-in for one Supabase storage bucket."""

    def __init__(self, owner): self._o = owner

    def upload(self, path, file, file_options=None):
        self._o.objects[path] = (file.read() if hasattr(file, "read") else bytes(file),
                                 (file_options or {}).get("content-type", ""))
        return {"Key": "uploads/" + path}

    def remove(self, paths):
        removed = [p for p in (paths or []) if p in self._o.objects]
        for p in removed:
            del self._o.objects[p]
        return removed


class _Storage:
    def __init__(self, owner): self._o = owner

    def from_(self, name): return _Bucket(self._o)


fake = None


@pytest.fixture(autouse=True)
def _supabase_upload(monkeypatch):
    """Pretend Supabase is configured and hand the fake a storage bucket."""
    global fake
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa"})
    fake.objects = {}
    fake.storage = _Storage(fake)
    monkeypatch.setattr(Config, "UPLOAD_MODE", "supabase")
    monkeypatch.setattr(Config, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake")
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    yield fake


def test_own_bucket_url_becomes_an_uploads_link():
    """Our own storage URL is same-origin, not external."""
    import storage as storage_mod
    url = ("https://fake.supabase.co/storage/v1/object/public/uploads/"
           "products/2026/ab12-34.jpg")
    assert storage_mod.own_upload_path(url) == "/uploads/products/2026/ab12-34.jpg"
    out = catalog_mod.resolve_image({"id": "jau-1", "slug": "jau-1", "name": "X",
                                     "image": url, "images": [url]})
    assert out["image"] == "/uploads/products/2026/ab12-34.jpg"
    assert out["usesPlaceholder"] is False
    assert out["images"] == ["/uploads/products/2026/ab12-34.jpg"]


def test_own_upload_cover_keeps_repo_gallery_entries():
    """A committed gallery path rides along; only foreign hosts are dropped."""
    base = "https://fake.supabase.co/storage/v1/object/public/uploads/"
    out = catalog_mod.resolve_image({
        "id": "jau-gal", "slug": "jau-gal", "name": "X",
        "image": base + "products/2026/a.jpg",
        "images": ["images/brand/logo.jpg", base + "products/2026/b.jpg",
                   "https://cdn.example.com/x.jpg"]})
    assert out["image"] == "/uploads/products/2026/a.jpg"
    assert out["images"] == ["images/brand/logo.jpg",
                             "/uploads/products/2026/b.jpg"]


def test_a_foreign_host_still_becomes_the_placeholder():
    """The anti-Wix policy is untouched."""
    out = catalog_mod.resolve_image({
        "id": "jau-2", "slug": "jau-2", "name": "X",
        "image": "https://static.wixstatic.com/media/x.jpg"})
    assert out["image"] == catalog_mod.PLACEHOLDER_IMG
    assert out["usesPlaceholder"] is True
    out2 = catalog_mod.resolve_image({
        "id": "jau-3", "slug": "jau-3", "name": "X",
        "image": "https://other.supabase.co/storage/v1/object/public/uploads/products/x.jpg"})
    assert out2["image"] == catalog_mod.PLACEHOLDER_IMG


def test_upload_route_hands_out_a_same_origin_link(client, monkeypatch):
    tok = login(client)
    r = client.post("/api/admin/uploads/product", data={"file": (_jpeg(), "d.jpg")},
                    headers={"X-CSRF-Token": tok}, content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    url = r.get_json()["url"]
    assert url.startswith("/uploads/products/"), url
    assert url[len("/uploads/"):] in fake.objects, "the bytes never reached the bucket"


def test_uploads_route_redirects_a_public_key_and_404s_a_proof(client, monkeypatch):
    tok = login(client)
    up = client.post("/api/admin/uploads/product", data={"file": (_jpeg(), "p.jpg")},
                     headers={"X-CSRF-Token": tok},
                     content_type="multipart/form-data")
    key = up.get_json()["url"][len("/uploads/"):]
    assert not storage.resolve_local(key), "the object must not be on this disk"
    g = client.get("/uploads/" + key, follow_redirects=False)
    assert g.status_code == 302, g.status_code
    assert g.headers["Location"].startswith(
        "https://fake.supabase.co/storage/v1/object/public/")
    # a hand-uploaded, root-level object works the same way
    assert client.get("/uploads/IMG_20260708_094137_76.jpg",
                      follow_redirects=False).status_code == 302
    # a payment proof is never handed out as a public redirect
    assert client.get("/uploads/proofs/2026/private.jpg",
                      follow_redirects=False).status_code == 404


def test_replacing_a_photo_still_deletes_the_old_object(client, monkeypatch):
    """TASK 5 changes the stored shape; deletion must follow it."""
    tok = login(client)
    old = client.post("/api/admin/uploads/product", data={"file": (_jpeg(), "old.jpg")},
                      headers={"X-CSRF-Token": tok},
                      content_type="multipart/form-data").get_json()["url"]
    assert old.startswith("/uploads/")
    assert storage.delete_upload(old) is True
    assert old[len("/uploads/"):] not in fake.objects


def test_saved_product_keeps_its_photo_in_the_mirror(client, iso_catalog, monkeypatch):
    """Upload, save, and the photo is in the Supabase row - not the placeholder."""
    tok = login(client)
    url = client.post("/api/admin/uploads/product", data={"file": (_jpeg(), "dudu.jpg")},
                      headers={"X-CSRF-Token": tok},
                      content_type="multipart/form-data").get_json()["url"]
    r = client.post("/api/admin/products",
                    json={"product": _row("jau-photo", name="Dudu Lotion",
                                          image=url, images=[url])},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert r.get_json()["mirrored"] is True
    row = [x for x in fake.tables["products"] if x.get("id") == "jau-photo"][0]
    assert row["image"] == url, "the mirror stored a placeholder, not the photo"
    assert row["image"] != catalog_mod.PLACEHOLDER_IMG
    served = {str(p["id"]): p for p in catalog_mod.merged(include_hidden=True)}
    assert served["jau-photo"]["image"] == url
    body = client.get("/api/catalog").get_json()
    assert not any((p.get("image") or "").startswith("http") for p in body["products"])
