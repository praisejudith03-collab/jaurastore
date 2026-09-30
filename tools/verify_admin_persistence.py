#!/usr/bin/env python3
"""Live admin product persistence probe.

This is intentionally a real API round-trip against staging/production, not a
unit test. It creates one uniquely named product with every high-risk admin
field, forces a no-cache admin catalogue reload, verifies the saved row still
contains the exact values, and (by default) deletes the probe product again.

Required environment:
  JAURA_BASE_URL        e.g. https://jaurastore.onrender.com
  ADMIN_PASSWORD        admin/bootstrap password for the target site
Optional:
  ADMIN_EMAIL           default jaurastore@gmail.com
  VERIFY_CLEANUP        1 (default) deletes the probe after verification; set 0
                        to leave it visible for manual browser inspection.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Any


BASE = (os.environ.get("JAURA_BASE_URL") or os.environ.get("SITE_ORIGIN") or "").rstrip("/")
EMAIL = os.environ.get("ADMIN_EMAIL") or "jaurastore@gmail.com"
PASSWORD = os.environ.get("ADMIN_PASSWORD") or os.environ.get("ADMIN_BOOTSTRAP_PASSWORD") or ""
CLEANUP = os.environ.get("VERIFY_CLEANUP", "1").lower() not in {"0", "false", "no"}


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


if not BASE:
    die("Set JAURA_BASE_URL (or SITE_ORIGIN) to the live staging/production URL.")
if not PASSWORD:
    die("Set ADMIN_PASSWORD for the target admin account.")

jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def api(path: str, *, method: str = "GET", body: Any = None, csrf: str = "") -> dict[str, Any]:
    url = BASE + "/" + path.lstrip("/")
    data = None
    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=45) as res:
            raw = res.read().decode("utf-8")
            return json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            payload = {"raw": raw}
        die(f"{method} {path} failed with HTTP {exc.code}: {payload}")


login = api("api/admin/login", method="POST", body={"email": EMAIL, "password": PASSWORD})
csrf = str(login.get("csrf") or "")
if not login.get("ok") or not csrf:
    die(f"Admin login failed: {login}")

stamp = int(time.time())
pid = f"arena-persist-{stamp}"
option_key = "Colour: Arena Rose Alias"
product = {
    "id": pid,
    "sku": f"ARENA-{stamp}",
    "slug": pid,
    "name": f"Arena Persistence Probe {stamp}",
    "nameFr": f"Sonde persistance Arena {stamp}",
    "category": "beauty",
    "priceNgn": 12345,
    "compareNgn": 15678,
    "image": "images/products/_placeholder.jpg",
    "images": ["images/products/_placeholder.jpg", "images/brand/logo.jpg"],
    "description": "Persistence probe description: every field must survive reload.",
    "descriptionFr": "Description de test de persistance.",
    "stock": 7,
    "stock_quantity": 7,
    "badge": "new",
    "featured": True,
    "online": True,
    "colors": ["Arena Rose Alias"],
    "options": [{"title": "Colour", "type": "COLOR", "values": ["Arena Rose Alias"]}],
    "optionStock": {"Arena Rose Alias": 7},
    "optionPrices": {option_key: 11111},
    "optionCompareAt": {option_key: 15000},
    "optionSupplierSku": {option_key: "https://supplier.example/arena-rose"},
    "optionSku": {option_key: "ARENA-ROSE-ALIAS-1"},
    "dimensions": "12 x 8 x 4 cm",
    "bulkQty": 10,
    "bulkPercent": 15,
    "supplierSku": "https://supplier.example/product-main",
    "supplier_url": "https://supplier.example/product-main",
    "reviews": [{
        "name": "Arena QA",
        "body": "Review note persistence probe.",
        "rating": 5,
        "created_at": "2026-09-30T10:00:00Z",
    }],
}

saved = api("api/admin/products", method="POST", body={"product": product}, csrf=csrf)
if not saved.get("ok"):
    die(f"Product save was not acknowledged: {saved}")

catalog = api(f"api/catalog?all=1&_fresh={int(time.time() * 1000)}")
rows = catalog.get("products") or []
row = next((p for p in rows if str(p.get("id")) == pid), None)
if not row:
    die(f"Saved product {pid} was not returned by hard-refresh catalogue reload")

checks = {
    "supplierSku": "https://supplier.example/product-main",
    "compareNgn": 15678,
    "optionPrices." + option_key: 11111,
    "optionCompareAt." + option_key: 15000,
    "optionSupplierSku." + option_key: "https://supplier.example/arena-rose",
    "optionSku." + option_key: "ARENA-ROSE-ALIAS-1",
    "optionStock.Arena Rose Alias": 7,
    "bulkQty": 10,
    "bulkPercent": 15,
    "featured": True,
    "online": True,
    "reviews.0.body": "Review note persistence probe.",
}


def dotted(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur

failures = []
for key, expected in checks.items():
    got = dotted(row, key)
    if got != expected:
        failures.append(f"{key}: expected {expected!r}, got {got!r}")
if len(row.get("images") or []) < 2:
    failures.append("images: expected both uploaded/attached media entries after reload")
if failures:
    die("Persistence probe failed:\n  " + "\n  ".join(failures))

print("OK: live save -> hard-refresh reload preserved all probed admin product fields.")
print(f"Product id: {pid}")

if CLEANUP:
    try:
        deleted = api(f"api/admin/products/{urllib.parse.quote(pid)}", method="DELETE", csrf=csrf)
        if deleted.get("ok"):
            print("Cleanup: probe product deleted.")
        else:
            print(f"Cleanup warning: delete response was {deleted}", file=sys.stderr)
    except SystemExit:
        print("Cleanup warning: probe product could not be deleted; remove it manually.", file=sys.stderr)
        raise
else:
    print("Cleanup disabled; probe product was left on the target site for manual inspection.")
