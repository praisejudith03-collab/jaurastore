"""Offline tests for the Google Sheets accounting boundary and calculations."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import accounting
import api
import catalog
import google_sheets


def _ledger_row(day, order_id, revenue, supplier=0, transport=0, batch=""):
    return [day, order_id, "Customer", "2× Item", "NGN", revenue,
            supplier, transport, "", batch, ""]


def test_monthly_summary_counts_order_and_unlinked_costs_by_batch():
    ledger = {
        "currency": "NGN",
        "orders": [google_sheets._headers("NGN"),
                   _ledger_row("2026-10-02", "JA-001", 100_000, 15_000, 2_000, "Batch A"),
                   _ledger_row("2026-10-03", "JA-002", 40_000, 3_000, 1_000, "Batch B"),
                   _ledger_row("2026-09-30", "JA-OLD", 500_000, 0, 0, "Batch A")],
        "expenses": [
            ["Date", "Batch", "Type", "Description", "Amount · NGN", "Notes"],
            ["2026-10-04", "Batch A", "Supplier Cost", "Unlinked stock", 5_000, ""],
            ["2026-10-04", "Batch A", "Transport", "Market pickup", 700, ""],
            ["2026-10-04", "Batch B", "Supplier Cost", "Other stock", 9_000, ""],
        ],
    }

    summary = google_sheets.summarize(
        ledger, period="month", batch="Batch A", today=dt.date(2026, 10, 6))

    assert summary == {
        "currency": "NGN", "period": "month",
        "periodStart": "2026-10-01", "periodEnd": "2026-10-06",
        "batch": "Batch A", "revenue": 100_000,
        "supplierCosts": 20_000, "transport": 2_700,
        "netProfit": 77_300, "orderCount": 1,
        "batches": ["Batch A", "Batch B"],
    }


def test_periods_and_fcfa_normalization_are_explicit():
    start, end = google_sheets.period_bounds("week", today=dt.date(2026, 10, 6))
    assert (start.isoformat(), end.isoformat()) == ("2026-10-05", "2026-10-06")
    start, end = google_sheets.period_bounds("year", today=dt.date(2026, 10, 6))
    assert (start.isoformat(), end.isoformat()) == ("2026-01-01", "2026-10-06")
    assert google_sheets._currency("FCFA") == "CFA"

    summary = google_sheets.summarize({
        "currency": "FCFA",
        "orders": [google_sheets._headers("CFA"),
                   ["2026-10-06", "CFA-01", "A", "Item", "CFA", 22_000,
                    8_000, 1_000, "", "", ""]],
        "expenses": [],
    }, period="month", today=dt.date(2026, 10, 6))
    assert summary["currency"] == "CFA"
    assert summary["netProfit"] == 13_000


def test_sheet_order_row_keeps_order_facts_and_manual_cost_fields():
    snapshot = accounting.new_snapshot(45_000, "NGN", 0.5, "2026-10-06T09:15:00Z")
    order = {
        "id": "JA-SHEET-001", "status": "confirmed", "total": 45_000,
        "currency": "NGN", "at": "2026-10-06T09:15:00Z",
        "customer_name": "Amina Buyer",
        "payload": {
            "customer": {"name": "Amina Buyer"},
            "items": [{"id": "product-1", "name": "Body Lotion", "qty": 2,
                       "color": "Vanilla"}],
            "accounting": snapshot,
        },
    }
    row = google_sheets._order_row(order)
    assert row[:6] == ["2026-10-06", "JA-SHEET-001", "Amina Buyer",
                       "2× Body Lotion · Vanilla", "NGN", 45_000]
    assert row[6:11] == ["", "", "", "", ""]


def test_reconfirm_sync_updates_facts_without_overwriting_sheet_costs(monkeypatch):
    order = {
        "id": "JA-SHEET-002", "status": "confirmed", "total": 12_000,
        "currency": "NGN", "at": "2026-10-06T09:15:00Z",
        "payload": {"customer": {"name": "Customer"},
                   "items": [{"name": "Soap", "qty": 1}]},
    }
    captured = []
    monkeypatch.setattr(google_sheets, "_spreadsheet_id", lambda currency: "sheet-ngn")
    monkeypatch.setattr(google_sheets, "_existing_order_rows", lambda sheet: {"JA-SHEET-002": 4})
    monkeypatch.setattr(google_sheets, "_google_request",
                        lambda method, url, body=None: captured.append((method, url, body)) or {})

    assert google_sheets.sync_order(order) is True
    assert len(captured) == 1
    method, url, body = captured[0]
    assert method == "PUT"
    assert "A4:F4" in url
    assert body["values"] == [google_sheets._order_row(order)[:6]]


def test_new_ledger_contains_dynamic_profit_formula_and_editable_batch_dropdowns(monkeypatch):
    calls = []
    monkeypatch.setattr(google_sheets, "_google_request",
                        lambda method, url, body=None: calls.append((method, url, body)) or {})

    google_sheets._configure_ledger("sheet-ngn", "NGN", {
        "Orders": 1, "Expenses": 2, "Lists": 3,
    })

    assert calls[0][0] == "POST"
    values = calls[0][2]["data"]
    assert values[1]["range"] == "'Orders'!I2"
    assert values[1]["values"] == [['=ARRAYFORMULA(IF(B2:B="","",F2:F-N(G2:G)-N(H2:H)))']]
    requests = calls[1][2]["requests"]
    validations = [item["setDataValidation"] for item in requests if "setDataValidation" in item]
    assert len(validations) == 3
    assert validations[0]["range"]["sheetId"] == 1
    assert validations[0]["range"]["startColumnIndex"] == 9
    assert validations[1]["range"]["sheetId"] == 2
    assert validations[1]["range"]["startColumnIndex"] == 1


def test_refresh_token_encryption_does_not_store_plaintext(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_TOKEN_KEY", "a stable integration test key")
    cipher = google_sheets._cipher()
    encrypted = cipher.encrypt(b"refresh-token-secret")
    assert b"refresh-token-secret" not in encrypted
    assert cipher.decrypt(encrypted) == b"refresh-token-secret"


def test_server_generated_sku_is_stable_and_variant_supplier_links_survive():
    product = {
        "id": "sku-server-generated-test", "name": "Variant item", "priceCfa": 4_000,
        "supplierSku": "VENDOR-BASE",
        "optionSupplierSku": {"Small": "VENDOR-SMALL", "Large": "VENDOR-LARGE"},
        "optionSku": {"Small": "JAU-SMALL", "Large": "JAU-LARGE"},
    }

    saved = catalog.normalize(product)
    resaved = catalog.normalize(saved)

    assert saved["sku"].startswith("JAU-")
    assert saved["sku"] == catalog.generated_product_sku(product["id"])
    assert resaved["sku"] == saved["sku"]
    assert saved["supplierSku"] == "VENDOR-BASE"
    assert saved["optionSupplierSku"] == product["optionSupplierSku"]
    assert saved["optionSku"] == product["optionSku"]


def test_best_seller_decoration_uses_sales_volumes_and_clears_stale_badges(monkeypatch):
    monkeypatch.setattr(api, "_confirmed_sales_units", lambda: {
        "product-best": 42, "product-second": 18,
    })
    products = api._apply_confirmed_best_sellers([
        {"id": "product-best", "name": "Most sold", "online": True},
        {"id": "product-second", "name": "Second most sold", "online": True},
        {"id": "product-none", "name": "No confirmed sales", "online": True,
         "badge": "bestseller"},
    ], limit=2)
    mapped = {product["id"]: product for product in products}
    assert mapped["product-best"]["bestSeller"] is True
    assert mapped["product-second"]["badge"] == "bestseller"
    assert mapped["product-none"]["bestSeller"] is False
    assert mapped["product-none"]["badge"] == ""
