"""Free-tier memory hygiene for the single 512 MB Render worker.

The service runs one gunicorn worker with eight threads, and the instance is
kept awake by a 15-minute external ping plus the in-process scheduler. An
instance that never sleeps is exactly when a slow leak gets to matter, so this
module bounds the two allocations that have no natural ceiling:

  * **Request transients.** Pillow buffers, decoded image bytes, parsed JSON
    bodies and response copies die with the request, but only if the cyclic
    garbage collector runs. Each shop request now runs a bounded
    ``gc.collect(1)`` on ``teardown_request`` - after the view and after every
    ``after_request`` handler, so the response is already built and the
    shopper never waits on the collector - and a full ``gc.collect()`` when
    the request carried an upload.

  * **Idle database pools and caches.** The Supabase clients hold httpx
    connection pools, and ``supabase_store`` keeps a 10-second read cache plus
    ``api`` a serialized catalogue snapshot (~600 KB). When nobody has touched
    the shop for ``SUPABASE_POOL_IDLE_SECONDS``, the pools are closed and both
    caches are dropped, then ``malloc_trim`` hands the freed pages back to the
    OS. The next request simply rebuilds them.

Design rules:

  * Nothing here runs on ``/healthz``. The liveness probe must not allocate,
    collect, block or touch the network - it is the one endpoint Render uses to
    decide whether the process is alive.
  * Collection is throttled (``JAURA_GC_INTERVAL`` seconds) so a burst of
    requests cannot spend its time in the collector.
  * Every routine is best-effort: a failure to trim memory must never become a
    failed request.

Wiring: ``install(app)`` from ``app.create_app`` and ``idle_tick()`` from the
scheduler's five-minute loop (which also keeps working when no HTTP traffic
arrives, e.g. overnight).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import gc
import os
import sys
import threading
import time

# How long the shop may sit untouched before its database clients and caches
# are recycled. Ten minutes: long enough that an ordinary browsing session
# never pays for a reconnect, short enough that an idle overnight instance
# holds no pool at all.
IDLE_SECONDS = float(os.environ.get("SUPABASE_POOL_IDLE_SECONDS", "600") or 600)

# Minimum spacing between request-triggered collections. A collection costs
# ~0.2 ms at generation 1; doing it on every request of a 24-photo grid would
# be wasted work, and doing it never is how RSS creeps.
GC_INTERVAL_SECONDS = float(os.environ.get("JAURA_GC_INTERVAL", "2.0") or 2.0)

# Paths whose requests never allocate meaningfully. The health probes are here
# deliberately: the liveness endpoint promises zero memory work.
_SKIP_PREFIXES = ("/healthz", "/health", "/api/health")
_SKIP_SUFFIXES = (".css", ".js", ".mjs", ".map", ".png", ".jpg", ".jpeg",
                  ".webp", ".gif", ".svg", ".ico", ".woff", ".woff2",
                  ".ttf", ".otf", ".mp4", ".webm", ".json")

_lock = threading.RLock()
_state = {
    "startedAt": time.time(),
    "requests": 0,
    "collections": 0,
    "fullCollections": 0,
    "recycles": 0,
    "lastCollectionAt": 0.0,
    "lastRecycleAt": 0.0,
    "lastRecycleReason": "",
    "lastTrimmedBytes": 0,
    "inFlight": 0,
    "lastActivity": time.monotonic(),
    "enabled": True,
}

_libc = None
_libc_checked = False


def _malloc_trim():
    """Return freed heap pages to the OS (glibc only), best-effort.

    Python's allocator keeps arenas after a collection; on a 512 MB instance
    that retained-but-unused memory is what makes the next upload look like a
    leak. ``malloc_trim(0)`` is a no-op where it is unavailable (musl, macOS),
    which is why this never raises.
    """
    global _libc, _libc_checked
    if not _libc_checked:
        _libc_checked = True
        try:
            name = ctypes.util.find_library("c") or "libc.so.6"
            _libc = ctypes.CDLL(name)
            if not hasattr(_libc, "malloc_trim"):
                _libc = None
        except Exception:
            _libc = None
    if _libc is None:
        return 0
    try:
        return int(_libc.malloc_trim(0) or 0)
    except Exception:
        return 0


_RSS_TTL_SECONDS = 5.0
_rss_cache = {"at": 0.0, "value": 0.0}


def _rss_mb():
    """RSS in MB, cached for five seconds.

    ``/healthz`` reports this on every probe. Reading ``/proc/self/statm`` is
    cheap but not free (a file open), and the value cannot change meaningfully
    inside a burst of probes - so cache it, and the liveness path allocates
    nothing at all.
    """
    now = time.monotonic()
    with _lock:
        if _rss_cache["at"] and (now - float(_rss_cache["at"])) < _RSS_TTL_SECONDS:
            return float(_rss_cache["value"])
    try:
        import observability
        value = float(observability.memory_mb() or 0.0)
    except Exception:
        value = 0.0
    with _lock:
        _rss_cache["at"] = now
        _rss_cache["value"] = value
    return value


def note_request():
    """Record an inbound request (cheap: one float write)."""
    with _lock:
        _state["requests"] = int(_state["requests"]) + 1
        _state["inFlight"] = int(_state["inFlight"]) + 1
        _state["lastActivity"] = time.monotonic()


def note_response(*, full=False):
    """Record the end of a request; one caller per request."""
    with _lock:
        _state["inFlight"] = max(0, int(_state["inFlight"]) - 1)
        _state["lastActivity"] = time.monotonic()
    # A routine request sweeps generation 1 (throttled); an upload's Pillow and
    # multipart transients are old enough to justify the full sweep.
    collect(full=full)


def collect(full=False):
    """Run one bounded collection, throttled except for ``full`` sweeps."""
    now = time.monotonic()
    with _lock:
        if not full and (now - float(_state["lastCollectionAt"] or 0)) < GC_INTERVAL_SECONDS:
            return False
        _state["lastCollectionAt"] = now
        _state["collections"] = int(_state["collections"]) + 1
        if full:
            _state["fullCollections"] = int(_state["fullCollections"]) + 1
    try:
        # Generation 1 over generation 2: request transients are almost always
        # young objects, and a full sweep on every request would be a visible
        # pause. The idle path below asks for the full sweep.
        gc.collect(2 if full else 1)
        return True
    except Exception:
        return False


def _skip_path(path):
    p = str(path or "")
    if any(p.startswith(prefix) for prefix in _SKIP_PREFIXES):
        return True
    return p.lower().endswith(_SKIP_SUFFIXES)


def install(app):
    """Hook the request lifecycle. Idempotent and always safe to call."""
    if getattr(app, "_ja_memory_installed", False):
        return False
    app._ja_memory_installed = True

    @app.before_request
    def _ja_memory_before():
        from flask import g, request
        # Health probes and static assets are outside the accounting: they
        # must stay free (no counters, no collection, no activity bump), and a
        # 15-minute monitor ping must not make an idle shop look busy.
        if _skip_path(request.path or ""):
            g._ja_memory_skip = True
            return None
        g._ja_memory_counted = True
        note_request()
        return None

    @app.teardown_request
    def _ja_memory_teardown(_exc=None):
        """Release the request's transients once its response has been built.

        ``teardown_request`` is the one hook Flask guarantees exactly once per
        request, after the view and after every ``after_request`` handler, and
        it also runs for a 404 or an aborted upload - the cases where a Pillow
        buffer or a spooled multipart stream is most likely to be left behind.
        It collects *after* the response object exists, so the shopper never
        waits for the collector (the body is already in memory and does not
        depend on anything the collector can free).
        """
        try:
            from flask import g, request
            if getattr(g, "_ja_memory_skip", False):
                return
            if not getattr(g, "_ja_memory_counted", False):
                return
            g._ja_memory_counted = False
            full = (request.method in ("POST", "PUT", "PATCH")
                    and request.mimetype == "multipart/form-data")
        except Exception:
            full = False
        note_response(full=full)

    return True


def idle_seconds(now=None):
    now = time.monotonic() if now is None else float(now)
    with _lock:
        return max(0.0, now - float(_state["lastActivity"] or 0))


def recycle_idle_pools(*, force=False, now=None):
    """Close idle Supabase pools and drop the catalogue caches.

    Returns a small report; a no-op (``recycled: False``) while requests are in
    flight or while the shop is still being used, so an ordinary session never
    pays for a reconnect.
    """
    with _lock:
        if int(_state["inFlight"]) > 0 and not force:
            return {"recycled": False, "reason": "busy"}
        idle = idle_seconds(now)
        if not force and idle < IDLE_SECONDS:
            return {"recycled": False, "reason": "active", "idleSeconds": round(idle, 1)}
    try:
        # A request in flight is not the only thing that can be using the
        # pool: the scheduler's own jobs (supplier sweep, mirror, backup) read
        # Supabase too. They mark supabase_store's activity clock, so require
        # that clock to be idle as well before closing anything under them.
        import supabase_store
        db_idle = supabase_store.idle_seconds()
        if not force and db_idle < IDLE_SECONDS:
            return {"recycled": False, "reason": "database-active",
                    "idleSeconds": round(idle, 1),
                    "dbIdleSeconds": round(db_idle, 1)}
    except Exception:
        pass
    report = {"recycled": False, "reason": "nothing-to-do", "idleSeconds": round(idle, 1)}
    try:
        report["dbIdleSeconds"] = round(float(__import__("supabase_store").idle_seconds()), 1)
    except Exception:
        pass
    try:
        import supabase_store
        closed = supabase_store.recycle_clients(reason="idle" if not force else "forced")
        report.update(closed or {})
        report["recycled"] = bool((closed or {}).get("recycled"))
    except Exception as exc:                       # never break the shop
        report["error"] = str(exc)[:160]
    # The serialized catalogue snapshot can be ~600 KB; it is rebuilt from the
    # database on the next request, and dropping it now is what keeps an idle
    # instance small.
    try:
        import api as api_mod
        api_mod._invalidate_all_catalog_caches()
        report["catalogCacheCleared"] = True
    except Exception:
        report["catalogCacheCleared"] = False
    try:
        import catalog as catalog_mod
        catalog_mod._french_description_table.cache_clear()
        report["descriptionCacheCleared"] = True
    except Exception:
        report["descriptionCacheCleared"] = False
    collect(full=True)
    trimmed = _malloc_trim()
    report["trimmedBytes"] = int(trimmed or 0)
    with _lock:
        _state["recycles"] = int(_state["recycles"]) + 1
        _state["lastRecycleAt"] = time.time()
        _state["lastRecycleReason"] = str(report.get("reason") or "")
        _state["lastTrimmedBytes"] = int(trimmed or 0)
        _state["lastActivity"] = time.monotonic()
    return report


def idle_tick():
    """Called from the scheduler's five-minute loop (and by tests)."""
    if not _state.get("enabled", True):
        return {"skipped": "disabled"}
    try:
        report = recycle_idle_pools()
    except Exception as exc:                       # pragma: no cover - defensive
        return {"error": str(exc)[:160]}
    if report.get("recycled"):
        try:
            import observability
            observability.record_failure  # noqa: B018 - presence check only
        except Exception:
            pass
    return report


def stats():
    """Small, JSON-safe snapshot for /health and the admin job panel."""
    with _lock:
        out = dict(_state)
    out["rssMb"] = _rss_mb()
    out["idleSeconds"] = round(idle_seconds(), 1)
    out["idleLimitSeconds"] = IDLE_SECONDS
    out["gcIntervalSeconds"] = GC_INTERVAL_SECONDS
    out["gcCounts"] = list(gc.get_count())
    out["gcEnabled"] = bool(gc.isenabled())
    out["implemented"] = sys.implementation.name
    return out


def reset_for_tests():
    """Restore a pristine state (tests only)."""
    _rss_cache["at"] = 0.0
    _rss_cache["value"] = 0.0
    with _lock:
        _state.update({
            "requests": 0, "collections": 0, "fullCollections": 0,
            "recycles": 0, "lastCollectionAt": 0.0, "lastRecycleAt": 0.0,
            "lastRecycleReason": "", "lastTrimmedBytes": 0, "inFlight": 0,
            "lastActivity": time.monotonic(), "enabled": True,
        })
