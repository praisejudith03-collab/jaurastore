"""Admin product API aliases preserve variant fields without data loss."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("SECRET_KEY", "test-secret")

import auth as authmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
from app import create_app  # noqa: E402
from db import init_db  # noqa: E402


def _csrf(client):
    return client.get("/api/csrf").get_json()["token"]


def test_products_variants_alias_patches_existing_product(monkeypatch, tmp_path):
    monkeypatch.setattr(authmod, "current_admin", lambda: "admin@example.com")
    monkeypatch.setattr(authmod, "require_admin", lambda fn: fn)
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "catalog.json"))
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(tmp_path / "catalog.json"))
    init_db()
    app = create_app()
    client = app.test_client()
    token = _csrf(client)

    base = {
        "id": "jau-var-alias",
        "name": "Variant Alias",
        "priceNgn": 1000,
        "stock": 5,
        "options": [{"title": "Colour", "values": ["Chocolate", "Black"]}],
        "optionStock": {"Chocolate": 2, "Black": 3},
    }
    r = client.post("/api/admin/products", json=base, headers={"X-CSRF-Token": token})
    assert r.status_code == 200, r.get_data(as_text=True)

    patch = {
        "productId": "jau-var-alias",
        "variant_prices": {"Colour: Chocolate": 1200},
        "variant_compare_at": {"Colour: Chocolate": 1500},
        "option_supplier_urls": {"Colour: Chocolate": "https://supplier.example/choc"},
        "variant_skus": {"Colour: Chocolate": "CHOCO-1"},
        "optionStock": {"Chocolate": 0, "Black": 3},
        "supplier_url": "https://supplier.example/main",
        "bulk_quantity": 10,
        "bulk_discount_percent": 15,
    }
    r = client.put("/api/products/variants", json=patch, headers={"X-CSRF-Token": token})
    assert r.status_code == 200, r.get_data(as_text=True)
    product = r.get_json()["product"]
    assert product["optionPrices"]["Colour: Chocolate"] == 1200
    assert product["optionCompareAt"]["Colour: Chocolate"] == 1500
    assert product["optionSupplierSku"]["Colour: Chocolate"] == "https://supplier.example/choc"
    assert product["optionSku"]["Colour: Chocolate"] == "CHOCO-1"
    assert product["optionStock"] == {"Chocolate": 0, "Black": 3}
    assert product["supplierSku"] == "https://supplier.example/main"
    assert product["bulkQty"] == 10 and product["bulkPercent"] == 15
