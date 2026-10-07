"""The background task queue: the fix for "Could not reach server" on deletes.

An admin delete used to run its whole Supabase half - the atomic RPC, the child
cascade, the Storage purge - inside the HTTP request. A product with a gallery
made that a multi-second call, the browser gave up first, and the operator
retried a delete that was already running.

The contract these tests pin:

  * the request path does one small durable write (the tombstone) and then
    answers 200 + ``deleteMode: "queued"`` + a ``jobId`` - it never waits for
    the RPC, however slow the RPC is;
  * the heavy half runs on the queue in chunks of at most ``BATCH_SIZE``
    (5-10) records, with an explicit ``gc.collect()`` after every chunk, so RSS
    stays flat on the 512MB instance;
  * a failing task retries with exponential backoff and is only parked (with
    its reason, visible in the job panel) once every attempt is spent;
  * a dead worker is restarted silently; only a worker that keeps dying is
    recorded, and even then without paging anyone.

Run with:  python3 -m pytest tests/test_async_deletions.py -q
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

import task_queue  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def isolated_queue():
    """Every test gets an empty queue and no worker racing its assertions."""
    task_queue.reset()
    task_queue.set_manual(True)
    yield
    task_queue.set_manual(False)
    task_queue.reset()


# ---------------------------------------------------------------- the contract
def test_the_chunk_size_stays_between_five_and_ten_records():
    assert 5 <= task_queue.BATCH_SIZE <= 10, task_queue.BATCH_SIZE


def test_a_drain_never_runs_more_than_one_chunk():
    done = []
    task_queue.register("test.chunk", lambda payload: done.append(payload["n"]) or {})
    try:
        for n in range(12):
            task_queue.enqueue("test.chunk", {"n": n}, dedupe=False)
        report = task_queue.drain()
        assert report["processed"] == task_queue.BATCH_SIZE
        assert len(done) == task_queue.BATCH_SIZE
        assert task_queue.pending() == 12 - task_queue.BATCH_SIZE
        # the rest goes out on the next pass, not in one giant loop
        task_queue.drain()
        assert task_queue.pending() == 12 - (2 * task_queue.BATCH_SIZE)
    finally:
        task_queue._handlers.pop("test.chunk", None)


def test_every_chunk_is_followed_by_a_garbage_collection(monkeypatch):
    calls = {"n": 0}
    real = task_queue.gc.collect

    def counting_collect(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(task_queue.gc, "collect", counting_collect)
    task_queue.register("test.gc", lambda payload: {})
    try:
        for n in range(3):
            task_queue.enqueue("test.gc", {"n": n}, dedupe=False)
        task_queue.drain()
        assert calls["n"] >= 3, "a chunk finished without releasing its memory"
    finally:
        task_queue._handlers.pop("test.gc", None)


def test_an_identical_task_is_not_queued_twice():
    task_queue.register("test.dedupe", lambda payload: {})
    try:
        first = task_queue.enqueue("test.dedupe", {"id": "same"})
        second = task_queue.enqueue("test.dedupe", {"id": "same"})
        assert first["id"] == second["id"]
        assert task_queue.pending() == 1
        third = task_queue.enqueue("test.dedupe", {"id": "other"})
        assert third["id"] != first["id"]
        assert task_queue.pending() == 2
    finally:
        task_queue._handlers.pop("test.dedupe", None)


def test_the_job_ring_stays_bounded():
    task_queue.register("test.ring", lambda payload: {})
    try:
        for n in range(task_queue.RING_SIZE + 60):
            task_queue.enqueue("test.ring", {"n": n}, dedupe=False)
        assert len(task_queue._jobs) <= task_queue.RING_SIZE
    finally:
        task_queue._handlers.pop("test.ring", None)


def test_an_unknown_task_kind_fails_visibly_and_cannot_kill_the_worker():
    job = task_queue.enqueue("test.nothing-registered", {"id": "x"})
    report = task_queue.drain()
    assert report["processed"] == 1
    parked = task_queue.get(job["id"])
    assert parked["state"] in ("retry", "failed")
    assert "handler" in parked["lastError"].lower()
    # the worker is still usable afterwards
    task_queue.register("test.after", lambda payload: {"ok": True})
    try:
        good = task_queue.enqueue("test.after", {"id": "y"})
        task_queue.drain()
        assert task_queue.get(good["id"])["state"] == "done"
    finally:
        task_queue._handlers.pop("test.after", None)


# ------------------------------------------------------------------- retrying
def test_the_backoff_is_exponential_and_capped():
    assert task_queue.backoff_seconds(1) == 2.0
    assert task_queue.backoff_seconds(2) == 4.0
    assert task_queue.backoff_seconds(3) == 8.0
    assert task_queue.backoff_seconds(9) == task_queue.RETRY_CAP


def test_a_failing_task_backs_off_then_parks_with_its_reason():
    task_queue.register("test.boom", _always_boom)
    try:
        job = task_queue.enqueue("test.boom", {"id": "boom"})
        delays = []
        for expected_attempt in range(1, task_queue.MAX_ATTEMPTS + 1):
            current = task_queue._jobs[job["id"]]
            current["nextAttemptAt"] = 0            # ignore the wait in the test
            task_queue.drain()
            current = task_queue._jobs[job["id"]]
            if current["state"] == "retry":
                delays.append(round(current["nextAttemptAt"] - time.time()))
        assert delays == [2, 4, 8][:task_queue.MAX_ATTEMPTS - 1], delays
        parked = task_queue.get(job["id"])
        assert parked["state"] == "failed"
        assert parked["attempts"] == task_queue.MAX_ATTEMPTS
        assert "boom" in parked["lastError"]
        assert task_queue.stats()["failed"] >= 1
    finally:
        task_queue._handlers.pop("test.boom", None)


def _always_boom(payload):
    raise RuntimeError("boom")


def test_a_parked_task_can_be_retried_by_hand():
    task_queue.register("test.park", _always_boom)
    try:
        job = task_queue.enqueue("test.park", {"id": "p"})
        for _ in range(task_queue.MAX_ATTEMPTS):
            task_queue._jobs[job["id"]]["nextAttemptAt"] = 0
            task_queue.drain()
        assert task_queue.get(job["id"])["state"] == "failed"
        assert task_queue.requeue(job["id"]) is True
        assert task_queue.get(job["id"])["state"] == "queued"
        assert task_queue.get(job["id"])["attempts"] == 0
        assert task_queue.requeue("JOB-NOPE") is False
    finally:
        task_queue._handlers.pop("test.park", None)


# -------------------------------------------------------------- worker liveness
def test_start_is_idempotent_and_the_thread_is_a_named_daemon():
    was_started = task_queue._started.is_set()
    try:
        task_queue._started.clear()
        assert task_queue.start() is True
        assert task_queue.start() is False
        worker = [t for t in threading.enumerate() if t.name == task_queue.TASK_THREAD]
        assert worker, "the worker thread is not running"
        assert worker[0].daemon, "a non-daemon worker would block shutdown"
        assert task_queue.alive()
        assert task_queue.stats()["threadAlive"] is True
    finally:
        if not was_started:
            task_queue.stop()
            task_queue._started.clear()


def test_a_dead_worker_self_heals_silently_and_only_a_repeat_is_recorded(
        monkeypatch):
    import observability
    recorded = []
    monkeypatch.setattr(observability, "record_failure",
                        lambda *a, **k: recorded.append(a[0]) if a else None)
    monkeypatch.setattr(task_queue, "alive", lambda name=task_queue.TASK_THREAD: False)
    was_started = task_queue._started.is_set()
    task_queue._started.set()
    try:
        before = int(task_queue.stats()["restarts"] or 0)
        assert task_queue.ensure_alive() == [task_queue.TASK_THREAD]
        assert not recorded, "a single self-heal must not be reported as a crash"
        assert task_queue.ensure_alive() == [task_queue.TASK_THREAD]
        assert recorded == ["scheduler.worker_died"], recorded
        assert task_queue.stats()["restarts"] == before + 2
    finally:
        if not was_started:
            task_queue._started.clear()
        task_queue._stats["restarts"] = 0


def test_ensure_alive_does_nothing_before_the_queue_is_started(monkeypatch):
    was_started = task_queue._started.is_set()
    task_queue._started.clear()
    try:
        monkeypatch.setattr(task_queue, "alive", lambda name=task_queue.TASK_THREAD: False)
        assert task_queue.ensure_alive() == []
    finally:
        if was_started:
            task_queue._started.set()


# ------------------------------------------------------- the HTTP-path budget
def test_the_delete_request_never_waits_for_a_slow_hard_delete(
        client, monkeypatch):
    """The regression that mattered: a slow RPC must not slow the request.

    ``?sync=1`` is the control - it must still block for the whole RPC, which
    proves the RPC really is slow in this test and that the async path is the
    thing that stopped waiting for it.
    """
    from config import Config
    import supabase_store
    from _pw import PW as ADMIN_PW

    monkeypatch.setattr(Config, "ENV", "production")
    monkeypatch.setattr(Config, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake")
    monkeypatch.setattr(supabase_store, "add_deleted_id", lambda pid: True)
    slept = []

    def _slow(ids):
        slept.append(list(ids))
        time.sleep(1.2)
        return {"deleted": list(ids), "files": 0, "errors": []}

    monkeypatch.setattr(supabase_store, "hard_delete_products", _slow)

    from db import execute, init_db
    init_db()
    execute("DELETE FROM rate_limits")
    with client.test_client() as c:
        r = c.post("/api/admin/login",
                   json={"email": "jaurastore@gmail.com", "password": ADMIN_PW,
                         "recaptcha": ""})
        assert r.status_code == 200, r.data
        tok = r.get_json()["csrf"]

        started = time.monotonic()
        fast = c.delete("/api/admin/products/jau-fast?queued=1",
                        headers={"X-CSRF-Token": tok})
        elapsed_fast = time.monotonic() - started
        assert fast.status_code == 200, fast.data
        assert fast.get_json()["deleteMode"] == "queued"
        assert elapsed_fast < 0.5, f"the request waited {elapsed_fast:.2f}s"
        assert slept == [], "the RPC ran inside the request"

        started = time.monotonic()
        slow = c.delete("/api/admin/products/jau-slow?sync=1",
                        headers={"X-CSRF-Token": tok})
        elapsed_slow = time.monotonic() - started
        assert slow.status_code == 200, slow.data
        assert elapsed_slow >= 1.0, elapsed_slow
        assert slept == [["jau-slow"]]


@pytest.fixture()
def client():
    import app as appmod
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


def test_the_runbook_documents_both_delete_modes():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "STAGING_HARD_DELETE_RUNBOOK.md"),
              encoding="utf-8") as fh:
        text = fh.read()
    assert '"queued"' in text
    assert "?sync=1" in text
    assert "supabase-hard" in text
    assert "Background jobs" in text
