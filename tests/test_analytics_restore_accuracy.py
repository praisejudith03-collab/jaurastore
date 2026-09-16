"""The dashboard must survive a deploy without losing TODAY'S visitors.

Root cause of "1 visitor right after a deploy": the boot restore read the
Supabase mirror with `.order("at").limit(5000)` - OLDEST first, hard-capped -
so once the 400-day retention window passed 5000 rows the restore silently
dropped the newest events and rebuilt the dashboard from ancient days.
Visitors today became 1 (or 0) no matter how many people were on the site.

The read is now newest-first and paged through the whole window, and the
restore runs under a cross-process lock so two gunicorn workers booting
together cannot duplicate the window. These tests pin all three properties
with an in-memory fake of the PostgREST client (same trick as
tests/test_insights_durable.py, extended with range()/order() semantics).

Run with:  python3 -m pytest tests/test_analytics_restore_accuracy.py -q
"""
import datetime
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

import analytics as analytics_mod  # noqa: E402
import supabase_store  # noqa: E402
from db import execute, init_db, one, query  # noqa: E402


def _day(n):
    return (datetime.datetime.utcnow() - datetime.timedelta(days=n)).strftime("%Y-%m-%d")


def _mirror_row(kind, day, at, vid="v-1", path="/shop.html"):
    return {"kind": kind, "vid": vid, "sid": "s-1", "path": path, "page": "Shop",
            "ref": "", "city": "Lagos", "country": "Nigeria", "day": day, "at": at,
            "product_id": "", "product_name": "", "value": 0, "currency": "NGN"}


class FakeTable:
    """PostgREST-shaped fake: gte/order(desc)/range windowing, like the real
    query builder the production client uses."""

    def __init__(self, owner, name):
        self._owner = owner
        self._name = name
        self._filters = []
        self._order = None
        self._limit = None
        self._range = None

    def insert(self, rows):
        store = self._owner.tables.setdefault(self._name, [])
        rows = rows if isinstance(rows, list) else [rows]
        for r in rows:
            store.append(dict(r))
        return self

    def select(self, *a):
        return self

    def order(self, column=None, *a, **k):
        self._order = (column, bool(k.get("desc")))
        return self

    def limit(self, n=None):
        self._limit = n
        return self

    def range(self, start, end, *a, **k):
        self._range = (int(start), int(end))
        return self

    def gte(self, key, val):
        self._filters.append(("gte", key, val))
        return self

    def execute(self):
        rows = list(self._owner.tables.get(self._name, []))
        for op, key, val in self._filters:
            if op == "gte":
                rows = [r for r in rows if str(r.get(key) or "") >= str(val)]
        if self._order and self._order[0]:
            col, desc = self._order
            rows = sorted(rows, key=lambda r: str(r.get(col) or ""), reverse=desc)
        if self._range:
            s, e = self._range
            rows = rows[s:e + 1]
        elif self._limit:
            rows = rows[:self._limit]
        return {"data": rows}


class FakeSupabaseClient:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return FakeTable(self, name)


@pytest.fixture()
def fake_supabase(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(supabase_store, "client", lambda: client)
    return client


@pytest.fixture(autouse=True)
def _clean_analytics():
    init_db()
    for table in ("page_views", "events", "visitors", "presence", "search_queries"):
        try:
            execute(f"DELETE FROM {table}")
        except Exception:
            pass
    yield
    for table in ("page_views", "events", "visitors", "presence", "search_queries"):
        try:
            execute(f"DELETE FROM {table}")
        except Exception:
            pass


def test_restore_copies_a_window_far_larger_than_one_page(fake_supabase, monkeypatch):
    """More rows than one page: every row still comes back, today's first."""
    monkeypatch.setattr(supabase_store, "ANALYTICS_PAGE", 3, raising=False)
    rows = []
    for i in range(40):                              # 40 rows, 4 pages of 3 + rest
        day = _day(30 - i // 2)
        rows.append(_mirror_row("page_view", day, f"{day}T10:00:00", vid=f"v-{i:03d}"))
    rows.append(_mirror_row("page_view", _day(0), f"{_day(0)}T09:00:00", vid="v-today"))
    fake_supabase.tables["analytics_events"] = rows
    restored = analytics_mod.restore_from_supabase()
    assert restored == 41, restored
    assert one("SELECT COUNT(*) n FROM page_views")["n"] == 41


def test_restore_keeps_the_newest_rows_not_the_oldest(fake_supabase):
    """THE defect: with a cap, the cap must eat the OLDEST days. Today's
    visitors are the dashboard's headline number."""
    rows = [_mirror_row("page_view", _day(0), f"{_day(0)}T10:00:00", vid="v-today-1"),
            _mirror_row("page_view", _day(0), f"{_day(0)}T11:00:00", vid="v-today-2"),
            _mirror_row("page_view", _day(0), f"{_day(0)}T12:00:00", vid="v-today-3")]
    for old in range(1, 10):
        rows.append(_mirror_row("page_view", _day(old), f"{_day(old)}T10:00:00",
                                vid=f"v-old-{old}"))
    fake_supabase.tables["analytics_events"] = rows
    got = supabase_store.load_analytics_events(_day(400), limit=3)
    assert len(got) == 3
    assert all(r["vid"].startswith("v-today") for r in got), [r["vid"] for r in got]


def test_load_analytics_events_returns_newest_first(fake_supabase):
    rows = []
    for i in range(7):
        day = _day(7 - i)
        rows.append(_mirror_row("page_view", day, f"{day}T10:00:00", vid=f"v-{i}"))
    fake_supabase.tables["analytics_events"] = rows
    got = supabase_store.load_analytics_events(_day(400))
    assert [r["day"] for r in got] == sorted([r["day"] for r in got], reverse=True)


def test_visitors_today_survive_a_deploy(fake_supabase):
    """End to end: the wiped disk is restored and today's visitor count is the
    number of distinct people who actually visited today."""
    today = _day(0)
    fake_supabase.tables["analytics_events"] = [
        _mirror_row("page_view", today, f"{today}T10:00:00", vid="v-a"),
        _mirror_row("page_view", today, f"{today}T10:05:00", vid="v-a"),
        _mirror_row("page_view", today, f"{today}T10:30:00", vid="v-b"),
        _mirror_row("page_view", today, f"{today}T11:00:00", vid="v-c"),
        _mirror_row("page_view", _day(2), f"{_day(2)}T10:00:00", vid="v-old"),
    ]
    restored = analytics_mod.restore_from_supabase()
    assert restored == 5
    periods = analytics_mod.periods()
    assert periods["today"]["visitors"] == 3, periods["today"]


def test_concurrent_boot_restores_never_duplicate_the_window(fake_supabase):
    """Two gunicorn workers booting together: one restores, the other waits,
    re-checks emptiness inside the lock and writes nothing."""
    today = _day(0)
    fake_supabase.tables["analytics_events"] = [
        _mirror_row("page_view", today, f"{today}T1{i}:00:00", vid=f"v-{i}")
        for i in range(6)
    ]
    results = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait(timeout=10)
        results.append(analytics_mod.restore_from_supabase())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == [0, 6], results
    assert one("SELECT COUNT(*) n FROM page_views")["n"] == 6


def test_search_history_restore_is_paged_and_locked_too(fake_supabase, monkeypatch):
    monkeypatch.setattr(supabase_store, "ANALYTICS_PAGE", 2, raising=False)
    today = _day(0)
    fake_supabase.tables["search_queries"] = [
        {"vid": f"v-{i}", "sid": "s", "q": f"oil {i}", "q_norm": f"oil {i}",
         "results": 3, "category": "", "city": "", "country": "", "day": today,
         "at": f"{today}T10:00:00"} for i in range(7)
    ]
    restored = analytics_mod.restore_searches_from_supabase()
    assert restored == 7, restored
    assert one("SELECT COUNT(*) n FROM search_queries")["n"] == 7
    # and a second restore is a no-op, not a duplicate
    assert analytics_mod.restore_searches_from_supabase() == 0
    assert one("SELECT COUNT(*) n FROM search_queries")["n"] == 7
