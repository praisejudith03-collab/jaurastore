"""Receipt + order emails: the shop is mailed, with the file attached.

When a customer uploads a payment receipt the shop is emailed WITH THE
CUSTOMER'S OWN FILE ATTACHED - the exact bytes they uploaded - and every new
order emails the shop too. Render's free/starter instances block outbound
SMTP ports (25/465/587), so mailer.py sends over an HTTPS provider
(Resend first, then Brevo) and only falls back to SMTP when no HTTPS
provider is configured. Nothing is sent until MAIL_FROM, MAIL_TO and a
provider are configured, so local dev and the test suite stay silent.

The HTTP tests drive the admin portal's Orders tab: the transport status
line and the "Email a test" button, plus the end-to-end rule that the bytes
handed to the provider are the bytes the customer uploaded.

Run with:  python3 -m pytest tests/test_mailer.py -q
"""
import base64
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
import mailer  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

EMAIL = "jaurastore@gmail.com"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


@pytest.fixture()
def resend_env(monkeypatch):
    """A fully configured Resend transport; captures every send."""
    import config as config_mod
    sent = []

    def _fake_post(url, headers, payload):
        sent.append({"url": url, "headers": headers, "payload": payload})
        return True, "resend: accepted"

    monkeypatch.setattr(config_mod, "MAIL_FROM",
                        "Jaura Store <orders@jaurastore.com.ng>", raising=False)
    monkeypatch.setattr(config_mod, "MAIL_TO", EMAIL, raising=False)
    monkeypatch.setattr(config_mod, "RESEND_API_KEY", "re_test_key_123",
                        raising=False)
    monkeypatch.setattr(config_mod, "BREVO_API_KEY", "", raising=False)
    monkeypatch.setattr(config_mod, "SMTP_HOST", "", raising=False)
    monkeypatch.setattr(mailer, "_http_post_json", _fake_post)
    return sent


@pytest.fixture()
def unconfigured(monkeypatch):
    import config as config_mod
    for attr in ("MAIL_FROM", "MAIL_TO", "RESEND_API_KEY", "BREVO_API_KEY",
                 "SMTP_HOST"):
        monkeypatch.setattr(config_mod, attr, "", raising=False)


def _png(width=24, height=24):
    """A real, tiny PNG - the upload route decides the type from the bytes."""
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes([120, 90, 60]) * width for _ in range(height))

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _upload_proof(client, tok=None, order_id="JAUTEST-101"):
    """Upload a receipt the way the customer's browser does; return the bytes."""
    data = _png()
    tok = tok or client.get("/api/csrf").get_json()["token"]
    r = client.post("/api/payment-proof",
                    data={"file": (io.BytesIO(data), "my-receipt.png"),
                          "orderId": order_id,
                          "name": "Ada Obi", "phone": "+2290199001122",
                          "email": "ada@example.com",
                          "method": "MTN MoMo Benin (F CFA)",
                          "items": "1 x Shea butter", "amount": "5000",
                          "currency": "XOF"},
                    headers={"X-CSRF-Token": tok},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True
    return data


# ------------------------------------------------------------------ transport
class TestTransportSelection:
    def test_resend_wins_over_brevo_and_smtp(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "RESEND_API_KEY", "re_x", raising=False)
        monkeypatch.setattr(config_mod, "BREVO_API_KEY", "brevo_x", raising=False)
        monkeypatch.setattr(config_mod, "SMTP_HOST", "smtp.example", raising=False)
        assert mailer.provider() == "resend"

    def test_brevo_beats_smtp(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "RESEND_API_KEY", "", raising=False)
        monkeypatch.setattr(config_mod, "BREVO_API_KEY", "brevo_x", raising=False)
        monkeypatch.setattr(config_mod, "SMTP_HOST", "smtp.example", raising=False)
        assert mailer.provider() == "brevo"

    def test_smtp_when_no_https_provider(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "RESEND_API_KEY", "", raising=False)
        monkeypatch.setattr(config_mod, "BREVO_API_KEY", "", raising=False)
        monkeypatch.setattr(config_mod, "SMTP_HOST", "smtp.example", raising=False)
        assert mailer.provider() == "smtp"

    def test_status_reports_missing_pieces(self, unconfigured):
        st = mailer.transport_status()
        assert st["enabled"] is False and st["provider"] == ""
        # MAIL_TO is no longer required: the inbox defaults to the primary
        # admin address, so only the sender + a provider can be missing.
        assert set(st["missing"]) == {"MAIL_FROM",
                                      "RESEND_API_KEY (or BREVO_API_KEY / SMTP_HOST)"}
        assert st["to"] == EMAIL  # ADMIN_EMAILS[0] fallback

    def test_settings_read_from_config_class(self, monkeypatch):
        """Regression: _cfg used to read only module-level attributes, which
        never exist at runtime - so a fully configured Render deploy still
        sent nothing. It must fall through to config.Config."""
        import config as config_mod
        monkeypatch.setattr(config_mod, "MAIL_TO", "", raising=False)
        monkeypatch.setattr(config_mod, "RESEND_API_KEY", "", raising=False)
        monkeypatch.setattr(config_mod.Config, "RESEND_API_KEY", "re_from_class",
                            raising=False)
        assert mailer._cfg("RESEND_API_KEY") == "re_from_class"

    def test_shop_inbox_defaults_to_primary_admin(self, unconfigured):
        assert mailer._shop_inbox() == EMAIL
        assert mailer.send_mail("s", "<p>b</p>") == (
            False, "not configured: MAIL_FROM, "
                   "RESEND_API_KEY (or BREVO_API_KEY / SMTP_HOST)")

    def test_status_exposes_no_secret(self, resend_env):
        st = mailer.transport_status()
        assert st["enabled"] is True and st["provider"] == "resend"
        flat = repr(st)
        assert "re_test_key_123" not in flat


# ------------------------------------------------------------------ behaviour
class TestSendBehaviour:
    def test_resend_payload_carries_attachment(self, resend_env):
        data = b"\x89PNG\r\n\x1a\nEXACT-BYTES"
        ok, detail = mailer.notify_receipt(
            {"order_id": "JAUTEST-1", "name": "Ada", "phone": "+229...",
             "email": "ada@example.com", "method": "MTN MoMo Benin (F CFA)",
             "amount": "5000", "items": "1 x Shea butter"},
            "payment-JAUTEST-1-my-receipt.png", data, "image/png")
        assert ok, detail
        payload = resend_env[0]["payload"]
        assert payload["to"] == [EMAIL]
        assert payload["from"] == "Jaura Store <orders@jaurastore.com.ng>"
        att = payload["attachments"][0]
        assert att["filename"] == "payment-JAUTEST-1-my-receipt.png"
        assert base64.b64decode(att["content"]) == data  # byte-identical
        assert "JAUTEST-1" in payload["subject"]

    def test_brevo_payload_shape(self, resend_env, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "RESEND_API_KEY", "", raising=False)
        monkeypatch.setattr(config_mod, "BREVO_API_KEY", "brevo_x", raising=False)
        data = b"PDFBYTES"
        ok, detail = mailer.notify_receipt(
            {"order_id": "JAUTEST-2"}, "payment-JAUTEST-2-slip.pdf", data,
            "application/pdf")
        assert ok, detail
        call = resend_env[0]
        assert call["url"] == "https://api.brevo.com/v3/smtp/email"
        assert call["headers"]["api-key"] == "brevo_x"
        att = call["payload"]["attachment"][0]
        assert base64.b64decode(att["content"]) == data
        assert att["name"] == "payment-JAUTEST-2-slip.pdf"

    def test_smtp_fallback_used_without_https_provider(self, resend_env,
                                                       monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "RESEND_API_KEY", "", raising=False)
        monkeypatch.setattr(config_mod, "SMTP_HOST", "smtp.example", raising=False)
        smtpd = {}

        class _FakeSMTP:
            def __init__(self, host, port, timeout=None):
                smtpd["host"] = host

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def starttls(self):
                smtpd["tls"] = True

            def login(self, u, p):
                smtpd["auth"] = (u, p)

            def send_message(self, msg):
                smtpd["attachments"] = [p.get_filename()
                                        for p in msg.iter_attachments()]

        import smtplib
        monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
        ok, detail = mailer.notify_receipt(
            {"order_id": "JAUTEST-3"}, "payment-JAUTEST-3-r.png", b"PNG",
            "image/png")
        assert ok, detail
        assert smtpd["host"] == "smtp.example" and smtpd["tls"] is True
        assert smtpd["attachments"] == ["payment-JAUTEST-3-r.png"]

    def test_smtp_ssl_port_465(self, resend_env, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "RESEND_API_KEY", "", raising=False)
        monkeypatch.setattr(config_mod, "SMTP_HOST", "smtp.example", raising=False)
        smtpd = {}

        class _FakeSMTPSSL:
            def __init__(self, host, port, timeout=None):
                smtpd["ssl"] = (host, port)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def send_message(self, msg):
                smtpd["sent"] = True

        import smtplib
        monkeypatch.setattr(config_mod, "SMTP_PORT", 465, raising=False)
        monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTPSSL)
        ok, _ = mailer.send_mail("s", "<p>body</p>")
        assert ok and smtpd["ssl"] == ("smtp.example", 465) and smtpd["sent"]

    def test_nothing_sent_when_unconfigured(self, unconfigured, monkeypatch):
        def _boom(*a):
            raise AssertionError("no provider configured - nothing may send")

        monkeypatch.setattr(mailer, "_http_post_json", _boom)
        ok, detail = mailer.send_mail("s", "<p>body</p>")
        assert ok is False and "MAIL_FROM" in detail

    def test_provider_failure_never_raises(self, resend_env, monkeypatch):
        def _fail(url, headers, payload):
            return False, "HTTP 422: sender not verified"

        monkeypatch.setattr(mailer, "_http_post_json", _fail)
        ok, detail = mailer.notify_receipt({"order_id": "X"}, "f.png", b"a",
                                           "image/png")
        assert ok is False and "resend" in detail

    def test_html_content_summary(self, resend_env):
        ok, _ = mailer.notify_new_order(
            {"id": "JAUTEST-9", "total": 12000, "currency": "NGN",
             "payment": "UBA bank transfer (₦ Naira)",
             "customer": {"name": "Ada Obi", "phone": "+229...", "city": "Cotonou"},
             "items": [{"name": "Power bank", "quantity": 1}]})
        assert ok
        html = resend_env[0]["payload"]["html"]
        assert "JAUTEST-9" in html and "Ada Obi" in html and "UBA" in html


# ------------------------------------------------------------- HTTP: upload
class TestReceiptUploadEmailsTheShop:
    def test_customer_bytes_reach_the_provider_intact(self, client, monkeypatch,
                                                      resend_env):
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "testing", raising=False)
        captured = {}

        def _capture(proof, filename, data, mime):
            captured.update(proof=proof, filename=filename, data=data, mime=mime)
            return True, "resend: accepted"

        monkeypatch.setattr(mailer, "notify_receipt", _capture)
        monkeypatch.setattr(mailer, "_fire", lambda fn, *a: fn(*a))  # inline
        tok = login(client)
        data = _upload_proof(client, tok)
        assert captured["data"] == data, "the shop must get the exact bytes"
        assert captured["mime"] == "image/png"
        assert captured["filename"].startswith("payment-")
        assert captured["proof"]["order_id"] == "JAUTEST-101"

    def test_upload_survives_a_mail_failure(self, client, monkeypatch,
                                            resend_env):
        # Mail is an extra channel: a provider outage must not fail the
        # customer's receipt upload.
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "testing", raising=False)

        def _boom(proof, filename, data, mime):
            raise RuntimeError("provider down")

        monkeypatch.setattr(mailer, "notify_receipt", _boom)
        monkeypatch.setattr(mailer, "_fire", lambda fn, *a: fn(*a))  # inline
        tok = login(client)
        r = client.post("/api/payment-proof",
                        data={"file": (io.BytesIO(_png()), "r.png"), "orderId": "JAUTEST-7",
                              "name": "Ada", "phone": "+2290199001122",
                              "email": "ada@example.com",
                              "method": "MTN MoMo Benin (F CFA)",
                              "items": "1 x item", "amount": "3000",
                              "currency": "XOF"},
                        headers={"X-CSRF-Token": tok},
                        content_type="multipart/form-data")
        assert r.status_code == 200, r.data
        assert r.get_json()["ok"] is True

    def test_unconfigured_shop_still_accepts_receipts(self, client,
                                                      monkeypatch,
                                                      unconfigured):
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "testing", raising=False)

        def _boom(*a, **k):
            raise AssertionError("nothing may send while unconfigured")

        monkeypatch.setattr(mailer, "_http_post_json", _boom)
        monkeypatch.setattr(mailer, "_fire", lambda fn, *a: fn(*a))  # inline
        tok = login(client)
        r = client.post("/api/payment-proof",
                        data={"file": (io.BytesIO(_png()), "r.png"), "orderId": "JAUTEST-8",
                              "name": "Ada", "phone": "+2290199001122",
                              "email": "ada@example.com",
                              "method": "MTN MoMo Benin (F CFA)",
                              "items": "1 x item", "amount": "3000",
                              "currency": "XOF"},
                        headers={"X-CSRF-Token": tok},
                        content_type="multipart/form-data")
        assert r.status_code == 200 and r.get_json()["ok"] is True


# -------------------------------------------------- HTTP: admin Orders tab
class TestOrdersTabEndpoints:
    def test_status_when_configured(self, client, resend_env):
        tok = login(client)
        r = client.get("/api/admin/mail/status", headers={"X-CSRF-Token": tok})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True and body["enabled"] is True
        assert body["provider"] == "resend"
        assert body["to"] == EMAIL
        assert "re_test_key_123" not in r.data.decode()

    def test_status_when_unconfigured(self, client, unconfigured):
        tok = login(client)
        r = client.get("/api/admin/mail/status", headers={"X-CSRF-Token": tok})
        assert r.status_code == 200
        body = r.get_json()
        assert body["enabled"] is False and body["missing"]

    def test_status_requires_admin(self, client):
        r = client.get("/api/admin/mail/status")
        assert r.status_code in (401, 403)

    def test_email_a_test_when_configured(self, client, resend_env):
        tok = login(client)
        r = client.post("/api/admin/mail/test", json={},
                        headers={"X-CSRF-Token": tok})
        assert r.status_code == 200, r.data
        body = r.get_json()
        assert body["ok"] is True and body["provider"] == "resend"
        assert resend_env[0]["payload"]["subject"] == "Jaura Store test email"

    def test_email_a_test_reports_missing_config(self, client, unconfigured):
        tok = login(client)
        r = client.post("/api/admin/mail/test", json={},
                        headers={"X-CSRF-Token": tok})
        assert r.status_code == 400
        body = r.get_json()
        assert body["ok"] is False and body["missing"]

    def test_email_a_test_surfaces_provider_failure(self, client, resend_env,
                                                    monkeypatch):
        monkeypatch.setattr(mailer, "_http_post_json",
                            lambda *a, **k: (False, "HTTP 403: denied"))
        tok = login(client)
        r = client.post("/api/admin/mail/test", json={},
                        headers={"X-CSRF-Token": tok})
        assert r.status_code == 502
        assert "403" in r.get_json()["error"]

    def test_email_a_test_requires_auth(self, client, resend_env):
        # require_admin runs before the CSRF check, so a signed-out POST is 401.
        r = client.post("/api/admin/mail/test", json={})
        assert r.status_code == 401


# --------------------------------------------------- the async fire-and-forget
class TestAsyncWrappers:
    def test_notify_receipt_async_runs_off_thread(self, resend_env,
                                                  monkeypatch):
        # The request path must not block on the provider: _fire starts a
        # thread. Inline-run it here and prove the send still happens.
        import threading
        started = []

        class _InlineThread:
            def __init__(self, target=None, daemon=None, **k):
                started.append(1)
                self._t = target

            def start(self):
                self._t()

        monkeypatch.setattr(threading, "Thread", _InlineThread)
        mailer.notify_receipt_async({"order_id": "A"}, "f.png", b"d",
                                    "image/png")
        assert started == [1]
        assert len(resend_env) == 1, "the shop email must go out"
        assert base64.b64decode(
            resend_env[0]["payload"]["attachments"][0]["content"]) == b"d"

    def test_async_swallows_exceptions(self, resend_env, monkeypatch):
        monkeypatch.setattr(mailer, "notify_new_order",
                            lambda order: (_ for _ in ()).throw(RuntimeError("x")))
        mailer.notify_new_order_async({"id": "B"})  # must not raise


# ========================================== order emails: admin + customer
def _store_order(oid="JA-MAILTEST-1", email="praisejudith03@gmail.com",
                 status="pending", proof=""):
    """Insert an order the way a completed checkout does; return its id."""
    execute("DELETE FROM orders WHERE id=?", (oid,))
    payload = {"id": oid, "total": 12000, "currency": "NGN", "status": status,
               "payment": "UBA bank transfer (₦ Naira)", "proofUrl": proof,
               "customer": {"name": "Praise Judith", "email": email,
                            "phone": "+22968953110", "city": "Cotonou"},
               "items": [{"id": "wix-001", "name": "Shea butter", "qty": 2,
                          "price": 6000}]}
    execute(
        "INSERT INTO orders (id, payload, email, customer_name, phone, country, "
        "city, zone, address, note, payment, proof_url, items_count, total, "
        "currency, source, status, at, updated_at) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (oid, json.dumps(payload), email, "Praise Judith", "", "",
         "", "Cotonou", "", "", "UBA bank transfer (₦ Naira)", proof, 1,
         12000, "NGN", "web", status,
         "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
    return oid


class TestAdminNewOrderEmail:
    def test_order_email_carries_the_receipt_link(self, resend_env):
        ok, detail = mailer.notify_new_order(
            {"id": "JA-WI2OSD", "total": 12000, "currency": "NGN",
             "payment": "UBA bank transfer (₦ Naira)",
             "proofUrl": "https://example.supabase.co/storage/v1/object/public/proofs/r.png",
             "customer": {"name": "Praise Judith", "phone": "+229...",
                          "email": "praisejudith03@gmail.com",
                          "city": "Cotonou"},
             "items": [{"name": "Shea butter", "qty": 2}]})
        assert ok, detail
        payload = resend_env[0]["payload"]
        assert payload["to"] == [EMAIL]            # the shop inbox
        html_body = payload["html"]
        assert "JA-WI2OSD" in html_body            # order id
        assert "Praise Judith" in html_body        # customer details
        assert "Shea butter" in html_body          # items
        assert "UBA" in html_body                  # payment method
        assert "\u20a612,000" in html_body        # total, formatted
        assert ("https://example.supabase.co/storage/v1/object/public/proofs/r.png"
                in html_body)                      # receipt link

    def test_relative_receipt_url_becomes_absolute(self, resend_env):
        import config as config_mod
        monkeypatch_target = config_mod
        # SITE_ORIGIN comes from Config in production
        monkeypatch_target.Config.SITE_ORIGIN = "https://jaurastore.com.ng"
        try:
            ok, _ = mailer.notify_new_order(
                {"id": "JA-REL", "proofUrl": "/uploads/proofs/r.png",
                 "customer": {}, "items": []})
            assert ok
            assert "https://jaurastore.com.ng/uploads/proofs/r.png" \
                in resend_env[0]["payload"]["html"]
        finally:
            monkeypatch_target.Config.SITE_ORIGIN = "http://localhost:8080"

    def test_item_quantities_use_qty_field(self, resend_env):
        """Checkout items store 'qty' - the email used to read only
        'quantity' and always showed 1."""
        ok, _ = mailer.notify_new_order(
            {"id": "JA-QTY", "items": [{"name": "Soap", "qty": 3}]})
        assert ok
        assert ">3</td>" in resend_env[0]["payload"]["html"]


class TestCustomerConfirmationEmail:
    def test_confirmation_goes_to_the_customer(self, resend_env):
        ok, detail = mailer.notify_order_confirmed(
            {"id": "JA-WI2OSD", "total": 12000, "currency": "NGN",
             "payment": "UBA bank transfer (₦ Naira)",
             "customer": {"name": "Praise Judith",
                          "email": "praisejudith03@gmail.com"},
             "items": [{"name": "Shea butter", "qty": 2}]})
        assert ok, detail
        payload = resend_env[0]["payload"]
        assert payload["to"] == ["praisejudith03@gmail.com"]  # the CUSTOMER
        assert "JA-WI2OSD" in payload["subject"]
        html_body = payload["html"]
        assert "confirmed" in html_body.lower()
        assert "Praise Judith" in html_body and "Shea butter" in html_body
        assert "\u20a612,000" in html_body

    def test_no_valid_customer_email_is_a_quiet_no_op(self, resend_env):
        ok, detail = mailer.notify_order_confirmed({"id": "JA-NOEMAIL",
                                                    "customer": {}})
        assert ok is False and "customer email" in detail
        assert resend_env == []                    # nothing went out

    def test_confirmation_async_runs_off_thread(self, resend_env,
                                                monkeypatch):
        import threading
        started = []

        class _InlineThread:
            def __init__(self, target=None, daemon=None, **k):
                started.append(1)
                self._t = target

            def start(self):
                self._t()

        monkeypatch.setattr(threading, "Thread", _InlineThread)
        mailer.notify_order_confirmed_async(
            {"id": "JA-ASYNC", "customer": {"email": "x@example.com"}})
        assert started == [1]
        assert len(resend_env) == 1

    def test_confirmation_async_swallows_exceptions(self, monkeypatch):
        monkeypatch.setattr(mailer, "notify_order_confirmed",
                            lambda order: (_ for _ in ()).throw(RuntimeError("x")))
        mailer.notify_order_confirmed_async({"id": "JA-BOOM"})  # must not raise


class TestCustomerOrderNoticeEmail:
    def _order(self, country="Benin", kind="partial_payment"):
        message = ("You have a pending balance. Please contact us on WhatsApp to "
                   "balance up your payment before your order is confirmed.")
        return {
            "id": "JA-NOTICE", "total": 12000, "currency": "NGN",
            "customer": {"name": "Customer", "email": "buyer@example.com",
                         "country": country},
            "customer_notice": {"type": kind, "message": message},
            "payment_review": {"total": 12000, "paid": 5000,
                               "balance": 7000, "currency": "NGN"},
            "items": [{"name": "Bag", "qty": 1, "price": 12000}],
        }

    def test_pending_balance_copy_and_benin_whatsapp_are_in_email(self, resend_env):
        order = self._order()
        ok, detail = mailer.notify_order_notice(order)
        assert ok, detail
        body = resend_env[0]["payload"]["html"]
        assert order["customer_notice"]["message"] in body
        assert "Pending balance" in body and "\u20a67,000" in body
        assert "https://wa.me/22968953110" in body
        assert "2290168953110" not in body

    def test_nigerian_notice_routes_to_nigeria_whatsapp(self, resend_env):
        ok, detail = mailer.notify_order_notice(self._order(country="Nigeria"))
        assert ok, detail
        body = resend_env[0]["payload"]["html"]
        assert "https://wa.me/2349161670236" in body


class TestConfirmStatusTriggersCustomerEmail:
    """PATCH /admin/orders/<oid> -> confirmed must email the customer without
    ever blocking or failing the admin action."""

    def _patch(self, client, tok, oid, status):
        return client.patch(f"/api/admin/orders/{oid}", json={"status": status},
                            headers={"X-CSRF-Token": tok})

    def test_confirm_sends_customer_email(self, client, resend_env, monkeypatch):
        captured = {}

        def _capture(order):
            captured.update(order)
            return True, "resend: accepted"

        monkeypatch.setattr(mailer, "notify_order_confirmed", _capture)
        monkeypatch.setattr(mailer, "_fire", lambda fn, *a: fn(*a))  # inline
        tok = login(client)
        oid = _store_order()
        r = self._patch(client, tok, oid, "confirmed")
        assert r.status_code == 200, r.data
        assert r.get_json()["status"] == "confirmed"
        assert captured["id"] == oid
        assert captured["customer"]["email"] == "praisejudith03@gmail.com"
        # the DB update happened too - mail is not a dependency
        row = one("SELECT status FROM orders WHERE id=?", (oid,))
        assert row["status"] == "confirmed"

    def test_decline_and_reopen_send_nothing(self, client, resend_env,
                                             monkeypatch):
        fired = []

        def _capture(order):
            fired.append(order.get("id"))
            return True, "sent"

        monkeypatch.setattr(mailer, "notify_order_confirmed", _capture)
        monkeypatch.setattr(mailer, "_fire", lambda fn, *a: fn(*a))  # inline
        tok = login(client)
        oid = _store_order("JA-MAILTEST-2")
        for status in ("declined", "pending"):
            r = self._patch(client, tok, oid, status)
            assert r.status_code == 200, r.data
        assert fired == []

    def test_resaving_confirmed_does_not_resend(self, client, resend_env,
                                                 monkeypatch):
        fired = []

        def _capture(order):
            fired.append(order.get("id"))
            return True, "sent"

        monkeypatch.setattr(mailer, "notify_order_confirmed", _capture)
        monkeypatch.setattr(mailer, "_fire", lambda fn, *a: fn(*a))  # inline
        tok = login(client)
        oid = _store_order("JA-MAILTEST-3")
        assert self._patch(client, tok, oid, "confirmed").status_code == 200
        assert self._patch(client, tok, oid, "confirmed").status_code == 200
        assert fired == [oid]                     # exactly one email

    def test_mail_failure_never_breaks_the_status_update(self, client,
                                                         resend_env,
                                                         monkeypatch):
        def _boom(order):
            raise RuntimeError("provider down")

        monkeypatch.setattr(mailer, "notify_order_confirmed", _boom)
        monkeypatch.setattr(mailer, "_fire", lambda fn, *a: fn(*a))  # inline
        tok = login(client)
        oid = _store_order("JA-MAILTEST-4")
        r = self._patch(client, tok, oid, "confirmed")
        assert r.status_code == 200, r.data
        assert r.get_json()["ok"] is True
        row = one("SELECT status FROM orders WHERE id=?", (oid,))
        assert row["status"] == "confirmed"

    def test_customer_email_falls_back_to_the_order_row(self, client,
                                                        resend_env,
                                                        monkeypatch):
        """Legacy orders whose payload has no customer dict still confirm:
        the email comes from the orders.email column."""
        captured = {}

        def _capture(order):
            captured.update(order)
            return True, "sent"

        monkeypatch.setattr(mailer, "notify_order_confirmed", _capture)
        monkeypatch.setattr(mailer, "_fire", lambda fn, *a: fn(*a))  # inline
        tok = login(client)
        oid = _store_order("JA-MAILTEST-5")          # row email is set
        execute("UPDATE orders SET payload=? WHERE id=?",
                (json.dumps({"id": oid, "total": 5000, "currency": "NGN",
                             "items": []}), oid))
        r = self._patch(client, tok, oid, "confirmed")
        assert r.status_code == 200, r.data
        assert captured["customer"]["email"] == "praisejudith03@gmail.com"
