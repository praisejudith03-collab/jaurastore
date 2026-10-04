"""Thumbnail-first uploads: the .400w.webp companion and its backfill.

The storefront's phone speed comes from three small objects instead of one big
one: the catalogue photos ship a 400px WebP sibling in the repo, an UPLOADED
photo gets the same sibling written into the bucket by the background queue
(storage.save_image -> enqueue_thumbnail), and tools/backfill_thumbs.py gives
that sibling to every photo uploaded before the queue existed.

The rules pinned here are the ones that keep this safe:

* a companion is built off the request path and can never fail or slow an
  upload (a thumbnail is an optimisation, not a condition);
* payment proofs never get a copy (they are private evidence);
* the companion key is derived from the object key, so it always lands next to
  the photo it belongs to - the /uploads/<key> route, the bucket and any folder
  delete cover both objects;
* the backfill tool is dry-run by default, idempotent, and never rewrites or
  deletes an original.
"""
import importlib.util
import io
import os

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import storage  # noqa: E402
import task_queue  # noqa: E402


@pytest.fixture(autouse=True)
def quiet_queue():
    """Never let the shared worker thread race a test's own drain."""
    task_queue.reset()
    task_queue.set_manual(True)
    yield
    task_queue.set_manual(False)
    task_queue.reset()


@pytest.fixture()
def upload_dir(tmp_path, monkeypatch):
    """A scratch upload root: the real data/uploads is never touched."""
    monkeypatch.setattr(storage.Config, "UPLOAD_DIR", str(tmp_path))
    return tmp_path


def photo_bytes(size=(1200, 900), color=(200, 40, 90)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def load_tool():
    path = os.path.join(ROOT, "tools", "backfill_thumbs.py")
    spec = importlib.util.spec_from_file_location("backfill_thumbs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------- key rules
def test_thumb_key_maps_images_only():
    assert storage.thumb_key("products/2026/10/abc.jpg") == "products/2026/10/abc.400w.webp"
    assert storage.thumb_key("products/2026/10/abc.JPEG") == "products/2026/10/abc.400w.webp"
    assert storage.thumb_key("categories/2026/09/logo.png") == "categories/2026/09/logo.400w.webp"
    # A companion has no companion, a document has none, a traversal has none.
    assert storage.thumb_key("products/2026/10/abc.400w.webp") == ""
    assert storage.thumb_key("receipts/2026/10/order.pdf") == ""
    assert storage.thumb_key("../etc/passwd.jpg") == ""
    assert storage.thumb_key("products/no-extension") == ""
    assert storage.thumb_key("") == ""
    assert storage.thumb_key(None) == ""


def test_the_companion_keeps_the_original_in_the_same_folder():
    key = "products/2026/10/0123456789abcdef-deadbeef.jpg"
    companion = storage.thumb_key(key)
    assert companion.rsplit("/", 1)[0] == key.rsplit("/", 1)[0], \
        "a companion in another folder would survive a product-folder delete"
    assert companion.endswith(storage.THUMB_SUFFIX)


# ------------------------------------------------------------- the bytes
def test_make_thumb_bytes_is_a_small_webp():
    data = photo_bytes((1600, 1200))
    thumb = storage.make_thumb_bytes(data)
    assert thumb, "a normal JPEG must produce a companion"
    assert thumb[:4] == b"RIFF" and thumb[8:12] == b"WEBP", "companion must be WebP"
    assert len(thumb) < len(data), "a companion bigger than the original is pointless"
    with Image.open(io.BytesIO(thumb)) as img:
        assert max(img.size) <= storage.THUMB_LONGEST_EDGE


def test_make_thumb_bytes_never_upscales_or_raises():
    small = photo_bytes((80, 60))
    thumb = storage.make_thumb_bytes(small)
    with Image.open(io.BytesIO(thumb)) as img:
        assert img.size == (80, 60)
    assert storage.make_thumb_bytes(b"not an image") == b""
    assert storage.make_thumb_bytes(b"") == b""
    assert storage.make_thumb_bytes(b"x" * (storage.THUMB_MAX_SOURCE_BYTES + 1)) == b""


def test_store_thumbnail_bytes_writes_beside_the_object(upload_dir):
    key = "products/2026/10/abc.jpg"
    url = storage.store_thumbnail(photo_bytes(), key)
    assert url == "/uploads/" + storage.thumb_key(key)
    written = upload_dir / storage.thumb_key(key)
    assert written.is_file() and written.stat().st_size > 0


def test_a_sensitive_folder_never_gets_a_companion(upload_dir):
    assert storage.store_thumbnail_bytes(b"thumb", "proofs/2026/10/pay.jpg") == ""
    assert not list(upload_dir.rglob("*.webp"))


def test_a_bad_companion_write_returns_empty_instead_of_raising(upload_dir, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bucket down")
    monkeypatch.setattr(storage, "_save_supabase", boom)
    monkeypatch.setattr(storage.Config, "UPLOAD_MODE", "supabase")
    assert storage.store_thumbnail_bytes(b"thumb", "products/2026/10/abc.jpg") == ""


# --------------------------------------------------------- the upload path
def test_an_upload_queues_its_companion_and_the_worker_writes_it(upload_dir):
    ok, msg, url = storage.save_image(photo_bytes(), folder="products",
                                      filename="phone.jpg")
    assert ok, msg
    key = storage._key_from_url(url)
    assert key, f"unexpected url {url}"
    jobs = [j for j in task_queue.snapshot(20) if j["kind"] == "thumb.build"]
    assert len(jobs) == 1, "the upload must queue exactly one companion build"
    assert not (upload_dir / storage.thumb_key(key)).exists(), \
        "the companion is built off the request path, not during the upload"

    task_queue.wait_idle(timeout=5)
    assert (upload_dir / storage.thumb_key(key)).is_file(), \
        "the worker never wrote the companion"
    assert task_queue.stats()["failed"] == 0


def test_a_proof_upload_never_queues_a_companion(upload_dir):
    ok, msg, url = storage.save_image(photo_bytes(), folder="proofs",
                                      filename="pay.jpg", allow_pdf=True)
    assert ok, msg
    assert "proofs/" in url
    assert [j for j in task_queue.snapshot(20) if j["kind"] == "thumb.build"] == []
    assert not list(upload_dir.rglob("*.webp"))


def test_a_broken_queue_never_breaks_the_upload(upload_dir, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no queue")
    monkeypatch.setattr(storage, "enqueue_thumbnail", boom)
    ok, msg, url = storage.save_image(photo_bytes(), folder="products",
                                      filename="phone.jpg")
    assert ok, msg
    assert url


def test_the_job_is_deduped_while_it_waits(upload_dir):
    first = storage.enqueue_thumbnail("/uploads/products/2026/10/abc.jpg")
    second = storage.enqueue_thumbnail("/uploads/products/2026/10/abc.jpg")
    assert first and first == second, "an impatient retry must not queue twice"


# ------------------------------------------------------------- the backfill
class _FakeBucket:
    def __init__(self, objects):
        self.objects = dict(objects)          # key -> bytes
        self.writes = {}
        self.lists = []

    def list(self, prefix="", options=None):
        self.lists.append(prefix)
        rows = []
        seen = set()
        prefix = prefix.strip("/")
        for key in sorted(self.objects):
            if prefix and not key.startswith(prefix + "/"):
                continue
            rest = key[len(prefix) + 1:] if prefix else key
            head = rest.split("/", 1)[0]
            if head in seen:
                continue
            seen.add(head)
            if "/" in rest:
                rows.append({"name": head, "id": None, "metadata": None})
            else:
                rows.append({"name": head, "id": "obj", "metadata": {}})
        return rows

    def download(self, key):
        return self.objects.get(key)


class _FakeStorage:
    def __init__(self, bucket):
        self.bucket = bucket

    def from_(self, name):
        return self.bucket


class _FakeClient:
    def __init__(self, bucket):
        self.storage = _FakeStorage(bucket)


@pytest.fixture()
def backfill(upload_dir, monkeypatch):
    """The tool, a fake bucket, and a local upload root it can write into."""
    tool = load_tool()
    bucket = _FakeBucket({})
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    # Config snapshots the environment at import time, so the attributes are
    # what storage.fetch_object actually reads.
    monkeypatch.setattr(storage.Config, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(storage.Config, "SUPABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setattr(tool.supabase_store, "client", lambda: _FakeClient(bucket))
    # The tool writes through storage, and reads through storage.fetch_object,
    # which prefers the local root first: keep both on the scratch directory.
    monkeypatch.setattr(storage.Config, "UPLOAD_MODE", "local")
    return tool, bucket, upload_dir


def test_the_backfill_is_a_dry_run_until_apply(backfill):
    tool, bucket, upload_dir = backfill
    bucket.objects["products/2026/10/a.jpg"] = photo_bytes()
    assert tool.main([]) == 0
    assert not list(upload_dir.rglob("*.webp")), "the default run wrote something"

    assert tool.main(["--apply"]) == 0
    assert (upload_dir / "products/2026/10/a.400w.webp").is_file()


def test_the_backfill_skips_an_existing_companion_and_proofs(backfill):
    tool, bucket, upload_dir = backfill
    bucket.objects["products/2026/10/a.jpg"] = photo_bytes()
    bucket.objects["products/2026/10/a.400w.webp"] = b"already there"
    bucket.objects["proofs/2026/10/pay.jpg"] = photo_bytes()
    assert tool.main(["--apply"]) == 0
    assert not (upload_dir / "products/2026/10/a.400w.webp").exists(), \
        "a photo that already has a companion was rewritten"
    assert not list((upload_dir / "proofs").rglob("*.webp")), \
        "a payment proof was copied"


def test_the_backfill_limit_stops_early(backfill):
    tool, bucket, upload_dir = backfill
    for name in ("a", "b", "c"):
        bucket.objects[f"products/2026/10/{name}.jpg"] = photo_bytes()
    assert tool.main(["--apply", "--limit", "1"]) == 0
    assert len(list(upload_dir.rglob("*.webp"))) == 1


def test_the_backfill_skips_objects_it_cannot_read(backfill):
    tool, bucket, upload_dir = backfill
    bucket.objects["products/2026/10/broken.jpg"] = b"not an image"
    assert tool.main(["--apply"]) == 0
    assert not list(upload_dir.rglob("*.webp"))


def test_the_backfill_requires_credentials(monkeypatch, capsys):
    tool = load_tool()
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert tool.main([]) == 1
    assert "missing environment variable" in capsys.readouterr().out
