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


def test_multiple_supplier_urls_are_fetched_and_preserved(monkeypatch):
    """Two listings for one merchant option are separate supplier reads."""
    from tools import supplier_stock_sync as sync

    product = {
        "id": "multi-url",
        "options": [{"title": "Color", "values": ["Red"]}],
        "optionSupplierSku": {"Red": ["https://supplier/one", "https://supplier/two"]},
        "optionStock": {"Red": 0},
    }
    seen = []

    def fetch(url):
        seen.append(url)
        return {"https://supplier/one": 0, "https://supplier/two": 7}[url], "confirmed"

    resolved = sync.resolve_option_links(sync._store_variant_rows(product), product["optionSupplierSku"])
    assert resolved["links"]["Red"] == product["optionSupplierSku"]["Red"]
    stock, audit, missing, review = sync.fetch_per_option_supplier_stock(product, fetch=fetch)
    assert seen == ["https://supplier/one", "https://supplier/two"]
    assert stock == {"Red": 7}
    assert [row["quantity"] for row in audit] == [0, 7]
    assert not missing and not review


def test_multiple_supplier_urls_share_one_positive_cap(monkeypatch):
    from tools import supplier_stock_sync as sync

    product = {
        "options": [{"title": "Color", "values": ["Red"]}],
        "optionSupplierSku": {"Red": ["one", "two"]},
    }
    stock, _audit, _missing, _review = sync.fetch_per_option_supplier_stock(
        product, fetch=lambda url: (18, "confirmed"))
    assert stock["Red"] == 20


def test_positive_supplier_quantity_restores_zero_variant(monkeypatch):
    from tools import supplier_stock_sync as sync

    product = {
        "id": "restore-variant", "name": "Restore variant", "supplierId": "splendall",
        "options": [{"title": "Color", "values": ["Red"]}],
        "optionSupplierSku": {"Red": "red-url"},
        "optionStock": {"Red": 0}, "stock": 0, "stock_quantity": 0,
    }
    writes = []
    monkeypatch.setattr(sync, "fetch_supplier_quantity", lambda url: (9, "confirmed"))
    monkeypatch.setattr(sync, "_patch_product", lambda pid, changes, actor=None: writes.append(changes) or True)
    result = sync.sync_one(product, warnings={})
    assert result == "updated"
    assert writes[0]["optionStock"]["Red"] == 9
    assert writes[0]["stock"] == 9
    assert writes[0]["stock_quantity"] == 9


def test_shared_asset_token_is_165():
    from tests import test_asset_cache_token as assets
    assert assets.TOKEN == "165"
    assert 'const VERSION = "jaura-v165";' in (ROOT / "sw.js").read_text(encoding="utf-8")
