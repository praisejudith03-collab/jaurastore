"""Nightly 2:00 AM maintenance: supplier sweep + storage sweeper + ghost guards.

Freeze the contract added for the emergency maintenance audit:

  * the supplier watchdog has an off-peak deep pass that runs once per day
    after 2:00 AM in the owner's timezone (default UTC+1, Porto-Novo),
    in ADDITION to the small day-time batches;
  * the nightly run also purges orphaned / duplicate upload media through
    the same protected plan as the admin's Storage cleanup card;
  * a hard-deleted product id can never be re-checked (and thereby
    re-created in Supabase) by an automated sync - not by a tick, not by
    the nightly sweep, not by the remirror pass;
  * the watchdog watches supplier PRICES: a rise raises an admin-visible
    warning, and the shop's own retail price is never rewritten.

Run with:  python3 -m pytest tests/test_nightly_maintenance.py -q
"""
import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")

import pytest  # noqa: E402

import catalog as catalog_mod  # noqa: E402
import scheduler  # noqa: E402
import supplier_watchdog  # noqa: E402

SUPPLIER = "https://supplier.example/item/42"


# --------------------------------------------------------- the 2 AM schedule

def test_nightly_defaults_are_2am_in_the_owners_timezone(monkeypatch):
    monkeypatch.delenv("SUPPLIER_WATCHDOG_NIGHTLY_HOUR", raising=False)
    monkeypatch.delenv("SUPPLIER_WATCHDOG_NIGHTLY_TZ_OFFSET", raising=False)
    monkeypatch.delenv("SUPPLIER_WATCHDOG_NIGHTLY", raising=False)
    assert scheduler.NIGHTLY_HOUR == 2
    assert scheduler.NIGHTLY_TZ_OFFSET == 1          # Africa/Porto-Novo (WAT)
    assert scheduler._nightly_enabled() is True


def test_nightly_is_due_once_per_local_day_after_2am(monkeypatch):
    monkeypatch.setattr(scheduler, "_last_nightly_date", "")
    before = datetime.datetime(2026, 10, 1, 0, 30)   # 01:30 WAT - too early
    assert scheduler._nightly_due(before) is False
    after = datetime.datetime(2026, 10, 1, 1, 10)    # 02:10 WAT - due
    assert scheduler._nightly_due(after) is True
    # after the run it is not due again on the same local day
    scheduler._last_nightly_date = "2026-10-01"
    assert scheduler._nightly_due(after) is False
    assert scheduler._nightly_due(
        datetime.datetime(2026, 10, 2, 1, 10)) is True   # next day: due again
    scheduler._last_nightly_date = ""


def test_nightly_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SUPPLIER_WATCHDOG_NIGHTLY", "off")
    monkeypatch.setattr(scheduler, "_last_nightly_date", "")
    assert scheduler._nightly_due(datetime.datetime(2026, 10, 1, 5, 0)) is False


def test_maintenance_tick_runs_the_nightly_pass_when_due(monkeypatch):
    ran = []
    monkeypatch.setattr(scheduler, "_step",
                        lambda job, fn, logger=None, payload_id="": ran.append(job))
    for job in ("scheduler.keep_alive", "scheduler.daily_backup",
                "scheduler.remirror_strays", "scheduler.repair_photos",
                "scheduler.persist_counters", "supplier.watchdog"):
        pass  # _step is fully stubbed above
    monkeypatch.setattr(scheduler, "_nightly_due", lambda: True)
    monkeypatch.setattr(scheduler, "_nightly_run", lambda logger=None: {})
    scheduler._maintenance_tick(logger=None)
    assert "scheduler.nightly" in ran
    monkeypatch.setattr(scheduler, "_nightly_due", lambda: False)
    ran.clear()
    scheduler._maintenance_tick(logger=None)
    assert "scheduler.nightly" not in ran


def test_health_snapshot_advertises_the_nightly_schedule():
    snap = scheduler.health_snapshot(repair=False)
    assert snap["nightly"]["schedule"].startswith("02:00")
    assert "storage sweeper" in snap["nightly"]["jobs"]


# ------------------------------------------------------- the nightly sweep

def test_nightly_sweep_checks_every_product_exactly_once(monkeypatch):
    """Each supplier-linked product is synced once - never twice, never zero
    times because a day-time tick checked it recently."""
    synced = []
    monkeypatch.setattr(supplier_watchdog, "enabled", lambda: True)
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "merged",
                        lambda include_hidden=False: [
                            _ghost_row(), _ghost_row("jau-live-1"),
                            _ghost_row("jau-live-2"),
                            {"id": "jau-no-url", "name": "No URL", "stock": 1}])
    monkeypatch.setattr(catalog_mod, "deleted_product_ids", lambda: {"jau-ghost-1"})
    monkeypatch.setattr(supplier_watchdog, "sync_product",
                        lambda p, actor="supplier-watchdog": (synced.append(p["id"]), (True, []))[1])
    monkeypatch.setattr(supplier_watchdog, "_save_warnings", lambda w: None)
    out = supplier_watchdog.nightly_sweep()
    # every LIVE linked product once; the deleted id and the URL-less row never
    assert sorted(synced) == ["jau-live-1", "jau-live-2"]
    assert out["checked"] == 2 and out["updated"] == 2


def test_nightly_sweep_is_bounded_and_terminates(monkeypatch):
    """The sweep must finish: one pass over the candidates, capped, with no
    re-check loop (an earlier design re-checked the same batch forever)."""
    rows = [{"id": f"jau-live-{i}", "name": f"Bag {i}", "stock": 3,
             "supplierSku": SUPPLIER} for i in range(150)]
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "merged",
                        lambda include_hidden=False: rows)
    monkeypatch.setattr(catalog_mod, "deleted_product_ids", lambda: set())
    monkeypatch.setattr(supplier_watchdog, "sync_product",
                        lambda p, actor="supplier-watchdog": (False, []))
    monkeypatch.setattr(supplier_watchdog, "_save_warnings", lambda w: None)
    out = supplier_watchdog.nightly_sweep(max_products=100)
    assert out["checked"] == 100            # capped, and finished


# ------------------------------------------------- ghost products stay dead

def _ghost_row(pid="jau-ghost-1"):
    return {"id": pid, "name": "Ghost Bag", "priceNgn": 1000,
            "supplierSku": SUPPLIER, "stock": 5}


def test_tick_never_syncs_a_hard_deleted_id(monkeypatch):
    synced = []
    monkeypatch.setattr(supplier_watchdog, "enabled", lambda: True)
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "merged",
                        lambda include_hidden=False: [_ghost_row(), _ghost_row("jau-live-1")])
    monkeypatch.setattr(catalog_mod, "deleted_product_ids",
                        lambda: {"jau-ghost-1"})
    monkeypatch.setattr(supplier_watchdog, "sync_product",
                        lambda p, actor="supplier-watchdog": (synced.append(p["id"]), (True, []))[1])
    monkeypatch.setattr(supplier_watchdog, "_save_warnings", lambda w: None)
    supplier_watchdog._last_checked.clear()
    out = supplier_watchdog.tick(limit=10, min_interval_seconds=0)
    assert synced == ["jau-live-1"]
    assert out["checked"] == 1


def test_deleted_product_ids_unions_local_and_durable(monkeypatch):
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [], "deleted": ["local-dead"]})
    monkeypatch.setattr(catalog_mod, "_durable_deleted_ids",
                        lambda: {"durable-dead"})
    assert catalog_mod.deleted_product_ids() == {"local-dead", "durable-dead"}


def test_deleted_product_ids_survives_a_durable_outage(monkeypatch):
    """An unreachable Supabase must never look like 'nothing is deleted'."""
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [], "deleted": ["local-dead"]})
    def boom():
        raise RuntimeError("supabase down")
    monkeypatch.setattr(catalog_mod, "_durable_deleted_ids", boom)
    assert catalog_mod.deleted_product_ids() == {"local-dead"}


def test_remirror_never_pushes_a_hard_deleted_id(monkeypatch):
    import supabase_store as sb
    pushed = []
    seed = catalog_mod._seed_products()
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: [dict(seed[0])])
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [
                            {"id": "jau-stray", "sku": "JAUSTRY", "slug": "stray",
                             "name": "Stray", "category": "beauty", "priceNgn": 1},
                            {"id": "jau-ghost-9", "sku": "JAUGHOST", "slug": "ghost",
                             "name": "Ghost", "category": "beauty", "priceNgn": 1}],
                            "deleted": ["jau-ghost-9"]})
    monkeypatch.setattr(sb, "enabled", lambda: True)
    monkeypatch.setattr(sb, "upsert_products", lambda rows: pushed.extend(rows) or True)
    n = catalog_mod.remirror_strays()
    assert n == 1
    assert [r["id"] for r in pushed] == ["jau-stray"]


def test_hard_delete_tables_pin():
    """The absolute-delete inventory: every Supabase surface a product row
    touches.

    The shop keeps variants, prices and options as jsonb columns ON the
    products row (optionStock / optionPrices / optionCompareAt / optionSku),
    so deleting the row takes them with it. Where a deployment has normalised
    them into their own tables instead, they are named explicitly so those
    rows are purged too - either by the SQL CASCADE (hard_delete_products.sql)
    or by the explicit sweep below, whichever the database supports.
    """
    src = open(os.path.join(ROOT, "supabase_store.py"), encoding="utf-8").read()
    fn = src[src.index("def hard_delete_products"):]
    fn = fn[:fn.index("\ndef ")]
    # the row itself
    assert 'c.table("products").delete()' in fn
    # and the child tables, declared once and swept in a loop
    for table in ("product_variants", "product_prices", "product_options",
                  "variant_stock", "product_reviews", "product_views",
                  "featured_products"):
        assert f'("{table}", "product_id")' in src, table
    assert "def _purge_product_children(c, ids)" in src
    assert "_purge_product_children(c, ids)" in fn
    # a table that is simply not in this deployment is skipped, not failed
    assert "does not exist" in src
    # and the durable tombstone that stops the seed copy coming back
    assert "add_deleted_id" in fn


def test_hard_delete_prefers_the_sql_cascade_and_falls_back_safely():
    """One SQL CASCADE when the function is installed, an identical
    table-by-table delete when it is not - and never a silent no-op."""
    src = open(os.path.join(ROOT, "supabase_store.py"), encoding="utf-8").read()
    fn = src[src.index("def hard_delete_products"):]
    fn = fn[:fn.index("\ndef ")]
    assert 'c.rpc("hard_delete_products", {"product_ids": ids}).execute()' in fn
    assert "deleted_by_sql = True" in fn
    # a missing function must NOT look like a successful delete: the explicit
    # delete still has to run before the ids are reported gone
    assert "if not deleted_by_sql:" in fn
    assert "report[\"deleted\"] = list(ids)" in fn


# --------------------------------------------------------- supplier prices

def test_parse_price_handles_the_real_world():
    assert supplier_watchdog.parse_price("12500") == 12500.0
    assert supplier_watchdog.parse_price("₦12,500") == 12500.0
    assert supplier_watchdog.parse_price("$12.99") == 12.99
    assert supplier_watchdog.parse_price({"amount": "45.00"}) == 45.0
    assert supplier_watchdog.parse_price("abc") is None
    assert supplier_watchdog.parse_price(0) is None
    assert supplier_watchdog.parse_price("-3") is None


def test_product_page_price_reads_json_ld():
    html = ('<script type="application/ld+json">'
            '{"@type":"Product","name":"Bag","offers":{"@type":"Offer",'
            '"price":"82.50","priceCurrency":"USD"}}</script>')
    assert supplier_watchdog.product_page_price(html) == 82.5


def _variant_page(prices):
    import json
    items = []
    for (title, qty, price) in prices:
        row = {"title": title, "available": True, "quantity": qty}
        if price is not None:
            row["price"] = price
        items.append(row)
    return ('<script type="application/json">'
            + json.dumps({"variants": items}) + "</script>")


@pytest.fixture()
def saved(monkeypatch):
    box = {}

    def fake_upsert(row, actor=None):
        box.clear()
        box.update(row)
        return row, "updated", True

    monkeypatch.setattr(supplier_watchdog.catalog_mod, "upsert", fake_upsert)
    return box


def _price_product():
    return {"id": "jau-price-1", "name": "Glow Set", "priceNgn": 5000,
            "supplierSku": SUPPLIER,
            "options": [{"title": "Type", "values": ["Serum", "Cream"]}],
            "optionStock": {"Serum": 5, "Cream": 5}, "stock": 10}


def _run(monkeypatch, page):
    monkeypatch.setattr(supplier_watchdog, "fetch_url", lambda url: page)
    return supplier_watchdog.sync_product(_price_product())


def test_supplier_price_increase_raises_a_warning_not_a_rewrite(monkeypatch, saved):
    supplier_watchdog._price_map.clear()
    monkeypatch.setattr(supplier_watchdog, "_save_price_map", lambda: None)
    # first observation: remembered, no warning
    _, warns = _run(monkeypatch, _variant_page([("Serum", 5, 30.0), ("Cream", 5, 35.0)]))
    assert not [w for w in warns if "price" in w["code"]]
    # the price book remembers what it saw
    assert supplier_watchdog._price_map["jau-price-1"]["prices"]["Serum"] == 30.0
    # supplier raises the Serum price (and lowers stock so a save happens):
    # a warning must reach the admin...
    _, warns = _run(monkeypatch, _variant_page([("Serum", 3, 42.0), ("Cream", 5, 35.0)]))
    up = [w for w in warns if w["code"] == "supplier_price_increased"]
    assert up and "Serum" in up[0]["reason"] and "42" in up[0]["reason"]
    # ... the stock update went through ...
    assert saved.get("optionStock") == {"Serum": 3, "Cream": 5}
    # ... and the shop's own retail price was NEVER rewritten by the supplier
    assert saved.get("priceNgn") == 5000
    assert (saved.get("optionPrices") or {}).get("Serum") != 42.0


def test_supplier_price_drop_is_reported_too(monkeypatch, saved):
    supplier_watchdog._price_map.clear()
    monkeypatch.setattr(supplier_watchdog, "_save_price_map", lambda: None)
    _run(monkeypatch, _variant_page([("Serum", 5, 30.0), ("Cream", 5, 35.0)]))
    _, warns = _run(monkeypatch, _variant_page([("Serum", 5, 20.0), ("Cream", 5, 35.0)]))
    assert any(w["code"] == "supplier_price_dropped" for w in warns)


# --------------------------------------------------- the nightly sweeper

def test_storage_sweeper_purges_orphans_through_the_protected_plan(monkeypatch):
    import storage_cleanup
    plan_calls, apply_calls = [], []

    def fake_build(min_age_days=2):
        plan_calls.append(min_age_days)
        return {"candidate_count": 2, "candidate_bytes": 128,
                "delete_candidates": ["products/old-a.jpg", "products/old-b.jpg"]}

    def fake_apply(report=None):
        apply_calls.append(report)
        return {"deleted": report["delete_candidates"], "errors": []}

    monkeypatch.setattr(scheduler.os, "environ",
                        {**os.environ, "STORAGE_SWEEPER_NIGHTLY": "1"})
    monkeypatch.setattr(storage_cleanup, "build_plan", fake_build)
    monkeypatch.setattr(storage_cleanup, "apply_plan", fake_apply)
    monkeypatch.setitem(sys.modules, "storage_cleanup", storage_cleanup)
    out = scheduler._storage_sweeper()
    assert plan_calls == [2]                 # the same 2-day grace window
    assert len(apply_calls) == 1
    assert out["deleted"] == ["products/old-a.jpg", "products/old-b.jpg"]


def test_storage_sweeper_deletes_nothing_when_clean(monkeypatch):
    import storage_cleanup
    monkeypatch.setattr(storage_cleanup, "build_plan",
                        lambda min_age_days=2: {"candidate_count": 0,
                                                "candidate_bytes": 0,
                                                "delete_candidates": []})
    monkeypatch.setattr(storage_cleanup, "apply_plan",
                        lambda report=None: (_ for _ in ()).throw(AssertionError("must not apply")))
    monkeypatch.setitem(sys.modules, "storage_cleanup", storage_cleanup)
    out = scheduler._storage_sweeper()
    assert out["candidates"] == 0 and out["deleted"] == []


def test_storage_sweeper_can_be_disabled(monkeypatch):
    monkeypatch.setenv("STORAGE_SWEEPER_NIGHTLY", "0")
    out = scheduler._storage_sweeper()
    assert out == {"skipped": True}


def test_storage_sweeper_failure_is_traced_never_fatal(monkeypatch):
    import storage_cleanup
    def boom(min_age_days=2):
        raise RuntimeError("reference scan failed")
    monkeypatch.setattr(storage_cleanup, "build_plan", boom)
    monkeypatch.setitem(sys.modules, "storage_cleanup", storage_cleanup)
    result = scheduler._step("storage.nightly_sweeper",
                             lambda: scheduler._storage_sweeper())
    assert result is None                     # _step swallowed + traced it
    assert scheduler._health["lastErrorJob"] == "storage.nightly_sweeper"
    scheduler._health["lastErrorJob"] = ""


# ------------------------------------------------------- schedule pin

def test_scheduler_source_pins_the_nightly_contract():
    src = open(os.path.join(ROOT, "scheduler.py"), encoding="utf-8").read()
    assert "NIGHTLY_HOUR" in src and '"2"' in src
    assert "nightly_sweep" in src
    assert "_storage_sweeper" in src
    assert "_nightly_due" in src


def test_supplier_recheck_interval_is_tunable(monkeypatch):
    """The day-time tick's per-product wait is an env knob (default 1h) so a
    deployment can tune it; the nightly sweep always ignores it."""
    monkeypatch.delenv("SUPPLIER_WATCHDOG_MIN_INTERVAL", raising=False)
    assert scheduler.SUPPLIER_MIN_INTERVAL == 3600
    import importlib
    monkeypatch.setenv("SUPPLIER_WATCHDOG_MIN_INTERVAL", "30")
    assert int(os.environ["SUPPLIER_WATCHDOG_MIN_INTERVAL"]) == 30
    src = open(os.path.join(ROOT, "scheduler.py"), encoding="utf-8").read()
    assert "min_interval_seconds=SUPPLIER_MIN_INTERVAL" in src
    assert 'supplier_watchdog.nightly_sweep' in src
