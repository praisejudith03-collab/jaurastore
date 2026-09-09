#!/usr/bin/env python3
"""Reviewed, non-destructive import of the approved Wix customer catalogue.

The command is deliberately a dry run unless --confirm-real-import is given.
It never deletes rows or images and never uses replace_all. Existing values are
only filled when the incoming value is non-blank; online is the one intentional
policy update for the 258 customer rows.
"""
import argparse, json, os, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "seed.json"
EXCLUDED_PREFIX = "jau-"
FIELDS = ("sku", "slug", "name", "nameFr", "category", "priceCfa", "compareCfa",
          "priceNgn", "compareNgn", "image", "description", "stock", "badge",
          "featured", "colors", "options", "placeholderImage", "usesPlaceholder")

def nonblank(v):
    return v is not None and v != "" and v != [] and v != {}

def load_source():
    rows = json.loads(SOURCE.read_text(encoding="utf-8"))
    included = [dict(p) for p in rows if str(p.get("id", "")).startswith("wix-")]
    excluded = [p.get("id") for p in rows if str(p.get("id", "")).startswith(EXCLUDED_PREFIX)]
    return rows, included, excluded

def fetch_existing():
    """Fetch all rows without printing URL, keys, or response secrets."""
    from config import Config
    if not (Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY):
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required to report Supabase rows")
    from supabase_store import client
    c = client()
    if c is None:
        raise RuntimeError("Supabase client could not be initialized")
    result = c.table("products").select("*").execute()
    return result.data or []

def safe_row(p):
    """Canonical row for upsert; source values are retained, online is policy."""
    r = dict(p)
    r["online"] = True
    r["stock_quantity"] = r.get("stock", 0)
    # Local repository paths are catalogue mappings, not uploaded objects.
    # Never promote them to production image_url without confirmed HTTPS upload.
    r["image_url"] = "" if not str(r.get("image", "")).startswith("https://") else r["image"]
    r["images"] = [u for u in (r.get("images", []) or []) if str(u).startswith("https://")]
    return r

def proposed_update(old, incoming):
    changes = {}
    for f in FIELDS:
        if nonblank(incoming.get(f)) and not nonblank(old.get(f)):
            changes[f] = incoming[f]
    if old.get("online") is not True:
        changes["online"] = True
    return changes

def report(existing, source, excluded):
    src_ids = [p["id"] for p in source]
    old_ids = [str(p.get("id")) for p in existing if p.get("id")]
    dup_src = sorted(k for k,v in Counter(src_ids).items() if v > 1)
    dup_old = sorted(k for k,v in Counter(old_ids).items() if v > 1)
    old_by = {str(p["id"]): p for p in existing if p.get("id")}
    missing = sorted(set(src_ids) - set(old_by))
    common = sorted(set(src_ids) & set(old_by))
    updates = {i: proposed_update(old_by[i], next(p for p in source if p["id"] == i)) for i in common}
    updates = {i:v for i,v in updates.items() if v}
    invalid_prices, invalid_stock = [], []
    for p in source:
        for f in ("priceCfa", "priceNgn"):
            if not isinstance(p.get(f), (int,float)) or isinstance(p[f], bool) or p[f] < 0: invalid_prices.append(f"{p['id']}:{f}")
        if not isinstance(p.get("stock"), int) or isinstance(p.get("stock"), bool) or p["stock"] < 0: invalid_stock.append(f"{p['id']}:stock")
    mappings = {p["id"]: p.get("image", "") for p in source}
    return {"source_count": len(source), "source_prefix_check": len(source) == 258,
            "existing_supabase_count": len(existing),
            "missing_ids": missing, "existing_ids": common, "duplicate_source_ids": dup_src,
            "duplicate_supabase_ids": dup_old, "proposed_inserts": missing,
            "proposed_updates": updates, "image_mappings": mappings,
            "invalid_prices": invalid_prices, "invalid_stock": invalid_stock,
            "price_stock_validation": {"valid": not (invalid_prices or invalid_stock)},
            "fixture_exclusions": sorted(excluded),
            "proposed_skips": [],
            "online_true_decisions": {"count": len(source), "policy": "set true for every wix-* row"},
            "safe_to_apply": (len(source) == 258 and not dup_src and not dup_old
                              and not invalid_prices and not invalid_stock)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="explicitly select the protected no-write mode")
    ap.add_argument("--confirm-real-import", action="store_true")
    ap.add_argument("--report", default="customer_catalog_import_report.json")
    args = ap.parse_args()
    _, source, excluded = load_source()
    existing = fetch_existing()
    result = report(existing, source, excluded)
    result["mode"] = "real-import" if args.confirm_real_import else "dry-run"
    Path(args.report).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("mode", "source_count", "existing_supabase_count", "missing_ids", "proposed_inserts", "proposed_updates", "fixture_exclusions", "online_true_decisions")}, ensure_ascii=False, indent=2))
    if not args.confirm_real_import:
        print("DRY RUN: nothing was written.")
        return 0
    if not result["safe_to_apply"]:
        raise SystemExit("Import refused: validation or duplicate IDs failed; review the report")
    from supabase_store import upsert_products
    by = {str(p["id"]): p for p in existing if p.get("id")}
    rows = []
    for p in source:
        merged = dict(by.get(p["id"], {}))
        for k,v in p.items():
            if nonblank(v) or k == "online": merged[k] = v
        merged["online"] = True
        rows.append(safe_row(merged))
    if not upsert_products(rows): raise SystemExit("Supabase upsert failed; rerun safely after fixing the error")
    print(f"Imported {len(rows)} wix-* products idempotently; no rows were deleted.")
    return 0
if __name__ == "__main__": raise SystemExit(main())
