"""Tests for smart FCFA vs NGN auto-routing (Task 2).

During historical data parsing each row is inspected for currency markers
(including destination country / well-known city names), and FCFA rows land
on the FCFA ledger while NGN rows land on the NGN ledger.
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import import_historical_orders as imp
import accounting


def _import(headers, rows, default="NGN", rate=None):
    if rate is None:
        rate = accounting.LEGACY_RATE
    return imp.import_rows(headers, rows, rate=rate, default_currency=default)


def test_explicit_currency_column_routes_correctly():
    headers = ["Date", "Currency", "Selling Price"]
    result = _import(headers, [
        ["2024-03-14", "NGN", "45,000"],
        ["2024-03-15", "CFA", "62,000 FCFA"],
        ["2024-03-16", "FCFA", "8,000"],
    ])
    assert len(result["ngn"]) == 1
    assert len(result["cfa"]) == 2
    assert [i["currency"] for _, i in result["ngn"]] == ["NGN"]
    assert [i["currencyHow"] for _, i in result["ngn"]] == ["explicit"]


def test_cfa_markers_in_row_body_route_to_cfa():
    headers = ["Date", "Customer", "Total"]
    result = _import(headers, [
        ["2024-03-14", "Marie", "12 500 FCFA"],
        ["2024-03-15", "Ada", "₦45,000"],
    ])
    assert len(result["cfa"]) == 1
    assert len(result["ngn"]) == 1
    assert result["cfa"][0][1]["currencyHow"] == "inferred"


def test_country_column_routes_benin_to_cfa():
    headers = ["Date", "Country", "Selling Price"]
    result = _import(headers, [
        ["2024-03-14", "Benin", "12500"],
        ["2024-03-15", "Nigeria", "45000"],
        ["2024-03-16", "Togo", "8000"],
    ])
    assert len(result["cfa"]) == 2
    assert len(result["ngn"]) == 1
    currencies = sorted([i["currency"] for _, i in result["cfa"]])
    assert currencies == ["CFA", "CFA"]


def test_cotonou_destination_routes_to_cfa():
    headers = ["Date", "Town", "Selling Price"]
    result = _import(headers, [["2024-03-14", "Cotonou", "12500"]])
    assert len(result["cfa"]) == 1
    info = result["cfa"][0][1]
    assert info["currency"] == "CFA"
    assert info["currencyHow"] == "inferred-location"


def test_lagos_destination_routes_to_ngn():
    headers = ["Date", "City", "Selling Price"]
    result = _import(headers, [["2024-03-14", "Lagos", "45000"]])
    assert len(result["ngn"]) == 1
    info = result["ngn"][0][1]
    assert info["currency"] == "NGN"


def test_default_currency_used_when_no_signal():
    headers = ["Date", "Customer", "Selling Price"]
    result = _import(headers, [["2024-03-14", "Anonymous", "45000"]])
    assert len(result["ngn"]) == 1
    assert result["ngn"][0][1]["currencyHow"] == "default"
