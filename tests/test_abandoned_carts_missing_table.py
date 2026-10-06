"""An absent public.abandoned_carts table degrades quietly instead of erroring forever.

The Supabase copy of abandoned carts is a supplement to the local SQLite one,
so a project that never ran that migration is healthy rather than broken.
Supabase answers PGRST205 for the missing table; these tests pin that a single
absent table is detected once, skipped thereafter, logged once, and healed
without a restart when the table appears - while a genuine transient failure
stays loud and keeps being retried.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import supabase_store  # noqa: E402
from config import Config  # noqa: E402

MISSING_TABLE = ('{"code":"PGRST205","message":"Could not find the table '
                 "'public.abandoned_carts' in the schema cache\"}")


class _Query:
    """A PostgREST builder double; every verb chains, execute() is scripted."""

    def __init__(self, outcome):
        self._outcome = outcome

    def __getattr__(self, name):
        # Any unknown chaining verb returns the builder again, so the double
        # tracks the real builder's fluent shape without listing every verb.
        def _chain(*_args, **_kwargs):
            return self
        return _chain

    @property
    def not_(self):
        return self

    def execute(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _Client:
    """Counts table() calls so a short-circuit is observable."""

    def __init__(self, outcome, table="abandoned_carts"):
        self._outcome = outcome
        self.calls = 0
        self._table = table

    def table(self, name):
        self.calls += 1
        assert name == self._table, name
        return _Query(self._outcome)


@pytest.fixture(autouse=True)
def _reset_feature_check():
    """Each test starts believing the table exists and nothing logged."""
    supabase_store._abandoned_missing_until = 0.0
    supabase_store._abandoned_missing_logged = False
    yield
    supabase_store._abandoned_missing_until = 0.0
    supabase_store._abandoned_missing_logged = False


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(Config, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")


def _install(monkeypatch, client):
    monkeypatch.setattr(supabase_store, "client", lambda: client)
    return client


def test_a_missing_table_is_probed_once_then_skipped(configured, monkeypatch):
    """After PGRST205 the doomed roundtrip stops being attempted."""
    fake = _install(monkeypatch, _Client(RuntimeError(MISSING_TABLE)))

    assert supabase_store.load_due_abandoned_carts("2026-01-01T00:00:00Z") == []
    assert fake.calls == 1

    for _ in range(5):
        assert supabase_store.load_due_abandoned_carts("2026-01-01T00:00:00Z") == []
    assert fake.calls == 1, "the feature check did not short-circuit later calls"


def test_every_abandoned_helper_skips_an_absent_table(configured, monkeypatch):
    """All seven helpers degrade instead of raising or re-querying."""
    fake = _install(monkeypatch, _Client(RuntimeError(MISSING_TABLE)))

    assert supabase_store.load_due_abandoned_carts("2026-01-01T00:00:00Z") == []
    assert supabase_store.mirror_abandoned_cart({"token": "t1"}) is False
    assert supabase_store.mark_abandoned_converted("t1", "2026-01-01T00:00:00Z") is False
    assert supabase_store.mark_abandoned_reminder_sent("t1", "2026-01-01T00:00:00Z") is False
    assert supabase_store.delete_abandoned_carts_for_tokens(["t1"]) == 0
    assert supabase_store.delete_abandoned_carts_for_email("a@b.co") == 0
    assert supabase_store.delete_abandoned_carts_for_product("p1", "Widget") == 0
    assert fake.calls == 1, "only the first probe should ever reach Supabase"


def test_the_absent_table_is_logged_once_not_every_call(configured, monkeypatch, capsys):
    """One missing table must not print an error on every 5-minute tick."""
    _install(monkeypatch, _Client(RuntimeError(MISSING_TABLE)))
    for _ in range(10):
        supabase_store.load_due_abandoned_carts("2026-01-01T00:00:00Z")
    out = capsys.readouterr().out
    assert out.count("abandoned_carts is absent") == 1, out
    assert "PGRST205" not in out.split("abandoned_carts is absent")[0], out


def test_a_transient_failure_stays_loud_and_keeps_retrying(configured, monkeypatch, capsys):
    """A real error is not mistaken for a missing table."""
    fake = _install(monkeypatch, _Client(RuntimeError("HTTP Error 500: boom")))

    for _ in range(3):
        assert supabase_store.load_due_abandoned_carts("2026-01-01T00:00:00Z") == []

    assert fake.calls == 3, "a transient failure must not be latched as permanent"
    out = capsys.readouterr().out
    assert out.count("abandoned carts load failed") == 3, out
    assert "abandoned_carts is absent" not in out, out


def test_creating_the_table_later_heals_without_a_restart(configured, monkeypatch):
    """The periodic re-probe picks up a table the owner adds afterwards."""
    missing = _Client(RuntimeError(MISSING_TABLE))
    _install(monkeypatch, missing)
    assert supabase_store.load_due_abandoned_carts("2026-01-01T00:00:00Z") == []
    assert missing.calls == 1
    assert not supabase_store._abandoned_table_usable()

    # Once the re-check interval has passed the next call tries Supabase again,
    # and a table that now exists clears the verdict for good.
    supabase_store._abandoned_missing_until = 0.0
    healthy = _Client({"data": [{"token": "t9"}]})
    _install(monkeypatch, healthy)
    rows = supabase_store.load_due_abandoned_carts("2026-01-01T00:00:00Z")
    assert rows == [{"token": "t9"}]
    assert healthy.calls == 1
    assert supabase_store._abandoned_table_usable()
    assert supabase_store._abandoned_missing_logged is False


def test_the_recheck_interval_is_longer_than_one_reminder_tick():
    """Skipping must outlast the 300s sweep or the log spam simply returns."""
    import scheduler
    assert supabase_store._ABANDONED_RECHECK_SECONDS > scheduler.TICK_SECONDS


def test_healthz_never_queries_abandoned_carts(configured, monkeypatch):
    """/healthz must not inherit an optional table's failure or its latency."""
    import health_checks

    def explode(*_args, **_kwargs):
        raise AssertionError("/healthz must not read abandoned_carts")

    for name in ("load_due_abandoned_carts", "mirror_abandoned_cart",
                 "mark_abandoned_converted", "mark_abandoned_reminder_sent",
                 "delete_abandoned_carts_for_tokens",
                 "delete_abandoned_carts_for_email",
                 "delete_abandoned_carts_for_product"):
        monkeypatch.setattr(supabase_store, name, explode)

    monkeypatch.setattr(supabase_store, "ping", lambda: "ok")
    monkeypatch.setattr(health_checks, "DATABASE_PING_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(Config, "SCHEDULER_ENABLED", False)

    body, status = health_checks.health_report()
    assert status == 200, body
    assert body["database"]["checks"]["supabase"]["ok"] is True
