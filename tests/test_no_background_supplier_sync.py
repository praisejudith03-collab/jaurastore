"""Owner directive (2026-09-30): the autonomous background supplier
discovery/stock-sync worker is permanently removed.

Supplier links are now 100% owner-entered plain URL text fields (one for a
whole product, one per option/variant row). Nothing in the codebase reads
them to guess a match, mirror stock, or ever reset a link back to blank -
so a link can never silently revert to "Not supplier-synced" again.

Run with:  python3 -m pytest tests/test_no_background_supplier_sync.py -q
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_worker_script_is_gone():
    assert not os.path.exists(os.path.join(ROOT, "tools", "supplier_stock_sync.py"))


def test_scheduled_workflow_is_gone():
    assert not os.path.exists(
        os.path.join(ROOT, ".github", "workflows", "supplier-stock-sync.yml"))


def test_no_other_workflow_or_scheduler_references_the_removed_worker():
    hits = []
    for base, _dirs, files in os.walk(ROOT):
        if "/.git" in base or "/node_modules" in base or "/.venv" in base:
            continue
        for name in files:
            if not name.endswith((".yml", ".yaml", ".py")):
                continue
            path = os.path.join(base, name)
            if path.endswith("tests/test_no_background_supplier_sync.py"):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    content = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            if "supplier_stock_sync" in content:
                hits.append(path)
    assert not hits, f"Stale references to the removed worker: {hits}"


def test_admin_product_form_has_no_supplier_dropdown_or_auto_language():
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    # The old dropdown ("Not supplier-synced" / "Splendall") is gone - just a
    # single plain URL field remains.
    assert 'select name="supplierId"' not in admin_js
    assert "Not supplier-synced" not in admin_js
    assert "nightly sync" not in admin_js
    assert "automated tool never reads" not in admin_js
    # A clean, minimal manual field still exists for the whole product...
    assert 'name="supplierSku"' in admin_js
    assert "Supplier URL" in admin_js
    # ...and one per option/variant row.
    assert "data-opt-supplier" in admin_js


def test_admin_never_shows_a_supplier_sync_uncertainty_badge_or_alert():
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    for gone in (
        "supplierWarnings", "setSupplierBadge", "data-supplier-badge",
        "data-review-supplier", "reviewSupplierProduct", "supplierAttentionLine",
        "loadSupplierWarnings", "productHasUnlinkedOption", "supplier-sync-alert",
        "Supplier sync uncertain", "has-sync-warning",
    ):
        assert gone not in admin_js, f"Dead/removed supplier-automation reference still present: {gone}"


def test_catalog_still_stores_the_manual_supplier_fields():
    catalog = open(os.path.join(ROOT, "catalog.py"), encoding="utf-8").read()
    assert "supplierSku" in catalog
    assert "optionSupplierSku" in catalog
