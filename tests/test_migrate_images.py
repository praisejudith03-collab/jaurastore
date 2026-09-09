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


# ---------------------------------------------------------------------------
# Blocker 8 - the report must state "Existing HTTPS URLs" explicitly.
# "N already uploaded" is not the same number: that counts objects found in
# the bucket this run, while this tally is about rows that ALREADY point at a
# complete HTTPS URL and therefore must be left alone.
# ---------------------------------------------------------------------------

_GOOD = "https://abcxyz.supabase.co/storage/v1/object/public/uploads/products/wix-001/deadbeef-a.jpg"


@pytest.mark.parametrize("url,want_kind,want_host", [
    (_GOOD, "already_public_supabase_uploads", "abcxyz.supabase.co"),
    ("https://cdn.jaurastore.com/x.jpg", "other_https_host", "cdn.jaurastore.com"),
    ("https://static.wixstatic.com/media/a.jpg", "other_https_host", "static.wixstatic.com"),
    ("http://insecure.example.com/a.jpg", "insecure_http", "insecure.example.com"),
    ("https://<SUPABASE_URL>/storage/v1/object/public/uploads/a.jpg",
     "unresolved_template", ""),
    ("images/a.jpg", "relative_or_other", ""),
    ("", "blank", ""),
    (None, "blank", ""),
])
def test_classify_image_url(url, want_kind, want_host):
    kind, host = mi.classify_image_url(url)
    assert kind == want_kind
    assert host == want_host


def test_a_url_in_another_bucket_is_not_counted_as_ours():
    """Only the `uploads` bucket counts as already-migrated."""
    kind, host = mi.classify_image_url(
        "https://abcxyz.supabase.co/storage/v1/object/public/receipts/a.jpg")
    assert kind == "other_https_host"
    assert host == "abcxyz.supabase.co"


def test_the_tally_counts_every_examined_row_not_just_uploads():
    products = [
        {"id": "wix-001", "name": "A", "image": "images/a.jpg",
         "priceNgn": 10, "stock": 1, "image_url": _GOOD},
        {"id": "wix-002", "name": "B", "image": "", "priceNgn": 10,
         "stock": 1, "image_url": "https://cdn.example.com/b.jpg"},
        {"id": "wix-003", "name": "C", "image": "", "priceNgn": 10,
         "stock": 1, "image_url": ""},
    ]
    rep = mi.plan_products(products, "uploads", "https://abcxyz.supabase.co")
    t = rep["existing_https_urls"]
    assert t["count"] == 2, t
    assert t["already_public_supabase_uploads"] == 1
    assert t["other_https_host"] == 1
    assert t["blank"] == 1
    assert t["host_breakdown"] == {"abcxyz.supabase.co": 1,
                                   "cdn.example.com": 1}
    # a row with a good URL is skipped, never re-uploaded
    reasons = {d["id"]: d["reason"] for d in rep["skipped_detail"]}
    assert reasons.get("wix-001") == "url_already_current"
    assert rep["images_to_upload"] == 0


def test_an_insecure_url_is_a_blocker():
    products = [{"id": "wix-001", "name": "A", "image": "", "priceNgn": 10,
                 "stock": 1, "image_url": "http://insecure.example.com/a.jpg"}]
    rep = mi.plan_products(products, "uploads", "https://abcxyz.supabase.co")
    assert rep["existing_https_urls"]["insecure_http"] == 1


def test_the_report_serialises_the_tally(tmp_path):
    """write_report must carry the section through to disk, unmasked."""
    products = [{"id": "wix-001", "name": "A", "image": "", "priceNgn": 10,
                 "stock": 1, "image_url": _GOOD}]
    rep = mi.plan_products(products, "uploads", "https://abcxyz.supabase.co")
    path = str(tmp_path / "report.json")
    assert mi.write_report({"products": rep, "dry_run": True}, path) is True
    out = open(path, encoding="utf-8").read()
    assert "existing_https_urls" in out
    assert "already_public_supabase_uploads" in out
    assert _GOOD in out, "the public URL must survive masking"


# ---------------------------------------------------------------------------
# The live-product policy. "Do not automatically publish all 258 products" is
# only enforceable if the intended live set is written down per id, so the
# report classifies every row and a reviewer reads a list, not a count.
# ---------------------------------------------------------------------------

def _row(pid, **kw):
    base = {"id": pid, "name": "Thing " + pid, "image": "images/products/x.jpg",
            "priceNgn": 1000, "priceCfa": 500, "stock": 5}
    base.update(kw)
    return base


def test_a_complete_row_is_live(monkeypatch):
    monkeypatch.setattr(mi, "resolve_source_image",
                        lambda p: ("/tmp/x.jpg", "images/products/x.jpg",
                                   "found", ""))
    online, reason, _ = mi.classify_live_intent(_row("wix-900"))
    assert online is True and reason == "live"


def test_a_placeholder_row_is_not_live(monkeypatch):
    monkeypatch.setattr(mi, "resolve_source_image",
                        lambda p: ("", "images/products/_placeholder.jpg",
                                   "placeholder", "placeholder"))
    online, reason, _ = mi.classify_live_intent(_row("wix-901"))
    assert online is False and reason == "placeholder_only"


def test_a_zero_price_blocks_a_row_even_with_a_photo(monkeypatch):
    """A 0 price is not a missing value - it would put a free item on sale."""
    monkeypatch.setattr(mi, "resolve_source_image",
                        lambda p: ("/tmp/x.jpg", "images/products/x.jpg",
                                   "found", ""))
    online, reason, detail = mi.classify_live_intent(_row("wix-902", priceNgn=0))
    assert online is False and reason == "needs_review"
    assert "priceNgn" in detail


def test_zero_stock_blocks_a_row(monkeypatch):
    monkeypatch.setattr(mi, "resolve_source_image",
                        lambda p: ("/tmp/x.jpg", "images/products/x.jpg",
                                   "found", ""))
    online, reason, detail = mi.classify_live_intent(
        _row("wix-903", stock=0, stock_quantity=0))
    assert online is False and reason == "needs_review"
    assert "stock" in detail


def test_a_missing_price_blocks_a_row(monkeypatch):
    monkeypatch.setattr(mi, "resolve_source_image",
                        lambda p: ("/tmp/x.jpg", "images/products/x.jpg",
                                   "found", ""))
    online, reason, _ = mi.classify_live_intent(_row("wix-904", priceCfa=None))
    assert online is False and reason == "needs_review"


@pytest.mark.parametrize("pid", ["jau-stock-1", "jau-mirror-2", "jau-unit-3",
                                 "jau-sync-4", "jau-opt-5"])
def test_pytest_fixtures_are_never_live(monkeypatch, pid):
    """These ids were written into the tracked catalogue by the test suite.
    Publishing one to production would be a real incident."""
    monkeypatch.setattr(mi, "resolve_source_image",
                        lambda p: ("/tmp/x.jpg", "images/products/x.jpg",
                                   "found", ""))
    online, reason, _ = mi.classify_live_intent(_row(pid))
    assert online is False and reason == "fixture"


def test_the_live_set_report_buckets_are_exhaustive(monkeypatch):
    monkeypatch.setattr(mi, "resolve_source_image",
                        lambda p: ("/tmp/x.jpg", p.get("image"), "found", "")
                        if "placeholder" not in str(p.get("image"))
                        else ("", p.get("image"), "placeholder", ""))
    # wix-777 rather than wix-001: wix-001 is now an explicit operator
    # decision, so it would land in operator_offline and this test would stop
    # exercising the placeholder path at all.
    products = [
        _row("wix-777", image="images/products/_placeholder.jpg"),
        _row("wix-002"),
        _row("wix-003", priceNgn=0),
        _row("jau-stock-9"),
    ]
    rep = mi.live_set_report(products, {})
    c = rep["counts"]
    assert c == {"approved_live": 1, "operator_offline": 0,
                 "placeholder_only": 1, "no_image": 0,
                 "needs_review": 1, "test_fixtures_excluded": 1}
    assert rep["approved_live_count"] == 1
    assert rep["live_ids"] == ["wix-002"]
    assert rep["placeholder_only_ids"] == ["wix-777"]
    assert [e["id"] for e in rep["needs_review"]] == ["wix-003"]
    assert rep["test_fixtures_excluded_ids"] == ["jau-stock-9"]
    # the script must not claim to have applied anything
    assert rep["applied_by_this_script"] is False


def test_an_existing_online_flag_that_contradicts_the_policy_is_reported(monkeypatch):
    """This is the real wix-001 case: the only local row flagged online=true
    points at the placeholder, so the conflict must be stated, not buried."""
    monkeypatch.setattr(mi, "resolve_source_image",
                        lambda p: ("", "images/products/_placeholder.jpg",
                                   "placeholder", ""))
    rep = mi.live_set_report([_row("wix-001", online=True)], {})
    conflicts = rep["existing_online_flags_that_conflict_with_the_policy"]
    assert len(conflicts) == 1
    assert conflicts[0]["id"] == "wix-001"
    # wix-001 is now an explicit operator decision, which takes precedence over
    # the generic placeholder classification.
    assert conflicts[0]["policy_says"] == "operator_offline"


def test_a_row_not_flagged_online_produces_no_conflict(monkeypatch):
    monkeypatch.setattr(mi, "resolve_source_image",
                        lambda p: ("", "images/products/_placeholder.jpg",
                                   "placeholder", ""))
    rep = mi.live_set_report([_row("wix-002", online=False)], {})
    assert rep["existing_online_flags_that_conflict_with_the_policy"] == []


def test_the_policy_never_publishes_everything():
    """The headline failure mode this whole section exists to prevent."""
    products, _seed, overrides, _deleted = mi.load_local_catalogue()
    rep = mi.live_set_report(list(products.values()), overrides)
    total = sum(rep["counts"].values())
    assert total == len(products), "every row must be classified"
    assert rep["counts"]["approved_live"] < total, (
        "the policy published every row - it is not filtering anything")
    assert rep["counts"]["test_fixtures_excluded"] >= 1
    # and the ids it does publish must all be real products
    assert all(not mi._FIXTURE_ID_RE.match(i) for i in rep["live_ids"])


# ---------------------------------------------------------------------------
# A price of 0 is not the same as a missing price. Conflating them let wix-012
# (priceNgn=0) read as "0 missing prices" while it was in fact unpriceable.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row,want", [
    ({"priceNgn": 1000, "priceCfa": 500}, []),
    ({"priceNgn": 0, "priceCfa": 500}, [("priceNgn", 0)]),
    ({"priceNgn": 1000, "priceCfa": 0}, [("priceCfa", 0)]),
    ({"priceNgn": -5, "priceCfa": 500}, [("priceNgn", -5)]),
    ({"priceNgn": 0, "priceCfa": 0}, [("priceNgn", 0), ("priceCfa", 0)]),
    # absent/unparseable belongs to _price_missing, not here
    ({"priceNgn": None, "priceCfa": 500}, []),
    ({"priceCfa": 500}, []),
    ({"priceNgn": "abc", "priceCfa": 500}, []),
])
def test_price_non_positive(row, want):
    assert mi._price_non_positive(row) == want


def test_zero_price_is_reported_separately_from_missing_price():
    products = [
        {"id": "wix-801", "name": "Free", "slug": "free", "image": "",
         "priceNgn": 0, "priceCfa": 500, "stock": 3},
        {"id": "wix-802", "name": "No price", "slug": "noprice", "image": "",
         "priceNgn": None, "priceCfa": 500, "stock": 3},
        {"id": "wix-803", "name": "Fine", "slug": "fine", "image": "",
         "priceNgn": 1000, "priceCfa": 500, "stock": 3},
    ]
    rep = mi.plan_products(products, "uploads", "https://abcxyz.supabase.co")
    assert [e["id"] for e in rep["non_positive_prices"]] == ["wix-801"]
    assert rep["non_positive_prices"][0]["offenders"] == {"priceNgn": 0}
    assert [e["id"] for e in rep["missing_prices"]] == ["wix-802"]
