"""Bounded health checks for the synchronous Flask/Gunicorn service.

Render expects an HTTP health endpoint to answer within five seconds. The
shop's production source of truth is Supabase/PostgREST; SQLite is used as the
local working database when Supabase is not configured. Remote checks run in a
single daemon probe thread so a broken HTTP connection can never pin a Gunicorn
request thread indefinitely. A timed-out probe stays marked in-flight until it
really returns, preventing repeated health checks from leaking threads.

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
    global _remote_probe_running
    if timeout is None:
        timeout = DATABASE_PING_TIMEOUT_SECONDS
    started = time.monotonic()
    with _remote_probe_lock:
        if _remote_probe_running:
            return {"ok": False, "latencyMs": round((time.monotonic() - started) * 1000),
                    "error": "previous database probe is still pending"}
        _remote_probe_running = True

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
            with _remote_probe_lock:
                _remote_probe_running = False
            completed.set()

    try:
        threading.Thread(target=probe, name="health-database-probe", daemon=True).start()
    except Exception as exc:
        with _remote_probe_lock:
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

    return ({
        "ok": ok,
        "env": Config.ENV,
        "database": {"ok": database_ok, "backend": "supabase+sqlite" if use_supabase else "sqlite",
                     "checks": databases},
        "eventLoop": event_loop,
        "background": background,
        "latencyMs": round((time.monotonic() - started) * 1000),
    }, 200 if ok else 500)
