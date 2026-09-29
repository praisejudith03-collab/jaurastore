"""Regression coverage for the live-environment backend fixes.

Groups:
  1. /api/admin/needs-attention no longer 500s on a bare module-level
     `supabase_store` reference (NameError).
  2. A payment-proof storage upload failure must never fail an otherwise
     valid checkout.
  3. Per-option price overrides AND per-option compare-at ("was") prices
     survive catalog.normalize() so they persist instead of reverting.
  4. The abandoned-cart reader degrades gracefully when its table is
     missing (PGRST205 / uninitialised SQLite) instead of raising.
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
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
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def csrf(client):
    return client.get("/api/config").get_json()["csrf"]


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


# --------------------------------------------------------------- 1. NameError
def test_needs_attention_endpoint_does_not_500(client):
    """Previously raised NameError: name 'supabase_store' is not defined."""
    login(client)
    r = client.get("/api/admin/needs-attention")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert "supplierWarnings" in body
    assert isinstance(body["supplierWarnings"], list)


# ------------------------------------------------- 2. checkout storage resilience
def _make_orderable(pid):
    saved, _action, _mirrored = catalog_mod.upsert({
        "id": pid, "sku": pid.upper().replace("-", ""), "slug": pid,
        "name": "Storage Resilience " + pid, "category": "beauty",
        "priceNgn": 5000, "stock": 20, "online": True,
    }, "tester")
    assert saved is not None
    return saved


def test_checkout_completes_when_proof_upload_fails(client, monkeypatch):
    pid = "jau-store-fail"
    _make_orderable(pid)
    execute("DELETE FROM rate_limits WHERE action='order'")

    # Force the storage layer to reject the upload the way a Supabase Storage
    # outage would (validation passes, the WRITE fails).
    import storage
    monkeypatch.setattr(storage, "save_image",
                        lambda *a, **k: (False, "Supabase Storage upload failed.", ""))

    tok = csrf(client)
    order = {
        "id": "JA-STOREFAIL", "currency": "NGN", "total": 5000,
        "customer": {"name": "Buyer", "email": "buyer@example.com",
                     "phone": "+2348012345678", "city": "Lagos",
                     "zone": "Lagos Mainland", "address": "1 Test St"},
        "items": [{"id": pid, "name": "Storage Resilience", "qty": 1, "price": 5000}],
    }
    data = {
        "order": json.dumps(order),
        "proof": (io.BytesIO(b"\xff\xd8\xff\xe0" + b"0" * 512 + b"\xff\xd9"),
                  "receipt.jpg"),
    }
    r = client.post("/api/orders", data=data, content_type="multipart/form-data",
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert body["id"] == "JA-STOREFAIL"
    # The sale went through with an empty proof and a clear flag for admin.
    assert body.get("proofUploadFailed") is True
    assert body.get("proofUrl") in ("", None)


# -------------------------------------------- 3. variant pricing persistence
def test_option_prices_and_compare_at_survive_normalize():
    normalized = catalog_mod.normalize({
        "id": "jau-variant-price", "name": "Hair Serum Set", "category": "beauty",
        "priceNgn": 3000, "compareNgn": 4000,
        "options": [{"title": "Type", "type": "TEXT",
                     "values": ["Serum", "Shampoo", "Conditioner"]}],
        "optionPrices": {"Type: Serum": 3000, "Type: Shampoo": 3800},
        "optionCompareAt": {"Type: Serum": 4000, "Type: Shampoo": 4600},
    })
    assert normalized is not None
    assert normalized["optionPrices"]["Type: Serum"] == 3000
    assert normalized["optionPrices"]["Type: Shampoo"] == 3800
    assert normalized["optionCompareAt"]["Type: Serum"] == 4000
    assert normalized["optionCompareAt"]["Type: Shampoo"] == 4600


def test_option_compare_at_is_an_allowed_field():
    assert "optionCompareAt" in catalog_mod.BASE_FIELDS


def test_variant_override_is_used_for_server_side_pricing(client):
    """A variant with its own override must price at the override, not the base."""
    pid = "jau-variant-checkout"
    catalog_mod.upsert({
        "id": pid, "sku": "VARCHK", "slug": pid, "name": "Serum Bundle",
        "category": "beauty", "priceNgn": 3000, "online": True, "stock": 20,
        "options": [{"title": "Type", "type": "TEXT",
                     "values": ["Serum", "Shampoo"]}],
        "optionStock": {"Serum": 10, "Shampoo": 10},
        "optionPrices": {"Shampoo": 3800},
    }, "tester")
    import api
    live = {str(p.get("id")): p for p in catalog_mod.merged(include_hidden=True)}
    prod = live[pid]
    assert api._server_unit_price(prod, "NGN", "Shampoo") == 3800
    assert api._server_unit_price(prod, "NGN", "Serum") == 3000


# ------------------------------------------- 4. abandoned-cart table resilience
def test_due_carts_survives_missing_table(monkeypatch):
    import abandoned

    def _boom(*a, **k):
        raise RuntimeError("relation \"abandoned_carts\" does not exist / PGRST205")

    monkeypatch.setattr(abandoned, "query", _boom)
    # Must not raise even though the local read blew up.
    rows = abandoned.due_carts(limit=5)
    assert rows == []
