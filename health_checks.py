"""Bounded health checks for the synchronous Flask/Gunicorn service.

Render expects an HTTP health endpoint to answer within five seconds. The
shop's production source of truth is Supabase/PostgREST; SQLite is used as the
local working database when Supabase is not configured. Remote checks run in a
single daemon probe thread so a broken HTTP connection can never pin a Gunicorn
request thread indefinitely. A timed-out probe stays marked in-flight until it
really returns, so repeated health checks cannot pile up threads; that guard is
itself bounded by PROBE_STALE_AFTER_SECONDS, because a probe that never returns
must not be able to answer "previous database probe is still pending" forever
and hold /healthz down long after the underlying blip has passed.

Flask here is WSGI and has no persistent asyncio event loop. ``eventLoop`` in
the response therefore measures whether the Python request runtime can
schedule and run a lightweight worker promptly; if this application later
adds a long-lived asyncio loop, its own loop lag should be added here too.
"""
import sqlite3
import threading
import time

from config import Config

EVENT_LOOP_TIMEOUT_SECONDS = 0.25
DATABASE_PING_TIMEOUT_SECONDS = 3.75
SQLITE_CONNECT_TIMEOUT_SECONDS = 0.25

_remote_probe_lock = threading.Lock()
_remote_probe_running = False
# Generation id of the in-flight probe. A probe thread only clears the guard
# when it is still the current one, so a probe that was given up on cannot
# later clear the guard belonging to its replacement.
_remote_probe_token = 0
# Monotonic start of the in-flight probe, used by the staleness bound below.
_remote_probe_started = 0.0

# Upper bound on how long an in-flight probe may keep new health checks
# waiting behind it. Well above DATABASE_PING_TIMEOUT_SECONDS - the underlying
# HTTP request is itself bounded (supabase_store.SUPABASE_HTTP_TIMEOUT_SECONDS)
# so a healthy-but-slow probe never reaches this - while still guaranteeing the
# endpoint recovers on its own if something hangs outside that timeout's reach.
PROBE_STALE_AFTER_SECONDS = 30.0

# ---------------------------------------------------------------- liveness
# /healthz is Render's health-check path AND the endpoint the 15-minute
# UptimeRobot ping hits. It must answer in milliseconds without touching the
# network, allocating a buffer or waiting on the filesystem, so the expensive
# facts are measured elsewhere and read from memory here:
#
#   * the runtime + SQLite checks are cheap (~1 ms) and cached for one second;
#   * the Supabase verdict is refreshed by a background daemon at most every
#     READINESS_TTL_SECONDS, and this endpoint only reads the last one.
#
# A failed Supabase streak shorter than HEALTHZ_FAIL_STREAK is reported as
# ``degraded`` but still answers 200: one slow query must not make Render kill
# an otherwise healthy instance. After three consecutive failures (roughly 30
# seconds of a genuinely broken database) the probe answers 503, which is what
# lets Render recycle the process without anyone pressing "Restart Service".
LIVENESS_CACHE_SECONDS = 1.0
READINESS_TTL_SECONDS = 10.0
HEALTHZ_FAIL_STREAK = 3

_liveness_lock = threading.Lock()
_liveness = {"at": 0.0, "payload": None}
_readiness_lock = threading.Lock()
_readiness = {"ok": None, "at": 0.0, "checkedAt": 0.0, "error": "", "streak": 0}
_readiness_inflight = False
_STARTED_AT = time.time()


def _event_loop_probe(timeout=EVENT_LOOP_TIMEOUT_SECONDS):
    """Check that the service can schedule work off the request thread."""
    started = time.monotonic()
    done = threading.Event()
    try:
        thread = threading.Thread(
            target=done.set, name="health-event-loop-probe", daemon=True)
        thread.start()
    except Exception as exc:
        return {"ok": False, "latencyMs": round((time.monotonic() - started) * 1000),
                "error": str(exc)[:160]}
    ok = done.wait(max(0.01, float(timeout)))
    latency = round((time.monotonic() - started) * 1000)
    return {"ok": bool(ok), "latencyMs": latency,
            **({} if ok else {"error": "runtime scheduling probe timed out"})}


def _sqlite_ping():
    """Open the configured local SQLite file and execute a bounded SELECT 1."""
    started = time.monotonic()
    conn = None
    try:
        conn = sqlite3.connect(
            Config.DB_PATH, timeout=SQLITE_CONNECT_TIMEOUT_SECONDS,
            check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=250")
        value = conn.execute("SELECT 1").fetchone()
        if not value or value[0] != 1:
            raise RuntimeError("SQLite health query returned an unexpected value")
        return {"ok": True, "latencyMs": round((time.monotonic() - started) * 1000)}
    except Exception as exc:
        return {"ok": False, "latencyMs": round((time.monotonic() - started) * 1000),
                "error": str(exc)[:160]}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _supabase_ping(timeout=None):
    """Ping PostgREST with a hard response deadline and bounded thread count."""
    global _remote_probe_running, _remote_probe_token, _remote_probe_started
    if timeout is None:
        timeout = DATABASE_PING_TIMEOUT_SECONDS
    started = time.monotonic()
    with _remote_probe_lock:
        if _remote_probe_running:
            age = time.monotonic() - _remote_probe_started
            if age < max(0.0, float(PROBE_STALE_AFTER_SECONDS)):
                return {"ok": False, "latencyMs": round((time.monotonic() - started) * 1000),
                        "error": "previous database probe is still pending"}
            # The in-flight probe has outlived every deadline it was ever
            # given, so it is treated as abandoned rather than as a reason to
            # keep failing. Python cannot kill the thread; it is simply
            # disowned, and its own finally clause will not touch the guard
            # any more because the token below has moved on.
        _remote_probe_running = True
        _remote_probe_token += 1
        token = _remote_probe_token
        _remote_probe_started = time.monotonic()

    completed = threading.Event()
    outcome = {}

    def probe():
        global _remote_probe_running
        try:
            import supabase_store
            result = supabase_store.ping()
            outcome["result"] = result
            outcome["ok"] = result == "ok"
            if result != "ok":
                outcome["error"] = "Supabase/PostgreSQL ping returned " + str(result)
        except Exception as exc:
            outcome["ok"] = False
            outcome["error"] = str(exc)[:160]
        finally:
            # Runs on every exit path - success, exception, or a slow return
            # after the caller already gave up - so the guard cannot be left
            # set by this probe. Only this probe's own generation is cleared.
            with _remote_probe_lock:
                if _remote_probe_token == token:
                    _remote_probe_running = False
            completed.set()

    try:
        threading.Thread(target=probe, name="health-database-probe", daemon=True).start()
    except Exception as exc:
        with _remote_probe_lock:
            if _remote_probe_token == token:
                _remote_probe_running = False
        return {"ok": False, "latencyMs": round((time.monotonic() - started) * 1000),
                "error": ("could not start database probe: " + str(exc))[:160]}

    if not completed.wait(max(0.01, float(timeout))):
        return {"ok": False, "latencyMs": round((time.monotonic() - started) * 1000),
                "error": "Supabase/PostgreSQL ping timed out"}
    return {"ok": bool(outcome.get("ok")),
            "latencyMs": round((time.monotonic() - started) * 1000),
            **({} if outcome.get("ok") else {"error": outcome.get("error", "database ping failed")})}


def health_report():
    """Return the public health document and the corresponding HTTP status.

    The remote and local database checks are deliberately not cached: every
    Render probe must observe current connectivity. The response target is
    below Render's five-second health-check deadline, leaving time for the
    HTTP response itself after a remote database timeout.
    """
    started = time.monotonic()
    event_loop = _event_loop_probe()
    sqlite = _sqlite_ping()
    try:
        import supabase_store
        use_supabase = bool(supabase_store.enabled())
    except Exception:
        use_supabase = False

    remote = _supabase_ping() if use_supabase else None
    databases = {"sqlite": sqlite}
    if use_supabase:
        databases["supabase"] = remote
    database_ok = sqlite["ok"] and (not use_supabase or bool(remote and remote.get("ok")))
    ok = bool(database_ok and event_loop.get("ok"))

    background = None
    if Config.SCHEDULER_ENABLED and Config.ENV != "testing":
        try:
            import scheduler
            background = scheduler.health_snapshot()
        except Exception as exc:
            background = {"started": False, "backgroundAlive": False,
                          "error": str(exc)[:160]}

    # Notification queue visibility: a send still in flight is normal, but a
    # number that never returns to zero means the provider is hanging. It is
    # informational only - it never fails the health check, because a slow
    # mail provider must not take the shop out of rotation.
    mail = None
    try:
        import mailer
        mail = {"pending": mailer.pending_sends()}
    except Exception:
        mail = None

    return ({
        "ok": ok,
        "env": Config.ENV,
        "database": {"ok": database_ok, "backend": "supabase+sqlite" if use_supabase else "sqlite",
                     "checks": databases},
        "eventLoop": event_loop,
        "background": background,
        "mail": mail,
        "latencyMs": round((time.monotonic() - started) * 1000),
    }, 200 if ok else 500)

def reset_caches():
    """Forget cached liveness/readiness observations (tests, after a change)."""
    global _readiness_inflight
    with _liveness_lock:
        _liveness["payload"] = None
        _liveness["at"] = 0.0
    with _readiness_lock:
        _readiness.update({"ok": None, "at": 0.0, "checkedAt": 0.0,
                           "error": "", "streak": 0})
        _readiness_inflight = False


def _supabase_configured():
    try:
        import supabase_store
        return bool(supabase_store.enabled())
    except Exception:
        return False


def _liveness_snapshot():
    """The cheap half of health, recomputed at most once a second."""
    now = time.monotonic()
    with _liveness_lock:
        cached = _liveness.get("payload")
        if cached is not None and (now - float(_liveness.get("at") or 0)) < LIVENESS_CACHE_SECONDS:
            return cached
    payload = {"eventLoop": _event_loop_probe(), "sqlite": _sqlite_ping(), "at": time.time()}
    with _liveness_lock:
        _liveness["payload"] = payload
        _liveness["at"] = time.monotonic()
    return payload


def refresh_readiness(force=False):
    """Refresh the Supabase verdict in the background (never blocks the caller).

    One daemon thread at a time; a probe already in flight is reused. The
    scheduler's ``ensure_alive()`` runs here too, so a dead background worker
    is restarted on the same cadence without depending on the 5-minute tick.
    """
    global _readiness_inflight
    now = time.monotonic()
    with _readiness_lock:
        stale = _readiness.get("ok") is None or (now - float(_readiness.get("at") or 0)) > READINESS_TTL_SECONDS
        if _readiness_inflight or (not force and not stale):
            return False
        if not _supabase_configured():
            _readiness.update({"ok": True, "at": now, "checkedAt": time.time(),
                               "error": "", "streak": 0})
            return False
        _readiness_inflight = True

    def run():
        global _readiness_inflight
        try:
            outcome = _supabase_ping()
        except Exception as exc:                   # pragma: no cover - defensive
            outcome = {"ok": False, "error": str(exc)[:160]}
        try:
            import scheduler
            scheduler.ensure_alive()
        except Exception:
            pass
        with _readiness_lock:
            ok = bool(outcome.get("ok"))
            _readiness["ok"] = ok
            _readiness["at"] = time.monotonic()
            _readiness["checkedAt"] = time.time()
            _readiness["error"] = "" if ok else str(outcome.get("error") or "database ping failed")[:160]
            _readiness["streak"] = 0 if ok else int(_readiness.get("streak") or 0) + 1
            _readiness_inflight = False

    try:
        threading.Thread(target=run, name="health-readiness", daemon=True).start()
    except Exception:                              # pragma: no cover - defensive
        with _readiness_lock:
            _readiness_inflight = False
        return False
    return True


def _iso(epoch):
    if not epoch:
        return ""
    try:
        import datetime
        return datetime.datetime.utcfromtimestamp(float(epoch)).isoformat(timespec="seconds") + "Z"
    except Exception:
        return ""


def quick_background():
    """Background-worker liveness without the scheduler's full report.

    ``health_snapshot()`` reads the failure table and may restart a worker;
    both are wrong on a <50 ms probe. This only asks ``threading.enumerate``,
    and mirrors the old report's ``None`` when the scheduler is off (tests,
    one-off CLI runs) so the documented shape does not change.
    """
    if not Config.SCHEDULER_ENABLED or Config.ENV == "testing":
        return None
    try:
        import scheduler
        alive = bool(scheduler._alive())
        return {
            **dict(getattr(scheduler, "_health", {}) or {}),
            "started": bool(scheduler._started.is_set()),
            "backgroundAlive": alive,
            # Kept for the watchdog and the admin panel, which still read the
            # two pre-consolidation worker names.
            "maintenanceAlive": alive,
            "remindersAlive": alive,
            "supplierAlive": alive,
            "threadName": scheduler.BACKGROUND_THREAD,
            "intervalSeconds": scheduler.TICK_SECONDS,
            # Deliberately omitted here (they are DB reads, and this probe is
            # not allowed to do I/O): the full report on /health still has
            # them.
            "recentFailures": [],
        }
    except Exception:
        return {"started": False, "backgroundAlive": False,
                "maintenanceAlive": False, "remindersAlive": False,
                "supplierAlive": False}


def liveness_report():
    """The <50 ms probe: no network, no threads we spawn, no scheduler work.

    Keeps the keys the watchdog and Render already read (``database.ok``,
    ``database.checks.sqlite``, ``eventLoop``, ``background``) so nothing
    downstream has to change, while the expensive Supabase round trip moves to
    a background refresher. ``/health`` and ``/api/health`` keep the full,
    synchronous report for humans and for the watchdog's detail.
    """
    started = time.monotonic()
    snap = _liveness_snapshot()
    configured = _supabase_configured()
    refresh_readiness()
    with _readiness_lock:
        readiness = dict(_readiness)
    streak = int(readiness.get("streak") or 0)
    verdict = readiness.get("ok")
    if not configured:
        db_ok = True
    elif verdict is False and streak >= HEALTHZ_FAIL_STREAK:
        db_ok = False
    else:
        db_ok = True
    ok = bool(snap["eventLoop"].get("ok") and snap["sqlite"].get("ok") and db_ok)
    try:
        import memory_manager
        memory = memory_manager.stats()
    except Exception:
        memory = {}
    body = {
        "ok": ok,
        "live": True,
        "env": Config.ENV,
        "uptimeSeconds": round(time.time() - _STARTED_AT, 1),
        "database": {
            "ok": db_ok,
            "backend": "supabase+sqlite" if configured else "sqlite",
            "cached": True,
            "checkedAt": _iso(readiness.get("checkedAt")),
            "streak": streak,
            "degraded": bool(verdict is False and db_ok),
            "error": readiness.get("error") or "",
            "checks": {
                # SQLite is cheap enough to sample inline (cached 1s); the
                # Supabase verdict is the last background observation.
                "sqlite": snap["sqlite"],
                "supabase": {"ok": verdict, "cached": True,
                             "checkedAt": _iso(readiness.get("checkedAt")),
                             "error": readiness.get("error") or ""},
            },
        },
        "eventLoop": snap["eventLoop"],
        "background": quick_background(),
        "memory": memory,
        "latencyMs": round((time.monotonic() - started) * 1000, 2),
    }
    return body
