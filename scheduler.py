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
import task_queue

TICK_SECONDS = 300
# One tick never processes more than this many reminders, and never holds more
# than one page of rows in memory.
REMINDER_PAGE_SIZE = 20
REMINDER_MAX_PER_TICK = 200
SUPPLIER_PAGE_SIZE = int(os.environ.get("SUPPLIER_WATCHDOG_BATCH", "2") or 2)
# Hard-bounded unique external supplier URLs per five-minute tick. The
# watchdog clamps this to 2 even if a deployment is misconfigured.
SUPPLIER_LINKS_PER_TICK = min(2, max(1, int(
    os.environ.get("SUPPLIER_WATCHDOG_LINKS_PER_TICK", "2") or 2)))
# Cooldown is per supplier URL (not per product); nightly maintenance honors
# the same interval instead of bypassing it with a full-catalogue burst.
SUPPLIER_MIN_INTERVAL = int(os.environ.get("SUPPLIER_WATCHDOG_MIN_INTERVAL", "3600") or 3600)

# ------------------------------------------------------------- nightly 2 AM
# The once-a-day maintenance pass runs one more cooldown-protected supplier
# batch and the storage sweeper that purges orphaned / duplicate media the
# day's edits left behind. Render's free tier has no real cron, so the
# in-process loop IS the cron: each 5-minute tick checks whether the nightly
# hour has passed and the pass has not run yet today.
NIGHTLY_HOUR = int(os.environ.get("SUPPLIER_WATCHDOG_NIGHTLY_HOUR", "2") or 0)
NIGHTLY_TZ_OFFSET = float(os.environ.get("SUPPLIER_WATCHDOG_NIGHTLY_TZ_OFFSET", "1") or 0)
_last_nightly_date = ""


def _nightly_enabled():
    raw = os.environ.get("SUPPLIER_WATCHDOG_NIGHTLY", "1").strip().lower()
    return NIGHTLY_HOUR >= 0 and raw not in ("0", "false", "no", "off")


def _nightly_due(now=None):
    """True once per local day, after the configured nightly hour."""
    if not _nightly_enabled():
        return False
    now = now or datetime.datetime.utcnow()
    local = now + datetime.timedelta(hours=NIGHTLY_TZ_OFFSET)
    if local.hour < NIGHTLY_HOUR:
        return False
    return _last_nightly_date != local.date().isoformat()


def _supplier_nightly(logger=None):
    import supplier_watchdog
    result = supplier_watchdog.nightly_sweep(
        logger=logger, min_interval_seconds=SUPPLIER_MIN_INTERVAL,
        link_limit=SUPPLIER_LINKS_PER_TICK)
    _health["supplierLastRun"] = result.get("at") or _utc_now()
    return result


def _storage_sweeper(logger=None):
    """Purge orphaned / duplicate upload media the day left behind.

    Runs the same battle-tested plan as the admin's Storage cleanup card:
    files a live product, order, receipt or the site itself still references
    are never candidates, and uploads from the last two days (grace window)
    are protected. Nothing is guessed - a failed reference scan aborts the
    sweep rather than treating unreadable references as orphans."""
    raw = os.environ.get("STORAGE_SWEEPER_NIGHTLY", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return {"skipped": True}
    import storage_cleanup
    report = storage_cleanup.build_plan(min_age_days=2)
    out = {"candidates": int(report.get("candidate_count") or 0),
           "bytes": int(report.get("candidate_bytes") or 0), "deleted": [], "errors": []}
    if out["candidates"] > 0:
        result = storage_cleanup.apply_plan(report=report)
        out["deleted"] = list(result.get("deleted") or [])
        out["errors"] = list(result.get("errors") or [])
    if logger:
        logger.info("nightly storage sweeper: candidates=%s deleted=%s errors=%s",
                    out["candidates"], len(out["deleted"]), len(out["errors"]))
    return out


def _nightly_run(logger=None):
    """At 2 AM run one paced supplier batch plus the bounded storage sweeper."""
    global _last_nightly_date
    now = datetime.datetime.utcnow()
    local = now + datetime.timedelta(hours=NIGHTLY_TZ_OFFSET)
    supplier = _step("supplier.nightly_sweep", lambda: _supplier_nightly(logger), logger)
    sweeper = _step("storage.nightly_sweeper", lambda: _storage_sweeper(logger), logger)
    _health["nightlyLastRun"] = _utc_now()
    _health["nightlySchedule"] = (
        f"{NIGHTLY_HOUR:02d}:00 (UTC{NIGHTLY_TZ_OFFSET:+g})" if _nightly_enabled() else "off")
    # Only stamp the date when both steps at least ran (a raise would have
    # been traced by _step; a None result still counts as "attempted today" so
    # a broken supplier page cannot re-run the sweep every 5 minutes).
    _last_nightly_date = local.date().isoformat()
    _collect()
    return {"supplier": supplier, "sweeper": sweeper}

# Compatibility names kept for health/tests/admin copy; they now refer to the
# same in-process consolidated loop instead of separate Render worker services.
BACKGROUND_THREAD = "jaurastore-background"
MAINTENANCE_THREAD = BACKGROUND_THREAD
REMINDERS_THREAD = BACKGROUND_THREAD

_started = threading.Event()
_start_lock = threading.Lock()
_health = {
    "maintenanceLastRun": "",
    "stockGuardrailLastRun": "",
    "stockGuardrailChecked": 0,
    "stockGuardrailFixed": 0,
    "stockGuardrailDisagreements": 0,
    "remindersLastRun": "",
    "supplierLastRun": "",
    "nightlyLastRun": "",
    "nightlySchedule": "",
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
# One chunk of a batch job. Render gives the free instance 512MB; five records
# per pass keeps the transient allocation (the row, its media refs, the new
# photo bytes) small enough that a full repair pass cannot push RSS over the
# ceiling. Never raised above ten.
CHUNK_SIZE = int(os.environ.get("JAURA_CHUNK_SIZE", "5") or 5)
CHUNK_SIZE = max(1, min(10, CHUNK_SIZE))
_last_photo_repair = ""


def _collect():
    """Explicit gc between chunks: the memory half of the chunk contract."""
    try:
        gc.collect()
    except Exception:                             # pragma: no cover
        pass


def _backoff_seconds(attempt, base=2.0, cap=60.0):
    """Exponential backoff for attempt N (1-based), capped."""
    try:
        attempt = int(attempt)
    except (TypeError, ValueError):
        attempt = 1
    return min(float(cap), float(base) * (2 ** max(0, attempt - 1)))


def _chunked(job, fn, chunks, chunk_size=None, logger=None, stop_when=None):
    """Run ``fn(chunk_size)`` in bounded chunks with a gc() between each.

    ``fn`` must accept a ``limit`` and return a dict. The loop stops early when
    the caller's ``stop_when(result)`` says the work is exhausted, or when a
    chunk raises - a crash in chunk 3 must not abandon chunks 4..n silently, it
    is traced by ``_note_failure`` and the sweep starts again next tick.
    """
    chunk_size = max(1, min(10, int(chunk_size or CHUNK_SIZE)))
    last = {}
    for index in range(max(1, int(chunks or 1))):
        try:
            last = fn(chunk_size) or {}
        except Exception as exc:                  # pragma: no cover - traced
            _note_failure(job, exc, logger=logger, attempt=index + 1)
            break
        _collect()
        try:
            if stop_when and stop_when(last):
                break
        except Exception:
            break
    return last


def _repair_photos(logger=None):
    """Once a day: re-point products whose stored photo is missing.

    The repair used to happen in one 60-row call; it now runs the same total
    work in five-row chunks, collecting between them, so the daily pass has a
    flat memory profile on the 512MB instance.
    """
    global _last_photo_repair
    today = datetime.date.today().isoformat()
    if _last_photo_repair == today:
        return
    import catalog as catalog_mod
    chunks = max(1, _PHOTO_REPAIR_PER_RUN // CHUNK_SIZE)
    report = _chunked(
        "scheduler.repair_photos",
        lambda limit: catalog_mod.repair_dead_photos(limit=limit, actor="scheduler"),
        chunks=chunks, logger=logger,
        stop_when=lambda r: not (r.get("missing") or r.get("repaired")))
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
    """Run a two-link, cooldown-protected supplier batch in the web process."""
    import supplier_watchdog
    result = supplier_watchdog.tick(limit=SUPPLIER_PAGE_SIZE,
                                    min_interval_seconds=SUPPLIER_MIN_INTERVAL,
                                    logger=logger,
                                    link_limit=SUPPLIER_LINKS_PER_TICK)
    _collect()
    _health["supplierLastRun"] = result.get("at") or _utc_now()
    return result


def _stock_guardrail(logger=None):
    """Keep every stored row's quantities consistent with what the shop serves.

    The write paths (a checkout reservation, an admin save, a supplier
    mirror) fix the rows they touch. This is the sweep for the rows they do
    not: a row whose variants all reached zero while its total still said
    otherwise, or a total that drifted away from its variant sum. It is a
    no-op on a healthy catalogue (one read, no writes), so it runs on the
    regular maintenance tick rather than waiting for the nightly pass - a
    product that sold out must never keep advertising itself as in stock
    between two nightly runs.

    Rows whose two stock spellings merely DISAGREE are counted, never
    rewritten: which number the shop means is the owner's decision, and the
    count is surfaced on the admin health screen.
    """
    import catalog as _catalog
    result = _catalog.stock_guardrail_sweep(actor="scheduler.stock_guardrail")
    _collect()
    _health["stockGuardrailLastRun"] = _utc_now()
    _health["stockGuardrailChecked"] = int(result.get("checked") or 0)
    _health["stockGuardrailFixed"] = int(result.get("fixed") or 0)
    _health["stockGuardrailDisagreements"] = int(result.get("disagreements") or 0)
    if result.get("fixed"):
        # The storefront answered from a snapshot taken before the repair;
        # the next request must not keep serving the old availability.
        try:
            import api as _api
            _api._invalidate_all_catalog_caches()
        except Exception:
            pass
    if result.get("skipped"):
        _health["lastError"] = f"stock guardrail: {result['skipped']}"[:200]
    return result


def _maintenance_tick(logger=None):
    _step("scheduler.keep_alive", lambda: _keep_alive(logger), logger)
    _step("scheduler.daily_backup", lambda: _run_backup(logger), logger)
    _step("scheduler.remirror_strays", lambda: _remirror(logger), logger)
    _step("scheduler.repair_photos", lambda: _repair_photos(logger), logger)
    _step("scheduler.persist_counters", lambda: _persist_counters(logger), logger)
    # The task queue's own thread drains within a second of a request; this
    # step is the net under it - a job that was parked by backoff, left behind
    # by a death+restart, or enqueued while the thread was being respawned is
    # picked up on the next five-minute tick instead of waiting for the admin
    # to notice a stuck panel.
    _step("tasks.sweep", lambda: task_queue.drain(logger=logger), logger)
    # Nightly maintenance replaces (rather than adds to) the regular supplier
    # batch, so the same five-minute cycle never doubles the two-link ceiling.
    if _nightly_due():
        _step("scheduler.nightly", lambda: _nightly_run(logger), logger)
    else:
        _step("supplier.watchdog", lambda: _supplier_watchdog(logger), logger)
    # After the supplier batch, so a supplier-side quantity that landed this
    # tick is reconciled in the same pass instead of the next one.
    _step("stock.guardrail", lambda: _stock_guardrail(logger), logger)
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
        # Same exponential ladder as every other retry (2s, 4s, 8s ...) and a
        # collection between attempts: the retry path is the one most likely to
        # be holding a half-built page of rows when it gives up.
        _collect()
        time.sleep(_backoff_seconds(attempt + 1, cap=30.0))
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
            # The deletion/broadcast worker is event-driven (it wakes on
            # enqueue, not on the five-minute tick); this just guarantees it is
            # running even if it died between ticks.
            _step("tasks.ensure_alive", lambda: task_queue.ensure_alive(), logger)
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
        # A single bounce is self-healing and must not page anyone: it is
        # logged, counted in /healthz ("restarts") and only escalated to a
        # recorded failure when the same worker keeps dying. The GitHub
        # notifier is silenced for the escalation too - the record is for the
        # job panel and job_failures, not a false alarm.
        if int(_health["restarts"]) >= 2 or len(restarted) > 1:
            observability.record_failure(
                "scheduler.worker_died",
                RuntimeError("restarted dead worker(s): " + ", ".join(restarted)),
                logger=logger, notify=False, worker=BACKGROUND_THREAD)
        elif logger:
            logger.info("scheduler worker restarted (self-heal #%s): %s",
                        _health["restarts"], ", ".join(restarted))
    try:
        task_queue.ensure_alive(app)
    except Exception as exc:                      # pragma: no cover
        _note_failure("tasks.ensure_alive", exc, logger=logger)
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
    nightly = {
        "schedule": (f"{NIGHTLY_HOUR:02d}:00 (UTC{NIGHTLY_TZ_OFFSET:+g})"
                     if _nightly_enabled() else "off"),
        "jobs": "supplier sweep + storage sweeper",
    }
    guardrail = {
        "schedule": f"every {TICK_SECONDS}s maintenance tick",
        "lastRun": _health.get("stockGuardrailLastRun") or "",
        "checked": _health.get("stockGuardrailChecked") or 0,
        "fixed": _health.get("stockGuardrailFixed") or 0,
        # Rows whose two stock spellings disagree. Never auto-repaired: the
        # owner decides which number is right. A non-zero count is worth a
        # look, not an alarm.
        "disagreements": _health.get("stockGuardrailDisagreements") or 0,
    }
    try:
        tasks = task_queue.stats()
    except Exception:                             # pragma: no cover
        tasks = {}
    return {**_health, "started": _started.is_set(),
            "tasks": tasks,
            "backgroundAlive": alive,
            "nightly": nightly,
            # Backwards-compatible booleans: both old logical workers are now
            # represented by the same consolidated in-process loop.
            "maintenanceAlive": alive,
            "remindersAlive": alive,
            "supplierAlive": alive,
            "threadName": BACKGROUND_THREAD,
            "intervalSeconds": TICK_SECONDS,
            "supplier": supplier,
            "stockGuardrail": guardrail,
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
    try:
        task_queue.start(app)
    except Exception as exc:                      # pragma: no cover
        _note_failure("tasks.start", exc, logger=logger)
    return True
