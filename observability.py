"""Crash tracing for background workers and notification pipelines.

The shop's background work runs on daemon threads inside the web dyno:
the maintenance tick (keep-alive, midnight backup, stray remirror, photo
repair), the abandoned-cart reminder worker and the fire-and-forget mail
sends. When one of those raised, the only evidence was a single stdout
line on a dyno that Render recycles - so the 20-minute watchdog could
only ever report the symptom:

    FAIL could not measure production:
    background scheduler workers are not healthy

and nobody could see *why*. Every failure now lands here with:

  * the exception type and message,
  * the full stack trace,
  * a UTC timestamp,
  * the worker's resident memory at the moment it failed (an OOM-killed
    thread looks identical to a crashed one without it),
  * the payload id the job was working on (order id, cart token,
    recipient, product id, ...),
  * the attempt number and the host/process.

Failures are written to SQLite (`job_failures`), mirrored to Supabase so
they survive the dyno, exposed on /healthz and /api/admin/job-failures,
and - when GITHUB_TOKEN is configured - reported to GitHub as a
repository dispatch so CI/Actions can raise the alert.

Nothing in this module may ever raise: it runs inside `except` blocks.
"""
from __future__ import annotations

import datetime
import json
import os
import socket
import threading
import traceback

MAX_TRACEBACK = 8000
MAX_MESSAGE = 1000
# Keep the local crash log bounded: it is a diagnostic tail, not an archive.
LOCAL_RETENTION = 500

# One in-memory ring as well, so /healthz can answer even when the database
# is the thing that is broken.
_RECENT_MAX = 25
_recent = []
_lock = threading.Lock()

# GitHub dispatch is rate limited to one report per (job, error type) per
# window, so a worker failing every tick cannot open a thousand alerts.
_GITHUB_WINDOW_SECONDS = 900
_github_last = {}


def _now_iso():
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


def memory_mb():
    """Resident set size of this process in MB (0.0 when unavailable).

    Read from /proc on Linux (Render) with a resource-module fallback, so
    this needs no extra dependency.
    """
    try:
        with open("/proc/self/statm", "r", encoding="utf-8") as fh:
            pages = int(fh.read().split()[1])
        return round(pages * (os.sysconf("SC_PAGE_SIZE") / (1024.0 * 1024.0)), 1)
    except Exception:
        pass
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KB, macOS bytes.
        return round(peak / 1024.0, 1) if peak > 1024 * 1024 else round(peak / 1024.0, 1)
    except Exception:
        return 0.0


def _host():
    try:
        return f"{socket.gethostname()}/{os.getpid()}"
    except Exception:
        return str(os.getpid())


def failure_record(job, exc, payload_id="", attempt=1, worker="", extra=None):
    """Build the crash report dict. Pure - never touches the database."""
    try:
        tb = "".join(traceback.format_exception(
            type(exc), exc, exc.__traceback__))[-MAX_TRACEBACK:]
    except Exception:
        tb = ""
    record = {
        "job": str(job or "unknown")[:120],
        "worker": str(worker or threading.current_thread().name)[:120],
        "payload_id": str(payload_id or "")[:120],
        "error_type": type(exc).__name__[:80] if exc is not None else "",
        "message": str(exc)[:MAX_MESSAGE],
        "traceback": tb,
        "rss_mb": memory_mb(),
        "attempt": int(attempt or 1),
        "host": _host(),
        "at": _now_iso(),
    }
    if extra:
        try:
            record["message"] = (record["message"] + " | " +
                                 json.dumps(extra, default=str))[:MAX_MESSAGE]
        except Exception:
            pass
    return record


def _store_local(record):
    try:
        from db import execute
        execute(
            "INSERT INTO job_failures (job, worker, payload_id, error_type, "
            "message, traceback, rss_mb, attempt, host, at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (record["job"], record["worker"], record["payload_id"],
             record["error_type"], record["message"], record["traceback"],
             record["rss_mb"], record["attempt"], record["host"], record["at"]))
        execute(
            "DELETE FROM job_failures WHERE id NOT IN "
            "(SELECT id FROM job_failures ORDER BY id DESC LIMIT ?)",
            (LOCAL_RETENTION,))
        return True
    except Exception:
        return False


def _store_remote(record):
    try:
        from supabase_store import mirror_job_failure
        return bool(mirror_job_failure(record))
    except Exception:
        return False


def _log_line(record, logger=None):
    line = ("[crash] job=%s worker=%s payload=%s error=%s rss=%sMB attempt=%s: %s"
            % (record["job"], record["worker"], record["payload_id"] or "-",
               record["error_type"], record["rss_mb"], record["attempt"],
               record["message"]))
    try:
        if logger is not None:
            logger.error(line)
            if record["traceback"]:
                logger.error("[crash] traceback for %s:\n%s",
                             record["job"], record["traceback"])
        else:
            print(line, flush=True)
            if record["traceback"]:
                print(record["traceback"], flush=True)
    except Exception:
        pass


def github_enabled():
    return bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_API_TOKEN")) \
        and bool(os.environ.get("GITHUB_REPOSITORY"))


def _report_github(record):
    """Send the crash to GitHub as a repository_dispatch event.

    `.github/workflows/crash-report.yml` listens for `jaura-crash` and opens
    or comments on the alert issue, which is how the owner is told that a
    background worker died. Best-effort: a GitHub outage must never turn a
    worker crash into a second crash.
    """
    if not github_enabled():
        return False
    if os.environ.get("FLASK_ENV", "") == "testing":
        return False
    key = (record["job"], record["error_type"])
    import time
    now = time.time()
    with _lock:
        last = _github_last.get(key, 0)
        if now - last < _GITHUB_WINDOW_SECONDS:
            return False
        _github_last[key] = now
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_API_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    client_payload = {k: record[k] for k in (
        "job", "worker", "payload_id", "error_type", "message",
        "rss_mb", "attempt", "host", "at")}
    client_payload["traceback"] = record["traceback"][-4000:]
    payload = json.dumps({"event_type": "jaura-crash",
                          "client_payload": client_payload}).encode("utf-8")
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/dispatches",
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "jaura-crash-reporter/1.0",
            },
            method="POST")
        with urllib.request.urlopen(req, timeout=8) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        try:
            print(f"[crash] GitHub dispatch skipped: {exc}", flush=True)
        except Exception:
            pass
        return False


def record_failure(job, exc, payload_id="", attempt=1, worker="",
                   logger=None, extra=None, notify=True):
    """Persist and report one background-job crash. Never raises.

    Returns the report dict so a caller can surface it (tests, /healthz).
    """
    try:
        record = failure_record(job, exc, payload_id=payload_id,
                                attempt=attempt, worker=worker, extra=extra)
    except Exception:
        return {}
    _log_line(record, logger)
    with _lock:
        _recent.append(record)
        del _recent[:-_RECENT_MAX]
    _store_local(record)
    _store_remote(record)
    if notify:
        try:
            _report_github(record)
        except Exception:
            pass
    return record


def recent(limit=10):
    """The most recent crash reports, newest first (in-memory ring)."""
    with _lock:
        rows = list(_recent)
    rows.reverse()
    return rows[:max(1, int(limit or 10))]


def recent_stored(limit=50, job=None):
    """Crash reports from the database, newest first. [] when unavailable."""
    try:
        from db import query
        limit = max(1, min(int(limit or 50), 500))
        if job:
            rows = query(
                "SELECT id, job, worker, payload_id, error_type, message, "
                "traceback, rss_mb, attempt, host, at FROM job_failures "
                "WHERE job=? ORDER BY id DESC LIMIT ?", (str(job), limit))
        else:
            rows = query(
                "SELECT id, job, worker, payload_id, error_type, message, "
                "traceback, rss_mb, attempt, host, at FROM job_failures "
                "ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
    except Exception:
        return []


def failure_count(since_iso=None):
    """How many crashes were logged (optionally since an ISO timestamp)."""
    try:
        from db import one
        if since_iso:
            row = one("SELECT COUNT(*) n FROM job_failures WHERE at >= ?",
                      (str(since_iso),))
        else:
            row = one("SELECT COUNT(*) n FROM job_failures")
        return int((row or {"n": 0})["n"] or 0)
    except Exception:
        return 0


def clear_recent():
    """Test helper: empty the in-memory ring and the dispatch rate limiter."""
    with _lock:
        _recent.clear()
        _github_last.clear()


def guard(job, payload_id="", logger=None, attempt=1, reraise=False):
    """Context manager that records any exception raised inside it.

        with observability.guard("scheduler.backup"):
            backup.run()

    The exception is swallowed by default (the worker loop keeps ticking)
    and re-raised when `reraise` is set.
    """
    return _Guard(job, payload_id=payload_id, logger=logger,
                  attempt=attempt, reraise=reraise)


class _Guard:
    def __init__(self, job, payload_id="", logger=None, attempt=1, reraise=False):
        self.job = job
        self.payload_id = payload_id
        self.logger = logger
        self.attempt = attempt
        self.reraise = reraise
        self.report = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is None:
            return False
        self.report = record_failure(self.job, exc, payload_id=self.payload_id,
                                     attempt=self.attempt, logger=self.logger)
        return not self.reraise
