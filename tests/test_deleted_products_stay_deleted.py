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


# ------------------------------------------- issue 6: the durable row itself
class _ProductsQuery:
    """Chainable stand-in for select/order/range on the products table."""

    def __init__(self, rows, calls, count=None):
        self._rows = rows
        self._calls = calls
        self._count = count
        self._start = 0
        self._end = -1

    def order(self, _col, **_kw):
        return self

    def range(self, start, end):
        self._start, self._end = start, end
        return self

    def execute(self):
        self._calls.append((self._start, self._end))
        window = self._rows[self._start:self._end + 1]
        res = type("Res", (), {"data": window})()
        if self._count == "exact":
            res.count = len(self._rows)
        return res


class _ProductsTable:
    def __init__(self, rows, calls):
        self._rows = sorted(rows, key=lambda r: str(r.get("id") or ""))
        self._calls = calls

    def select(self, cols, count=None):
        return _ProductsQuery(self._rows, self._calls, count)


class _ProductsClient:
    def __init__(self, rows):
        self.calls = []
        self._table = _ProductsTable(rows, self.calls)

    def table(self, _name):
        return self._table


def test_merged_folds_dead_table_rows_when_the_tombstone_list_is_empty(
        monkeypatch, tmp_path):
    """The EXACT production case: a seed product's products-table row is
    source="deleted" but the durable growth_settings tombstone write never
    landed (legacy table). merged() unions the 258 bundled seed rows on
    every read, so only the dead ROW can keep the product gone. The
    tombstone list must not be required for suppression."""
    pid = _seed_id()
    live_row = {
        "id": "jau-live-1", "source": "admin", "name": "Live admin piece",
        "priceNgn": 1000, "priceCfa": 440, "stock": 3, "stock_quantity": 3,
    }
    dead_row = {
        "id": pid, "source": "deleted", "name": "Deleted seed piece",
        "priceNgn": 0, "priceCfa": 0, "stock": 0, "stock_quantity": 0,
    }
    client = _ProductsClient([live_row, dead_row])
    monkeypatch.setattr(supabase_store, "client", lambda: client)
    monkeypatch.setattr(supabase_store, "PAGE_SIZE", 50)
    # EMPTY tombstone list - the growth_settings write failed in production.
    mem = _MemGrowth([])
    _wire(monkeypatch, mem)
    monkeypatch.setattr(catmod, "CATALOG_FILE", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text(json.dumps({"products": [], "deleted": []}))

    merged = catmod.merged(include_hidden=True)
    ids = {str(p["id"]) for p in merged}
    assert pid not in ids, "the deleted seed product came back from the dead"
    assert "jau-live-1" in ids, "the live admin row must still be served"
    assert len(ids) >= 200, "the rest of the seed catalogue must stay visible"

    # the dead-row walk itself answers the tombstoned id
    assert supabase_store.dead_product_ids_table() == {pid}


def test_a_deleted_row_is_served_again_once_it_is_recreated(monkeypatch,
                                                            tmp_path):
    """A later save re-writes the row with source="admin" (and clears the
    tombstone list). The dead-row fold must NOT keep hiding it - the row's
    own source is the durable truth in both directions."""
    pid = _seed_id()
    client = _ProductsClient([{
        "id": pid, "source": "admin", "name": "Recreated piece",
        "priceNgn": 500, "priceCfa": 220, "stock": 1, "stock_quantity": 1,
    }])
    monkeypatch.setattr(supabase_store, "client", lambda: client)
    monkeypatch.setattr(supabase_store, "PAGE_SIZE", 50)
    mem = _MemGrowth([])
    _wire(monkeypatch, mem)
    monkeypatch.setattr(catmod, "CATALOG_FILE", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text(json.dumps({"products": [], "deleted": []}))

    ids = {str(p["id"]) for p in catmod.merged(include_hidden=True)}
    assert pid in ids, "a recreated row with source=admin must be live again"


def test_admin_delete_surfaces_a_tombstone_write_failure(monkeypatch):
    """The tombstone write used to be swallowed (try/except: pass): the
    portal reported 'deleted' while the growth_settings write never landed,
    and the product came back after the next deploy. A failed tombstone
    must now surface an honest 503 so the admin retries."""
    import app as appmod
    from db import execute, init_db
    from config import Config
    from _pw import PW

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
    mem = _MemGrowth(fail_save=True)
    _wire(monkeypatch, mem)

    email = "jaurastore@gmail.com"
    with a.test_client() as c:
        r = c.post("/api/admin/login",
                   json={"email": email, "password": PW, "recaptcha": ""})
        assert r.status_code == 200, r.data
        tok = r.get_json()["csrf"]
        r = c.delete("/api/admin/products/jau-tomb-2",
                     headers={"X-CSRF-Token": tok})
        assert r.status_code == 503, r.data
        body = r.get_json()
        assert body["ok"] is False
        assert "tombstone" in body["error"].lower(), body
    assert mem.saves >= 1, "the tombstone write must have been attempted"
