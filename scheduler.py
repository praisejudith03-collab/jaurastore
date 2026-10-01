"""In-process maintenance scheduler for the single Render web service.

Option-1 free-tier architecture: there are no Render background worker services.
The web dyno starts one lightweight daemon thread named ``jaurastore-background``
that runs the old maintenance job, the abandoned-cart reminders, and the
supplier stock watchdog in bounded batches. This keeps instance-hours at one
web service only and avoids the duplicated RAM footprint of separate workers.

Every step is isolated with observability.record_failure(), so a failing email,
backup, supplier page or storage probe is logged and cannot crash the Flask
process. Work is paginated/bounded; each tick releases transient memory before
sleeping.
"""
import datetime, gc, os, threading, time

import observability

TICK_SECONDS = 300
# One tick never processes more than this many reminders, and never holds more
# than one page of rows in memory.
REMINDER_PAGE_SIZE = 20
REMINDER_MAX_PER_TICK = 200
SUPPLIER_PAGE_SIZE = int(os.environ.get("SUPPLIER_WATCHDOG_BATCH", "8") or 8)

# Compatibility names kept for health/tests/admin copy; they now refer to the
# same in-process consolidated loop instead of separate Render worker services.
BACKGROUND_THREAD = "jaurastore-background"
MAINTENANCE_THREAD = BACKGROUND_THREAD
REMINDERS_THREAD = BACKGROUND_THREAD

_started = threading.Event()
_start_lock = threading.Lock()
_health = {
    "maintenanceLastRun": "",
    "remindersLastRun": "",
    "supplierLastRun": "",
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
    scheduler. Skipped completely under pytest (FLASK_ENV=testing) so the test
    suite never makes real network calls.
    """
    env = os.environ.get("FLASK_ENV", "")
    if env == "testing":                          # pragma: no cover
        return
    site_origin = os.environ.get("SITE_ORIGIN", "").strip().rstrip("/")
    raw = os.environ.get("KEEP_ALIVE", "").strip().lower()
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
    """Push lifetime analytics counters to Supabase every tick."""
    import analytics as analytics_mod
    analytics_mod.persist_counters()


def _supplier_watchdog(logger=None):
    """Run one bounded supplier-stock page in the web service process."""
    import supplier_watchdog
    result = supplier_watchdog.tick(limit=SUPPLIER_PAGE_SIZE, logger=logger)
    _health["supplierLastRun"] = result.get("at") or _utc_now()
    return result


def _maintenance_tick(logger=None):
    _step("scheduler.keep_alive", lambda: _keep_alive(logger), logger)
    _step("scheduler.daily_backup", lambda: _run_backup(logger), logger)
    _step("scheduler.remirror_strays", lambda: _remirror(logger), logger)
    _step("scheduler.repair_photos", lambda: _repair_photos(logger), logger)
    _step("scheduler.persist_counters", lambda: _persist_counters(logger), logger)
    _step("supplier.watchdog", lambda: _supplier_watchdog(logger), logger)
    _health["maintenanceLastRun"] = _utc_now()


# Backwards-compatible test helper name.
def _tick(logger=None):
    _maintenance_tick(logger)


def _abandoned_tick(logger=None, attempts=3):
    """Run abandoned-cart delivery with bounded retries.

    Work is paginated inside abandoned.send_due_reminders (one small page of
    rows in memory at a time) and every individual send is try/except-ed there,
    so one bad recipient can neither stop the queue nor kill the worker.
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
                result = abandoned.send_due_reminders(limit=REMINDER_PAGE_SIZE)
            total_sent += int(result.get("sent") or 0)
        except Exception as exc:                  # pragma: no cover - traced
            _note_failure("notifications.abandoned_cart", exc, logger=logger,
                          attempt=attempt + 1)
            result = {"sent": 0, "failed": 1}
        if not result.get("failed") or attempt + 1 >= attempts:
            break
        time.sleep(2 ** attempt)
    result = {"sent": total_sent, "failed": int(result.get("failed") or 0)}
    if logger and (result["sent"] or result["failed"]):
        logger.info("abandoned-cart reminders: sent=%s failed=%s",
                    result["sent"], result["failed"])
    _health["remindersLastRun"] = _utc_now()
    return result


def _release_memory(job, logger=None):
    """Drop per-tick garbage and log RSS for free-tier RAM safety."""
    try:
        gc.collect()
        rss = observability.memory_mb()
        _health["rssMb"] = rss
        if logger and rss:
            logger.debug("%s tick finished rss=%.1fMB", job, rss)
        return rss
    except Exception:                             # pragma: no cover
        return 0.0


def _loop(logger=None):
    """One consolidated loop: maintenance, supplier watchdog, reminders."""
    while True:
        try:
            _maintenance_tick(logger)
            _abandoned_tick(logger)
            _release_memory(BACKGROUND_THREAD, logger)
        except Exception as exc:                  # pragma: no cover
            _note_failure("scheduler.background_tick", exc, logger=logger)
        time.sleep(TICK_SECONDS)


def _alive(name=BACKGROUND_THREAD):
    return any(t.name == name and t.is_alive() for t in threading.enumerate())


def _spawn(name, target, logger):
    thread = threading.Thread(target=target, args=(logger,), daemon=True,
                              name=name)
    thread.start()
    return thread


def ensure_alive(app=None):
    """Restart the consolidated in-process worker if it died."""
    logger = app.logger if app is not None else _app_logger
    restarted = []
    if not _started.is_set():
        return restarted
    with _start_lock:
        if not _alive(BACKGROUND_THREAD):
            _spawn(BACKGROUND_THREAD, _loop, logger)
            restarted.append(BACKGROUND_THREAD)
    if restarted:
        _health["restarts"] = int(_health.get("restarts") or 0) + len(restarted)
        observability.record_failure(
            "scheduler.worker_died",
            RuntimeError("restarted dead worker(s): " + ", ".join(restarted)),
            logger=logger)
    return restarted


def health_snapshot(repair=True):
    """Public-safe liveness used by /healthz and the admin job panel."""
    if repair:
        try:
            ensure_alive()
        except Exception:                          # pragma: no cover
            pass
    alive = _alive(BACKGROUND_THREAD)
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
    supplier = {}
    try:
        import supplier_watchdog
        supplier = supplier_watchdog.summary()
    except Exception:
        supplier = {}
    return {**_health, "started": _started.is_set(),
            "backgroundAlive": alive,
            # Backwards-compatible booleans: both old logical workers are now
            # represented by the same consolidated in-process loop.
            "maintenanceAlive": alive,
            "remindersAlive": alive,
            "supplierAlive": alive,
            "threadName": BACKGROUND_THREAD,
            "intervalSeconds": TICK_SECONDS,
            "supplier": supplier,
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
            backup.mark_backup_done()
    except Exception as exc:                     # pragma: no cover
        _note_failure("scheduler.backup_baseline", exc, logger=logger)
    with _start_lock:
        _spawn(BACKGROUND_THREAD, _loop, logger)
    return True
