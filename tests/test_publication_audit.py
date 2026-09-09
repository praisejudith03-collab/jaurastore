"""Tests for Jaurastore Publication Audit & Production Verification (Reconciled 29/7/17).

Verifies:
  1. Complete production classification: 29 Live / 7 Offline / 17 Fixtures = 53 Supabase Rows.
  2. The 7 offline products include wix-001, wix-012, wix-041, wix-055, wix-197, jau-mtot3318, wix-002.
  3. The 152 approved local products missing from Supabase remain separate (NOT in publication SQL).
  4. Publication SQL safety: zero INSERTs, zero DELETEs, touches only existing production IDs, touches only online and updated_at.
  5. Drift guard detects and aborts on any unclassified production ID.
  6. Immutability invariants: zero rows deleted, zero IDs renamed, zero price/stock/image/name/category changes.
  7. Admin behavior: valid new products default to online=true.
  8. Storefront behavior: Supabase source of truth, filters online IS TRUE.
  9. Cache & ETag invalidation.
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
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


# --------------------------------------------------------------------------
# 1. Production Scope & Approved 29 / 7 / 17 Classification Tests
# --------------------------------------------------------------------------

def test_production_classification_counts_and_disjoint_sets():
    assert len(dg.PRODUCTION_LIVE_IDS) == 29
    assert len(dg.PRODUCTION_OFFLINE_IDS) == 7
    assert len(dg.PRODUCTION_FIXTURE_IDS) == 17
    assert len(dg.ALL_PRODUCTION_53_IDS) == 53
    assert len(dg.MISSING_FROM_PRODUCTION_IDS) == 152

    # Verify all sets are strictly disjoint
    assert len(dg.PRODUCTION_LIVE_IDS & dg.PRODUCTION_OFFLINE_IDS) == 0
    assert len(dg.PRODUCTION_LIVE_IDS & dg.PRODUCTION_FIXTURE_IDS) == 0
    assert len(dg.PRODUCTION_OFFLINE_IDS & dg.PRODUCTION_FIXTURE_IDS) == 0


def test_production_offline_items_exact_composition():
    expected_offline = {
        "wix-001", "wix-012", "wix-041", "wix-055", "wix-197", "jau-mtot3318", "wix-002"
    }
    assert dg.PRODUCTION_OFFLINE_IDS == expected_offline
    assert "jau-mtot3318" in dg.PRODUCTION_OFFLINE_IDS
    assert "wix-002" in dg.PRODUCTION_OFFLINE_IDS


def test_missing_from_production_kept_separate():
    # 181 local approved = 29 production live + 152 missing from production
    assert len(dg.PRODUCTION_LIVE_IDS | dg.MISSING_FROM_PRODUCTION_IDS) == 181
    assert len(dg.PRODUCTION_LIVE_IDS & dg.MISSING_FROM_PRODUCTION_IDS) == 0


# --------------------------------------------------------------------------
# 2. Publication SQL Safety & Drift Guard Checks
# --------------------------------------------------------------------------

def test_sql_contains_zero_insert_statements():
    sql_path = os.path.join(ROOT, "publication_review.sql")
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()

    code_only = re.sub(r"--[^\n]*", "", sql)
    code_only = re.sub(r"/\*.*?\*/", "", code_only, flags=re.S)
    assert not re.search(r"\bINSERT\s+INTO\b", code_only, re.I), (
        "publication_review.sql must NEVER contain executable INSERT statements")


def test_sql_live_update_targets_only_29_existing_supabase_ids():
    sql_path = os.path.join(ROOT, "publication_review.sql")
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()

    code_only = re.sub(r"--[^\n]*", "", sql)
    code_only = re.sub(r"/\*.*?\*/", "", code_only, flags=re.S)

    match = re.search(r"UPDATE\s+products\s+SET\s+online\s*=\s*true[^;]+;", code_only, re.S | re.I)
    assert match, "Missing online = true UPDATE statement"
    update_live_sql = match.group(0)

    # 1. All 29 existing production IDs must be present in the UPDATE block
    for pid in dg.PRODUCTION_LIVE_IDS:
        assert f"'{pid}'" in update_live_sql, f"Existing live ID {pid} missing from UPDATE statement"

    # 2. None of the 7 offline IDs, 17 fixtures, or 152 missing IDs may be present in the UPDATE block
    for pid in (dg.PRODUCTION_OFFLINE_IDS | dg.PRODUCTION_FIXTURE_IDS | dg.MISSING_FROM_PRODUCTION_IDS):
        assert f"'{pid}'" not in update_live_sql, (
            f"Non-live ID {pid} must NOT be in online=true UPDATE block")


def test_sql_offline_update_includes_all_7_offline_and_17_fixture_ids():
    sql_path = os.path.join(ROOT, "publication_review.sql")
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()

    code_only = re.sub(r"--[^\n]*", "", sql)
    code_only = re.sub(r"/\*.*?\*/", "", code_only, flags=re.S)

    for pid in dg.PRODUCTION_OFFLINE_IDS:
        assert f"'{pid}'" in code_only, f"Offline ID {pid} missing from SQL"

    for pid in dg.PRODUCTION_FIXTURE_IDS:
        assert f"'{pid}'" in code_only, f"Fixture ID {pid} missing from SQL"


def test_sql_modifies_only_online_and_updated_at():
    sql_path = os.path.join(ROOT, "publication_review.sql")
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()

    code_only = re.sub(r"--[^\n]*", "", sql)
    code_only = re.sub(r"/\*.*?\*/", "", code_only, flags=re.S)

    for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER TABLE"):
        assert forbidden not in code_only.upper(), f"SQL contains forbidden statement: {forbidden}"

    update_blocks = re.findall(r"UPDATE\s+products\s+SET\s+(.*?)\s+WHERE\b", code_only, re.S | re.I)
    assert len(update_blocks) > 0
    for block in update_blocks:
        cols = [c.split("=")[0].strip().lower() for c in block.split(",")]
        for col in cols:
            assert col in ("online", "updated_at"), f"Forbidden column in UPDATE: {col}"


def test_sql_drift_guard_aborts_on_unclassified_ids():
    sql_path = os.path.join(ROOT, "publication_review.sql")
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()

    assert "DO $$" in sql
    assert "unclassified" in sql.lower()
    assert "RAISE EXCEPTION" in sql
    assert "BEGIN;" in sql
    assert "COMMIT;" in sql


# --------------------------------------------------------------------------
# 3. Drift Guard Python Module & CLI Execution
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
    assert "53" in proc.stdout


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

    res = dg.verify_reconciliation()
    assert res["ok"] is True

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

    norm = catalog_mod.normalize({"name": "Admin Handbag", "priceNgn": 18000, "stock": 4})
    assert norm["online"] is True

    r = client.post(
        "/api/admin/products",
        json={"product": {"id": "jau-unit-rec", "name": "Reconciled Bag", "priceNgn": 14000, "category": "bags", "stock": 3}},
        headers={"X-CSRF-Token": tok}
    )
    assert r.status_code == 200
    assert r.get_json()["product"]["online"] is True

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

    r2 = client.get("/api/catalog", headers={"If-None-Match": etag1})
    assert r2.status_code == 304

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

    client.delete("/api/admin/products/jau-etag-test", headers={"X-CSRF-Token": tok})
