"""Master-fix regression contract for the Jaura Store release.

Note (2026-09-30): the autonomous background supplier discovery/stock-sync
worker (and its GitHub Actions schedule) has been permanently removed - see
tests/test_no_background_supplier_sync.py. Supplier links are now 100%
owner-entered, plain URL fields with no automated read or write path, so
they can never be silently reset or "discovered" again. The tests that used
to pin that tool's behaviour were removed with it.
"""
from pathlib import Path

ROOT = Path(__file__).parents[1]

def text(path):
    return (ROOT / path).read_text(encoding="utf-8")

def test_master_fix_sources_are_present():
    for path in ("api.py", "catalog.py", "js/admin.js", "js/store.js",
                 "css/style.css"):
        assert (ROOT / path).is_file()

def test_receipt_upload_failure_blocks_checkout_inline():
    assert "RECEIPT_UPLOAD_FAILED_MESSAGE" in text("api.py")
    assert "receipt_upload_failed" in text("api.py")
    app_js = text("js/app.js")
    assert "Receipt upload failed. Please try choosing the photo again." in app_js
    assert "showProofUploadFailure" in app_js

def test_browser_and_catalog_accept_legacy_option_maps():
    assert "JSON.parse(raw)" in text("js/store.js")
    catalog = text("catalog.py")
    assert "option_compare_at" in catalog and "option_supplier_sku" in catalog

def test_shared_asset_token_matches_sw_version():
    from tests import test_asset_cache_token as assets
    assert f'const VERSION = "jaura-v{assets.TOKEN}";' in (ROOT / "sw.js").read_text(encoding="utf-8")
