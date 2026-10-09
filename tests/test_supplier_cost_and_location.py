"""Supplier-cost automation, the FCFA conversion at push time, and the
customer Location / Destination ledger column.

  * a confirmed order pre-fills its NGN supplier cost + supplier link from the
    saved product defaults, so the desk opens already priced instead of 0;
  * an FCFA order converts that NGN cost with the ACTIVE admin rate when it is
    pushed to the FCFA ledger - never a hardcoded rate, and the NGN ledger
    keeps the Naira original;
  * both ledgers carry a Location / Destination column mapped from the order's
    country (falling back to city, then zone).

Run with:  python3 -m pytest tests/test_supplier_cost_and_location.py -q
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import accounting as accounting_mod  # noqa: E402
import google_sheets  # noqa: E402


def _order(oid, currency="NGN", total=40_000, items=None, customer=None):
    return {
        "id": oid, "status": "confirmed", "total": total,
        "currency": currency, "at": "2026-10-05T10:00:00Z",
        "payload": json.dumps({
            "customer": customer or {"name": "Ada"},
            "items": items if items is not None
                     else [{"id": "p1", "name": "Handbag", "qty": 2}],
        }),
    }


# ------------------------------------------------- saved supplier defaults
def test_saved_supplier_defaults_prefill_the_ngn_cost(monkeypatch):
    """The last supplier price saved per product IS the base item cost in NGN."""
    monkeypatch.setattr(
        "supplier_watchdog.saved_price_book",
        lambda: {"p1": {"prices": {"product": 3000.0}, "at": "2026-10-01"}})
    got = accounting_mod.saved_supplier_defaults(
        [{"id": "p1", "qty": 2}])
    # 3,000 NGN a unit x 2 ordered = 6,000 NGN of supplier spend.
    assert got["costNgn"] == 6000
    assert got["qty"] == 2


def test_a_variant_price_wins_over_the_product_price(monkeypatch):
    monkeypatch.setattr(
        "supplier_watchdog.saved_price_book",
        lambda: {"p1": {"prices": {"product": 3000.0, "Red": 4500.0}}})
    got = accounting_mod.saved_supplier_defaults(
        [{"id": "p1", "qty": 1, "variant": "Red"}])
    assert got["costNgn"] == 4500


def test_no_saved_price_means_zero_not_an_error(monkeypatch):
    monkeypatch.setattr("supplier_watchdog.saved_price_book", lambda: {})
    got = accounting_mod.saved_supplier_defaults([{"id": "p1", "qty": 3}])
    assert got == {"costNgn": 0, "qty": 3, "link": ""}


def test_the_saved_supplier_link_is_attached(monkeypatch):
    monkeypatch.setattr("supplier_watchdog.saved_price_book", lambda: {})
    monkeypatch.setattr(
        "catalog.product_index",
        lambda: {"p1": {"supplierSku": "https://supplier.example/handbag"}})
    got = accounting_mod.saved_supplier_defaults([{"id": "p1", "qty": 1}])
    assert got["link"] == "https://supplier.example/handbag"


def test_saved_defaults_never_raise_when_the_watchdog_is_unavailable(monkeypatch):
    def boom():
        raise RuntimeError("supabase down")

    monkeypatch.setattr("supplier_watchdog.saved_price_book", boom)
    got = accounting_mod.saved_supplier_defaults([{"id": "p1", "qty": 1}])
    assert got["costNgn"] == 0


# ------------------------------------------------ pre-filled order snapshot
def test_a_confirmation_snapshot_carries_the_saved_supplier_cost(monkeypatch):
    monkeypatch.setattr(
        "supplier_watchdog.saved_price_book",
        lambda: {"p1": {"prices": {"product": 3000.0}}})
    snap = accounting_mod.new_snapshot(
        40_000, "NGN", 0.44, "2026-10-05T10:00:00Z",
        supplier_cost_ngn=6000, supplier_qty=2,
        supplier_link="https://supplier.example/handbag")
    assert snap["supplierCostNgn"] == 6000
    assert snap["supplierUnitPriceNgn"] == 3000      # 6,000 over 2 units
    assert snap["supplierQty"] == 2
    assert snap["supplierLink"] == "https://supplier.example/handbag"


def test_a_snapshot_without_defaults_starts_at_zero():
    snap = accounting_mod.new_snapshot(40_000, "NGN", 0.44, "2026-10-05")
    assert snap["supplierCostNgn"] == 0
    assert snap["supplierUnitPriceNgn"] == 0
    assert snap["supplierQty"] == 0
    assert snap["supplierLink"] == ""


# -------------------------------- FCFA conversion at the ACTIVE push-time rate
def test_the_fcfa_ledger_converts_the_ngn_cost_at_the_active_rate():
    """FCFA cost = NGN cost x the ACTIVE rate. No hardcoded number:
    moving the rate moves the converted figure."""
    assert accounting_mod.supplier_cost_cfa(12_000, 0.44) == 5_280
    assert accounting_mod.supplier_cost_cfa(12_000, 0.50) == 6_000
    assert accounting_mod.supplier_cost_cfa(12_000, 2.0) == 24_000


def test_an_ngn_entry_keeps_the_naira_original():
    snap = {"saleAmount": 40_000, "supplierCostNgn": 12_000,
            "deliveryExpense": 0, "discount": 0}
    figures = accounting_mod.entry_figures(snap, "NGN", 0.44)
    assert figures["supplierCostNgn"] == 12_000
    assert figures["supplierCostInCurrency"] == 12_000     # Naira, unconverted
    assert figures["supplierCostCfa"] == 5_280             # shown for reference


def test_a_cfa_entry_bills_the_converted_cost():
    snap = {"saleAmount": 20_000, "supplierCostNgn": 12_000,
            "deliveryExpense": 0, "discount": 0}
    figures = accounting_mod.entry_figures(snap, "CFA", 0.44)
    assert figures["supplierCostInCurrency"] == 5_280
    assert figures["netProfit"] == 20_000 - 5_280


def test_the_sheet_row_writes_the_converted_fcfa_supplier_cost(monkeypatch):
    """The FCFA ledger's Supplier costs cell is the NGN cost converted at the
    rate locked on the order - not a figure derived from a hardcoded rate."""
    monkeypatch.setattr(accounting_mod, "current_exchange_rate",
                        lambda: __import__("decimal").Decimal("0.50"))
    order = _order("JA-CFA-9", currency="CFA", total=20_000)
    order["payload"] = json.dumps({
        "customer": {"name": "Koffi"},
        "items": [{"id": "p1", "name": "Parfum", "qty": 2}],
        "accounting": accounting_mod.new_snapshot(
            20_000, "CFA", 0.50, "2026-10-05T10:00:00Z",
            supplier_cost_ngn=12_000, supplier_qty=2),
    })
    row = google_sheets._order_row(order, batch="Batch 1")
    # 12,000 NGN x 0.50 = 6,000 F CFA in the Supplier costs column.
    assert row[6] == 6_000


# -------------------------------------------------- Location / Destination
def test_the_location_column_exists_in_both_ledgers():
    for currency in ("NGN", "CFA"):
        headers = google_sheets._headers(currency)
        assert "Location / Destination" in headers, currency
        # the money columns keep their positions, so existing workbooks read on
        assert headers[5].endswith(currency if currency == "NGN" else "FCFA")


def test_the_row_carries_the_location_after_the_notes_column():
    """Location is appended, so every existing column keeps its index."""
    row = google_sheets._order_row(_order("JA-LOC-1"))
    assert len(row) == 12
    assert row[10] == row[10]        # Notes (unchanged position)
    assert isinstance(row[11], str)  # Location / Destination


def test_location_maps_the_customer_country():
    order = _order("JA-LOC-2",
                   customer={"name": "Koffi", "country": "Benin Republic"})
    assert accounting_mod.order_location(order) == "Benin Republic"


def test_location_falls_back_to_city_then_zone():
    assert accounting_mod.order_location(
        _order("JA-LOC-3", customer={"name": "Ada", "city": "Cotonou"})) == "Cotonou"
    order = _order("JA-LOC-4", customer={"name": "Ada", "zone": "Lagos Mainland"})
    order["zone"] = "Lagos Mainland"
    # a top-level zone is read before the customer block
    assert accounting_mod.order_location(order) == "Lagos Mainland"


def test_an_order_with_no_location_is_blank_never_a_placeholder():
    assert accounting_mod.order_location(_order("JA-LOC-5")) == ""


def test_the_entry_exposes_the_location_to_the_desk():
    order = _order("JA-LOC-6", customer={"name": "Ada", "country": "Togo"})
    entry = accounting_mod.entry_from_order(order)
    assert entry["location"] == "Togo"


def test_the_pushed_row_carries_the_location(monkeypatch):
    order = _order("JA-LOC-7",
                   customer={"name": "Ada", "country": "Nigeria"})
    row = google_sheets._order_row(order)
    assert row[11] == "Nigeria"
