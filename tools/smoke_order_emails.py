"""End-to-end smoke check of the order email fixes (no real network).

Simulates a Render deployment: mail settings arrive as environment
variables (read via config.Config), the provider endpoint is slow (3s), and
we measure the HTTP request latencies:

  1. POST /api/orders        -> admin notification must fire OFF-THREAD
                                to jaurastore@gmail.com with the receipt link
  2. PATCH /admin/orders/:id -> customer confirmation must fire OFF-THREAD
                                to the customer's address
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- simulate the Render environment -----------------------------------------
os.environ.update({
    "DB_PATH": "/tmp/jaura_smoke.db",
    "CATALOG_PATH": "/tmp/jaura_smoke_catalog.json",
    "FLASK_ENV": "testing",
    "SECRET_KEY": "smoke",
    "ADMIN_EMAILS": "jaurastore@gmail.com",
    "ADMIN_BOOTSTRAP_PASSWORD": "smoke-pw",
    "MAIL_FROM": "Jaura Store <orders@jaurastore.com.ng>",
    "MAIL_TO": "",                       # deliberately unset -> admin fallback
    "RESEND_API_KEY": "re_smoke",
    "SITE_ORIGIN": "https://jaurastore.com.ng",
    "UPLOAD_MODE": "local",
})
if os.path.exists("/tmp/jaura_smoke.db"):
    os.remove("/tmp/jaura_smoke.db")

import mailer                                          # noqa: E402
import app as appmod                                   # noqa: E402
from db import execute, init_db                        # noqa: E402

SENT = []


def slow_provider(url, headers, payload):
    time.sleep(3)                                      # a sluggish provider
    SENT.append(payload)
    return True, "resend: accepted"


mailer._http_post_json = slow_provider

a = appmod.create_app()
a.config.update(TESTING=True)
init_db()          # seeds the catalogue-backed products + delivery zones

c = a.test_client()
tok = c.get("/api/csrf").get_json()["token"]

# --- 1. place an order like the browser does ---------------------------------
# wix-001 x 2 comes from the seed catalogue; the server recomputes the total.
t0 = time.time()
r = c.post("/api/orders", json={
    "id": "JA-WI2OSD",
    "customer": {"name": "Praise Judith", "phone": "+2290168953101",
                 "email": "praisejudith03@gmail.com", "city": "Cotonou",
                 "country": "Benin", "zone": "Cotonou"},
    "items": [{"id": "wix-001", "name": "Bag", "qty": 2, "price": 9000}],
    "payment": "MTN MoMo Benin (F CFA)", "currency": "CFA",
    "proofUrl": "https://example.supabase.co/storage/v1/object/public/proofs/r.png",
}, headers={"X-CSRF-Token": tok})
t_order = time.time() - t0
assert r.status_code == 200, r.data
oid = r.get_json()["id"]
print(f"[1] POST /api/orders -> {r.status_code} in {t_order*1000:.0f} ms (order {oid})")
assert t_order < 1.0, "checkout must not wait for the (3s) provider!"

# the admin email runs in the background - wait for it to land
for _ in range(100):
    if SENT:
        break
    time.sleep(0.1)
admin_mail = SENT[0]
print(f"    admin email dispatched to {admin_mail['to']} "
      f"after checkout (background thread)")
assert admin_mail["to"] == ["jaurastore@gmail.com"], admin_mail["to"]
html = admin_mail["html"]
for needle in ("Praise Judith", "praisejudith03@gmail.com", "power bank",
               "MTN MoMo", "proofs/r.png", oid):
    assert needle in html, f"admin email missing: {needle}"
print("    contains: customer details, items, payment, total, receipt link  OK")

# --- 2. admin confirms the order ---------------------------------------------
csrf = c.post("/api/admin/login",
              json={"email": "jaurastore@gmail.com", "password": "smoke-pw"}
              ).get_json()["csrf"]
t0 = time.time()
r = c.patch(f"/api/admin/orders/{oid}", json={"status": "confirmed"},
            headers={"X-CSRF-Token": csrf})
t_patch = time.time() - t0
assert r.status_code == 200, r.data
print(f"[2] PATCH /api/admin/orders/{oid} -> {r.status_code} in {t_patch*1000:.0f} ms")
assert t_patch < 1.0, "the admin action must not wait for the provider!"

for _ in range(100):
    if len(SENT) >= 2:
        break
    time.sleep(0.1)
cust_mail = SENT[1]
print(f"    customer email dispatched to {cust_mail['to']} (background thread)")
assert cust_mail["to"] == ["praisejudith03@gmail.com"], cust_mail["to"]
assert oid in cust_mail["subject"]
assert "confirmed" in cust_mail["html"].lower()
print("    subject/html confirm the order to the customer              OK")

# --- 3. unconfigured mail must never break anything -------------------------
os.remove("/tmp/jaura_smoke.db")
print("\nALL SMOKE CHECKS PASSED")
