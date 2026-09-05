"""Confirming an order decrements catalog stock; decline/reopen/delete restore it.

Uses dedicated jau-stock-* product ids so seed wix-* rows stay untouched.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    authmod.ensure_seed_admins()
    authmod.set_password(EMAIL, PW)
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def csrf(client):
    return client.get("/api/config").get_json()["csrf"]


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def _stock_of(pid):
    for p in catalog_mod.merged(include_hidden=True):
        if str(p.get("id")) == pid:
            return p
    return None


def _make_stock_product(pid, stock=10, option_stock=None, options=None):
    rec = {
        "id": pid,
        "sku": pid.upper().replace("-", ""),
        "slug": pid,
        "name": "Stock Test " + pid,
        "category": "beauty",
        "priceNgn": 8000,
        "stock": stock,
        "online": True,
    }
    if options:
        rec["options"] = options
    if option_stock:
        rec["optionStock"] = option_stock
    catalog_mod.upsert(rec, "tester")
    return rec


def _place(client, oid, items):
    execute("DELETE FROM rate_limits WHERE action='order'")
    total = sum(int(i.get("price") or 8000) * int(i.get("qty") or 1) for i in items)
    body = {
        "id": oid, "currency": "NGN", "total": total,
        "customer": {"name": "Stock Tester", "email": "stock@example.com",
                     "phone": "+2348012345678", "city": "Lagos",
                     "zone": "Lagos Mainland", "address": "1 Test St"},
        "items": items,
    }
    r = client.post("/api/orders", json=body, headers={"X-CSRF-Token": csrf(client)})
    assert r.status_code == 200, r.get_json()
    return r


def _confirm(client, oid, tok=None):
    tok = tok or login(client)
    r = client.patch("/api/admin/orders/" + oid, json={"status": "confirmed"},
                     headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.get_json()
    return r


def test_confirm_decrements_product_stock(client):
    pid = "jau-stock-dec"
    _make_stock_product(pid, stock=10)
    _place(client, "JA-STK01", [{"id": pid, "name": "Oil", "qty": 3, "price": 8000}])
    _confirm(client, "JA-STK01")
    assert _stock_of(pid)["stock"] == 7


def test_confirm_is_idempotent_does_not_double_decrement(client):
    pid = "jau-stock-idem"
    _make_stock_product(pid, stock=10)
    _place(client, "JA-STK02", [{"id": pid, "name": "Oil", "qty": 2, "price": 8000}])
    tok = login(client)
    _confirm(client, "JA-STK02", tok)
    _confirm(client, "JA-STK02", tok)
    assert _stock_of(pid)["stock"] == 8


def test_email_confirm_decrements_stock(client):
    import security
    pid = "jau-stock-eml"
    _make_stock_product(pid, stock=9)
    _place(client, "JA-STK03", [{"id": pid, "name": "Oil", "qty": 1, "price": 8000}])
    token = security.order_token("JA-STK03", "confirm")
    r = client.get("/api/orders/JA-STK03/confirm?action=confirm&token=" + token)
    assert r.status_code == 200, r.get_json()
    assert _stock_of(pid)["stock"] == 8


def test_email_confirm_already_does_not_double_decrement(client):
    import security
    pid = "jau-stock-em2"
    _make_stock_product(pid, stock=9)
    _place(client, "JA-STK04", [{"id": pid, "name": "Oil", "qty": 1, "price": 8000}])
    token = security.order_token("JA-STK04", "confirm")
    client.get("/api/orders/JA-STK04/confirm?action=confirm&token=" + token)
    r = client.get("/api/orders/JA-STK04/confirm?action=confirm&token=" + token)
    assert r.get_json().get("already") is True
    assert _stock_of(pid)["stock"] == 8


def test_decline_of_pending_does_not_change_stock(client):
    pid = "jau-stock-dnp"
    _make_stock_product(pid, stock=10)
    _place(client, "JA-STK05", [{"id": pid, "name": "Oil", "qty": 4, "price": 8000}])
    tok = login(client)
    r = client.patch("/api/admin/orders/JA-STK05", json={"status": "declined"},
                     headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert _stock_of(pid)["stock"] == 10


def test_decline_of_confirmed_restores_stock(client):
    pid = "jau-stock-dcf"
    _make_stock_product(pid, stock=10)
    _place(client, "JA-STK06", [{"id": pid, "name": "Oil", "qty": 3, "price": 8000}])
    tok = login(client)
    _confirm(client, "JA-STK06", tok)
    assert _stock_of(pid)["stock"] == 7
    r = client.patch("/api/admin/orders/JA-STK06", json={"status": "declined"},
                     headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert _stock_of(pid)["stock"] == 10


def test_reopen_of_confirmed_restores_stock(client):
    pid = "jau-stock-rop"
    _make_stock_product(pid, stock=10)
    _place(client, "JA-STK07", [{"id": pid, "name": "Oil", "qty": 2, "price": 8000}])
    tok = login(client)
    _confirm(client, "JA-STK07", tok)
    r = client.patch("/api/admin/orders/JA-STK07", json={"status": "pending"},
                     headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert _stock_of(pid)["stock"] == 10


def test_delete_of_confirmed_restores_stock(client):
    pid = "jau-stock-del"
    _make_stock_product(pid, stock=10)
    _place(client, "JA-STK08", [{"id": pid, "name": "Oil", "qty": 4, "price": 8000}])
    tok = login(client)
    _confirm(client, "JA-STK08", tok)
    assert _stock_of(pid)["stock"] == 6
    r = client.delete("/api/admin/orders/JA-STK08", headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.get_json()
    assert _stock_of(pid)["stock"] == 10


def test_delete_of_pending_does_not_change_stock(client):
    pid = "jau-stock-dpn"
    _make_stock_product(pid, stock=10)
    _place(client, "JA-STK09", [{"id": pid, "name": "Oil", "qty": 4, "price": 8000}])
    tok = login(client)
    r = client.delete("/api/admin/orders/JA-STK09", headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert _stock_of(pid)["stock"] == 10


def test_option_stock_decrements_on_confirm(client):
    pid = "jau-stock-opt"
    _make_stock_product(
        pid, stock=15,
        options=[{"title": "Colour", "type": "COLOR", "values": ["Red", "Blue"]}],
        option_stock={"Red": 10, "Blue": 5},
    )
    _place(client, "JA-STK10", [{"id": pid, "name": "Oil", "qty": 2, "price": 8000,
                                 "color": "Colour: Red"}])
    _confirm(client, "JA-STK10")
    p = _stock_of(pid)
    assert p["stock"] == 13
    assert p["optionStock"]["Red"] == 8
    assert p["optionStock"]["Blue"] == 5


def test_option_stock_restores_on_decline(client):
    pid = "jau-stock-opr"
    _make_stock_product(
        pid, stock=15,
        options=[{"title": "Colour", "type": "COLOR", "values": ["Red", "Blue"]}],
        option_stock={"Red": 10, "Blue": 5},
    )
    _place(client, "JA-STK11", [{"id": pid, "name": "Oil", "qty": 2, "price": 8000,
                                 "color": "Red"}])
    tok = login(client)
    _confirm(client, "JA-STK11", tok)
    client.patch("/api/admin/orders/JA-STK11", json={"status": "declined"},
                 headers={"X-CSRF-Token": tok})
    p = _stock_of(pid)
    assert p["stock"] == 15
    assert p["optionStock"]["Red"] == 10
    assert p["optionStock"]["Blue"] == 5


def test_confirm_two_items_decrements_both(client):
    a, b = "jau-stock-a", "jau-stock-b"
    _make_stock_product(a, stock=8)
    _make_stock_product(b, stock=6)
    _place(client, "JA-STK12", [
        {"id": a, "name": "Oil A", "qty": 2, "price": 8000},
        {"id": b, "name": "Oil B", "qty": 3, "price": 8000},
    ])
    _confirm(client, "JA-STK12")
    assert _stock_of(a)["stock"] == 6
    assert _stock_of(b)["stock"] == 3


def test_stock_applied_stored_on_order_payload(client):
    pid = "jau-stock-pay"
    _make_stock_product(pid, stock=10)
    _place(client, "JA-STK13", [{"id": pid, "name": "Oil", "qty": 2, "price": 8000}])
    _confirm(client, "JA-STK13")
    row = one("SELECT payload FROM orders WHERE id=?", ("JA-STK13",))
    payload = json.loads(row["payload"])
    applied = payload.get("stockApplied")
    assert isinstance(applied, list) and applied
    assert applied[0]["id"] == pid
    assert applied[0]["qty"] == 2
