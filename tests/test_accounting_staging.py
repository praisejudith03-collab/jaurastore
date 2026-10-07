"""Staged accounting queue: push-to-Sheets, manual expenses, live balance.

Covers the workspace-overhaul flows:
  * the accounting queue stages confirmed orders and CLEARS them on push;
  * the push routes NGN rows to the NGN tab and FCFA rows to the FCFA tab of
    the reference workbook (upserted by order id, never duplicated);
  * a failed push leaves the orders staged (nothing archived, nothing lost);
  * the manual expense logger feeds the live running bank balance
    (starting + pushed net profit - expenses - charges);
  * the Sales / History insights read the immutable pushed batches.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import accounting as accounting_mod  # noqa: E402
import app as appmod  # noqa: E402
import google_sheets  # noqa: E402
import supabase_store  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tests"))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"
ORDER_IDS = ("STG-NGN-1", "STG-NGN-2", "STG-CFA-1")
KEYS = (supabase_store.ACCOUNTING_BATCHES_KEY,
        supabase_store.ACCOUNTING_EXPENSES_KEY,
        supabase_store.ACCOUNTING_SETTINGS_KEY)


@pytest.fixture()
def client(monkeypatch):
    init_db()
    for oid in ORDER_IDS:
        execute("DELETE FROM orders WHERE id=?", (oid,))
    for key in KEYS:
        execute("DELETE FROM growth_settings WHERE key=?", (key,))
    # No real Google traffic in the offline suite.
    monkeypatch.setattr(google_sheets, "sync_order_async", lambda _order: None)
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        r = c.post("/api/admin/login", json={"email": EMAIL, "password": PW})
        assert r.status_code == 200, r.data
        yield c
    for oid in ORDER_IDS:
        execute("DELETE FROM orders WHERE id=?", (oid,))
    for key in KEYS:
        execute("DELETE FROM growth_settings WHERE key=?", (key,))


def csrf_of(client):
    r = client.get("/api/admin/session")
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def add_pending(order_id, currency, total, customer, at):
    payload = {"customer": {"name": customer},
               "items": [{"id": "stg-item", "name": "Staged item", "qty": 2}]}
    execute(
        "INSERT INTO orders (id,payload,total,currency,status,customer_name,"
        "items_count,at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (order_id, json.dumps(payload), total, currency, "confirmed", customer,
         2, at, at))


class FakePush:
    """Records every push_orders call and fakes the Google answers."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, orders, *, batch_name="", reference_id=""):
        self.calls.append({
            "orderIds": sorted(str(o.get("id")) for o in orders),
            "batchName": batch_name,
            "referenceId": reference_id,
        })
        if self.fail:
            raise google_sheets.GoogleSheetsError("Google Sheets is offline.")
        grouped = {}
        for order in orders:
            currency = google_sheets._currency(
                accounting_mod.account_block(order).get("currency")
                or order.get("currency"))
            grouped.setdefault(currency, 0)
            grouped[currency] += 1
        return {currency: {"spreadsheetId": reference_id or f"sb-{currency}",
                           "tab": google_sheets.REFERENCE_TABS[currency],
                           "url": "https://sheets.example/" + currency,
                           "count": count, "appended": count, "updated": 0}
                for currency, count in grouped.items()}


def test_push_routes_by_currency_clears_the_queue_and_keeps_history(client, monkeypatch):
    csrf = csrf_of(client)
    add_pending(ORDER_IDS[0], "NGN", 10_000, "Ada Naira One", "2026-10-01T09:00:00Z")
    add_pending(ORDER_IDS[1], "NGN", 5_000, "Ada Naira Two", "2026-10-02T09:00:00Z")
    add_pending(ORDER_IDS[2], "CFA", 20_000, "Koffi CFA", "2026-10-03T09:00:00Z")
    for oid in ORDER_IDS:
        r = client.patch(f"/api/admin/orders/{oid}", json={"status": "confirmed"},
                         headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.data

    fake = FakePush()
    monkeypatch.setattr(google_sheets, "push_orders", fake)

    # The staged queue holds every confirmed, un-pushed order.
    staged = client.get("/api/admin/accounting").get_json()
    assert {row["id"] for row in staged["entries"]} == set(ORDER_IDS)
    assert staged["settings"]["referenceSpreadsheetId"] == \
        google_sheets.DEFAULT_REFERENCE_SPREADSHEET_ID

    pushed = client.post("/api/admin/accounting/push", headers={"X-CSRF-Token": csrf},
                         json={"orderIds": list(ORDER_IDS),
                               "name": "October restock",
                               "transferFee": 100})
    assert pushed.status_code == 200, pushed.data
    body = pushed.get_json()

    # Mixed-currency pushes are routed automatically in one request.
    assert fake.calls and fake.calls[0]["referenceId"] == \
        google_sheets.DEFAULT_REFERENCE_SPREADSHEET_ID
    assert fake.calls[0]["orderIds"] == sorted(ORDER_IDS)

    by_currency = {batch["currency"]: batch for batch in body["batches"]}
    assert set(by_currency) == {"NGN", "CFA"}
    assert by_currency["NGN"]["sheet"]["tab"] == "NGN"
    assert by_currency["CFA"]["sheet"]["tab"] == "FCFA"
    assert by_currency["NGN"]["transferFee"] == 100
    assert by_currency["CFA"]["transferFee"] == 0
    assert by_currency["NGN"]["orderIds"] == [ORDER_IDS[0], ORDER_IDS[1]]
    assert by_currency["CFA"]["orderIds"] == [ORDER_IDS[2]]

    # Pushed orders clear off the active queue immediately.
    after = client.get("/api/admin/accounting").get_json()
    assert after["entries"] == []
    assert set(after["archivedOrderIds"]) == set(ORDER_IDS)
    assert after["totals"]["archivedOrders"] == 3
    # But the audit view still lists them.
    audit_view = client.get("/api/admin/accounting?includeArchived=true").get_json()
    assert {row["id"] for row in audit_view["entries"]} == set(ORDER_IDS)

    # A second push of the same orders is refused (no double counting).
    twice = client.post("/api/admin/accounting/push", headers={"X-CSRF-Token": csrf},
                        json={"orderIds": [ORDER_IDS[0]]})
    assert twice.status_code == 409
    assert "already pushed" in twice.get_json()["error"].lower()

    # Balance math: starting 0 + pushed profit - the transfer fee.
    balances = after["balances"]["NGN"]
    assert balances["startingBalance"] == 0
    assert balances["salesNetProfit"] == 15_000 - 100
    assert balances["bankCharges"] == 0
    assert balances["balance"] == 14_900


def test_failed_push_leaves_the_queue_untouched(client, monkeypatch):
    csrf = csrf_of(client)
    add_pending(ORDER_IDS[0], "NGN", 10_000, "Ada Naira One", "2026-10-01T09:00:00Z")
    r = client.patch(f"/api/admin/orders/{ORDER_IDS[0]}", json={"status": "confirmed"},
                     headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.data

    monkeypatch.setattr(google_sheets, "push_orders", FakePush(fail=True))
    failed = client.post("/api/admin/accounting/push", headers={"X-CSRF-Token": csrf},
                         json={"orderIds": [ORDER_IDS[0]]})
    assert failed.status_code == 502, failed.data
    body = failed.get_json()
    assert body["ok"] is False
    assert "nothing was archived" in body["error"].lower()

    # Still staged, nothing archived, no batch recorded.
    after = client.get("/api/admin/accounting").get_json()
    assert {row["id"] for row in after["entries"]} == {ORDER_IDS[0]}
    assert after["archivedOrderIds"] == []
    assert after["balances"]["NGN"]["balance"] == 0


def test_manual_expense_logger_feeds_the_live_balance(client):
    csrf = csrf_of(client)
    add_pending(ORDER_IDS[0], "NGN", 10_000, "Ada Naira One", "2026-10-01T09:00:00Z")
    assert client.patch(f"/api/admin/orders/{ORDER_IDS[0]}",
                        json={"status": "confirmed"},
                        headers={"X-CSRF-Token": csrf}).status_code == 200

    invalid = client.post("/api/admin/accounting/expenses",
                          headers={"X-CSRF-Token": csrf},
                          json={"kind": "nonsense", "currency": "NGN",
                                "amount": 500})
    assert invalid.status_code == 400

    stock = client.post("/api/admin/accounting/expenses",
                        headers={"X-CSRF-Token": csrf},
                        json={"kind": "purchase", "currency": "NGN",
                              "amount": 30_000, "note": "Ankara bulk buy"})
    assert stock.status_code == 200, stock.data
    fee = client.post("/api/admin/accounting/expenses",
                      headers={"X-CSRF-Token": csrf},
                      json={"kind": "fee", "currency": "NGN",
                            "amount": 100, "note": "Transfer charge"})
    assert fee.status_code == 200, fee.data

    page = client.get("/api/admin/accounting").get_json()
    ngn = page["balances"]["NGN"]
    assert ngn["manualExpenses"] == 30_000
    assert ngn["bankCharges"] == 100
    # Unpushed sales never move the balance.
    assert ngn["salesNetProfit"] == 0
    assert ngn["balance"] == -30_100
    assert page["balances"]["CFA"]["balance"] == 0

    # A payout and a CFA purchase stay in their own ledger.
    assert client.post("/api/admin/accounting/expenses",
                       headers={"X-CSRF-Token": csrf},
                       json={"kind": "payout", "currency": "CFA",
                             "amount": 5_000}).status_code == 200
    assert client.get("/api/admin/accounting").get_json()["balances"]["CFA"][
        "manualExpenses"] == 5_000

    # Deleting a mistake removes its effect.
    expense_id = fee.get_json()["expense"]["id"]
    removed = client.delete(f"/api/admin/accounting/expenses/{expense_id}",
                            headers={"X-CSRF-Token": csrf})
    assert removed.status_code == 200, removed.data
    assert removed.get_json()["balances"]["NGN"]["bankCharges"] == 0


def test_starting_balance_setting_and_reference_sheet_override(client):
    csrf = csrf_of(client)
    saved = client.put("/api/admin/accounting/settings",
                       headers={"X-CSRF-Token": csrf},
                       json={"startingBalanceNgn": 250_000,
                             "startingBalanceCfa": 40_000,
                             "referenceSpreadsheetId": "abc123"})
    assert saved.status_code == 200, saved.data
    body = saved.get_json()
    assert body["settings"]["startingBalanceNgn"] == 250_000
    assert body["settings"]["referenceSpreadsheetId"] == "abc123"
    assert body["balances"]["NGN"]["balance"] == 250_000
    assert body["balances"]["CFA"]["balance"] == 40_000

    bad = client.put("/api/admin/accounting/settings",
                     headers={"X-CSRF-Token": csrf},
                     json={"startingBalanceNgn": -5})
    assert bad.status_code == 400

    # The accounting page serves the saved settings back.
    page = client.get("/api/admin/accounting").get_json()
    assert page["settings"]["startingBalanceCfa"] == 40_000


def test_sales_insights_read_pushed_batches_by_period(client, monkeypatch):
    csrf = csrf_of(client)
    add_pending(ORDER_IDS[0], "NGN", 10_000, "Ada Naira One", "2026-10-01T09:00:00Z")
    assert client.patch(f"/api/admin/orders/{ORDER_IDS[0]}",
                        json={"status": "confirmed"},
                        headers={"X-CSRF-Token": csrf}).status_code == 200
    monkeypatch.setattr(google_sheets, "push_orders", FakePush())
    pushed = client.post("/api/admin/accounting/push", headers={"X-CSRF-Token": csrf},
                         json={"orderIds": [ORDER_IDS[0]], "name": "Week 41 push",
                               "transferFee": 150})
    assert pushed.status_code == 200, pushed.data

    insights = client.get("/api/admin/sales/insights?currency=NGN&period=month")
    assert insights.status_code == 200, insights.data
    data = insights.get_json()
    summary = data["summary"]
    assert summary["currency"] == "NGN"
    assert summary["revenue"] == 10_000
    assert summary["transferFees"] == 150
    assert summary["netProfit"] == 9_850
    assert summary["batchCount"] == 1
    assert data["batches"][0]["name"] == "Week 41 push"
    assert data["batches"][0]["netProfit"] == 9_850
    assert data["batches"][0]["tab" if False else "sheet"]["tab"] == "NGN"
    # The live balance rides along for the header strip.
    assert data["balances"]["NGN"]["balance"] == 9_850

    # A period that does not contain the push shows nothing.
    empty = client.get("/api/admin/sales/insights?currency=CFA&period=month")
    assert empty.get_json()["summary"]["batchCount"] == 0
    assert empty.get_json()["summary"]["revenue"] == 0


def test_sheet_reference_tabs_are_created_when_missing(monkeypatch):
    """ensure_reference_tab reuses an existing tab and creates a missing one."""
    calls = {"meta": 0, "batch": 0, "values": 0}

    def fake_request(method, url, body=None):
        if method == "GET":
            calls["meta"] += 1
            return {"sheets": [{"properties": {"title": " ngn "}},
                               {"properties": {"title": "Dashboard"}}]}
        if ":batchUpdate" in url:
            calls["batch"] += 1
            assert body["requests"][0]["addSheet"]["properties"]["title"] == "FCFA"
            return {"spreadsheetId": "sb"}
        calls["values"] += 1
        assert "FCFA" in url or "fcfa" in url
        return {}

    monkeypatch.setattr(google_sheets, "_google_request", fake_request)
    assert google_sheets.ensure_reference_tab("sb", "NGN") == "ngn"
    assert google_sheets.ensure_reference_tab("sb", "CFA") == "FCFA"
    assert calls == {"meta": 2, "batch": 1, "values": 1}


def test_push_orders_upsert_by_order_id_never_duplicates(monkeypatch):
    """The reference-tab writer updates an existing row instead of appending."""
    seen = {}

    def fake_request(method, url, body=None):
        seen.setdefault("calls", []).append((method, url))
        if method == "GET" and "/values:batchGet" in url:
            return {"valueRanges": [{"values": [
                google_sheets._headers("NGN"),
                ["2026-10-01", "JA-DUP-1", "Old Customer", "1× Old", "NGN",
                 1, "", "", "", "", ""],
            ]}]}
        return {}

    monkeypatch.setattr(google_sheets, "_google_request", fake_request)
    order = {"id": "JA-DUP-1", "status": "confirmed", "total": 4_000,
             "currency": "NGN", "at": "2026-10-05T10:00:00Z",
             "payload": json.dumps({
                 "customer": {"name": "New Customer"},
                 "items": [{"name": "New item", "qty": 1}],
                 "accounting": accounting_mod.new_snapshot(
                     4_000, "NGN", 0.44, "2026-10-05T10:00:00Z")})}
    report = google_sheets.push_orders([order], batch_name="Batch 9",
                                       reference_id="sb-ref")
    assert report["NGN"]["updated"] == 1
    assert report["NGN"]["appended"] == 0
    put_calls = [c for c in seen["calls"] if c[0] == "PUT" and "/values/" in c[1]
                 and ":append" not in c[1]]
    assert put_calls, "an existing order id must be updated in place"
