"""Dual-currency accounting snapshots, ledger isolation and archive flows."""
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
import supabase_store  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tests"))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"
ORDER_IDS = ("ACCT-NGN-001", "ACCT-CFA-001")


@pytest.fixture()
def client():
    init_db()
    for oid in ORDER_IDS:
        execute("DELETE FROM orders WHERE id=?", (oid,))
    execute("DELETE FROM growth_settings WHERE key=?",
            (supabase_store.ACCOUNTING_BATCHES_KEY,))
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client
    for oid in ORDER_IDS:
        execute("DELETE FROM orders WHERE id=?", (oid,))
    execute("DELETE FROM growth_settings WHERE key=?",
            (supabase_store.ACCOUNTING_BATCHES_KEY,))


def login(client):
    response = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["csrf"]


def add_pending_order(order_id, currency, total, customer):
    payload = {
        "customer": {"name": customer},
        "items": [{"id": "accounting-test-item", "name": "Test serum", "qty": 2}],
    }
    execute(
        "INSERT INTO orders (id,payload,total,currency,status,customer_name,items_count,at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (order_id, json.dumps(payload), total, currency, "pending", customer, 2,
         "2026-10-01T10:00:00Z", "2026-10-01T10:00:00Z"),
    )


def confirm(client, csrf, order_id):
    response = client.patch(
        f"/api/admin/orders/{order_id}", json={"status": "confirmed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.get_json()
    return response


def get_entries(client, *, include_deleted=False, include_archived=False):
    params = []
    if include_deleted:
        params.append("includeDeleted=true")
    if include_archived:
        params.append("includeArchived=true")
    suffix = ("?" + "&".join(params)) if params else ""
    response = client.get(f"/api/admin/accounting{suffix}")
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_accounting_math_keeps_currency_ledgers_and_locked_rates_isolated():
    ngn = accounting_mod.new_snapshot(15_000, "NGN", 0.73, "2026-10-01T10:00:00Z")
    cfa = accounting_mod.new_snapshot(9_800, "CFA", 0.5, "2026-10-01T10:00:00Z")
    ngn["supplierCostNgn"] = 7_000
    ngn["deliveryExpense"] = 600
    cfa["supplierCostNgn"] = 10_000
    cfa["deliveryExpense"] = 300
    ngn_entry = accounting_mod.entry_from_order({
        "id": "NGN-1", "status": "confirmed", "currency": "NGN", "total": 15_000,
        "payload": json.dumps({"accounting": ngn}),
    })
    cfa_entry = accounting_mod.entry_from_order({
        "id": "CFA-1", "status": "confirmed", "currency": "CFA", "total": 9_800,
        "payload": json.dumps({"accounting": cfa}),
    })

    assert ngn_entry["supplierCostInCurrency"] == 7_000
    assert ngn_entry["supplierCostCfa"] == 5_110
    assert ngn_entry["netProfit"] == 8_000
    assert ngn_entry["netCashProfit"] == 7_400
    assert cfa_entry["supplierCostCfa"] == cfa_entry["supplierCostInCurrency"] == 5_000
    assert cfa_entry["netProfit"] == 4_800
    assert cfa_entry["netCashProfit"] == 4_500
    assert accounting_mod.batch_totals([ngn_entry, cfa_entry], "CFA") == {
        "currency": "CFA", "orderCount": 1, "customerRevenue": 9_800,
        "supplierPayableNgn": 10_000, "supplierCostInCurrency": 5_000,
        "transportExpense": 300, "netCashProfit": 4_500,
    }


def test_confirmation_edit_soft_delete_restore_and_delivery_archive(client, monkeypatch):
    add_pending_order(ORDER_IDS[0], "NGN", 15_000, "Naira Buyer")
    add_pending_order(ORDER_IDS[1], "CFA", 9_800, "CFA Buyer")
    csrf = login(client)

    monkeypatch.setattr(accounting_mod, "current_exchange_rate", lambda: 0.5)
    confirm(client, csrf, ORDER_IDS[1])
    # A rate update before another confirmation belongs only to that later order.
    monkeypatch.setattr(accounting_mod, "current_exchange_rate", lambda: 0.73)
    confirm(client, csrf, ORDER_IDS[0])
    # Reconfirming an already confirmed order must never rewrite its snapshot.
    monkeypatch.setattr(accounting_mod, "current_exchange_rate", lambda: 0.9)
    confirm(client, csrf, ORDER_IDS[1])

    snapshot = json.loads(one("SELECT payload FROM orders WHERE id=?", (ORDER_IDS[1],))["payload"])
    assert snapshot["accounting"]["exchangeRate"] == 0.5
    assert snapshot["accounting"]["currency"] == "CFA"
    assert snapshot["accounting"]["saleAmount"] == 9_800

    # Each mutation requires the authenticated Admin CSRF token.
    no_csrf = client.patch(
        f"/api/admin/accounting/orders/{ORDER_IDS[1]}", json={"supplierCostNgn": 10_000})
    assert no_csrf.status_code == 403

    cfa_edit = client.patch(
        f"/api/admin/accounting/orders/{ORDER_IDS[1]}",
        json={"supplierCostNgn": 10_000, "deliveryExpense": 300, "notes": "Batch A"},
        headers={"X-CSRF-Token": csrf},
    )
    assert cfa_edit.status_code == 200, cfa_edit.get_json()
    assert cfa_edit.get_json()["entry"]["supplierCostCfa"] == 5_000
    assert cfa_edit.get_json()["entry"]["netProfit"] == 4_800
    assert cfa_edit.get_json()["entry"]["netCashProfit"] == 4_500

    ngn_edit = client.patch(
        f"/api/admin/accounting/orders/{ORDER_IDS[0]}",
        json={"supplierCostNgn": 7_000, "deliveryExpense": 600},
        headers={"X-CSRF-Token": csrf},
    )
    assert ngn_edit.status_code == 200, ngn_edit.get_json()
    ngn_entry = ngn_edit.get_json()["entry"]
    assert ngn_entry["supplierCostInCurrency"] == 7_000
    assert ngn_entry["netProfit"] == 8_000
    assert ngn_entry["netCashProfit"] == 7_400

    bad_edit = client.patch(
        f"/api/admin/accounting/orders/{ORDER_IDS[0]}", json={"supplierCostNgn": -1},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad_edit.status_code == 400

    entries = {entry["id"]: entry for entry in get_entries(client)["entries"]}
    assert set(entries) == set(ORDER_IDS)
    assert entries[ORDER_IDS[1]]["currency"] == "CFA"
    assert entries[ORDER_IDS[0]]["currency"] == "NGN"
    assert entries[ORDER_IDS[1]]["exchangeRate"] == 0.5
    assert entries[ORDER_IDS[0]]["exchangeRate"] == 0.73

    mixed_batch = client.post(
        "/api/admin/accounting/batches",
        json={"orderIds": list(ORDER_IDS)}, headers={"X-CSRF-Token": csrf},
    )
    assert mixed_batch.status_code == 409

    archived = client.post(
        "/api/admin/accounting/batches",
        json={"orderIds": [ORDER_IDS[1]], "currency": "CFA", "name": "October delivery"},
        headers={"X-CSRF-Token": csrf},
    )
    assert archived.status_code == 200, archived.get_json()
    batch = archived.get_json()["batch"]
    assert batch["totals"] == {
        "currency": "CFA", "orderCount": 1, "customerRevenue": 9_800,
        "supplierPayableNgn": 10_000, "supplierCostInCurrency": 5_000,
        "transportExpense": 300, "netCashProfit": 4_500,
    }
    assert batch["orders"][0]["exchangeRate"] == 0.5

    archive_get = get_entries(client)
    # Pushed/archived orders are CLEARED off the active staging queue; the
    # audit view brings them back explicitly.
    assert ORDER_IDS[1] not in {row["id"] for row in archive_get["entries"]}
    audit_view = get_entries(client, include_archived=True)
    assert next(row for row in audit_view["entries"] if row["id"] == ORDER_IDS[1])["archived"] is True
    assert audit_view["batches"][0]["id"] == batch["id"]
    locked_edit = client.patch(
        f"/api/admin/accounting/orders/{ORDER_IDS[1]}", json={"saleAmount": 10_000},
        headers={"X-CSRF-Token": csrf},
    )
    assert locked_edit.status_code == 409
    locked_delete = client.delete(
        f"/api/admin/accounting/orders/{ORDER_IDS[1]}",
        headers={"X-CSRF-Token": csrf},
    )
    assert locked_delete.status_code == 409
    already_archived = client.post(
        "/api/admin/accounting/batches",
        json={"orderIds": [ORDER_IDS[1]], "currency": "CFA"},
        headers={"X-CSRF-Token": csrf},
    )
    assert already_archived.status_code == 409

    removed = client.delete(
        f"/api/admin/accounting/orders/{ORDER_IDS[0]}",
        headers={"X-CSRF-Token": csrf},
    )
    assert removed.status_code == 200
    assert ORDER_IDS[0] not in {row["id"] for row in get_entries(client)["entries"]}
    deleted_row = next(row for row in get_entries(client, include_deleted=True)["entries"]
                       if row["id"] == ORDER_IDS[0])
    assert deleted_row["deleted"] is True

    restored = client.post(
        f"/api/admin/accounting/orders/{ORDER_IDS[0]}/restore", json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert restored.status_code == 200, restored.get_json()
    assert restored.get_json()["entry"]["deleted"] is False


def test_accounting_page_is_standalone_and_admin_only_data_is_not_cached(client):
    # The HTML shell is public, but ledger data always remains authenticated.
    page = client.get("/admin/accounting")
    assert page.status_code == 200
    assert b"/js/accounting.js" in page.data
    assert client.get("/api/admin/accounting").status_code == 401


def test_supabase_accounting_reader_paginates_all_confirmed_orders(monkeypatch):
    rows = [{"id": f"ROW-{index:04d}", "status": "confirmed", "at": str(index)}
            for index in range(1_205)]
    calls = []

    class Builder:
        def __init__(self):
            self.filters = {}
            self.start = 0
            self.end = None

        def select(self, _columns):
            return self

        def eq(self, key, value):
            self.filters[key] = value
            return self

        def order(self, *_args, **_kwargs):
            return self

        def range(self, start, end):
            self.start, self.end = start, end
            return self

        def execute(self):
            calls.append((self.start, self.end, dict(self.filters)))
            filtered = [row for row in rows if all(row.get(k) == v for k, v in self.filters.items())]
            return SimpleNamespace(data=filtered[self.start:self.end + 1])

    class FakeClient:
        def table(self, _name):
            return Builder()

    monkeypatch.setattr(supabase_store, "client", lambda: FakeClient())
    result = supabase_store.load_confirmed_orders_for_accounting(page_size=400)
    assert len(result) == len(rows)
    assert [call[:2] for call in calls] == [(0, 399), (400, 799), (800, 1199), (1200, 1599)]
    assert all(call[2] == {"status": "confirmed"} for call in calls)


def test_supabase_accounting_reader_reports_failure_without_partial_ledger(monkeypatch):
    class BrokenBuilder:
        def select(self, *_args): return self
        def eq(self, *_args): return self
        def order(self, *_args, **_kwargs): return self
        def range(self, *_args): return self
        def execute(self): raise ConnectionError("temporary Supabase outage")

    class FakeClient:
        def table(self, _name): return BrokenBuilder()

    monkeypatch.setattr(supabase_store, "client", lambda: FakeClient())
    assert supabase_store.load_confirmed_orders_for_accounting(page_size=2) is None


def test_admin_accounting_summary_uses_sheet_totals_or_confirmed_order_fallback(client, monkeypatch):
    import datetime as dt
    import google_sheets

    add_pending_order(ORDER_IDS[0], "NGN", 15_000, "Naira Buyer")
    csrf = login(client)
    # Avoid any real asynchronous Google call during the offline test.
    monkeypatch.setattr(google_sheets, "sync_order_async", lambda _order: None)
    confirm(client, csrf, ORDER_IDS[0])
    monkeypatch.setattr(google_sheets, "integration_status", lambda: {
        "configured": False, "connected": False, "email": "", "ledgers": {},
        "syncingExistingOrders": False,
    })

    fallback = client.get("/api/admin/accounting/summary?currency=NGN&period=month")
    assert fallback.status_code == 200, fallback.get_json()
    assert fallback.get_json()["summary"]["revenue"] == 15_000
    assert fallback.get_json()["summary"]["netProfit"] == 15_000
    assert fallback.get_json()["summary"]["orderCount"] == 1

    today = dt.datetime.now(dt.timezone.utc).date()
    sheet_row = [today.isoformat(), "JA-SHEET-01", "Customer", "Item", "NGN",
                 80_000, 12_000, 3_000, "", "Batch A", ""]
    monkeypatch.setattr(google_sheets, "integration_status", lambda: {
        "configured": True, "connected": True, "email": "owner@example.com",
        "ledgers": {"NGN": {"id": "ngn", "url": "https://docs.google.com/spreadsheets/d/ngn/edit"},
                    "CFA": {"id": "cfa", "url": "https://docs.google.com/spreadsheets/d/cfa/edit"}},
        "syncingExistingOrders": False,
    })
    monkeypatch.setattr(google_sheets, "read_ledger", lambda _currency: {
        "currency": "NGN", "orders": [google_sheets._headers("NGN"), sheet_row],
        "expenses": [["Date", "Batch", "Type", "Description", "Amount · NGN", "Notes"],
                     [today.isoformat(), "Batch A", "Supplier Cost", "Unlinked", 5_000, ""]],
    })
    connected = client.get("/api/admin/accounting/summary?currency=NGN&period=year&batch=Batch%20A")
    assert connected.status_code == 200, connected.get_json()
    summary = connected.get_json()["summary"]
    assert summary["revenue"] == 80_000
    assert summary["supplierCosts"] == 17_000
    assert summary["transport"] == 3_000
    assert summary["netProfit"] == 60_000
    assert summary["batch"] == "Batch A"


def test_google_connect_is_admin_gated_and_callback_state_bound(client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://shop.example/api/admin/accounting/google/callback")
    assert client.get("/api/admin/accounting/google/connect").status_code == 401

    login(client)
    start = client.get("/api/admin/accounting/google/connect")
    assert start.status_code == 302
    location = urlsplit(start.headers["Location"])
    params = parse_qs(location.query)
    assert location.netloc == "accounts.google.com"
    assert params["response_type"] == ["code"]
    assert params["state"]
    # Full Drive access: the push must be able to write to the owner's
    # pre-existing ITEMFLOW reference workbook, not only app-created files.
    assert "https://www.googleapis.com/auth/drive" in params["scope"][0]
    assert "test-client-secret" not in start.headers["Location"]

    callback = client.get(
        "/api/admin/accounting/google/callback?state=attacker-controlled&code=not-used")
    assert callback.status_code == 302
    assert callback.headers["Location"].endswith("/admin/accounting?google=state-error")
