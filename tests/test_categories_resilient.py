"""Category saves must land on the legacy live categories table.

The production ``categories`` table predates the category editor and is
NARROWER than the row the app writes (no name_fr / image_url / hidden /
updated_at) - sometimes with extra NOT NULL columns on top. A plain upsert
used to die with PGRST204 "Could not find the 'name_fr' column", so every
Admin add/rename answered 503. The client had already written localStorage,
so the rename LOOKED saved and then "went back" on reload.

supabase_store.save_categories_table now repairs its payload against the
LIVE table, the way delivery._upsert_zone_resilient does for delivery_zones:
23502 fills the named NOT NULL column from the category being saved,
PGRST204/42703 drops the unknown column (id and name are never dropped),
22P02 walks the value through 0 -> False -> "" - bounded, and the discovered
shape is cached per worker so the next save lands first-try. The post-upsert
prune still removes ids that were deleted locally.

Run with:  python3 -m pytest tests/test_categories_resilient.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

import supabase_store  # noqa: E402


def _api_error(code, message, details=None):
    return APIError({"message": message, "code": code,
                     "details": details, "hint": None})


class _FakeResult:
    def __init__(self, data):
        self.data = data or []


class _LegacyCatTable:
    """categories as the LIVE table behaves: a schema check first (PGRST204
    for columns the table lacks), then NOT NULL checks (23502 for required
    columns absent from the payload), then value coercion (22P02). Records
    every upsert attempt so the tests can prove repairs happened and the
    next save landed first-try. The prune chain (delete/not_/in_) is
    supported too."""

    def __init__(self, store, required=None, missing=()):
        self._store = store
        self.required = dict(required or {})
        self.missing = tuple(missing)
        self.attempts = []
        self._pending_upsert = None
        self._keep = None
        # postgrest-py exposes the negation operator as an ATTRIBUTE:
        # `.delete().not_.in_(...)` - attribute access, no call.
        self.not_ = self

    # -------------------------------------------------------- query chain
    def upsert(self, rows, **_kw):
        self._pending_upsert = [dict(r) for r in (rows or []) if r]
        return self

    def delete(self, **_kw):
        self._keep = None
        return self

    def in_(self, _col, keep):
        self._keep = set(keep or [])
        return self

    def execute(self):
        if self._pending_upsert is not None:
            rows = self._pending_upsert
            self._pending_upsert = None
            self.attempts.append([dict(r) for r in rows])
            for row in rows:
                for col in self.missing:
                    if col in row:
                        raise _api_error(
                            "PGRST204",
                            f"Could not find the '{col}' column of "
                            f"'categories' in the schema cache")
            for col, accept in self.required.items():
                for row in rows:
                    if col not in row:
                        raise _api_error(
                            "23502",
                            f'null value in column "{col}" of relation '
                            '"categories" violates not-null constraint',
                            details=f"Failing row contains "
                                    f"({row.get('id')}, ...)")
                    problem = accept(row[col])
                    if problem:
                        raise _api_error(
                            "22P02",
                            f"invalid input syntax for type {problem}: "
                            f"{row[col]!r}")
            for row in rows:
                self._store[row["id"]] = dict(row)
            return _FakeResult([dict(r) for r in rows])
        if self._keep is None:
            return _FakeResult([])
        for pid in [pid for pid in list(self._store) if pid not in self._keep]:
            del self._store[pid]
        return _FakeResult([])


class _FakeClient:
    def __init__(self, store, table):
        self._store = store
        self._table = table

    def table(self, name):
        if name == "categories":
            return self._table
        raise AssertionError(f"unexpected table {name!r} in fake")


@pytest.fixture()
def _blank_cat_shape():
    """The discovered-table-shape cache is per worker; a test must not
    inherit what an earlier test's fake table taught this worker."""
    shape = getattr(supabase_store, "_CATS_SHAPE", None)
    if shape is not None:
        shape["fill"].clear()
        shape["drop"].clear()
        shape.get("values", {}).clear()
    yield
    if shape is not None:
        shape["fill"].clear()
        shape["drop"].clear()
        shape.get("values", {}).clear()


def _cats():
    return [
        {"id": "beauty", "name": "Beauty & skincare",
         "name_fr": "Beauté & soins", "image_url": "images/categories/beauty.jpg",
         "hidden": False, "updated_at": "2026-09-10T00:00:00Z"},
        {"id": "gift-set", "name": "Gift set & packaging",
         "name_fr": "Coffrets & emballage",
         "image_url": "images/categories/gift.jpg",
         "hidden": False, "updated_at": "2026-09-10T00:00:00Z"},
    ]


def test_save_drops_columns_the_narrow_table_lacks(monkeypatch, _blank_cat_shape):
    """The production PGRST204: the live table has none of name_fr /
    image_url / hidden / updated_at. The save must drop them one at a time
    and still land the rows (id and name are never dropped)."""
    store = {}
    table = _LegacyCatTable(store, missing=("name_fr", "image_url",
                                            "hidden", "updated_at"))
    monkeypatch.setattr(supabase_store, "client",
                        lambda: _FakeClient(store, table))

    assert supabase_store.save_categories_table(_cats()) is True
    assert set(store) == {"beauty", "gift-set"}
    for pid, row in store.items():
        assert row["id"] == pid and row["name"], "id/name must survive"
        assert "name_fr" not in row and "image_url" not in row, \
            "the narrower table cannot store these columns"
    assert len(table.attempts) >= 5, "one drop per missing column"


def test_save_fills_an_extra_not_null_column(monkeypatch, _blank_cat_shape):
    """The production 23502: the legacy table still carries `label text not
    null`. The save must fill it from the category name and land."""
    store = {}
    table = _LegacyCatTable(store, required={"label": lambda v: None})
    monkeypatch.setattr(supabase_store, "client",
                        lambda: _FakeClient(store, table))

    assert supabase_store.save_categories_table(_cats()) is True
    assert store["beauty"]["label"] == "Beauty & skincare"
    assert store["gift-set"]["label"] == "Gift set & packaging"
    assert len(table.attempts) >= 2
    assert "label" not in table.attempts[0][0]
    assert "label" in table.attempts[1][0]


def test_save_walks_a_typed_column_and_the_next_save_lands_first_try(
        monkeypatch, _blank_cat_shape):
    """Narrow table AND a legacy typed NOT NULL column (rank integer): the
    first save discovers the shape - fill, then the 22P02 walk lands 0 for
    the numeric - and the NEXT save starts from the cached shape and lands
    on its very first attempt."""
    store = {}
    table = _LegacyCatTable(
        store,
        missing=("name_fr", "image_url", "hidden", "updated_at"),
        required={"rank": lambda v: (
            None if isinstance(v, int) and not isinstance(v, bool)
            else "integer")})
    monkeypatch.setattr(supabase_store, "client",
                        lambda: _FakeClient(store, table))

    assert supabase_store.save_categories_table(_cats()) is True
    assert store["beauty"]["rank"] == 0, "the typed walk must land 0"
    assert "rank" in supabase_store._CATS_SHAPE["fill"]
    assert supabase_store._CATS_SHAPE["values"].get("rank") == 0
    first_attempts = len(table.attempts)

    # the owner's rename - the exact production flow that used to die
    assert supabase_store.save_categories_table(_cats()) is True
    assert len(table.attempts) == first_attempts + 1, \
        "the second save must land on the first attempt"


def test_save_prunes_categories_removed_locally(monkeypatch, _blank_cat_shape):
    """The post-upsert prune keeps working: a category deleted in the Admin
    portal must not come back on the next boot."""
    store = {}
    table = _LegacyCatTable(store)
    monkeypatch.setattr(supabase_store, "client",
                        lambda: _FakeClient(store, table))

    assert supabase_store.save_categories_table(_cats()) is True
    assert set(store) == {"beauty", "gift-set"}
    assert supabase_store.save_categories_table(_cats()[:1]) is True
    assert set(store) == {"beauty"}, "the prune must remove gift-set"


def test_unsatisfiable_table_fails_loudly_and_stores_nothing(
        monkeypatch, _blank_cat_shape):
    """If the live table demands a column the save can never satisfy, the
    failure must be honest (False + a named repair statement), never a
    half-written table."""
    store = {}
    # a typed NOT NULL column that accepts NOTHING the repair walk can
    # produce (0, False, "" are int/bool/str - only a float would pass)
    table = _LegacyCatTable(store, required={"rank": lambda v: (
        None if isinstance(v, float) and not isinstance(v, bool)
        else "real")})
    monkeypatch.setattr(supabase_store, "client",
                        lambda: _FakeClient(store, table))

    assert supabase_store.save_categories_table(_cats()) is False
    assert not store, "nothing may land when the table cannot be satisfied"
