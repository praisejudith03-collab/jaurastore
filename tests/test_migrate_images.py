"""Offline tests for migrate_images.py.

The one-shot that uploads the repository's committed product photos into the
public Supabase `uploads` bucket and points products.image_url at the complete
HTTPS public URL. The contract under test is the safety contract, because a
bug here would touch production rows:

  * --dry-run performs NO upload and NO database write,
  * the only columns ever written are image_url + updated_at, matched by id
    (never INSERT, never DELETE, never a renamed id, never price/stock),
  * Storage keys are deterministic, so a second run reuses instead of
    duplicating,
  * image_url is written only after the object was verified in the bucket,
  * a photo whose filename does not belong to the product is skipped instead
    of being uploaded to the wrong id,
  * a catalogue path escaping images/ is refused,
  * the service-role key never reaches the report.

Run with:  python3 -m pytest tests/test_migrate_images.py -q
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

import migrate_images as mi  # noqa: E402

SUPA_URL = "https://projref.supabase.co"
JPEG = b"\xff\xd8\xff\xe0" + b"jaura" * 40


# ------------------------------------------------------------------ fakes
class FakeStorageFile:
    def __init__(self, log, bucket, objects):
        self.log, self.bucket, self.objects = log, bucket, objects

    def info(self, key):
        if key not in self.objects[self.bucket]:
            raise RuntimeError("Object not found")
        return {"name": key, "metadata": {"size": len(self.objects[self.bucket][key])}}

    def list(self, prefix, options=None):
        pre = prefix.rstrip("/") + "/"
        return [{"name": k.split("/")[-1]}
                for k in self.objects[self.bucket] if k.startswith(pre)]

    def upload(self, key, data, options=None):
        if self.log.get("fail_upload"):
            raise RuntimeError("boom")
        self.objects[self.bucket][key] = data
        self.log["uploads"].append((self.bucket, key, options))
        return {"Key": key}


class FakeStorage:
    def __init__(self, log, objects):
        self.log, self.objects = log, objects

    def from_(self, bucket):
        return FakeStorageFile(self.log, bucket, self.objects)


class FakeQuery:
    def __init__(self, log, table, rows, kind, payload=None):
        self.log, self.table, self.rows = log, table, rows
        self.kind, self.payload = kind, payload
        self._filters = {}

    def select(self, cols, count=None):
        self.log["selects"].append((self.table, cols))
        return self

    def order(self, col):
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def execute(self):
        if self.kind == "select":
            lo, hi = getattr(self, "_range", (0, 10 ** 9))
            page = self.rows[lo:hi + 1]
            if self._filters.get("id"):
                page = [r for r in self.rows if r["id"] == self._filters["id"]]
            return type("R", (), {"data": page, "count": len(self.rows)})()
        if self.kind == "update":
            self.log["updates"].append((self.table, dict(self._filters),
                                        dict(self.payload)))
            for r in self.rows:
                if all(r.get(k) == v for k, v in self._filters.items()):
                    r.update(self.payload)
            return type("R", (), {"data": [dict(r) for r in self.rows
                                          if all(r.get(k) == v for k,
                                                 v in self._filters.items())]})()
        raise AssertionError("unexpected query kind")


class FakeTable:
    def __init__(self, log, name, rows):
        self.log, self.name, self.rows = log, name, rows

    def select(self, cols, count=None):
        return FakeQuery(self.log, self.name, self.rows, "select").select(cols)

    def update(self, payload):
        self.log.setdefault("update_payload_keys", set()).update(payload.keys())
        return FakeQuery(self.log, self.name, self.rows, "update", payload)

    def insert(self, payload):            # must never be called
        self.log["inserts"].append((self.name, payload))
        raise AssertionError("migrate_images must never INSERT")

    def upsert(self, payload):            # must never be called
        self.log["inserts"].append((self.name, payload))
        raise AssertionError("migrate_images must never UPSERT")

    def delete(self):                     # must never be called
        self.log["deletes"].append(self.name)
        raise AssertionError("migrate_images must never DELETE")


class FakeClient:
    def __init__(self, rows, categories=None):
        self.log = {"uploads": [], "updates": [], "selects": [], "inserts": [],
                    "deletes": [], "fail_upload": False,
                    "update_payload_keys": set()}
        self.objects = {"uploads": {}}
        self.rows = rows
        self.categories = categories or []

    def table(self, name):
        return FakeTable(self.log, name,
                         self.rows if name == "products" else self.categories)

    @property
    def storage(self):
        return FakeStorage(self.log, self.objects)


# --------------------------------------------------------------- fixtures
@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A throwaway repo root with a real image and a real catalogue."""
    (tmp_path / "images" / "products").mkdir(parents=True)
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "images" / "products" / "kettle.jpg").write_bytes(JPEG)
    (tmp_path / "images" / "products" / "_placeholder.jpg").write_bytes(JPEG)
    seed = [
        {"id": "wix-001", "slug": "kettle", "name": "Kettle", "category": "household",
         "priceNgn": 10000, "priceCfa": 7500, "stock": 5,
         "image": "images/products/kettle.jpg"},
        {"id": "wix-002", "slug": "nophoto", "name": "No photo", "category": "bags",
         "priceNgn": 100, "priceCfa": 50, "stock": 1,
         "image": "images/products/_placeholder.jpg"},
    ]
    (tmp_path / "data" / "seed.json").write_text(json.dumps(seed))
    (tmp_path / "data" / "catalog.json").write_text(json.dumps({"products": [],
                                                                "deleted": []}))
    monkeypatch.setattr(mi, "ROOT", str(tmp_path))
    return tmp_path


def _rows():
    return [
        {"id": "wix-001", "slug": "kettle", "name": "Kettle", "online": True,
         "priceNgn": 10000, "priceCfa": 7500, "stock_quantity": 5,
         "image": "images/products/kettle.jpg", "image_url": None,
         "source": "admin"},
        {"id": "wix-002", "slug": "nophoto", "name": "No photo", "online": True,
         "priceNgn": 100, "priceCfa": 50, "stock_quantity": 1,
         "image": "images/products/_placeholder.jpg", "image_url": None,
         "source": "admin"},
    ]


# ------------------------------------------------------------------ tests
def test_dry_run_uploads_nothing_and_writes_no_row(sandbox, monkeypatch):
    """The whole point of --dry-run: read and report, touch nothing."""
    client = FakeClient(_rows())
    monkeypatch.setattr(mi, "_client", lambda: (client, SUPA_URL))
    code = mi.main(["--dry-run", "--source", "supabase"])
    assert code == 0
    assert client.log["uploads"] == []
    assert client.log["updates"] == []
    assert client.objects["uploads"] == {}
    assert client.rows[0]["image_url"] is None


def test_dry_run_report_lists_every_required_field(sandbox, monkeypatch, tmp_path):
    client = FakeClient(_rows())
    monkeypatch.setattr(mi, "_client", lambda: (client, SUPA_URL))
    out = tmp_path / "dry.json"
    mi.main(["--dry-run", "--source", "supabase", "--report", str(out)])
    rep = json.loads(out.read_text())
    p = rep["products"]
    for key in ("products_examined", "products_matched", "products_skipped",
                "images_discovered", "images_missing", "images_already_uploaded",
                "images_to_upload", "duplicate_products", "missing_prices",
                "missing_stock", "legacy_id_references",
                "proposed_database_updates", "proposed_storage_paths"):
        assert key in p, f"report is missing {key}"
    assert p["products_examined"] == 2
    assert p["products_matched"] == 1          # only wix-001 has a real photo
    assert p["images_to_upload"] == 1
    assert p["legacy_id_references"] == ["wix-001", "wix-002"]
    assert rep["dry_run"] is True
    # a complete HTTPS public URL in the exact required shape
    url = p["proposed_storage_paths"][0]["public_url"]
    assert url.startswith("https://projref.supabase.co/storage/v1/object/public/uploads/products/wix-001/")


def test_report_never_contains_the_service_role_key(sandbox, monkeypatch, tmp_path):
    secret = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.SUPERSECRETVALUE"
    client = FakeClient(_rows())
    monkeypatch.setattr(mi, "_client", lambda: (client, SUPA_URL))
    out = tmp_path / "dry.json"
    mi.main(["--dry-run", "--source", "supabase", "--report", str(out)])
    assert secret not in out.read_text()

    # the signature segment of a JWT must be redacted too, not just the header
    assert "SUPERSECRET" not in mi._mask(f"upload failed with {secret}")
    # and a configured key is redacted verbatim wherever it appears
    from config import Config
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", secret, raising=False)
    assert secret not in mi._mask(f"Authorization: Bearer {secret}")


def test_write_report_masks_the_serialised_document(sandbox, monkeypatch, tmp_path):
    secret = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.SUPERSECRETVALUE"
    out = tmp_path / "rep.json"
    mi.write_report({"error": f"db said {secret}"}, str(out))
    body = out.read_text()
    assert "SUPERSECRET" not in body
    assert json.loads(body)["error"].startswith("db said eyJ…REDACTED")


def test_real_run_writes_only_image_url_and_matches_by_id(sandbox, monkeypatch):
    client = FakeClient(_rows())
    monkeypatch.setattr(mi, "_client", lambda: (client, SUPA_URL))
    plan = mi.plan_products(client.rows, "uploads", SUPA_URL, client=client)
    result = mi.execute_product_plan(client, plan, "uploads", SUPA_URL)

    assert result["uploaded"] == 1
    assert result["rows_updated"] == 1
    assert result["verified"] == 1
    assert result["failed"] == []
    # exactly one UPDATE, on products, matched by the untouched id
    assert len(client.log["updates"]) == 1
    table, filters, payload = client.log["updates"][0]
    assert table == "products"
    assert filters == {"id": "wix-001"}
    assert set(payload) == {"image_url", "updated_at"}
    assert payload["image_url"].startswith("https://projref.supabase.co/")
    # no INSERT / DELETE was even attempted (the fakes raise on them)
    assert client.log["inserts"] == [] and client.log["deletes"] == []


def test_prices_and_stock_are_left_exactly_alone(sandbox, monkeypatch):
    before = _rows()
    client = FakeClient([dict(r) for r in before])
    monkeypatch.setattr(mi, "_client", lambda: (client, SUPA_URL))
    plan = mi.plan_products(client.rows, "uploads", SUPA_URL, client=client)
    mi.execute_product_plan(client, plan, "uploads", SUPA_URL)
    after = client.rows[0]
    for col in ("priceNgn", "priceCfa", "stock_quantity", "id", "name", "online"):
        assert after[col] == before[0][col], f"{col} drifted"
    # the plan never even carried a price/stock column into a patch
    for upd in plan["proposed_database_updates"]:
        assert set(upd["set"]) == {"image_url", "updated_at"}
        assert "priceNgn" in upd["columns_never_written"]


def test_second_run_is_idempotent_and_reuploads_nothing(sandbox, monkeypatch):
    client = FakeClient(_rows())
    monkeypatch.setattr(mi, "_client", lambda: (client, SUPA_URL))
    plan = mi.plan_products(client.rows, "uploads", SUPA_URL, client=client)
    mi.execute_product_plan(client, plan, "uploads", SUPA_URL)
    first_uploads = len(client.log["uploads"])
    assert first_uploads == 1

    # same rows, same client: the URL is now current and the object exists
    plan2 = mi.plan_products(client.rows, "uploads", SUPA_URL, client=client)
    assert plan2["products_matched"] == 0
    assert plan2["images_to_upload"] == 0
    assert plan2["images_already_uploaded"] == 1
    res2 = mi.execute_product_plan(client, plan2, "uploads", SUPA_URL)
    assert res2["uploaded"] == 0 and res2["rows_updated"] == 0
    assert len(client.log["uploads"]) == first_uploads   # no second object


def test_failed_upload_never_touches_the_database(sandbox, monkeypatch):
    """image_url is written only after a verified object."""
    client = FakeClient(_rows())
    client.log["fail_upload"] = True
    monkeypatch.setattr(mi, "_client", lambda: (client, SUPA_URL))
    plan = mi.plan_products(client.rows, "uploads", SUPA_URL, client=client)
    result = mi.execute_product_plan(client, plan, "uploads", SUPA_URL)
    assert result["uploaded"] == 0 and result["rows_updated"] == 0
    assert client.log["updates"] == []
    assert client.rows[0]["image_url"] is None
    assert result["failed"] and "upload failed" in result["failed"][0]["reason"]


def test_a_photo_that_does_not_belong_to_the_product_is_skipped(sandbox, monkeypatch):
    """stem != slug => wrong-product risk => refuse, do not upload."""
    rows = _rows()
    rows[0]["slug"] = "completely-different-product"
    client = FakeClient(rows)
    plan = mi.plan_products(rows, "uploads", SUPA_URL, client=client)
    assert plan["products_matched"] == 0
    assert plan["mapping_warnings"]
    assert plan["mapping_warnings"][0]["reason"] == "stem_mismatch"
    assert plan["proposed_storage_paths"] == []
    assert plan["proposed_database_updates"] == []


def test_a_path_escaping_the_images_tree_is_refused(sandbox):
    row = {"id": "wix-900", "slug": "etc", "image": "../../etc/passwd"}
    assert mi._inside_repo_images("../../etc/passwd") == ""
    local, rel, status, _ = mi.resolve_source_image(row)
    assert status == "unsafe" and local == ""


def test_blank_and_duplicate_ids_are_blocked_not_uploaded(sandbox, monkeypatch):
    rows = _rows() + [{"id": "", "slug": "x", "name": "no id"},
                      dict(_rows()[0]), dict(_rows()[0])]
    plan = mi.plan_products(rows, "uploads", SUPA_URL, client=None)
    assert plan["blank_ids"] and plan["duplicate_products"] == ["wix-001"]
    # duplicates are collapsed to a single proposal, never a second row
    assert len(plan["proposed_storage_paths"]) == 1


def test_tombstone_rows_are_skipped_and_never_resurrected(sandbox):
    rows = _rows()
    rows[0]["source"] = "deleted"
    plan = mi.plan_products(rows, "uploads", SUPA_URL, client=None)
    assert plan["tombstone_rows_skipped"] == ["wix-001"]
    assert plan["proposed_storage_paths"] == []


def test_storage_key_is_deterministic_and_content_addressed(sandbox):
    local = str(sandbox / "images" / "products" / "kettle.jpg")
    digest = mi._sha256(local)
    k1 = mi.storage_key_for("wix-001", local, digest)
    k2 = mi.storage_key_for("wix-001", local, digest)
    assert k1 == k2
    assert k1.startswith("products/wix-001/")
    assert digest[:16] in k1 and k1.endswith(".jpg")
    # a different product id can never collide onto the same key
    assert mi.storage_key_for("wix-002", local, digest) != k1


def test_public_url_shape_matches_the_required_format():
    url = mi._public_url("https://projref.supabase.co/", "uploads", "products/a/b.jpg")
    assert url == "https://projref.supabase.co/storage/v1/object/public/uploads/products/a/b.jpg"
    assert mi._is_public_supabase_url(url)
    assert not mi._is_public_supabase_url("images/products/b.jpg")
    assert not mi._is_public_supabase_url("http://projref.supabase.co/x")


def test_no_base_url_never_produces_a_relative_image_url(sandbox):
    """Without SUPABASE_URL the plan must say 'template', not emit a half URL."""
    assert mi._public_url("", "uploads", "products/a/b.jpg") == ""
    rows = _rows()
    plan = mi.plan_products(rows, "uploads", "", client=None)
    url = plan["proposed_storage_paths"][0]["public_url"]
    assert url.startswith("https://<SUPABASE_URL>/storage/v1/object/public/uploads/")
    assert plan["proposed_storage_paths"][0]["url_is_template"] is True
    assert not mi._is_public_supabase_url(url)


def test_execute_refuses_to_save_a_non_https_url(sandbox, monkeypatch):
    client = FakeClient(_rows())
    plan = mi.plan_products(_rows(), "uploads", "", client=client)  # templates
    result = mi.execute_product_plan(client, plan, "uploads", "")
    assert result["rows_updated"] == 0 and result["uploaded"] == 0
    assert "not a complete HTTPS public URL" in result["failed"][0]["reason"]
    assert client.log["updates"] == []
    assert client.objects["uploads"] == {}


def test_offline_dry_run_reports_itself_as_not_applicable(sandbox, monkeypatch, tmp_path):
    monkeypatch.setattr(mi, "_client", lambda: (None, ""))
    out = tmp_path / "dry.json"
    code = mi.main(["--dry-run", "--source", "local", "--report", str(out)])
    assert code == 0                      # an offline plan is not an error
    rep = json.loads(out.read_text())
    assert rep["supabase_configured"] is False
    assert rep["environment_blockers"]
    assert rep["safe_to_apply"] is False


def test_writable_column_whitelist_excludes_price_and_stock():
    assert set(mi.WRITABLE_COLUMNS) == {"image_url", "updated_at"}
    for forbidden in ("id", "priceNgn", "priceCfa", "compareNgn", "compareCfa",
                      "stock_quantity", "stock", "name", "category", "online"):
        assert forbidden not in mi.WRITABLE_COLUMNS


def test_dry_run_report_writes_no_file_outside_the_report_path(sandbox, monkeypatch,
                                                              tmp_path):
    """No local image is deleted or moved by a dry run."""
    client = FakeClient(_rows())
    monkeypatch.setattr(mi, "_client", lambda: (client, SUPA_URL))
    before = sorted(os.listdir(sandbox / "images" / "products"))
    out = tmp_path / "dry.json"
    mi.main(["--dry-run", "--source", "supabase", "--report", str(out)])
    assert sorted(os.listdir(sandbox / "images" / "products")) == before
