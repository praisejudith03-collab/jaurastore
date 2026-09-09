"""Resend mode is HTTPS-only and must never touch SMTP."""
from unittest.mock import patch

import emailer
from config import Config


def test_resend_mode_never_calls_smtp(monkeypatch):
    monkeypatch.setattr(Config, "MAIL_MODE", "resend")
    monkeypatch.setattr(Config, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(Config, "MAIL_FROM", "verified@example.com")
    with patch.object(emailer, "_deliver_smtp", side_effect=AssertionError("SMTP called")):
        with patch("urllib.request.urlopen") as open_url:
            response = open_url.return_value.__enter__.return_value
            response.status = 200
            assert emailer.send("owner@example.com", "subject", "body") == (
                True, emailer.RESEND_DELIVERED)
            open_url.assert_called_once()


def test_resend_missing_key_is_sanitized_and_does_not_call_smtp(monkeypatch):
    monkeypatch.setattr(Config, "MAIL_MODE", "resend")
    monkeypatch.setattr(Config, "RESEND_API_KEY", "")
    with patch.object(emailer, "_deliver_smtp", side_effect=AssertionError("SMTP called")):
        ok, diagnostic = emailer.send("owner@example.com", "subject", "body")
    assert not ok
    assert diagnostic == emailer.RESEND_MISSING_KEY
    assert "key" not in diagnostic.lower() or diagnostic == emailer.RESEND_MISSING_KEY


def test_resend_401_is_sanitized(monkeypatch):
    from urllib.error import HTTPError
    monkeypatch.setattr(Config, "MAIL_MODE", "resend")
    monkeypatch.setattr(Config, "RESEND_API_KEY", "test-key")
    with patch("urllib.request.urlopen", side_effect=HTTPError(
            "https://api.resend.com/emails", 401, "secret body", {}, None)):
        ok, diagnostic = emailer.send("owner@example.com", "subject", "body")
    assert not ok and diagnostic == emailer.RESEND_UNAUTHORIZED
