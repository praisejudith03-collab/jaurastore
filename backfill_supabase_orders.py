#!/usr/bin/env python3
"""Backfill the Supabase orders / receipts tables from the shop's SQLite.

Every live checkout written before the mirror was fixed answered 500 after
its SQLite insert (see supabase_store.create_order), so the Supabase `orders`
table never received a single row and the boot-time restore had nothing to
restore. This one-shot script copies what is already on the Render disk into
Supabase so the two copies agree again:

    python3 backfill_supabase_orders.py                      # both tables
    python3 backfill_supabase_orders.py --table orders       # orders only
    python3 backfill_supabase_orders.py --table receipts     # receipts only
    python3 backfill_supabase_orders.py --dry-run            # counts, no writes

It reads the shop's SQLite (DB_PATH from the environment, like the app) and
upserts through supabase_store.client(). Rows are paged 200 at a time with a
print per page. Plain idempotent upserts: no deletes, no --reset - running it
twice changes nothing. New orders mirror themselves through api.py, so this
is only ever needed once per table.
"""
import os, sys, json, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config              # noqa: E402
from db import query                   # noqa: E402

PAGE = 200


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _get(row, key, default=""):
    """sqlite3.Row has no .get; old databases may also lack newer columns."""
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def order_row(row, now):
    """The exact keys api.py's checkout mirror sends; payload json-dumped."""
    payload = _get(row, "payload")
    if not isinstance(payload, str):          # SQLite keeps it pre-dumped
        payload = json.dumps(payload, ensure_ascii=False)
    return {
        "id": _get(row, "id"), "email": _get(row, "email"),
        "customer_name": _get(row, "customer_name"),
        "phone": _get(row, "phone"), "country": _get(row, "country"),
        "city": _get(row, "city"), "zone": _get(row, "zone"),
        "address": _get(row, "address"), "note": _get(row, "note"),
        "payment": _get(row, "payment"), "proof_url": _get(row, "proof_url"),
        "items_count": _get(row, "items_count", 0),
        "total": _get(row, "total"), "currency": _get(row, "currency"),
        "source": _get(row, "source", "web"),
        "status": _get(row, "status", "pending"),
        "payload": payload, "at": _get(row, "at"), "updated_at": now,
    }


def receipt_row(row, now):
    """payment_proofs columns mapped onto what create_receipt receives."""
    return {
        "id": _get(row, "order_id"), "order_id": _get(row, "order_id"),
        "name": _get(row, "name"), "phone": _get(row, "phone"),
        "email": _get(row, "email"), "method": _get(row, "method"),
        "items": _get(row, "items"), "quantity": _get(row, "quantity"),
        "amount": _get(row, "amount"), "note": _get(row, "note"),
        "file_url": _get(row, "file_url"), "file_name": _get(row, "file_name"),
        "file_size": _get(row, "file_size", 0),
        "file_type": _get(row, "mime"),
        "emailed": bool(_get(row, "emailed", 0)),
        "email_info": _get(row, "email_info"),
        "created_at": now,
    }


def _backfill(sb, table_sql, table_sb, shape, label, dry_run):
    """Page the SQLite table 200 rows at a time and upsert into Supabase."""
    total = 0
    offset = 0
    while True:
        try:
            rows = query(f"SELECT * FROM {table_sql} ORDER BY id "
                         f"LIMIT {PAGE} OFFSET ?", (offset,))
        except Exception as exc:
            print(f"[backfill] could not read {table_sql}: {exc}")
            sys.exit(1)
        if not rows:
            break
        page = [shape(r, _now()) for r in rows]
        total += len(page)
        print(f"{label}: page of {len(page)} (rows {offset + 1}-"
              f"{offset + len(page)})" + (" [dry run]" if dry_run else ""))
        if not dry_run:
            try:
                sb.table(table_sb).upsert(page).execute()
            except Exception as exc:
                print(f"[backfill] {table_sb} upsert failed at offset "
                      f"{offset}: {exc}")
                sys.exit(1)
        offset += len(page)
        if len(page) < PAGE:
            break
    return total


def main():
    dry_run = "--dry-run" in sys.argv
    table = "both"
    for i, arg in enumerate(sys.argv):
        if arg == "--table" and i + 1 < len(sys.argv):
            table = sys.argv[i + 1]
        elif arg.startswith("--table="):
            table = arg.split("=", 1)[1]
    if table not in ("orders", "receipts", "both"):
        print("--table must be one of: orders, receipts, both.")
        sys.exit(1)

    sb = None
    if not dry_run:
        if not (Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY):
            print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY first "
                  "(or pass --dry-run to only count).")
            sys.exit(1)
        from supabase_store import client
        sb = client()
        if sb is None:
            print("Could not initialise the Supabase client.")
            sys.exit(1)

    orders = receipts = 0
    if table in ("orders", "both"):
        orders = _backfill(sb, "orders", "orders", order_row, "orders", dry_run)
    if table in ("receipts", "both"):
        receipts = _backfill(sb, "payment_proofs", "receipts", receipt_row,
                             "receipts", dry_run)

    verb = "would backfill" if dry_run else "backfilled"
    print(f"{verb} {orders} order(s) and {receipts} receipt(s).")
    if dry_run:
        print("Dry run: nothing was written to Supabase.")
    else:
        print("New orders now mirror themselves through the checkout route.")


if __name__ == "__main__":
    main()
