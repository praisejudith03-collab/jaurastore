"""v126: stock cap, confirmed-only sales, BOM exports, thumbs, phone nav."""
import json
import os
import re
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
from config import Config  # noqa: E402
from db import execute, init_db  # noqa: E402

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


def _seed_name(pid):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "data", "seed.json")) as fh:
        seed = json.load(fh)
    products = seed if isinstance(seed, list) else seed.get("products", [])
    for p in products:
        if p.get("id") == pid:
            return p.get("name") or pid
    return pid


def _order_payload(order_id, pid, name, qty, price, total):
    return {
        "id": order_id,
        "currency": "NGN",
        "total": total,
        "customer": {"name": "Test Buyer", "email": "buyer@test.com",
                     "zone": "Lagos Mainland"},
        "items": [{"id": pid, "name": name, "qty": qty, "price": price}],
    }


def test_over_order_rejected_with_counts(client):
    pid = "wix-001"
    name = _seed_name(pid)
    payload = _order_payload("JA-OVER1", pid, name, 25, 1000, 25000)
    r = client.post("/api/orders", json=payload,
                    headers={"X-CSRF-Token": csrf(client)})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert body.get("code") == "out_of_stock"
    err = body.get("error", "")
    assert name in err
    assert "24" in err and "25" in err
    assert body.get("items")


def test_stock_bypass_when_enforce_off(client, monkeypatch):
    monkeypatch.setattr(Config, "ENFORCE_STOCK", False)
    pid = "wix-001"
    name = _seed_name(pid)
    payload = _order_payload("JA-OVER2", pid, name, 25, 1000, 25000)
    r = client.post("/api/orders", json=payload,
                    headers={"X-CSRF-Token": csrf(client)})
    assert r.status_code == 200, r.get_json()


def test_sales_confirmed_only_and_csv(client):
    execute("DELETE FROM orders")
    pid = "wix-001"
    name = _seed_name(pid)
    for oid, qty, price, total in (("JA-SPEND1", 1, 5000, 5000),
                                  ("JA-SCONF1", 2, 4000, 8000)):
        payload = _order_payload(oid, pid, name, qty, price, total)
        r = client.post("/api/orders", json=payload,
                        headers={"X-CSRF-Token": csrf(client)})
        assert r.status_code == 200, r.get_json()

    tok = login(client)
    r = client.patch("/api/admin/orders/JA-SCONF1",
                     json={"status": "confirmed"},
                     headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.get_json()

    report = client.get("/api/admin/sales?days=all").get_json()
    assert report["orders"] == 1
    assert report["pendingCount"] == 1
    by_cur = {row["currency"]: row["value"]
              for row in report["revenueByCurrency"]}
    assert by_cur.get("NGN") == 8000
    assert report["revenue"] == 8000

    from analytics import sales_csv

    first_line = sales_csv(days="all").splitlines()[0]
    assert first_line.startswith("Order,Date (UTC),Customer")

    r = client.get("/api/admin/sales.csv?days=all")
    assert r.status_code == 200
    assert r.data[:3] == b"\xef\xbb\xbf"
    assert r.data.decode("utf-8-sig").splitlines()[0].startswith(
        "Order,Date (UTC),Customer")

    r = client.get("/api/admin/orders.csv")
    assert r.status_code == 200
    assert r.data[:3] == b"\xef\xbb\xbf"


def test_listing_thumbs_exist_and_smaller():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prods = os.path.join(root, "images", "products")
    checked = 0
    for fname in sorted(os.listdir(prods)):
        if fname.endswith(".400w.webp"):
            continue
        base, ext = os.path.splitext(fname)
        if ext.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        orig = os.path.join(prods, fname)
        thumb = os.path.join(prods, base + ".400w.webp")
        assert os.path.exists(thumb), f"missing thumb for {fname}"
        assert os.path.getsize(thumb) < os.path.getsize(orig) * 0.9, fname
        checked += 1
    assert checked > 100


def test_phone_nav_tabs_and_padding():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "js", "admin.js")) as fh:
        js = fh.read()
    nav_at = js.find("admin-app-nav")
    assert nav_at != -1
    nav_block = js[nav_at:nav_at + 4000]
    for tab in ("analytics", "products", "orders", "sales", "marketing",
                "categories", "settings", "account"):
        assert f'data-tab="{tab}"' in nav_block, tab

    with open(os.path.join(root, "css", "style.css")) as fh:
        css = fh.read()
    found = False
    for match in re.finditer(
        r"@media\s*\(\s*max-width\s*:\s*(\d+)px\s*\)\s*\{(.*?)\n\}",
        css, re.DOTALL,
    ):
        if int(match.group(1)) > 920:
            continue
        for pad in re.finditer(r"padding-bottom\s*:\s*(\d+)px",
                               match.group(2)):
            if int(pad.group(1)) >= 130:
                found = True
    assert found, "no >=130px bottom padding inside <=920px media query"
