"""In-process scheduler: abandoned-cart reminders + the midnight backup.

One daemon thread, one tick every 5 minutes:
  * growth.send_abandoned_reminders() — emails checkouts stalled for the
    configured number of hours (default 2);
  * backup.run() — the first tick on or after midnight backs up all
    products and orders to GitHub (once per calendar day).

Started from create_app(); never started twice, never under pytest.
"""
import os, threading, time

TICK_SECONDS = 300
_started = threading.Event()


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


def _tick(logger=None):
    try:
        _keep_alive(logger)
    except Exception as exc:                      # pragma: no cover
        if logger: logger.warning("keep-alive failed: %s", exc)
    try:
        import growth
        growth.send_abandoned_reminders()
    except Exception as exc:                      # pragma: no cover
        if logger: logger.warning("abandoned reminders skipped: %s", exc)
    try:
        import backup
        if backup.due():
            ok, report = backup.run()
            backup.mark_backup_done()
            if logger: logger.info("daily backup ok=%s %s", ok, report)
    except Exception as exc:                      # pragma: no cover
        if logger: logger.warning("daily backup skipped: %s", exc)


def _loop(logger=None):
    while True:
        _tick(logger)
        time.sleep(TICK_SECONDS)


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
    t = threading.Thread(target=_loop, args=(logger,), daemon=True,
                         name="jaura-scheduler")
    t.start()
    return True
