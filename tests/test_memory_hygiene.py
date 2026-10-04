"""512MB hardening: bounded chunks, collections, isolation and self-healing.

Render's free instance gives one 512MB worker for the web process and every
background loop it runs. The failure modes this file pins:

  * a batch job holding a whole result set (a 60-photo repair, a supplier page,
    a broadcast to every inbox) and pushing RSS over the ceiling;
  * a crashed step - or a crashed *queue* - taking the whole maintenance tick
    with it, so the next tick never runs;
  * a worker thread that dies once (a deploy, an OOM kill of the old process)
    and either never comes back or pages the owner with a false alarm;
  * a retry ladder that hammers a broken dependency instead of backing off.

Run with:  python3 -m pytest tests/test_memory_hygiene.py -q
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

import scheduler  # noqa: E402
import task_queue  # noqa: E402


def test_the_scheduler_chunk_is_bounded_to_ten_records():
    assert 1 <= scheduler.CHUNK_SIZE <= 10
    assert scheduler.CHUNK_SIZE < scheduler._PHOTO_REPAIR_PER_RUN


def test_both_backoff_ladders_agree():
    for attempt in range(1, 7):
        assert scheduler._backoff_seconds(attempt) == task_queue.backoff_seconds(attempt)


def test_the_backoff_ladder_grows_and_is_capped():
    values = [scheduler._backoff_seconds(n) for n in range(1, 8)]
    assert values[0] < values[1] < values[2]
    assert values[-1] <= scheduler._backoff_seconds(99) <= 60.0


def test_photo_repair_runs_in_chunks_with_a_collection_between(monkeypatch):
    """The daily repair used to hand one 60-row batch to the catalogue."""
    import catalog as catalog_mod

    seen = []
    collections = {"n": 0}
    real_collect = scheduler.gc.collect

    def fake_repair(limit=None, actor=""):
        # One repair found per chunk keeps the sweep going for every chunk, so
        # the test can see the chunking rather than the early exit.
        seen.append(int(limit or 0))
        return {"checked": 1, "missing": ["x"], "repaired": ["x"]}

    def counting_collect(*a, **k):
        collections["n"] += 1
        return real_collect(*a, **k)

    monkeypatch.setattr(catalog_mod, "repair_dead_photos", fake_repair)
    monkeypatch.setattr(scheduler.gc, "collect", counting_collect)
    monkeypatch.setattr(scheduler, "_last_photo_repair", "")
    scheduler._repair_photos()
    assert len(seen) >= 2, "the repair was not chunked at all"
    assert max(seen) <= 10, seen
    assert collections["n"] >= len(seen), "no collection ran between chunks"


def test_a_broken_chunk_is_traced_and_the_tick_survives(monkeypatch):
    failures = []
    monkeypatch.setattr(scheduler, "_note_failure",
                        lambda *a, **k: failures.append(a[0] if a else ""))
    calls = {"n": 0}

    def fake_repair(limit=None, actor=""):
        calls["n"] += 1
        raise RuntimeError("catalogue unavailable")

    import catalog as catalog_mod
    monkeypatch.setattr(catalog_mod, "repair_dead_photos", fake_repair)
    monkeypatch.setattr(scheduler, "_last_photo_repair", "")
    scheduler._repair_photos()                      # must not raise
    assert calls["n"] == 1, "a broken chunk kept hammering the catalogue"
    assert "scheduler.repair_photos" in failures


def test_the_maintenance_tick_drains_the_task_queue(monkeypatch):
    """The queue's thread is event-driven; this is the five-minute safety net."""
    seen = []
    monkeypatch.setattr(task_queue, "drain",
                        lambda logger=None, **k: seen.append("drained") or {})
    silent = lambda *a, **k: None
    for name in ("_keep_alive", "_run_backup", "_remirror", "_repair_photos",
                 "_persist_counters", "_supplier_watchdog", "_stock_guardrail",
                 "_nightly_run"):
        monkeypatch.setattr(scheduler, name, silent)
    monkeypatch.setattr(scheduler, "_nightly_due", lambda now=None: False)
    scheduler._maintenance_tick()
    assert seen == ["drained"]


def test_a_broken_step_cannot_skip_the_rest_of_the_tick(monkeypatch):
    order = []
    monkeypatch.setattr(scheduler, "_note_failure", lambda *a, **k: None)

    def boom(name):
        def _f(*a, **k):
            order.append(name)
            raise RuntimeError(name + " is broken")
        return _f

    monkeypatch.setattr(scheduler, "_keep_alive", boom("keep_alive"))
    monkeypatch.setattr(scheduler, "_run_backup", boom("backup"))
    monkeypatch.setattr(scheduler, "_remirror", boom("remirror"))
    monkeypatch.setattr(scheduler, "_repair_photos", boom("repair"))
    monkeypatch.setattr(scheduler, "_persist_counters", boom("counters"))
    monkeypatch.setattr(scheduler, "_supplier_watchdog", boom("supplier"))
    monkeypatch.setattr(scheduler, "_stock_guardrail", boom("guardrail"))
    monkeypatch.setattr(scheduler, "_nightly_due", lambda now=None: False)
    monkeypatch.setattr(task_queue, "drain", boom("tasks"))
    scheduler._maintenance_tick()                    # must not raise
    assert order[-1] == "guardrail", f"the tick stopped early: {order}"
    assert set(order) == {"keep_alive", "backup", "remirror", "repair", "counters",
                          "supplier", "tasks", "guardrail"}
    assert scheduler._health["maintenanceLastRun"]


def test_a_broken_queue_cannot_kill_the_maintenance_tick(monkeypatch):
    monkeypatch.setattr(scheduler, "_note_failure", lambda *a, **k: None)
    for name in ("_keep_alive", "_run_backup", "_remirror", "_repair_photos",
                 "_persist_counters", "_supplier_watchdog", "_stock_guardrail"):
        monkeypatch.setattr(scheduler, name, lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "_nightly_due", lambda now=None: False)
    monkeypatch.setattr(scheduler, "_stock_guardrail", lambda *a, **k: None)

    def broken_drain(logger=None, **k):
        raise RuntimeError("queue exploded")

    monkeypatch.setattr(task_queue, "drain", broken_drain)
    scheduler._maintenance_tick()                    # must not raise


def test_health_snapshot_exposes_the_queue_and_the_restart_count():
    snapshot = scheduler.health_snapshot(repair=False)
    assert "tasks" in snapshot
    assert snapshot["tasks"]["thread"] == task_queue.TASK_THREAD
    assert "restarts" in snapshot
    assert snapshot["intervalSeconds"] == scheduler.TICK_SECONDS
    assert snapshot["backgroundAlive"] in (True, False)


def test_the_scheduler_supervises_the_task_worker(monkeypatch):
    seen = []
    was_started = scheduler._started.is_set()
    scheduler._started.set()
    try:
        monkeypatch.setattr(task_queue, "ensure_alive",
                            lambda app=None: seen.append(app) or [])
        monkeypatch.setattr(scheduler, "_alive", lambda name=None: True)
        scheduler.ensure_alive()
        assert seen == [None]
    finally:
        if not was_started:
            scheduler._started.clear()


def test_the_worker_records_its_own_rss_on_every_pass(monkeypatch):
    monkeypatch.setattr(scheduler.observability, "memory_mb", lambda: 123.4)
    scheduler._release_memory("test")
    assert scheduler._health["rssMb"] == 123.4


def test_the_supplier_batch_releases_its_page_every_tick(monkeypatch):
    import supplier_watchdog
    collections = {"n": 0}
    real_collect = scheduler.gc.collect
    monkeypatch.setattr(scheduler.gc, "collect",
                        lambda *a, **k: (collections.__setitem__("n", collections["n"] + 1),
                                         real_collect(*a, **k))[1])
    monkeypatch.setattr(supplier_watchdog, "tick",
                        lambda **k: {"at": "now", "checked": 0})
    scheduler._supplier_watchdog()
    assert collections["n"] >= 1


def test_the_order_image_cache_cannot_grow_without_bound():
    """Part of the leak audit: the order thumbnail index has a short TTL."""
    import api
    assert 0 < api._ORDER_IMAGE_TTL <= 300


# ------------------------------------------------------- audit: cache bounds
def test_the_signed_url_cache_is_bounded():
    """The audit's rule: an in-process cache either has a TTL or a hard cap."""
    import storage
    assert isinstance(storage._signed_url_cache, dict)
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "storage.py"), encoding="utf-8").read()
    assert "_signed_url_cache.clear()" in src, "the signed-URL cache has no cap"
    assert "len(_signed_url_cache) > 1024" in src


def test_the_object_exists_cache_is_bounded_and_ttl_checked():
    import storage
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "storage.py"), encoding="utf-8").read()
    assert storage.OBJECT_EXISTS_TTL_SECONDS > 0
    assert "len(_object_exists_cache) > 2048" in src


def test_the_broadcast_stream_table_cannot_grow_without_bound(monkeypatch):
    import api
    monkeypatch.setattr(api, "_broadcast_sent_emails", lambda cid: set())
    monkeypatch.setattr(api, "_iter_marketing_recipients", lambda **k: iter([]))
    assert api.BROADCAST_MAX_STREAMS >= 1
    api._broadcast_streams.clear()
    for n in range(api.BROADCAST_MAX_STREAMS + 3):
        api._broadcast_stream("CMP-%s" % n)
    assert len(api._broadcast_streams) <= api.BROADCAST_MAX_STREAMS
    api._broadcast_streams.clear()


def test_the_scheduler_reports_its_own_failures_without_paging(monkeypatch):
    """Self-healing must not raise an alert for a single honest restart."""
    import observability
    calls = []
    monkeypatch.setattr(observability, "record_failure",
                        lambda *a, **k: calls.append(k))
    monkeypatch.setattr(scheduler, "_alive", lambda name=None: False)
    was_started = scheduler._started.is_set()
    scheduler._started.set()
    try:
        scheduler._health["restarts"] = 0
        monkeypatch.setattr(scheduler, "_spawn", lambda *a, **k: None)
        monkeypatch.setattr(task_queue, "ensure_alive", lambda app=None: [])
        scheduler.ensure_alive()
        assert calls == [], "a single self-heal paged the owner"
        scheduler.ensure_alive()
        assert len(calls) == 1
        assert calls[0].get("notify") is False, "the repeat report must not notify"
    finally:
        scheduler._health["restarts"] = 0
        if not was_started:
            scheduler._started.clear()
