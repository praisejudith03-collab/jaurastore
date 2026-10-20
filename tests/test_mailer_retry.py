"""Resend/Brevo transient-failure retry, without retrying permanent errors."""
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")

import mailer  # noqa: E402
import security  # noqa: E402


def _http_error(code):
    return urllib.error.HTTPError("https://api.resend.com/emails", code, "err", {}, None)


def test_transient_5xx_is_retried_then_succeeds(monkeypatch):
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise _http_error(503)
        return b'{"id":"ok"}'

    monkeypatch.setattr(security, "safe_fetch", fake_fetch)
    monkeypatch.setattr(mailer.time, "sleep", lambda _s: None)
    ok, detail = mailer._http_post_json("https://api.resend.com/emails", {}, {})
    assert ok is True
    assert len(calls) == 3
    assert "ok" in detail


def test_rate_limit_429_is_retried(monkeypatch):
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429)
        return b"{}"

    monkeypatch.setattr(security, "safe_fetch", fake_fetch)
    monkeypatch.setattr(mailer.time, "sleep", lambda _s: None)
    ok, _ = mailer._http_post_json("https://api.resend.com/emails", {}, {})
    assert ok is True and len(calls) == 2


def test_permanent_4xx_is_not_retried(monkeypatch):
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append(1)
        raise _http_error(422)

    monkeypatch.setattr(security, "safe_fetch", fake_fetch)
    monkeypatch.setattr(mailer.time, "sleep", lambda _s: None)
    ok, detail = mailer._http_post_json("https://api.resend.com/emails", {}, {})
    assert ok is False
    assert len(calls) == 1
    assert "422" in detail


def test_network_error_is_retried_and_reported(monkeypatch):
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append(1)
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr(security, "safe_fetch", fake_fetch)
    monkeypatch.setattr(mailer.time, "sleep", lambda _s: None)
    ok, detail = mailer._http_post_json("https://api.resend.com/emails", {}, {})
    assert ok is False
    assert len(calls) == mailer.HTTP_ATTEMPTS
    assert "connection reset" in detail


def test_the_dead_duplicate_except_is_gone():
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "mailer.py"), encoding="utf-8").read()
    block = src.split("def _http_post_json", 1)[1].split("def _send_resend", 1)[0]
    assert block.count("except Exception as exc:") == 1


def test_in_flight_sends_are_drained_on_shutdown(monkeypatch):
    """A recycle must not kill an accepted order email mid-flight.

    Render recycles the worker on every deploy and after a free-tier spin-down.
    The send runs on a daemon thread, so without a drain an order confirmation
    the shop already acknowledged could die before the provider saw it.
    """
    import threading
    import time as _time

    import mailer

    started = threading.Event()
    finished = threading.Event()

    def slow():
        started.set()
        _time.sleep(0.25)
        finished.set()
        return True, "accepted"

    mailer._fire(slow)
    assert started.wait(2), "the job never started"
    assert mailer.pending_sends() >= 1, "an in-flight send must be visible"
    assert mailer.drain(5) is True, "drain must wait for the send"
    assert finished.is_set(), "the send was cut off"
    assert mailer.pending_sends() == 0, "the counter must return to zero"


def test_the_drain_is_bounded_and_never_raises(monkeypatch):
    import threading
    import time as _time

    import mailer

    release = threading.Event()

    def stuck():
        release.wait(5)
        return True, "accepted"

    mailer._fire(stuck)
    started = _time.monotonic()
    assert mailer.drain(0.2) is False, "a stuck provider must not block forever"
    assert _time.monotonic() - started < 3
    release.set()
    assert mailer.drain(5) is True
    assert mailer.pending_sends() == 0


def test_health_reports_the_notification_queue_but_never_fails_on_it():
    import health_checks
    body, status = health_checks.health_report()
    assert "mail" in body
    assert isinstance(body["mail"], dict)
    assert "pending" in body["mail"]
    assert status in (200, 500)      # the mail figure itself never decides
