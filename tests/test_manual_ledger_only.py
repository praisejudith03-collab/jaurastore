"""The owner's Google Sheet ledger is manual-batch only.

Directive 2026-10-19: confirming an order must NOT push it to Google Sheets;
the owner stages confirmed orders and pushes the chosen batch from the
accounting desk after shipping/transport adjustments.

Run with: python3 -m pytest tests/test_manual_ledger_only.py -q
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _api():
    return (ROOT / "api.py").read_text(encoding="utf-8")


def test_order_confirmation_does_not_call_the_sheets_helper():
    api = _api()
    assert "sync_order_async" not in api, (
        "order confirmation must never auto-push a ledger row")
    assert "start_backfill" not in api, (
        "connecting Google Drive must not backfill confirmed orders")


def test_the_manual_batch_push_is_still_the_one_ledger_write_path():
    api = _api()
    assert '@api.post("/admin/accounting/push")' in api
    # The batch push writes only after every row is confirmed; it is the
    # explicit owner action the directive preserves.
    assert "google_sheets.push_orders(" in api
    assert "The push did not complete, so nothing was archived." in api


def test_the_confirmation_path_documents_the_manual_contract():
    api = _api()
    marker = "Deliberately NO automatic Google Sheets write here"
    assert marker in api
    assert "POST /admin/accounting/push" in api


def test_the_oauth_callback_only_stores_the_credential():
    api = _api()
    callback = api.split("def admin_accounting_google_callback():", 1)[1]
    callback = callback.split("def _accounting_mutation_error", 1)[0]
    assert "complete_authorization" in callback
    assert "_accounting_orders" not in callback
