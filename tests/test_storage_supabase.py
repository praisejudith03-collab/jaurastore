"""Supabase Storage backend tests for storage.py.

Covers the three things that must hold:

* UPLOAD_MODE=supabase uploads to the Supabase bucket and returns
  …/storage/v1/object/public/<bucket>/<key> for public assets, and a
  SIGNED url (never a public one) for payment proofs / receipts.
* A payment receipt is never lost: when the bucket is unreachable (or
  Supabase is not configured at all) the write falls back to the local
  disk, and /uploads/... keeps serving those files.
* delete_upload() removes the object from the Supabase bucket, for both
  the public URL shape and the signed URL shape stored in the database.

Run with:  python3 -m pytest tests -q
No real Supabase is needed: supabase_store.client() is monkeypatched to a
fake in-memory bucket that records every upload / sign / remove.
"""
import io, json, os, struct, sys, zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

from config import Config  # noqa: E402
import storage  # noqa: E402
import supabase_store  # noqa: E402

if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
from db import execute, init_db, one, query  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402  - one strong password per run; ADMIN_PW pins it
EMAIL = "jaurastore@gmail.com"
FAKE_ORIGIN = "https://fake.supabase.co"


# --------------------------------------------------------------------- fixtures
class FakeBucket:
    """In-memory stand-in for a Supabase Storage bucket."""

    def __init__(self, owner, name):
        self._owner = owner
        self._name = name

    def upload(self, path, file, file_options=None):
        if self._owner.fail_uploads:
            raise ConnectionError("bucket unreachable")
        data = file.read() if hasattr(file, "read") else bytes(file)
        self._owner.objects.setdefault(self._name, {})[path] = (
            data, (file_options or {}).get("content-type", ""))
        return {"Key": f"{self._name}/{path}"}

    def create_signed_url(self, path, expires_in, options=None):
        if expires_in > 604800:      # Supabase's real cap: 7 days
            raise ValueError("expiresIn must not exceed 604800")
        self._owner.token_seq += 1
        self._owner.signed.append((self._name, path, expires_in))
        return {"signedUrl": (
            f"{FAKE_ORIGIN}/storage/v1/object/sign/{self._name}/{path}"
            f"?token=fake-token-{self._owner.token_seq}")}

    def remove(self, paths):
        objs = self._owner.objects.get(self._name, {})
        removed = [p for p in paths if p in objs]
        for p in removed:
            del objs[p]
        return [{"id": p} for p in removed]


class FakeStorage:
    def __init__(self, owner):
        self._owner = owner

    def from_(self, name):
        return FakeBucket(self._owner, name)


class FakeTable:
    """In-memory stand-in for one PostgREST table (upsert / select / eq)."""

    def __init__(self, owner, name):
        self._owner = owner
        self._name = name
        self._filter = None

    def upsert(self, rows):
        store = self._owner.tables.setdefault(self._name, [])
        for r in rows:
            if self._name == "growth_settings":      # key/value store
                store[:] = [x for x in store if x.get("key") != r.get("key")]
            else:
                store[:] = [x for x in store if x.get("id") != r.get("id")]
            store.append(dict(r))
        return self

    def select(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n=None):
        return self

    def eq(self, key, val):
        self._filter = (key, val)
        return self

    def execute(self):
        rows = list(self._owner.tables.get(self._name, []))
        if self._filter:
            k, v = self._filter
            rows = [r for r in rows if r.get(k) == v]
        return {"data": rows}


class FakeSupabaseClient:
    def __init__(self):
        self.objects = {}        # bucket -> {path: (bytes, content-type)}
        self.signed = []         # (bucket, path, expires_in) for every sign
        self.token_seq = 0
        self.fail_uploads = False
        self.tables = {}         # table name -> [row dicts]
        self.storage = FakeStorage(self)

    def table(self, name):
        return FakeTable(self, name)


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    """Supabase configured + reachable: UPLOAD_MODE=supabase, fake client."""
    client = FakeSupabaseClient()
    monkeypatch.setattr(Config, "UPLOAD_MODE", "supabase")
    monkeypatch.setattr(Config, "SUPABASE_URL", FAKE_ORIGIN)
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(supabase_store, "client", lambda: client)
    storage._signed_url_cache.clear()
    return client


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


# --------------------------------------------------------------------- payloads
def _png() -> bytes:
    """A real 1x1 PNG (magic bytes matter: storage re-identifies files)."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
            + chunk(b"IEND", b""))


def _pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF"


# ------------------------------------------------------- uploads: public assets
def test_product_photo_goes_to_bucket_with_public_url(fake):
    ok, msg, url = storage.save_image(_png(), "products", "photo.png")
    assert ok, msg
    assert url.startswith(f"{FAKE_ORIGIN}/storage/v1/object/public/uploads/products/")
    path = url.split("/uploads/", 1)[1]
    data, content_type = fake.objects["uploads"][path]
    assert data == _png()
    assert content_type == "image/png"
    # nothing was written to the local disk
    assert not os.path.isdir(storage.local_root())


def test_category_asset_is_public(fake):
    ok, msg, url = storage.save_asset(_png(), "categories", "cover.png")
    assert ok, msg
    assert f"/storage/v1/object/public/uploads/categories/" in url


def test_hero_video_is_public(fake):
    mp4 = b"\x00\x00\x00\x10ftypisom" + b"\x00" * 24
    ok, msg, url = storage.save_video(mp4, "videos", "hero.mp4")
    assert ok, msg
    assert f"/storage/v1/object/public/uploads/videos/" in url


def test_misc_asset_is_public(fake):
    ok, msg, url = storage.save_image(_png(), "misc", "logo.png")
    assert ok, msg
    assert f"/storage/v1/object/public/uploads/misc/" in url


# ------------------------------------------------------- uploads: sensitive
def test_payment_proof_gets_signed_url_not_public(fake):
    ok, msg, url = storage.save_image(_png(), "proofs", "receipt.png")
    assert ok, msg
    assert f"/storage/v1/object/sign/uploads/proofs/" in url
    assert "token=" in url
    assert "/object/public/" not in url
    # the object itself is in the bucket (the signed url points at it)
    bucket_path = url.split("/uploads/", 1)[1].split("?", 1)[0]
    assert bucket_path in fake.objects["uploads"]


def test_pdf_receipt_gets_signed_url(fake):
    ok, msg, url = storage.save_image(_pdf(), "proofs", "receipt.pdf",
                                      allow_pdf=True, max_bytes=storage.MAX_RECEIPT_BYTES)
    assert ok, msg
    assert f"/storage/v1/object/sign/uploads/proofs/" in url
    path = url.split("/uploads/", 1)[1].split("?", 1)[0]
    assert fake.objects["uploads"][path][1] == "application/pdf"


def test_sensitive_is_by_folder_not_by_extension(fake):
    # same bytes, different folder: products is public, proofs is signed
    ok1, _, url_public = storage.save_image(_png(), "products", "a.png")
    ok2, _, url_signed = storage.save_image(_png(), "proofs", "b.png")
    assert ok1 and ok2
    assert "/object/public/" in url_public and "/object/sign/" not in url_public
    assert "/object/sign/" in url_signed and "/object/public/" not in url_signed


# ------------------------------------------------------- uploads: never lost
def test_bucket_down_keeps_the_receipt_on_disk(fake):
    fake.fail_uploads = True
    ok, msg, url = storage.save_image(_png(), "proofs", "receipt.png")
    assert ok, "a payment receipt must never be lost to an unreachable bucket"
    assert url.startswith("/uploads/proofs/")
    full = storage.resolve_local(url[len("/uploads/"):])
    assert full and os.path.isfile(full)
    with open(full, "rb") as fh:
        assert fh.read() == _png()


def test_bucket_down_product_photo_falls_back_to_disk(fake):
    fake.fail_uploads = True
    ok, msg, url = storage.save_image(_png(), "products", "photo.png")
    assert ok, msg
    assert url.startswith("/uploads/products/")
    assert storage.resolve_local(url[len("/uploads/"):])


def test_unconfigured_supabase_falls_back_to_local(monkeypatch, tmp_path):
    # UPLOAD_MODE=supabase but the env vars were never filled in
    monkeypatch.setattr(Config, "UPLOAD_MODE", "supabase")
    monkeypatch.setattr(Config, "SUPABASE_URL", "")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    ok, msg, url = storage.save_image(_png(), "proofs", "receipt.png")
    assert ok, msg
    assert url.startswith("/uploads/proofs/")
    assert storage.resolve_local(url[len("/uploads/"):])


def test_client_unavailable_falls_back_to_local(monkeypatch, tmp_path):
    # configured, but the supabase package / client cannot be created
    monkeypatch.setattr(Config, "UPLOAD_MODE", "supabase")
    monkeypatch.setattr(Config, "SUPABASE_URL", FAKE_ORIGIN)
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(supabase_store, "client", lambda: None)
    ok, msg, url = storage.save_image(_png(), "proofs", "receipt.png")
    assert ok, msg
    assert url.startswith("/uploads/proofs/")


def test_local_mode_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "UPLOAD_MODE", "local")
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    ok, msg, url = storage.save_image(_png(), "products", "photo.png")
    assert ok, msg
    assert url.startswith("/uploads/products/")
    assert storage.resolve_local(url[len("/uploads/"):])


def test_s3_mode_still_falls_back_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "UPLOAD_MODE", "s3")
    monkeypatch.setattr(Config, "S3_BUCKET", "")
    monkeypatch.setattr(Config, "S3_ACCESS_KEY", "")
    monkeypatch.setattr(Config, "S3_SECRET_KEY", "")
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    ok, msg, url = storage.save_image(_png(), "products", "photo.png")
    assert ok, msg
    assert url.startswith("/uploads/products/")


# ------------------------------------------------------------- deletion
def test_delete_removes_object_from_bucket(fake):
    ok, _, url = storage.save_image(_png(), "products", "photo.png")
    assert ok
    path = url.split("/uploads/", 1)[1]
    assert storage.delete_upload(url) is True
    assert path not in fake.objects.get("uploads", {})
    # deleting again: nothing left to delete, still a clean False
    assert storage.delete_upload(url) is False


def test_delete_signed_proof_url_removes_object(fake):
    ok, _, url = storage.save_image(_png(), "proofs", "receipt.png")
    assert ok
    assert storage.delete_upload(url) is True
    path = url.split("/uploads/", 1)[1].split("?", 1)[0]
    assert path not in fake.objects.get("uploads", {})


def test_delete_a_stale_signed_url_still_removes_object(fake):
    ok, _, url = storage.save_image(_png(), "proofs", "receipt.png")
    assert ok
    path = url.split("/uploads/", 1)[1].split("?", 1)[0]
    stale = f"{FAKE_ORIGIN}/storage/v1/object/sign/uploads/{path}?token=expired-token"
    assert storage.delete_upload(stale) is True
    assert path not in fake.objects.get("uploads", {})


def test_delete_ignores_foreign_urls(fake):
    assert storage.delete_upload("https://not-ours.example.com/file.jpg") is False
    assert storage.delete_upload("") is False


def test_local_files_still_deleted(monkeypatch, tmp_path):
    # mixed history: mode is supabase, but an old receipt lives on the disk
    monkeypatch.setattr(Config, "UPLOAD_MODE", "supabase")
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    key = "proofs/2026/01/old-local-receipt.png"
    full = os.path.join(storage.local_root(), key.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(_png())
    assert storage.delete_upload("/uploads/" + key) is True
    assert not os.path.isfile(full)


# --------------------------------------------------- key parsing / signing
def test_key_from_url_supabase_shapes():
    assert (storage._key_from_url(
        f"{FAKE_ORIGIN}/storage/v1/object/public/uploads/products/a/b.png")
        == "products/a/b.png")
    assert (storage._key_from_url(
        f"{FAKE_ORIGIN}/storage/v1/object/sign/uploads/proofs/a/b.png?token=x")
        == "proofs/a/b.png")
    # a non-default bucket name must parse too
    assert (storage._key_from_url(
        f"{FAKE_ORIGIN}/storage/v1/object/public/my-bucket/products/a/b.png")
        == "products/a/b.png")


def test_signed_url_for_refreshes_a_stored_proof_url(fake):
    ok, _, url = storage.save_image(_png(), "proofs", "receipt.png")
    assert ok
    refreshed = storage.signed_url_for(url)
    assert refreshed != url                    # a fresh token, not the stale one
    assert f"/storage/v1/object/sign/uploads/proofs/" in refreshed
    # the object path is the same - only the token moved
    assert url.split("/uploads/", 1)[1].split("?", 1)[0] == \
        refreshed.split("/uploads/", 1)[1].split("?", 1)[0]
    # a second call reuses the freshly minted url (cache) instead of
    # hitting the storage API again
    assert storage.signed_url_for(url) == refreshed


def test_signed_url_for_leaves_everything_else_alone(fake):
    ok, _, url = storage.save_image(_png(), "products", "photo.png")
    assert ok
    # public asset URL: untouched
    assert storage.signed_url_for(url) == url
    # local /uploads/ link: untouched
    assert storage.signed_url_for("/uploads/products/a/b.png") == "/uploads/products/a/b.png"
    # foreign url: untouched
    foreign = "https://elsewhere.example.com/storage/v1/object/public/uploads/proofs/x.png"
    assert storage.signed_url_for(foreign) == foreign
    assert storage.signed_url_for("") == ""


def test_signed_url_for_never_raises_when_client_is_down(fake, monkeypatch):
    url = (f"{FAKE_ORIGIN}/storage/v1/object/sign/uploads/proofs/a/b.png"
           f"?token=fake-token-0")

    def _boom():
        raise RuntimeError("client exploded")

    monkeypatch.setattr(supabase_store, "client", _boom)
    # best effort: the original url comes back, the receipts view still works
    assert storage.signed_url_for(url) == url


def test_admin_receipts_view_serves_fresh_signed_urls(client, fake):
    """The admin receipts table must not hand out the 7-day-old signed URL
    stored in the database - each file_url is refreshed on the way out."""
    login(client)
    execute("DELETE FROM payment_proofs")      # isolated: this test's row only
    ok, _, url = storage.save_image(_png(), "proofs", "receipt.png")
    assert ok
    stored_path = url.split("/uploads/", 1)[1].split("?", 1)[0]
    execute("INSERT INTO payment_proofs (order_id, name, phone, email, method, items, "
            "quantity, amount, note, file_url, file_name, file_size, mime, emailed, "
            "email_info, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("JA-TEST", "C", "0800", "c@x.com", "Bank transfer", "item", "1",
             "1000", "", url, "receipt.png", 10, "image/png", 1, "ok", "2026-09-04T00:00:00"))
    row = one("SELECT id FROM payment_proofs WHERE file_url=?", (url,))
    assert row, "test setup: the receipt row must exist"

    r = client.get("/api/admin/payment-proofs")
    assert r.status_code == 200, r.data
    proofs = r.get_json()["proofs"]
    assert len(proofs) == 1
    served = proofs[0]["file_url"]
    assert served != url                        # refreshed, not the stored one
    assert served.split("/uploads/", 1)[1].split("?", 1)[0] == stored_path
    assert f"/storage/v1/object/sign/uploads/proofs/" in served


# ------------------------------------------- variant stock survives a deploy
# The Stock panel writes to the SQLite-only variant_stock table, which a
# Render redeploy wipes. These tests cover the mirror (write) and the boot
# restore (read) that keep stock levels in Supabase.
def _growth_value(fake, key):
    for row in fake.tables.get("growth_settings", []):
        if row.get("key") == key:
            return row.get("value")
    return None


def test_variant_stock_mirror_roundtrip(fake):
    rows = [{"product_id": "wix-001", "variant_key": "Red", "variant_label": "Red",
             "qty": 3, "low_threshold": 2, "updated_at": "2026-09-04T00:00:00"}]
    assert supabase_store.save_variant_stock(rows) is True
    assert supabase_store.load_variant_stock() == rows


def test_variant_stock_mirror_noop_without_supabase(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "SUPABASE_URL", "")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.setattr(supabase_store, "client", lambda: None)
    assert supabase_store.save_variant_stock([{"a": 1}]) is False
    assert supabase_store.load_variant_stock() is None


def test_admin_stock_set_mirrors_to_supabase(client, fake):
    """Changing a stock level in the Stock panel must land in Supabase,
    because the SQLite row alone would be wiped on the next deploy."""
    login(client)
    execute("DELETE FROM variant_stock")
    tok = client.get("/api/config").get_json()["csrf"]
    r = client.put("/api/admin/stock",
                   json={"productId": "wix-001", "variant": "Red", "label": "Red",
                         "qty": 3, "lowThreshold": 2},
                   headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    raw = _growth_value(fake, supabase_store.VARIANT_STOCK_KEY)
    assert raw is not None, "the stock table was not mirrored to growth_settings"
    mirrored = json.loads(raw)
    assert any(m["product_id"] == "wix-001" and m["variant_key"] == "Red"
               and m["qty"] == 3 for m in mirrored)


def test_boot_restores_variant_stock_from_supabase(monkeypatch, tmp_path):
    """A fresh boot on a wiped disk pulls the stock levels back from
    Supabase before the first request is served."""
    fake = FakeSupabaseClient()
    fake.tables["growth_settings"] = [{
        "key": supabase_store.VARIANT_STOCK_KEY,
        "value": json.dumps([
            {"product_id": "wix-001", "variant_key": "Red", "variant_label": "Red",
             "qty": 3, "low_threshold": 2, "updated_at": "2026-09-04T00:00:00"},
            {"product_id": "wix-002", "variant_key": "__default__", "variant_label": None,
             "qty": 11, "low_threshold": 5, "updated_at": "2026-09-04T00:00:00"},
        ]),
    }]
    monkeypatch.setattr(Config, "UPLOAD_MODE", "supabase")
    monkeypatch.setattr(Config, "SUPABASE_URL", FAKE_ORIGIN)
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    monkeypatch.setattr(supabase_store, "client", lambda: fake)

    execute("DELETE FROM variant_stock")        # simulate the wiped disk
    appmod.create_app()                         # runs the boot restore

    rows = { (r["product_id"], r["variant_key"]): r["qty"]
             for r in query("SELECT * FROM variant_stock") }
    assert rows == {("wix-001", "Red"): 3, ("wix-002", "__default__"): 11}
    execute("DELETE FROM variant_stock")        # leave the shared test DB clean
