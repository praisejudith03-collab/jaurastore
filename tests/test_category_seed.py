"""The live categories table must keep every shipped default category.

`perfume` is a shipped default (js/store.js DEFAULT_CATEGORIES +
data/categories.json) but no product in the live catalogue carries the
category, so a rewrite of the production Supabase ``categories`` table
dropped it - and with it the shop pill, the categories-page tile and the
header-menu entry. The owner cannot get it back without re-entering the
French name and picking a cover, which is exactly the drift this seed fixes.

category_seed.seed_missing_default_categories() runs once per deployment
family from the production/staging boot path (app.py), re-inserts any
shipped default the table lost, and marks the run durably in Supabase
growth_settings so it NEVER fights a later deliberate delete by the owner.

Run with:  python3 -m pytest tests/test_category_seed.py -q
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

import pytest  # noqa: E402

import category_seed  # noqa: E402
import supabase_store  # noqa: E402
from test_categories_resilient import _blank_cat_shape  # noqa: E402,F401


class _Result:
    def __init__(self, data):
        self.data = data or []


class _CategoriesTable:
    """The live categories table: a read chain (select/order/range) plus the
    upsert / prune-delete chains, storing rows in the real snake_case shape."""

    def __init__(self, store):
        self._store = store
        self.attempts = []
        self.not_ = self
        self._pending = None
        self._keep = None

    def select(self, _cols):
        return self

    def order(self, _col):
        return self

    def range(self, _a, _b):
        return self

    def upsert(self, rows, **_kw):
        self._pending = [dict(r) for r in (rows or []) if r]
        return self

    def delete(self, **_kw):
        self._keep = None
        return self

    def in_(self, _col, keep):
        self._keep = set(keep or [])
        return self

    def execute(self):
        if self._pending is not None:
            rows, self._pending = self._pending, None
            self.attempts.append([dict(r) for r in rows])
            for row in rows:
                self._store[row["id"]] = dict(row)
            return _Result([dict(r) for r in rows])
        if self._keep is None:                     # the read chain
            return _Result([dict(r) for r in
                            sorted(self._store.values(),
                                   key=lambda r: str(r.get("id")))])
        for pid in [pid for pid in list(self._store) if pid not in self._keep]:
            del self._store[pid]
        return _Result([])


class _GrowthSettings:
    """growth_settings as the seed uses it: full-map select, keyed select and
    plain upserts."""

    def __init__(self, store):
        self._store = store

    def table(self, _name):
        return self

    def select(self, _cols):
        return self

    def eq(self, _col, value):
        self._eq = value
        return self

    def limit(self, _n):
        return self

    def upsert(self, rows, **_kw):
        for row in rows or []:
            self._store[str(row.get("key"))] = str(row.get("value") or "")
        return self

    def execute(self):
        if getattr(self, "_eq", None) is not None:
            value = self._store.get(self._eq)
            self._eq = None
            return _Result([{"value": value}] if value is not None else [])
        return _Result([{"key": k, "value": v}
                        for k, v in sorted(self._store.items())])


class _Client:
    def __init__(self, store, table):
        self._store = store
        self._cats = table

    def table(self, name):
        if name == "categories":
            return self._cats
        if name == "growth_settings":
            return _GrowthSettings(self._store)
        raise AssertionError(f"unexpected table {name!r} in fake")


@pytest.fixture()
def shop(monkeypatch, _blank_cat_shape):
    """A live shop: a categories table with the owner's rows and a
    growth_settings details row that matches, plus a clean seed marker."""
    store = {"categories": {}, "growth": {}}
    store["categories"]["beauty"] = {
        "id": "beauty", "name": "Beauty & skincare", "name_fr": "Beauté & soins",
        "image_url": "https://example.supabase.co/beauty.jpg",
        "hidden": False, "updated_at": "2026-09-10T00:00:00Z"}
    store["categories"]["bags"] = {
        "id": "bags", "name": "Bags", "name_fr": "Sacs",
        "image_url": "https://example.supabase.co/bags.jpg",
        "hidden": False, "updated_at": "2026-09-10T00:00:00Z"}
    details = [
        {"id": "beauty", "name": "Beauty & skincare", "nameFr": "Beauté & soins",
         "image": "https://example.supabase.co/beauty.jpg", "hidden": False,
         "order": 0},
        {"id": "bags", "name": "Bags", "nameFr": "Sacs",
         "image": "https://example.supabase.co/bags.jpg", "hidden": False,
         "order": 1},
    ]
    store["growth"][supabase_store.CATEGORIES_KEY] = json.dumps(details)
    table = _CategoriesTable(store["categories"])
    client = _Client(store["growth"], table)
    monkeypatch.setattr(supabase_store, "client", lambda: client)
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    # A standalone run of this file has not created the app yet, so the
    # SQLite fallback table may not exist; create the schema, then clear any
    # marker an earlier run left behind (never inherit a marker).
    from db import execute
    import db
    db.init_db()
    execute("DELETE FROM growth_settings WHERE key=?", (category_seed.MARKER_KEY,))
    yield store, table
    execute("DELETE FROM growth_settings WHERE key=?", (category_seed.MARKER_KEY,))

def _details(store):
    return json.loads(store["growth"][supabase_store.CATEGORIES_KEY])


def test_seeds_a_missing_default_and_marks_the_run(shop):
    store, table = shop
    status, ids = category_seed.seed_missing_default_categories()
    assert status == "seeded" and ids == ["perfume"]
    # the row landed on the live table with the shipped names + cover
    row = store["categories"]["perfume"]
    assert row["name"] == "Perfume"
    assert row["name_fr"] == "Parfum"
    assert row["image_url"] == "images/categories/beauty.jpg"
    assert row["hidden"] is False
    # the owner's own rows survived the full-set write untouched
    assert store["categories"]["beauty"]["name"] == "Beauty & skincare"
    assert store["categories"]["bags"]["name_fr"] == "Sacs"
    # the growth_settings details carry it too (order/nameFr/cover source)
    details = _details(store)
    perfume = next(c for c in details if c["id"] == "perfume")
    assert perfume["nameFr"] == "Parfum"
    assert perfume["order"] == 2                    # appended after the owner's rows
    # the marker is set: a second run never writes again
    assert category_seed.MARKER_KEY in store["growth"]
    assert category_seed.seed_missing_default_categories() == ("already-applied", [])
    assert len(table.attempts) == 1                 # exactly one upsert, ever


def test_default_already_live_is_nothing_to_do(shop):
    store, table = shop
    store["categories"]["perfume"] = {
        "id": "perfume", "name": "Perfume", "name_fr": "Parfum",
        "image_url": "images/categories/beauty.jpg", "hidden": False}
    status, ids = category_seed.seed_missing_default_categories()
    assert (status, ids) == ("nothing-to-do", [])
    assert table.attempts == []                     # no write happened
    assert category_seed.MARKER_KEY in store["growth"]


def test_owner_hidden_default_is_never_forced_back(shop):
    store, table = shop
    details = _details(store)
    details.append({"id": "perfume", "name": "Perfume", "nameFr": "Parfum",
                    "image": "images/categories/beauty.jpg", "hidden": True,
                    "order": 2})
    store["growth"][supabase_store.CATEGORIES_KEY] = json.dumps(details)
    status, ids = category_seed.seed_missing_default_categories()
    assert (status, ids) == ("nothing-to-do", [])
    assert "perfume" not in store["categories"]     # never re-added
    assert table.attempts == []


def test_seed_reuses_the_owners_details(shop):
    store, _table = shop
    details = _details(store)
    details.append({"id": "perfume", "name": "Perfumes & fragrances",
                    "nameFr": "Parfums & fragrances",
                    "image": "https://example.supabase.co/perfume.jpg",
                    "hidden": False, "order": 7})
    store["growth"][supabase_store.CATEGORIES_KEY] = json.dumps(details)
    status, ids = category_seed.seed_missing_default_categories()
    assert status == "seeded" and ids == ["perfume"]
    row = store["categories"]["perfume"]
    assert row["name"] == "Perfumes & fragrances"   # the owner's copy, not the default
    assert row["name_fr"] == "Parfums & fragrances"
    assert row["image_url"] == "https://example.supabase.co/perfume.jpg"


def test_failed_write_leaves_the_marker_unset(shop, monkeypatch):
    store, _table = shop
    real_save = supabase_store.save_categories_table
    monkeypatch.setattr(supabase_store, "save_categories_table",
                        lambda rows: False)
    status, ids = category_seed.seed_missing_default_categories()
    assert status == "failed" and ids == ["perfume"]
    assert category_seed.MARKER_KEY not in store["growth"]
    assert "perfume" not in store["categories"]
    # a later boot (write healthy again) retries and succeeds
    monkeypatch.setattr(supabase_store, "save_categories_table", real_save)
    assert category_seed.seed_missing_default_categories() == ("seeded", ["perfume"])


def test_unreadable_table_retries_next_boot(shop, monkeypatch):
    store, _table = shop
    monkeypatch.setattr(supabase_store, "load_categories_table",
                        lambda: None)
    assert category_seed.seed_missing_default_categories() == ("unavailable", [])
    assert category_seed.MARKER_KEY not in store["growth"]


def test_not_configured_is_a_no_op(shop, monkeypatch):
    monkeypatch.setattr(supabase_store, "enabled", lambda: False)
    assert category_seed.seed_missing_default_categories() == ("not-configured", [])


def test_production_boot_runs_the_seed():
    """The hook must stay wired into the production/staging boot path."""
    import app as appmod
    source = open(os.path.join(os.path.dirname(appmod.__file__) or ".",
                               "app.py"), encoding="utf-8").read()
    assert "seed_missing_default_categories()" in source, \
        "app.py must call the one-shot category seed on production boot"
