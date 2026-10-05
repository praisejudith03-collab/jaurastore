"""Supplier stock sync rules - freeze the owner-specified contract.

  * product-level supplier counts    -> mirrored 1:1 (no buffer, no cap)
  * option-URL counts                -> mirrored 1:1 (no buffer, no cap)
  * bare "In stock" (no count)       -> keeps the shelf, opens a multi-unit one
  * supplier out / 0                 -> only the matched product/variant goes out
  * restock                          -> restores only the matched variant
  * simple products                  -> the product-level 40% rule applies
  * variant products                 -> own URL overrides the main URL per option
  * unmatched variants               -> keep current stock, warning logged
  * unreadable / uncertain page      -> nothing changes, warning logged
  * the supplier LINK itself         -> never removed by a sync

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


def test_public_catalog_flags_supplier_tracked_products_without_exposing_urls():
    from api import _public_product

    public = _public_product({
        "id": "jau-public-supplier", "name": "Supplier-linked item", "stock": 4,
        "supplier_id": "supplier-private-id", "supplierUrl": SUPPLIER,
        "optionSupplierUrls": {"Colour: Black": [SUPPLIER]},
    })
    assert public["supplierTracked"] is True
    assert not any(key in public for key in (
        "supplier_id", "supplierUrl", "supplierSku", "supplier_sku", "optionSupplierUrls",
        "optionSupplierSku", "variantSupplierUrls"))
    assert SUPPLIER not in str(public)
    assert "supplier-private-id" not in str(public)

    ordinary = _public_product({"id": "jau-local-item", "stock": 1})
    assert ordinary["supplierTracked"] is False

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_js = open(os.path.join(root, "js", "app.js"), encoding="utf-8").read()
    translations = open(os.path.join(root, "js", "i18n.js"), encoding="utf-8").read()
    # The PDP was purged of every supplier-facing notice (owner request
    # 2026-10-05): the flag stays private server-side, the page shows no
    # supplier box, and the translated strings are gone too - a leftover
    # translation is how a removed box creeps back in.
    assert "supplierAvailabilityHTML" not in app_js
    assert "pdp-supplier-availability" not in app_js
    assert "Supplier-linked availability" not in translations
    assert "disponibilité auprès du fournisseur" not in translations


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
    class Box(dict):
        pass

    box = Box()

    def fake_apply(pid, stock, option_changes=None, actor=None, allow_increase=False,
                   option_snapshot_keys=None):
        row = dict(getattr(box, "_source", {}))
        if option_changes is None:
            previous = max(0, int(row.get("stock_quantity", row.get("stock", 0)) or 0))
            qty = int(stock) if allow_increase else min(previous, int(stock))
        else:
            options = dict(row.get("optionStock") or {})
            for key, value in option_changes.items():
                incoming = max(0, int(value or 0))
                previous = max(0, int(options.get(key, 0) or 0))
                options[key] = incoming if allow_increase else min(previous, incoming)
            row["optionStock"] = options
            qty = sum(max(0, int(value or 0)) for value in options.values())
        row["stock"] = row["stock_quantity"] = qty
        box.clear()
        box.update(row)
        return row, "updated", True

    fake_apply.test_box = box
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "apply_supplier_stock", fake_apply)
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


def _sync(monkeypatch, product, html):
    # The test double applies the stock-only patch to the latest row, and is
    # given the candidate snapshot only as its initial state.
    try:
        supplier_watchdog.catalog_mod.apply_supplier_stock.test_box._source = dict(product)
    except Exception:
        pass
    monkeypatch.setattr(supplier_watchdog, "fetch_url", lambda url: html)
    return supplier_watchdog.sync_product(product)


# ------------------------------------------------------------- variant rules

def test_supplier_lower_reduces_to_supplier_count(monkeypatch, saved):
    ok, warns = _sync(monkeypatch, _product(),
                      _variants_html(("Serum", True, 3), ("Cream", True, 4)))
    assert ok is True
    # The supplier's own count is the shelf: 3 sells as 3, 4 as 4.
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


def test_supplier_count_is_sold_one_to_one_without_a_buffer(monkeypatch, saved):
    """No 40% buffer: 25 units on the supplier page are 25 units on the PDP."""
    ok, warns = _sync(monkeypatch, _product(),
                      _variants_html(("Serum", True, 25), ("Cream", True, 4)))
    assert ok is True
    assert saved["optionStock"]["Serum"] == 25
    assert saved["optionStock"]["Cream"] == 4
    assert saved["stock"] == 29


def test_generic_in_stock_signal_opens_a_multi_unit_shelf(monkeypatch, saved):
    """A bare "In stock" is not the number 1.

    It opens the sold-out variant to the multi-unit fallback and never
    shrinks a variant whose shelf is already higher.
    """
    p = _product(optionStock={"Serum": 0, "Cream": 25}, stock=25)
    ok, _ = _sync(monkeypatch, p,
                  _variants_html(("Serum", True, None), ("Cream", True, None)))
    assert ok is True
    assert saved["optionStock"]["Serum"] == supplier_watchdog.SUPPLIER_IN_STOCK_UNITS
    assert saved["optionStock"]["Cream"] == 25, "a bigger shelf is never shrunk by a flag"



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
    changed, warns = supplier_watchdog.sync_product(_product())
    assert changed is False
    assert saved.get("optionStock") is None
    assert any(w["code"] == "supplier_fetch_failed" for w in warns)


# ------------------------------------------------------- option URL watchdog


def _colour_product(**over):
    p = {
        "id": "jau-supplier-black-brown",
        "name": "Everyday Tote",
        "priceNgn": 3000,
        "supplierSku": "https://supplier.example/tote-all",
        "options": [{"title": "Colour", "values": ["Black", "Brown"]}],
        "optionStock": {"Black": 0, "Brown": 0},
        "stock": 0,
        "optionSupplierSku": {
            "Colour: Black": "https://supplier.example/tote-black",
            "Colour: Brown": "https://supplier.example/tote-brown",
        },
    }
    p.update(over)
    return p


def test_option_supplier_urls_win_and_watchdog_restocks_black_and_brown_separately(
        monkeypatch, saved):
    """The option URLs override a conflicting main page for each colour.

    This exercises the actual watchdog tick (the same cycle used in service).
    A 100-unit Black listing and a 40-unit Brown listing are mirrored 1:1. The
    main product URL says Black is sold out and Brown has 100; neither result
    may leak into the explicitly linked option URLs.
    """
    p = _colour_product()
    pages = {
        "https://supplier.example/tote-all": _variants_html(
            ("Black", False, None), ("Brown", True, 100)),
        "https://supplier.example/tote-black": _variants_html(("Black", True, 100)),
        "https://supplier.example/tote-brown": _variants_html(("Brown", True, 40)),
    }
    calls = []

    def fetch(url):
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(supplier_watchdog, "fetch_url", fetch)
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "merged",
                        lambda include_hidden=True: [p])
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "deleted_product_ids", lambda: set())
    monkeypatch.setattr(supplier_watchdog, "_last_checked", {})
    supplier_watchdog.catalog_mod.apply_supplier_stock.test_box._source = dict(p)

    result = supplier_watchdog.tick(limit=1, min_interval_seconds=0)

    assert result["checked"] == 1 and result["updated"] == 1
    assert calls == ["https://supplier.example/tote-black",
                     "https://supplier.example/tote-brown"]
    assert saved["optionStock"] == {"Black": 100, "Brown": 40}
    assert saved["stock"] == saved["stock_quantity"] == 140


def test_option_url_out_of_stock_is_isolated_and_main_url_sells_one_to_one(
        monkeypatch, saved):
    p = _colour_product(optionStock={"Black": 8, "Brown": 10}, stock=18,
                        optionSupplierSku={"Colour: Black": "https://supplier.example/tote-black"})
    supplier_watchdog.catalog_mod.apply_supplier_stock.test_box._source = dict(p)
    pages = {
        "https://supplier.example/tote-all": _variants_html(
            ("Black", True, 100), ("Brown", True, 100)),
        "https://supplier.example/tote-black": _variants_html(("Black", False, None)),
    }
    monkeypatch.setattr(supplier_watchdog, "fetch_url", lambda url: pages[url])

    ok, warnings = supplier_watchdog.sync_product(p)

    assert ok is True
    assert saved["optionStock"] == {"Black": 0, "Brown": 100}
    assert saved["stock"] == saved["stock_quantity"] == 100
    assert not any(w["code"] == "supplier_partial_match" for w in warnings)


def test_option_url_one_to_one_survives_a_real_local_watchdog_catalog_cycle(tmp_path, monkeypatch):
    """Exercise tick -> stock-only catalog patch -> persisted local row.

    The storage backend here is the test/local catalog, not a real Supabase
    project. It verifies the watchdog's cycle and write semantics end-to-end.
    """
    import json
    import catalog as catalog_mod

    p = _colour_product()
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"products": [p], "deleted": []}), encoding="utf-8")
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(path))
    monkeypatch.setattr(catalog_mod, "merged", lambda include_hidden=True: [p])
    monkeypatch.setattr(catalog_mod, "deleted_product_ids", lambda: set())
    monkeypatch.setattr(supplier_watchdog, "_last_checked", {})
    pages = {
        "https://supplier.example/tote-all": _variants_html(
            ("Black", False, None), ("Brown", True, 100)),
        "https://supplier.example/tote-black": _variants_html(("Black", True, 100)),
        "https://supplier.example/tote-brown": _variants_html(("Brown", True, 40)),
    }
    calls = []

    def fetch(url):
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(supplier_watchdog, "fetch_url", fetch)
    result = supplier_watchdog.tick(limit=1, min_interval_seconds=0)
    stored = json.loads(path.read_text(encoding="utf-8"))["products"][0]

    assert result["checked"] == 1 and result["updated"] == 1
    assert calls == ["https://supplier.example/tote-black",
                     "https://supplier.example/tote-brown"]
    assert stored["optionStock"] == {"Black": 100, "Brown": 40}
    assert stored["stock"] == stored["stock_quantity"] == 140


def test_removed_option_supplier_url_is_never_polled_or_restored():
    p = _colour_product(
        options=[{"title": "Colour", "values": ["Black"]}],
        optionStock={"Black": 4, "Brown": 30},
    )
    keys = supplier_watchdog._stock_keys(p, supplier_watchdog.variant_labels(p))
    assert keys == ["Black"]
    assert supplier_watchdog.option_supplier_urls_for(p, "Black") == [
        "https://supplier.example/tote-black"]
    assert "Brown" not in keys, "a stale stock/URL map cannot restore a deleted variant"


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


def test_simple_product_restock_mirrors_the_supplier_count(monkeypatch, saved):
    p = _product(options=[], optionStock={}, stock=0)
    ok, warns = _sync(monkeypatch, p,
                      '<script type="application/json">{"quantity": 100, "name": "Shea Glow Set"}</script>')
    assert ok is True
    assert saved["stock"] == 100
    assert saved["stock_quantity"] == 100


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


def test_local_supplier_patch_preserves_admin_edits_and_cannot_restore_a_sale(tmp_path, monkeypatch):
    import catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(tmp_path / "catalog.json"))
    pid = "jau-supplier-current-row"
    initial = {"id": pid, "name": "Original title", "priceNgn": 1000,
               "stock": 10, "stock_quantity": 10, "online": True}
    assert catalog_mod.upsert(initial, actor="test")[0]

    # Simulate an admin edit and checkout that happened after the watchdog
    # captured its stale product snapshot, but before its supplier write.
    latest = {**initial, "name": "Admin's current title", "priceNgn": 9000,
              "image": "latest-image.jpg", "stock": 4, "stock_quantity": 4}
    assert catalog_mod.upsert(latest, actor="admin-test")[0]

    saved, action, mirrored = catalog_mod.apply_supplier_stock(
        pid, 3, actor="supplier-test", allow_increase=False)
    assert action == "updated" and mirrored is True
    assert saved["stock"] == saved["stock_quantity"] == 3
    assert saved["name"] == "Admin's current title"
    assert saved["priceNgn"] == 9000 and saved["image"] == "latest-image.jpg"

    # A later stale supplier read of 7 must not put back units already sold.
    saved, action, _ = catalog_mod.apply_supplier_stock(
        pid, 7, actor="supplier-test", allow_increase=False)
    assert action == "updated"
    assert saved["stock"] == saved["stock_quantity"] == 3


def test_local_supplier_patch_cannot_recreate_an_option_removed_after_snapshot(tmp_path, monkeypatch):
    import catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(tmp_path / "catalog.json"))
    pid = "jau-stale-supplier-option"
    initial = {"id": pid, "name": "Variant Item", "priceNgn": 1000,
               "stock": 5, "stock_quantity": 5, "online": True,
               "options": [{"title": "Colour", "values": ["Red", "Black"]}],
               "optionStock": {"Red": 1, "Black": 4}}
    assert catalog_mod.upsert(initial, actor="test")[0]
    latest = {**initial,
              "options": [{"title": "Colour", "values": ["Black"]}],
              "optionStock": {"Black": 4}, "stock": 4, "stock_quantity": 4}
    assert catalog_mod.upsert(latest, actor="admin-test")[0]
    saved, action, mirrored = catalog_mod.apply_supplier_stock(
        pid, 10, {"Red": 4, "Black": 2}, actor="supplier-test",
        allow_increase=True, option_snapshot_keys=["Red", "Black"])
    assert action == "updated" and mirrored is True
    assert saved["optionStock"] == {"Black": 2}
    assert saved["stock"] == saved["stock_quantity"] == 2


def test_supplier_out_of_stock_reaches_the_public_catalog_promptly(tmp_path, monkeypatch):
    import api
    import app as appmod
    import catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(tmp_path / "live-catalog.json"))
    product, _, _ = catalog_mod.upsert({
        "id": "jau-supplier-public-refresh", "name": "Supplier Live Item",
        "priceNgn": 3000, "stock": 5, "stock_quantity": 5, "online": True,
        "supplierSku": SUPPLIER,
    }, actor="test")
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as client:
        before = client.get("/api/catalog").get_json()
        row = next(p for p in before["products"] if p["id"] == product["id"])
        assert row["stock_status"] == "in"

        ok, warnings = supplier_watchdog._save_synced(
            product, {**product, "stock": 0, "stock_quantity": 0},
            "supplier-watchdog", [])
        assert ok is True and warnings == []
        after = client.get("/api/catalog").get_json()
        row = next(p for p in after["products"] if p["id"] == product["id"])
        assert row["stock_status"] == "out"
        assert "stock" not in row and "stock_quantity" not in row

        ok, warnings = supplier_watchdog._save_synced(
            {**product, "stock": 0, "stock_quantity": 0},
            {**product, "stock": 40, "stock_quantity": 40},
            "supplier-watchdog", [])
        assert ok is True and warnings == []
        restored = client.get("/api/catalog").get_json()
        row = next(p for p in restored["products"] if p["id"] == product["id"])
        assert row["stock_status"] == "in"
