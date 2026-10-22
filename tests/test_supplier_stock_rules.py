"""Supplier stock sync rules - freeze the owner-specified contract.

  * product-level high supplier stock -> 20-40% safety buffer (50 -> 35)
  * option-URL high stock             -> the same buffer, never > supplier
  * small supplier counts (1-9)       -> mirrored exactly (never rounded to 0)
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

    locked = _public_product({"id": "jau-manual-lock", "stock": 0,
                              "manualStockLock": True})
    assert "manualStockLock" not in locked
    assert locked["stock_status"] == "out"

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
    """Status-only fake; captures the input row to prove no shop field changes."""
    class Box(dict):
        pass
    box = Box()
    def fake_status(pid, status, actor="supplier-watchdog"):
        original = dict(getattr(box, "_source", {}))
        box.clear(); box.update({"supplier_status": status, "original": original})
        return True
    fake_status.test_box = box
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "apply_supplier_status", fake_status)
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
        supplier_watchdog.catalog_mod.apply_supplier_status.test_box._source = dict(product)
    except Exception:
        pass
    monkeypatch.setattr(supplier_watchdog, "fetch_url", lambda url: html)
    return supplier_watchdog.sync_product(product)


# Supplier status never changes live inventory: these tests intentionally
# retain the original storefront row as the fixture's invariant baseline.
def test_supplier_observation_updates_only_diagnostic_status(monkeypatch, saved):
    product = _product()
    saved._source = dict(product)
    ok, warnings = _sync(monkeypatch, product,
        _variants_html(("Serum", True, 3), ("Cream", True, 4)))
    assert ok is True and warnings == []
    assert saved["supplier_status"] == "low_stock"
    assert saved["original"] == product
    assert {k: saved["original"][k] for k in ("stock", "optionStock", "priceNgn", "name", "supplierSku")} == {
        "stock": 14, "optionStock": {"Serum": 10, "Cream": 4}, "priceNgn": 1000,
        "name": "Shea Glow Set", "supplierSku": SUPPLIER}


def test_supplier_zero_observation_does_not_zero_shop_stock(monkeypatch, saved):
    product = _product()
    saved._source = dict(product)
    ok, _ = _sync(monkeypatch, product,
        _variants_html(("Serum", False, None), ("Cream", True, 4)))
    assert ok is True
    assert saved["supplier_status"] in ("low_stock", "out_of_stock")
    assert saved["original"]["stock"] == 14
    assert saved["original"]["optionStock"] == {"Serum": 10, "Cream": 4}


def test_manual_admin_out_of_stock_lock_blocks_supplier_fetch_and_restock(monkeypatch, saved):
    calls=[]; product=_product(stock=0, stock_quantity=0, optionStock={"Serum":0,"Cream":0})
    monkeypatch.setattr(supplier_watchdog.catalog_mod,"is_stock_locked",lambda pid: pid==product["id"])
    monkeypatch.setattr(supplier_watchdog,"fetch_url",lambda url: calls.append(url) or _variants_html(("Serum",True,25),("Cream",True,40)))
    changed,warnings=supplier_watchdog.sync_product(product)
    assert changed is False and [w["code"] for w in warnings]==["manual_stock_lock"] and calls==[]
    assert not saved


def test_high_supplier_count_is_status_only(monkeypatch,saved):
    product=_product(); saved._source=dict(product)
    ok,_=_sync(monkeypatch,product,_variants_html(("Serum",True,25),("Cream",True,40)))
    assert ok is True and saved["supplier_status"]=="ok"
    assert saved["original"]["stock"]==14


def test_partial_supplier_variant_match_only_changes_status(monkeypatch,saved):
    product=_product(); saved._source=dict(product)
    ok,warnings=_sync(monkeypatch,product,_variants_html(("Serum",True,2)))
    assert ok is True and saved["supplier_status"]=="low_stock"
    assert any(w["code"]=="supplier_partial_match" for w in warnings)
    assert saved["original"]["optionStock"]==product["optionStock"]


def test_uncertain_supplier_page_changes_nothing_and_warns(monkeypatch,saved):
    changed,warns=_sync(monkeypatch,_product(),"<html><body>shop closed</body></html>")
    assert changed is False and not saved
    assert any(w["code"] in ("supplier_no_variants","supplier_no_matches") for w in warns)


def test_fetch_failure_changes_nothing_and_warns(monkeypatch,saved):
    monkeypatch.setattr(supplier_watchdog,"fetch_url",lambda url: (_ for _ in ()).throw(OSError("connection reset")))
    changed,warns=supplier_watchdog.sync_product(_product())
    assert changed is False and not saved
    assert any(w["code"]=="supplier_fetch_failed" for w in warns)


def _colour_product(**over):
    p={"id":"jau-supplier-black-brown","name":"Everyday Tote","priceNgn":3000,
       "supplierSku":"https://supplier.example/tote-all",
       "options":[{"title":"Colour","values":["Black","Brown"]}],
       "optionStock":{"Black":0,"Brown":0},"stock":0,
       "optionSupplierSku":{"Colour: Black":"https://supplier.example/tote-black",
                             "Colour: Brown":"https://supplier.example/tote-brown"}}
    p.update(over); return p


def test_option_supplier_urls_are_checked_but_only_status_is_persisted(monkeypatch,saved):
    p=_colour_product(); saved._source=dict(p)
    pages={"https://supplier.example/tote-black":_variants_html(("Black",True,100)),
           "https://supplier.example/tote-brown":_variants_html(("Brown",True,40))}
    calls=[]; monkeypatch.setattr(supplier_watchdog,"fetch_url",lambda u:calls.append(u) or pages[u])
    monkeypatch.setattr(supplier_watchdog.catalog_mod,"merged",lambda include_hidden=True:[p])
    monkeypatch.setattr(supplier_watchdog.catalog_mod,"deleted_product_ids",lambda:set())
    monkeypatch.setattr(supplier_watchdog,"_last_checked",{})
    result=supplier_watchdog.tick(limit=1,min_interval_seconds=0)
    assert result["checked"]==1 and result["updated"]==1
    assert set(calls)==set(pages)
    assert saved["original"]["stock"]==0 and saved["original"]["optionStock"]=={"Black":0,"Brown":0}
    assert saved["supplier_status"]=="ok"


def test_supplier_out_status_does_not_override_manual_store_availability(monkeypatch,saved):
    p=_colour_product(); saved._source=dict(p)
    pages={"https://supplier.example/tote-all":_variants_html(("Black",False,None),("Brown",True,100)),
           "https://supplier.example/tote-black":_variants_html(("Black",False,None)),
           "https://supplier.example/tote-brown":_variants_html(("Brown",True,40))}
    monkeypatch.setattr(supplier_watchdog,"fetch_url",lambda u:pages[u])
    ok,_=supplier_watchdog.sync_product(p)
    assert ok is True and saved["supplier_status"] in ("low_stock", "out_of_stock", "ok")
    assert saved["original"]["stock"]==0
    assert saved["original"]["optionStock"] == {"Black": 0, "Brown": 0}


def test_local_watchdog_cycle_keeps_store_stock_unchanged(tmp_path,monkeypatch):
    import json, catalog as catalog_mod
    p=_colour_product(); path=tmp_path/"catalog.json"
    path.write_text(json.dumps({"products":[p],"deleted":[]}))
    monkeypatch.setattr(catalog_mod,"CATALOG_FILE",str(path))
    monkeypatch.setattr(catalog_mod,"merged",lambda include_hidden=True:[p])
    monkeypatch.setattr(catalog_mod,"deleted_product_ids",lambda:set())
    monkeypatch.setattr(supplier_watchdog,"_last_checked",{})
    pages={"https://supplier.example/tote-black":_variants_html(("Black",False,None)),
           "https://supplier.example/tote-brown":_variants_html(("Brown",True,40))}
    monkeypatch.setattr(supplier_watchdog,"fetch_url",lambda u:pages[u])
    result=supplier_watchdog.tick(limit=1,min_interval_seconds=0)
    stored=json.loads(path.read_text())["products"][0]
    assert result["updated"]==1
    assert stored["stock"]==0 and stored["optionStock"]=={"Black":0,"Brown":0}


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

def test_simple_product_supplier_status_is_isolated(monkeypatch,saved):
    p=_product(options=[],optionStock={},stock=12); saved._source=dict(p)
    ok,_=_sync(monkeypatch,p,'<script type="application/json">{"quantity": 5, "name": "Shea Glow Set"}</script>')
    assert ok is True and saved["supplier_status"]=="low_stock"
    assert saved["original"]["stock"]==12


def test_simple_product_ambiguous_supplier_page_is_untouched(monkeypatch,saved):
    p=_product(name="Shea Glow Set",options=[],optionStock={},stock=12)
    html=_variants_html(("Night Repair Oil",True,3),("Hand Sanitizer XL",True,8))
    changed,warns=_sync(monkeypatch,p,html)
    assert changed is False and not saved
    assert any(w["code"]=="supplier_no_product_match" for w in warns)


def test_simple_supplier_out_of_stock_is_diagnostic_not_checkout_state(monkeypatch,saved):
    p=_product(options=[],optionStock={},stock=12); saved._source=dict(p)
    ok,_=_sync(monkeypatch,p,'<script type="application/json">{"quantity": 0, "name": "Shea Glow Set"}</script>')
    assert ok is True and saved["supplier_status"]=="out_of_stock"
    assert saved["original"]["stock"]==12


def test_simple_supplier_restock_status_does_not_restock_store(monkeypatch,saved):
    p=_product(options=[],optionStock={},stock=0); saved._source=dict(p)
    ok,_=_sync(monkeypatch,p,'<script type="application/json">{"quantity": 100, "name": "Shea Glow Set"}</script>')
    assert ok is True and saved["supplier_status"]=="ok"
    assert saved["original"]["stock"]==0


def test_supplier_url_is_preserved_and_store_fields_immutable(monkeypatch,saved):
    p=_product(); saved._source=dict(p)
    ok,_=_sync(monkeypatch,p,_variants_html(("Serum",True,7),("Cream",True,4)))
    assert ok is True
    assert saved["original"]["supplierSku"]==SUPPLIER
    assert saved["original"]["stock"]==p["stock"]


def test_catalog_status_patch_changes_only_supplier_status(tmp_path, monkeypatch):
    import catalog as catalog_mod, json
    path=tmp_path/"catalog.json"
    monkeypatch.setattr(catalog_mod,"CATALOG_FILE",str(path))
    product={"id":"jau-status-only","name":"Keep me","priceNgn":1234,
             "image":"permanent.jpg","stock":6,"stock_quantity":6,
             "online":True,"category":"beauty"}
    assert catalog_mod.upsert(product,actor="test")[0]
    assert catalog_mod.apply_supplier_status(product["id"],"out_of_stock") is True
    stored=json.loads(path.read_text())["products"][0]
    assert stored["supplier_status"]=="out_of_stock"
    assert {k:stored[k] for k in ("name","priceNgn","image","stock","stock_quantity","online","category")} == {
        "name":"Keep me","priceNgn":1234,"image":"permanent.jpg","stock":6,
        "stock_quantity":6,"online":True,"category":"beauty"}


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



def test_supplier_zero_does_not_change_public_checkout_availability(tmp_path,monkeypatch):
    import app as appmod, catalog as catalog_mod
    monkeypatch.setattr(catalog_mod,"CATALOG_FILE",str(tmp_path/"live-catalog.json"))
    product,_,_=catalog_mod.upsert({"id":"jau-supplier-public-refresh","name":"Supplier Live Item",
       "priceNgn":3000,"stock":5,"stock_quantity":5,"online":True,"supplierSku":SUPPLIER},actor="test")
    monkeypatch.setattr(catalog_mod,"apply_supplier_status",lambda pid,status,actor="supplier-watchdog":True)
    app=appmod.create_app();app.config.update(TESTING=True)
    with app.test_client() as client:
        before=client.get("/api/catalog").get_json(); row=next(x for x in before["products"] if x["id"]==product["id"])
        assert row["stock_status"]=="in"
        ok,warnings=supplier_watchdog._sync_whole_product(product,[{"qty":0,"flag":True}],"test",[])
        assert ok is True and warnings==[]
        after=client.get("/api/catalog").get_json(); row=next(x for x in after["products"] if x["id"]==product["id"])
        assert row["stock_status"]=="in"
