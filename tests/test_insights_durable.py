"""Durable store insights: analytics survive a redeploy.

Page views and engagement events used to live only in the SQLite file on
the Render disk, so every deploy wiped the dashboard. Each row is now
mirrored into the Supabase analytics_events table (schema_sections/
17_analytics.sql) and create_app() copies the retention window back into
the local tables on boot.

These tests cover both halves - the record() mirror and the boot restore -
plus the failure modes: a down mirror must never stop the counting, and a
failed restore must never stop the boot.

Run with:  python3 -m pytest tests/test_insights_durable.py -q
No real Supabase needed: supabase_store.client() is monkeypatched to an
in-memory fake.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

from config import Config  # noqa: E402
import app as appmod  # noqa: E402
import analytics as analytics_mod  # noqa: E402
import supabase_store  # noqa: E402
from db import execute, init_db, one, query  # noqa: E402

FAKE_ORIGIN = "https://fake.supabase.co"


def _today():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


def _wipe_analytics_tables():
    """Simulate the wiped Render disk: analytics gone from SQLite."""
    init_db()
    for table in ("page_views", "events", "visitors", "presence"):
        try:
            execute(f"DELETE FROM {table}")
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _clean_analytics():
    _wipe_analytics_tables()
    yield
    _wipe_analytics_tables()


# Same in-memory fakes as tests/test_growth_persistence.py, plus insert/gte
# for the analytics_events mirror.
class FakeTable:
    def __init__(self, owner, name):
        self._owner = owner
        self._name = name
        self._filters = []

    def insert(self, rows):
        store = self._owner.tables.setdefault(self._name, [])
        rows = rows if isinstance(rows, list) else [rows]
        for r in rows:
            store.append(dict(r))
        return self

    def upsert(self, rows, on_conflict=None):
        store = self._owner.tables.setdefault(self._name, [])
        rows = rows if isinstance(rows, list) else [rows]
        for r in rows:
            if on_conflict:
                cols = [c.strip() for c in on_conflict.split(",")]
                key = tuple(str(r.get(c)) for c in cols)
                store[:] = [x for x in store
                            if tuple(str(x.get(c)) for c in cols) != key]
            elif self._name == "growth_settings":
                store[:] = [x for x in store if x.get("key") != r.get("key")]
            else:
                store[:] = [x for x in store if x.get("id") != r.get("id")]
            store.append(dict(r))
        return self

    def select(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n=None):
        return self

    def eq(self, key, val):
        self._filters.append(("eq", key, val))
        return self

    def gte(self, key, val):
        self._filters.append(("gte", key, val))
        return self

    def execute(self):
        rows = list(self._owner.tables.get(self._name, []))
        for op, key, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if r.get(key) == val]
            elif op == "gte":
                rows = [r for r in rows if str(r.get(key) or "") >= str(val)]
        return {"data": rows}


class FakeSupabaseClient:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return FakeTable(self, name)


@pytest.fixture()
def sb(monkeypatch):
    """Supabase configured + reachable, with a fresh in-memory database."""
    client = FakeSupabaseClient()
    monkeypatch.setattr(Config, "SUPABASE_URL", FAKE_ORIGIN)
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    monkeypatch.setattr(supabase_store, "client", lambda: client)
    return client


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


def _csrf(client):
    return client.get("/api/config").get_json()["csrf"]


def _track(client, events):
    tok = _csrf(client)
    return client.post("/api/track", json={"events": events},
                       headers={"X-CSRF-Token": tok})


def _mirror_row(kind, day, at, **kw):
    row = {"kind": kind, "vid": "v-restored", "sid": "s1", "path": "/",
           "page": "home", "ref": "", "product_id": "",
           "product_name": "", "value": 0, "currency": "", "city": "",
           "region": "", "country": "", "day": day, "at": at}
    row.update(kw)
    return row


# ------------------------------------------------------------------- mirror
def test_track_mirrors_every_stored_row_to_supabase(client, sb):
    """One /api/track batch stores 1 page view + 3 events locally and the
    same 4 rows land in the Supabase analytics_events table."""
    r = _track(client, [
        {"type": "visit", "path": "/index.html", "page": "home", "sid": "s1",
         "city": "Lome", "country": "Togo"},
        {"type": "view", "path": "/product.html", "page": "product",
         "productId": "wix-001", "productName": "Test Bag", "sid": "s1"},
        {"type": "cart", "productId": "wix-001", "sid": "s1"},
        {"type": "checkout_start", "sid": "s1", "page": "checkout"},
    ])
    assert r.status_code == 200, r.data
    assert r.get_json()["recorded"] == 4

    assert one("SELECT COUNT(*) n FROM page_views")["n"] == 1
    assert one("SELECT COUNT(*) n FROM events")["n"] == 3

    mirrored = sb.tables.get("analytics_events", [])
    assert len(mirrored) == 4, mirrored
    assert sorted(m["kind"] for m in mirrored) == [
        "cart", "checkout_start", "page_view", "view"]
    assert {m["day"] for m in mirrored} == {_today()}
    assert len({m["vid"] for m in mirrored}) == 1     # one visitor, one batch
    pv = [m for m in mirrored if m["kind"] == "page_view"][0]
    assert pv["path"] == "/index.html" and pv["city"] == "Lome"
    view = [m for m in mirrored if m["kind"] == "view"][0]
    assert view["product_id"] == "wix-001" and view["product_name"] == "Test Bag"


def test_heartbeat_is_neither_stored_nor_mirrored(client, sb):
    """Presence heartbeats only refresh the live-now table - they are not
    analytics rows, so they must not reach the durable mirror either."""
    r = _track(client, [{"type": "heartbeat", "page": "home", "sid": "s1"}])
    assert r.status_code == 200, r.data
    assert r.get_json()["recorded"] == 0
    assert one("SELECT COUNT(*) n FROM page_views")["n"] == 0
    assert one("SELECT COUNT(*) n FROM events")["n"] == 0
    assert sb.tables.get("analytics_events", []) == []
    assert one("SELECT COUNT(*) n FROM presence")["n"] == 1  # still live


def test_tracking_keeps_counting_locally_when_the_mirror_is_down(client, monkeypatch):
    """Supabase down mid-sale: the batch is still counted locally and the
    tracker still answers 200 - only durability has a gap."""
    class _BreakingTable(FakeTable):
        def insert(self, rows):
            raise RuntimeError("supabase is down")

    class _BreakingClient(FakeSupabaseClient):
        def table(self, name):
            if name == "analytics_events":
                return _BreakingTable(self, name)
            return super().table(name)

    monkeypatch.setattr(Config, "SUPABASE_URL", FAKE_ORIGIN)
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    monkeypatch.setattr(supabase_store, "client", lambda: _BreakingClient())

    r = _track(client, [
        {"type": "visit", "path": "/", "page": "home", "sid": "s1"},
        {"type": "view", "productId": "wix-001", "sid": "s1"},
    ])
    assert r.status_code == 200, r.data
    assert r.get_json()["recorded"] == 2
    assert one("SELECT COUNT(*) n FROM page_views")["n"] == 1
    assert one("SELECT COUNT(*) n FROM events")["n"] == 1


# ------------------------------------------------------------- boot restore
def test_boot_restore_refills_page_views_and_events_after_a_wiped_disk(sb):
    """The exact scenario: insights were mirrored, a deploy wiped the disk,
    the shop boots - the retention window comes back."""
    today = _today()
    sb.tables["analytics_events"] = [
        _mirror_row("page_view", today, f"{today}T10:00:00", path="/index.html"),
        _mirror_row("page_view", today, f"{today}T11:00:00", path="/shop.html",
                    vid="v-second"),
        _mirror_row("view", today, f"{today}T11:05:00", product_id="wix-001",
                    product_name="Test Bag"),
    ]
    _wipe_analytics_tables()
    assert one("SELECT COUNT(*) n FROM page_views")["n"] == 0  # wiped

    appmod.create_app()

    assert one("SELECT COUNT(*) n FROM page_views")["n"] == 2
    assert one("SELECT COUNT(*) n FROM events")["n"] == 1
    totals = analytics_mod.report(days=7)["totals"]
    assert totals["pageViews"] == 2
    assert totals["uniqueVisitors"] == 2


def test_boot_restore_rebuilds_visitors_locations_and_uniques(sb):
    """Visitors have no mirrored rows of their own - they are re-derived
    from the restored page views so uniques and locations come back too."""
    today = _today()
    sb.tables["analytics_events"] = [
        _mirror_row("page_view", today, f"{today}T09:00:00", vid="v-lome",
                    city="Lome", country="Togo"),
        _mirror_row("page_view", today, f"{today}T10:00:00", vid="v-lome",
                    city="Lome", country="Togo"),
        _mirror_row("page_view", today, f"{today}T10:05:00", vid="v-lagos",
                    city="Lagos", country="Nigeria"),
    ]
    _wipe_analytics_tables()
    appmod.create_app()

    visitors = query("SELECT vid, sessions, city, country FROM visitors")
    assert sorted(v["vid"] for v in visitors) == ["v-lagos", "v-lome"]
    report = analytics_mod.report(days=7)
    assert report["totals"]["newVisitors"] == 2
    cities = {(loc.get("city"), loc.get("country")) for loc in report["locations"]}
    assert ("Lome", "Togo") in cities and ("Lagos", "Nigeria") in cities


def test_boot_restore_is_a_noop_when_the_disk_is_intact(sb, monkeypatch):
    """A normal reboot (disk intact) must not duplicate anything - the
    loader is not even called when the local window is non-empty."""
    today = _today()
    execute("INSERT INTO page_views (vid, sid, path, page, ref, city, country, day, at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("v-local", "s1", "/", "home", "", "", "", today, f"{today}T08:00:00"))
    sb.tables["analytics_events"] = [
        _mirror_row("page_view", today, f"{today}T10:00:00", vid="v-remote")]
    calls = []
    real_load = supabase_store.load_analytics_events

    def _spy(cutoff, limit=5000):
        calls.append(cutoff)
        return real_load(cutoff, limit)

    monkeypatch.setattr(supabase_store, "load_analytics_events", _spy)
    appmod.create_app()

    assert calls == []                                   # loader never called
    assert one("SELECT COUNT(*) n FROM page_views")["n"] == 1
    assert one("SELECT vid v FROM page_views")["v"] == "v-local"


def test_boot_restore_keeps_only_the_retention_window(sb, monkeypatch):
    """Rows older than the retention window are not restored: the mirror
    is read with the prune cutoff, and the restore enforces it too."""
    today = _today()
    old_day = (datetime.datetime.utcnow() - datetime.timedelta(days=500)
               ).strftime("%Y-%m-%d")
    sb.tables["analytics_events"] = [
        _mirror_row("page_view", today, f"{today}T10:00:00", path="/new"),
        _mirror_row("page_view", old_day, f"{old_day}T10:00:00", path="/old"),
    ]
    _wipe_analytics_tables()
    appmod.create_app()
    rows = query("SELECT path FROM page_views")
    assert [r["path"] for r in rows] == ["/new"]

    # ... and the same cutoff holds when restoring an explicit row list.
    _wipe_analytics_tables()
    restored = analytics_mod.restore_from_supabase([
        _mirror_row("page_view", today, f"{today}T10:00:00", path="/new"),
        _mirror_row("page_view", old_day, f"{old_day}T10:00:00", path="/old"),
        {"kind": "page_view", "day": "junk", "at": "junk", "path": "/junk"},
        {"kind": "heartbeat", "day": today, "at": f"{today}T10:00:00"},
        "not-a-row",
    ])
    assert restored == 1
    rows = query("SELECT path FROM page_views")
    assert [r["path"] for r in rows] == ["/new"]

    # The window matches prune(): the same cutoff day on both sides.
    assert analytics_mod._retention_cutoff_day() == analytics_mod._days_ago(
        max(1, int(Config.ANALYTICS_RETENTION_DAYS)))


def test_boot_survives_supabase_being_unreachable(monkeypatch):
    """A failed restore must never stop the boot: the shop still comes up
    and answers health checks with an empty (but working) dashboard."""
    class _DownTable(FakeTable):
        def execute(self):
            raise RuntimeError("network down")

    class _DownClient(FakeSupabaseClient):
        def table(self, name):
            if name == "analytics_events":
                return _DownTable(self, name)
            return super().table(name)

    monkeypatch.setattr(Config, "SUPABASE_URL", FAKE_ORIGIN)
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    monkeypatch.setattr(supabase_store, "client", lambda: _DownClient())

    _wipe_analytics_tables()
    boot = appmod.create_app()
    assert boot is not None
    assert boot.test_client().get("/healthz").get_json() == {
        "ok": True, "env": Config.ENV}
    assert analytics_mod.report(days=7)["totals"]["pageViews"] == 0


def test_restored_rows_keep_product_attribution_and_conversion(sb):
    """Insights, not just counts: top products and the funnel survive the
    wipe because the mirror keeps every engagement row whole."""
    today = _today()
    sb.tables["analytics_events"] = [
        _mirror_row("page_view", today, f"{today}T09:00:00",
                    path="/product.html", page="product"),
        _mirror_row("view", today, f"{today}T09:01:00", product_id="wix-001",
                    product_name="Test Bag"),
        _mirror_row("cart", today, f"{today}T09:02:00", product_id="wix-001",
                    product_name="Test Bag"),
        _mirror_row("checkout_start", today, f"{today}T09:03:00"),
        _mirror_row("purchase", today, f"{today}T09:04:00",
                    product_id="wix-001", product_name="Test Bag",
                    value=5000, currency="NGN"),
    ]
    _wipe_analytics_tables()
    appmod.create_app()

    report = analytics_mod.report(days=7)
    assert report["topProducts"][0]["productId"] == "wix-001"
    assert report["topProducts"][0]["views"] == 1
    assert report["topProducts"][0]["carts"] == 1
    assert report["topProducts"][0]["purchases"] == 1
    assert report["conversion"]["checkoutAttempts"] == 1
    assert report["conversion"]["productViews"] == 1
    assert report["conversion"]["cartAdds"] == 1
