"""Supplier costs, discounts, opening balance, batch transport + broadcast filters.

Covers the "complete system refinement" contract end to end:

  * supplier price / supplier link may stay blank (an order still stages);
  * Unit Supplier Price x Quantity = Total Supplier Cost, recalculated the
    moment a field is saved;
  * applied discounts are deducted, and stay hidden at zero;
  * FCFA orders convert the NGN supplier cost at the ledger exchange rate;
  * Net Profit = Selling Price - Discounts - Supplier Cost - Transport, and
    each batch's net profit accumulates on the Starting Profit / opening
    balance;
  * a single Batch Transportation Fee wins over the per-order transport fees,
    and a blank box falls back to them;
  * marketing/broadcast screens never offer a soft-deleted or archived row;
  * GOOGLE_SHEET_ID / the Google Drive setup guide are wired for Render.

Run with:  python3 -m pytest tests/test_accounting_refinement.py -q
"""
import json
import os
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
import app as appmod  # noqa: E402
import catalog  # noqa: E402
import google_sheets  # noqa: E402
import supabase_store  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tests"))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"
ORDER_IDS = ("REF-NGN-1", "REF-NGN-2", "REF-CFA-1")
KEYS = (supabase_store.ACCOUNTING_BATCHES_KEY,
        supabase_store.ACCOUNTING_EXPENSES_KEY,
        supabase_store.ACCOUNTING_SETTINGS_KEY)
JOBS = ("REF-JOB-1", "REF-JOB-2")


@pytest.fixture()
def client(monkeypatch):
    init_db()
    for oid in ORDER_IDS:
        execute("DELETE FROM orders WHERE id=?", (oid,))
    for key in KEYS:
        execute("DELETE FROM growth_settings WHERE key=?", (key,))
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


def add_order(order_id, currency, total, *, qty=3, customer="Refinement Buyer",
              unit_name="Silk scarf", at="2026-10-04T09:00:00Z", rate=0.5):
    payload = {
        "customer": {"name": customer},
        "items": [{"id": "ref-item", "name": unit_name, "qty": qty}],
        "accounting": accounting_mod.new_snapshot(total, currency, rate, at),
    }
    execute(
        "INSERT INTO orders (id,payload,total,currency,status,customer_name,"
        "items_count,at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (order_id, json.dumps(payload), total, currency, "confirmed", customer,
         qty, at, at))


def entries_of(client, **params):
    suffix = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
    data = client.get("/api/admin/accounting" + suffix).get_json()
    return {row["id"]: row for row in data["entries"]}, data


def patch_order(client, csrf, oid, body):
    return client.patch(f"/api/admin/accounting/orders/{oid}", json=body,
                        headers={"X-CSRF-Token": csrf})


def allow_all_pushes(monkeypatch):
    calls = []

    def fake(orders, *, batch_name="", reference_id=""):
        calls.append({"ids": sorted(str(o.get("id")) for o in orders),
                      "batch": batch_name, "reference": reference_id})
        grouped = {}
        for order in orders:
            currency = google_sheets._currency(
                accounting_mod.account_block(order).get("currency")
                or order.get("currency"))
            grouped[currency] = {"spreadsheetId": reference_id or "sb",
                                 "tab": google_sheets.REFERENCE_TABS[currency],
                                 "url": "https://sheets.example/" + currency,
                                 "count": 1, "appended": 1, "updated": 0}
        return grouped

    monkeypatch.setattr(google_sheets, "push_orders", fake)
    return calls


# ------------------------------------------------- supplier price x quantity
def test_supplier_price_times_quantity_and_blank_start_state():
    snapshot = accounting_mod.new_snapshot(50_000, "NGN", 0.5, "2026-10-01T10:00:00Z")
    order = {"id": "X", "payload": {"items": [{"qty": 4}]}}
    # Blank to begin with: no supplier price and no link is a valid state.
    entry = accounting_mod.entry_from_order(
        {"id": "X", "status": "confirmed", "total": 50_000, "currency": "NGN",
         "payload": json.dumps({"accounting": snapshot,
                                "items": [{"name": "Scarf", "qty": 4}]})})
    assert entry["supplierCostNgn"] == 0
    assert entry["supplierLink"] == ""
    assert entry["supplierQty"] == 4          # follows the order's own quantity
    assert entry["netProfit"] == 50_000

    # Unit price x quantity = total, with the quantity defaulting to the order.
    saved, error = accounting_mod.apply_supplier_fields(
        snapshot, order, {"supplierUnitPriceNgn": 2_500})
    assert error == ""
    assert saved["supplierCostNgn"] == 10_000
    assert saved["supplierUnitPriceNgn"] == 2_500
    assert saved["supplierQty"] == 4

    # An explicit quantity re-multiplies the stored unit price.
    resized, _ = accounting_mod.apply_supplier_fields(saved, order, {"supplierQty": 6})
    assert resized["supplierCostNgn"] == 15_000

    # A directly typed total derives the unit price so both stay consistent.
    typed, _ = accounting_mod.apply_supplier_fields(resized, order,
                                                    {"supplierCostNgn": 9_000})
    assert typed["supplierCostNgn"] == 9_000
    assert typed["supplierUnitPriceNgn"] == 1_500

    # Blank clears a previously entered figure; garbage is still refused.
    cleared, error = accounting_mod.apply_supplier_fields(
        typed, order, {"supplierUnitPriceNgn": "", "supplierLink": ""})
    assert error == "" and cleared["supplierCostNgn"] == 0
    _bad, error = accounting_mod.apply_supplier_fields(
        typed, order, {"supplierUnitPriceNgn": "abc"})
    assert "non-negative" in error
    _neg, error = accounting_mod.apply_supplier_fields(
        typed, order, {"supplierUnitPriceNgn": -50})
    assert "non-negative" in error


def test_discount_and_net_profit_formula_with_currency_conversion():
    snapshot = accounting_mod.new_snapshot(45_000, "CFA", 0.5, "2026-10-02T10:00:00Z")
    order = {"id": "Y", "payload": {"items": [{"qty": 3}]}}
    priced, error = accounting_mod.apply_supplier_fields(
        snapshot, order,
        {"supplierUnitPriceNgn": 10_000, "supplierQty": 3, "supplierLink": "https://sup.example/x"})
    assert error == ""
    assert priced["supplierCostNgn"] == 30_000
    # FCFA order: 30,000 NGN x 0.5 = 15,000 FCFA of supplier cost.
    figures = accounting_mod.entry_figures(priced, "CFA", 0.5)
    assert figures["supplierCostInCurrency"] == 15_000
    assert figures["netProfit"] == 30_000            # 45,000 - 0 - 15,000

    # 10% discount off the selling price, deducted from the net profit.
    discounted, error = accounting_mod.apply_supplier_fields(
        priced, order, {"discountPercent": 10})
    assert error == ""
    assert discounted["discount"] == 4_500
    figures = accounting_mod.entry_figures(discounted, "CFA", 0.5)
    assert figures["netProfit"] == 45_000 - 4_500 - 15_000
    assert figures["netCashProfit"] == figures["netProfit"]   # transport still 0

    # An absolute discount reports the percentage it equals, and 0 clears it.
    absolute, _ = accounting_mod.apply_supplier_fields(
        priced, order, {"discount": 9_000})
    assert absolute["discountPercent"] == 20.0
    zeroed, _ = accounting_mod.apply_supplier_fields(absolute, order, {"discount": 0})
    assert zeroed["discount"] == 0 and zeroed["discountPercent"] == 0.0


# ------------------------------------------------------ API auto-save routes
def test_patch_autosaves_supplier_fields_discounts_and_recalculates(client):
    csrf = csrf_of(client)
    add_order(ORDER_IDS[0], "NGN", 50_000, qty=3)

    # Blank supplier price + link stages happily and nets the whole sale.
    staged, data = entries_of(client)
    assert staged[ORDER_IDS[0]]["supplierCostNgn"] == 0
    assert staged[ORDER_IDS[0]]["supplierLink"] == ""
    assert staged[ORDER_IDS[0]]["netProfit"] == 50_000

    # Typing a unit price auto-saves and instantly recalculates the net profit.
    first = patch_order(client, csrf, ORDER_IDS[0],
                        {"supplierUnitPriceNgn": 2_000, "supplierQty": 3})
    assert first.status_code == 200, first.get_json()
    entry = first.get_json()["entry"]
    assert entry["supplierCostNgn"] == 6_000
    assert entry["supplierQty"] == 3
    assert entry["netProfit"] == 44_000

    # The supplier link saves on its own, blank or filled.
    linked = patch_order(client, csrf, ORDER_IDS[0],
                         {"supplierLink": "https://supplier.example/order/9"})
    assert linked.status_code == 200
    assert linked.get_json()["entry"]["supplierLink"] == "https://supplier.example/order/9"
    assert linked.get_json()["entry"]["supplierCostNgn"] == 6_000

    # A discount is deducted from the net profit and reported with its percent.
    discounted = patch_order(client, csrf, ORDER_IDS[0], {"discountPercent": 10})
    entry = discounted.get_json()["entry"]
    assert entry["discount"] == 5_000
    assert entry["discountPercent"] == 10.0
    assert entry["netProfit"] == 50_000 - 5_000 - 6_000

    # Per-order transport is part of the same live formula.
    shipped = patch_order(client, csrf, ORDER_IDS[0],
                          {"deliveryExpense": 1_200, "notes": "Batch 41"})
    entry = shipped.get_json()["entry"]
    assert entry["netProfit"] == 39_000
    assert entry["netCashProfit"] == 37_800

    # Invalid values are refused (never silently zeroed).
    assert patch_order(client, csrf, ORDER_IDS[0],
                       {"supplierUnitPriceNgn": -1}).status_code == 400
    assert patch_order(client, csrf, ORDER_IDS[0],
                       {"discountPercent": 200}).status_code == 400

    # Blanking the supplier price is allowed and keeps the link + discount.
    cleared = patch_order(client, csrf, ORDER_IDS[0], {"supplierUnitPriceNgn": ""})
    entry = cleared.get_json()["entry"]
    assert entry["supplierCostNgn"] == 0
    assert entry["supplierLink"].endswith("/order/9")
    assert entry["discount"] == 5_000
    assert entry["netProfit"] == 50_000 - 5_000


def test_cfa_supplier_cost_converts_with_the_active_rate(client, monkeypatch):
    csrf = csrf_of(client)
    add_order(ORDER_IDS[2], "CFA", 30_000, qty=2, rate=0.44)
    monkeypatch.setattr(accounting_mod, "current_exchange_rate", lambda: 0.75)

    # The desk reprices the CFA row at the ACTIVE Store Settings rate when the
    # owner prices the item (applyActiveRate), so the conversion is current.
    priced = patch_order(client, csrf, ORDER_IDS[2], {
        "supplierUnitPriceNgn": 4_000, "supplierQty": 2, "applyActiveRate": True,
        "exchangeRate": 0.75})
    assert priced.status_code == 200, priced.get_json()
    entry = priced.get_json()["entry"]
    assert entry["supplierCostNgn"] == 8_000
    assert entry["exchangeRate"] == 0.75
    assert entry["supplierCostCfa"] == 6_000          # 8,000 x 0.75
    assert entry["supplierCostInCurrency"] == 6_000
    assert entry["netProfit"] == 24_000

    # Untouched history keeps the rate locked in at confirmation.
    add_order(ORDER_IDS[0], "NGN", 12_000, qty=1, rate=0.44)
    entries, _data = entries_of(client)
    assert entries[ORDER_IDS[0]]["exchangeRate"] == 0.44


# ----------------------------------------------------------- opening balance
def test_starting_profit_input_accumulates_every_new_batch(client, monkeypatch):
    csrf = csrf_of(client)
    # The input box at the top of the desk (aliases accepted).
    opened = client.put("/api/admin/accounting/settings",
                        headers={"X-CSRF-Token": csrf},
                        json={"startingProfitNgn": 250_000,
                              "openingBalanceCfa": 40_000})
    assert opened.status_code == 200, opened.get_json()
    body = opened.get_json()
    assert body["settings"]["startingBalanceNgn"] == 250_000
    assert body["settings"]["startingProfitNgn"] == 250_000
    assert body["settings"]["startingBalanceCfa"] == 40_000
    assert body["balances"]["NGN"]["startingProfit"] == 250_000
    assert body["balances"]["NGN"]["balance"] == 250_000

    add_order(ORDER_IDS[0], "NGN", 20_000, qty=2)
    assert patch_order(client, csrf, ORDER_IDS[0],
                       {"supplierUnitPriceNgn": 3_000, "supplierQty": 2}).status_code == 200
    allow_all_pushes(monkeypatch)
    pushed = client.post("/api/admin/accounting/push",
                         headers={"X-CSRF-Token": csrf},
                         json={"orderIds": [ORDER_IDS[0]], "name": "Week 41"})
    assert pushed.status_code == 200, pushed.data
    balances = pushed.get_json()["balances"]["NGN"]
    # Net profit 20,000 - 6,000 = 14,000 accumulates ON TOP of the opening figure.
    assert balances["salesNetProfit"] == 14_000
    assert balances["balance"] == 264_000

    # An invalid opening figure is refused, and the saved one is untouched.
    assert client.put("/api/admin/accounting/settings",
                      headers={"X-CSRF-Token": csrf},
                      json={"startingProfitNgn": -1}).status_code == 400
    assert client.get("/api/admin/accounting").get_json()[
        "settings"]["startingBalanceNgn"] == 250_000


# ------------------------------------------- batch vs per-order transport
def test_batch_transportation_fee_wins_and_blank_falls_back(client, monkeypatch):
    csrf = csrf_of(client)
    add_order(ORDER_IDS[0], "NGN", 20_000, qty=2)
    add_order(ORDER_IDS[1], "NGN", 10_000, qty=1, customer="Second Buyer")
    for oid, fee in ((ORDER_IDS[0], 500), (ORDER_IDS[1], 300)):
        assert patch_order(client, csrf, oid, {"deliveryExpense": fee}).status_code == 200

    # ONE Batch Transportation Fee covers the whole batch: the individual
    # per-order fees are ignored for the batch total.
    batched = client.post("/api/admin/accounting/batches",
                          headers={"X-CSRF-Token": csrf},
                          json={"orderIds": [ORDER_IDS[0], ORDER_IDS[1]],
                                "currency": "NGN", "name": "Batch with fee",
                                "batchTransportFee": 1_800})
    assert batched.status_code == 200, batched.get_json()
    batch = batched.get_json()["batch"]
    assert batch["batchTransportFee"] == 1_800
    assert batch["transportSource"] == "batch"
    assert batch["totals"]["transportExpense"] == 1_800
    assert batch["totals"]["netCashProfit"] == 30_000 - 1_800
    balances = client.get("/api/admin/accounting").get_json()["balances"]["NGN"]
    assert balances["salesNetProfit"] == 28_200

    # Correcting the fee later recalculates the batch and the running balance.
    edited = client.put(f"/api/admin/accounting/batches/{batch['id']}",
                        headers={"X-CSRF-Token": csrf},
                        json={"batchTransportFee": 2_500})
    assert edited.status_code == 200, edited.get_json()
    assert edited.get_json()["batch"]["totals"]["transportExpense"] == 2_500
    assert edited.get_json()["balances"]["NGN"]["salesNetProfit"] == 27_500

    # Blank/None clears it -> the per-order fees (500 + 300) come back.
    cleared = client.put(f"/api/admin/accounting/batches/{batch['id']}",
                         headers={"X-CSRF-Token": csrf},
                         json={"batchTransportFee": None})
    assert cleared.status_code == 200, cleared.get_json()
    cleared_batch = cleared.get_json()["batch"]
    assert "batchTransportFee" not in cleared_batch
    assert cleared_batch["transportSource"] == "per-order"
    assert cleared_batch["totals"]["transportExpense"] == 800
    assert cleared.get_json()["balances"]["NGN"]["salesNetProfit"] == 29_200


def test_push_logs_the_batch_fee_once(client, monkeypatch):
    csrf = csrf_of(client)
    add_order(ORDER_IDS[0], "NGN", 20_000, qty=2)
    add_order(ORDER_IDS[1], "NGN", 10_000, qty=1, customer="Second Buyer")
    calls = allow_all_pushes(monkeypatch)
    pushed = client.post("/api/admin/accounting/push",
                         headers={"X-CSRF-Token": csrf},
                         json={"orderIds": [ORDER_IDS[0], ORDER_IDS[1]],
                               "name": "Push with batched transport",
                               "batchTransportFee": 2_000})
    assert pushed.status_code == 200, pushed.data
    assert calls and calls[0]["ids"] == sorted([ORDER_IDS[0], ORDER_IDS[1]])
    batch = pushed.get_json()["batches"][0]
    assert batch["batchTransportFee"] == 2_000
    assert batch["transportSource"] == "batch"
    assert batch["totals"]["transportExpense"] == 2_000
    assert batch["totals"]["netCashProfit"] == 30_000 - 2_000

    # A blank box (absent / null) records the per-order fallback instead.
    add_order("REF-JOB-3" if False else ORDER_IDS[2], "CFA", 9_000, qty=1)
    blank = client.post("/api/admin/accounting/push",
                        headers={"X-CSRF-Token": csrf},
                        json={"orderIds": [ORDER_IDS[2]], "batchTransportFee": None})
    assert blank.status_code == 200, blank.data
    assert blank.get_json()["batches"][0]["transportSource"] == "per-order"
    assert "batchTransportFee" not in blank.get_json()["batches"][0]


def test_a_batch_fee_is_allocated_across_the_sheet_rows(client, monkeypatch):
    """The Sheet rows carry the fee's shares (so the sheet's own net-profit
    column agrees with the batch), while the batch keeps the single figure."""
    csrf = csrf_of(client)
    add_order(ORDER_IDS[0], "NGN", 20_000, qty=2)
    add_order(ORDER_IDS[1], "NGN", 10_000, qty=1, customer="Second Buyer")
    seen = {}

    def fake(orders, *, batch_name="", reference_id=""):
        seen["delivery"] = [accounting_mod.account_block(o).get("deliveryExpense")
                            for o in orders]
        return {"NGN": {"spreadsheetId": "sb", "tab": "NGN",
                        "url": "https://sheets.example/NGN", "count": len(orders),
                        "appended": len(orders), "updated": 0}}

    monkeypatch.setattr(google_sheets, "push_orders", fake)
    pushed = client.post("/api/admin/accounting/push",
                         headers={"X-CSRF-Token": csrf},
                         json={"orderIds": [ORDER_IDS[0], ORDER_IDS[1]],
                               "batchTransportFee": 1_800})
    assert pushed.status_code == 200, pushed.data
    assert sum(seen["delivery"]) == 1_800
    assert seen["delivery"][0] == 900 and seen["delivery"][1] == 900
    batch = pushed.get_json()["batches"][0]
    # The batch document keeps the ONE figure, not the allocated shares.
    assert batch["batchTransportFee"] == 1_800
    assert batch["totals"]["transportExpense"] == 1_800
    assert [row["deliveryExpense"] for row in batch["orders"]] == [0, 0]

    # An odd fee leaves the remainder on the first row and still adds up.
    shares = accounting_mod.allocate_transport(
        [{"id": "a"}, {"id": "b"}, {"id": "c"}], 1_000)
    assert shares == {"a": 334, "b": 333, "c": 333}
    assert sum(shares.values()) == 1_000
    assert accounting_mod.allocate_transport([], 500) == {}
    assert accounting_mod.allocate_transport([{"id": "a"}], 0) == {"a": 0}


def test_discounts_flow_into_batch_totals_and_sales_insights(client, monkeypatch):
    csrf = csrf_of(client)
    add_order(ORDER_IDS[0], "NGN", 20_000, qty=2)
    assert patch_order(client, csrf, ORDER_IDS[0], {
        "supplierUnitPriceNgn": 3_000, "supplierQty": 2, "discountPercent": 25,
        "deliveryExpense": 500}).status_code == 200
    allow_all_pushes(monkeypatch)
    pushed = client.post("/api/admin/accounting/push",
                         headers={"X-CSRF-Token": csrf},
                         json={"orderIds": [ORDER_IDS[0]], "name": "Discount push"})
    assert pushed.status_code == 200, pushed.data
    batch = pushed.get_json()["batches"][0]
    assert batch["totals"]["discountsTotal"] == 5_000
    # 20,000 - 5,000 discount - 6,000 supplier - 500 transport.
    assert batch["totals"]["netCashProfit"] == 8_500

    insights = client.get("/api/admin/sales/insights?currency=NGN&period=month").get_json()
    assert insights["summary"]["discounts"] == 5_000
    assert insights["summary"]["netProfit"] == 8_500
    card = insights["batches"][0]
    assert card["discounts"] == 5_000
    assert card["transportSource"] == "per-order"
    assert card["netProfit"] == 8_500


# ------------------------------------------------- broadcast / marketing filters
def test_deleted_and_archived_products_are_never_live_or_broadcastable():
    assert catalog.is_deleted_product({"is_deleted": True})
    assert catalog.is_deleted_product({"isDeleted": True})
    assert catalog.is_deleted_product({"archived": True})
    assert catalog.is_deleted_product({"is_archived": True})
    assert catalog.is_deleted_product({"deletedAt": "2026-10-01T00:00:00Z"})
    assert catalog.is_deleted_product({"active": False})
    assert catalog.is_deleted_product({"enabled": False})
    assert catalog.is_deleted_product({"source": "deleted"})
    assert catalog.is_deleted_product({"source": "replaced"})
    assert catalog.is_deleted_product({"status": "archived"})
    assert catalog.is_deleted_product({"status": "deleted"})
    assert catalog.is_deleted_product(None)          # no row at all is not live
    # An unmarked row is not "deleted" - but without an id it is still not
    # sellable, which is what the broadcast screens key off.
    assert not catalog.is_deleted_product({})
    assert not catalog.is_live_product({})

    assert not catalog.is_deleted_product({"id": "p1", "online": True})
    # Hidden-but-not-deleted is not "deleted"...
    assert not catalog.is_deleted_product({"id": "p1", "online": False})
    # ...but it is not broadcastable either: a hidden piece cannot be sold.
    assert not catalog.is_live_product({"id": "p1", "online": False})
    assert not catalog.is_live_product({"id": "p1", "is_deleted": True})
    assert not catalog.is_live_product({"id": "", "online": True})
    assert catalog.is_live_product({"id": "p1", "online": True})


def test_merged_catalogue_drops_soft_deleted_rows(monkeypatch):
    products = [
        {"id": "keep-1", "name": "Live piece", "online": True, "priceNgn": 1000},
        {"id": "gone-1", "name": "Deleted piece", "online": True, "is_deleted": True},
        {"id": "gone-2", "name": "Archived piece", "online": True, "archived": True},
        {"id": "gone-3", "name": "Inactive piece", "online": True, "active": False},
        {"id": "hidden-1", "name": "Hidden piece", "online": False},
    ]
    monkeypatch.setattr(catalog, "_supabase_products", lambda: products)
    monkeypatch.setattr(catalog, "overrides", lambda: {})
    monkeypatch.setattr(catalog, "_seed_products", lambda: [])
    monkeypatch.setattr(catalog, "resolve_images", lambda rows: rows)
    monkeypatch.setattr(catalog, "_fixture_guard_active", lambda: False)
    visible = {p["id"] for p in catalog.merged()}
    admin_view = {p["id"] for p in catalog.merged(include_hidden=True)}
    assert visible == {"keep-1"}
    # Even the admin catalogue (marketing pickers read ?all=1) loses them.
    assert admin_view == {"keep-1", "hidden-1"}


def test_broadcast_products_resolution_refuses_retired_rows(client, monkeypatch):
    csrf = csrf_of(client)
    monkeypatch.setattr(catalog, "merged", lambda include_hidden=False: [
        {"id": "live-1", "name": "Live", "online": True, "priceNgn": 1000},
        {"id": "dead-1", "name": "Deleted", "online": True, "is_deleted": True},
    ])
    ok = client.post("/api/admin/marketing/broadcast/preview",
                     headers={"X-CSRF-Token": csrf},
                     json={"subject": "New in", "content": "Hello",
                           "productIds": ["live-1"]})
    assert ok.status_code == 200, ok.get_json()
    refused = client.post("/api/admin/marketing/broadcast/preview",
                          headers={"X-CSRF-Token": csrf},
                          json={"subject": "New in", "content": "Hello",
                                "productIds": ["dead-1"]})
    assert refused.status_code == 400
    assert "unavailable" in refused.get_json()["error"].lower()


# ------------------------------------------------------------ Google wiring
def test_google_sheet_id_env_feeds_the_reference_workbook(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    assert (google_sheets.reference_spreadsheet_id()
            == google_sheets.DEFAULT_REFERENCE_SPREADSHEET_ID)
    monkeypatch.setenv("GOOGLE_SHEET_ID", "env-sheet-123")
    assert google_sheets.reference_spreadsheet_id() == "env-sheet-123"
    # A value saved on the accounting desk wins over the environment.
    assert google_sheets.reference_spreadsheet_id(
        {"referenceSpreadsheetId": "desk-sheet-9"}) == "desk-sheet-9"
    # A full URL is accepted and reduced to the id.
    assert google_sheets.reference_spreadsheet_id(
        {"referenceSpreadsheetId": "https://docs.google.com/spreadsheets/d/abc_123/edit"}
    ) == "abc_123"
    # A cleared desk setting falls back to the environment, never to nowhere.
    assert google_sheets.reference_spreadsheet_id(
        {"referenceSpreadsheetId": ""}) == "env-sheet-123"


def test_accounting_page_exposes_the_effective_reference_sheet(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEET_ID", "env-sheet-123")
    csrf_of(client)
    page = client.get("/api/admin/accounting").get_json()
    assert page["settings"]["referenceSpreadsheetId"] == "env-sheet-123"
    assert page["googleSheetId"] == "env-sheet-123"


def test_google_verify_reports_every_step_without_writing(client, monkeypatch):
    csrf_of(client)
    report = {"ok": True, "configured": True, "connected": True,
              "email": "owner@example.com",
              "reference": {"id": "ref", "title": "ITEMFLOW", "tabs": ["NGN", "FCFA"],
                            "readable": True},
              "ledgers": {"NGN": {"id": "a", "readable": True},
                          "CFA": {"id": "b", "readable": True}},
              "steps": [{"step": "oauth-client", "ok": True, "detail": "set"}],
              "message": "Google Drive sync is working."}
    monkeypatch.setattr(google_sheets, "verify_sync",
                        lambda reference_id="", settings=None: report)
    response = client.get("/api/admin/accounting/google/verify")
    assert response.status_code == 200, response.data
    body = response.get_json()
    assert body["verification"]["ok"] is True
    assert body["verification"]["steps"][0]["step"] == "oauth-client"
    # Admin-only, like every other accounting read.
    assert client.get("/api/admin/accounting").status_code == 200


def test_google_verify_is_honest_when_nothing_is_configured(monkeypatch):
    for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    report = google_sheets.verify_sync("some-sheet")
    assert report["ok"] is False
    assert report["configured"] is False
    assert "GOOGLE_CLIENT_ID" in report["steps"][0]["detail"]
    assert "GOOGLE_CLIENT_ID" in report["message"]


def test_render_blueprint_and_guide_carry_the_reference_sheet_id():
    guide = open(os.path.join(ROOT, "GOOGLE_DRIVE_SYNC_SETUP.md"), encoding="utf-8").read()
    blueprint = open(os.path.join(ROOT, "render.yaml"), encoding="utf-8").read()
    env_example = open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()
    sheet_id = "1GnBgXl-VNoRzV-jiz4qCeb_BKzs31_Fu"
    for blob in (guide, blueprint, env_example):
        assert "GOOGLE_SHEET_ID" in blob
        assert sheet_id in blob
    assert "GOOGLE_CLIENT_ID" in guide and "GOOGLE_CLIENT_SECRET" in guide
    assert "GOOGLE_REDIRECT_URI" in guide
    assert "api/admin/accounting/google/callback" in guide
    assert "Test Google sync" in guide


# ----------------------------------------------------- accounting desk (JS)
def _read(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


ACCOUNTING_JS = _read("js/accounting.js")


def test_the_desk_has_the_opening_balance_input_at_the_top():
    assert "STARTING PROFIT / OPENING BALANCE" in ACCOUNTING_JS
    assert "function openingSection()" in ACCOUNTING_JS
    assert "startingBalanceNgn" in ACCOUNTING_JS and "startingBalanceCfa" in ACCOUNTING_JS
    # Mounted above the balance strip, so it is the first thing on the desk.
    body = ACCOUNTING_JS[ACCOUNTING_JS.index("${openingSection()}"):]
    assert body.index("${openingSection()}") < body.index("${balanceStrip()}")
    assert "/api/admin/accounting/settings" in ACCOUNTING_JS


def test_the_desk_autosaves_supplier_fields_and_hides_a_zero_discount():
    assert 'data-stage-edit="supplierLink"' in ACCOUNTING_JS
    assert 'data-stage-edit="supplierUnitPriceNgn"' in ACCOUNTING_JS
    assert 'data-stage-edit="supplierQty"' in ACCOUNTING_JS
    assert "function scheduleEntrySave(" in ACCOUNTING_JS
    assert "function rowFigures(" in ACCOUNTING_JS
    assert "productIsDeleted" not in ACCOUNTING_JS  # marketing lives in admin.js
    # The discount cell renders the input only when a discount exists.
    assert 'const discountCell = discount > 0' in ACCOUNTING_JS
    # FCFA rows reprice at the active Store Settings rate on edit.
    assert "applyActiveRate" in ACCOUNTING_JS


def test_the_desk_has_both_transport_inputs_and_keeps_the_per_order_ones():
    assert 'data-batch-transport' in ACCOUNTING_JS
    assert "Batch transportation fee" in ACCOUNTING_JS
    assert 'data-stage-edit="deliveryExpense"' in ACCOUNTING_JS
    assert "blank = per-order fees" in ACCOUNTING_JS
    assert "batchTransportFee" in ACCOUNTING_JS
    assert 'data-action="verify-google"' in ACCOUNTING_JS


# ------------------------------- accounting desk recalc (runs the real code)
ACCOUNTING_VM_SCRIPT = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";
const src = readFileSync("js/accounting.js", "utf8");
const grab = (name) => {
  for (const prefix of ["async function ", "function "]) {
    const start = src.indexOf(prefix + name + "(");
    if (start < 0) continue;
    let depth = 0, i = src.indexOf("{", start);
    for (; i < src.length; i++) {
      if (src[i] === "{") depth++;
      else if (src[i] === "}") { depth--; if (!depth) break; }
    }
    return src.slice(start, i + 1);
  }
  throw new Error(name + " not found");
};
const sandbox = { Math, Number, String, Object, Array, console };
sandbox.__entries = [];
sandbox.state = {
  currency: "NGN", currentExchangeRate: 0.75, entries: [], batchTransportFee: "",
  money: (n) => n,
};
vm.createContext(sandbox);
const pieces = [];
for (const fn of ["entrySupplierTotal", "entryFigures", "rowFigures",
                  "entryQuantity", "batchTransportFee", "stagedEntries",
                  "stageTotals"]) {
  pieces.push(grab(fn));
}
vm.runInContext(pieces.join("\n"), sandbox);
sandbox.state = {
  currency: "NGN", currentExchangeRate: 0.75,
  batchTransportFee: "",
  entries: [
    { id: "n1", currency: "NGN", saleAmount: 50000, discount: 5000,
      supplierCostNgn: 6000, supplierCostCfa: 3000, supplierUnitPriceNgn: 2000,
      supplierQty: 3, itemQuantity: 3, deliveryExpense: 1200, exchangeRate: 0.75 },
    { id: "c1", currency: "CFA", saleAmount: 30000, discount: 0,
      supplierCostNgn: 8000, supplierCostCfa: 6000, supplierUnitPriceNgn: 4000,
      supplierQty: 2, itemQuantity: 2, deliveryExpense: 0, exchangeRate: 0.75 },
  ],
};
const run = (code) => vm.runInContext(code, sandbox);
const assert = (cond, msg) => { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } };

// The entry figures match the server formula exactly.
const ngn = run("entryFigures(state.entries[0])");
assert(ngn.netProfit === 50000 - 5000 - 6000, "net profit = sale - discount - supplier");
assert(ngn.netCashProfit === ngn.netProfit - 1200, "cash profit also removes transport");
const cfa = run("entryFigures(state.entries[1])");
assert(cfa.supplier === 6000, "CFA entries show their converted FCFA cost");
assert(run("entryQuantity(state.entries[1])") === 2, "quantity follows the order");

// Live edit: typing a unit price and quantity recalculates before the save.
const fakeRow = (values) => ({ querySelector: (sel) => {
  const name = (sel.match(/data-stage-edit="([^"]+)"/) || [])[1];
  if (!(name in values)) return null;
  return { value: String(values[name]) };
} });
sandbox.__row = fakeRow({ supplierUnitPriceNgn: "2500", supplierQty: "4",
                          saleAmount: "50000", discount: "0", deliveryExpense: "800" });
const live = run("rowFigures(__row, state.entries[0])");
assert(live.totalNgn === 10000, "unit price x quantity = total supplier cost");
assert(live.netProfit === 50000 - 10000, "live net profit recalculates");
assert(live.netCashProfit === 40000 - 800, "live cash profit removes transport");
sandbox.__row = fakeRow({ supplierUnitPriceNgn: "4000", supplierQty: "2",
                          saleAmount: "30000", discount: "3000", deliveryExpense: "0" });
const liveCfa = run("rowFigures(__row, state.entries[1])");
assert(liveCfa.supplier === 6000, "FCFA conversion uses the ledger rate (8000 x 0.75)");
assert(liveCfa.netProfit === 30000 - 3000 - 6000, "discounts are deducted live");

// Batch Transportation Fee: filled wins; blank falls back to per-order fees.
run('state.currency = "NGN"');
assert(run("stageTotals().transport") === 1200, "blank batch fee -> per-order transport");
assert(run('stageTotals().transportSource') === "per-order", "blank means per-order mode");
run('state.batchTransportFee = "2000"');
assert(run("stageTotals().transport") === 2000, "batch fee wins over per-order fees");
assert(run('stageTotals().transportSource') === "batch", "batch mode is reported");
assert(run("stageTotals().supplier") === 6000, "supplier total stays currency-scoped");
console.log("ALL ACCOUNTING RECALC CHECKS PASSED");
"""


def test_the_accounting_desk_recalculates_net_profit_in_the_browser():
    """Executes the real js/accounting.js calculator in a Node VM with a fake
    DOM: unit price x quantity, discount deduction, FCFA conversion at the
    ledger rate, and the batch-vs-per-order transport fallback."""
    import subprocess
    import textwrap

    result = subprocess.run(
        ["node", "--input-type=module", "-e", textwrap.dedent(ACCOUNTING_VM_SCRIPT)],
        cwd=ROOT, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, (
        "accounting VM run failed:\n" + result.stdout + "\n" + result.stderr)
    assert "ALL ACCOUNTING RECALC CHECKS PASSED" in result.stdout


def test_the_accounting_desk_renders_and_autosaves_in_a_real_dom():
    """Runs tests/_accounting_desk_dom_check.mjs (jsdom) against recorded API
    answers: the opening-balance box, the supplier autosave, the hidden $0
    discount, the batch transport fallback and the Google verify call.

    jsdom is a developer dependency (`npm install jsdom`, or ``JA_JSDOM_DIR``
    pointing at an install), so a laptop without it skips - exactly like the
    browser smoke tests skip without chromium. CI sets ``JA_REQUIRE_BROWSER=1``
    and installs jsdom, so there a missing install is a FAILURE: a green CI run
    must never mean "the desk was not exercised".
    """
    import subprocess

    script = os.path.join(ROOT, "tests", "_accounting_desk_dom_check.mjs")
    result = subprocess.run(["node", script], cwd=ROOT, text=True,
                            capture_output=True, timeout=180)
    if result.returncode == 3:
        message = ("jsdom is not installed (npm install jsdom, or set "
                   "JA_JSDOM_DIR); the accounting desk DOM check did not run")
        if os.environ.get("JA_REQUIRE_BROWSER") == "1":
            pytest.fail(message + " - CI installs it, so this is a broken build")
        pytest.skip(message)
    assert result.returncode == 0, (
        "accounting desk DOM check failed:\n" + result.stdout + "\n" + result.stderr)
    assert "ACCOUNTING DESK DOM CHECKS PASSED" in result.stdout
