"""Supplier stock sync rules - freeze the owner-specified contract.

  * supplier lower                    -> reduce Jaura to the supplier count
  * supplier out / 0                  -> Jaura product/variant goes out of stock
  * supplier higher (counted)         -> NEVER auto-increase past the owner's
                                         hand-entered quantity, unless the safe
                                         SUPPLIER_STOCK_AUTO_INCREASE setting is on
  * supplier "in stock", no number    -> keep the owner's positive count
  * simple products (no variants)     -> the rule applies to the whole product
  * variant products                  -> only the matched variant moves
  * unmatched variants                -> keep current stock, warning logged
  * unreadable / uncertain supplier page -> nothing changes, warning logged
  * the supplier LINK itself is never removed by a sync

Run with:  python3 -m pytest tests/test_supplier_stock_rules.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")

import pytest  # noqa: E402

import supplier_watchdog  # noqa: E402

SUPPLIER = "https://supplier.example/item/42"


def _variants_html(*rows):
    """rows of (title, available, qty|None)."""
    items = []
    for title, available, qty in rows:
        row = {"title": title, "available": available}
        if qty is not None:
            row["quantity"] = qty
        items.append(row)
    import json
    return '<script type="application/json">{"variants": %s}</script>' % json.dumps(items)


@pytest.fixture()
def saved(monkeypatch):
    box = {}

    def fake_upsert(row, actor=None):
        box.clear()
        box.update(row)
        return row, "updated", True

    monkeypatch.setattr(supplier_watchdog.catalog_mod, "upsert", fake_upsert)
    return box


def _product(**over):
    p = {
        "id": "jau-sup-1",
        "name": "Shea Glow Set",
        "priceNgn": 1000,
        "supplierSku": SUPPLIER,
        "options": [{"title": "Type", "values": ["Serum", "Cream"]}],
        "optionStock": {"Serum": 10, "Cream": 4},
        "stock": 14,
    }
    p.update(over)
    return p


def _sync(monkeypatch, product, html, auto_increase=False):
    monkeypatch.setattr(supplier_watchdog, "fetch_url", lambda url: html)
    monkeypatch.setenv("SUPPLIER_STOCK_AUTO_INCREASE", "1" if auto_increase else "0")
    return supplier_watchdog.sync_product(product)


# ------------------------------------------------------------- variant rules

def test_supplier_lower_reduces_to_supplier_count(monkeypatch, saved):
    ok, warns = _sync(monkeypatch, _product(),
                      _variants_html(("Serum", True, 3), ("Cream", True, 4)))
    assert ok is True
    assert saved["optionStock"]["Serum"] == 3
    assert saved["optionStock"]["Cream"] == 4
    assert saved["stock"] == 7


def test_supplier_out_of_stock_zeroes_the_matched_variant(monkeypatch, saved):
    ok, warns = _sync(monkeypatch, _product(),
                      _variants_html(("Serum", False, None), ("Cream", True, 4)))
    assert ok is True
    assert saved["optionStock"]["Serum"] == 0
    assert saved["optionStock"]["Cream"] == 4
    assert saved["stock"] == 4


def test_supplier_higher_never_increases_by_default(monkeypatch, saved):
    changed, warns = _sync(monkeypatch, _product(),
                           _variants_html(("Serum", True, 25), ("Cream", True, 4)),
                           auto_increase=False)
    # Serum would rise 10 -> 25: forbidden. Cream unchanged. No write at all.
    assert changed is False
    assert saved.get("optionStock") is None


def test_supplier_higher_raises_only_with_the_safe_setting(monkeypatch, saved):
    ok, warns = _sync(monkeypatch, _product(),
                      _variants_html(("Serum", True, 25), ("Cream", True, 4)),
                      auto_increase=True)
    assert ok is True
    assert saved["optionStock"]["Serum"] == 25
    assert saved["stock"] == 29


def test_boolean_in_stock_keeps_the_owners_count(monkeypatch, saved):
    changed, warns = _sync(monkeypatch, _product(),
                           _variants_html(("Serum", True, None), ("Cream", True, 4)))
    # Serum 10 -> boolean "in": never shrink a counted stock to a boolean.
    assert changed is False
    assert saved.get("optionStock") is None


def test_boolean_in_stock_revives_a_zero_only_with_the_setting(monkeypatch, saved):
    p = _product(optionStock={"Serum": 0, "Cream": 4}, stock=4)
    changed, _ = _sync(monkeypatch, p,
                       _variants_html(("Serum", True, None), ("Cream", True, 4)),
                       auto_increase=False)
    assert changed is False
    ok, _ = _sync(monkeypatch, p,
                  _variants_html(("Serum", True, None), ("Cream", True, 4)),
                  auto_increase=True)
    assert ok is True
    assert saved["optionStock"]["Serum"] == 1


def test_unmatched_variants_keep_their_stock_and_warn(monkeypatch, saved):
    html = _variants_html(("Serum", True, 2))   # supplier page has no Cream row
    ok, warns = _sync(monkeypatch, _product(), html)
    assert ok is True
    assert saved["optionStock"]["Serum"] == 2
    assert saved["optionStock"]["Cream"] == 4, "unmatched variant must not move"
    assert any(w["code"] == "supplier_partial_match" for w in warns)


def test_uncertain_supplier_page_changes_nothing_and_warns(monkeypatch, saved):
    changed, warns = _sync(monkeypatch, _product(), "<html><body>shop closed</body></html>")
    assert changed is False
    assert saved.get("optionStock") is None
    assert any(w["code"] in ("supplier_no_variants", "supplier_no_matches") for w in warns)


def test_fetch_failure_changes_nothing_and_warns(monkeypatch, saved):
    def boom(url):
        raise OSError("connection reset")
    monkeypatch.setattr(supplier_watchdog, "fetch_url", boom)
    monkeypatch.setenv("SUPPLIER_STOCK_AUTO_INCREASE", "0")
    changed, warns = supplier_watchdog.sync_product(_product())
    assert changed is False
    assert saved.get("optionStock") is None
    assert any(w["code"] == "supplier_fetch_failed" for w in warns)


# -------------------------------------------------------- whole-product rule

def test_simple_product_syncs_as_a_whole(monkeypatch, saved):
    p = _product(options=[], optionStock={}, stock=12)
    ok, warns = _sync(monkeypatch, p,
                      '<script type="application/json">{"quantity": 5, "name": "Shea Glow Set"}</script>')
    assert ok is True
    assert saved["stock"] == 5
    assert saved["stock_quantity"] == 5
    assert "optionStock" not in saved or not saved.get("optionStock")


def test_simple_product_ambiguous_supplier_page_is_untouched(monkeypatch, saved):
    p = _product(name="Shea Glow Set", options=[], optionStock={}, stock=12)
    html = _variants_html(("Night Repair Oil", True, 3), ("Hand Sanitizer XL", True, 8))
    changed, warns = _sync(monkeypatch, p, html)
    assert changed is False
    assert saved.get("stock") is None, "no confident name match -> nothing written"
    assert any(w["code"] == "supplier_no_product_match" for w in warns)


def test_simple_product_out_of_stock(monkeypatch, saved):
    p = _product(options=[], optionStock={}, stock=12)
    ok, warns = _sync(monkeypatch, p,
                      '<script type="application/json">{"quantity": 0, "name": "Shea Glow Set"}</script>')
    assert ok is True
    assert saved["stock"] == 0
    assert saved["stock_quantity"] == 0


def test_simple_product_higher_never_increases_by_default(monkeypatch, saved):
    p = _product(options=[], optionStock={}, stock=2)
    changed, warns = _sync(monkeypatch, p,
                           '<script type="application/json">{"quantity": 40, "name": "Shea Glow Set"}</script>')
    assert changed is False
    assert saved.get("stock") is None


# ------------------------------------------------------------- link safety

def test_the_supplier_link_itself_survives_a_sync(monkeypatch, saved):
    ok, warns = _sync(monkeypatch, _product(),
                      _variants_html(("Serum", True, 7), ("Cream", True, 4)))
    assert ok is True
    assert saved["optionStock"]["Serum"] == 7
    assert saved.get("supplierSku") == SUPPLIER, "a sync must never strip the supplier link"


def test_product_supplier_urls_reads_every_alias():
    for key in ("supplierSku", "supplierUrl", "supplier_url", "supplierURL", "supplier_sku"):
        p = {"id": "x", key: SUPPLIER}
        assert supplier_watchdog.product_supplier_urls(p) == [SUPPLIER], key
    p = {"id": "x", "optionSupplierSku": {"Serum": SUPPLIER}}
    assert supplier_watchdog.product_supplier_urls(p) == [SUPPLIER]
