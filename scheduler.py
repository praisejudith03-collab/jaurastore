"""In-process scheduler for the midnight backup and catalog maintenance.

Two isolated daemon workers, each ticking every 5 minutes:
  * backup.run() — the first tick on or after midnight backs up all
    products and orders to GitHub (once per calendar day);
  * catalog.repair_dead_photos() — once a day, re-point a product whose
    uploaded photo is missing from the bucket at one that still exists.

The abandoned-cart worker is isolated from catalog/backup work, so a slow
maintenance task cannot delay reminders. Started from create_app(); never
started twice, never under pytest.
"""
import datetime, os, threading, time

TICK_SECONDS = 300
_started = threading.Event()
_health = {"maintenanceLastRun": "", "remindersLastRun": "", "lastError": ""}


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


def _tick(logger=None):
    try:
        _keep_alive(logger)
    except Exception as exc:                      # pragma: no cover
        if logger: logger.warning("keep-alive failed: %s", exc)
    try:
        import backup
        if backup.due():
            ok, report = backup.run()
            backup.mark_backup_done()
            if logger: logger.info("daily backup ok=%s %s", ok, report)
    except Exception as exc:                      # pragma: no cover
        if logger: logger.warning("daily backup skipped: %s", exc)
    try:
        import catalog as catalog_mod
        n = catalog_mod.remirror_strays()
        if logger and n:
            logger.info("remirrored %d local-only product(s) to Supabase", n)
    except Exception as exc:                      # pragma: no cover
        if logger: logger.warning("stray remirror skipped: %s", exc)
    try:
        _repair_photos(logger)
    except Exception as exc:                      # pragma: no cover
        if logger: logger.warning("photo repair skipped: %s", exc)


def _loop(logger=None):
    """Maintenance loop; a failed tick is logged and never kills the thread."""
    while True:
        try:
            _tick(logger)
            _health["maintenanceLastRun"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        except Exception as exc:                  # pragma: no cover
            _health["lastError"] = str(exc)[:200]
            if logger: logger.exception("maintenance tick failed: %s", exc)
        time.sleep(TICK_SECONDS)


def _abandoned_tick(logger=None, attempts=3):
    """Run abandoned-cart delivery independently with bounded retries."""
    import abandoned
    result = {"sent": 0, "failed": 0}
    total_sent = 0
    for attempt in range(max(1, attempts)):
        try:
            result = abandoned.send_due_reminders(limit=25)
            total_sent += int(result.get("sent") or 0)
        except Exception as exc:                  # pragma: no cover
            if logger:
                logger.warning("abandoned-cart attempt %d/%d failed: %s",
                               attempt + 1, attempts, exc)
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


def _abandoned_loop(logger=None):
    """A dedicated loop means slow catalog/backup work cannot block recovery."""
    while True:
        try:
            _abandoned_tick(logger)
            _health["remindersLastRun"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        except Exception as exc:                  # pragma: no cover
            _health["lastError"] = str(exc)[:200]
            if logger: logger.exception("abandoned-cart worker survived: %s", exc)
        time.sleep(TICK_SECONDS)


def health_snapshot():
    """Public-safe liveness used by /healthz and the 20-minute watchdog."""
    names = {thread.name for thread in threading.enumerate() if thread.is_alive()}
    return {**_health, "started": _started.is_set(),
            "maintenanceAlive": "jaura-maintenance" in names,
            "remindersAlive": "jaura-abandoned-carts" in names,
            "intervalSeconds": TICK_SECONDS}


def start(app=None):
    if _started.is_set():
        return False
    _started.set()
    try:
        import backup
        if not backup.last_backup_date():
            # First boot: baseline today so the first automatic backup runs
            # at the NEXT midnight, exactly as scheduled.
            backup.mark_backup_done()
    except Exception:                            # pragma: no cover
        pass
    logger = app.logger if app is not None else None
    maintenance = threading.Thread(target=_loop, args=(logger,), daemon=True,
                                   name="jaura-maintenance")
    reminders = threading.Thread(target=_abandoned_loop, args=(logger,), daemon=True,
                                 name="jaura-abandoned-carts")
    maintenance.start()
    reminders.start()
    return True
