"""Tests for Jaurastore Publication Audit & Production Verification (Reconciled).

Verifies:
  1. Scope separation: 275 local catalogue rows vs 53 production Supabase rows.
  2. Exactly 29 approved live IDs present in Supabase (target online = true).
  3. Exactly 152 approved live IDs missing from Supabase (reported as missing_from_production, NOT inserted).
  4. Publication SQL safety: zero INSERTs, zero DELETEs, touches only existing production IDs, touches only online and updated_at.
  5. Immutability invariants: zero rows deleted, zero IDs renamed, zero price/stock/image/name/category changes.
  6. Admin behavior: valid new products default to online=true.
  7. Storefront behavior: Supabase source of truth, filters online IS TRUE.
  8. Cache & ETag invalidation.
"""
import copy
import json
import os
import re
import subprocess
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import catalog as catalog_mod
import drift_guard as dg
import migrate_images as mi
import app as appmod
import auth as authmod
from db import execute, init_db

sys.path.insert(0, os.path.join(ROOT, "tests"))
from _pw import PW

EMAIL = "jaurastore@gmail.com"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    authmod.ensure_seed_admins()
    authmod.set_password(EMAIL, PW)
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


# --------------------------------------------------------------------------
# 1. Scope Separation & Reconciliation Tests
# --------------------------------------------------------------------------

def test_scope_separation_local_vs_production():
    merged, seed_count, overrides, deleted = mi.load_local_catalogue()
    with open(os.path.join(ROOT, "data", "catalog.json"), encoding="utf-8") as f:
        cat = json.load(f)

    rep_local = mi.live_set_report(list(merged.values()), overrides)
    rep_prod = mi.live_set_report(cat.get("products", []), {})

    # 1. Local catalogue: 275 rows, 181 approved live
    assert rep_local["total_source_rows"] == 275
    assert rep_local["approved_live_count"] == 181

    # 2. Production Supabase subset: 29 approved live
    assert rep_prod["approved_live_count"] == 29
    assert set(rep_prod["live_ids"]) == dg.EXISTING_PRODUCTION_LIVE_IDS

    # 3. Present in both: exactly 29 IDs
    present_in_both = set(rep_local["live_ids"]) & set(rep_prod["live_ids"])
    assert present_in_both == dg.EXISTING_PRODUCTION_LIVE_IDS
    assert len(present_in_both) == 29

    # 4. Approved local IDs missing from Supabase: exactly 152 IDs
    missing = set(rep_local["live_ids"]) - set(rep_prod["live_ids"])
    assert missing == dg.MISSING_FROM_PRODUCTION_IDS
    assert len(missing) == 152


def test_production_offline_and_fixtures():
    with open(os.path.join(ROOT, "data", "catalog.json"), encoding="utf-8") as f:
        cat = json.load(f)
    rep_prod = mi.live_set_report(cat.get("products", []), {})

    assert set(rep_prod["operator_offline_ids"]) == dg.PRODUCTION_OPERATOR_OFFLINE_IDS
    assert set(rep_prod["placeholder_only_ids"]) == dg.PRODUCTION_PLACEHOLDER_IDS
    assert set(rep_prod["test_fixtures_excluded_ids"]) == dg.PRODUCTION_FIXTURE_IDS


# --------------------------------------------------------------------------
# 2. Safety: SQL Cannot Silently Import Missing Local Products
# --------------------------------------------------------------------------

def test_sql_contains_zero_insert_statements():
    sql_path = os.path.join(ROOT, "publication_review.sql")
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()

    assert not re.search(r"\bINSERT\s+INTO\b", sql, re.I), (
        "publication_review.sql must NEVER contain INSERT statements (importing is separate)")


def test_sql_live_update_targets_only_existing_supabase_ids():
    sql_path = os.path.join(ROOT, "publication_review.sql")
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()

    # Find the online = true UPDATE statement
    match = re.search(r"UPDATE\s+products\s+SET\s+online\s*=\s*true[^;]+;", sql, re.S | re.I)
    assert match, "Missing online = true UPDATE statement"
    update_live_sql = match.group(0)

    # 1. All 29 existing production IDs must be present in the UPDATE block
    for pid in dg.EXISTING_PRODUCTION_LIVE_IDS:
        assert f"'{pid}'" in update_live_sql, f"Existing live ID {pid} missing from UPDATE statement"

    # 2. None of the 152 missing IDs may be present in the UPDATE block
    for pid in dg.MISSING_FROM_PRODUCTION_IDS:
        assert f"'{pid}'" not in update_live_sql, (
            f"Missing ID {pid} must NOT be in online=true UPDATE block (cannot update rows not in DB)")


def test_sql_modifies_only_online_and_updated_at():
    sql_path = os.path.join(ROOT, "publication_review.sql")
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()

    # Check that forbidden DDL / DML keywords are absent
    for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER TABLE", "INSERT INTO"):
        assert forbidden not in sql.upper(), f"SQL contains forbidden statement: {forbidden}"

    # Check that every UPDATE statement touches ONLY online and updated_at
    update_blocks = re.findall(r"UPDATE\s+products\s+SET\s+(.*?)\s+WHERE\b", sql, re.S | re.I)
    assert len(update_blocks) > 0
    for block in update_blocks:
        cols = [c.split("=")[0].strip().lower() for c in block.split(",")]
        for col in cols:
            assert col in ("online", "updated_at"), f"Forbidden column in UPDATE: {col}"


def test_sql_has_select_preview_and_drift_guard():
    sql_path = os.path.join(ROOT, "publication_review.sql")
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()

    assert "SELECT" in sql.upper()
    assert "DO $$" in sql
    assert "RAISE EXCEPTION" in sql
    assert "BEGIN;" in sql
    assert "COMMIT;" in sql


# --------------------------------------------------------------------------
# 3. Drift Guard Verification
# --------------------------------------------------------------------------

def test_drift_guard_module_and_cli():
    res = dg.verify_reconciliation()
    assert res["ok"] is True
    assert res["errors"] == []

    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "drift_guard.py")],
        capture_output=True, text=True, cwd=ROOT
    )
    assert proc.returncode == 0
    assert "STATUS: PASSED" in proc.stdout


def test_drift_guard_catches_sql_insert_leak():
    fake_sql = "INSERT INTO products (id) VALUES ('wix-999');"
    res = dg.verify_publication_sql(fake_sql)
    assert res["ok"] is False
    assert any("INSERT" in e for e in res["errors"])


def test_drift_guard_catches_missing_id_in_sql_live_update():
    fake_sql = "UPDATE products SET online = true, updated_at = now() WHERE id IN ('wix-003');"
    res = dg.verify_publication_sql(fake_sql)
    assert res["ok"] is False
    assert any("wix-003" in e for e in res["errors"])


# --------------------------------------------------------------------------
# 4. Immutability Invariants (No deletions, renames, price/stock/image edits)
# --------------------------------------------------------------------------

def test_catalogue_data_remains_strictly_unmodified():
    merged, seed_count, overrides, deleted = mi.load_local_catalogue()
    original = copy.deepcopy(merged)

    # Execute drift guard / verification
    res = dg.verify_reconciliation()
    assert res["ok"] is True

    # Check again
    after, _, _, _ = mi.load_local_catalogue()
    assert len(after) == len(original)
    for pid, orig in original.items():
        curr = after[pid]
        assert curr["id"] == orig["id"]
        assert curr.get("name") == orig.get("name")
        assert curr.get("category") == orig.get("category")
        assert curr.get("priceNgn") == orig.get("priceNgn")
        assert curr.get("priceCfa") == orig.get("priceCfa")
        assert curr.get("stock") == orig.get("stock")
        assert curr.get("stock_quantity") == orig.get("stock_quantity")
        assert curr.get("image") == orig.get("image")
        assert curr.get("image_url") == orig.get("image_url")


# --------------------------------------------------------------------------
# 5. Admin & Storefront Behavior
# --------------------------------------------------------------------------

def test_admin_new_valid_products_default_online_true(client):
    tok = login(client)

    # Normalization defaults
    norm = catalog_mod.normalize({"name": "Admin Handbag", "priceNgn": 18000, "stock": 4})
    assert norm["online"] is True

    # Upsert route defaults
    r = client.post(
        "/api/admin/products",
        json={"product": {"id": "jau-unit-rec", "name": "Reconciled Bag", "priceNgn": 14000, "category": "bags", "stock": 3}},
        headers={"X-CSRF-Token": tok}
    )
    assert r.status_code == 200
    assert r.get_json()["product"]["online"] is True

    # Cleanup
    client.delete("/api/admin/products/jau-unit-rec", headers={"X-CSRF-Token": tok})


def test_storefront_filters_online_is_true(client):
    r = client.get("/api/catalog")
    assert r.status_code == 200
    prods = r.get_json()["products"]

    for p in prods:
        assert p.get("online") is not False, f"Offline product {p.get('id')} leaked to storefront"


def test_etag_invalidates_on_catalogue_change(client):
    r1 = client.get("/api/catalog")
    assert r1.status_code == 200
    etag1 = r1.headers.get("ETag")
    assert etag1 and etag1.startswith('W/"')

    # Matching If-None-Match gives 304
    r2 = client.get("/api/catalog", headers={"If-None-Match": etag1})
    assert r2.status_code == 304

    # Save a temporary product -> ETag changes
    tok = login(client)
    client.post(
        "/api/admin/products",
        json={"product": {"id": "jau-etag-test", "name": "ETag Bag", "priceNgn": 9999, "stock": 2}},
        headers={"X-CSRF-Token": tok}
    )

    r3 = client.get("/api/catalog", headers={"If-None-Match": etag1})
    assert r3.status_code == 200
    etag3 = r3.headers.get("ETag")
    assert etag3 != etag1

    # Cleanup
    client.delete("/api/admin/products/jau-etag-test", headers={"X-CSRF-Token": tok})
