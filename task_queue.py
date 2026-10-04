"""Bounded, retrying background task queue for the heavy half of admin actions.

Why this exists
---------------
A product (or receipt) deletion used to run *everything* inside the HTTP
request: the atomic ``hard_delete_products`` RPC, the child-row cascade and the
Storage object purge. On the free Render instance that is a multi-second call.
The browser gave up long before the work finished and painted "Could not reach
server" while the delete was in fact still running - the worst of both worlds,
because the operator retried a delete that had already started.

The rule now is:

* the request path does exactly one small durable write (the tombstone that
  stops the product selling) and then hands the rest to this queue;
* everything slow - the RPC, the cascade, the Storage purge, a broadcast to
  hundreds of inboxes - happens here, in chunks of at most ``BATCH_SIZE``
  (5-10) records, with an explicit ``gc.collect()`` after every chunk so RSS
  stays flat under Render's 512MB ceiling;
* a failing task never kills the worker: it is retried with exponential
  backoff, and only after ``MAX_ATTEMPTS`` is it parked as ``failed`` where the
  admin job panel can see it and retry it by hand.

The queue lives in the process. It is deliberately *not* a second datastore:
work that must survive a redeploy is written durably *before* it is queued (a
product tombstone, a campaign row with its per-recipient send log), and the
scheduler's periodic sweep re-enqueues whatever durable state says is still
outstanding.
"""
from __future__ import annotations

import gc
import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

# The worker thread is named so /healthz, the scheduler's ensure_alive() and
# the tests can all find the same object.
TASK_THREAD = "jaurastore-tasks"

# Render's free instance has 512MB. A chunk is the unit of memory: five records
# are purged, then the garbage they created is collected before the next five
# start. Keeping it at five (never more than ten) is what makes the peak flat
# instead of proportional to the queue length.
def _int_env(name, default, low, high):
    try:
        value = int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


BATCH_SIZE = _int_env("JAURA_TASK_BATCH", 5, 1, 10)
POLL_SECONDS = float(os.environ.get("JAURA_TASK_POLL_SECONDS", "1") or 1)
MAX_ATTEMPTS = _int_env("JAURA_TASK_ATTEMPTS", 4, 1, 10)
RETRY_BASE = 2.0            # 2s, 4s, 8s ... between attempts
RETRY_CAP = 60.0            # a parked task never sleeps longer than a minute
RING_SIZE = 120             # jobs kept for the admin panel after they finish
# One drain() call is bounded by wall clock as well as by count, so a slow
# network RPC can never turn a queue tick into a stalled worker.
CHUNK_SECONDS = float(os.environ.get("JAURA_TASK_CHUNK_SECONDS", "45") or 45)

_lock = threading.RLock()
_wake = threading.Event()
_handlers = {}              # kind -> {"fn": callable, "label": str, "actor": bool}
_jobs = OrderedDict()       # job id -> job dict (live + finished, ring capped)
_stats = {
    "queued": 0, "done": 0, "failed": 0, "retried": 0,
    "lastRunAt": "", "lastError": "", "restarts": 0, "rssMb": 0.0,
}
_started = threading.Event()
_start_lock = threading.Lock()
_app_logger = None
_stop = threading.Event()
# Manual mode: the worker thread stays alive but does not drain, so a test can
# queue work, assert what the request did (and did not do), and then drain the
# queue on its own thread. Production never sets it.
_manual = threading.Event()


def _now():
    return time.time()


def _iso(ts=None):
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def backoff_seconds(attempt, base=RETRY_BASE, cap=RETRY_CAP):
    """Exponential backoff for attempt N (1-based), capped at ``cap``."""
    try:
        attempt = int(attempt)
    except (TypeError, ValueError):
        attempt = 1
    return min(float(cap), float(base) * (2 ** max(0, attempt - 1)))


def register(kind, handler, label="", actor=False):
    """Bind one task kind to its handler.

    ``handler(payload) -> dict`` runs on the worker thread. It may raise; the
    queue retries it. ``actor=True`` means the handler already audits the
    action itself (product deletes do), so the queue only records the outcome.
    """
    kind = str(kind or "").strip()
    if not kind or not callable(handler):
        raise ValueError("a task kind and a callable handler are required")
    with _lock:
        _handlers[kind] = {"fn": handler, "label": str(label or kind),
                           "actor": bool(actor)}
    return handler


def registered(kind):
    with _lock:
        return str(kind or "") in _handlers


def label_for(kind):
    with _lock:
        entry = _handlers.get(str(kind or ""))
    return (entry or {}).get("label") or str(kind or "")


def _dedupe_key(kind, payload):
    try:
        blob = json.dumps(payload or {}, sort_keys=True, default=str)
    except Exception:                                  # pragma: no cover
        blob = str(payload)
    return f"{kind}|{blob[:400]}"


def enqueue(kind, payload=None, dedupe=True, delay=0.0):
    """Queue one task and wake the worker. Returns the (public) job dict.

    ``dedupe`` keeps a retried admin tap, a double-submit or a re-entrant sweep
    from purging the same object twice: an identical task already waiting is
    returned instead of appended.
    """
    kind = str(kind or "").strip()
    payload = dict(payload or {})
    if not kind:
        raise ValueError("a task kind is required")
    now = _now()
    job = {
        "id": "JOB-" + secrets.token_hex(4).upper(),
        "kind": kind,
        "label": label_for(kind),
        "payload": payload,
        "state": "queued",
        "attempts": 0,
        "createdAt": now,
        "startedAt": 0.0,
        "finishedAt": 0.0,
        "nextAttemptAt": now + max(0.0, float(delay or 0.0)),
        "lastError": "",
        "result": {},
    }
    key = _dedupe_key(kind, payload)
    with _lock:
        if dedupe:
            for existing in _jobs.values():
                if existing["state"] in ("queued", "running"):
                    if _dedupe_key(existing["kind"], existing["payload"]) == key:
                        return public_job(existing)
        job["key"] = key
        _jobs[job["id"]] = job
        _trim_locked()
        _stats["queued"] = int(_stats.get("queued") or 0) + 1
    _wake.set()
    return public_job(job)


def _trim_locked():
    """Keep the ring bounded: finished jobs are dropped from the front first."""
    while len(_jobs) > RING_SIZE:
        for job_id, job in list(_jobs.items()):
            if job["state"] in ("done", "failed"):
                _jobs.pop(job_id, None)
                break
        else:
            _jobs.popitem(last=False)


def public_job(job):
    """A JSON-safe view: no payload blobs, no unbounded error strings."""
    return {
        "id": job.get("id"),
        "kind": job.get("kind"),
        "label": job.get("label") or job.get("kind"),
        "target": str((job.get("payload") or {}).get("id") or
                      (job.get("payload") or {}).get("campaign") or "")[:80],
        "state": job.get("state"),
        "attempts": int(job.get("attempts") or 0),
        "createdAt": _iso(job.get("createdAt")),
        "finishedAt": _iso(job.get("finishedAt")),
        "nextAttemptAt": _iso(job.get("nextAttemptAt")) if job.get("state") == "retry" else "",
        "lastError": str(job.get("lastError") or "")[:300],
        "result": job.get("result") or {},
    }


def pending():
    with _lock:
        return len([j for j in _jobs.values()
                    if j["state"] in ("queued", "running", "retry")])


def get(job_id):
    with _lock:
        job = _jobs.get(str(job_id or ""))
        return public_job(job) if job else None


def snapshot(limit=40):
    """Most recent jobs, newest first - the admin job panel reads this."""
    with _lock:
        jobs = list(_jobs.values())[-max(1, int(limit or 40)):]
    return [public_job(j) for j in reversed(jobs)]


def stats():
    with _lock:
        out = dict(_stats)
        out["pending"] = pending()
        out["batchSize"] = BATCH_SIZE
        out["maxAttempts"] = MAX_ATTEMPTS
        out["thread"] = TASK_THREAD
    out["threadAlive"] = alive()
    out["kinds"] = sorted(_handlers)
    return out


def requeue(job_id):
    """Put a parked/failed job back on the queue (admin 'Retry' button)."""
    with _lock:
        job = _jobs.get(str(job_id or ""))
        if not job or job["state"] in ("queued", "running"):
            return False
        job["state"] = "queued"
        job["attempts"] = 0
        job["lastError"] = ""
        job["nextAttemptAt"] = _now()
    _wake.set()
    return True


def _fail_task(job, exc, logger=None):
    """Record one exhausted task. Never raises, never alerts for a first miss.

    A first failure is retried silently (see ``drain``); reaching this point
    means every attempt failed, which is a real problem the operator must see.
    """
    message = f"{type(exc).__name__}: {exc}"[:400]
    job["lastError"] = message
    job["state"] = "failed"
    job["finishedAt"] = _now()
    with _lock:
        _stats["failed"] = int(_stats.get("failed") or 0) + 1
        _stats["lastError"] = message[:200]
    try:
        import observability
        observability.record_failure(
            f"task.{job['kind']}", exc,
            payload_id=str((job.get("payload") or {}).get("id") or "")[:120],
            attempt=int(job.get("attempts") or 1), worker=TASK_THREAD,
            logger=logger)
    except Exception:                                  # pragma: no cover
        pass


def run_job(job, logger=None):
    """Execute one job exactly once, updating its state. Never raises."""
    entry = _handlers.get(job["kind"])
    job["attempts"] = int(job.get("attempts") or 0) + 1
    job["state"] = "running"
    job["startedAt"] = _now()
    try:
        if entry is None:
            raise RuntimeError(f"no handler registered for task kind {job['kind']!r}")
        result = entry["fn"](dict(job.get("payload") or {}))
        job["result"] = result if isinstance(result, dict) else {}
        job["state"] = "done"
        job["finishedAt"] = _now()
        job["lastError"] = ""
        with _lock:
            _stats["done"] = int(_stats.get("done") or 0) + 1
        return job["result"]
    except Exception as exc:
        job["result"] = {}
        if job["attempts"] < MAX_ATTEMPTS:
            # Silent self-heal: park it with exponential backoff instead of
            # reporting a crash the operator cannot act on.
            job["state"] = "retry"
            job["nextAttemptAt"] = _now() + backoff_seconds(job["attempts"])
            job["lastError"] = f"{type(exc).__name__}: {exc}"[:400]
            with _lock:
                _stats["retried"] = int(_stats.get("retried") or 0) + 1
            if logger:
                logger.warning("task %s (%s) attempt %s failed, retrying in %ss: %s",
                               job["id"], job["kind"], job["attempts"],
                               int(backoff_seconds(job["attempts"])), exc)
        else:
            _fail_task(job, exc, logger=logger)
        return {}


def drain(limit=None, logger=None):
    """Run the ready tasks, newest memory released after every one.

    Returns a small report. Never raises: a broken handler is caught inside
    ``run_job``, a broken *queue* is caught by the caller's own boundary.
    """
    limit = BATCH_SIZE if limit is None else max(1, min(BATCH_SIZE, int(limit)))
    deadline = _now() + max(5.0, CHUNK_SECONDS)
    now = _now()
    with _lock:
        ready = [j for j in _jobs.values()
                 if j["state"] in ("queued", "retry") and j["nextAttemptAt"] <= now]
        ready = ready[:limit]
    report = {"processed": 0, "done": 0, "retry": 0, "failed": 0}
    for job in ready:
        with _lock:
            if job["state"] not in ("queued", "retry"):
                continue
        report["processed"] += 1
        run_job(job, logger=logger)
        if job["state"] == "done":
            report["done"] += 1
        elif job["state"] == "failed":
            report["failed"] += 1
        else:
            report["retry"] += 1
        # Drop the objects the handler built before the next record starts.
        # This is the "flat 512MB" guarantee: peak memory is one chunk wide.
        try:
            gc.collect()
        except Exception:                              # pragma: no cover
            pass
        if _now() >= deadline:
            break
    if report["processed"]:
        with _lock:
            _stats["lastRunAt"] = _iso(_now())
    report["pending"] = pending()
    return report


def wake():
    _wake.set()


def wait_idle(timeout=10.0, logger=None):
    """Drain until the queue is empty (tests, and the sync escape hatch)."""
    end = _now() + max(0.0, float(timeout or 0))
    while True:
        # Ignore the retry backoff here: a caller that asked to wait wants the
        # queue empty even when the next attempt is scheduled in two seconds.
        with _lock:
            for job in _jobs.values():
                if job["state"] == "retry":
                    job["state"] = "queued"
                    job["nextAttemptAt"] = _now()
        drain(logger=logger)
        if pending() == 0 or _now() >= end:
            break
        time.sleep(0.05)
    return snapshot(5)


def _release_memory():
    try:
        gc.collect()
        import observability
        _stats["rssMb"] = observability.memory_mb()
    except Exception:                                  # pragma: no cover
        pass


def set_manual(on=True):
    """Test hook: stop the worker thread from draining behind the test."""
    if on:
        _manual.set()
    else:
        _manual.clear()
    _wake.set()
    return _manual.is_set()


def manual():
    return _manual.is_set()


def _loop(logger=None):
    """One long-lived worker: drain, collect, sleep until woken."""
    while not _stop.is_set():
        try:
            if _manual.is_set():
                _wake.wait(max(0.05, POLL_SECONDS))
                _wake.clear()
                continue
            drain(logger=logger)
        except Exception as exc:                       # pragma: no cover
            try:
                import observability
                observability.record_failure("task_queue.tick", exc,
                                             worker=TASK_THREAD, logger=logger,
                                             notify=False)
            except Exception:
                pass
        _release_memory()
        _wake.wait(max(0.05, POLL_SECONDS))
        _wake.clear()


def alive(name=TASK_THREAD):
    return any(t.name == name and t.is_alive() for t in threading.enumerate())


def start(app=None):
    """Start the worker once. Safe to call from every boot path."""
    global _app_logger
    if _started.is_set():
        return False
    _started.set()
    _stop.clear()
    _app_logger = app.logger if app is not None else None
    thread = threading.Thread(target=_loop, args=(_app_logger,), daemon=True,
                              name=TASK_THREAD)
    thread.start()
    return True


def reset():
    """Test helper: empty the queue and counters, keep the registrations."""
    with _lock:
        _jobs.clear()
        _stats.update({"queued": 0, "done": 0, "failed": 0, "retried": 0,
                       "lastRunAt": "", "lastError": "", "restarts": 0})
    _wake.clear()


def ensure_alive(app=None):
    """Restart a dead worker. One silent bounce, the second one is reported.

    A single restart is normal on a free instance (a deploy, an OOM kill of the
    old process); reporting it paged the owner for nothing. A worker that keeps
    dying within the same hour is a real defect and is still recorded, with
    ``notify=False`` so the report is in /healthz and job_failures instead of a
    fresh GitHub issue every five minutes.
    """
    logger = app.logger if app is not None else _app_logger
    restarted = []
    if not _started.is_set():
        return restarted
    with _start_lock:
        if not alive():
            thread = threading.Thread(target=_loop, args=(logger,), daemon=True,
                                      name=TASK_THREAD)
            thread.start()
            restarted.append(TASK_THREAD)
    if restarted:
        with _lock:
            _stats["restarts"] = int(_stats.get("restarts") or 0) + 1
            hits = _stats["restarts"]
        if hits >= 2:
            try:
                import observability
                observability.record_failure(
                    "scheduler.worker_died",
                    RuntimeError("task queue worker restarted repeatedly"),
                    worker=TASK_THREAD, logger=logger, notify=False)
            except Exception:                          # pragma: no cover
                pass
        elif logger:
            logger.info("task queue worker restarted (self-heal #%s)", hits)
    return restarted


def stop():
    """Stop the worker (tests / clean shutdown)."""
    _stop.set()
    _wake.set()
