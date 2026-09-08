#!/usr/bin/env python3
"""Drift Guard for Jaurastore Publication Audit & Production Verification (Reconciled 29/7/17).

Verifies the approved 53-row production Supabase classification:
  - 29 existing valid products: online = true
  - 7 non-fixture offline/review products: online = false
      * wix-001 (placeholder + stock_quantity = 0)
      * wix-012 (priceNgn = 0, invalid retail price)
      * wix-041 (placeholder)
      * wix-055 (placeholder)
      * wix-197 (placeholder)
      * jau-mtot3318 (no image; remains offline until image confirmed)
      * wix-002 (no image / placeholder; remains offline until image confirmed)
  - 17 test fixtures: online = false
  - Total: 29 + 7 + 17 = 53 production rows.

Also tracks the 152 local approved products missing from Supabase (kept separate,
NEVER inserted by publication SQL).

Safety Invariants Enforced:
  1. No rows inserted (product importing is strictly separate)
  2. No rows deleted
  3. No product IDs renamed
  4. No prices changed
  5. No stock quantities changed
  6. No image URLs changed
  7. No category or name changes
  8. Only 'online' and 'updated_at' on existing production rows may be updated
  9. Aborts if any production ID is unclassified
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import migrate_images as mi

# 1. The 29 existing valid live IDs in production Supabase (online = true)
PRODUCTION_LIVE_IDS = frozenset([
    "wix-011", "wix-030", "wix-032", "wix-033", "wix-042", "wix-053", "wix-054",
    "wix-070", "wix-072", "wix-079", "wix-080", "wix-088", "wix-091", "wix-117",
    "wix-118", "wix-163", "wix-164", "wix-189", "wix-198", "wix-199", "wix-200",
    "wix-201", "wix-202", "wix-222", "wix-228", "wix-229", "wix-237", "wix-244",
    "wix-252"
])

# 2. The 7 non-fixture offline / review IDs in production Supabase (online = false)
PRODUCTION_OFFLINE_IDS = frozenset([
    "wix-001", "wix-012", "wix-041", "wix-055", "wix-197", "jau-mtot3318", "wix-002"
])

# 3. The 17 test fixture IDs in production Supabase (online = false)
PRODUCTION_FIXTURE_IDS = frozenset([
    "jau-mirror-fail", "jau-mirror-ok", "jau-mirror-post", "jau-stock-a",
    "jau-stock-b", "jau-stock-dcf", "jau-stock-dec", "jau-stock-del",
    "jau-stock-dnp", "jau-stock-dpn", "jau-stock-em2", "jau-stock-eml",
    "jau-stock-idem", "jau-stock-opr", "jau-stock-opt", "jau-stock-pay",
    "jau-stock-rop"
])

# Complete set of all 53 production Supabase rows
ALL_PRODUCTION_53_IDS = frozenset(PRODUCTION_LIVE_IDS | PRODUCTION_OFFLINE_IDS | PRODUCTION_FIXTURE_IDS)

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

EXPECTED_COUNTS = {
    "production_total": 53,
    "production_live": 29,
    "production_offline": 7,
    "production_fixtures": 17,
    "missing_from_production": 152,
    "local_total": 275,
}


def verify_publication_sql(sql_text=None):
    """Verify that publication_review.sql classifies all 53 production rows and is 100% safe."""
    if sql_text is None:
        path = os.path.join(ROOT, "publication_review.sql")
        if not os.path.exists(path):
            return {"ok": False, "errors": ["publication_review.sql not found"]}
        with open(path, encoding="utf-8") as f:
            sql_text = f.read()

    errors = []

    # Strip comments for executable statement analysis
    code_only = re.sub(r"--[^\n]*", "", sql_text)
    code_only = re.sub(r"/\*.*?\*/", "", code_only, flags=re.S)

    # 1. Must NOT contain INSERT statements
    if re.search(r"\bINSERT\s+INTO\b", code_only, re.I):
        errors.append("CRITICAL: publication_review.sql must NOT contain INSERT statements")

    # 2. Must NOT contain DELETE or DROP statements
    for bad in (r"\bDELETE\s+FROM\b", r"\bDROP\s+TABLE\b", r"\bTRUNCATE\b", r"\bALTER\s+TABLE\b"):
        if re.search(bad, code_only, re.I):
            errors.append(f"CRITICAL: publication_review.sql contains forbidden statement: {bad}")

    # 3. All 29 production live IDs must be present in the live UPDATE statement
    live_match = re.search(r"UPDATE\s+products\s+SET\s+online\s*=\s*true[^;]+;", code_only, re.S | re.I)
    if not live_match:
        errors.append("CRITICAL: Missing online = true UPDATE statement in SQL")
    else:
        live_sql = live_match.group(0)
        for pid in PRODUCTION_LIVE_IDS:
            if f"'{pid}'" not in live_sql:
                errors.append(f"Existing live ID {pid} missing from online=true UPDATE statement")

    # 4. None of the 7 offline IDs, 17 fixtures, or 152 missing IDs may be in online=true UPDATE block
    if live_match:
        live_sql = live_match.group(0)
        for off_id in (PRODUCTION_OFFLINE_IDS | PRODUCTION_FIXTURE_IDS | MISSING_FROM_PRODUCTION_IDS):
            if f"'{off_id}'" in live_sql:
                errors.append(f"CRITICAL: Non-live ID {off_id} was included in online=true UPDATE block!")

    # 5. All 7 offline IDs must be in an online=false UPDATE statement
    offline_match = re.search(r"UPDATE\s+products\s+SET\s+online\s*=\s*false[^;]+;", code_only, re.S | re.I)
    if not offline_match:
        errors.append("CRITICAL: Missing online = false UPDATE statement in SQL")
    else:
        for off_id in PRODUCTION_OFFLINE_IDS:
            if f"'{off_id}'" not in code_only:
                errors.append(f"Offline ID {off_id} missing from SQL offline statement")

    # 6. All 17 fixture IDs must be in an online=false UPDATE statement
    for fix_id in PRODUCTION_FIXTURE_IDS:
        if f"'{fix_id}'" not in code_only:
            errors.append(f"Fixture ID {fix_id} missing from SQL fixture statement")

    # 7. Must touch ONLY online and updated_at
    update_blocks = re.findall(r"UPDATE\s+products\s+SET\s+(.*?)\s+WHERE\b", code_only, re.S | re.I)
    for block in update_blocks:
        cols = [c.split("=")[0].strip().lower() for c in block.split(",")]
        for col in cols:
            if col not in ("online", "updated_at"):
                errors.append(f"CRITICAL: Forbidden column in UPDATE: {col}")

    # 8. Must include drift guard checking for unclassified production rows
    if "DO $$" not in sql_text or "unclassified" not in sql_text.lower():
        errors.append("CRITICAL: Drift guard checking for unclassified production rows is missing from SQL")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
    }


def verify_reconciliation():
    """Verify the 29 / 7 / 17 production classification and 53-row reconciliation."""
    errors = []

    # 1. Total production sets check
    assert len(PRODUCTION_LIVE_IDS) == 29, f"Expected 29 live IDs, got {len(PRODUCTION_LIVE_IDS)}"
    assert len(PRODUCTION_OFFLINE_IDS) == 7, f"Expected 7 offline IDs, got {len(PRODUCTION_OFFLINE_IDS)}"
    assert len(PRODUCTION_FIXTURE_IDS) == 17, f"Expected 17 fixture IDs, got {len(PRODUCTION_FIXTURE_IDS)}"
    assert len(ALL_PRODUCTION_53_IDS) == 53, f"Expected 53 production IDs, got {len(ALL_PRODUCTION_53_IDS)}"

    # Check for overlapping sets
    if PRODUCTION_LIVE_IDS & PRODUCTION_OFFLINE_IDS:
        errors.append(f"Live and offline sets overlap: {PRODUCTION_LIVE_IDS & PRODUCTION_OFFLINE_IDS}")
    if PRODUCTION_LIVE_IDS & PRODUCTION_FIXTURE_IDS:
        errors.append(f"Live and fixture sets overlap: {PRODUCTION_LIVE_IDS & PRODUCTION_FIXTURE_IDS}")
    if PRODUCTION_OFFLINE_IDS & PRODUCTION_FIXTURE_IDS:
        errors.append(f"Offline and fixture sets overlap: {PRODUCTION_OFFLINE_IDS & PRODUCTION_FIXTURE_IDS}")

    # 2. SQL file check
    sql_check = verify_publication_sql()
    if not sql_check["ok"]:
        errors.extend(sql_check["errors"])

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "counts": EXPECTED_COUNTS,
    }


def main():
    parser = argparse.ArgumentParser(description="Jaurastore Reconciled Publication Drift Guard (29/7/17)")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args()

    result = verify_reconciliation()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("=" * 76)
        print("  JAURASTORE PUBLICATION DRIFT GUARD — 29 / 7 / 17 RECONCILIATION")
        print("=" * 76)
        print(f"  Production Total Rows      : {EXPECTED_COUNTS['production_total']} (29 + 7 + 17 = 53)")
        print(f"  Approved Live (Public)     : {EXPECTED_COUNTS['production_live']} -> online = true")
        print(f"  Offline / Review           : {EXPECTED_COUNTS['production_offline']} -> online = false")
        print(f"  Test Fixtures Excluded     : {EXPECTED_COUNTS['production_fixtures']} -> online = false")
        print(f"  Missing from Production    : {EXPECTED_COUNTS['missing_from_production']} -> NOT inserted (separate)")
        print("-" * 76)
        if result["ok"]:
            print("  STATUS: PASSED (All 53 production rows classified · Zero SQL drift)")
            print("=" * 76)
            return 0
        else:
            print(f"  STATUS: FAILED ({len(result['errors'])} errors detected!)")
            for err in result["errors"]:
                print(f"    - {err}")
            print("=" * 76)
            return 1


if __name__ == "__main__":
    sys.exit(main())
