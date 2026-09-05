"""A product save must survive a narrower Supabase products table.

The incident: the hand-built products table has fewer columns than the row the
app writes, so PostgREST answered PGRST204 ("Could not find the 'compareCfa'
column of 'products' in the schema cache") and *every* product save was
rejected. Reads kept working, so the shop looked fine while the owner's save
sat in the server's local override file - invisible to the other phone and
wiped by the next deploy.

These tests hold the two halves of the fix:

* supabase_store writes whatever row the table actually accepts (drop the
  rejected column, retry), and reports the write as failed - mirrored=False -
  when the table rejects a column the product cannot exist without, or when it
  fails for any other reason.
* the admin save then really goes live: the product reaches the fake Supabase
  table and /api/catalog-level reads see it.

Run with:  python3 -m pytest tests -q
No real Supabase is needed: supabase_store.client() is monkeypatched to an
in-memory fake that enforces a column allowlist, exactly like a table with a
subset of the columns.
"""
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

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import supabase_store  # noqa: E402
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


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


@pytest.fixture(autouse=True)
def _supabase_configured(monkeypatch):
    """Pretend Supabase is configured, so a save genuinely has to mirror.

    Without this the test suite runs with no SUPABASE_URL / service key, and
    upsert_products() short-circuits to "nothing to mirror" - which would make
    every assertion below pass for the wrong reason.
    """
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)


@pytest.fixture()
def iso_catalog():
    """Run against an empty catalogue override file, then put it back.

    Every test file shares /tmp/jaura_test_catalog.json. A row left there by an
    earlier test would change what merged() returns (and a stray .bak would be
    restored instead), so this file starts from a clean, isolated catalogue.
    """
    path = catalog_mod._norm_filename(catalog_mod.CATALOG_FILE)
    bak = path + ".bak"
    saved = {p: (open(p, "rb").read() if os.path.isfile(p) else None)
             for p in (path, bak)}
    catalog_mod._write_overrides(
        {"products": [], "deleted": [], "updatedAt": "", "updatedBy": ""}, path)
    try:
        yield path
    finally:
        for p, raw in saved.items():
            try:
                if raw is None:
                    if os.path.isfile(p):
                        os.remove(p)
                else:
                    with open(p, "wb") as fh:
                        fh.write(raw)
            except OSError:
                pass


# ------------------------------------------------------------------ fakes
class _FakeTable:
    """In-memory stand-in for one PostgREST table (upsert / select / update).

    The rows live on the client that owns the table (self._o.tables[name]) so a
    subclass can consult the client's own settings while validating a write -
    see _StrictTable.
    """

    def __init__(self, owner, name):
        self._o = owner
        self._name = name
        self._filter = None
        self._in = None
        self._update = None
        self._window = None

    def upsert(self, rows):
        store = self._o.tables.setdefault(self._name, [])
        for r in rows or []:
            store[:] = [x for x in store if x.get("id") != (r or {}).get("id")]
            store.append(dict(r or {}))
        return self

    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n=None):
        return self

    def range(self, lo, hi):
        self._window = (lo, hi)
        return self

    def update(self, payload):
        self._update = payload or {}
        return self

    def eq(self, key, val):
        self._filter = (key, val)
        return self

    def in_(self, key, vals):
        self._in = (key, [str(v) for v in (vals or [])])
        return self

    def execute(self):
        store = self._o.tables.setdefault(self._name, [])
        rows = list(store)
        if self._filter:
            k, v = self._filter
            rows = [r for r in rows if r.get(k) == v]
        if self._in:
            k, vals = self._in
            rows = [r for r in rows if str(r.get(k)) in vals]
        if self._update is not None:
            for r in rows:
                r.update(self._update)
        count = len(rows)
        if self._window:
            lo, hi = self._window
            rows = rows[lo:hi + 1]
        return {"data": rows, "count": count}


class _FakeSupabase:
    """A reachable Supabase whose tables are plain in-memory lists."""

    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _FakeTable(self, name)


class _BoomSupabase:
    """A configured but broken Supabase: every table access raises."""

    def table(self, name):
        raise RuntimeError(f"connection reset while writing {name}")


# The exact shape migrate_supabase.py creates in Supabase. The app writes more
# keys than this (images, optionStock, usesPlaceholder, ...) and production
# historically lacked compareCfa - that mismatch is what killed every save.
MIGRATE_COLUMNS = {"id", "sku", "slug", "name", "nameFr", "category",
                   "priceCfa", "compareCfa", "priceNgn", "compareNgn",
                   "image", "placeholderImage", "description", "stock",
                   "badge", "featured", "online", "colors", "options",
                   "source", "updated_at"}


class _StrictTable(_FakeTable):
    """Reject a row carrying a column the table does not have, like PostgREST.

    The error text is the production one, word for word, including the
    PGRST204 code: the resilient writer keys off it.
    """

    def upsert(self, rows):
        for r in rows or []:
            for key in (r or {}):
                if key not in self._o.allowed:
                    raise Exception(
                        f"Could not find the '{key}' column of 'products' "
                        "in the schema cache (PGRST204)")
        return super().upsert(rows)


class _StrictSupabase(_FakeSupabase):
    """A products table narrowed to `allowed`; every other table is permissive."""

    def __init__(self, allowed):
        super().__init__()
        self.allowed = set(allowed)

    def table(self, name):
        if name == "products":
            return _StrictTable(self, name)
        return super().table(name)


def _full_row(pid="jau-narrow-1"):
    return {"id": pid, "sku": "JAU-1", "slug": pid, "name": f"Vis {pid}",
            "nameFr": "", "category": "beauty", "priceCfa": 4400,
            "compareCfa": None, "priceNgn": 10000, "compareNgn": None,
            "image": "", "images": [], "description": "", "stock": 7,
            "badge": "", "featured": False, "online": True,
            "colors": [], "options": [], "optionStock": {}}


def _row(pid="jau-visible-1", **over):
    """A minimal, valid admin payload - what the admin portal posts for a new
    product. catalog.normalize() expands it into the full row the mirror sends."""
    row = {"id": pid, "sku": "JAU-VIS", "slug": pid, "name": f"Vis {pid}",
           "category": "beauty", "priceCfa": 4400, "priceNgn": 10000,
           "stock": 7, "online": True}
    row.update(over)
    return row


# ------------------------------------------------------------------- writes
def test_upsert_adapts_to_narrow_table(monkeypatch):
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    assert supabase_store.upsert_products([_full_row()]) is True
    stored = [x for x in fake.tables["products"]
              if x.get("id") == "jau-narrow-1"][0]
    assert stored["stock"] == 7 and stored["priceNgn"] == 10000
    assert stored["name"] == "Vis jau-narrow-1"
    assert "compareCfa" not in stored
    assert "images" not in stored and "optionStock" not in stored


def test_upsert_refuses_critical_column_drop(monkeypatch):
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa", "stock"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    assert supabase_store.upsert_products([_full_row()]) is False
    assert fake.tables.get("products", []) == []


def test_upsert_false_on_other_errors(monkeypatch):
    monkeypatch.setattr(supabase_store, "client", lambda: _BoomSupabase())
    assert supabase_store.upsert_products([_full_row()]) is False


def test_bulk_replace_adapts_to_narrow_table(monkeypatch):
    """The CSV / bulk import mirrors through the same resilient path.

    A rejected column must not cost the whole catalogue: the tombstone pass
    still runs and every row lands with the columns the table has. The caller's
    own dicts stay untouched (a retry edits a copy, not the product the
    request will hand back).
    """
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    first = _full_row("jau-narrow-3")
    supabase_store.replace_all_products([first, _full_row("jau-narrow-4")])
    stored = fake.tables["products"]
    assert {"jau-narrow-3", "jau-narrow-4"} <= {x.get("id") for x in stored}
    assert all("compareCfa" not in x and x["stock"] == 7 for x in stored)
    assert all(x["source"] == "admin" for x in stored)
    assert "compareCfa" in first and first["stock"] == 7


# ---------------------------------------------------------------- end to end
def test_save_goes_live_on_narrow_table(client, iso_catalog, monkeypatch):
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    tok = login(client)
    r = client.post("/api/admin/products",
                    json={"product": _row("jau-narrow-2")},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert r.get_json()["mirrored"] is True
    ids = {str(p.get("id")) for p in catalog_mod.merged(include_hidden=True)}
    assert "jau-narrow-2" in ids
