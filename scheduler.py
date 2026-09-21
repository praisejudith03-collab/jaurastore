"""In-process scheduler for the midnight backup and catalog maintenance.

Two isolated daemon workers, each ticking every 5 minutes:
  * backup.run() — the first tick on or after midnight backs up all
    products and orders to GitHub (once per calendar day);
  * catalog.repair_dead_photos() — once a day, re-point a product whose
    uploaded photo is missing from the bucket at one that still exists.

The abandoned-cart worker is isolated from catalog/backup work, so a slow
maintenance task cannot delay reminders. Started from create_app(); never
started twice, never under pytest.

Crash tracing
-------------
Every step of every tick runs inside observability.record_failure(), so a
failure is stored with its exception type, stack trace, timestamp, the
worker's memory usage and the payload id it was working on - locally in
`job_failures`, mirrored to Supabase and dispatched to GitHub Actions.
The watchdog's "background scheduler workers are not healthy" can then be
traced to the actual exception instead of guessed at.

Both loops are also self-healing: health_snapshot() reports whether each
named thread is alive, and start()/ensure_alive() restart a thread that
died so the watchdog's liveness check describes a worker that is really
running.
"""
import datetime, gc, os, threading, time

import observability

TICK_SECONDS = 300
# One tick never processes more than this many reminders, and never holds
# more than one page of rows in memory. Both ceilings exist so the worker's
# resident memory stays far below the 50MB budget that used to be blown by
# "SELECT everything" batches (the cause of scheduler.worker_died).
REMINDER_PAGE_SIZE = 25
REMINDER_MAX_PER_TICK = 200
MAINTENANCE_THREAD = "jaura-maintenance"
REMINDERS_THREAD = "jaura-abandoned-carts"
_started = threading.Event()
_start_lock = threading.Lock()
_health = {
    "maintenanceLastRun": "",
    "remindersLastRun": "",
    "lastError": "",
    "lastErrorAt": "",
    "lastErrorJob": "",
    "failures": 0,
    "restarts": 0,
    "rssMb": 0.0,
}
_app_logger = None


def _utc_now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _note_failure(job, exc, logger=None, payload_id="", attempt=1):
    """Record a crash with full detail and keep the health snapshot honest."""
    report = observability.record_failure(
        job, exc, payload_id=payload_id, attempt=attempt,
        logger=logger if logger is not None else _app_logger)
    _health["lastError"] = str(exc)[:200]
    _health["lastErrorAt"] = report.get("at") or _utc_now()
    _health["lastErrorJob"] = job
    _health["failures"] = int(_health.get("failures") or 0) + 1
    return report


def _step(job, fn, logger=None, payload_id=""):
    """Run one tick step; a crash is traced and never kills the worker."""
    try:
        return fn()
    except Exception as exc:                      # pragma: no cover - traced
        _note_failure(job, exc, logger=logger, payload_id=payload_id)
        return None


def _keep_alive(logger=None):
    """Ping the public site so the free Render service never goes to sleep.

    Guarded by KEEP_ALIVE (default: on when SITE_ORIGIN is set). A short
    timeout plus a catch-all try/except mean this can never crash the
    scheduler. Skipped completely under pytest (FLASK_ENV=testing) so the
    test suite never makes real network calls.
    """
    env = os.environ.get("FLASK_ENV", "")
    if env == "testing":                          # pragma: no cover
        return
    site_origin = os.environ.get("SITE_ORIGIN", "").strip().rstrip("/")
    raw = os.environ.get("KEEP_ALIVE", "").strip().lower()
    # Default: keep-alive on only when a public URL is configured.
    enabled = site_origin and (raw == "" or raw in ("1", "true", "yes", "on"))
    if not site_origin or not enabled:
        return
    url = site_origin + "/healthz"
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "jaura-keepalive/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if logger and 200 <= resp.status < 300:
                logger.info("keep-alive ok: %s", url)
    except Exception as exc:                      # pragma: no cover
        if logger: logger.warning("keep-alive skipped: %s", exc)


# Photo repair runs at most once a day: it walks the live catalogue and asks
# the storage bucket whether each product's own uploaded photo still exists,
# so a photo that went missing is swapped for one that works (and saved) long
# before it can annoy a shopper for weeks. Voluntarily bounded - never a
# catalogue-wide scan on every tick.
_PHOTO_REPAIR_PER_RUN = 60
_last_photo_repair = ""


def _repair_photos(logger=None):
    """Once a day: re-point products whose stored photo is missing."""
    global _last_photo_repair
    today = datetime.date.today().isoformat()
    if _last_photo_repair == today:
        return
    import catalog as catalog_mod
    report = catalog_mod.repair_dead_photos(limit=_PHOTO_REPAIR_PER_RUN,
                                            actor="scheduler")
    _last_photo_repair = today
    if logger:
        logger.info("photo repair: checked=%s missing=%s repaired=%s",
                    report.get("checked"), len(report.get("missing") or []),
                    len(report.get("repaired") or []))


def _run_backup(logger=None):
    import backup
    if backup.due():
        ok, report = backup.run()
        backup.mark_backup_done()
        if logger: logger.info("daily backup ok=%s %s", ok, report)


def _remirror(logger=None):
    import catalog as catalog_mod
    n = catalog_mod.remirror_strays()
    if logger and n:
        logger.info("remirrored %d local-only product(s) to Supabase", n)


def _persist_counters(logger=None):
    """Push the lifetime analytics counters to Supabase every tick.

    Without this the odometer only reaches durable storage when a request
    happens to fire it, and a quiet hour before a deploy loses the tail.
    """
    import analytics as analytics_mod
    analytics_mod.persist_counters()


def _tick(logger=None):
    _step("scheduler.keep_alive", lambda: _keep_alive(logger), logger)
    _step("scheduler.daily_backup", lambda: _run_backup(logger), logger)
    _step("scheduler.remirror_strays", lambda: _remirror(logger), logger)
    _step("scheduler.repair_photos", lambda: _repair_photos(logger), logger)
    _step("scheduler.persist_counters", lambda: _persist_counters(logger), logger)


def _loop(logger=None):
    """Maintenance loop; a failed tick is traced and never kills the thread."""
    while True:
        try:
            _tick(logger)
            _health["maintenanceLastRun"] = _utc_now()
            _release_memory(MAINTENANCE_THREAD, logger)
        except Exception as exc:                  # pragma: no cover
            _note_failure("scheduler.maintenance_tick", exc, logger=logger)
        time.sleep(TICK_SECONDS)


def _abandoned_tick(logger=None, attempts=3):
    """Run abandoned-cart delivery independently with bounded retries.

    Work is paginated inside abandoned.send_due_reminders (one small page of
    rows in memory at a time) and every individual send is try/except-ed
    there, so one bad recipient can neither stop the queue nor kill the
    worker. A failing attempt is recorded as a crash report (with traceback
    and attempt number) so a downed notification pipeline is visible rather
    than merely quiet.
    """
    import abandoned
    result = {"sent": 0, "failed": 0}
    total_sent = 0
    for attempt in range(max(1, attempts)):
        try:
            try:
                result = abandoned.send_due_reminders(
                    limit=REMINDER_MAX_PER_TICK, page_size=REMINDER_PAGE_SIZE)
            except TypeError:
                # A sender without the pagination keyword (older build or a
                # test double) still runs with its own bounded default.
                result = abandoned.send_due_reminders(limit=REMINDER_PAGE_SIZE)
            total_sent += int(result.get("sent") or 0)
        except Exception as exc:                  # pragma: no cover - traced
            _note_failure("notifications.abandoned_cart", exc, logger=logger,
                          attempt=attempt + 1)
            result = {"sent": 0, "failed": 1}
        if not result.get("failed") or attempt + 1 >= attempts:
            break
        # Failed sends release their claim and are safe to retry. Keep this
        # short so the independent worker remains responsive.
        time.sleep(2 ** attempt)
    result = {"sent": total_sent, "failed": int(result.get("failed") or 0)}
    if logger and (result["sent"] or result["failed"]):
        logger.info("abandoned-cart reminders: sent=%s failed=%s",
                    result["sent"], result["failed"])
    return result


def _release_memory(job, logger=None):
    """Drop per-tick garbage and log the worker's RSS.

    The workers used to die (scheduler.worker_died) because a tick built
    large transient lists that the allocator kept hold of between ticks.
    Collecting at the end of every tick returns that memory immediately and
    gives the health snapshot an honest reading.
    """
    try:
        gc.collect()
        rss = observability.memory_mb()
        _health["rssMb"] = rss
        if logger and rss:
            logger.debug("%s tick finished rss=%.1fMB", job, rss)
        return rss
    except Exception:                             # pragma: no cover
        return 0.0


def _abandoned_loop(logger=None):
    """A dedicated loop means slow catalog/backup work cannot block recovery."""
    while True:
        try:
            _abandoned_tick(logger)
            _health["remindersLastRun"] = _utc_now()
            _release_memory(REMINDERS_THREAD, logger)
        except Exception as exc:                  # pragma: no cover
            _note_failure("scheduler.reminders_tick", exc, logger=logger)
        time.sleep(TICK_SECONDS)


def _alive(name):
    return any(t.name == name and t.is_alive() for t in threading.enumerate())


def _spawn(name, target, logger):
    thread = threading.Thread(target=target, args=(logger,), daemon=True,
                              name=name)
    thread.start()
    return thread


def ensure_alive(app=None):
    """Restart any worker thread that died. Returns the names restarted.

    A daemon thread can be lost to an unrecoverable error, an OOM kill or a
    fork; when that happened the watchdog reported "workers are not
    healthy" forever because nothing ever put them back. /healthz calls
    this on every check, so the next 20-minute run finds a live worker and
    the crash that killed it is already in job_failures.
    """
    logger = app.logger if app is not None else _app_logger
    restarted = []
    if not _started.is_set():
        return restarted
    with _start_lock:
        if not _alive(MAINTENANCE_THREAD):
            _spawn(MAINTENANCE_THREAD, _loop, logger)
            restarted.append(MAINTENANCE_THREAD)
        if not _alive(REMINDERS_THREAD):
            _spawn(REMINDERS_THREAD, _abandoned_loop, logger)
            restarted.append(REMINDERS_THREAD)
    if restarted:
        _health["restarts"] = int(_health.get("restarts") or 0) + len(restarted)
        observability.record_failure(
            "scheduler.worker_died",
            RuntimeError("restarted dead worker(s): " + ", ".join(restarted)),
            logger=logger)
    return restarted


def health_snapshot(repair=True):
    """Public-safe liveness used by /healthz and the 20-minute watchdog.

    `repair` restarts a dead worker before reporting, so the watchdog gets
    a truthful answer and a self-healed service rather than a permanent
    failure. The recent crash reports travel with it: when the watchdog
    fails, the reason is in the same payload.
    """
    if repair:
        try:
            ensure_alive()
        except Exception:                          # pragma: no cover
            pass
    names = {thread.name for thread in threading.enumerate() if thread.is_alive()}
    recent = []
    try:
        for report in observability.recent(5):
            recent.append({
                "job": report.get("job"),
                "error": report.get("error_type"),
                "message": str(report.get("message") or "")[:200],
                "payloadId": report.get("payload_id") or "",
                "rssMb": report.get("rss_mb"),
                "at": report.get("at"),
            })
    except Exception:                              # pragma: no cover
        recent = []
    return {**_health, "started": _started.is_set(),
            "maintenanceAlive": MAINTENANCE_THREAD in names,
            "remindersAlive": REMINDERS_THREAD in names,
            "intervalSeconds": TICK_SECONDS,
            "recentFailures": recent}


def start(app=None):
    global _app_logger
    if _started.is_set():
        return False
    _started.set()
    logger = app.logger if app is not None else None
    _app_logger = logger
    try:
        import backup
        if not backup.last_backup_date():
            # First boot: baseline today so the first automatic backup runs
            # at the NEXT midnight, exactly as scheduled.
            backup.mark_backup_done()
    except Exception as exc:                     # pragma: no cover
        _note_failure("scheduler.backup_baseline", exc, logger=logger)
    with _start_lock:
        _spawn(MAINTENANCE_THREAD, _loop, logger)
        _spawn(REMINDERS_THREAD, _abandoned_loop, logger)
    return True
