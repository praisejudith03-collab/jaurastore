import os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,ROOT)
os.environ.setdefault("FLASK_ENV","testing");os.environ.setdefault("DB_PATH","/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH","/tmp/jaura_test_catalog.json");os.environ.setdefault("ADMIN_EMAILS","jaurastore@gmail.com")
from _pw import PW
import app, api, catalog, google_sheets
from config import Config


def test_walkin_sale_records_confirmed_order_and_attempts_ledger(monkeypatch):
    application=app.create_app();application.config.update(TESTING=True)
    monkeypatch.setattr(Config,"SUPABASE_ENABLED",False)
    monkeypatch.setattr(catalog,"merged",lambda **kw:[{"id":"walkin-item","name":"Test Item","online":True,"stock":8,"stock_quantity":8,"priceNgn":2000,"priceCfa":1000,"supplierCost":500,"supplierSku":"https://supplier.test/item"}])
    reserved=[]
    monkeypatch.setattr(catalog,"reserve_stock",lambda pid,qty,**kw: reserved.append((pid,qty)) or True)
    monkeypatch.setattr(catalog,"release_stock",lambda *a,**k: True)
    pushed=[]
    monkeypatch.setattr(google_sheets,"push_orders",lambda orders: pushed.extend(orders) or {"NGN":{"count":1}})
    with application.test_client() as client:
        token=client.get("/api/admin/session").get_json()["csrf"]
        login=client.post("/api/admin/login",json={"email":"jaurastore@gmail.com","password":PW},headers={"X-CSRF-Token":token})
        assert login.status_code==200,login.get_json()
        token=login.get_json()["csrf"]
        response=client.post("/api/admin/walk-in-sales",json={"customerName":"Walk In","productId":"walkin-item","quantity":2,"salePrice":1800,"currency":"NGN","paymentMethod":"cash"},headers={"X-CSRF-Token":token})
        assert response.status_code==200,response.get_json()
        result=response.get_json()
        assert result["ok"] and result["sheets"]["ok"]
        assert reserved==[("walkin-item",2)]
        assert len(pushed)==1 and pushed[0]["status"]=="confirmed"
        assert pushed[0]["total"]==3600
        assert pushed[0]["source"]=="walk-in"
