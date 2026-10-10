"""Tests for the catalog default supplier cost fallback (Task 3).

When an order stages or is processed with a blank/zero supplier cost, the
backend must look up the product's default supplier cost from the catalogue
(using the watchdog price book first, then a retail-price ratio estimate)
and populate Column G so the sheet never ships an empty supplier figure.
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounting
from decimal import Decimal


def _make_order(items, total=10000, currency="NGN"):
    return {
        "id": "T-001",
        "status": "confirmed",
        "total": total,
        "currency": currency,
        "at": "2026-10-09T12:00:00Z",
        "payload": {
            "id": "T-001",
            "total": total,
            "currency": currency,
            "customer": {"name": "Test"},
            "items": items,
        },
    }


def test_saved_supplier_defaults_falls_back_to_catalog_ratio(monkeypatch):
    """When the price book is empty, defaults use a ratio of the NGN retail price."""
    monkeypatch.setattr(
        "supplier_watchdog.saved_price_book",
        lambda: {},
        raising=False,
    )

    class _FakeProduct(dict):
        pass

    fake_index = {
        "prod-1": {"id": "prod-1", "priceNgn": 10000},
    }
    monkeypatch.setattr("catalog.product_index", lambda: fake_index)

    items = [{"id": "prod-1", "qty": 2}]
    defaults = accounting.saved_supplier_defaults(items)
    assert defaults["qty"] == 2
    # 55% of 10,000 = 5,500 per unit × 2 = 11,000
    assert defaults["costNgn"] == 11000


def test_apply_supplier_defaults_to_snapshot_fills_zero_cost(monkeypatch):
    """Blank/zero supplier cost on a snapshot gets filled in."""
    monkeypatch.setattr(
        "supplier_watchdog.saved_price_book",
        lambda: {"prod-1": {"prices": {"product": 3000}}},
        raising=False,
    )
    order = _make_order([{"id": "prod-1", "qty": 1}], total=10000)
    snapshot = {
        "saleAmount": 10000,
        "supplierCostNgn": 0,
        "supplierUnitPriceNgn": 0,
        "supplierQty": 1,
        "supplierLink": "",
    }
    filled = accounting.apply_supplier_defaults_to_snapshot(snapshot, order)
    assert filled["supplierCostNgn"] == 3000
    assert filled["supplierUnitPriceNgn"] == 3000


def test_apply_supplier_defaults_keeps_existing_cost():
    """A snapshot that already has a supplier cost is not overwritten."""
    order = _make_order([{"id": "prod-1", "qty": 1}])
    snapshot = {
        "saleAmount": 10000,
        "supplierCostNgn": 4200,
        "supplierUnitPriceNgn": 4200,
        "supplierQty": 1,
        "supplierLink": "https://supplier.example/x",
    }
    filled = accounting.apply_supplier_defaults_to_snapshot(snapshot, order)
    assert filled["supplierCostNgn"] == 4200
    assert filled["supplierLink"] == "https://supplier.example/x"


def test_account_block_backfills_blank_supplier_cost(monkeypatch):
    """account_block auto-fills zero supplier cost for legacy orders."""
    monkeypatch.setattr(
        "supplier_watchdog.saved_price_book",
        lambda: {"prod-x": {"prices": {"product": 2500}}},
        raising=False,
    )
    order = _make_order([{"id": "prod-x", "qty": 1}])
    snap = accounting.account_block(order)
    assert snap["supplierCostNgn"] == 2500
