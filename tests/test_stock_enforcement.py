"""Exact stock accuracy enforced everywhere, with public counts exposed safely.

The production defects this file pins shut forever:

  * a product (or a single variant) at 0 stock showed "In Stock" and stayed
    orderable - the storefront read one stock alias, the reservation guarded
    another, and per-variant availability never reached the browser at all;
  * per-variant stock was validated at checkout but the reservation was
    variant-blind, so a sold-out variant kept its stale number and could be
    ordered again while other variants still had units;
  * the shelf itself was not atomic: validation and decrement were separate,
    unlocked steps, so the same last unit could be taken twice and the shelf
    could be driven below zero;
  * public availability is exposed through dedicated exact-stock aliases,
    while the admin storage field names remain private.

Every product and variant is capped at its saved integer quantity. Requests
above the remaining count return a precise 409 and reserve nothing; concurrent
checkouts may never oversell the same shelf. Public storefront rows expose only
the exact availability aliases needed to cap the selector and cart, never the
admin storage column names.

Everything here runs on the local backend (no Supabase needed): reservation
goes through catalog.reserve_stock, which guards under the catalogue's
cross-process file lock - the same invariant the PostgreSQL RPC enforces in
production (see test_stock_rpc_concurrency.py for the SQL side).

Run with:  python3 -m pytest tests/test_stock_enforcement.py -q
"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

EMAIL = "jaurastore@gmail.com"
OUT_OF_STOCK = "Out of Stock"

FORBIDDEN_PUBLIC_KEYS = ("stock", "stock_quantity", "optionStock", "variantStock", "inventory")


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


def make_product(pid, stock=10, option_stock=None, options=None, price=2000,
                 legacy_id=None):
    rec = {
        "id": pid,
        "sku": pid.upper().replace("-", ""),
        "slug": pid,
        "name": "Enforcement Test " + pid,
        "category": "beauty",
        "priceNgn": price,
        "stock": stock,
        "stock_quantity": stock,
        "online": True,
    }
    if options:
        rec["options"] = options
    if option_stock is not None:
        rec["optionStock"] = option_stock
    if legacy_id:
        rec["legacyId"] = legacy_id
    saved, action, _mirrored = catalog_mod.upsert(rec, "tester")
    assert saved is not None, f"product {pid} was rejected"
    return saved


def product_row(pid):
    for p in catalog_mod.merged(include_hidden=True):
        if str(p.get("id")) == pid:
            return p
    return None


def place(client, oid, items):
    """POST /api/orders; returns the response (caller asserts the status)."""
    execute("DELETE FROM rate_limits WHERE action='order'")
    total = sum(int(i.get("price") or 2000) * int(i.get("qty") or 1) for i in items)
    body = {
        "id": oid, "currency": "NGN", "total": total,
        "customer": {"name": "Stock Tester", "email": "stock@example.com",
                     "phone": "+2348012345678", "city": "Lagos",
                     "zone": "Lagos Mainland", "address": "1 Test St"},
        "items": items,
    }
    return client.post("/api/orders", json=body,
                       headers={"X-CSRF-Token": csrf(client)})


COLOR_OPTS = [{"title": "Colour", "type": "COLOR", "values": ["Red", "Black"]}]


# =========================================================== A. display
def test_zero_stock_product_is_publicly_out_of_stock(client):
    make_product("jau-enf-zero", stock=0)
    r = client.get("/api/catalog")
    assert r.status_code == 200
    row = next(p for p in r.get_json()["products"] if p["id"] == "jau-enf-zero")
    assert row["stock_status"] == "out"
    # Exact availability is explicitly exposed, not the raw admin columns.
    assert row["stock_available"] == 0
    for key in FORBIDDEN_PUBLIC_KEYS:
        assert key not in row, f"public row leaked {key}"


def test_variant_product_with_every_variant_zero_is_out(client):
    make_product("jau-enf-allout", stock=0, option_stock={"Red": 0, "Black": 0},
                 options=COLOR_OPTS)
    r = client.get("/api/catalog")
    row = next(p for p in r.get_json()["products"] if p["id"] == "jau-enf-allout")
    assert row["stock_status"] == "out"
    assert row["stock_available"] == 0
    assert row["option_stock_status"] == {"Red": "out", "Black": "out"}
    assert row["option_stock_available"] == {"Red": 0, "Black": 0}


def test_unassigned_or_unknown_variant_is_zero_stock(client):
    make_product("jau-enf-unassigned-variant", stock=5, options=COLOR_OPTS)
    public = client.get("/api/catalog").get_json()["products"]
    row = next(p for p in public if p["id"] == "jau-enf-unassigned-variant")
    # The saved product-level count remains visible, but absent per-variant
    # allocations are not borrowed by any choice on the product page.
    assert row["stock_status"] == "in"
    assert row["stock_available"] == 5
    missing_map = place(client, "JA-ENF-UNASSIGNED", [
        {"id": "jau-enf-unassigned-variant", "name": "X", "qty": 1,
         "price": 2000, "color": "Red"},
    ])
    assert missing_map.status_code == 409
    assert missing_map.get_json()["error"] == OUT_OF_STOCK
    assert product_row("jau-enf-unassigned-variant")["stock"] == 5

    make_product("jau-enf-unknown-variant", stock=5,
                 option_stock={"Red": 5, "Black": 0}, options=COLOR_OPTS)
    unknown = place(client, "JA-ENF-UNKNOWN", [
        {"id": "jau-enf-unknown-variant", "name": "X", "qty": 1,
         "price": 2000, "color": "Green"},
    ])
    assert unknown.status_code == 409
    assert unknown.get_json()["error"] == OUT_OF_STOCK


def test_variant_stock_cannot_be_bypassed_by_omitting_or_faking_selection(client):
    make_product("jau-enf-variant-required", stock=5,
                 option_stock={"Red": 5, "Black": 0}, options=COLOR_OPTS)
    omitted = place(client, "JA-ENF-OMITTED", [
        {"id": "jau-enf-variant-required", "name": "X", "qty": 1, "price": 2000},
    ])
    assert omitted.status_code == 409
    assert omitted.get_json()["error"] == OUT_OF_STOCK
    unknown = place(client, "JA-ENF-FAKE", [
        {"id": "jau-enf-variant-required", "name": "X", "qty": 1,
         "price": 2000, "color": "Not a real variant"},
    ])
    assert unknown.status_code == 409
    assert unknown.get_json()["error"] == OUT_OF_STOCK
    assert product_row("jau-enf-variant-required")["stock"] == 5


def test_custom_note_is_product_gated_and_carries_into_order_email(client):
    import api as api_mod
    from mailer import order_received_email_html

    enabled = make_product("jau-enf-note-enabled", stock=3)
    enabled.update({"enableCustomNote": True,
                    "customNotePrompt": "Type the name to engrave"})
    enabled, _action, _mirrored = catalog_mod.upsert(enabled, "tester")
    assert enabled["customNotePrompt"] == "Type the name to engrave"
    checked, _total, error = api_mod._checkout_items([{
        "id": enabled["id"], "name": enabled["name"], "qty": 1,
        "note": "Engrave Amina on the lid",
    }], "NGN")
    assert error is None
    assert checked[0]["note"] == "Engrave Amina on the lid"
    email = order_received_email_html({
        "id": "JA-NOTE1", "currency": "NGN", "total": 2000,
        "customer": {"name": "Amina"}, "items": checked,
    })
    assert "Product note: Engrave Amina on the lid" in email

    disabled = make_product("jau-enf-note-disabled", stock=3)
    ignored, _total, error = api_mod._checkout_items([{
        "id": disabled["id"], "name": disabled["name"], "qty": 1,
        "note": "This must not be persisted",
    }], "NGN")
    assert error is None
    assert "note" not in ignored[0]


def test_variant_availability_is_public_without_quantities(client):
    """Red: 5, Black: 0 - the shopper learns which colour is sold out, never
    how many Reds remain."""
    make_product("jau-enf-var", stock=5, option_stock={"Red": 5, "Black": 0},
                 options=COLOR_OPTS)
    r = client.get("/api/catalog")
    row = next(p for p in r.get_json()["products"] if p["id"] == "jau-enf-var")
    assert row["stock_status"] == "in"
    assert row["option_stock_status"] == {"Red": "in", "Black": "out"}
    assert "optionStock" not in row and "stock" not in row
    assert "5" not in json.dumps(row["option_stock_status"])


def test_stock_status_reflects_variant_sum_not_stale_total(client):
    """stock says 24 but every variant is 0: the product must read out, and a
    save re-syncs the total to the variant sum."""
    make_product("jau-enf-sync", stock=24, option_stock={"Red": 0, "Black": 0},
                 options=COLOR_OPTS)
    saved = product_row("jau-enf-sync")
    assert saved["stock"] == 0 and saved["stock_quantity"] == 0
    r = client.get("/api/catalog")
    row = next(p for p in r.get_json()["products"] if p["id"] == "jau-enf-sync")
    assert row["stock_status"] == "out"


def test_one_stock_alias_can_never_beat_the_other_again(client):
    """catalog.stock_of is THE reader: stock_quantity wins when present (the
    pinned write contract), the legacy alias only when the canonical column
    is absent. The split readers are what made a product show In Stock while
    checkout reserved against 0."""
    assert catalog_mod.stock_of({"stock": 24, "stock_quantity": 0}) == 0
    assert catalog_mod.stock_of({"stock": 24, "stock_quantity": None}) == 0
    assert catalog_mod.stock_of({"stock": 24, "stock_quantity": "  "}) == 0
    assert catalog_mod.stock_of({"stock": 7, "stock_quantity": 9}) == 9
    assert catalog_mod.stock_of({}) == 0
    assert catalog_mod.stock_of({"stock_quantity": -4}) == 0
    assert catalog_mod.stock_of({"stock": "junk"}) == 0
    assert catalog_mod.stock_of(None) == 0


# ======================================================= B. enforcement
def test_zero_stock_product_is_not_orderable(client):
    make_product("jau-enf-zero", stock=0)
    r = place(client, "JA-ENF001", [{"id": "jau-enf-zero", "name": "X", "qty": 1,
                                     "price": 2000}])
    assert r.status_code == 409
    assert r.get_json()["code"] == "out_of_stock"
    assert r.get_json()["error"] == OUT_OF_STOCK


def test_variant_boundaries_allow_exact_stock_and_reject_one_over(client):
    make_product("jau-enf-var2", stock=15, option_stock={"Red": 5, "Black": 10},
                 options=COLOR_OPTS)
    ok = place(client, "JA-ENF-VAR2-01", [{"id": "jau-enf-var2", "name": "X", "qty": 4,
                                      "price": 2000, "color": "Red"}])
    assert ok.status_code == 200, ok.get_json()
    over_red = place(client, "JA-ENF-VAR2-02", [{"id": "jau-enf-var2", "name": "X", "qty": 2,
                                            "price": 2000, "color": "Red"}])
    assert over_red.status_code == 409
    assert over_red.get_json()["code"] == "insufficient_stock"
    assert over_red.get_json()["error"] == "Only 1 items remaining in stock"
    row = product_row("jau-enf-var2")
    assert row["optionStock"]["Red"] == 1
    assert row["optionStock"]["Black"] == 10, "Black was not touched"

    exact_red = place(client, "JA-ENF-VAR2-03", [{"id": "jau-enf-var2", "name": "X", "qty": 1,
                                              "price": 2000, "color": "Red"}])
    assert exact_red.status_code == 200, exact_red.get_json()
    over_black = place(client, "JA-ENF-VAR2-04", [{"id": "jau-enf-var2", "name": "X", "qty": 11,
                                              "price": 2000, "color": "Black"}])
    assert over_black.status_code == 409
    assert over_black.get_json()["error"] == "Only 10 items remaining in stock"
    assert product_row("jau-enf-var2")["optionStock"]["Black"] == 10
    exact_black = place(client, "JA-ENF-VAR2-05", [{"id": "jau-enf-var2", "name": "X", "qty": 10,
                                               "price": 2000, "color": "Black"}])
    assert exact_black.status_code == 200, exact_black.get_json()
    row = product_row("jau-enf-var2")
    assert row["optionStock"]["Red"] == 0
    assert row["optionStock"]["Black"] == 0 and row["stock"] == 0
    gone = place(client, "JA-ENF-VAR2-06", [{"id": "jau-enf-var2", "name": "X", "qty": 1,
                                        "price": 2000, "color": "Red"}])
    assert gone.status_code == 409
    assert gone.get_json()["error"] == OUT_OF_STOCK
    assert product_row("jau-enf-var2")["stock"] == 0, "never below zero"


def test_duplicate_variant_lines_share_one_exact_inventory_pool(client):
    make_product("jau-enf-var3", stock=5, option_stock={"Red": 5, "Black": 0},
                 options=COLOR_OPTS)
    over = place(client, "JA-ENF-VAR3-01", [
        {"id": "jau-enf-var3", "name": "X", "qty": 3, "price": 2000, "color": "Red"},
        {"id": "jau-enf-var3", "name": "X", "qty": 3, "price": 2000, "color": "Colour: Red"},
    ])
    assert over.status_code == 409
    assert over.get_json()["error"] == "Only 5 items remaining in stock"
    assert product_row("jau-enf-var3")["optionStock"]["Red"] == 5

    exact = place(client, "JA-ENF-VAR3-02", [
        {"id": "jau-enf-var3", "name": "X", "qty": 3, "price": 2000, "color": "Red"},
        {"id": "jau-enf-var3", "name": "X", "qty": 2, "price": 2000, "color": "Colour: Red"},
    ])
    assert exact.status_code == 200, exact.get_json()
    row = product_row("jau-enf-var3")
    assert row["optionStock"]["Red"] == 0
    assert row["stock"] == 0, "exactly the saved quantity was reserved"


def test_order_above_stock_is_rejected_without_reserving_or_writing(client):
    make_product("jau-enf-num", stock=2)
    r = place(client, "JA-ENF-OVER-01", [{"id": "jau-enf-num", "name": "Silk Press",
                                     "qty": 3, "price": 2000}])
    assert r.status_code == 409
    assert r.get_json()["code"] == "insufficient_stock"
    assert r.get_json()["error"] == "Only 2 items remaining in stock"
    assert product_row("jau-enf-num")["stock"] == 2
    from db import one
    assert one("SELECT id FROM orders WHERE id=?", ("JA-ENF-OVER-01",)) is None



def test_checkout_race_rejection_rolls_back_earlier_reservations(client, monkeypatch):
    """A concurrent sellout on line two rejects the order and releases line one."""
    make_product("jau-enf-rollback-a", stock=5)
    make_product("jau-enf-rollback-b", stock=5)
    reserve = catalog_mod.reserve_stock
    calls = []

    def race_on_second(pid, qty, option_key=None, actor=""):
        calls.append((pid, qty))
        if pid == "jau-enf-rollback-b" and len(calls) == 2:
            # Simulate another shopper taking B after checkout validated both
            # lines but before this request reaches the atomic reservation.
            assert reserve(pid, 5, option_key=option_key, actor="racer")
        return reserve(pid, qty, option_key=option_key, actor=actor)

    monkeypatch.setattr(catalog_mod, "reserve_stock", race_on_second)
    result = place(client, "JA-ENF-ROLLBACK", [
        {"id": "jau-enf-rollback-a", "name": "A", "qty": 2, "price": 2000},
        {"id": "jau-enf-rollback-b", "name": "B", "qty": 2, "price": 2000},
    ])
    assert result.status_code == 409
    assert result.get_json()["error"] == "Out of Stock"
    assert product_row("jau-enf-rollback-a")["stock"] == 5
    assert product_row("jau-enf-rollback-b")["stock"] == 0
    from db import one
    assert one("SELECT id FROM orders WHERE id=?", ("JA-ENF-ROLLBACK",)) is None


def test_fifteen_units_from_a_ten_unit_shelf_are_rejected(client):
    make_product("jau-enf-10-to-15", stock=10)
    r = place(client, "JA-ENF-OVER-02", [{"id": "jau-enf-10-to-15", "name": "X",
                                      "qty": 11, "price": 2000}])
    assert r.status_code == 409
    assert r.get_json()["error"] == "Only 10 items remaining in stock"
    assert product_row("jau-enf-10-to-15")["stock"] == 10


def test_cart_validator_is_csrf_protected_stateless_and_server_priced(client):
    make_product("jau-enf-cart", stock=10, price=2000)
    body = {"currency": "NGN", "items": [{"id": "jau-enf-cart", "qty": 2,
                                            "price": 1, "name": "fake"}]}
    assert client.post("/api/cart", json=body).status_code == 403
    token = csrf(client)
    valid = client.post("/api/cart", json=body, headers={"X-CSRF-Token": token})
    assert valid.status_code == 200, valid.get_json()
    result = valid.get_json()
    assert result["ok"] is True and result["subtotal"] == 4000
    assert result["items"][0]["price"] == 4000
    assert product_row("jau-enf-cart")["stock"] == 10, "cart validation must not reserve"

    # The stateless cart endpoint enforces the same exact inventory boundary,
    # but does not reserve stock when it accepts an in-range quantity.
    too_many = {"currency": "NGN", "items": [{"id": "jau-enf-cart", "qty": 11}]}
    rejected = client.put("/api/cart", json=too_many,
                          headers={"X-CSRF-Token": token})
    assert rejected.status_code == 409
    assert rejected.get_json()["error"] == "Only 10 items remaining in stock"
    assert product_row("jau-enf-cart")["stock"] == 10


def test_catalog_category_and_cart_reads_handle_concurrent_shoppers(client, app):
    make_product("jau-enf-public-race", stock=10)
    shoppers = 12
    barrier = threading.Barrier(shoppers)
    results = []

    def browse(i):
        with app.test_client() as shopper:
            token = csrf(shopper)
            barrier.wait(timeout=10)
            catalog_status = shopper.get("/api/catalog").status_code
            product_status = shopper.get("/api/products").status_code
            category_status = shopper.get("/api/categories").status_code
            cart_status = shopper.post("/api/cart", json={
                "currency": "NGN",
                "items": [{"id": "jau-enf-public-race", "qty": 1}],
            }, headers={"X-CSRF-Token": token}).status_code
            results.append((i, catalog_status, product_status, category_status, cart_status))

    threads = [threading.Thread(target=browse, args=(i,)) for i in range(shoppers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(results) == shoppers, results
    assert all(status == 200 for row in results for status in row[1:]), results
    assert product_row("jau-enf-public-race")["stock"] == 10


def test_checkout_alias_rejects_over_stock_without_reserving(client):
    make_product("jau-enf-checkout-alias", stock=1)
    body = {
        "id": "JA-ENF-CHECKOUT-ALIAS", "currency": "NGN", "total": 4000,
        "customer": {"name": "Alias Tester", "email": "alias@example.com",
                     "phone": "+2348012345678", "city": "Lagos",
                     "zone": "Lagos Mainland", "address": "1 Test St"},
        "items": [{"id": "jau-enf-checkout-alias", "name": "X", "qty": 2,
                   "price": 2000}],
    }
    response = client.post("/api/checkout", json=body,
                           headers={"X-CSRF-Token": csrf(client)})
    assert response.status_code == 409
    assert response.get_json()["error"] == "Only 1 items remaining in stock"
    assert product_row("jau-enf-checkout-alias")["stock"] == 1


def test_null_blank_absent_negative_and_zero_stock_fail_closed(client, monkeypatch):
    cases = [
        ("null", {"stock_quantity": None, "stock": 12}),
        ("blank", {"stock_quantity": "  ", "stock": 12}),
        ("absent", {}),
        ("negative", {"stock_quantity": -3, "stock": 12}),
        ("zero", {"stock_quantity": 0, "stock": 12}),
    ]
    for suffix, stock_fields in cases:
        pid = "jau-enf-missing-stock-" + suffix
        row = {"id": pid, "name": "Unassigned inventory", "priceNgn": 2000,
               "online": True, **stock_fields}
        monkeypatch.setattr(catalog_mod, "merged", lambda include_hidden=False, row=row: [row])
        result = place(client, "JA-ENF0" + str(len(suffix)) + suffix[:1].upper(),
                       [{"id": pid, "name": "X", "qty": 1, "price": 2000}])
        assert result.status_code == 409, (suffix, result.get_json())
        assert result.get_json()["error"] == OUT_OF_STOCK, suffix


def test_sold_out_variant_is_not_orderable_after_the_units_are_gone(client):
    make_product("jau-enf-var4", stock=5, option_stock={"Red": 5, "Black": 0},
                 options=COLOR_OPTS)
    ok = place(client, "JA-ENF-VAR4-01", [{"id": "jau-enf-var4", "name": "X", "qty": 5,
                                      "price": 2000, "color": "Red"}])
    assert ok.status_code == 200
    # the reservation decremented the VARIANT, not only the product total
    row = product_row("jau-enf-var4")
    assert row["optionStock"]["Red"] == 0 and row["stock"] == 0
    # and the next customer sees it as unavailable, in the variant map too
    r = place(client, "JA-ENF-VAR4-02", [{"id": "jau-enf-var4", "name": "X", "qty": 1,
                                     "price": 2000, "color": "Red"}])
    assert r.status_code == 409
    cat = client.get("/api/catalog").get_json()["products"]
    pub = next(p for p in cat if p["id"] == "jau-enf-var4")
    assert pub["stock_status"] == "out"
    assert pub["option_stock_status"]["Red"] == "out"


def test_legacy_cart_id_resolves_onto_the_real_row(client):
    """A cart line saved against a legacyId alias reserves the canonical row."""
    make_product("jau-enf-leg", stock=6, legacy_id="wix-enf-legacy")
    r = place(client, "JA-ENF-LEGACY", [{"id": "wix-enf-legacy", "name": "X", "qty": 2,
                                     "price": 2000}])
    assert r.status_code == 200, r.get_json()
    assert product_row("jau-enf-leg")["stock"] == 4


# ======================================================= C. lifecycle
def test_placement_reserves_confirm_never_double_decrements(client):
    make_product("jau-enf-life", stock=10)
    assert place(client, "JA-ENF020", [{"id": "jau-enf-life", "name": "X", "qty": 3,
                                        "price": 2000}]).status_code == 200
    assert product_row("jau-enf-life")["stock"] == 7
    tok = login(client)
    r = client.patch("/api/admin/orders/JA-ENF020", json={"status": "confirmed"},
                     headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert product_row("jau-enf-life")["stock"] == 7
    r = client.patch("/api/admin/orders/JA-ENF020", json={"status": "declined"},
                     headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert product_row("jau-enf-life")["stock"] == 10


def test_deleting_a_pending_order_returns_the_reserved_units(client):
    make_product("jau-enf-del", stock=10)
    assert place(client, "JA-ENF021", [{"id": "jau-enf-del", "name": "X", "qty": 4,
                                        "price": 2000}]).status_code == 200
    assert product_row("jau-enf-del")["stock"] == 6
    tok = login(client)
    r = client.delete("/api/admin/orders/JA-ENF021", headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert product_row("jau-enf-del")["stock"] == 10


def test_order_payload_records_the_reservation_moves(client):
    make_product("jau-enf-mv", stock=8, option_stock={"Red": 8, "Black": 0},
                 options=COLOR_OPTS)
    assert place(client, "JA-ENF022", [{"id": "jau-enf-mv", "name": "X", "qty": 2,
                                        "price": 2000, "color": "Red"}]).status_code == 200
    from db import one
    row = one("SELECT payload FROM orders WHERE id=?", ("JA-ENF022",))
    payload = json.loads(row["payload"])
    assert payload["stockApplied"] == [{"id": "jau-enf-mv", "option": "Red", "qty": 2}]


# ========================================================== D. races
def _race_orders(app, pid, n, qty=1, color="", tag="R"):
    """n shoppers hit checkout for the same product at the same moment."""
    results = []
    barrier = threading.Barrier(n)
    # A hermetic race: the order guard counts 30/hour per IP for the WHOLE
    # suite, and 429s from earlier modules would read as "no oversell" and
    # make the assertions below vacuous.
    execute("DELETE FROM rate_limits")

    def shopper(i):
        try:
            with app.test_client() as c:
                tok = csrf(c)
                barrier.wait(timeout=10)
                body = {
                    # Order ids must be unique per race: re-posting an id the
                    # server already stored hits the duplicate-order short
                    # circuit (a 200 that reserves nothing), which would make
                    # a healthy reservation look like an oversell.
                    "id": f"JA-{tag}{i:03d}", "currency": "NGN", "total": 2000 * qty,
                    "customer": {"name": "Racer", "email": f"r{i}@example.com",
                                 "phone": "+2348012345678", "city": "Lagos",
                                 "zone": "Lagos Mainland", "address": "1 Test St"},
                    "items": [{"id": pid, "name": "X", "qty": qty,
                               "price": 2000, "color": color}],
                }
                r = c.post("/api/orders", json=body, headers={"X-CSRF-Token": tok})
                results.append(r.status_code)
        except Exception:                    # pragma: no cover
            results.append(500)

    threads = [threading.Thread(target=shopper, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def test_concurrent_orders_for_the_last_unit_are_capped(client, app):
    """Only one of eight simultaneous orders may reserve the single saved unit."""
    make_product("jau-enf-race", stock=1)
    results = _race_orders(app, "jau-enf-race", 8, qty=1, tag="R1")
    assert results.count(200) == 1, results
    assert results.count(409) == 7, results
    assert product_row("jau-enf-race")["stock"] == 0


def test_concurrent_orders_never_drive_a_limited_run_below_zero(client, app):
    """5 units, 8 shoppers wanting 2 each: at most two orders can reserve."""
    make_product("jau-enf-race2", stock=5)
    results = _race_orders(app, "jau-enf-race2", 8, qty=2, tag="R2")
    assert results.count(200) == 2, results
    assert results.count(409) == 6, results
    stock = product_row("jau-enf-race2")["stock"]
    assert stock == 1, f"exactly four of the five units were reserved, left {stock}"


def test_concurrent_orders_for_the_last_variant_unit_are_capped(client, app):
    """Only one shopper gets Red's last unit; Black remains untouched."""
    make_product("jau-enf-race3", stock=5, option_stock={"Red": 1, "Black": 4},
                 options=COLOR_OPTS)
    results = _race_orders(app, "jau-enf-race3", 6, qty=1, color="Red", tag="R3")
    assert results.count(200) == 1, results
    assert results.count(409) == 5, results
    row = product_row("jau-enf-race3")
    assert row["optionStock"]["Red"] == 0
    assert row["optionStock"]["Black"] == 4, "another variant was not touched"
    assert row["stock"] == 4


# ============================================ E2. one store for the manager
def test_admin_stock_manager_writes_the_product_row(client, app):
    """PUT /api/admin/stock used to update a separate variant_stock copy the
    checkout never read: the manager said 10 while the product row still had
    sellable units. The setter now writes the product row FIRST (the same
    store reservations guard), and only then the label/threshold row."""
    make_product("jau-enf-mgr", stock=24)
    tok = login(client)
    r = client.put("/api/admin/stock", json={
        "productId": "jau-enf-mgr", "variant": "__default__",
        "qty": 7, "lowThreshold": 5}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["product"]["stock"] == 7
    assert body["product"]["stock_quantity"] == 7
    row = product_row("jau-enf-mgr")
    assert row["stock"] == 7 and row["stock_quantity"] == 7
    # the legacy row (labels/thresholds) is still kept in step
    from db import one
    legacy = one("SELECT qty, low_threshold FROM variant_stock "
                 "WHERE product_id='jau-enf-mgr' AND variant_key='__default__'")
    assert legacy is not None and legacy["qty"] == 7 and legacy["low_threshold"] == 5
    # and the shopper-facing view agrees with the exact number the owner typed
    # without exposing the admin storage columns.
    with app.test_client() as anon:
        cat = anon.get("/api/catalog").get_json()["products"]
    pub = next(p for p in cat if p["id"] == "jau-enf-mgr")
    assert pub["stock_status"] == "in"
    assert pub["stock_available"] == 7


def test_admin_stock_manager_sets_a_single_variant(client, app):
    make_product("jau-enf-mgr2", stock=15, option_stock={"Red": 5, "Black": 10},
                 options=COLOR_OPTS)
    tok = login(client)
    r = client.put("/api/admin/stock", json={
        "productId": "jau-enf-mgr2", "variant": "Red",
        "qty": 0, "label": "Rouge"}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    row = product_row("jau-enf-mgr2")
    assert row["optionStock"]["Red"] == 0
    assert row["optionStock"]["Black"] == 10
    assert row["stock"] == 10 and row["stock_quantity"] == 10   # variant sum
    # the sold-out variant is what the storefront now says (anonymous view)
    with app.test_client() as anon:
        cat = anon.get("/api/catalog").get_json()["products"]
    pub = next(p for p in cat if p["id"] == "jau-enf-mgr2")
    assert pub["option_stock_status"]["Red"] == "out"
    assert pub["option_stock_status"]["Black"] == "in"
    assert pub["option_stock_available"] == {"Red": 0, "Black": 10}
    # and checkout refuses it while its sibling still sells
    assert place(client, "JA-ENF030", [{"id": "jau-enf-mgr2", "name": "X", "qty": 1,
                                        "price": 2000, "color": "Red"}]).status_code == 409
    assert place(client, "JA-ENF031", [{"id": "jau-enf-mgr2", "name": "X", "qty": 1,
                                        "price": 2000, "color": "Black"}]).status_code == 200


def test_stock_view_reflects_reservations_and_manager_edits(client, app):
    """Both writers - checkout reservations and the admin stock manager -
    move the same store, so /api/stock can never disagree with either."""
    make_product("jau-enf-view", stock=10)
    assert place(client, "JA-ENF032", [{"id": "jau-enf-view", "name": "X", "qty": 4,
                                        "price": 2000}]).status_code == 200
    client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    admin = client.get("/api/stock").get_json()
    entry = next(i for i in admin["stock"]["jau-enf-view"]
                 if i["variant"] == "__default__")
    assert entry["qty"] == 6                       # 10 - the 4 reserved
    tok = csrf(client)
    r = client.put("/api/admin/stock", json={
        "productId": "jau-enf-view", "variant": "__default__", "qty": 2},
        headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    with app.test_client() as anon:
        pub = anon.get("/api/stock").get_json()
    entry = next(i for i in pub["stock"]["jau-enf-view"] if i["variant"] == "__default__")
    assert entry["state"] == "low"                 # 2 left, at/below the default threshold


def test_stock_manager_rejects_unknown_products(client):
    tok = login(client)
    r = client.put("/api/admin/stock", json={
        "productId": "jau-enf-nope", "variant": "__default__", "qty": 5},
        headers={"X-CSRF-Token": tok})
    assert r.status_code == 404
    assert r.get_json()["ok"] is False


# ================================================= E. storefront contract
def test_store_js_caps_cart_quantities_and_shows_exact_limit_text():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "js", "store.js"), encoding="utf-8").read()
    app_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "js", "app.js"), encoding="utf-8").read()
    assert OUT_OF_STOCK in src and OUT_OF_STOCK in app_src
    assert "Only ${remaining} items remaining in stock" in src
    assert "Only ${room} items remaining in stock" in app_src
    assert "stockProblems" in src
    assert "data-qty-stock-error" in app_src
    assert "qty.max = String(inputMax)" in app_src


def test_store_js_reads_the_public_variant_status_map():
    """The storefront derives per-variant availability from the numberless
    option_stock_status map the server ships."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "js", "store.js"), encoding="utf-8").read()
    assert "option_stock_status" in src
    assert "optionStockStatus" in src


def test_app_js_shows_a_server_rejection_instead_of_fake_success():
    """A definitive 409 at checkout must keep the cart and tell the shopper -
    never paint the order-complete view for an order that does not exist."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "js", "app.js"), encoding="utf-8").read()
    assert "rejectOrder" in src
    assert "data-ck-order-error" in src
    # the old swallow-everything catch is gone
    assert ".catch(() => {});\n      });\n    }" not in src
