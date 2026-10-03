"""Absolute deletion: a hard-deleted product can never come back.

The shop's delete button must remove a product FOR GOOD, and the reason
products used to reappear was never the delete itself - it was the writers
that ran after it:

  * the 5-minute supplier watchdog, which picks candidates, spends time
    fetching a supplier page, and only then writes. Deleting a product
    inside that window made the watchdog the LAST writer: catalog.upsert()
    re-created the row AND cleared the durable tombstone on the way
    (that is what un-hides a re-created product), so the owner's deletion
    was silently undone. The window was minutes wide on a real catalogue.
  * the nightly 2:00 AM sweep, which touches every linked product.
  * the remirror pass, which pushes disk rows back into Supabase.

Candidate selection already skipped deleted ids, which is not enough on its
own: selection and write are minutes apart. These tests pin the guard that
runs immediately BEFORE the write, which is the only place the race can
actually be closed.

Run with:  python3 -m pytest tests/test_absolute_delete_and_ghost_guard.py -q
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")

import pytest  # noqa: E402

import catalog as catalog_mod  # noqa: E402
import supplier_watchdog  # noqa: E402
import supabase_store as sb  # noqa: E402

SUPPLIER = "https://supplier.example/item/42"


def _row(pid="jau-ghost-1", **over):
    row = {"id": pid, "name": f"Ghost {pid}", "stock": 4, "stock_quantity": 4,
           "supplierSku": SUPPLIER, "supplierUrl": SUPPLIER, "priceNgn": 1000}
    row.update(over)
    return row


# ------------------------------------- the last line of defence before a write

def test_a_product_deleted_during_a_sync_is_never_written_back(monkeypatch):
    """A tombstone blocks the stock-only write before it reaches storage."""
    written = []
    monkeypatch.setattr(catalog_mod, "deleted_product_ids", lambda: {"jau-ghost-1"})
    monkeypatch.setattr(catalog_mod, "apply_supplier_stock",
                        lambda *args, **kwargs: written.append(args))

    warnings = []
    ok, warnings = supplier_watchdog._save_synced(
        _row(), _row(stock=9), "supplier-watchdog", warnings)

    assert written == [], "the watchdog tried to update an owner-deleted product"
    assert ok is False
    assert warnings[0]["code"] == "product_deleted_during_sync"


def test_a_live_product_receives_only_a_stock_patch(monkeypatch):
    """No stale name/price/image payload can be replayed over concurrent edits."""
    calls = []
    latest = _row(name="Admin's current name", priceNgn=9000, image="latest.jpg")
    monkeypatch.setattr(catalog_mod, "deleted_product_ids", lambda: set())

    def patch(pid, stock, option_changes=None, actor=None, allow_increase=False, option_snapshot_keys=None):
        calls.append((pid, stock, option_changes, actor, allow_increase))
        latest["stock"] = latest["stock_quantity"] = stock
        return latest, "updated", True

    monkeypatch.setattr(catalog_mod, "apply_supplier_stock", patch)
    stale = _row(name="Stale name", priceNgn=1000, image="old.jpg")
    warnings = []
    ok, warnings = supplier_watchdog._save_synced(
        stale, _row(stock=3, stock_quantity=3, name="Stale name",
                    priceNgn=1000, image="old.jpg"),
        "supplier-watchdog", warnings)

    assert ok is True and warnings == []
    assert calls == [("jau-ghost-1", 3, None, "supplier-watchdog", True)]
    assert latest["name"] == "Admin's current name"
    assert latest["priceNgn"] == 9000 and latest["image"] == "latest.jpg"
    assert latest["stock"] == 3


def test_the_never_recreate_result_is_honoured_before_the_written_check(monkeypatch):
    """The atomic UPDATE-only RPC reports a delete that won the race."""
    monkeypatch.setattr(catalog_mod, "deleted_product_ids", lambda: set())
    monkeypatch.setattr(catalog_mod, "apply_supplier_stock",
                        lambda *args, **kwargs: (None, "permanently-removed", True))

    warnings = []
    ok, warnings = supplier_watchdog._save_synced(
        _row(), _row(stock=9), "supplier-watchdog", warnings)

    assert ok is False
    assert warnings[0]["code"] == "product_deleted_during_sync"
    assert "never-re-create" in warnings[0]["reason"]


def test_a_deleted_id_is_skipped_by_both_the_tick_and_the_nightly_sweep(monkeypatch):
    """Neither the daytime nor nightly link batch may sync a tombstoned id."""
    monkeypatch.setattr(supplier_watchdog, "enabled", lambda: True)
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "merged",
                        lambda include_hidden=False: [_row(), _row("jau-live-1")])
    monkeypatch.setattr(catalog_mod, "deleted_product_ids", lambda: {"jau-ghost-1"})
    monkeypatch.setattr(supplier_watchdog, "_save_warnings", lambda w: None)
    supplier_watchdog._last_checked.clear()

    for run in (lambda: supplier_watchdog.tick(limit=10, min_interval_seconds=0),
                lambda: supplier_watchdog.nightly_sweep(min_interval_seconds=0)):
        supplier_watchdog._last_checked.clear()
        synced = []
        monkeypatch.setattr(supplier_watchdog, "sync_product",
                            lambda p, actor="supplier-watchdog", allowed_urls=None:
                                (synced.append(p["id"]), (True, []))[1])
        run()
        assert "jau-ghost-1" not in synced
        assert "jau-live-1" in synced


# --------------------------------------------- the hard delete sweeps the DB

class _Recorder:
    """Minimal PostgREST double that records the delete calls it received."""

    def __init__(self, missing=(), rpc_error=None):
        self.missing = set(missing)
        self.rpc_error = rpc_error
        self.deleted = []
        self.tombstones = []
        self.rpc_calls = []

    def table(self, name):
        rec = self

        class _T:
            def upsert(self_inner, rows, **_kwargs):
                class _U:
                    def execute(self_inner2):
                        if name in rec.missing:
                            raise RuntimeError(
                                f'Could not find the table public.{name} (PGRST205)')
                        rec.tombstones.extend(rows)
                        return type("R", (), {"data": rows})()
                return _U()

            def delete(self_inner):
                class _D:
                    def in_(self_inner2, col, ids):
                        if name in rec.missing:
                            raise RuntimeError(
                                f'Could not find the table public.{name} (PGRST205)')
                        rec.deleted.append((name, tuple(ids)))
                        return self_inner2

                    def execute(self_inner2):
                        return type("R", (), {"data": None})()
                return _D()

            def select(self_inner, cols="*"):
                class _S:
                    def eq(self_inner2, col, val):
                        return self_inner2

                    def execute(self_inner2):
                        return type("R", (), {"data": []})()
                return _S()

        return _T()

    def rpc(self, name, params):
        rec = self

        class _R:
            def execute(self_inner):
                rec.rpc_calls.append((name, tuple(params["product_ids"])))
                if rec.rpc_error:
                    raise rec.rpc_error
                if "deleted_products" in rec.missing:
                    raise RuntimeError("relation public.deleted_products does not exist")
                return type("Res", (), {"data": list(params["product_ids"])})()
        return _R()


def test_missing_atomic_delete_rpc_fails_closed(monkeypatch):
    """Without the RPC, a REST delete can race a supplier write that already
    passed its trigger; no row or media may be reported as deleted."""
    rec = _Recorder(rpc_error=RuntimeError("cascade not installed"))
    legacy_writes = []
    monkeypatch.setattr(sb, "client", lambda: rec)
    monkeypatch.setattr(sb, "add_deleted_id", lambda pid: legacy_writes.append(pid) or True)

    report = sb.hard_delete_products(["jau-x-1"])

    assert report["deleted"] == []
    assert report["errors"] and "hard_delete_products.sql" in report["errors"][0]
    assert rec.deleted == []
    assert legacy_writes == []


def test_hard_delete_fails_closed_when_the_race_safe_guard_is_missing(monkeypatch):
    """Without the SQL tombstone table/trigger, REST deletion can race a stale
    save, so the route must not claim an absolute delete succeeded."""
    rec = _Recorder(missing={"deleted_products"})
    monkeypatch.setattr(sb, "client", lambda: rec)
    monkeypatch.setattr(sb, "add_deleted_id", lambda pid: True)

    report = sb.hard_delete_products(["jau-x-guard-missing"])

    assert report["deleted"] == []
    assert report["errors"] and "hard_delete_products.sql" in report["errors"][0]
    assert rec.rpc_calls == [("hard_delete_products", ("jau-x-guard-missing",))]
    assert rec.deleted == []


def test_a_table_that_does_not_exist_is_skipped_not_failed(monkeypatch):
    """Most deployments keep options as JSON columns and have no
    product_options table at all. Its absence must not fail the delete."""
    rec = _Recorder(missing={"product_options", "product_variants",
                             "product_prices", "featured_products"})
    monkeypatch.setattr(sb, "client", lambda: rec)
    monkeypatch.setattr(sb, "add_deleted_id", lambda pid: True)

    report = sb.hard_delete_products(["jau-x-2"])

    assert report["deleted"] == ["jau-x-2"]
    assert report["errors"] == []


def test_a_real_child_table_failure_is_reported_not_swallowed(monkeypatch):
    """Silently skipping a failure is how orphans accumulate."""
    class _Broken(_Recorder):
        def table(self, name):
            if name == "product_variants":
                class _T:
                    def delete(self_inner):
                        raise RuntimeError("permission denied for table product_variants")
                return _T()
            return super().table(name)

    rec = _Broken()
    monkeypatch.setattr(sb, "client", lambda: rec)
    monkeypatch.setattr(sb, "add_deleted_id", lambda pid: True)

    report = sb.hard_delete_products(["jau-x-3"])
    assert any("product_variants" in e for e in report["errors"])


def test_the_sql_cascade_is_used_when_it_is_installed(monkeypatch):
    """With hard_delete_products.sql applied, the row and every child go in
    ONE database statement, and the app must not then delete the parent row a
    second time - that is a wasted round trip and an extra chance to fail
    after the deletion already succeeded."""
    rec = _Recorder()
    monkeypatch.setattr(sb, "client", lambda: rec)
    monkeypatch.setattr(sb, "add_deleted_id", lambda pid: True)

    report = sb.hard_delete_products(["jau-x-4"])

    assert rec.rpc_calls == [("hard_delete_products", ("jau-x-4",))]
    # the SQL function already took the parent row
    assert ("products", ("jau-x-4",)) not in rec.deleted
    assert report["deleted"] == ["jau-x-4"]


def test_the_cascade_and_the_sweep_never_both_delete_the_children(monkeypatch):
    """Either the database cascades, or the app sweeps. Doing both would be
    three extra round trips per delete for no gain."""
    rec = _Recorder()
    monkeypatch.setattr(sb, "client", lambda: rec)
    monkeypatch.setattr(sb, "add_deleted_id", lambda pid: True)
    sb.hard_delete_products(["jau-x-4b"])
    children = [t for t, _ in rec.deleted]
    assert children.count("variant_stock") <= 1
    assert len(rec.deleted) == len(set(rec.deleted))


def test_a_missing_cascade_function_never_falls_back_to_an_unsafe_delete(monkeypatch):
    """An incomplete migration is a visible failure, not a race-prone REST
    delete that can let a stale supplier write recreate the product."""
    rec = _Recorder(rpc_error=RuntimeError(
        "Could not find the function public.hard_delete_products"))
    monkeypatch.setattr(sb, "client", lambda: rec)
    monkeypatch.setattr(sb, "add_deleted_id", lambda pid: True)

    report = sb.hard_delete_products(["jau-x-5"])

    assert rec.rpc_calls == [("hard_delete_products", ("jau-x-5",))]
    assert rec.deleted == []
    assert report["deleted"] == []
    assert report["errors"]


def test_a_failed_delete_is_never_reported_as_deleted(monkeypatch):
    """If the row could not be removed, the admin must be told so instead of
    believing the product is gone while it still sells."""

    class _FailRow(_Recorder):
        def table(self, name):
            if name == "products":
                class _T:
                    def select(self_inner, cols="*"):
                        class _S:
                            def eq(self_inner2, c, v):
                                return self_inner2

                            def execute(self_inner2):
                                return type("R", (), {"data": []})()
                        return _S()

                    def delete(self_inner):
                        class _D:
                            def in_(self_inner2, col, ids):
                                return self_inner2

                            def execute(self_inner2):
                                raise RuntimeError("row level security violation")
                        return _D()
                return _T()
            return super().table(name)

    rec = _FailRow(rpc_error=RuntimeError("no function"))
    monkeypatch.setattr(sb, "client", lambda: rec)

    report = sb.hard_delete_products(["jau-x-6"])
    assert report["deleted"] == []
    assert any("delete" in e for e in report["errors"])


# ----------------------------------------------------- the migration itself

def test_the_sql_migration_creates_a_real_cascade():
    """The guarantee has to live in the SCHEMA, not in a Python list, so the
    child tables are actually gone and not merely unlisted."""
    sql = open(os.path.join(ROOT, "hard_delete_products.sql"), encoding="utf-8").read()
    lowered = sql.lower()
    for table in ("product_variants", "product_prices", "product_options",
                  "variant_stock", "product_reviews", "product_views",
                  "featured_products"):
        assert table in lowered, table
    assert lowered.count("on delete cascade") >= 4
    assert "create or replace function public.hard_delete_products" in lowered
    # the tombstone stays single-sourced in growth_settings
    assert "deleted_product_ids_json" in lowered


def test_production_supplier_patch_calls_only_the_update_rpc(monkeypatch):
    calls = []

    class _RpcResult:
        def execute(self):
            return type("Result", (), {"data": True})()

    class _Client:
        def rpc(self, name, params):
            calls.append((name, params))
            return _RpcResult()

    monkeypatch.setattr(sb, "client", lambda: _Client())
    monkeypatch.setattr(sb, "invalidate_read_cache", lambda *_args: None)
    ok = sb.apply_supplier_stock("jau-live-1", 4, {"Red": 2}, allow_increase=False)
    assert ok is True
    assert calls == [("sync_supplier_stock", {
        "p_id": "jau-live-1", "p_stock": 4,
        "p_option_stock_changes": {"Red": 2}, "p_allow_increase": False,
        "p_option_snapshot_keys": None,
    })]
