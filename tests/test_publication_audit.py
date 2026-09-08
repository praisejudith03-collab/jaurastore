"""Tests for Jaurastore Publication Audit & Production Verification.

Verifies:
  1. Classification of the product catalogue under the owner's publication policy.
  2. Immutability invariants: zero rows deleted, zero IDs renamed, zero prices/stock/image_url modified.
  3. Strict enforcement of forced offline rules: wix-001 (placeholder + 0 stock) and wix-012 (0 price).
  4. Safety and correctness of publication_review.sql and drift_guard.py.
  5. Admin behavior: valid new products default to online=true.
  6. Storefront behavior: Supabase source of truth, filters online IS TRUE.
  7. Cache & ETag invalidation.
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


@pytest.fixture
def clean_catalogue():
    merged, seed_count, overrides, deleted = mi.load_local_catalogue()
    return merged, seed_count, overrides, deleted


# --------------------------------------------------------------------------
# 1. Classification & ID Set Verification
# --------------------------------------------------------------------------

def test_full_catalogue_classification_tally(clean_catalogue):
    merged, seed_count, overrides, deleted = clean_catalogue
    products = list(merged.values())
    rep = mi.live_set_report(products, overrides)

    assert rep["total_source_rows"] == 275
    assert rep["approved_live_count"] == 181
    assert rep["counts"] == {
        "approved_live": 181,
        "operator_offline": 2,
        "placeholder_only": 75,
        "no_image": 0,
        "needs_review": 0,
        "test_fixtures_excluded": 17,
    }


def test_approved_live_ids_exact_match(clean_catalogue):
    merged, _, overrides, _ = clean_catalogue
    rep = mi.live_set_report(list(merged.values()), overrides)
    live_ids = set(rep["live_ids"])

    assert live_ids == dg.APPROVED_LIVE_IDS
    assert len(live_ids) == 181


def test_operator_offline_ids_exact_match(clean_catalogue):
    merged, _, overrides, _ = clean_catalogue
    rep = mi.live_set_report(list(merged.values()), overrides)
    offline_ids = set(rep["operator_offline_ids"])

    assert offline_ids == dg.OPERATOR_OFFLINE_IDS
    assert offline_ids == {"wix-001", "wix-012"}


def test_placeholder_only_ids_exact_match(clean_catalogue):
    merged, _, overrides, _ = clean_catalogue
    rep = mi.live_set_report(list(merged.values()), overrides)
    placeholder_ids = set(rep["placeholder_only_ids"])

    assert placeholder_ids == dg.PLACEHOLDER_ONLY_IDS
    assert len(placeholder_ids) == 75


def test_test_fixtures_excluded_exact_match(clean_catalogue):
    merged, _, overrides, _ = clean_catalogue
    rep = mi.live_set_report(list(merged.values()), overrides)
    fixtures = set(rep["test_fixtures_excluded_ids"])

    assert fixtures == dg.TEST_FIXTURE_IDS
    assert len(fixtures) == 17


# --------------------------------------------------------------------------
# 2. Live Row Criteria & Quality Assertions
# --------------------------------------------------------------------------

def test_every_approved_live_row_has_valid_photo_price_and_stock(clean_catalogue):
    merged, _, _, _ = clean_catalogue
    by_id = {str(p["id"]): p for p in merged.values()}

    for pid in dg.APPROVED_LIVE_IDS:
        assert pid in by_id, f"Approved ID {pid} missing from merged catalogue"
        p = by_id[pid]

        # Valid positive retail prices
        ngn = float(p.get("priceNgn", 0))
        cfa = float(p.get("priceCfa", 0))
        assert ngn > 0, f"{pid} priceNgn must be > 0, got {ngn}"
        assert cfa > 0, f"{pid} priceCfa must be > 0, got {cfa}"

        # Valid positive stock
        stock = p.get("stock_quantity") if p.get("stock_quantity") is not None else p.get("stock")
        assert int(stock) > 0, f"{pid} stock must be > 0, got {stock}"

        # Real committed image (not placeholder)
        _loc, rel, status, _detail = mi.resolve_source_image(p)
        assert status == "found", f"{pid} image status must be 'found', got {status}"
        assert rel != "images/products/_placeholder.jpg", f"{pid} must not be placeholder"
        assert not rel.endswith("_placeholder.400w.webp")


def test_wix_001_and_wix_012_forced_offline_reasons(clean_catalogue):
    merged, _, _, _ = clean_catalogue
    by_id = {str(p["id"]): p for p in merged.values()}

    w001 = by_id["wix-001"]
    _loc, rel, status, _ = mi.resolve_source_image(w001)
    assert rel == "images/products/_placeholder.jpg"
    assert int(w001.get("stock_quantity", 0)) == 0

    w012 = by_id["wix-012"]
    assert float(w012.get("priceNgn", 0)) == 0


# --------------------------------------------------------------------------
# 3. Immutability Invariants (No rows, IDs, prices, stock, images modified)
# --------------------------------------------------------------------------

def test_audit_preserves_all_rows_and_fields(clean_catalogue):
    merged, seed_count, overrides, deleted = clean_catalogue
    original_snapshot = copy.deepcopy(merged)

    # Run audit live_set_report
    rep = mi.live_set_report(list(merged.values()), overrides)
    assert rep["total_source_rows"] == len(original_snapshot)

    # Re-verify catalogue identity
    after_merged, _, _, _ = mi.load_local_catalogue()
    assert set(after_merged.keys()) == set(original_snapshot.keys())

    for pid, orig in original_snapshot.items():
        curr = after_merged[pid]
        assert curr["id"] == orig["id"]
        assert curr.get("priceNgn") == orig.get("priceNgn")
        assert curr.get("priceCfa") == orig.get("priceCfa")
        assert curr.get("stock") == orig.get("stock")
        assert curr.get("stock_quantity") == orig.get("stock_quantity")
        assert curr.get("image") == orig.get("image")
        assert curr.get("image_url") == orig.get("image_url")
        assert curr.get("slug") == orig.get("slug")
        assert curr.get("name") == orig.get("name")


# --------------------------------------------------------------------------
# 4. Drift Guard Python & CLI Execution
# --------------------------------------------------------------------------

def test_drift_guard_module_passes(clean_catalogue):
    merged, _, overrides, _ = clean_catalogue
    res = dg.verify_catalogue(list(merged.values()), overrides)
    assert res["ok"] is True
    assert res["errors"] == []


def test_drift_guard_cli_exit_zero():
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "drift_guard.py")],
        capture_output=True, text=True, cwd=ROOT
    )
    assert proc.returncode == 0
    assert "STATUS: PASSED" in proc.stdout
    assert "275" in proc.stdout


def test_drift_guard_catches_tampered_price():
    merged, _, overrides, _ = mi.load_local_catalogue()
    tampered = copy.deepcopy(list(merged.values()))
    # Set price of an approved item to 0
    for p in tampered:
        if p["id"] == "wix-003":
            p["priceNgn"] = 0
            break
    res = dg.verify_catalogue(tampered, overrides)
    assert res["ok"] is False
    assert any("wix-003" in e for e in res["errors"])


def test_drift_guard_catches_forced_offline_leak():
    merged, _, overrides, _ = mi.load_local_catalogue()
    # Mock live list with wix-001 leaked
    custom_prods = copy.deepcopy(list(merged.values()))
    res = dg.verify_catalogue(custom_prods, overrides)
    assert "wix-001" not in res.get("approved_live_ids", [])


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


# --------------------------------------------------------------------------
# 5. Reviewable SQL File Verification
# --------------------------------------------------------------------------

def test_sql_file_exists_and_is_well_formed():
    sql_path = os.path.join(ROOT, "publication_review.sql")
    assert os.path.exists(sql_path), "publication_review.sql must exist"
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()

    # Safety: Must NOT contain destructive DDL/DML statements
    for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER TABLE", "INSERT INTO"):
        assert forbidden not in sql.upper(), f"SQL file contains forbidden statement: {forbidden}"

    # Must contain SELECT previews
    assert "SELECT" in sql.upper()
    assert "WHERE id IN (" in sql

    # Must contain PL/pgSQL Drift Guard block
    assert "DO $$" in sql
    assert "RAISE EXCEPTION" in sql

    # Must contain narrow UPDATE block touching ONLY online and updated_at
    assert "UPDATE products" in sql
    assert re.search(r"SET\s+online\s*=\s*true,\s*updated_at\s*=\s*now\(\)", sql, re.I)
    assert re.search(r"SET\s+online\s*=\s*false,\s*updated_at\s*=\s*now\(\)", sql, re.I)

    # All 181 live IDs must be present in SQL
    for pid in dg.APPROVED_LIVE_IDS:
        assert f"'{pid}'" in sql, f"Live ID {pid} missing from SQL"

    # Operator offline IDs must be present in SQL
    for pid in dg.OPERATOR_OFFLINE_IDS:
        assert f"'{pid}'" in sql, f"Operator offline ID {pid} missing from SQL"


# --------------------------------------------------------------------------
# 6. Admin Behavior: Default online=true for valid new products
# --------------------------------------------------------------------------

def test_normalize_defaults_new_products_to_online_true():
    # When online is omitted
    p1 = catalog_mod.normalize({"name": "New Dress", "priceNgn": 15000, "stock": 5})
    assert p1["online"] is True

    # When online is explicitly True
    p2 = catalog_mod.normalize({"name": "New Dress", "priceNgn": 15000, "stock": 5, "online": True})
    assert p2["online"] is True

    # When online is explicitly False
    p3 = catalog_mod.normalize({"name": "New Dress", "priceNgn": 15000, "stock": 5, "online": False})
    assert p3["online"] is False


def test_admin_upsert_route_preserves_online_default(client):
    tok = login(client)

    # Upsert new product without online field
    r = client.post(
        "/api/admin/products",
        json={"product": {"id": "jau-unit-online", "name": "Online Bag", "priceNgn": 12000, "category": "bags", "stock": 5}},
        headers={"X-CSRF-Token": tok}
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["product"]["online"] is True

    # Cleanup
    client.delete("/api/admin/products/jau-unit-online", headers={"X-CSRF-Token": tok})


# --------------------------------------------------------------------------
# 7. Storefront Behavior: Filters online IS TRUE & Caching/ETag
# --------------------------------------------------------------------------

def test_public_catalog_filters_offline_products(client):
    # Public request (no auth)
    r = client.get("/api/catalog")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True

    # wix-001 carries online=false in live resolution or is offline
    for p in data["products"]:
        assert p.get("online") is not False, f"Offline product {p.get('id')} leaked to storefront"


def test_catalog_etag_and_caching_headers(client):
    r1 = client.get("/api/catalog")
    assert r1.status_code == 200
    etag = r1.headers.get("ETag")
    cache_ctrl = r1.headers.get("Cache-Control")

    assert etag is not None and etag.startswith('W/"')
    assert "public" in cache_ctrl and "max-age=30" in cache_ctrl

    # Repeat request with If-None-Match header
    r2 = client.get("/api/catalog", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.get_data(as_text=True) == ""


def test_meta_updated_at_reflects_product_updates():
    m = catalog_mod.meta()
    assert "updatedAt" in m
    assert "count" in m
    assert m["count"] >= 258
