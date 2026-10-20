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
