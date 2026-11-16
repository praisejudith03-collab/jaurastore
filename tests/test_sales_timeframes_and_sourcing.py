"""Sales timeframe filters, the inline missing-cost fix and sourcing batches.

Owner request 2026-11-16:

  * the Sales tab switches between Today / This week / This month / This year
    for revenue, net profit, orders and units;
  * the "orders missing a cost price" warning opens those exact orders so their
    supplier cost and delivery expense can be typed in place;
  * the Accounting desk is organised around sourcing / pre-order batches
    ("Cotonou Batch 1") with itemised goods, sourcing and transport costs, a
    multi-select order checklist and automatic Total Spent vs Total Revenue vs
    Net Profit;
  * the Dashboard truncates its long product / location lists and keeps orders
    on the Orders page only.

Run with:  python3 -m pytest tests/test_sales_timeframes_and_sourcing.py -q
"""
import datetime
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import accounting as accounting_mod  # noqa: E402
import analytics as analytics_mod  # noqa: E402
import app as appmod  # noqa: E402
import supabase_store  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tests"))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"
MARKER = "TFWindow"
SALES_IDS = ("TF-TODAY", "TF-WEEK", "TF-MONTH", "TF-YEAR", "TF-LASTYEAR")
QFIX_ID = "JA-QFIX-9"
BATCH_IDS = ("JA-SRC-1", "JA-SRC-2", "JA-SRC-CFA")
DASH_SIM = os.path.join(ROOT, "tests", "_dashboard_truncation_sim.mjs")
SALES_SIM = os.path.join(ROOT, "tests", "_sales_timeframes_quickfix_sim.mjs")


def _wipe():
    for oid in SALES_IDS + (QFIX_ID,) + BATCH_IDS:
        execute("DELETE FROM orders WHERE id=?", (oid,))
    execute("DELETE FROM growth_settings WHERE key=?",
            (supabase_store.ACCOUNTING_SOURCING_KEY,))
    execute("DELETE FROM growth_settings WHERE key=?",
            (supabase_store.ACCOUNTING_BATCHES_KEY,))


@pytest.fixture()
def client():
    init_db()
    _wipe()
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client
    _wipe()


def login(client):
    response = client.post("/api/admin/login",
                           json={"email": EMAIL, "password": PW})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["csrf"]


def _line_items(items, unit_costs=None):
    costs = list(unit_costs or [])
    lines = []
    for index, item in enumerate(items):
        qty = int(item.get("qty") or 1)
        unit = costs[index] if index < len(costs) else 0
        lines.append({
            "lineId": f"line-{index + 1:02d}",
            "productId": item.get("id") or "",
            "name": item.get("name") or "Item",
            "qty": qty,
            "saleAmount": int(item.get("price") or 0) * qty,
            "supplierCostNgn": int(unit) * qty,
        })
    return lines


def insert_order(order_id, at, *, currency="NGN", total, items, unit_costs=None,
                 delivery=0, discount=0, customer=None, items_count=None):
    """Insert one confirmed order with an accounting snapshot."""
    lines = _line_items(items, unit_costs)
    payload = {
        "id": order_id,
        "currency": currency,
        "total": total,
        "customer": {"name": customer or f"{MARKER} {order_id}"},
        "items": items,
        "accounting": {
            "currency": currency,
            "exchangeRate": 0.45,
            "saleAmount": sum(line["saleAmount"] for line in lines),
            "discount": discount,
            "supplierCostNgn": sum(line["supplierCostNgn"] for line in lines),
            "supplierQty": sum(line["qty"] for line in lines),
            "deliveryExpense": delivery,
            "deliveryExpenseNgn": delivery,
            "lineItems": lines,
        },
    }
    units = sum(int(item.get("qty") or 1) for item in items)
    execute(
        "INSERT INTO orders (id,payload,total,currency,status,customer_name,"
        "items_count,at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (order_id, json.dumps(payload), total, currency, "confirmed",
         payload["customer"]["name"], items_count if items_count is not None else units,
         at, at),
    )


def _item(pid, name, qty, price):
    return {"id": pid, "name": name, "qty": qty, "price": price}


# --------------------------------------------------------------- 1. timeframes
def test_sales_timeframes_switch_revenue_profit_orders_and_units(client, monkeypatch):
    """Today / This week / This month / This year are calendar windows.

    Pinned to Wednesday 18 Nov 2026 so the boundaries are unambiguous: the week
    starts Monday 16th, the month on the 1st and the year on 1 January.
    """
    login(client)
    monkeypatch.setattr(analytics_mod, "_now",
                        lambda: datetime.datetime(2026, 11, 18, 12, 0, 0))
    insert_order("TF-TODAY", "2026-11-18T09:00:00Z", total=10000,
                 items=[_item("tf-1", f"{MARKER} Serum", 2, 5000)],
                 unit_costs=[2000], delivery=500)
    insert_order("TF-WEEK", "2026-11-16T09:00:00Z", total=20000,
                 items=[_item("tf-2", f"{MARKER} Bag", 1, 20000)],
                 unit_costs=[10000])
    insert_order("TF-MONTH", "2026-11-03T09:00:00Z", total=30000,
                 items=[_item("tf-3", f"{MARKER} Ankara", 3, 10000)],
                 unit_costs=[0])
    insert_order("TF-YEAR", "2026-03-15T09:00:00Z", total=40000,
                 items=[_item("tf-4", f"{MARKER} Parfum", 4, 10000)],
                 unit_costs=[5000])
    insert_order("TF-LASTYEAR", "2025-12-20T09:00:00Z", total=50000,
                 items=[_item("tf-5", f"{MARKER} Old stock", 5, 10000)],
                 unit_costs=[5000])

    def report(period):
        response = client.get(
            f"/api/admin/sales?period={period}&q={MARKER.lower()}")
        assert response.status_code == 200, response.get_json()
        return response.get_json()

    today = report("today")
    assert today["period"] == "today" and today["periodLabel"] == "Today"
    assert (today["from"], today["to"]) == ("2026-11-18", "2026-11-18")
    assert today["orders"] == 1 and today["units"] == 2
    assert today["revenue"] == 10000
    # 10,000 sale - 4,000 supplier cost - 500 transport.
    assert today["netProfit"] == 5500

    week = report("week")
    assert (week["from"], week["to"]) == ("2026-11-16", "2026-11-18")
    assert week["orders"] == 2 and week["units"] == 3
    assert week["revenue"] == 30000
    assert week["netProfit"] == 5500 + 10000  # 10,000 - 10,000 = 0 for the bag
    month = report("month")
    assert (month["from"], month["to"]) == ("2026-11-01", "2026-11-18")
    assert month["orders"] == 3 and month["units"] == 6
    assert month["revenue"] == 60000
    # The costless Ankara order stays revenue but is left out of profit.
    assert month["netProfit"] == 15500
    assert month["ordersMissingCost"] == 1
    year = report("year")
    assert (year["from"], year["to"]) == ("2026-01-01", "2026-11-18")
    assert year["orders"] == 4 and year["units"] == 10
    assert year["revenue"] == 100000
    assert year["netProfit"] == 15500 + 20000
    all_time = report("all")
    assert all_time["orders"] == 5 and all_time["revenue"] == 150000
    # An explicit date range still beats the timeframe switch.
    custom = client.get(
        f"/api/admin/sales?period=year&from=2026-03-01&to=2026-03-31&q={MARKER.lower()}"
    ).get_json()
    assert custom["orders"] == 1 and custom["revenue"] == 40000


def test_the_period_windows_start_on_monday_and_the_first_of_the_month():
    today = datetime.date(2026, 11, 18)          # a Wednesday
    assert analytics_mod.period_day_bounds("today", today) == \
        ("2026-11-18", "2026-11-18")
    assert analytics_mod.period_day_bounds("week", today) == \
        ("2026-11-16", "2026-11-18")
    assert analytics_mod.period_day_bounds("month", today) == \
        ("2026-11-01", "2026-11-18")
    assert analytics_mod.period_day_bounds("year", today) == \
        ("2026-01-01", "2026-11-18")
    assert analytics_mod.period_day_bounds("fortnight", today) is None


# ------------------------------------------------------ 2. inline cost fix
def test_the_missing_cost_warning_lists_the_orders_and_fixes_them_in_place(client):
    login(client)
    insert_order(QFIX_ID, "2026-11-10T10:00:00Z", total=20000,
                 items=[_item("qf-1", "Silk scarf", 2, 6000),
                        _item("qf-2", "Hair clip", 1, 8000)],
                 unit_costs=[0, 0],
                 customer=f"{MARKER} Costless Buyer")

    before = client.get(f"/api/admin/sales?days=all&q={MARKER.lower()}").get_json()
    assert before["ordersMissingCost"] == 1 and before["costComplete"] is False
    rows = before["missingCostOrders"]
    assert len(rows) == 1 and rows[0]["id"] == QFIX_ID
    assert [line["lineId"] for line in rows[0]["lineItems"]] == ["line-01", "line-02"]
    assert all(line["supplierCostNgn"] == 0 for line in rows[0]["lineItems"])

    csrf = client.get("/api/admin/session").get_json()["csrf"]
    response = client.patch(
        f"/api/admin/accounting/orders/{QFIX_ID}",
        json={"lineItems": [
            {"lineId": "line-01", "supplierCostNgn": 8000},
            {"lineId": "line-02", "supplierCostNgn": 4000},
        ], "deliveryExpense": 1000},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.get_json()

    after = client.get(f"/api/admin/sales?days=all&q={MARKER.lower()}").get_json()
    assert after["ordersMissingCost"] == 0 and after["costComplete"] is True
    assert after["missingCostOrders"] == []
    # 20,000 sale - 12,000 supplier cost - 1,000 delivery.
    ngn = next(b for b in after["profitByCurrency"] if b["currency"] == "NGN")
    assert ngn["supplierCost"] == 12000 and ngn["delivery"] == 1000
    assert ngn["netProfit"] == 7000 and after["netProfit"] == 7000

    # A typo is refused instead of being stored as zero.
    bad = client.patch(
        f"/api/admin/accounting/orders/{QFIX_ID}",
        json={"lineItems": [{"lineId": "line-01", "supplierCostNgn": "not-a-number"}]},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 400
    assert "cost" in bad.get_json()["error"].lower()


def test_costless_orders_already_pushed_are_marked_locked(client):
    """A pushed batch is immutable, so its costless order is flagged locked.

    The Sales tab then shows the figure read-only rather than offering a save
    the accounting endpoint would refuse.
    """
    login(client)
    insert_order(QFIX_ID, "2026-11-10T10:00:00Z", total=12000,
                 items=[_item("qf-lock", "Locked item", 1, 12000)],
                 unit_costs=[0], customer=f"{MARKER} Locked Buyer")
    execute(
        "INSERT INTO growth_settings (key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (supabase_store.ACCOUNTING_BATCHES_KEY,
         json.dumps([{"id": "BATCH-LOCK1", "name": "Pushed", "currency": "NGN",
                      "orderIds": [QFIX_ID], "orders": [], "totals": {}}])),
    )
    body = client.get(f"/api/admin/sales?days=all&q={MARKER.lower()}").get_json()
    assert body["ordersMissingCost"] == 1
    assert body["missingCostOrders"][0]["archived"] is True


# ------------------------------------------------------ 3. sourcing batches
def test_sourcing_batch_multi_select_and_profit_math(client):
    login(client)
    insert_order("JA-SRC-1", "2026-11-05T10:00:00Z", total=20000,
                 items=[_item("src-1", "Ankara", 2, 10000)],
                 unit_costs=[4000], customer="Src One")
    insert_order("JA-SRC-2", "2026-11-06T10:00:00Z", total=10000,
                 items=[_item("src-2", "Perfume", 1, 10000)],
                 unit_costs=[3000], discount=1000, customer="Src Two")
    insert_order("JA-SRC-CFA", "2026-11-07T10:00:00Z", currency="CFA", total=9000,
                 items=[_item("src-3", "FCFA item", 1, 9000)],
                 unit_costs=[3000], customer="Src Koffi")

    csrf = client.get("/api/admin/session").get_json()["csrf"]
    created = client.post("/api/admin/accounting/sourcing",
                          json={"name": "Cotonou Batch 1", "currency": "NGN"},
                          headers={"X-CSRF-Token": csrf})
    assert created.status_code == 200, created.get_json()
    batch = created.get_json()["sourcingBatches"][0]
    assert batch["name"] == "Cotonou Batch 1" and batch["currency"] == "NGN"
    batch_id = batch["id"]

    goods = client.post(f"/api/admin/accounting/sourcing/{batch_id}/costs",
                        json={"kind": "goods", "label": "12 Ankara wrappers",
                              "qty": 12, "amount": 15000},
                        headers={"X-CSRF-Token": csrf})
    assert goods.status_code == 200, goods.get_json()
    waybill = client.post(f"/api/admin/accounting/sourcing/{batch_id}/costs",
                          json={"kind": "transport", "label": "Cotonou waybill",
                                "qty": 1, "amount": 2000},
                          headers={"X-CSRF-Token": csrf})
    assert waybill.status_code == 200, waybill.get_json()

    assigned = client.patch(f"/api/admin/accounting/sourcing/{batch_id}",
                            json={"orderIds": ["JA-SRC-1", "JA-SRC-2"]},
                            headers={"X-CSRF-Token": csrf})
    assert assigned.status_code == 200, assigned.get_json()

    desk = client.get("/api/admin/accounting").get_json()
    stored = next(row for row in desk["sourcingBatches"] if row["id"] == batch_id)
    totals = stored["totals"]
    assert totals["orderCount"] == 2
    assert totals["totalSpent"] == 17000
    assert totals["goodsSpent"] == 15000 and totals["transportSpent"] == 2000
    # (20,000) + (10,000 - 1,000 discount) = 29,000 revenue.
    assert totals["revenue"] == 29000
    assert totals["netProfit"] == 12000
    # The desk receives each assigned order's own figures, so a pushed order
    # (no longer in the staging queue) still counts toward the trip's revenue.
    assert {row["id"] for row in stored["orders"]} == {"JA-SRC-1", "JA-SRC-2"}
    second = next(row for row in stored["orders"] if row["id"] == "JA-SRC-2")
    assert second["discount"] == 1000 and second["saleAmount"] == 10000
    summary = next(row for row in desk["sourcingSummary"] if row["currency"] == "NGN")
    assert summary["totalSpent"] == 17000 and summary["revenue"] == 29000
    assert summary["netProfit"] == 12000

    # A CFA order can never be folded into an NGN batch.
    mixed = client.patch(f"/api/admin/accounting/sourcing/{batch_id}",
                         json={"orderIds": ["JA-SRC-1", "JA-SRC-CFA"]},
                         headers={"X-CSRF-Token": csrf})
    assert mixed.status_code == 409
    assert "ledger currency" in mixed.get_json()["error"]
    # An order that is not confirmed is refused too, not silently dropped.
    unknown = client.patch(f"/api/admin/accounting/sourcing/{batch_id}",
                           json={"orderIds": ["JA-NOT-REAL"]},
                           headers={"X-CSRF-Token": csrf})
    assert unknown.status_code == 409

    # Renaming, deleting one cost line, then deleting the whole batch.
    renamed = client.patch(f"/api/admin/accounting/sourcing/{batch_id}",
                           json={"name": "Cotonou Batch 1 (Nov)"},
                           headers={"X-CSRF-Token": csrf})
    assert renamed.status_code == 200
    assert renamed.get_json()["sourcingBatches"][0]["name"] == "Cotonou Batch 1 (Nov)"
    cost_id = renamed.get_json()["sourcingBatches"][0]["costs"][-1]["id"]
    removed = client.delete(
        f"/api/admin/accounting/sourcing/{batch_id}/costs/{cost_id}",
        headers={"X-CSRF-Token": csrf})
    assert removed.status_code == 200
    refreshed = client.get("/api/admin/accounting").get_json()
    stored = next(row for row in refreshed["sourcingBatches"] if row["id"] == batch_id)
    assert stored["totals"]["totalSpent"] == 15000
    assert stored["totals"]["netProfit"] == 14000

    deleted = client.delete(f"/api/admin/accounting/sourcing/{batch_id}",
                            headers={"X-CSRF-Token": csrf})
    assert deleted.status_code == 200
    final = client.get("/api/admin/accounting").get_json()
    assert all(row["id"] != batch_id for row in final["sourcingBatches"])
    # The orders themselves are untouched by deleting their batch.
    ids = {entry["id"] for entry in final["entries"]}
    assert {"JA-SRC-1", "JA-SRC-2"} <= ids


def test_sourcing_batch_validation_and_currency_defaults(client):
    login(client)
    csrf = client.get("/api/admin/session").get_json()["csrf"]
    bad_currency = client.post("/api/admin/accounting/sourcing",
                               json={"name": "Bad", "currency": "USD"},
                               headers={"X-CSRF-Token": csrf})
    assert bad_currency.status_code == 400
    unnamed = client.post("/api/admin/accounting/sourcing",
                          json={"currency": "NGN"},
                          headers={"X-CSRF-Token": csrf})
    assert unnamed.status_code == 200
    created = unnamed.get_json()["sourcingBatches"][0]
    assert created["name"].startswith("Sourcing batch")
    bad_cost = client.post(f"/api/admin/accounting/sourcing/{created['id']}/costs",
                           json={"kind": "goods", "amount": "lots"},
                           headers={"X-CSRF-Token": csrf})
    assert bad_cost.status_code == 400
    unknown_kind = client.post(f"/api/admin/accounting/sourcing/{created['id']}/costs",
                               json={"kind": "spaceship", "amount": 100},
                               headers={"X-CSRF-Token": csrf})
    assert unknown_kind.status_code == 400
    client.delete(f"/api/admin/accounting/sourcing/{created['id']}",
                  headers={"X-CSRF-Token": csrf})


def test_sourcing_batch_math_is_pure_and_order_scoped():
    entries = [
        {"id": "A", "currency": "NGN", "saleAmount": 10000, "discount": 0,
         "supplierCostInCurrency": 3000, "deliveryExpense": 500,
         "profitOverrideActive": False},
        {"id": "B", "currency": "NGN", "saleAmount": 5000, "discount": 500,
         "supplierCostInCurrency": 0, "deliveryExpense": 0,
         "profitOverrideActive": False},
        {"id": "C", "currency": "CFA", "saleAmount": 99999, "discount": 0,
         "supplierCostInCurrency": 1, "deliveryExpense": 0,
         "profitOverrideActive": False},
    ]
    batch = accounting_mod.new_sourcing_batch(
        "Nov Pre-Orders", "NGN", order_ids=["A", "B", "C"],
        costs=[
            {"kind": "goods", "label": "Stock", "amount": 6000},
            {"kind": "sourcing", "label": "Tickets", "amount": 1000},
            {"kind": "transport", "label": "Waybill", "amount": 500},
        ])
    totals = accounting_mod.sourcing_batch_totals(batch, entries)
    assert totals["orderCount"] == 2          # the CFA order is excluded
    assert totals["totalSpent"] == 7500
    assert totals["revenue"] == 14500         # 10,000 + 4,500
    assert totals["netProfit"] == 7000
    assert totals["missingCostOrders"] == 1   # order B has no cost price


# ------------------------------------------------------- 4. dashboard cleanup
def test_the_dashboard_truncation_simulation_passes():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (dashboard sim needs Node >= 18)")
    proc = subprocess.run([node, DASH_SIM], cwd=ROOT, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "all dashboard truncation checks passed" in proc.stdout


def test_the_sales_tab_ships_timeframe_buttons_and_a_clickable_cost_warning():
    """Source pins for the shipped Sales tab wiring.

    The API is covered above; these assertions pin what the browser actually
    gets: the four calendar timeframe buttons, a warning that opens the exact
    orders, and a save that goes through the accounting order PATCH.
    """
    with open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8") as fh:
        source = fh.read()
    panel = source[source.index("function salesPanel()"):
                   source.index("async function fillSales()")]
    for value, label in (("today", "Today"), ("week", "This week"),
                         ("month", "This month"), ("year", "This year")):
        assert f'["{value}", "{label}"]' in panel, f"missing the {label} button"
    assert 'data-sales-range="${v}"' in panel, "the timeframe buttons carry their key"
    assert "sales-cost-fix" in panel, "the missing-cost editor container is missing"
    # The warning is a control, not a dead end.
    assert "sales-cost-fix-toggle" in source
    assert "Add their supplier cost here" in source
    assert "data-cost-line" in source and "data-cost-delivery" in source
    assert "missingCostOrders" in source
    # Saving an inline cost uses the same accounting PATCH as the desk, so the
    # two views can never disagree about an order's cost.
    assert "api/admin/accounting/orders/" in source
    # The Dashboard keeps orders off the page entirely.
    assert 'id="an-orders"' not in source
    assert "DASH_LIST_LIMIT = 5" in source


def test_the_sales_tab_simulation_passes():
    """The shipped Sales tab, driven in Node against canned API answers."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (Sales tab sim needs Node >= 18)")
    proc = subprocess.run([node, SALES_SIM], cwd=ROOT, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "all Sales tab checks passed" in proc.stdout
