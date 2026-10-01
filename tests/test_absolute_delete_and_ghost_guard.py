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
    """The exact race: the row was selected while it was alive, the owner
    deleted it, and only now does the watchdog try to save. Nothing may be
    written - a write would re-create the row and clear the tombstone."""
    written = []
    monkeypatch.setattr(catalog_mod, "deleted_product_ids", lambda: {"jau-ghost-1"})
    monkeypatch.setattr(catalog_mod, "upsert",
                        lambda row, actor=None: written.append(row["id"]))

    warnings = []
    ok, warnings = supplier_watchdog._save_synced(
        _row(), _row(stock=9), "supplier-watchdog", warnings)

    assert written == [], "the watchdog re-created a product the owner deleted"
    assert ok is False
    assert warnings[0]["code"] == "product_deleted_during_sync"


def test_a_live_product_still_syncs_normally(monkeypatch):
    """The guard must not turn the watchdog off for ordinary products."""
    written = []
    monkeypatch.setattr(catalog_mod, "deleted_product_ids", lambda: set())
    monkeypatch.setattr(catalog_mod, "upsert",
                        lambda row, actor=None: (written.append(row["id"]),
                                                 (row, "updated", True))[1])

    warnings = []
    ok, warnings = supplier_watchdog._save_synced(
        _row(), _row(stock=9), "supplier-watchdog", warnings)

    assert written == ["jau-ghost-1"]
    assert ok is True and warnings == []


def test_the_never_recreate_list_is_honoured_before_the_written_check(monkeypatch):
    """catalog.upsert() answers (None, "permanently-removed", True) for a
    never-re-create row. Read as "saved=False" that would be reported as a
    failed sync and the operator would go looking for a supplier problem."""
    monkeypatch.setattr(catalog_mod, "deleted_product_ids", lambda: set())
    monkeypatch.setattr(catalog_mod, "upsert",
                        lambda row, actor=None: (None, "permanently-removed", True))

    warnings = []
    ok, warnings = supplier_watchdog._save_synced(
        _row(), _row(stock=9), "supplier-watchdog", warnings)

    assert ok is False
    assert warnings[0]["code"] == "product_deleted_during_sync"
    assert "never-re-create" in warnings[0]["reason"]


def test_a_deleted_id_is_skipped_by_both_the_tick_and_the_nightly_sweep(monkeypatch):
    """The night pass touches every linked product, so it is the one most
    likely to run hours after a deletion."""
    monkeypatch.setattr(supplier_watchdog, "enabled", lambda: True)
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "merged",
                        lambda include_hidden=False: [_row(), _row("jau-live-1")])
    monkeypatch.setattr(catalog_mod, "deleted_product_ids", lambda: {"jau-ghost-1"})
    monkeypatch.setattr(supplier_watchdog, "_save_warnings", lambda w: None)
    supplier_watchdog._last_checked.clear()

    for run in (lambda: supplier_watchdog.tick(limit=10, min_interval_seconds=0),
                lambda: supplier_watchdog.nightly_sweep()):
        synced = []
        monkeypatch.setattr(supplier_watchdog, "sync_product",
                            lambda p, actor="supplier-watchdog":
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
        self.rpc_calls = []

    def table(self, name):
        rec = self

        class _T:
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
                return type("Res", (), {"data": list(params["product_ids"])})()
        return _R()


def test_every_named_product_table_is_purged(monkeypatch):
    """products, product_variants, product_prices, product_options and the
    rest must all be swept - a survivor is exactly what the storage sweeper
    later mistakes for live media."""
    rec = _Recorder(rpc_error=RuntimeError("cascade not installed"))
    monkeypatch.setattr(sb, "client", lambda: rec)
    monkeypatch.setattr(sb, "add_deleted_id", lambda pid: True)

    report = sb.hard_delete_products(["jau-x-1"])

    tables = [t for t, _ in rec.deleted]
    for expected in ("products", "product_variants", "product_prices",
                     "product_options", "variant_stock", "product_reviews",
                     "product_views", "featured_products"):
        assert expected in tables, f"{expected} was never purged"
    assert report["deleted"] == ["jau-x-1"]


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


def test_a_missing_cascade_function_still_deletes_the_row(monkeypatch):
    """Deploys that have not run the SQL yet must get a real delete, not a
    silent no-op that reports success."""
    rec = _Recorder(rpc_error=RuntimeError(
        "Could not find the function public.hard_delete_products"))
    monkeypatch.setattr(sb, "client", lambda: rec)
    monkeypatch.setattr(sb, "add_deleted_id", lambda pid: True)

    report = sb.hard_delete_products(["jau-x-5"])

    assert ("products", ("jau-x-5",)) in rec.deleted
    assert report["deleted"] == ["jau-x-5"]


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
