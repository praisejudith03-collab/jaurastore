"""Legacy wix-* ids must keep resolving to the canonical product.

A product's `id` is its primary key and is never renamed while orders,
reviews, carts and analytics still reference it. When a row is eventually
given a canonical jau-* id, the id it had before is kept in `legacyId` and
both must resolve to the same row - otherwise every bookmarked product link,
every past order email and every saved cart breaks.

These tests pin the resolution in the four places it matters: the server
catalogue index, checkout (price + stock), the Supabase row lookup, and the
storefront router.

Run with:  python3 -m pytest tests/test_legacy_id_alias.py -q
"""
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
import catalog as catalog_mod  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def _make(pid, legacy=None, price=8000, stock=10):
    rec = {"id": pid, "sku": pid.upper().replace("-", ""), "slug": pid,
           "name": "Alias Test " + pid, "category": "beauty",
           "priceNgn": price, "stock": stock, "online": True,
           # a real committed photo, or the publication policy keeps it offline
           "image": "images/products/10in1-raf-sandwich-maker.jpg"}
    if legacy:
        rec["legacyId"] = legacy
    catalog_mod.upsert(rec, "tester")
    return rec


def _find(pid):
    for p in catalog_mod.merged(include_hidden=True):
        if str(p.get("id")) == pid:
            return p
    return None


def _place(client, oid, items, total=None):
    execute("DELETE FROM rate_limits WHERE action='order'")
    # init_db() creates tables but does not clear rows, so a re-run would find
    # this order already present and /api/orders would answer duplicate=True
    # without storing anything - the confirm below would then be a no-op and
    # the test would pass or fail for the wrong reason.
    execute("DELETE FROM orders WHERE id=?", (oid,))
    body = {"id": oid, "currency": "NGN", "total": total if total is not None else 8000,
            "customer": {"name": "Alias Tester", "email": "alias@example.com",
                         "phone": "+2348012345678", "city": "Lagos",
                         "zone": "Lagos Mainland", "address": "1 Test St"},
            "items": items}
    r = client.post("/api/orders", json=body, headers={"X-CSRF-Token": csrf(client)})
    # A 200 that is really a duplicate means nothing was stored, so the caller's
    # assertions would be testing stale state. Rejections (409/400) are fine:
    # several tests expect them.
    if r.status_code == 200:
        assert not r.get_json().get("duplicate"), \
            f"{oid} already existed - the order was not actually created"
    return r


def _confirm(client, oid):
    tok = client.post("/api/admin/login",
                      json={"email": EMAIL, "password": PW}).get_json()["csrf"]
    return client.patch("/api/admin/orders/" + oid, json={"status": "confirmed"},
                        headers={"X-CSRF-Token": tok})


# ------------------------------------------------------------ the resolver
def test_legacy_id_survives_normalize_and_upsert():
    _make("jau-alias-norm", legacy="wix-alias-norm")
    row = _find("jau-alias-norm")
    assert row is not None
    assert row["legacyId"] == "wix-alias-norm"


def test_an_ordinary_edit_keeps_the_alias():
    """A price change must not silently drop legacyId and break old links."""
    _make("jau-alias-keep", legacy="wix-alias-keep", price=8000)
    catalog_mod.upsert({"id": "jau-alias-keep", "name": "Alias Test jau-alias-keep",
                        "slug": "jau-alias-keep", "priceNgn": 9999}, "tester")
    row = _find("jau-alias-keep")
    assert row["priceNgn"] == 9999
    assert row["legacyId"] == "wix-alias-keep", "the alias was dropped by an edit"


def test_legacy_id_is_none_not_empty_string():
    """The column has a partial UNIQUE index; '' would collide across rows."""
    _make("jau-alias-noalias")
    assert _find("jau-alias-noalias")["legacyId"] in (None, "")
    assert catalog_mod._clean_legacy_id("", "jau-x") is None
    assert catalog_mod._clean_legacy_id("jau-x", "jau-x") is None  # never self-referential


def test_product_index_maps_both_ids():
    _make("jau-alias-idx", legacy="wix-alias-idx")
    index = catalog_mod.product_index()
    assert index.get("jau-alias-idx") is not None
    assert index.get("wix-alias-idx") is not None
    assert index["jau-alias-idx"]["id"] == index["wix-alias-idx"]["id"]


def test_canonical_id_wins_a_legacy_collision():
    """A legacyId must never shadow a real primary key."""
    _make("wix-alias-owner")                      # a real row owns this id
    _make("jau-alias-thief", legacy="wix-alias-owner")
    index = catalog_mod.product_index()
    assert index["wix-alias-owner"]["id"] == "wix-alias-owner"


def test_resolve_product_id_normalises_legacy_to_canonical():
    _make("jau-alias-res", legacy="wix-alias-res")
    assert catalog_mod.resolve_product_id("wix-alias-res") == "jau-alias-res"
    assert catalog_mod.resolve_product_id("jau-alias-res") == "jau-alias-res"
    assert catalog_mod.resolve_product_id("does-not-exist") == ""
    assert catalog_mod.resolve_product_id("") == ""


# ------------------------------------------------------------- at checkout
def test_checkout_accepts_a_legacy_id_and_uses_the_server_price(client):
    _make("jau-alias-buy", legacy="wix-alias-buy", price=8000, stock=10)
    # the browser claims a 1 naira price and quotes the LEGACY id
    r = _place(client, "JA-ALIAS1",
               [{"id": "wix-alias-buy", "name": "Alias", "qty": 2, "price": 1}],
               total=2)
    assert r.status_code == 200, r.get_json()
    payload = one("SELECT payload FROM orders WHERE id=?", ("JA-ALIAS1",))
    assert payload is not None
    import json
    items = json.loads(payload["payload"]).get("items") or []
    assert items, "no items stored"
    # items[].price is the LINE total (unit * qty), see api._checkout_items
    line = int(items[0].get("price") or 0)
    assert line == 16000, f"expected 2 x 8000 from the server, got {line}"
    assert line != 2, "the browser's claimed price was used"
    assert items[0]["qty"] == 2
    # the row was priced, so the alias resolved to the canonical product
    assert _find("jau-alias-buy")["priceNgn"] == 8000


def test_checkout_enforces_stock_for_a_legacy_id(client):
    _make("jau-alias-stock", legacy="wix-alias-stock", price=8000, stock=2)
    r = _place(client, "JA-ALIAS2",
               [{"id": "wix-alias-stock", "name": "Alias", "qty": 5, "price": 8000}])
    assert r.status_code == 409, r.get_json()
    assert r.get_json().get("code") == "out_of_stock"
    body = r.get_json()
    assert not any(str(v).isdigit() and int(v) == 2
                   for v in [body.get("available", "x")]), \
        "the response leaked a numerical stock count"


def test_confirming_a_legacy_id_order_decrements_the_canonical_row(client):
    _make("jau-alias-dec", legacy="wix-alias-dec", price=8000, stock=10)
    r = _place(client, "JA-ALIAS3",
               [{"id": "wix-alias-dec", "name": "Alias", "qty": 3, "price": 8000}])
    assert r.status_code == 200, r.get_json()
    assert _confirm(client, "JA-ALIAS3").status_code == 200
    assert _find("jau-alias-dec")["stock"] == 7


# -------------------------------------------------------------- supabase
def test_supabase_product_by_id_falls_back_to_legacy_id(monkeypatch):
    import supabase_store

    class Q:
        def __init__(self, rows, log, col):
            self.rows, self.log, self.col = rows, log, col

        def select(self, *a):
            return self

        def eq(self, col, val):
            self.log.append((col, val))
            self.col[0] = col
            return self

        def limit(self, n):
            return self

        def execute(self):
            col = self.col[0]
            hit = [r for r in self.rows if str(r.get(col)) == self.log[-1][1]]
            return type("R", (), {"data": hit})()

    class T:
        def __init__(self, rows, log, col):
            self.rows, self.log, self.col = rows, log, col

        def select(self, *a):
            return Q(self.rows, self.log, self.col)

    class C:
        def __init__(self, rows):
            self.rows, self.log, self.col = rows, [], [""]

        def table(self, name):
            return T(self.rows, self.log, self.col)

    rows = [{"id": "jau-sb", "legacyId": "wix-sb", "name": "SB", "priceNgn": 5,
             "priceCfa": 2, "stock_quantity": 3, "online": True}]
    c = C(rows)
    monkeypatch.setattr(supabase_store, "client", lambda: c)
    if True:
        got = supabase_store.product_by_id("wix-sb")
        assert got is not None and got["id"] == "jau-sb"
        assert ("id", "wix-sb") in c.log and ("legacyId", "wix-sb") in c.log
        # the canonical id still resolves on the first lookup
        assert supabase_store.product_by_id("jau-sb")["id"] == "jau-sb"
        assert supabase_store.product_by_id("nope") is None
        assert supabase_store.product_by_id("") is None


def test_a_missing_legacy_id_column_does_not_break_the_primary_lookup(monkeypatch):
    """An un-migrated table has no legacyId column; the id lookup already ran."""
    import supabase_store

    class Boom:
        def select(self, *a):
            return self

        def eq(self, *a):
            return self

        def limit(self, n):
            return self

        def execute(self):
            raise RuntimeError('column "legacyId" does not exist')

    class T:
        def select(self, *a):
            return Boom()

    class C:
        def table(self, name):
            return T()

    monkeypatch.setattr(supabase_store, "client", lambda: C())
    assert supabase_store.product_by_id("wix-anything") is None


# ------------------------------------------------------------------ schema
def test_schema_adds_the_legacy_id_column_and_index():
    sql = open(os.path.join(ROOT, "supabase_schema.sql"), encoding="utf-8").read()
    assert re.search(r'add column if not exists "legacyId" text', sql)
    assert 'products_legacy_id_key' in sql
    assert 'where "legacyId" is not null' in sql, \
        "the unique index must be partial, or NULLs would collide"


def test_storefront_router_honours_the_alias():
    body = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    m = re.search(r"function product\(idOrSlug\) \{(.*?)\n  \}", body, re.S)
    assert m, "product() not found in js/store.js"
    assert "legacyId" in m.group(1)
    # canonical id / slug is tried first so an alias cannot shadow a real id
    assert m.group(1).index("p.id === want") < m.group(1).index('p.legacyId')


def test_the_258_seed_wix_ids_still_resolve_unchanged():
    """The alias layer is additive: nothing that worked before may stop."""
    ids = {str(p.get("id")) for p in catalog_mod.merged(include_hidden=True)}
    assert "wix-001" in ids and "wix-258" in ids
    index = catalog_mod.product_index()
    for pid in ("wix-001", "wix-100", "wix-258"):
        assert index.get(pid, {}).get("id") == pid
