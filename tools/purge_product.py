#!/usr/bin/env python3
"""Permanently delete a product from Supabase PostgreSQL + Storage.

This is a HARD delete, not a tombstone: the products-table row is removed,
every file it referenced is purged from the Supabase Storage bucket, and the
id is recorded in the durable deleted-ids list so no mirror, backup restore
or bulk re-import can bring it back.

Usage
-----
    # purge everything listed in catalog.PERMANENTLY_REMOVED_IDS
    # (currently the "100L storage bag", wix-002)
    python3 tools/purge_product.py

    # purge specific ids as well
    python3 tools/purge_product.py wix-002 jau-abc123

Requires SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in the environment. Without
them the script reports what it WOULD do and exits 0 - it never fails a
deploy. A product listed in catalog.PERMANENTLY_REMOVED_* also stays filtered
out of every catalogue read, so the storefront is already clean whether or
not this script has run.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog  # noqa: E402
import supabase_store  # noqa: E402


def main(argv):
    extra = [a.strip() for a in argv[1:] if a.strip()]
    ids = sorted(set(catalog.PERMANENTLY_REMOVED_IDS) | set(extra))
    print("purging:", ", ".join(ids))

    if not supabase_store.enabled():
        print("Supabase is not configured - nothing was deleted remotely.")
        print("The storefront still hides these products (catalog.merged "
              "filters them on every read).")
        return 0

    result = supabase_store.hard_delete_products(ids)
    print(f"rows deleted : {', '.join(result['deleted']) or 'none'}")
    print(f"files purged : {result['files']}")
    for err in result["errors"]:
        print("warning:", err)

    local = catalog.purge_permanently_removed(actor="purge_product.py")
    for err in local.get("errors") or []:
        print("warning:", err)
    print("done - these products can no longer be served or re-created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
