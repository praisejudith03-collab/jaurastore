"""Walk-in / offline sales: the counter sale Praise records by hand.

A walk-in sale is the ONLY order type with no email and no phone: the customer
name is the only identity field. It must deduct stock, land in the Sales
dashboard with revenue + net profit, and push a row to the Sheets ledger.

Every order this file creates is deleted again, keyed by the id the API
returns. The old version of this test left its confirmed order in the shared
/tmp database, where the Accounting summary picked it up and failed with
`18600 == 15000` whenever this module happened to run first.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

from _pw import PW  # noqa: E402

import app  # noqa: E402
import catalog  # noqa: E402
import db  # noqa: E402
import google_sheets  # noqa: E402
from config import Config  # noqa: E402

PRODUCT = {"id": "walkin-item", "name": "Test Item", "online": True, "stock": 8,
           "stock_quantity": 8, "priceNgn": 2000, "priceCfa": 1000,
           "supplierCost": 500, "supplierSku": "https://supplier.test/item"}


def _forget(order_ids):
    for oid in order_ids:
        if oid:
            db.execute("DELETE FROM orders WHERE id=?", (oid,))


def _signed_in_client(monkeypatch):
    monkeypatch.setattr(Config, "SUPABASE_ENABLED", False)
    monkeypatch.setattr(catalog, "merged", lambda **kw: [dict(PRODUCT)])
    reserved = []
    monkeypatch.setattr(catalog, "reserve_stock",
                        lambda pid, qty, **kw: reserved.append((pid, qty)) or True)
    monkeypatch.setattr(catalog, "release_stock", lambda *a, **k: True)
    application = app.create_app()
    application.config.update(TESTING=True)
    client = application.test_client()
    token = client.get("/api/admin/session").get_json()["csrf"]
    login = client.post("/api/admin/login",
                        json={"email": "jaurastore@gmail.com", "password": PW},
                        headers={"X-CSRF-Token": token})
    assert login.status_code == 200, login.get_json()
    return client, login.get_json()["csrf"], reserved


def test_walkin_sale_records_confirmed_order_and_attempts_ledger(monkeypatch):
    pushed = []
    monkeypatch.setattr(google_sheets, "push_orders",
                        lambda orders: pushed.extend(orders) or {"NGN": {"count": 1}})
    client, csrf, reserved = _signed_in_client(monkeypatch)
    order_ids = []
    try:
        with client:
            response = client.post("/api/admin/walk-in-sales", json={
                "customerName": "Walk In", "productId": "walkin-item",
                "quantity": 2, "salePrice": 1800, "currency": "NGN",
                "paymentMethod": "cash"}, headers={"X-CSRF-Token": csrf})
            assert response.status_code == 200, response.get_json()
            result = response.get_json()
            order_ids.append(result.get("orderId"))
            assert result["ok"] and result["sheets"]["ok"]
            assert reserved == [("walkin-item", 2)]
            assert len(pushed) == 1 and pushed[0]["status"] == "confirmed"
            assert pushed[0]["total"] == 3600
            assert pushed[0]["source"] == "walk-in"
            # Only a name identified the buyer - never an email or a phone.
            assert pushed[0]["customer_name"] == "Walk In"
            assert pushed[0]["email"] == "" and pushed[0]["phone"] == ""
            # The modal shows the profit of the sale that was just recorded.
            assert result["revenue"] == 3600
            assert result["netProfit"] == (1800 - 500) * 2
            assert result["costMissing"] is False
            assert result["currency"] == "NGN"
    finally:
        _forget(order_ids)


def test_walkin_sale_reports_a_red_toast_but_keeps_the_sale_when_the_ledger_fails(monkeypatch):
    def boom(_orders):
        raise RuntimeError("token expired")

    monkeypatch.setattr(google_sheets, "push_orders", boom)
    client, csrf, _reserved = _signed_in_client(monkeypatch)
    order_ids = []
    try:
        with client:
            response = client.post("/api/admin/walk-in-sales", json={
                "customerName": "Mama Ngozi", "productId": "walkin-item",
                "quantity": 1, "salePrice": 2000, "currency": "CFA",
                "paymentMethod": "pos"}, headers={"X-CSRF-Token": csrf})
            # 207: the sale is recorded, the ledger push is not.
            assert response.status_code == 207, response.get_json()
            result = response.get_json()
            order_ids.append(result.get("orderId"))
            assert result["ok"] is True and result["sheets"]["ok"] is False
            assert "Google Sheets" in result["message"]
            assert "token expired" in result["message"]
            assert result["revenue"] == 2000 and result["currency"] == "CFA"
    finally:
        _forget(order_ids)


def test_walkin_sale_rejects_a_missing_name_and_an_empty_basket(monkeypatch):
    monkeypatch.setattr(google_sheets, "push_orders", lambda _orders: {})
    client, csrf, reserved = _signed_in_client(monkeypatch)
    try:
        with client:
            for body, why in (
                ({"customerName": "", "productId": "walkin-item", "quantity": 1,
                  "salePrice": 2000, "currency": "NGN", "paymentMethod": "cash"},
                 "a customer name"),
                ({"customerName": "No Qty", "productId": "walkin-item",
                  "quantity": 0, "salePrice": 2000, "currency": "NGN",
                  "paymentMethod": "cash"}, "a quantity"),
                ({"customerName": "No Price", "productId": "walkin-item",
                  "quantity": 1, "salePrice": 0, "currency": "NGN",
                  "paymentMethod": "cash"}, "a price"),
                ({"customerName": "Bitcoin", "productId": "walkin-item",
                  "quantity": 1, "salePrice": 2000, "currency": "NGN",
                  "paymentMethod": "bitcoin"}, "a payment method"),
            ):
                response = client.post("/api/admin/walk-in-sales", json=body,
                                       headers={"X-CSRF-Token": csrf})
                assert response.status_code == 400, (why, response.get_json())
        # Nothing was reserved for a rejected sale.
        assert reserved == []
    finally:
        with client:
            pass
