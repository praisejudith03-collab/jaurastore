"""Shared pytest fixtures.

The suite writes to a single /tmp catalogue file. Confirming an order now
decrements product.stock, so a leftover edit to a seed (wix-*) row would
poison later tests such as test_over_order_rejected_with_counts (expects 24).
Restore seed stock after every test without weakening any assertion.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_seed_stock():
    yield
    try:
        import catalog as catalog_mod
        seed = {str(p.get("id")): p for p in catalog_mod._seed_products()}
        data, path = catalog_mod._load_overrides()
        products = data.get("products") or []
        changed = False
        for p in products:
            src = seed.get(str(p.get("id") or ""))
            if not src:
                continue
            if p.get("stock") != src.get("stock") or p.get("optionStock") != src.get("optionStock"):
                p["stock"] = src.get("stock")
                if "optionStock" in src:
                    p["optionStock"] = src.get("optionStock")
                elif "optionStock" in p:
                    p.pop("optionStock", None)
                changed = True
        if changed:
            catalog_mod._write_overrides(data, path)
    except Exception:
        pass
