"""Master-fix regression contract for the Jaura Store release."""
from pathlib import Path

ROOT = Path(__file__).parents[1]

def text(path):
    return (ROOT / path).read_text(encoding="utf-8")

def test_master_fix_sources_are_present():
    for path in ("api.py", "catalog.py", "js/admin.js", "js/store.js",
                 "css/style.css", "tools/supplier_stock_sync.py"):
        assert (ROOT / path).is_file()

def test_proof_failure_is_persisted_and_shown():
    assert "PROOF_UPLOAD_FAILURE_NOTE" in text("api.py")
    assert "proofUploadFailed" in text("js/admin.js")
    assert "proof-upload-failed" in text("css/style.css")

def test_browser_and_catalog_accept_legacy_option_maps():
    assert "JSON.parse(raw)" in text("js/store.js")
    catalog = text("catalog.py")
    assert "option_compare_at" in catalog and "option_supplier_sku" in catalog

def test_supplier_contract_covers_multiple_urls_and_cap():
    sync = text("tools/supplier_stock_sync.py")
    assert "SUPPLIER_STOCK_CAP" in sync
    assert "supplier_urls" in sync
    assert "capped_supplier_qty" in sync
