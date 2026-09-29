"""Admin uncertainty badge and product highlighting regression coverage."""
from pathlib import Path

ROOT = Path(__file__).parents[1]
ADMIN = (ROOT / "js" / "admin.js").read_text(encoding="utf-8")
API = (ROOT / "api.py").read_text(encoding="utf-8")
STORE = (ROOT / "js" / "store.js").read_text(encoding="utf-8")
CATALOG = (ROOT / "catalog.py").read_text(encoding="utf-8")


def test_needs_attention_returns_durable_supplier_warnings():
    assert "load_supplier_sync_warnings()" in API
    assert "supplierWarnings=supplier_warnings" in API


def test_admin_has_alert_badges_and_a_supplier_warning_queue():
    assert "data-supplier-badge" in ADMIN
    assert "Supplier sync uncertain" in ADMIN
    assert "supplierWarnings = d.supplierWarnings || []" in ADMIN
    assert "Review highlighted products" in ADMIN


def test_uncertain_product_cards_and_editor_are_highlighted():
    assert "has-sync-warning" in ADMIN
    assert "Sync uncertain" in ADMIN
    assert "Supplier stock is unconfirmed" in ADMIN
    assert ".adx-card.has-sync-warning" in (ROOT / "css" / "style.css").read_text(encoding="utf-8")


def test_multi_option_stock_prefers_the_complete_variant_key():
    assert "wholeStatus = stockFold(variant)" in STORE
    assert "whole = stockFold(variant)" in STORE
    assert "whole = fold_option_value(variant)" in CATALOG


def test_warning_badge_is_interactive_and_excludes_in_house_inventory():
    assert 'data-review-supplier' in ADMIN
    assert 'Supplier link unverified or stock out of sync. Tap to review/link manually.' in ADMIN
    assert 'String(p.supplierId || "").toLowerCase() === "splendall"' in ADMIN
    assert 'field.scrollIntoView' in ADMIN
    assert 'field.focus({ preventScroll: true })' in ADMIN
