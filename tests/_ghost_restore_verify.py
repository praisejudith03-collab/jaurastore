"""Live check: a hard-deleted product is never re-created, and the 2:00 AM
pass is actually wired.

Runs against the same environment the app is serving (DB_PATH, CATALOG_PATH,
...), so this exercises the real supplier watchdog, the real scheduler and the
real durable tombstone list rather than doubles.

Run with the app's env exported, e.g.
  DB_PATH=... CATALOG_PATH=... .venv/bin/python tests/_ghost_restore_verify.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import api as api_mod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import scheduler  # noqa: E402
import supplier_watchdog  # noqa: E402
import supabase_store as sb  # noqa: E402

GHOST = "jau-verify-ghost-live-1"
SUPPLIER = "https://supplier.example/item/ghost-1"

results = []


def check(name, ok, detail=""):
    results.append((bool(ok), name))
    print(("PASS  " if ok else "FAIL  ") + name + (("  ->  " + str(detail)) if detail else ""))


def _has(pid):
    return any(str(p.get("id")) == pid for p in catalog_mod.merged(include_hidden=True))


def main():
    print("--- the product ---")
    saved, action, _ = catalog_mod.upsert(
        {"id": GHOST, "name": "Ghost Live Piece", "category": "household",
         "priceNgn": 5000, "stock": 5, "online": True,
         "supplierSku": SUPPLIER, "supplierUrl": SUPPLIER},
        actor="verify")
    check("created the product to delete", bool(saved), action)
    check("it is live", _has(GHOST))

    print("--- the absolute delete ---")
    # Use exactly the branch DELETE /api/admin/products/<id> takes: the
    # Supabase SQL cascade in production, the local tombstone otherwise.
    if catalog_mod._prod_source():
        report = sb.hard_delete_products([GHOST])
        check("hard delete reported it removed", GHOST in (report.get("deleted") or []), report)
    else:
        catalog_mod.remove(GHOST, actor="verify")
        check("delete accepted (local tombstone path)", True)
    check("it is gone from the catalogue", not _has(GHOST))
    check("it is on the durable deleted list", GHOST in catalog_mod.deleted_product_ids(),
          sorted(catalog_mod.deleted_product_ids())[:5])

    print("--- the ghost-restore race ---")
    # Reproduce the exact hazard: the watchdog had already picked this product
    # and is holding a copy when the owner deletes it. Force the stale copy
    # back into the catalogue so the ONLY thing that can stop the re-create is
    # the guard inside _save_synced.
    stale = {"id": GHOST, "name": "Ghost Live Piece", "category": "household",
             "priceNgn": 5000, "stock": 5, "online": True,
             "supplierSku": SUPPLIER, "supplierUrl": SUPPLIER}
    real_merged = catalog_mod.merged
    catalog_mod.merged = lambda include_hidden=False: [dict(stale)] + list(
        real_merged(include_hidden=include_hidden))
    supplier_watchdog.catalog_mod = catalog_mod
    monkey = True
    try:
        warnings = []
        ok, warnings = supplier_watchdog._save_synced(
            stale, dict(stale, stock=3), "supplier-watchdog", warnings)
        check("the watchdog refused to write the deleted id", ok is False, ok)
        codes = [w.get("code") for w in warnings]
        check("and said why", "product_deleted_during_sync" in codes, codes)
    finally:
        catalog_mod.merged = real_merged

    check("still gone after the attempt", not _has(GHOST))

    print("--- the tick and the 2:00 AM sweep ---")
    supplier_watchdog._last_checked.clear()
    for label, run in (
        ("tick", lambda: supplier_watchdog.tick(limit=10, min_interval_seconds=0)),
        ("nightly_sweep", lambda: supplier_watchdog.nightly_sweep()),
    ):
        supplier_watchdog._last_checked.clear()
        out = run()
        check(f"{label} ran without error", isinstance(out, dict), out)
        check(f"{label} did not bring the product back", not _has(GHOST))

    # Three more rounds, with the stale copy re-injected each time, to be sure
    # nothing caches its way around the guard.
    for i in range(3):
        catalog_mod.merged = lambda include_hidden=False: [dict(stale)]
        supplier_watchdog._last_checked.clear()
        supplier_watchdog.tick(limit=10, min_interval_seconds=0)
        catalog_mod.merged = real_merged
        if _has(GHOST):
            break
    check("still gone after repeated automated passes", not _has(GHOST))

    print("--- the 2:00 AM schedule ---")
    check("the nightly hour is 2 AM", scheduler.NIGHTLY_HOUR == 2, scheduler.NIGHTLY_HOUR)
    check("the nightly pass is enabled", scheduler._nightly_enabled())

    import datetime
    # _nightly_due() takes UTC and adds the owner's offset (UTC+1) itself, so
    # 01:00 UTC IS 02:00 in Porto-Novo. Pass UTC, unadjusted.
    utc = datetime.datetime
    check("not due at 01:59 local",
          scheduler._nightly_due(utc(2026, 10, 1, 0, 59)) is False)
    check("due at 02:00 local",
          scheduler._nightly_due(utc(2026, 10, 1, 1, 0)) is True)

    ran = {}
    scheduler._supplier_nightly = lambda logger=None: ran.setdefault("supplier", True)
    scheduler._storage_sweeper = lambda logger=None: ran.setdefault("sweeper", True)
    scheduler._last_nightly_date = ""
    scheduler._nightly_run()
    check("the 2 AM pass runs the supplier sweep", ran.get("supplier") is True, ran)
    check("the 2 AM pass runs the storage sweeper", ran.get("sweeper") is True, ran)
    # ...and it does not re-run every 5 minutes. Pinned to a fixed date rather
    # than "today", so the result does not depend on what time of day this
    # script happens to run at.
    scheduler._last_nightly_date = "2026-10-01"
    check("it does not re-run every 5 minutes",
          scheduler._nightly_due(utc(2026, 10, 1, 8, 0)) is False)
    check("but it is due again the next day",
          scheduler._nightly_due(utc(2026, 10, 2, 1, 0)) is True)

    print("")
    failed = [r for r in results if not r[0]]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED: " + "; ".join(f[1] for f in failed))
        sys.exit(1)
    print("")


if __name__ == "__main__":
    main()
