"""SMTP failure categories + secret-free logging for admin reset mail."""
import os, smtplib, ssl, sys
from email.message import EmailMessage

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
ALLOWED_LOG_KEYS = (
    "mode=", "host=", "port=", "user_configured=", "password_configured=", "category=")


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


def _gmail(monkeypatch, port=587, password="abcdefghijklmnop"):
    monkeypatch.setattr(Config, "MAIL_MODE", "smtp")
    monkeypatch.setattr(Config, "MAIL_FROM", "other@example.com")
    monkeypatch.setattr(Config, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(Config, "SMTP_PORT", port)
    monkeypatch.setattr(Config, "SMTP_USER", "jaurastore@gmail.com")
    monkeypatch.setattr(Config, "SMTP_PASS", password)


def test_classify_gmail_535_is_smtp_authentication(monkeypatch):
    _gmail(monkeypatch)
    exc = smtplib.SMTPAuthenticationError(
        535, b"5.7.8 Username and Password not accepted. BadCredentials")
    assert emailer.classify_smtp_failure(exc) == "smtp_authentication"


def test_classify_gmail_534_is_smtp_authentication(monkeypatch):
    _gmail(monkeypatch)
    exc = smtplib.SMTPAuthenticationError(
        534, b"5.7.9 Application-specific password required")
    assert emailer.classify_smtp_failure(exc) == "smtp_authentication"


def test_classify_non_gmail_auth(monkeypatch):
    monkeypatch.setattr(Config, "MAIL_MODE", "smtp")
    monkeypatch.setattr(Config, "SMTP_HOST", "mail.example.com")
    exc = smtplib.SMTPAuthenticationError(535, b"auth failed")
    assert emailer.classify_smtp_failure(exc) == "smtp_authentication"


def test_classify_tls_and_connection(monkeypatch):
    _gmail(monkeypatch)
    assert emailer.classify_smtp_failure(ssl.SSLError("TLS handshake")) == "smtp_tls"
    assert emailer.classify_smtp_failure(TimeoutError("timed out")) == "smtp_connection"
    assert emailer.classify_smtp_failure(
        smtplib.SMTPConnectError(421, b"connection refused")) == "smtp_connection"


def test_classify_sender_and_recipient(monkeypatch):
    _gmail(monkeypatch)
    sender = smtplib.SMTPSenderRefused(550, b"5.7.1 not allowed to send", "other@x.com")
    assert emailer.classify_smtp_failure(sender) == "smtp_sender"
    rcpt = smtplib.SMTPRecipientsRefused({"x@y.com": (550, b"5.1.1 mailbox unavailable")})
    assert emailer.classify_smtp_failure(rcpt) == "smtp_recipient"


def test_classify_mail_mode_not_smtp(monkeypatch):
    monkeypatch.setattr(Config, "MAIL_MODE", "none")
    assert emailer.classify_smtp_failure(info="whatever") == "configuration"


def test_classify_resend_is_provider(monkeypatch):
    monkeypatch.setattr(Config, "MAIL_MODE", "resend")
    assert emailer.classify_smtp_failure(info="resend error: 401") == "smtp_provider"


def test_log_mail_event_only_safe_fields(capsys, monkeypatch):
    _gmail(monkeypatch, password="abcd efgh ijkl mnop")
    monkeypatch.setattr(Config, "RESEND_API_KEY", "re_secret_value")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "service-role-secret")
    emailer.log_mail_event(
        "smtp_authentication",
        "password=abcd efgh ijkl mnop token=ABCDEF code: 123456 "
        "re_secret_value service-role-secret")
    out = capsys.readouterr().out.strip()
    assert out.startswith("[mail] ")
    assert "mode=smtp" in out
    assert "host=smtp.gmail.com" in out
    assert "port=587" in out
    assert "user_configured=yes" in out
    assert "password_configured=yes" in out
    assert "category=smtp_authentication" in out
    for banned in ("abcd", "ijkl", "ABCDEF", "123456", "re_secret_value",
                   "service-role-secret", "SMTP_PASS", "extra=", "token=",
                   "password=", "jaurastore@gmail.com"):
        assert banned not in out, banned
    # nothing except the allowed keys
    assert " extra=" not in out


def test_gmail_app_password_spaces_and_quotes_are_stripped(monkeypatch):
    monkeypatch.setattr(Config, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(Config, "SMTP_PASS", '  "abcd efgh ijkl mnop"  ')
    assert emailer._smtp_pass() == "abcdefghijklmnop"
    monkeypatch.setattr(Config, "SMTP_PASS", "'wxyz abcd efgh ijkl'")
    assert emailer._smtp_pass() == "wxyzabcdefghijkl"


def test_mail_from_aligns_with_smtp_user(monkeypatch):
    _gmail(monkeypatch)
    assert emailer._gmail_from_header() == "jaurastore@gmail.com"
    monkeypatch.setattr(Config, "MAIL_FROM", "Jaura Store <jaurastore@gmail.com>")
    assert emailer._email_addr(emailer._gmail_from_header()).lower() == "jaurastore@gmail.com"


def test_port_587_starttls_before_login(monkeypatch):
    seq = []

    class Fake:
        def __init__(self, *a, **k):
            self._jaura_tls = False

        def connect(self, *a, **k):
            seq.append("connect")

        def ehlo(self):
            seq.append("ehlo")

        def starttls(self, context=None):
            seq.append("starttls")
            self._jaura_tls = True

        def login(self, user, password):
            seq.append("login")
            assert "starttls" in seq, "AUTH on 587 without STARTTLS"
            assert seq.index("starttls") < seq.index("login")
            assert password == "abcdefghijklmnop"

        def send_message(self, msg, from_addr=None, to_addrs=None):
            seq.append("send")
            assert from_addr == "jaurastore@gmail.com"

        def quit(self):
            seq.append("quit")

        def close(self):
            pass

    _gmail(monkeypatch, password=' "abcd efgh ijkl mnop" ')
    monkeypatch.setattr(emailer, "_SMTP4", Fake)
    monkeypatch.setattr(emailer, "_SMTP4_SSL", Fake)
    msg = EmailMessage()
    msg["From"] = emailer._gmail_from_header()
    msg["To"] = EMAIL
    msg.set_content("x")
    ok, info = emailer._deliver_smtp(msg, [EMAIL])
    assert ok is True, info
    assert seq.index("starttls") < seq.index("login")


def test_port_465_uses_ssl_not_starttls(monkeypatch):
    seen = []

    class SSLFake:
        def __init__(self, host, port, timeout=None, context=None):
            seen.append(("ssl", host, int(port), context is not None))
            self._jaura_tls = True

        def ehlo(self):
            seen.append("ehlo")

        def login(self, user, password):
            seen.append("login")

        def send_message(self, *a, **k):
            seen.append("send")

        def quit(self):
            seen.append("quit")

        def close(self):
            pass

    class PlainFake:
        def __init__(self, *a, **k):
            raise AssertionError("port 465 must not use plain SMTP")

    _gmail(monkeypatch, port=465)
    monkeypatch.setattr(emailer, "_SMTP4_SSL", SSLFake)
    monkeypatch.setattr(emailer, "_SMTP4", PlainFake)
    msg = EmailMessage()
    msg["From"] = "jaurastore@gmail.com"
    msg["To"] = EMAIL
    msg.set_content("x")
    ok, info = emailer._deliver_smtp(msg, [EMAIL])
    assert ok is True, info
    assert seen[0][0] == "ssl" and seen[0][2] == 465
    assert "login" in seen and "send" in seen


def test_gmail_587_falls_back_to_465_ssl(monkeypatch):
    attempts = []

    def fake_open(host, port, use_ssl, timeout=12):
        attempts.append((int(port), bool(use_ssl)))
        raise smtplib.SMTPConnectError(421, b"no")

    _gmail(monkeypatch, port=587)
    monkeypatch.setattr(emailer, "_open_smtp", fake_open)
    msg = EmailMessage()
    msg["To"] = EMAIL
    msg.set_content("x")
    ok, info = emailer._deliver_smtp(msg, [EMAIL])
    assert ok is False
    assert attempts[0] == (587, False)
    assert (465, True) in attempts


def test_otp_request_logs_category_on_auth_fail(client, monkeypatch, capsys):
    execute("DELETE FROM otp_codes")
    execute("DELETE FROM rate_limits")
    _gmail(monkeypatch, password="not-a-real-app-password")

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
    assert "category=smtp_authentication" in out
    assert "user_configured=yes" in out
    assert "password_configured=yes" in out
    assert "5.7.8" not in out
    assert "BadCredentials" not in out


def test_otp_request_db_failure_category(client, monkeypatch, capsys):
    execute("DELETE FROM rate_limits")

    def boom(email):
        raise RuntimeError("supabase unreachable")

    monkeypatch.setattr(authmod, "create_otp", boom)
    r = client.post("/api/admin/otp/request", json={"email": EMAIL})
    assert r.status_code == 502
    out = capsys.readouterr().out
    assert "category=configuration" in out
    assert "supabase unreachable" not in r.get_data(as_text=True)
    assert "supabase unreachable" not in out


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
        import re
        code = re.search(r"\b(\d{6})\b", raw).group(1)
        v = client.post("/api/admin/otp/verify", json={"email": EMAIL, "code": code})
        assert v.status_code == 200, v.data
        newpw = "MailReset9x"
        z = client.post("/api/admin/otp/reset", json={"newPassword": newpw})
        assert z.status_code == 200, z.data
        assert authmod.verify_login(EMAIL, newpw) is True
        authmod.set_shared_password(PW)


def test_bootstrap_password_not_applied_by_mailer(client):
    from config import Config as C
    assert C.BOOTSTRAP_ADMIN_PASSWORD == ""
    assert authmod.verify_login(EMAIL, PW) is True
