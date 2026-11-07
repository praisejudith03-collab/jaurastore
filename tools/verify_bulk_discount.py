#!/usr/bin/env python3
"""End-to-end proof for the restored per-product bulk discount.

Walks the real write path an admin save takes and the real pricing path a
checkout takes, and prints what happened at each step:

  1. save a product with bulkQty=10 / bulkPercent=15 through the ADMIN API
  2. re-read the row the way the storefront does - are the values there?
  3. order 10 units  (NOT more than the threshold)  -> expect full price
  4. order 11 units  (more than the threshold)      -> expect 15% off
  5. save an UNRELATED field only (a rename)        -> expect the offer kept
  6. clear both boxes                               -> expect the offer gone

Run from the repository root:  python3 tools/verify_bulk_discount.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_bulk_e2e.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_bulk_e2e_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "bulk-e2e-secret")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("CATEGORIES_PATH", "/tmp/jaura_bulk_e2e_categories.json")
os.environ["SITE_CONFIG_PATH"] = "/tmp/jaura_bulk_e2e_site.json"

sys.path.insert(0, os.path.join(ROOT, "tests"))
from _pw import PW  # noqa: E402

os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

import catalog as catalog_mod  # noqa: E402
from db import execute, init_db, one  # noqa: E402
import app as appmod  # noqa: E402

EMAIL = "jaurastore@gmail.com"
PID = "jau-bulk-e2e"
PRICE = 1000
ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'PASS' if good else 'FAIL'}] {label}: got {got!r}, expected {want!r}")
    return good


def main():
    # Every override file is a per-run artefact: the catalogue falls back to
    # "<path>.bak" when the main file is missing, so clearing only the main
    # file would let a previous run's numbers leak in.
    for f in ("/tmp/jaura_bulk_e2e.db", "/tmp/jaura_bulk_e2e_catalog.json",
              "/tmp/jaura_bulk_e2e_catalog.json.bak",
              "/tmp/jaura_bulk_e2e_catalog.json.lock",
              "/tmp/jaura_bulk_e2e_site.json"):
        if os.path.exists(f):
            os.remove(f)
    init_db()

    app = appmod.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    with client:
        csrf = client.get("/api/config").get_json()["csrf"]
        r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
        assert r.status_code == 200, r.data
        tok = r.get_json()["csrf"]

        def save(**fields):
            row = next((p for p in catalog_mod.merged(include_hidden=True)
                        if p["id"] == PID), {})
            payload = {
                **row,
                "id": PID, "name": "Bulk E2E Perfume", "category": "beauty",
                "priceNgn": PRICE, "stock": 500, "online": True,
                "image": "images/products/_placeholder.jpg",
                **fields,
            }
            res = client.post("/api/admin/products", json=payload,
                              headers={"X-CSRF-Token": tok})
            assert res.status_code == 200, res.data
            return res

        def row():
            return next(p for p in catalog_mod.merged(include_hidden=True)
                        if p["id"] == PID)

        def order(oid, qty):
            execute("DELETE FROM rate_limits WHERE action='order'")
            body = {"id": oid, "currency": "NGN", "total": PRICE * qty,
                    "customer": {"name": "Bulk E2E", "email": "e2e@example.com",
                                 "phone": "+2348012345678", "city": "Lagos",
                                 "zone": "Lagos Mainland", "address": "1 Test St"},
                    "items": [{"id": PID, "name": "Bulk E2E Perfume",
                               "qty": qty, "price": PRICE}]}
            res = client.post("/api/orders", json=body,
                              headers={"X-CSRF-Token": csrf})
            assert res.status_code == 200, res.data
            return res.get_json()

        print("STEP 1  admin save writes bulkQty=10 / bulkPercent=15")
        save(bulkQty=10, bulkPercent=15)
        check("stored bulkQty", row()["bulkQty"], 10)
        check("stored bulkPercent", row()["bulkPercent"], 15)

        print("\nSTEP 2  the PUBLIC catalogue the browser reads carries the offer")
        pub = next(p for p in client.get("/api/catalog").get_json()["products"]
                   if p["id"] == PID)
        check("public bulkQty", pub["bulkQty"], 10)
        check("public bulkPercent", pub["bulkPercent"], 15)

        print("\nSTEP 3  order exactly 10 units -> threshold is EXCLUSIVE, no discount")
        b = order("JA-E2E-010", 10)
        check("line total", b["items"][0]["price"], PRICE * 10)
        check("no bulkDiscount on the order", bool(b.get("bulkDiscount")), False)

        print("\nSTEP 4  order 11 units -> 15% off every unit")
        b = order("JA-E2E-011", 11)
        check("line total", b["items"][0]["price"], 850 * 11)
        check("per-line bulkPercent", b["items"][0]["bulkPercent"], 15)
        check("order total", b["total"], 850 * 11)
        check("bulkDiscount entry", b["bulkDiscount"],
              [{"id": PID, "name": "Bulk E2E Perfume", "qty": 11, "percent": 15}])

        print("\nSTEP 5  the stored order payload + admin order view carry it too")
        # The server mints its own order id (the posted one is a request hint),
        # so read the id it answered with.
        oid = b["id"]
        print(f"         (server order id: {oid})")
        payload = json.loads(one("SELECT payload FROM orders WHERE id=?",
                                 (oid,))["payload"])
        check("payload bulkDiscount percent", payload["bulkDiscount"][0]["percent"], 15)
        check("payload line bulkPercent", payload["items"][0]["bulkPercent"], 15)
        admin_orders = client.get(f"/api/admin/orders?q={oid}",
                                  headers={"X-CSRF-Token": tok}).get_json()["orders"]
        check("admin view percent",
              next(o for o in admin_orders if o["id"] == oid)
              ["bulkDiscount"][0]["percent"], 15)

        print("\nSTEP 6  an UNRELATED edit (a rename) must not drop the offer")
        save(name="Bulk E2E Perfume renamed")
        check("bulkQty after rename", row()["bulkQty"], 10)
        check("bulkPercent after rename", row()["bulkPercent"], 15)

        print("\nSTEP 7  clearing both boxes switches the offer OFF")
        save(bulkQty=None, bulkPercent=None)
        check("bulkQty cleared", row().get("bulkQty"), None)
        check("bulkPercent cleared", row().get("bulkPercent"), None)
        b = order("JA-E2E-100", 100)
        check("no discount once cleared", bool(b.get("bulkDiscount")), False)

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
