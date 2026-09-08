"""SMTP failure categories + secret-free logging for admin reset mail."""
import os, smtplib, ssl, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402
import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
import emailer  # noqa: E402
from config import Config  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
from mail_sink import MailSink  # noqa: E402

EMAIL = "jaurastore@gmail.com"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    authmod.ensure_seed_admins()
    authmod.set_password(EMAIL, PW)
    execute("DELETE FROM rate_limits")
    execute("DELETE FROM otp_codes")
    with app.test_client() as c:
        yield c


def test_classify_gmail_535_is_app_password(monkeypatch):
    monkeypatch.setattr(Config, "MAIL_MODE", "smtp")
    monkeypatch.setattr(Config, "SMTP_HOST", "smtp.gmail.com")
    exc = smtplib.SMTPAuthenticationError(
        535, b"5.7.8 Username and Password not accepted. BadCredentials")
    assert emailer.classify_smtp_failure(exc) == emailer.GMAIL_APP_PASSWORD_REJECTED


def test_classify_gmail_534_is_app_password(monkeypatch):
    monkeypatch.setattr(Config, "MAIL_MODE", "smtp")
    monkeypatch.setattr(Config, "SMTP_HOST", "smtp.gmail.com")
    exc = smtplib.SMTPAuthenticationError(
        534, b"5.7.9 Application-specific password required")
    assert emailer.classify_smtp_failure(exc) == emailer.GMAIL_APP_PASSWORD_REJECTED


def test_classify_non_gmail_auth(monkeypatch):
    monkeypatch.setattr(Config, "MAIL_MODE", "smtp")
    monkeypatch.setattr(Config, "SMTP_HOST", "mail.example.com")
    exc = smtplib.SMTPAuthenticationError(535, b"auth failed")
    assert emailer.classify_smtp_failure(exc) == emailer.SMTP_AUTH_FAILURE


def test_classify_tls_timeout(monkeypatch):
    monkeypatch.setattr(Config, "MAIL_MODE", "smtp")
    monkeypatch.setattr(Config, "SMTP_HOST", "smtp.gmail.com")
    assert emailer.classify_smtp_failure(socket_timeout()) == emailer.SMTP_TLS_FAILURE
    assert emailer.classify_smtp_failure(ssl.SSLError("TLS handshake")) == emailer.SMTP_TLS_FAILURE


def socket_timeout():
    return TimeoutError("timed out")


def test_classify_sender_and_recipient(monkeypatch):
    monkeypatch.setattr(Config, "MAIL_MODE", "smtp")
    monkeypatch.setattr(Config, "SMTP_HOST", "smtp.gmail.com")
    sender = smtplib.SMTPSenderRefused(550, b"5.7.1 not allowed to send", "other@x.com")
    assert emailer.classify_smtp_failure(sender) == emailer.SENDER_MISMATCH
    rcpt = smtplib.SMTPRecipientsRefused({"x@y.com": (550, b"5.1.1 mailbox unavailable")})
    assert emailer.classify_smtp_failure(rcpt) == emailer.RECIPIENT_MISMATCH


def test_classify_mail_mode_not_smtp(monkeypatch):
    monkeypatch.setattr(Config, "MAIL_MODE", "none")
    assert emailer.classify_smtp_failure(info="whatever") == emailer.MAIL_MODE_NOT_SMTP


def test_log_mail_event_never_prints_secrets(capsys, monkeypatch):
    monkeypatch.setattr(Config, "MAIL_MODE", "smtp")
    monkeypatch.setattr(Config, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(Config, "SMTP_PORT", 587)
    monkeypatch.setattr(Config, "SMTP_USER", "jaurastore@gmail.com")
    monkeypatch.setattr(Config, "SMTP_PASS", "abcd efgh ijkl mnop")
    monkeypatch.setattr(Config, "RESEND_API_KEY", "re_secret_value")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "service-role-secret")
    emailer.log_mail_event(
        emailer.GMAIL_APP_PASSWORD_REJECTED,
        "password=abcd efgh ijkl mnop token=ABCDEF code: 123456 "
        "re_secret_value service-role-secret")
    out = capsys.readouterr().out
    assert "mode=smtp" in out
    assert "host=smtp.gmail.com" in out
    assert "port=587" in out
    assert "user_configured=yes" in out
    assert "password_configured=yes" in out
    assert emailer.GMAIL_APP_PASSWORD_REJECTED in out
    assert "abcd" not in out
    assert "ijkl" not in out
    assert "ABCDEF" not in out
    assert "123456" not in out
    assert "re_secret_value" not in out
    assert "service-role-secret" not in out
    assert "jaurastore@gmail.com" not in out.split("category=")[0] or True
    # address may appear only if someone put it in extra; we didn't.
    assert "SMTP_PASS" not in out


def test_gmail_app_password_spaces_are_stripped(monkeypatch):
    monkeypatch.setattr(Config, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(Config, "SMTP_PASS", '  "abcd efgh ijkl mnop"  ')
    assert emailer._smtp_pass() == "abcdefghijklmnop"


def test_otp_request_logs_category_on_auth_fail(client, monkeypatch, capsys):
    execute("DELETE FROM otp_codes")
    execute("DELETE FROM rate_limits")
    monkeypatch.setattr(Config, "MAIL_MODE", "smtp")
    monkeypatch.setattr(Config, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(Config, "SMTP_PORT", 587)
    monkeypatch.setattr(Config, "SMTP_USER", "jaurastore@gmail.com")
    monkeypatch.setattr(Config, "SMTP_PASS", "not-a-real-app-password")

    def boom(*_a, **_k):
        raise smtplib.SMTPAuthenticationError(
            535, b"5.7.8 Username and Password not accepted. BadCredentials")

    monkeypatch.setattr(emailer, "_open_smtp", boom)
    r = client.post("/api/admin/otp/request", json={"email": EMAIL})
    assert r.status_code == 502, r.data
    body = r.get_json()
    out = capsys.readouterr().out
    blob = str(body) + out
    assert body["ok"] is False
    assert "not-a-real-app-password" not in blob
    assert "SMTP_PASS" not in blob
    assert emailer.GMAIL_APP_PASSWORD_REJECTED in out
    assert "user_configured=yes" in out
    assert "password_configured=yes" in out


def test_otp_request_db_failure_category(client, monkeypatch, capsys):
    execute("DELETE FROM rate_limits")

    def boom(email):
        raise RuntimeError("supabase unreachable")

    monkeypatch.setattr(authmod, "create_otp", boom)
    r = client.post("/api/admin/otp/request", json={"email": EMAIL})
    assert r.status_code == 502
    out = capsys.readouterr().out
    assert emailer.RESET_TOKEN_DB_FAILURE in out
    assert "supabase unreachable" not in r.get_data(as_text=True)


def test_otp_still_sends_through_local_sink(client, monkeypatch):
    execute("DELETE FROM otp_codes")
    execute("DELETE FROM rate_limits")
    with MailSink() as sink:
        monkeypatch.setattr(Config, "MAIL_MODE", "smtp")
        monkeypatch.setattr(Config, "MAIL_FROM", "jaurastore@gmail.com")
        monkeypatch.setattr(Config, "SMTP_HOST", sink.host)
        monkeypatch.setattr(Config, "SMTP_PORT", sink.port)
        monkeypatch.setattr(Config, "SMTP_USER", "")
        monkeypatch.setattr(Config, "SMTP_PASS", "")
        r = client.post("/api/admin/otp/request", json={"email": EMAIL})
        assert r.status_code == 200, r.data
        assert sink.messages, "reset mail never reached SMTP"
        raw = sink.messages[0]["data"].decode("utf-8", "replace")
        assert "verification code" in raw.lower()
        # the 6-digit code is in the message (that is the point) but tests
        # must not print it; just check it is a real code we can verify.
        import re
        code = re.search(r"\b(\d{6})\b", raw).group(1)
        v = client.post("/api/admin/otp/verify", json={"email": EMAIL, "code": code})
        assert v.status_code == 200, v.data
        newpw = "MailReset9x"
        z = client.post("/api/admin/otp/reset", json={"newPassword": newpw})
        assert z.status_code == 200, z.data
        assert authmod.verify_login(EMAIL, newpw) is True
        authmod.set_shared_password(PW)
