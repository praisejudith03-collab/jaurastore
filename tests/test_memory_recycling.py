"""Free-tier memory recycling: bounded collection, idle pool reaping, <3 MB.

The owner's constraint is concrete: one 512 MB Render worker that must stay
awake forever (a 15-minute external ping + the in-process scheduler), with
buffers under 3 MB and no manual "Restart Service" ever needed.

This file pins the three mechanisms that make that true:

  * ``memory_manager`` collects request transients AFTER the response is on the
    wire (never in front of a shopper) and throttles the cost;
  * idle Supabase pools + the serialized catalogue caches are dropped only
    when the shop has really been idle, and a busy shop is never disturbed;
  * ``/healthz`` stays under 50 ms and reports the recycling state, and the
    largest buffer the app builds for a request stays under 3 MB.

Run with:  python3 -m pytest tests/test_memory_recycling.py -q
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

import app as appmod  # noqa: E402
import health_checks  # noqa: E402
import memory_manager  # noqa: E402
import supabase_store  # noqa: E402
from config import Config  # noqa: E402
from db import init_db  # noqa: E402

MAX_BUFFER = 3 * 1024 * 1024


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setattr(Config, "SUPABASE_URL", "")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "")
    init_db()
    health_checks.reset_caches()
    memory_manager.reset_for_tests()
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        yield c


# ------------------------------------------------------------- request policy
def test_a_shop_request_collects_only_after_the_body_is_written(app, monkeypatch):
    """Collection must never sit between the shopper and the bytes."""
    order = []
    monkeypatch.setattr(memory_manager, "collect",
                        lambda full=False: order.append(full) or True)
    a = appmod.create_app()
    a.config.update(TESTING=True)
    client = a.test_client()
    response = client.get("/api/catalog")
    assert response.status_code == 200
    # teardown_request runs once the body has been built and after every
    # after_request handler: one generation-1 sweep, never a full one.
    assert order == [False], order


def test_health_probes_are_never_blocked_by_the_collector(client, monkeypatch):
    """The liveness path is exempt: it must not collect or allocate."""
    seen = []
    monkeypatch.setattr(memory_manager, "collect",
                        lambda full=False: seen.append(full) or True)
    for path in ("/healthz", "/health"):
        client.get(path)
    assert seen == [], f"health probe triggered a collection: {seen}"


def test_upload_requests_take_a_full_sweep(client, monkeypatch):
    seen = []
    monkeypatch.setattr(memory_manager, "collect",
                        lambda full=False: seen.append(full) or True)
    import io
    client.post("/api/uploads/proof",
                data={"file": (io.BytesIO(b"not-an-image"), "x.png")},
                content_type="multipart/form-data")
    assert seen == [True], seen


def test_the_collector_is_throttled(client, monkeypatch):
    """A burst of catalogue reads must not spend its time in gc."""
    calls = []
    real = memory_manager.gc.collect
    monkeypatch.setattr(memory_manager.gc, "collect",
                        lambda *a, **k: calls.append(a) or real(*a, **k))
    memory_manager.reset_for_tests()
    for _ in range(5):
        memory_manager.collect()
    assert len(calls) == 1, calls
    assert calls[0] == (1,), "a normal request may only sweep generation 1"
    memory_manager.collect(full=True)
    assert calls[-1] == (2,), "an upload/forced sweep is a full collection"


# ------------------------------------------------------------------ idle reap
class _FakeClient:
    def __init__(self):
        self.closed = 0
        self.postgrest = self
        self.auth = self
        self.functions = self
        self.storage = self

    def close(self):
        self.closed += 1


def test_an_idle_shop_closes_its_pools_and_drops_the_catalog_cache(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(supabase_store, "_client", fake)
    monkeypatch.setattr(supabase_store, "_probe_client", _FakeClient())
    monkeypatch.setattr(supabase_store, "_loaded", True)
    monkeypatch.setattr(supabase_store, "_probe_loaded", True)
    memory_manager._state["lastActivity"] = (
        time.monotonic() - (memory_manager.IDLE_SECONDS + 5))
    monkeypatch.setattr(supabase_store, "_last_activity",
                        time.monotonic() - (memory_manager.IDLE_SECONDS + 5))
    monkeypatch.setattr(memory_manager, "_malloc_trim", lambda: 4096)

    import api as api_mod
    cleared = {"n": 0}
    monkeypatch.setattr(api_mod, "_invalidate_all_catalog_caches",
                        lambda: cleared.__setitem__("n", cleared["n"] + 1))

    report = memory_manager.recycle_idle_pools()
    assert report["recycled"] is True, report
    assert fake.closed >= 1, "the shop's httpx pool was not closed"
    assert cleared["n"] == 1, "the serialized catalogue snapshot survived"
    assert supabase_store._loaded is False
    assert report["trimmedBytes"] == 4096
    assert memory_manager.stats()["recycles"] == 1


def test_a_busy_shop_is_never_recycled():
    memory_manager._state["lastActivity"] = time.monotonic()
    report = memory_manager.recycle_idle_pools()
    assert report["recycled"] is False
    assert report["reason"] == "active"


def test_a_request_in_flight_blocks_the_recycle_and_later_releases_it(app, monkeypatch):
    monkeypatch.setattr(supabase_store, "_loaded", True)
    monkeypatch.setattr(supabase_store, "_client", _FakeClient())
    monkeypatch.setattr(supabase_store, "_probe_loaded", False)
    memory_manager.note_request()
    try:
        assert memory_manager.recycle_idle_pools(force=False)["reason"] == "busy"
    finally:
        memory_manager.note_response()
    memory_manager._state["lastActivity"] = (
        time.monotonic() - (memory_manager.IDLE_SECONDS + 1))
    monkeypatch.setattr(supabase_store, "_last_activity",
                        time.monotonic() - (memory_manager.IDLE_SECONDS + 1))
    assert memory_manager.recycle_idle_pools()["recycled"] is True


def test_the_scheduler_tick_recycles_and_records_it(monkeypatch):
    """The five-minute loop is the only clock when nobody shops."""
    import scheduler
    report = {"recycled": True, "closed": 2, "trimmedBytes": 1024}
    monkeypatch.setattr(memory_manager, "idle_tick", lambda: report)
    monkeypatch.setattr(scheduler.observability, "memory_mb", lambda: 61.5)
    before = int(scheduler._health.get("idleRecycles") or 0)
    scheduler._release_memory("test")
    assert scheduler._health["idleRecycles"] == before + 1
    assert scheduler._health["rssMb"] == 61.5


def test_recycling_is_safe_with_no_client_and_no_database(tmp_path, monkeypatch):
    monkeypatch.setattr(supabase_store, "_client", None)
    monkeypatch.setattr(supabase_store, "_probe_client", None)
    monkeypatch.setattr(supabase_store, "_loaded", False)
    monkeypatch.setattr(supabase_store, "_probe_loaded", False)
    report = memory_manager.recycle_idle_pools(force=True)
    assert report["recycled"] is False
    assert "error" not in report


# ------------------------------------------------------------------- healthz
def test_healthz_stays_under_fifty_ms_with_recycling_installed(client, monkeypatch):
    monkeypatch.setattr(memory_manager, "stats", memory_manager.stats)
    client.get("/healthz")
    timings = []
    for _ in range(5):
        started = time.monotonic()
        response = client.get("/healthz")
        timings.append((time.monotonic() - started) * 1000)
        assert response.status_code == 200
    # The production measurement is ~3 ms; 50 ms is the owner's ceiling and a
    # loaded CI box still has room inside it.
    assert max(timings) < 50, timings
    body = response.get_json()
    assert len(body["memory"]["gcCounts"]) == 3
    assert body["memory"]["idleLimitSeconds"] == memory_manager.IDLE_SECONDS
    assert body["memory"]["rssMb"] > 0


def test_no_response_buffer_reaches_three_megabytes(client):
    """Owner constraint: buffers under 3 MB.

    The catalogue is the biggest body the shop builds; the serialized snapshot
    is already dropped by the idle reap. This fails loudly if a future change
    starts materialising something unbounded (e.g. a whole media gallery).
    """
    for path in ("/api/catalog", "/api/products", "/healthz", "/api/site"):
        body = client.get(path).data
        assert len(body) < MAX_BUFFER, f"{path} built a {len(body)} byte buffer"


# ------------------------------------------------------------- admin controls
def _admin_client(app):
    with app.test_client() as c:
        response = c.post("/api/admin/login",
                          json={"email": "jaurastore@gmail.com", "password": PW})
        assert response.status_code == 200, response.data
        return c, response.get_json()["csrf"]


def test_the_owner_can_see_and_trigger_recycling_without_restarting(app, monkeypatch):
    """Self-heal from the portal: Render's "Restart Service" is never needed."""
    client, csrf = _admin_client(app)
    body = client.get("/api/admin/memory").get_json()
    assert body["ok"] is True
    assert body["limits"]["bufferBytes"] == MAX_BUFFER
    assert body["memory"]["idleLimitSeconds"] == memory_manager.IDLE_SECONDS

    monkeypatch.setattr(supabase_store, "_loaded", True)
    fake = _FakeClient()
    monkeypatch.setattr(supabase_store, "_client", fake)
    response = client.post("/api/admin/memory/recycle?force=1",
                           headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.data
    report = response.get_json()["report"]
    assert report["recycled"] is True
    assert fake.closed >= 1


def test_memory_controls_are_admin_only(app):
    with app.test_client() as c:
        assert c.get("/api/admin/memory").status_code in (401, 403)
        assert c.post("/api/admin/memory/recycle").status_code in (401, 403)
