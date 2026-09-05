#!/usr/bin/env python3
"""Push the whole live catalogue into the Supabase products table.

After the PR-38 dedupe fix, the products table should hold the ENTIRE live
catalogue - not only the rows saved through the admin portal. This one-shot
script pushes catalog.merged(include_hidden=True) (seed products + every
admin edit; soft-deleted ids are already excluded by merged(); offline
products ARE pushed - online=false is stored, not deleted) into Supabase:

    python3 push_catalog_to_supabase.py            # push the live catalogue
    python3 push_catalog_to_supabase.py --dry-run  # print counts, no writes

Plain idempotent upserts in pages of 200 through
supabase_store.upsert_products, with a print per page: existing rows are
updated by id, missing rows are inserted. No replace_all, no deletes, no
reset - tombstoned rows (source 'deleted'/'replaced') are untouched, and a
second run changes nothing. Exits 1 on any hard failure (credentials
missing, client missing, a page failing) and always prints sent vs
reported, so a partial run is visible and safe to re-run.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config              # noqa: E402

import catalog                         # noqa: E402

PAGE = 200


def select_products():
    """The live catalogue the products table should hold.

    catalog.merged(include_hidden=True) is the single source of truth for
    what is live (Supabase rows + seed + local overrides, deduped, minus
    soft-deletes). Rows are copied and sorted by id so page boundaries are
    deterministic across runs, and rows without an id are dropped (they
    could not be upserted anyway).
    """
    rows = catalog.merged(include_hidden=True) or []
    out = []
    for p in rows:
        if not p or not str(p.get("id") or "").strip():
            continue
        out.append(dict(p))
    out.sort(key=lambda p: str(p.get("id")))
    return out


def pages(rows, size=PAGE):
    """Split rows into consecutive pages of at most ``size`` (200)."""
    return [rows[i:i + size] for i in range(0, len(rows or []), size)]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in argv

    rows = select_products()
    pgs = pages(rows)
    print(f"[push-catalog] live catalogue: {len(rows)} product(s) in "
          f"{len(pgs)} page(s) of <= {PAGE}"
          + (" [dry run]" if dry_run else ""))
    if dry_run:
        print("[push-catalog] dry run: nothing was written to Supabase.")
        return

    if not (Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY):
        print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY first "
              "(or pass --dry-run to only count).")
        sys.exit(1)
    from supabase_store import client, upsert_products
    if client() is None:
        print("Could not initialise the Supabase client.")
        sys.exit(1)

    sent = reported = 0
    for i, page in enumerate(pgs, start=1):
        try:
            ok = upsert_products(page)
        except Exception as exc:
            print(f"[push-catalog] page {i}/{len(pgs)} failed: {exc}")
            print(f"[push-catalog] sent {sent} product(s); stored {reported}. "
                  "Re-run to retry - upserts are idempotent.")
            sys.exit(1)
        sent += len(page)
        if ok:
            reported += len(page)
            print(f"[push-catalog] page {i}/{len(pgs)}: {len(page)} stored "
                  f"(rows {sent - len(page) + 1}-{sent})")
        else:
            print(f"[push-catalog] page {i}/{len(pgs)}: supabase_store "
                  "reported a failure - stopping")
            print(f"[push-catalog] sent {sent} product(s); stored {reported}. "
                  "Re-run to retry - upserts are idempotent.")
            sys.exit(1)

    print(f"[push-catalog] sent {sent} product(s); supabase reported "
          f"{reported} stored.")
    print("[push-catalog] done. The products table now mirrors the live "
          "catalogue.")


if __name__ == "__main__":
    main()
