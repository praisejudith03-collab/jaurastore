"""Supplier watchdog: in-process, single-URL multi-variant stock matching."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")

import supplier_watchdog  # noqa: E402


def test_smart_aliases_match_supplier_colour_names():
    assert supplier_watchdog.match_score("Colour: Chocolate", "Dark Brown") >= 70
    assert supplier_watchdog.match_score("Flavour: Lemon", "Lime") >= 70
    assert supplier_watchdog.match_score("Colour: Burgundy", "Wine") >= 70


def test_parser_extracts_json_variants_and_maps_only_that_variant():
    html = '''<script type="application/json">{
      "variants": [
        {"title":"Dark Brown", "available": false},
        {"title":"Black", "available": true, "quantity": 6},
        {"title":"Lime", "availability":"https://schema.org/InStock"}
      ]
    }</script>'''
    rows = supplier_watchdog.parse_supplier_variants(html, ["Chocolate", "Black", "Lemon"])
    mapped = supplier_watchdog.map_supplier_to_jaura(["Chocolate", "Black", "Lemon"], rows)
    assert mapped["Chocolate"]["qty"] == 0
    assert mapped["Black"]["qty"] == 6
    assert mapped["Lemon"]["qty"] == 1


def test_stock_update_is_variant_isolated(monkeypatch):
    product = {
        "id": "jau-watchdog",
        "name": "Watchdog Test",
        "priceNgn": 1000,
        "supplierSku": "https://supplier.example/item",
        "options": [{"title": "Colour", "values": ["Chocolate", "Black"]}],
        "optionStock": {"Chocolate": 5, "Black": 3},
        "stock": 8,
    }
    monkeypatch.setattr(supplier_watchdog, "fetch_url", lambda url: '''<script type="application/json">{
      "variants": [{"title":"Dark Brown", "available": false}, {"title":"Black", "available": true}]
    }</script>''')
    saved = {}

    def fake_upsert(row, actor=None):
        saved.update(row)
        return row, "updated", True

    monkeypatch.setattr(supplier_watchdog.catalog_mod, "upsert", fake_upsert)
    ok, warnings = supplier_watchdog.sync_product(product)
    assert ok is True
    assert warnings == []
    assert saved["optionStock"] == {"Chocolate": 0, "Black": 3}
    assert saved["stock"] == 3
