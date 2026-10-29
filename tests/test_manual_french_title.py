"""Manual French product titles (owner directive, this session).

The owner types the exact French name per product — especially perfumes —
in a dedicated "Name (French) / Title (Français)" box. Before this, the
product form had NO nameFr input and the save payload dropped the field,
so a product created in Admin could never carry a French title: French
shoppers and WhatsApp posts always fell back to English.

Run with:  python3 -m pytest tests/test_manual_french_title.py -q
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")


def _admin_js():
    return open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()


def test_product_form_has_the_french_title_box():
    js = _admin_js()
    assert "Name (French) / Title (Français)" in js
    assert 'name="nameFr"' in js
    # Editing an existing product shows its saved French title.
    assert "p.nameFr || p.name_fr" in js


def test_save_payload_ships_the_typed_french_title():
    js = _admin_js()
    assert 'nameFr: String(fd.get("nameFr") || "").trim()' in js
    # ...and the per-field merge knows the box edits nameFr, so a
    # concurrent save cannot silently keep a stale French title.
    assert 'nameFr: ["nameFr"]' in js


def test_server_round_trip_preserves_the_owners_exact_french():
    import catalog
    row = catalog.normalize({
        "id": "jau-perf-99", "name": "Elegance Perfume 50ml",
        "nameFr": "Parfum Élégance 50 ml", "category": "perfume",
        "priceNgn": 8500, "stock": 4})
    assert row is not None
    assert row["nameFr"] == "Parfum Élégance 50 ml"
    # Read-time French backfill only fills BLANKS — the owner's wording wins.
    filled = catalog._with_french_description(dict(row))
    assert filled["nameFr"] == "Parfum Élégance 50 ml"


def test_whatsapp_broadcast_uses_the_typed_title():
    js = _admin_js()
    assert "broadcastDisplayName" in js and "p.nameFr" in js


def test_currency_language_rule_still_holds():
    """FR -> CFA only (toggle hidden); EN -> NGN first with the toggle."""
    js = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    assert "function currencyLocked()" in js
    assert 'window.I18N.lang() === "fr"' in js
    assert 'if (currencyLocked()) return "CFA";' in js
    assert "el.hidden = locked" in js                 # pill hidden in French
    assert 'if (!manualCurrency()) return "NGN";' in js  # English opens in Naira
