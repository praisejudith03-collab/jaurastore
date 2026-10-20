"""The public catalogue never truncates and never drops a publishable row.

The report: a product saved from the admin portal shows up on the order form
but not on the storefront grid. Two halves have to be true for that to happen:

  * the SERVER must publish every row that is not hidden, whatever its
    category, creation date or price - no page limit, no payload cap;
  * the GRID must paint every row it receives (tests/test_storefront_grid_visibility.py
    proves that half in a real DOM).

This file pins the server half, including the failure mode a 1000-row
PostgREST answer would cause: the Supabase walk is paged and count-checked,
and the public routes are never paged at all. ``?watchdog_page=1`` remains the
only way to ask for a bounded page, and it is opt-in.

Run with:  python3 -m pytest tests/test_catalog_visibility.py -q
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"


# --------------------------------------------------------------- fake PostgREST
class _Query:
    def __init__(self, rows, calls):
        self._rows, self._calls = rows, calls
        self._count = None
        self._start, self._end = 0, -1

    def select(self, _cols, count=None):
        self._count = count
        return self

    def order(self, _col, **_kw):
        return self

    def range(self, start, end):
        self._start, self._end = start, end
        return self

    def eq(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._start, self._end = 0, int(n) - 1
        return self

    def execute(self):
        self._calls.append((self._start, self._end))
        res = type("Res", (), {"data": self._rows[self._start:self._end + 1]})()
        if self._count == "exact":
            res.count = len(self._rows)
        return res


class _Table:
    def __init__(self, rows, calls):
        # what PostgREST `.order("id")` returns
        self._rows = sorted(rows, key=lambda r: str(r.get("id") or ""))
        self._calls = calls

    def select(self, cols, count=None):
        return _Query(self._rows, self._calls).select(cols, count)


class _Client:
    def __init__(self, rows):
        self.calls = []
        self._rows = rows

    def table(self, _name):
        return _Table(self._rows, self.calls)


def _row(pid, **over):
    row = {
        "id": pid,
        "sku": "SKU-" + pid.upper(),
        "slug": "slug-" + pid.lower(),
        "name": "Piece " + pid,
        "category": "household",
        "priceNgn": 5000,
        "priceCfa": 2200,
        "image": "images/products/3-in-1-laptop-bag.jpg",
        "stock": 4,
        "online": True,
        "source": "admin",
        "updated_at": "2026-10-20T08:00:00+00:00",
    }
    row.update(over)
    return row


def _install(monkeypatch, rows, page_size=100):
    """Point the REAL read path at a fake products table.

    The bundled seed and the local override file are emptied for the duration
    of the test: these cases are about what the SUPABASE read path publishes,
    and a 257-row seed union would only obscure the counts.
    """
    import supabase_store
    import catalog as catalog_mod
    monkeypatch.setattr(catalog_mod, "_seed_products", lambda: [])
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [], "deleted": [], "updatedAt": "", "updatedBy": ""})
    fake = _Client(rows)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    # A small page forces the paged walk: no row may fall through a window,
    # and the count check must notice a server-side row cap.
    monkeypatch.setattr(supabase_store, "PAGE_SIZE", page_size)
    # The tombstone ledgers live in other tables; this fake only models the
    # products table, so both reads answer "nothing deleted".
    monkeypatch.setattr(supabase_store, "load_deleted_ids", lambda: [])
    monkeypatch.setattr(supabase_store, "load_hard_deleted_ids", lambda: [])
    return fake


@pytest.fixture(scope="module")
def app():
    import app as appmod
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    from db import init_db
    init_db()
    with app.test_client() as c:
        yield c


def _login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def _ids(body):
    return [str(p.get("id") or "") for p in body.get("products") or []]


# ------------------------------------------------------------------- the tests
def test_a_thousand_product_table_is_served_whole(monkeypatch, client):
    """No 1000-row cap anywhere: the shop sees every publishable row.

    This is the shape of the production incident the watchdog logged
    ("public catalogue: 282 products, Supabase: 286 online"): a row that
    exists in the table must never be missing from /api/products.
    """
    rows = [_row(f"bulk-{n:04d}") for n in range(1, 1206)]
    _install(monkeypatch, rows)
    for path in ("/api/products", "/api/catalog"):
        body = client.get(path).get_json()
        ids = _ids(body)
        assert len(rows) == 1205
        assert len(ids) == 1205, f"{path} truncated: {len(ids)} of 1205"
        assert len(set(ids)) == 1205, f"{path} duplicated a row"
        assert "pagination" not in body, f"{path} must never paginate on its own"
        assert body["meta"]["publishedCount"] == 1205
        assert body["meta"]["count"] == 1205
        assert body["meta"]["hiddenCount"] == 0


def test_a_new_product_with_a_brand_new_category_is_published(monkeypatch, client):
    """Category assignment, creation date and price must not gate publication."""
    rows = [
        _row("bulk-0001"),
        # saved a moment ago, in a category the shop has never seen
        _row("jau-today", category="brandnew-cat",
             created_at="2026-10-20T13:59:00Z"),
        # no category at all, and no price entered yet
        _row("jau-nocat", category="", priceNgn=0, priceCfa=0,
             created_at=""),
        # nothing but a legacy seed-style date field
        _row("jau-dated", updatedAt="2026-10-20T12:00:00Z"),
    ]
    _install(monkeypatch, rows)
    for path in ("/api/products", "/api/catalog"):
        body = client.get(path).get_json()
        ids = set(_ids(body))
        for want in ("jau-today", "jau-nocat", "jau-dated"):
            assert want in ids, f"{path} dropped {want}"
        by_id = {p["id"]: p for p in body["products"]}
        assert by_id["jau-today"]["category"] == "brandnew-cat"
        assert by_id["jau-nocat"]["priceNgn"] == 0


def test_hidden_rows_are_the_only_ones_withheld(monkeypatch, client):
    rows = [_row("bulk-0001"), _row("jau-hidden", online=False),
            _row("jau-source-deleted", source="deleted")]
    _install(monkeypatch, rows)
    body = client.get("/api/catalog").get_json()
    ids = set(_ids(body))
    assert ids == {"bulk-0001"}, ids
    assert body["meta"]["publishedCount"] == 1
    assert body["meta"]["count"] == 2          # the live row + the hidden row


def test_bounded_pages_are_opt_in_and_cover_everything(monkeypatch, client):
    rows = [_row(f"bulk-{n:04d}") for n in range(1, 61)]
    _install(monkeypatch, rows)
    whole = client.get("/api/catalog").get_json()
    assert "pagination" not in whole
    assert len(whole["products"]) == 60

    seen = set()
    offset = 0
    for _ in range(10):
        page = client.get(f"/api/catalog?watchdog_page=1&limit=7&offset={offset}").get_json()
        page_ids = _ids(page)
        seen.update(page_ids)
        if not page["pagination"]["hasMore"]:
            break
        offset = page["pagination"]["nextOffset"]
    assert seen == {p["id"] for p in rows}, "the watchdog pages lost a product"
    assert len(whole["products"]) == len(seen)


def test_admin_audit_traces_every_stage(monkeypatch, client):
    """The owner can see exactly where a product leaves the pipeline."""
    rows = [
        _row("bulk-0001"),
        _row("jau-hidden", online=False),
        _row("jau-nocat", category="", priceNgn=0, priceCfa=0,
             image="images/products/_placeholder.jpg"),
    ]
    _install(monkeypatch, rows)
    _login(client)
    body = client.get("/api/admin/catalog/audit").get_json()
    assert body["ok"] is True
    counts = body["counts"]
    assert counts["tableRows"] == 3
    assert counts["mergedRows"] == 3
    assert counts["onlineRows"] == 2
    assert counts["publishedRows"] == 2
    assert counts["hiddenRows"] == 1
    # The invariant that would have caught the production regression:
    assert body["missingFromPublic"] == [], body
    assert body["unexpectedInPublic"] == []
    assert "jau-hidden" in body["hidden"]
    assert "jau-nocat" in body["noCategory"]
    assert "jau-nocat" in body["noPrice"]
    assert "jau-nocat" in body["placeholderPhoto"]


def test_the_audit_is_admin_only(client):
    assert client.get("/api/admin/catalog/audit").status_code in (401, 403)


def test_the_audit_runs_on_the_real_catalogue_shape(client):
    """Against the bundled catalogue (no fake table): the invariant holds.

    The fake-table tests above prove the counting logic; this one proves the
    endpoint itself imports and runs against the real merge/serializer - the
    failure mode a syntax-only check cannot see.
    """
    _login(client)
    body = client.get("/api/admin/catalog/audit").get_json()
    assert body["ok"] is True, body
    counts = body["counts"]
    assert counts["publishedRows"] > 200, counts
    assert counts["mergedRows"] >= counts["onlineRows"] >= counts["publishedRows"]
    assert body["missingFromPublic"] == [], (
        "an online product the merge kept is missing from the public payload")
    assert body["unexpectedInPublic"] == []

    listed = client.get("/api/admin/catalog/audit?ids=1&limit=5").get_json()
    public = client.get("/api/products").get_json()
    assert set(listed["publishedIds"]) == {p["id"] for p in public["products"]}
