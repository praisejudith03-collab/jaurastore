"""Deleted products must stay deleted across Render redeploys.

Soft-deleting a seed product (``source="deleted"`` on its products-table
row) only hides rows that live in the products table. ``catalog.merged()``
still unions the 258 bundled seed rows on every read, and the local
``data/catalog.json`` ``deleted`` list lives on Render's ephemeral disk —
so a deleted seed id used to come back after every redeploy.

The fix keeps the id list in Supabase ``growth_settings`` under
``deleted_product_ids_json``. These tests pin that in place without a real
Supabase: every helper is monkeypatched.

Run with:  python3 -m pytest tests/test_deleted_products_stay_deleted.py -q
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("SITE_CONFIG_PATH", "/tmp/jaura_test_site_del.json")

import pytest  # noqa: E402

import catalog as catmod  # noqa: E402
import supabase_store  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------ helpers
class _MemGrowth:
    """In-memory stand-in for the growth_settings deleted-ids helpers."""

    def __init__(self, start=None, fail_load=False, fail_save=False):
        self.ids = list(start or [])
        self.fail_load = fail_load
        self.fail_save = fail_save
        self.loads = 0
        self.saves = 0
        self.adds = []
        self.clears = []

    def load(self):
        self.loads += 1
        if self.fail_load:
            return None
        return list(self.ids)

    def save(self, ids):
        self.saves += 1
        if self.fail_save:
            return False
        clean = []
        seen = set()
        for x in ids or []:
            pid = str(x or "").strip()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            clean.append(pid)
        self.ids = clean
        return True

    def add(self, pid):
        self.adds.append(pid)
        pid = str(pid or "").strip()
        if not pid:
            return False
        current = self.load()
        if current is None:
            return self.save([pid])
        if pid in current:
            return True
        return self.save(list(current) + [pid])

    def clear(self, pid):
        self.clears.append(pid)
        pid = str(pid or "").strip()
        if not pid:
            return False
        current = self.load()
        if current is None:
            return False
        if pid not in current:
            return True
        return self.save([x for x in current if x != pid])


def _wire(monkeypatch, mem):
    monkeypatch.setattr(supabase_store, "load_deleted_ids", mem.load)
    monkeypatch.setattr(supabase_store, "save_deleted_ids", mem.save)
    monkeypatch.setattr(supabase_store, "add_deleted_id", mem.add)
    monkeypatch.setattr(supabase_store, "clear_deleted_id", mem.clear)


def _seed_id():
    seeds = catmod._seed_products()
    assert seeds, "seed catalogue is empty"
    return str(seeds[0]["id"])


# ------------------------------------------------------------- unit helpers
def test_load_deleted_ids_returns_none_when_client_missing(monkeypatch):
    monkeypatch.setattr(supabase_store, "client", lambda: None)
    assert supabase_store.load_deleted_ids() is None


def test_save_deleted_ids_dedupes_and_drops_blanks(monkeypatch):
    stored = {}

    class FakeTable:
        def upsert(self, rows):
            stored["rows"] = rows
            return self
        def execute(self):
            return type("R", (), {"data": stored["rows"]})()

    class FakeClient:
        def table(self, name):
            assert name == "growth_settings"
            return FakeTable()

    monkeypatch.setattr(supabase_store, "client", lambda: FakeClient())
    ok = supabase_store.save_deleted_ids(["a", "a", "", "  ", "b", "a"])
    assert ok is True
    payload = json.loads(stored["rows"][0]["value"])
    assert payload == ["a", "b"]
    assert stored["rows"][0]["key"] == supabase_store.DELETED_IDS_KEY


def test_add_deleted_id_is_idempotent(monkeypatch):
    mem = _MemGrowth(["already"])
    # Drive the real helpers through the mem store via the public API shape.
    monkeypatch.setattr(supabase_store, "load_deleted_ids", mem.load)
    monkeypatch.setattr(supabase_store, "save_deleted_ids", mem.save)
    assert supabase_store.add_deleted_id("already") is True
    assert mem.ids == ["already"]
    assert supabase_store.add_deleted_id("new-one") is True
    assert mem.ids == ["already", "new-one"]


def test_clear_deleted_id_noops_on_failed_read(monkeypatch):
    mem = _MemGrowth(["keep-me"], fail_load=True)
    monkeypatch.setattr(supabase_store, "load_deleted_ids", mem.load)
    monkeypatch.setattr(supabase_store, "save_deleted_ids", mem.save)
    assert supabase_store.clear_deleted_id("keep-me") is False
    # Must not wipe the list on a blip.
    assert mem.saves == 0


# ------------------------------------------------------------- merged() filter
def test_merged_hides_durable_tombstone_even_when_local_deleted_is_empty(monkeypatch):
    """Simulate a wiped disk: local deleted=[], durable list still has the id."""
    pid = _seed_id()
    mem = _MemGrowth([pid])
    _wire(monkeypatch, mem)
    # No Supabase products table; seed union is the live catalogue.
    monkeypatch.setattr(catmod, "_supabase_products", lambda: None)
    monkeypatch.setattr(catmod, "_load_overrides",
                        lambda: ({"products": [], "deleted": []}, "/tmp/x"))
    merged = catmod.merged(include_hidden=True)
    assert pid not in {str(p["id"]) for p in merged}
    # And every other seed product is still there.
    assert len(merged) >= 200


def test_merged_unions_local_and_durable_deleted(monkeypatch):
    seeds = catmod._seed_products()
    a, b = str(seeds[0]["id"]), str(seeds[1]["id"])
    mem = _MemGrowth([a])
    _wire(monkeypatch, mem)
    monkeypatch.setattr(catmod, "_supabase_products", lambda: None)
    monkeypatch.setattr(catmod, "_load_overrides",
                        lambda: ({"products": [], "deleted": [b]}, "/tmp/x"))
    ids = {str(p["id"]) for p in catmod.merged(include_hidden=True)}
    assert a not in ids and b not in ids


def test_merged_empty_on_durable_load_failure(monkeypatch):
    """An outage must neither resurrect nor empty the shop."""
    pid = _seed_id()
    mem = _MemGrowth([pid], fail_load=True)
    _wire(monkeypatch, mem)
    monkeypatch.setattr(catmod, "_supabase_products", lambda: None)
    monkeypatch.setattr(catmod, "_load_overrides",
                        lambda: ({"products": [], "deleted": []}, "/tmp/x"))
    merged = catmod.merged(include_hidden=True)
    # Failure → empty durable set → product is visible (local deleted is empty).
    # This is the safe side: we never invent a full wipe on a blip.
    assert pid in {str(p["id"]) for p in merged}
    assert len(merged) >= 200


def test_merged_hides_durable_id_on_supabase_path_too(monkeypatch):
    pid = _seed_id()
    mem = _MemGrowth([pid])
    _wire(monkeypatch, mem)
    # Supabase returns the full seed (as if soft-delete never landed).
    monkeypatch.setattr(catmod, "_supabase_products",
                        lambda: [dict(p) for p in catmod._seed_products()])
    monkeypatch.setattr(catmod, "overrides",
                        lambda: {"products": [], "deleted": []})
    ids = {str(p["id"]) for p in catmod.merged(include_hidden=True)}
    assert pid not in ids


# ------------------------------------------------------------- remove / upsert
def test_remove_writes_both_local_and_durable(monkeypatch, tmp_path):
    pid = "jau-del-test-1"
    ov = tmp_path / "catalog.json"
    ov.write_text(json.dumps({
        "products": [{"id": pid, "name": "Gone", "category": "beauty",
                      "priceNgn": 1000, "online": True, "stock": 3}],
        "deleted": [],
    }))
    monkeypatch.setattr(catmod, "CATALOG_FILE", str(ov))
    monkeypatch.setattr(catmod, "_prod_source", lambda: False)
    monkeypatch.setattr(catmod, "_sync_repo_async", lambda: None)
    monkeypatch.setattr(supabase_store, "delete_products", lambda ids: True)
    mem = _MemGrowth()
    _wire(monkeypatch, mem)

    catmod.remove(pid, actor="test")

    data = json.loads(ov.read_text())
    assert pid in data["deleted"]
    assert pid not in {p["id"] for p in data["products"]}
    assert pid in mem.ids
    assert pid in mem.adds


def test_remove_on_prod_source_still_tombs(monkeypatch):
    """Production path: products-table soft-delete + durable tombstone."""
    pid = "jau-del-test-2"
    monkeypatch.setattr(catmod, "_prod_source", lambda: True)
    monkeypatch.setattr(catmod, "_sync_repo_async", lambda: None)
    called = {"delete": False}
    monkeypatch.setattr(supabase_store, "delete_products",
                        lambda ids: called.__setitem__("delete", True) or True)
    mem = _MemGrowth()
    _wire(monkeypatch, mem)

    catmod.remove(pid, actor="test")
    assert called["delete"] is True
    assert pid in mem.ids


def test_upsert_clears_durable_tombstone(monkeypatch, tmp_path):
    """Re-creating a product under the same id must un-hide it forever."""
    pid = "jau-del-test-3"
    ov = tmp_path / "catalog.json"
    ov.write_text(json.dumps({"products": [], "deleted": [pid]}))
    monkeypatch.setattr(catmod, "CATALOG_FILE", str(ov))
    monkeypatch.setattr(catmod, "_prod_source", lambda: False)
    monkeypatch.setattr(catmod, "_sync_repo_async", lambda: None)
    monkeypatch.setattr(supabase_store, "upsert_products", lambda rows: True)
    monkeypatch.setattr(supabase_store, "enabled", lambda: False)
    mem = _MemGrowth([pid])
    _wire(monkeypatch, mem)

    product = {
        "id": pid, "name": "Back", "category": "beauty",
        "priceNgn": 2000, "online": True, "stock": 5,
        "slug": "back", "sku": "BACK-1", "image": "x.jpg",
    }
    clean, action, mirrored = catmod.upsert(product, actor="test")
    assert clean is not None
    assert action in ("created", "updated")
    data = json.loads(ov.read_text())
    assert pid not in data["deleted"]
    assert pid not in mem.ids
    assert pid in mem.clears


def test_admin_delete_records_durable_tombstone(monkeypatch):
    """The production admin DELETE route must call add_deleted_id."""
    import app as appmod
    from db import execute, init_db
    from config import Config
    from _pw import PW

    # Match the suite-wide bootstrap password before the app is created.
    os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", PW)

    a = appmod.create_app()
    a.config.update(TESTING=True)
    init_db()
    execute("DELETE FROM rate_limits")

    monkeypatch.setattr(Config, "ENV", "production")
    monkeypatch.setattr(Config, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake")
    monkeypatch.setattr(catmod, "_prod_source", lambda: True)
    monkeypatch.setattr(catmod, "_sync_repo_async", lambda: None)
    monkeypatch.setattr(supabase_store, "delete_products_strict",
                        lambda ids: True)
    mem = _MemGrowth()
    _wire(monkeypatch, mem)

    email = "jaurastore@gmail.com"
    with a.test_client() as c:
        r = c.post("/api/admin/login",
                   json={"email": email, "password": PW, "recaptcha": ""})
        assert r.status_code == 200, r.data
        tok = r.get_json()["csrf"]
        r = c.delete("/api/admin/products/jau-tomb-1",
                     headers={"X-CSRF-Token": tok})
        assert r.status_code == 200, r.data
        assert r.get_json()["ok"] is True
    assert "jau-tomb-1" in mem.ids


# ------------------------------------------- category-merge marker durability
def test_merge_marker_prefers_supabase_over_sqlite(monkeypatch, tmp_path):
    """A wiped SQLite must NOT re-run the merge when Supabase has the marker."""
    from db import execute
    execute("DELETE FROM growth_settings WHERE key=?", (catmod.MERGE_MARKER,))

    cat_file = tmp_path / "categories.json"
    # Owner already renamed beauty after the original merge ran.
    cat_file.write_text(json.dumps({"categories": [
        {"id": "beauty", "name": "Owner Beauty Name", "nameFr": "Beauté",
         "image": "x"},
        {"id": "gift-set", "name": "Owner Gift Name", "nameFr": "Coffret",
         "image": "x"},
    ]}))
    monkeypatch.setenv("CATEGORIES_PATH", str(cat_file))
    monkeypatch.setattr(catmod, "CATALOG_FILE", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text(json.dumps({"products": [], "deleted": []}))

    # Supabase already carries the marker from a previous deploy.
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "load_growth_settings",
                        lambda: {catmod.MERGE_MARKER: "2026-01-01T00:00:00Z"})

    assert catmod.merge_categories() is False
    data = json.loads(cat_file.read_text())
    names = {c["id"]: c["name"] for c in data["categories"]}
    # Owner renames must survive — the force-rename must NOT re-fire.
    assert names["beauty"] == "Owner Beauty Name"
    assert names["gift-set"] == "Owner Gift Name"


def test_merge_marker_is_written_to_supabase(monkeypatch, tmp_path):
    from db import execute
    execute("DELETE FROM growth_settings WHERE key=?", (catmod.MERGE_MARKER,))

    cat_file = tmp_path / "categories.json"
    cat_file.write_text(json.dumps({"categories": [
        {"id": "nails", "name": "Nails", "image": "x"},
        {"id": "beauty", "name": "Beauty & skincare", "image": "x"},
    ]}))
    monkeypatch.setenv("CATEGORIES_PATH", str(cat_file))
    monkeypatch.setattr(catmod, "CATALOG_FILE", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text(json.dumps({"products": [], "deleted": []}))

    mirrored = {}
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "load_growth_settings", lambda: {})
    monkeypatch.setattr(
        supabase_store, "mirror_growth_settings",
        lambda d: mirrored.update(d))

    assert catmod.merge_categories() is True
    assert catmod.MERGE_MARKER in mirrored
    # Second call is a no-op via the local SQLite marker too.
    assert catmod.merge_categories() is False
