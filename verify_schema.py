"""Read-only verification that the production Supabase schema is applied.

The migration writes to products.image_url and to Storage, but it depends on
the schema being present first. This checks that without writing anything:
for every required table it asks PostgREST for exactly the columns the
application uses. A missing table or column makes PostgREST error, which is
the signal - so this needs no information_schema access and no psql
connection, just the two protected secrets the workflow already has.

It never prints the service-role key. It prints table names, column names and
row counts only.

Usage:
    python3 verify_schema.py              # live check against Supabase
    python3 verify_schema.py --json out.json
    python3 verify_schema.py --dry-run    # no network; verifies this file's
                                          # own expectations only
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REQUIRED_TABLES = (
    "site_settings",
    "products",
    "categories",
    "orders",
    "receipts",
    "admin_users",
    "admin_reset_tokens",
    "coupons",
    "coupon_uses",
    "referral_codes",
    "referral_uses",
    "delivery_zones",
    "product_reviews",
)

# The columns each table must answer for. Requesting them is the check: if one
# is absent, PostgREST rejects the query rather than returning a partial row.
REQUIRED_COLUMNS = {
    "products": ("id", '"legacyId"', "name", "category", '"priceNgn"', '"priceCfa"',
                 '"compareNgn"', '"compareCfa"', "image_url", "images",
                 "stock_quantity", "description", "featured", "online", "updated_at"),
    "product_reviews": ("product_id", "order_id", "email", "name", "rating", "title", "body",
                        "hidden", "created_at", "updated_at"),
    "coupon_uses": ("code", "email", "order_id", "percent", "used_at"),
    "coupons": ("code", "percent", "kind", "active", "max_uses", "uses", "expires_at"),
    "referral_codes": ("code", "email", "name", "uses", "reward_issued"),
    "referral_uses": ("code", "order_id"),
    "delivery_zones": ("id", "name", "currency", "fare_min", "fare_max", "kind", "active", "sort_order"),
}

# Constraints that a column probe cannot see. Verified against the SQL file,
# which is the thing actually applied to production.
CONSTRAINTS = (
    ("coupon_uses", "unique (code, order_id)",
     "the same coupon must not count twice for one order"),
    ("product_reviews", "unique (product_id, email)",
     "one review per customer per product"),
)

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "supabase_schema.sql")


def check_live(client):
    """Probe each table for its required columns. Returns a report dict."""
    report = {"tables": {}, "missing_tables": [], "missing_columns": {},
              "ok": True}
    for table in REQUIRED_TABLES:
        cols = REQUIRED_COLUMNS.get(table)
        select = ",".join(cols) if cols else "*"
        try:
            res = client.table(table).select(select).limit(1).execute()
        except Exception as exc:
            msg = str(exc)
            report["tables"][table] = {"present": False, "error": msg[:300]}
            report["missing_tables"].append(table)
            report["ok"] = False
            continue
        rows = getattr(res, "data", None)
        if rows is None:
            rows = (res or {}).get("data") or []
        report["tables"][table] = {"present": True, "probed": len(rows)}
    return report


def check_constraints(schema_text=None):
    """Verify the unique constraints are declared in the SQL that gets applied."""
    if schema_text is None:
        with open(SCHEMA_FILE, encoding="utf-8") as fh:
            schema_text = fh.read()
    out = []
    low = schema_text.lower()
    for table, needle, why in CONSTRAINTS:
        present = needle in low
        out.append({"table": table, "constraint": needle, "present": present,
                    "why": why})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="write the report to this path")
    ap.add_argument("--dry-run", action="store_true",
                    help="skip the network probe; check the SQL file only")
    args = ap.parse_args(argv)

    report = {"schema_file": os.path.basename(SCHEMA_FILE),
              "required_tables": list(REQUIRED_TABLES)}

    report["constraints"] = check_constraints()
    bad_constraints = [c for c in report["constraints"] if not c["present"]]
    if bad_constraints:
        report["ok"] = False
    else:
        report.setdefault("ok", True)

    if args.dry_run:
        report["network_probe"] = "skipped (--dry-run)"
    else:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url or not key:
            report["network_probe"] = "skipped: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY unset"
            report["ok"] = False
        else:
            try:
                import supabase_store
                client = supabase_store.client()
                if client is None:
                    report["network_probe"] = "skipped: client() returned None"
                    report["ok"] = False
                else:
                    live = check_live(client)
                    report.update(live)
                    report["network_probe"] = "completed"
            except Exception as exc:
                report["network_probe"] = f"failed: {exc}"
                report["ok"] = False

    print("=== SCHEMA VERIFICATION ===")
    print(f"required tables          : {len(REQUIRED_TABLES)}")
    for c in report["constraints"]:
        print(f"  {'OK ' if c['present'] else 'MISSING'} {c['table']}: "
              f"{c['constraint']}  ({c['why']})")
    if report.get("missing_tables"):
        print(f"missing tables           : {report['missing_tables']}")
    for t, info in (report.get("tables") or {}).items():
        if not info.get("present"):
            print(f"  MISSING {t}: {info.get('error', '')[:160]}")
    print(f"network probe            : {report['network_probe']}")
    print(f"ok                       : {report['ok']}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        print(f"report written to {args.json}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
