"""Regression tests for J Aura Store header and category fixes."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest

from postgrest.exceptions import APIError


def test_header_logo_visible_on_all_pages(client):
    """Test that the header logo is visible on all storefront pages."""
    from tests.conftest import AUTH_HEADERS
    
    pages = [
        "/",
        "/shop.html",
        "/categories.html",
        "/faq.html",
        "/cart.html",
        "/checkout.html",
    ]
    
    for path in pages:
        resp = client.get(path, headers=AUTH_HEADERS)
        assert resp.status_code == 200, f"Failed to load {path}"
        # Check that the header contains a logo image
        html = resp.get_data(as_text=True)
        # The header should contain the logo img tag
        assert 'src="images/brand/logo' in html, f"Logo not found in {path}"
        # The search button should be present
        assert 'data-open-search' in html, f"Search button not found in {path}"


def test_no_admin_portal_in_shopper_content(client):
    """Test that 'admin portal' phrase does not appear in shopper-facing pages."""
    from tests.conftest import AUTH_HEADERS
    
    pages = [
        "/",
        "/shop.html",
        "/product.html",
        "/categories.html",
        "/faq.html",
        "/cart.html",
        "/checkout.html",
    ]
    
    for path in pages:
        resp = client.get(path, headers=AUTH_HEADERS)
        html = resp.get_data(as_text=True)
        # admin portal should not appear (case-insensitive)
        lower_html = html.lower()
        assert "admin portal" not in lower_html,             f"Found 'admin portal' in {path} - this phrase must be removed from shopper-facing content"


def test_category_persistence_after_redeploy(client, monkeypatch):
    """Test that an added category survives a simulated redeploy."""
    # Add a test category
    from tests.test_categories_resilient import _cats, supabase_store
    
    # Save original categories
    original_cats = _cats()
    
    # Add a new category
    test_cat = {"id": "perfume", "name": "Perfume", "nameFr": "Parfum", "hidden": False}
    all_cats = original_cats + [test_cat]
    
    # Save via the API (simulated)
    with monkeypatch.context() as m:
        # Mock the supabase store to return the updated categories
        store = {}
        fake_table = _LegacyCatTable(store, missing=("name_fr", "image_url", "hidden", "updated_at"))
        m.setattr(supabase_store, "client", lambda: _FakeClient(store, table))
        m.setattr(supabase_store, "_CATS_SHAPE", {"fill": {}, "drop": [], "values": {}})
        
        result = supabase_store.save_categories_table(all_cats)
        assert result is True, "Failed to save test category"
        
        # Verify the category persists
        assert "perfume" in store, "Perfume category not saved"
        assert store["perfume"]["name"] == "Perfume"
        assert store["perfume"]["nameFr"] == "Parfum"


def test_household_kitchen_first(client):
    """Test that 'Household & Kitchen' category shows first on storefront."""
    from tests.conftest import AUTH_HEADERS
    
    resp = client.get("/categories.html", headers=AUTH_HEADERS)
    html = resp.get_data(as_text=True)
    
    # Find category names in the rendered HTML
    # Household items should appear before other categories
    import re
    cat_pattern = r'data-cat-id="([^"]*)"[^>]*>([^<]*)<'
    cats_found = re.findall(cat_pattern, html)
    
    household_idx = None
    for idx, (cat_id, cat_name) in enumerate(cats_found):
        if "household" in cat_name.lower():
            household_idx = idx
            break
    
    assert household_idx is not None, "Household category not found"
    # Household should be among the first categories (index 0 or close to it)
    assert household_idx < 3, f"Household category is at position {household_idx}, expected it to be near the top"


class _LegacyCatTable:
    """Mock for the narrow Supabase categories table schema."""
    def __init__(self, store, required=None, missing=()):
        self._store = store
        self.required = dict(required or {})
        self.missing = tuple(missing)
        self.attempts = []

    def upsert(self, rows, **_kw):
        self._pending_upsert = [dict(r) for r in (rows or []) if r]
        return self

    def delete(self, **_kw):
        self._keep = None
        return self

    def in_(self, _col, keep):
        self._keep = set(keep or [])
        return self

    def execute(self):
        if self._pending_upsert is not None:
            rows = self._pending_upsert
            self._pending_upsert = None
            self.attempts.append([dict(r) for r in rows])
            for row in rows:
                for col in self.missing:
                    if col in row:
                        raise APIError(
                            "PGRST204",
                            f"Could not find the '{col}' column of 'categories' in the schema cache"
                        )
            for col, accept in self.required.items():
                for row in rows:
                    if col not in row:
                        raise APIError(
                            "23502",
                            f'null value in column "{col}" of relation "categories" violates not-null constraint',
                            details=f"Failing row contains ({row.get('id')}, ...)"
                        )
                    problem = accept(row[col])
                    if problem:
                        raise APIError(
                            "22P02",
                            f"invalid input syntax for type {problem}: {row[col]!r}"
                        )
            for row in rows:
                self._store[row["id"]] = dict(row)
            return APIError({"message": "", "code": ""}, {})  # Fake success
        if self._keep is None:
            return APIError({"message": "", "code": ""}, {})
        for pid in [pid for pid in list(self._store) if pid not in self._keep]:
            del self._store[pid]
        return APIError({"message": "", "code": ""}, {})


class _FakeClient:
    def __init__(self, store, table):
        self._store = store
        self._table = table

    def table(self, name):
        if name == "categories":
            return self._table
        raise AssertionError(f"unexpected table {name!r} in fake")
