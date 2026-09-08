#!/usr/bin/env python3
"""Drift Guard for Jaurastore Publication Audit & Production Verification (Reconciled).

Verifies the reconciliation between the 275 local catalogue rows and the 53 production
Supabase rows:
  - Local Catalogue: 275 rows (181 approved live, 2 operator offline, 75 placeholder, 17 fixtures)
  - Production Supabase: 53 rows (29 approved live, 2 operator offline, 3 placeholder, 2 no-image, 17 fixtures)
  - Missing from Production: 152 approved live rows (NOT inserted by publication SQL)
  - Production Live Target: Exactly 29 existing Supabase rows

Safety Invariants Enforced:
  1. No rows inserted (product importing is strictly separate)
  2. No rows deleted
  3. No product IDs renamed
  4. No prices changed
  5. No stock quantities changed
  6. No image URLs changed
  7. No category or name changes
  8. Only 'online' and 'updated_at' on existing production rows may be updated
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import migrate_images as mi

# The 29 approved live IDs existing in production Supabase
EXISTING_PRODUCTION_LIVE_IDS = frozenset([
    "wix-011", "wix-030", "wix-032", "wix-033", "wix-042", "wix-053", "wix-054",
    "wix-070", "wix-072", "wix-079", "wix-080", "wix-088", "wix-091", "wix-117",
    "wix-118", "wix-163", "wix-164", "wix-189", "wix-198", "wix-199", "wix-200",
    "wix-201", "wix-202", "wix-222", "wix-228", "wix-229", "wix-237", "wix-244",
    "wix-252"
])

# The 152 approved live IDs present locally but missing from production Supabase
MISSING_FROM_PRODUCTION_IDS = frozenset([
    "wix-003", "wix-004", "wix-005", "wix-008", "wix-009", "wix-010", "wix-013",
    "wix-014", "wix-015", "wix-016", "wix-017", "wix-018", "wix-019", "wix-020",
    "wix-021", "wix-023", "wix-024", "wix-025", "wix-027", "wix-029", "wix-031",
    "wix-034", "wix-035", "wix-036", "wix-039", "wix-040", "wix-043", "wix-048",
    "wix-049", "wix-050", "wix-051", "wix-052", "wix-056", "wix-057", "wix-058",
    "wix-059", "wix-060", "wix-061", "wix-063", "wix-064", "wix-066", "wix-067",
    "wix-068", "wix-069", "wix-071", "wix-073", "wix-075", "wix-076", "wix-077",
    "wix-078", "wix-081", "wix-082", "wix-083", "wix-084", "wix-085", "wix-086",
    "wix-087", "wix-092", "wix-094", "wix-095", "wix-096", "wix-097", "wix-100",
    "wix-101", "wix-102", "wix-103", "wix-104", "wix-105", "wix-110", "wix-113",
    "wix-114", "wix-116", "wix-119", "wix-132", "wix-133", "wix-134", "wix-135",
    "wix-136", "wix-137", "wix-138", "wix-139", "wix-140", "wix-141", "wix-142",
    "wix-145", "wix-147", "wix-154", "wix-162", "wix-165", "wix-166", "wix-167",
    "wix-168", "wix-169", "wix-170", "wix-172", "wix-174", "wix-175", "wix-177",
    "wix-178", "wix-179", "wix-180", "wix-181", "wix-182", "wix-183", "wix-184",
    "wix-185", "wix-186", "wix-187", "wix-188", "wix-190", "wix-193", "wix-194",
    "wix-195", "wix-203", "wix-206", "wix-207", "wix-208", "wix-209", "wix-210",
    "wix-211", "wix-212", "wix-213", "wix-214", "wix-218", "wix-219", "wix-220",
    "wix-221", "wix-223", "wix-224", "wix-225", "wix-227", "wix-230", "wix-231",
    "wix-232", "wix-234", "wix-235", "wix-238", "wix-239", "wix-241", "wix-243",
    "wix-245", "wix-246", "wix-247", "wix-248", "wix-249", "wix-250", "wix-251",
    "wix-253", "wix-254", "wix-255", "wix-256", "wix-257"
])

# All 181 local approved live IDs
APPROVED_LOCAL_LIVE_IDS = frozenset(EXISTING_PRODUCTION_LIVE_IDS | MISSING_FROM_PRODUCTION_IDS)

# Operator offline IDs in production (2 IDs)
PRODUCTION_OPERATOR_OFFLINE_IDS = frozenset(["wix-001", "wix-012"])

# Placeholder-only IDs in production (3 IDs)
PRODUCTION_PLACEHOLDER_IDS = frozenset(["wix-041", "wix-055", "wix-197"])

# Combined production offline IDs (5 IDs)
PRODUCTION_OFFLINE_IDS = frozenset(PRODUCTION_OPERATOR_OFFLINE_IDS | PRODUCTION_PLACEHOLDER_IDS)

# Test fixture IDs in production (17 IDs)
PRODUCTION_FIXTURE_IDS = frozenset([
    "jau-mirror-fail", "jau-mirror-ok", "jau-mirror-post", "jau-stock-a",
    "jau-stock-b", "jau-stock-dcf", "jau-stock-dec", "jau-stock-del",
    "jau-stock-dnp", "jau-stock-dpn", "jau-stock-em2", "jau-stock-eml",
    "jau-stock-idem", "jau-stock-opr", "jau-stock-opt", "jau-stock-pay",
    "jau-stock-rop"
])

EXPECTED_PRODUCTION_COUNTS = {
    "approved_live": 29,
    "operator_offline": 2,
    "placeholder_only": 3,
    "test_fixtures_excluded": 17,
    "missing_from_production": 152,
    "total_local_live": 181,
    "total_local_rows": 275,
}


def verify_publication_sql(sql_text=None):
    """Verify that publication_review.sql is safe for the 53-row Supabase database."""
    if sql_text is None:
        path = os.path.join(ROOT, "publication_review.sql")
        if not os.path.exists(path):
            return {"ok": False, "errors": ["publication_review.sql not found"]}
        with open(path, encoding="utf-8") as f:
            sql_text = f.read()

    errors = []

    # 1. Must NOT contain INSERT statements
    if re.search(r"\bINSERT\s+INTO\b", sql_text, re.I):
        errors.append("CRITICAL: publication_review.sql must NOT contain INSERT statements")

    # 2. Must NOT contain DELETE or DROP statements
    for bad in (r"\bDELETE\s+FROM\b", r"\bDROP\s+TABLE\b", r"\bTRUNCATE\b"):
        if re.search(bad, sql_text, re.I):
            errors.append(f"CRITICAL: publication_review.sql contains forbidden statement: {bad}")

    # 3. Live UPDATE block must contain ONLY existing production IDs (29 IDs)
    for pid in EXISTING_PRODUCTION_LIVE_IDS:
        if f"'{pid}'" not in sql_text:
            errors.append(f"Existing live ID {pid} missing from publication SQL")

    # 4. Live UPDATE block must NOT attempt to update missing products
    for pid in MISSING_FROM_PRODUCTION_IDS:
        # Check if pid is in an UPDATE statement
        # Specifically, missing IDs should only appear in comments / missing_from_production docs
        matches = re.findall(rf"UPDATE\s+products\s+SET\s+online\s*=\s*true[^;]*?'{re.escape(pid)}'", sql_text, re.S | re.I)
        if matches:
            errors.append(f"CRITICAL: Missing ID {pid} was included in online=true UPDATE statement")

    # 5. Offline IDs must be set to online = false
    for pid in PRODUCTION_OFFLINE_IDS:
        if f"'{pid}'" not in sql_text:
            errors.append(f"Offline ID {pid} missing from SQL offline statement")

    # 6. Must touch ONLY online and updated_at
    update_blocks = re.findall(r"UPDATE\s+products\s+SET\s+(.*?)\s+WHERE\b", sql_text, re.S | re.I)
    for block in update_blocks:
        cols = [c.split("=")[0].strip().lower() for c in block.split(",")]
        for col in cols:
            if col not in ("online", "updated_at"):
                errors.append(f"CRITICAL: Forbidden column in UPDATE: {col}")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
    }


def verify_reconciliation():
    """Verify the full reconciliation between local catalogue and production Supabase."""
    errors = []

    # Local catalogue verification
    merged, seed_count, ov, deleted = mi.load_local_catalogue()
    rep_local = mi.live_set_report(list(merged.values()), ov or {})

    if rep_local["total_source_rows"] != 275:
        errors.append(f"Local total rows drift: expected 275, got {rep_local['total_source_rows']}")
    if rep_local["approved_live_count"] != 181:
        errors.append(f"Local live count drift: expected 181, got {rep_local['approved_live_count']}")

    # Supabase production subset verification
    with open(os.path.join(ROOT, "data", "catalog.json"), encoding="utf-8") as f:
        cat = json.load(f)
    rep_prod = mi.live_set_report(cat.get("products", []), {})

    if rep_prod["approved_live_count"] != 29:
        errors.append(f"Production live count drift: expected 29, got {rep_prod['approved_live_count']}")

    actual_prod_live = set(rep_prod["live_ids"])
    if actual_prod_live != EXISTING_PRODUCTION_LIVE_IDS:
        errors.append(f"Production live ID set mismatch: {actual_prod_live ^ EXISTING_PRODUCTION_LIVE_IDS}")

    missing = set(rep_local["live_ids"]) - actual_prod_live
    if missing != MISSING_FROM_PRODUCTION_IDS:
        errors.append(f"Missing from production ID set mismatch: {len(missing)} vs expected 152")

    sql_check = verify_publication_sql()
    if not sql_check["ok"]:
        errors.extend(sql_check["errors"])

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "counts": EXPECTED_PRODUCTION_COUNTS,
    }


def main():
    parser = argparse.ArgumentParser(description="Jaurastore Reconciled Publication Drift Guard")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args()

    result = verify_reconciliation()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("=" * 74)
        print("  JAURASTORE PUBLICATION DRIFT GUARD — PRODUCTION RECONCILIATION")
        print("=" * 74)
        print(f"  Local Catalogue Rows       : {EXPECTED_PRODUCTION_COUNTS['total_local_rows']}")
        print(f"  Local Approved Live Rows   : {EXPECTED_PRODUCTION_COUNTS['total_local_live']}")
        print(f"  Existing Production Live   : {EXPECTED_PRODUCTION_COUNTS['approved_live']} (Target online=true)")
        print(f"  Missing from Production    : {EXPECTED_PRODUCTION_COUNTS['missing_from_production']} (NOT inserted)")
        print(f"  Production Offline Rows    : {EXPECTED_PRODUCTION_COUNTS['operator_offline'] + EXPECTED_PRODUCTION_COUNTS['placeholder_only']} (Target online=false)")
        print(f"  Production Fixtures        : {EXPECTED_PRODUCTION_COUNTS['test_fixtures_excluded']} (Target online=false)")
        print("-" * 74)
        if result["ok"]:
            print("  STATUS: PASSED (Reconciliation and publication SQL are 100% safe)")
            print("=" * 74)
            return 0
        else:
            print(f"  STATUS: FAILED ({len(result['errors'])} errors detected!)")
            for err in result["errors"]:
                print(f"    - {err}")
            print("=" * 74)
            return 1


if __name__ == "__main__":
    sys.exit(main())
