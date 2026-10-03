"""Storage maintenance: orphaned/duplicate upload cleanup + hard deletes.

Covers the 2026-10-01 maintenance task:

* the cleanup scan (storage_cleanup.build_plan / apply_plan) - referenced
  files (product media, order receipts, site settings, categories) are never
  candidates; unreferenced files are; uploads younger than the grace window
  are protected; duplicate content is reported; apply deletes exactly the
  candidates and re-validates against the LIVE reference set;
* complete HARD deletes - a deleted product loses its DB rows
  (variant_stock / product_views / product_reviews) and its uploaded files in
  one step; a deleted order loses its coupon redemptions too;
* the admin endpoint - dry-run by default, apply on request, audit-logged,
  admin+CSRF protected, and a refused scan (unreadable reference source)
  answers 503 without deleting anything.
"""
import datetime
import json
import os
import sys

import pytest

import api as api_mod
import app as appmod
import catalog as catalog_mod
import storage_cleanup
import storage as storage_mod

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = "jaurastore@gmail.com"
OLD = datetime.datetime.utcnow() - datetime.timedelta(days=30)


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture()
def env(app, tmp_path, monkeypatch):
    """Scratch catalogue + scratch upload root + scratch database."""
    from db import init_db
    init_db()
    cat = tmp_path / "catalog.json"
    cat.write_text(json.dumps({"products": [], "deleted": []}), encoding="utf-8")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(cat))
    api_mod._invalidate_catalog_cache()
    api_mod._invalidate_category_cache()
    monkeypatch.setattr(storage_mod, "local_root", lambda: str(uploads))
    monkeypatch.setattr(storage_cleanup, "_local_mode", lambda: True)
    yield {"catalog": str(cat), "uploads": uploads, "tmp": tmp_path}
    api_mod._invalidate_catalog_cache()
    api_mod._invalidate_category_cache()


def _put_file(env, key, data=b"photo-bytes", age_days=30):
    path = env["uploads"] / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    stamp = datetime.datetime.utcnow() - datetime.timedelta(days=age_days)
    os.utime(path, (stamp.timestamp(), stamp.timestamp()))
    return "/uploads/" + key


@pytest.fixture()
def admin(client):
    login = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert login.status_code == 200
    token = login.get_json()["csrf"]

    def _save(payload):
        return client.post("/api/products",
                           headers={"X-CSRF-Token": token}, json=payload)
    return _save


def _csrf(client):
    login = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    return login.get_json()["csrf"]


# --------------------------------------------------------------- the scan

def test_referenced_files_are_never_candidates(client, admin, env):
    url = _put_file(env, "products/2026/10/live.jpg")
    admin({"product": {"id": "jau-clean-1", "name": "Live Photo Purse",
                       "sku": "CLN-1", "category": "accessories",
                       "priceNgn": 9000, "stock": 2, "online": True,
                       "images": [url]}})
    _put_file(env, "products/2026/10/dead.jpg")          # unreferenced + old
    report = storage_cleanup.build_plan(min_age_days=2)
    assert "products/2026/10/live.jpg" not in report["delete_candidates"]
    assert report["protected_referenced"] >= 1
    assert report["delete_candidates"] == ["products/2026/10/dead.jpg"]


def test_recent_uploads_are_protected_by_the_grace_window(client, admin, env):
    _put_file(env, "products/2026/10/inflight.jpg", age_days=0)   # just landed
    report = storage_cleanup.build_plan(min_age_days=2)
    assert report["delete_candidates"] == []
    assert report["protected_recent_uploads"] == 1


def test_order_receipts_are_references_too(client, admin, env):
    from db import execute
    url = _put_file(env, "proofs/receipt-live.jpg")
    execute("INSERT INTO orders (id, email, status, total, payload, proof_url)"
            " VALUES ('ORD-CLN', 'x@y.z', 'pending', 100, '{}', ?)", (url,))
    _put_file(env, "proofs/receipt-dead.jpg")
    report = storage_cleanup.build_plan(min_age_days=2)
    assert "proofs/receipt-live.jpg" not in report["delete_candidates"]
    assert report["delete_candidates"] == ["proofs/receipt-dead.jpg"]


def test_duplicate_content_is_reported_and_purged(client, admin, env):
    twin = b"same-photo-bytes"
    _put_file(env, "products/2026/10/dup-a.jpg", data=twin)
    _put_file(env, "products/2026/10/dup-b.jpg", data=twin)
    _put_file(env, "products/2026/10/unique.jpg", data=b"other")
    report = storage_cleanup.build_plan(min_age_days=2)
    assert sorted(report["delete_candidates"]) == [
        "products/2026/10/dup-a.jpg", "products/2026/10/dup-b.jpg",
        "products/2026/10/unique.jpg"]
    assert report["duplicate_groups"] == [["products/2026/10/dup-a.jpg",
                                           "products/2026/10/dup-b.jpg"]]


def test_apply_deletes_only_candidates_and_reports_bytes(client, admin, env):
    keep_url = _put_file(env, "products/2026/10/keep.jpg", data=b"k" * 100)
    admin({"product": {"id": "jau-clean-2", "name": "Keep Photo Purse",
                       "sku": "CLN-2", "category": "accessories",
                       "priceNgn": 9000, "stock": 2, "online": True,
                       "images": [keep_url]}})
    _put_file(env, "products/2026/10/gone.jpg", data=b"g" * 50)
    _put_file(env, "notes/readme.txt", data=b"not media")   # never a candidate
    report = storage_cleanup.build_plan(min_age_days=2)
    result = storage_cleanup.apply_plan(report=report)
    assert result["deleted"] == ["products/2026/10/gone.jpg"]
    assert result["freed_bytes"] == 50 and not result["errors"]
    assert (env["uploads"] / "products/2026/10/keep.jpg").exists()
    assert not (env["uploads"] / "products/2026/10/gone.jpg").exists()
    assert (env["uploads"] / "notes/readme.txt").exists()   # unknown ext kept
    # a second run finds nothing left to do
    assert storage_cleanup.build_plan(min_age_days=2)["delete_candidates"] == []


def test_apply_revalidates_against_the_live_catalogue(client, admin, env):
    """A file referenced AFTER the report was built must survive apply."""
    url = _put_file(env, "products/2026/10/raced.jpg", data=b"r" * 10)
    report = storage_cleanup.build_plan(min_age_days=2)
    assert "products/2026/10/raced.jpg" in report["delete_candidates"]
    # the product row lands between the report and the apply
    admin({"product": {"id": "jau-clean-3", "name": "Raced Photo Purse",
                       "sku": "CLN-3", "category": "accessories",
                       "priceNgn": 9000, "stock": 2, "online": True,
                       "images": [url]}})
    result = storage_cleanup.apply_plan(report=report)
    assert "products/2026/10/raced.jpg" not in result["deleted"]
    assert (env["uploads"] / "products/2026/10/raced.jpg").exists()


def test_an_unreadable_reference_source_aborts_instead_of_orphaning(client, admin, env, monkeypatch):
    _put_file(env, "products/2026/10/would-be-orphan.jpg")
    def boom():
        raise RuntimeError("db exploded")
    monkeypatch.setattr(storage_cleanup, "_referenced_keys", boom)
    with pytest.raises(RuntimeError):
        storage_cleanup.build_plan(min_age_days=2)


# --------------------------------------------------------- hard deletes

def test_product_hard_delete_removes_rows_and_files(client, admin, env):
    from db import execute, one
    url = _put_file(env, "products/2026/10/purge-me.jpg")
    admin({"product": {"id": "jau-clean-4", "name": "Purge Purse",
                       "sku": "CLN-4", "category": "accessories",
                       "priceNgn": 9000, "stock": 2, "online": True,
                       "images": [url]}})
    execute("INSERT INTO variant_stock (product_id, variant_key, variant_label,"
            " qty, low_threshold) VALUES ('jau-clean-4','k','K',2,1)")
    execute("INSERT INTO product_views (product_id, views) VALUES ('jau-clean-4', 7)")
    execute("INSERT INTO product_reviews (product_id, email, rating, body)"
            " VALUES ('jau-clean-4','a@b.c',5,'nice')",)
    token = _csrf(client)
    r = client.delete("/api/admin/products/jau-clean-4",
                      headers={"X-CSRF-Token": token})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert not (env["uploads"] / "products/2026/10/purge-me.jpg").exists()
    assert one("SELECT * FROM variant_stock WHERE product_id='jau-clean-4'") is None
    assert one("SELECT * FROM product_views WHERE product_id='jau-clean-4'") is None
    assert one("SELECT * FROM product_reviews WHERE product_id='jau-clean-4'") is None
    names = [p["name"] for p in catalog_mod.merged(include_hidden=True)]
    assert "Purge Purse" not in names


def test_order_hard_delete_removes_coupon_uses(client, admin, env):
    from db import execute, one
    execute("INSERT INTO orders (id, email, status, total, payload)"
            " VALUES ('ORD-DEL', 'x@y.z', 'pending', 100, '{}')")
    execute("INSERT INTO coupon_uses (code, email, order_id, percent)"
            " VALUES ('SAVE10','x@y.z','ORD-DEL',10)")
    token = _csrf(client)
    r = client.delete("/api/admin/orders/ORD-DEL",
                      headers={"X-CSRF-Token": token})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert one("SELECT * FROM coupon_uses WHERE order_id='ORD-DEL'") is None
    assert one("SELECT * FROM orders WHERE id='ORD-DEL'") is None


# ------------------------------------------------------------ the endpoint

def test_cleanup_endpoint_is_dry_run_by_default(client, admin, env):
    _put_file(env, "products/2026/10/ep-orphan.jpg", data=b"e" * 20)
    token = _csrf(client)
    r = client.post("/api/admin/storage/cleanup",
                    headers={"X-CSRF-Token": token}, json={})
    body = r.get_json()
    assert r.status_code == 200
    assert body["applied"] is False
    assert body["candidate_count"] == 1
    assert (env["uploads"] / "products/2026/10/ep-orphan.jpg").exists()
    # apply for real
    r2 = client.post("/api/admin/storage/cleanup",
                     headers={"X-CSRF-Token": token}, json={"apply": True})
    body2 = r2.get_json()
    assert r2.status_code == 200 and body2["applied"] is True
    assert body2["deleted"] == ["products/2026/10/ep-orphan.jpg"]
    assert not (env["uploads"] / "products/2026/10/ep-orphan.jpg").exists()


def test_cleanup_endpoint_requires_admin_and_csrf(client, admin, env):
    _put_file(env, "products/2026/10/locked.jpg")
    r = client.post("/api/admin/storage/cleanup", json={"apply": True})
    assert r.status_code in (401, 403)      # no session -> refused
    token = _csrf(client)
    r = client.post("/api/admin/storage/cleanup",
                    headers={"X-CSRF-Token": "wrong"}, json={"apply": True})
    assert r.status_code == 403


def test_cleanup_endpoint_audits_and_refuses_failed_scans(client, admin, env, monkeypatch):
    from db import query
    _put_file(env, "products/2026/10/audited.jpg")
    token = _csrf(client)
    client.post("/api/admin/storage/cleanup",
                headers={"X-CSRF-Token": token}, json={})
    rows = query("SELECT action FROM audit_log WHERE action='storage.cleanup'")
    assert rows, "the cleanup must leave an audit trail"
    # a broken reference source must answer 503 and delete nothing
    def boom():
        raise RuntimeError("reference read failed")
    monkeypatch.setattr(storage_cleanup, "_referenced_keys", boom)
    r = client.post("/api/admin/storage/cleanup",
                    headers={"X-CSRF-Token": token}, json={"apply": True})
    assert r.status_code == 503
    assert (env["uploads"] / "products/2026/10/audited.jpg").exists()


# ------------------------------------------------- source pins

def test_supabase_hard_delete_also_clears_product_rows():
    """Deleting a product must leave nothing behind for the storage sweeper to
    trip over later: every per-product child table is purged, and a table that
    does not exist in this deployment is skipped rather than failing."""
    src = open(os.path.join(ROOT, "supabase_store.py"), encoding="utf-8").read()
    sweep = src[src.index("def _purge_product_children"):]
    sweep = sweep[:sweep.index("\ndef ")]
    assert 'c.table(table).delete().in_(column, ids).execute()' in sweep
    for table in ("product_variants", "product_prices", "product_options",
                  "variant_stock", "product_reviews", "product_views",
                  "featured_products"):
        assert f'("{table}", "product_id")' in src, table
    # a missing table is not an error...
    assert "missing" in sweep
    # ...but a real failure is reported, never swallowed
    assert "errors.append" in sweep
    # and the sweep actually runs as part of the hard delete
    fn = src[src.index("def hard_delete_products"):]
    fn = fn[:fn.index("\ndef ")]
    assert "_purge_product_children(c, ids)" in fn


def test_supabase_order_delete_clears_order_scoped_rows():
    src = open(os.path.join(ROOT, "supabase_store.py"), encoding="utf-8").read()
    fn = src[src.index("def delete_order"):]
    fn = fn[:fn.index("\ndef ")]
    for table in ("coupon_uses", "referral_uses", "receipts"):
        assert f'c.table("{table}").delete().eq("order_id"' in fn
    assert 'c.table("orders").delete().eq("id", order_id)' in fn


def test_admin_settings_exposes_the_cleanup_buttons():
    js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert 'id="storage-cleanup-scan"' in js
    assert 'id="storage-cleanup-apply"' in js
    assert 'api/admin/storage/cleanup' in js
    # the UI never deletes straight from the scan: dry-run first, then apply
    assert js.index('id="storage-cleanup-scan"') < js.index('id="storage-cleanup-apply"')


def test_cli_tool_has_the_same_grace_window():
    src = open(os.path.join(ROOT, "tools", "cleanup_supabase_storage.py"),
               encoding="utf-8").read()
    assert "--min-age-days" in src
    assert "age is None or age < args.min_age_days" in src


# ---------------------------------------- the Supabase scan path (fake bucket)

def test_supabase_reference_scan_uses_receipts_file_url_schema(monkeypatch):
    """The live receipts table has file_url, not the orders-only proof_url."""
    from types import SimpleNamespace
    import db
    import supabase_settings
    import supabase_store

    order_url = "/uploads/proofs/order-proof.jpg"
    receipt_url = "/uploads/proofs/receipt-proof.jpg"

    class Query:
        def __init__(self, table):
            self.table_name = table
            self.fields = []

        def select(self, projection):
            self.fields = [part.strip() for part in projection.split(",")]
            allowed = {"orders": {"proof_url"}, "receipts": {"file_url"}}
            unknown = set(self.fields) - allowed.get(self.table_name, set())
            if unknown:
                raise RuntimeError(
                    f"column {self.table_name}.{sorted(unknown)[0]} does not exist")
            return self

        def execute(self):
            if self.table_name == "orders":
                return SimpleNamespace(data=[{"proof_url": order_url}])
            if self.table_name == "receipts":
                return SimpleNamespace(data=[{"file_url": receipt_url}])
            raise AssertionError(f"unexpected table {self.table_name}")

    class Client:
        def table(self, name):
            return Query(name)

    monkeypatch.setattr(catalog_mod, "merged", lambda **_kwargs: [])
    monkeypatch.setattr(db, "query", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(supabase_store, "client", lambda: Client())
    monkeypatch.setattr(supabase_settings, "enabled", lambda: False)
    monkeypatch.setattr(storage_cleanup, "_category_rows", lambda: [])

    keys, sources = storage_cleanup._referenced_keys()
    assert keys == {"proofs/order-proof.jpg", "proofs/receipt-proof.jpg"}
    assert sources["supabase orders+receipts"] == 2


def test_supabase_order_delete_reads_receipt_file_url_only(monkeypatch):
    """Order deletion can still purge a receipt against the production schema."""
    from types import SimpleNamespace
    import supabase_store

    receipt_url = "https://jaura.supabase.co/storage/v1/object/public/uploads/proofs/receipt.png"
    order_url = "https://jaura.supabase.co/storage/v1/object/public/uploads/proofs/order.png"
    removed_urls = []

    class Query:
        def __init__(self, table):
            self.table_name = table
            self.operation = "select"
            self.fields = []

        def select(self, projection):
            self.fields = [part.strip() for part in projection.split(",")]
            allowed = {"receipts": {"id", "file_url"}, "orders": {"proof_url"}}
            unknown = set(self.fields) - allowed.get(self.table_name, set())
            if unknown:
                raise RuntimeError(
                    f"column {self.table_name}.{sorted(unknown)[0]} does not exist")
            return self

        def eq(self, _column, _value):
            return self

        def delete(self):
            self.operation = "delete"
            return self

        def execute(self):
            if self.operation == "delete":
                return SimpleNamespace(data=[])
            if self.table_name == "receipts":
                return SimpleNamespace(data=[{"id": "R-1", "file_url": receipt_url}])
            if self.table_name == "orders":
                return SimpleNamespace(data=[{"proof_url": order_url}])
            return SimpleNamespace(data=[])

    class Client:
        def table(self, name):
            return Query(name)

    monkeypatch.setattr(supabase_store, "client", lambda: Client())
    monkeypatch.setattr(
        supabase_store, "_delete_storage_object_from_url",
        lambda url: removed_urls.append(url) or True)

    assert supabase_store.delete_order("JA-RECEIPT-1") is True
    assert removed_urls == [receipt_url, order_url]


class _FakeStorageBucket:
    """Minimal in-memory Supabase bucket: list / download / remove."""

    def __init__(self, objects):
        self.objects = objects        # {path: {"data": bytes, "row": {...}}}

    def list(self, prefix="", opts=None):
        rows = []
        for path in sorted(self.objects):
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix):].strip("/")
            if not rest:
                continue
            top = rest.split("/", 1)[0]
            row = {"name": top}
            if "/" in rest:                      # a folder: no id/metadata
                rows.append(row)
            else:
                info = self.objects[path]
                row.update({"id": path, "metadata": info.get("row", {}).get("metadata") or {},
                            "updated_at": info.get("row", {}).get("updated_at"),
                            "created_at": info.get("row", {}).get("created_at")})
                rows.append(row)
        return rows

    def download(self, path):
        return self.objects[path]["data"]

    def remove(self, paths):
        return [{"id": p} for p in paths if p in self.objects and not self.objects.pop(p)]


def test_supabase_listing_scan_and_delete(monkeypatch, tmp_path):
    """The bucket-side listing honors references, the grace window and
    deletes only the candidates."""
    import supabase_store

    old_ts = (datetime.datetime.utcnow() - datetime.timedelta(days=30)) \
        .isoformat() + "Z"
    objects = {
        "products/2026/10/live.jpg": {
            "data": b"live",
            "row": {"metadata": {"size": 4}, "updated_at": old_ts}},
        "products/2026/10/dead.jpg": {
            "data": b"dead",
            "row": {"metadata": {"size": 4}, "updated_at": old_ts}},
        "products/2026/10/fresh.jpg": {
            "data": b"new",
            "row": {"metadata": {"size": 3},
                    "updated_at": datetime.datetime.utcnow().isoformat() + "Z"}},
    }
    bucket = _FakeStorageBucket(objects)

    class _FakeStorage:
        def from_(self, name):
            return bucket

    class _FakeClient:
        storage = _FakeStorage()

    monkeypatch.setattr(supabase_store, "client", lambda: _FakeClient())
    monkeypatch.setattr(supabase_store, "_bucket", lambda: "uploads")
    monkeypatch.setattr(storage_cleanup, "_local_mode", lambda: False)
    # references: the live product points at live.jpg; nothing else does
    monkeypatch.setattr(storage_cleanup, "_referenced_keys",
                        lambda: ({"products/2026/10/live.jpg"},
                                 {"injected": 1}))

    report = storage_cleanup.build_plan(min_age_days=2)
    assert report["mode"] == "supabase"
    assert report["delete_candidates"] == ["products/2026/10/dead.jpg"]
    assert report["protected_referenced"] == 1
    assert report["protected_recent_uploads"] == 1       # fresh.jpg protected

    result = storage_cleanup.apply_plan(report=report)
    assert result["deleted"] == ["products/2026/10/dead.jpg"]
    assert "products/2026/10/live.jpg" in objects
    assert "products/2026/10/fresh.jpg" in objects
    assert "products/2026/10/dead.jpg" not in objects
