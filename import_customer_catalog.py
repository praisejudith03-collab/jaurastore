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
    r["image_url"] = r.get("image", "")
    r["images"] = r.get("images", []) or []
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
    invalid = []
    for p in source:
        for f in ("priceCfa", "priceNgn"):
            if not isinstance(p.get(f), (int,float)) or p[f] < 0: invalid.append(f"{p['id']}:{f}")
        if not isinstance(p.get("stock"), int) or p["stock"] < 0: invalid.append(f"{p['id']}:stock")
    mappings = {p["id"]: p.get("image", "") for p in source}
    return {"source_count": len(source), "existing_supabase_count": len(existing),
            "missing_ids": missing, "existing_ids": common, "duplicate_source_ids": dup_src,
            "duplicate_supabase_ids": dup_old, "proposed_inserts": missing,
            "proposed_updates": updates, "image_mappings": mappings,
            "price_stock_validation": {"invalid": invalid, "valid": not invalid},
            "fixture_exclusions": sorted(excluded),
            "online_true_decisions": {"count": len(source), "policy": "set true for every wix-* row"}}

def main():
    ap = argparse.ArgumentParser()
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
    if result["price_stock_validation"]["invalid"] or result["duplicate_source_ids"] or result["duplicate_supabase_ids"]:
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
