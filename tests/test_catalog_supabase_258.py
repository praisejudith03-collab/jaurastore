"""Regression: every online wix-* product must reach GET /api/catalog.

The production defect this pins down: the storefront showed fewer products
than Supabase held (241 rendered against 258 online ``wix-*`` rows), and the
browser-side dedupe in js/store.js collapsed rows on a shared slug or sku
ALONE - the same bug the server's ``catalog._dedupe_products`` had already
fixed (see tests/test_dedupe_scope.py). These tests rebuild the production
shape - the 276-row Supabase products table (258 online wix-* rows + 18
offline non-wix rows: the 17 jau-* test fixtures and the one offline review
row) served through the REAL deployed read path
(supabase_store.products_table_rows -> catalog.merged -> /api/catalog) - and
prove the public catalogue returns all 258 wix-* ids with every id, slug and
sku preserved.

The 17 ``jau-*`` rows are the suite's own products. The owner deleted them
from the shop and they came back, so they are now excluded on every read -
the public answer is 258 and the admin answer is 259 (the online wix-* rows
plus the one offline review row), even though the table still lists them.

Run with:  python3 -m pytest tests/test_catalog_supabase_258.py -q
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The 17 pytest fixtures that live in the production table (PUBLICATION_AUDIT.md
# section 4) plus the one offline/review row (section 3). All online=false:
# they must never leak into the public catalogue - and, since the owner deleted
# them and they came back, they must not reach the admin answer either. The
# one non-fixture offline row (jau-mtot3318) still counts there.
EMAIL = "jaurastore@gmail.com"

OFFLINE_NON_WIX_ROWS = [
    ("jau-mirror-fail", "X"), ("jau-mirror-ok", "Y"), ("jau-mirror-post", "Mirror Post"),
    ("jau-stock-a", "Stock Test jau-stock-a"), ("jau-stock-b", "Stock Test jau-stock-b"),
    ("jau-stock-dcf", "Stock Test jau-stock-dcf"), ("jau-stock-dec", "Stock Test jau-stock-dec"),
    ("jau-stock-del", "Stock Test jau-stock-del"), ("jau-stock-dnp", "Stock Test jau-stock-dnp"),
    ("jau-stock-dpn", "Stock Test jau-stock-dpn"), ("jau-stock-em2", "Stock Test jau-stock-em2"),
    ("jau-stock-eml", "Stock Test jau-stock-eml"), ("jau-stock-idem", "Stock Test jau-stock-idem"),
    ("jau-stock-opr", "Stock Test jau-stock-opr"), ("jau-stock-opt", "Stock Test jau-stock-opt"),
    ("jau-stock-pay", "Stock Test jau-stock-pay"), ("jau-stock-rop", "Stock Test jau-stock-rop"),
    ("jau-mtot3318", "Tote bag"),
]


# --------------------------------------------------------------- fake PostgREST
class _FakeSupabaseQuery:
    """Chainable stand-in for the supabase-py select/order/range builder."""

    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls
        self._count = None
        self._start = 0
        self._end = -1

    def select(self, _cols, count=None):
        self._count = count
        return self

    def order(self, _col, **_kw):
        return self

    def range(self, start, end):
        self._start, self._end = start, end
        return self

    def execute(self):
        self._calls.append((self._start, self._end))
        window = self._rows[self._start:self._end + 1]
        res = type("Res", (), {"data": window})()
        if self._count == "exact":
            res.count = len(self._rows)
        return res


class _FakeSupabaseTable:
    def __init__(self, rows, calls):
        # sorted the way a PostgREST `.order("id")` would return them
        self._rows = sorted(rows, key=lambda r: str(r.get("id") or ""))
        self._calls = calls

    def select(self, cols, count=None):
        return _FakeSupabaseQuery(self._rows, self._calls).select(cols, count)


class _FakeSupabaseClient:
    def __init__(self, rows):
        self.calls = []
        self._rows = rows

    def table(self, _name):
        return _FakeSupabaseTable(self._rows, self.calls)


# ------------------------------------------------------- production table shape
def _db_row(p, online=True):
    """One row exactly the way the production products table holds it.

    The reviewed customer-catalogue import wrote the camelCase columns and
    left the table's dead legacy snake_case columns (name_fr, price_cfa,
    price_ngn, compare_cfa, compare_ngn, option_stock) null - which is why
    the live /api/catalog answer carries both spellings.
    """
    return {
        "id": p["id"], "sku": p.get("sku"), "slug": p.get("slug"),
        "name": p["name"], "name_fr": None, "nameFr": p.get("nameFr"),
        "category": p.get("category"),
        "price_cfa": None, "compare_cfa": None, "price_ngn": None, "compare_ngn": None,
        "priceCfa": p.get("priceCfa"), "compareCfa": p.get("compareCfa"),
        "priceNgn": p.get("priceNgn"), "compareNgn": p.get("compareNgn"),
        "image": p.get("image"), "image_url": "", "images": p.get("images") or [],
        "description": p.get("description"),
        "stock": p.get("stock", 24), "stock_quantity": p.get("stock", 24),
        "badge": p.get("badge"), "featured": p.get("featured"), "online": online,
        "colors": p.get("colors"), "options": p.get("options"), "option_stock": None,
        "placeholderImage": "images/products/_placeholder.jpg",
        "usesPlaceholder": False,
        "source": "admin", "updated_at": "2026-09-09T07:57:14+00:00",
        "legacyId": None,
    }


def _production_table():
    """276 rows: 258 online wix-* customer rows + 18 offline non-wix rows."""
    seed = json.load(open(os.path.join(ROOT, "data", "seed.json"), encoding="utf-8"))
    wix = [p for p in seed if str(p.get("id", "")).startswith("wix-")]
    assert len(wix) == 258, f"the approved customer catalogue must hold 258 wix-* rows, found {len(wix)}"
    rows = [_db_row(p, online=True) for p in wix]
    for pid, name in OFFLINE_NON_WIX_ROWS:
        rows.append(_db_row({"id": pid, "sku": f"FIXTURE-{pid}", "slug": pid,
                             "name": name, "category": "household",
                             "priceNgn": 1000, "priceCfa": 440,
                             "image": "images/products/_placeholder.jpg",
                             "stock": 3}, online=False))
    assert len(rows) == 276
    return rows, [p["id"] for p in wix]


def _install_table(monkeypatch, rows, page_size=100):
    """Point the REAL read path at the fake 276-row production table."""
    import supabase_store
    fake = _FakeSupabaseClient(rows)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    # a small page forces the paged walk: no row may fall through a window
    monkeypatch.setattr(supabase_store, "PAGE_SIZE", page_size)
    return fake


def _production_overrides():
    """The production admin-override mirror the repo keeps in data/catalog.json."""
    try:
        with open(os.path.join(ROOT, "data", "catalog.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("products"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"products": [], "deleted": [], "updatedAt": "", "updatedBy": ""}


# ---------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def app():
    import app as appmod
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    from db import init_db
    with app.test_client() as c:
        yield c


# -------------------------------------------------------------------------- tests
def test_api_catalog_returns_all_258_online_wix_products(monkeypatch, client):
    """The deployed public catalogue: all 258 online wix-* rows, nothing lost.

    Supabase stays the source of truth: the 276-row table is read through
    supabase_store.products_table_rows() (paged, tombstones filtered,
    canonicalised) and merged by catalog.merged() exactly as in production.
    """
    import catalog as catalog_mod
    rows, wix_ids = _production_table()
    _install_table(monkeypatch, rows)
    monkeypatch.setattr(catalog_mod, "overrides", _production_overrides)

    r = client.get("/api/catalog")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True

    products = body["products"]
    assert len(products) == 258, \
        f"the public catalogue must serve all 258 online wix-* products, got {len(products)}"
    ids = [str(p.get("id")) for p in products]
    assert len(ids) == len(set(ids)), "a product id appears twice in /api/catalog"
    assert set(ids) == set(wix_ids), (
        "product ids were lost or swapped on the way to the browser: missing " +
        str(sorted(set(wix_ids) - set(ids))) + ", unexpected " +
        str(sorted(set(ids) - set(wix_ids))))
    # every id preserved exactly once, nothing renamed, nothing hidden
    assert "wix-001" in ids and "wix-258" in ids
    # online filtering excluded only the offline non-wix rows
    offline = {pid for pid, _name in OFFLINE_NON_WIX_ROWS}
    assert not (set(ids) & offline), "an offline fixture/review row leaked into the public catalogue"
    assert all(p.get("online") is not False for p in products), \
        "an online wix-* row reached the browser with online=false"
    # meta.count reflects the live catalogue: the 258 online wix-* rows plus
    # the one offline non-fixture row. The 17 test fixtures the table still
    # lists are not part of it any more.
    assert body["meta"]["count"] == 259, \
        f"meta.count must reflect the live catalogue (259), got {body['meta']['count']}"
    fixture_ids = {pid for pid, _name in OFFLINE_NON_WIX_ROWS if pid.startswith("jau-") and pid != "jau-mtot3318"}
    assert not (set(ids) & fixture_ids), \
        "a deleted test product is back in the public catalogue"
    # slug + sku survive the trip: dedupe must never need them to drop a row
    by_id = {p["id"]: p for p in products}
    for wix_id, src in zip(wix_ids, [p for p in json.load(
            open(os.path.join(ROOT, "data", "seed.json"), encoding="utf-8"))
            if str(p.get("id", "")).startswith("wix-")]):
        assert by_id[wix_id].get("slug") == src.get("slug"), f"{wix_id} lost its slug"
        assert by_id[wix_id].get("sku") == src.get("sku"), f"{wix_id} lost its sku"
    # Supabase provenance: the answer carries the products-table columns
    assert "name_fr" in products[0] and "updated_at" in products[0]


def test_api_catalog_never_drops_products_over_a_shared_slug_or_sku(monkeypatch, client):
    """A slug or sku clash between DIFFERENT products hides neither of them.

    This is the exact failure class behind "Supabase says 258, the storefront
    shows 241": rows keyed by slug/sku alone silently vanished. Reconciled
    rows are id-only; only a name-confirmed clash (a re-created product)
    collapses, and it collapses to a single copy - never to zero.
    """
    import catalog as catalog_mod
    rows, wix_ids = _production_table()
    by_id = {r["id"]: r for r in rows}
    # two DIFFERENT products that happen to share a slug ...
    by_id["wix-002"]["slug"] = by_id["wix-001"]["slug"]
    # ... and two that happen to share a sku
    by_id["wix-004"]["sku"] = by_id["wix-003"]["sku"]
    # a re-created product: fresh id, SAME name + slug + sku -> renders once
    reborn = dict(by_id["wix-005"])
    reborn["id"] = "wix-005-recreated"
    rows.append(reborn)
    _install_table(monkeypatch, rows)
    monkeypatch.setattr(catalog_mod, "overrides", _production_overrides)

    body = client.get("/api/catalog").get_json()
    ids = [str(p.get("id")) for p in body["products"]]

    assert "wix-001" in ids and "wix-002" in ids, \
        "a shared slug hid a DIFFERENT product on /api/catalog"
    assert "wix-003" in ids and "wix-004" in ids, \
        "a shared sku hid a DIFFERENT product on /api/catalog"
    assert "wix-005-recreated" not in ids, "a re-created product rendered twice"
    assert "wix-005" in ids, "the original copy of a re-created product vanished"
    # 258 originals + 1 re-creation collapsing to one copy = still 258 unique
    assert len(ids) == 258, f"expected the 258 online products intact, got {len(ids)}"


def test_storefront_fallback_seed_is_not_served_when_supabase_answers(client, monkeypatch):
    """When Supabase answers, its rows ARE the catalogue - the local seed only
    fills ids the table lacks, so a live table can never be shadowed by the
    bundled products-data.js snapshot."""
    import catalog as catalog_mod
    rows, wix_ids = _production_table()
    for r in rows:                      # the live table disagrees with the seed
        if r["id"] == "wix-001":
            r["name"] = "10000 mah power bank (live Supabase copy)"
    _install_table(monkeypatch, rows)
    monkeypatch.setattr(catalog_mod, "overrides", _production_overrides)

    body = client.get("/api/catalog").get_json()
    first = next(p for p in body["products"] if p["id"] == "wix-001")
    assert "live Supabase copy" in first["name"], \
        "the Supabase row did not win over the local seed copy"
    assert len(body["products"]) == 258


def _login_admin(client, monkeypatch):
    """Sign the sole admin in (env-backed master password) and return the
    session-holding client."""
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", "AdminMaster2026")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "AdminBootstrap7")
    r = client.post("/api/admin/login", json={"password": "AdminMaster2026"})
    assert r.status_code == 200, r.data
    return client


def test_admin_catalog_all_returns_exactly_what_is_saved(client, monkeypatch):
    """The admin portal reads GET /api/catalog?all=1: EVERY saved row (276 -
    online AND offline), with the stock numbers the public answer strips, and
    the count equals the saved total. This is what makes the admin product
    count match the database on every device, while the public storefront
    (all phones) keeps showing the online rows (258)."""
    import catalog as catalog_mod
    rows, wix_ids = _production_table()
    _install_table(monkeypatch, rows)
    monkeypatch.setattr(catalog_mod, "overrides", _production_overrides)
    offline = {pid for pid, _ in OFFLINE_NON_WIX_ROWS}

    # first, as an anonymous visitor: the public answer is the online-only
    # projection - 258 products, no stock keys, no offline rows
    pub = client.get("/api/catalog").get_json()
    assert len(pub["products"]) == 258
    pub_row = next(p for p in pub["products"] if p["id"] == "wix-001")
    assert "stock" not in pub_row and "stock_quantity" not in pub_row
    assert all(p["id"] not in offline for p in pub["products"])

    # then, signed in as the admin: ?all=1 lists every LIVE row - the 258
    # online wix-* rows plus the offline review row = 259. The 17 test
    # fixtures stay out of the admin answer too: the owner deleted them for
    # good, and a delete that only hides a row from the storefront while the
    # portal still lists it is how they kept coming back.
    _login_admin(client, monkeypatch)
    body = client.get("/api/catalog?all=1").get_json()
    ids = [str(p["id"]) for p in body["products"]]
    fixtures = {pid for pid, _name in OFFLINE_NON_WIX_ROWS
                if pid.startswith("jau-") and pid != "jau-mtot3318"}
    assert len(ids) == 259, \
        f"the admin catalogue must list the live rows (259), got {len(ids)}"
    assert len(ids) == len(set(ids))
    assert set(ids) == (set(wix_ids) | offline) - fixtures
    assert not (set(ids) & fixtures), "a deleted test product came back"
    assert any(p["id"] in offline and p.get("online") is False
               for p in body["products"]), "offline rows must be visible to the admin"
    wix_row = next(p for p in body["products"] if p["id"] == "wix-001")
    assert "stock" in wix_row or "stock_quantity" in wix_row, \
        "the admin answer must keep the stock numbers (the public one strips them)"
    assert body["meta"]["count"] == 259
