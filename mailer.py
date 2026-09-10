"""Email the shop: new-order alerts + customer receipts (file attached).

Every paid order is emailed to the shop inbox, and when a customer uploads a
payment receipt the shop is emailed WITH THE CUSTOMER'S OWN FILE ATTACHED - the
exact bytes they uploaded, unmodified - so the payment can be confirmed from
the phone on the road without opening the admin portal.

Render's free/starter instances block outbound SMTP ports (25/465/587), so the
plain smtplib path cannot be relied on there. Mail therefore goes over HTTPS
first and only falls back to SMTP:

  1. Resend - RESEND_API_KEY        POST https://api.resend.com/emails
  2. Brevo  - BREVO_API_KEY         POST https://api.brevo.com/v3/smtp/email
  3. SMTP   - SMTP_HOST/PORT/USER/PASS (smtplib; covers local runs and any
       host with open SMTP - it will NOT work on Render free/starter instances)

Off unless MAIL_TO, MAIL_FROM and one provider are configured - local dev and
the test suite send nothing (see ENVIRONMENT_VARIABLES.md for the Render
setup). The admin Orders tab reads transport_status() to show which transport
is live and offers an "Email a test" button backed by POST /admin/mail/test.

Nothing here may break a sale or a receipt upload: send_mail never raises, and
callers fire the notify_* helpers from a daemon thread via the *_async wrappers.
"""
import base64
import json
import re

TIMEOUT = 20

_EMAIL_IN_BRACKETS = re.compile(r"^\s*(.*?)\s*<([^>]+)>\s*$")


def _cfg(name, default=""):
    import config as config_mod
    return getattr(config_mod, name, default) or default


def provider():
    """The live transport: "resend", "brevo", "smtp", or "" when none is set."""
    if _cfg("RESEND_API_KEY"):
        return "resend"
    if _cfg("BREVO_API_KEY"):
        return "brevo"
    if _cfg("SMTP_HOST"):
        return "smtp"
    return ""


def configured():
    """True when a transport AND both addresses are set (mail may go out)."""
    return bool(provider() and _cfg("MAIL_TO") and _cfg("MAIL_FROM"))


def transport_status():
    """What the admin Orders tab shows. Never returns a secret value."""
    prov = provider()
    missing = []
    if not _cfg("MAIL_TO"):
        missing.append("MAIL_TO")
    if not _cfg("MAIL_FROM"):
        missing.append("MAIL_FROM")
    if not prov:
        missing.append("RESEND_API_KEY (or BREVO_API_KEY / SMTP_HOST)")
    return {
        "enabled": not missing,
        "provider": prov,
        "to": _cfg("MAIL_TO"),
        "from": _cfg("MAIL_FROM"),
        "missing": missing,
    }


def _split_address(value):
    """'Jaura Store <orders@x.y>' -> ("Jaura Store", "orders@x.y"); a bare
    address yields ("", address)."""
    m = _EMAIL_IN_BRACKETS.match(value or "")
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", (value or "").strip()


def _http_post_json(url, headers, payload):
    """POST JSON, return (ok, detail). Never raises."""
    import urllib.request, urllib.error
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={**headers, "Content-Type": "application/json",
                 "User-Agent": "jaurastore-mailer"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = (r.read() or b"").decode("utf-8", "replace")[:300]
            return 200 <= r.status < 300, body or f"HTTP {r.status}"
    except urllib.error.HTTPError as exc:
        try:
            body = (exc.read() or b"").decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        return False, f"HTTP {exc.code}: {body}".strip()
    except Exception as exc:
        return False, str(exc)[:300]


def _send_resend(subject, html, attachments):
    payload = {
        "from": _cfg("MAIL_FROM"),
        "to": [_cfg("MAIL_TO")],
        "subject": subject,
        "html": html,
    }
    if attachments:
        payload["attachments"] = [
            {"filename": name, "content": base64.b64encode(data).decode("ascii")}
            for name, data, _mime in attachments]
    ok, detail = _http_post_json(
        "https://api.resend.com/emails",
        {"Authorization": "Bearer " + _cfg("RESEND_API_KEY")},
        payload)
    return ok, ("resend: " + (detail if not ok else "accepted"))


def _send_brevo(subject, html, attachments):
    sender_name, sender_email = _split_address(_cfg("MAIL_FROM"))
    sender = {"email": sender_email}
    if sender_name:
        sender["name"] = sender_name
    payload = {
        "sender": sender,
        "to": [{"email": _cfg("MAIL_TO")}],
        "subject": subject,
        "htmlContent": html,
    }
    if attachments:
        payload["attachment"] = [
            {"name": name, "content": base64.b64encode(data).decode("ascii")}
            for name, data, _mime in attachments]
    ok, detail = _http_post_json(
        "https://api.brevo.com/v3/smtp/email",
        {"api-key": _cfg("BREVO_API_KEY"), "accept": "application/json"},
        payload)
    return ok, ("brevo: " + (detail if not ok else "accepted"))


def _build_message(subject, html, attachments):
    from email.message import EmailMessage
    msg = EmailMessage()
    _name, sender_email = _split_address(_cfg("MAIL_FROM"))
    msg["From"] = sender_email or _cfg("MAIL_FROM")
    msg["To"] = _cfg("MAIL_TO")
    msg["Subject"] = subject
    text = re.sub(r"<[^>]+>", " ", html or "")
    msg.set_content(re.sub(r"\s+", " ", text).strip())
    msg.add_alternative(html or "", subtype="html")
    for name, data, mime in attachments:
        mime = (mime or "application/octet-stream").lower()
        if "/" not in mime:
            mime = "application/octet-stream"
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype,
                           filename=name)
    return msg


def _send_smtp(subject, html, attachments):
    import smtplib
    host = _cfg("SMTP_HOST")
    port = int(_cfg("SMTP_PORT", "587") or 587)
    msg = _build_message(subject, html, attachments)
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT)
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT)
        with server:
            if port != 465:
                server.starttls()
            user, password = _cfg("SMTP_USER"), _cfg("SMTP_PASS")
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        return True, "smtp: accepted"
    except Exception as exc:
        return False, "smtp: " + str(exc)[:300]


def send_mail(subject, html, attachments=()):
    """Send one email to the shop inbox over the first configured transport.

    `attachments` is a list of (filename, bytes, mime). Returns
    (ok, detail); NEVER raises - a mail problem must never break a sale,
    a receipt upload, or an admin action.
    """
    try:
        if not configured():
            missing = transport_status()["missing"]
            return False, "not configured: " + ", ".join(missing)
        prov = provider()
        if prov == "resend":
            return _send_resend(subject, html, attachments)
        if prov == "brevo":
            return _send_brevo(subject, html, attachments)
        return _send_smtp(subject, html, attachments)
    except Exception as exc:                      # pragma: no cover
        return False, "mailer error: " + str(exc)[:300]


# ------------------------------------------------------------------ contents
def _money(amount, currency):
    return f"{amount or ''} {currency or ''}".strip()


def order_email_html(order):
    """A compact HTML summary of a completed checkout."""
    rows = "".join(
        f"<tr><td>{(i.get('name') or '')[:80]}</td>"
        f"<td style='text-align:center'>{i.get('quantity') or 1}</td></tr>"
        for i in (order.get("items") or [])[:30])
    cust = order.get("customer") or {}
    return f"""<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
  <h2 style="margin:0 0 12px">New order {order.get('id', '')}</h2>
  <p style="margin:0 0 8px"><b>{cust.get('name', '')}</b> · {cust.get('phone', '')} · {cust.get('email', '')}</p>
  <p style="margin:0 0 8px">{cust.get('city', '')} {cust.get('address', '')}</p>
  <p style="margin:0 0 8px">Payment: {order.get('payment', '')}</p>
  <table style="border-collapse:collapse;margin:12px 0">{rows}</table>
  <p style="margin:0 0 4px"><b>Total: {_money(order.get('total'), order.get('currency'))}</b></p>
  <p style="margin:12px 0 0;color:#777">Sent automatically by the shop. Manage it in Admin &rarr; Orders.</p>
</div>"""


def receipt_email_html(proof):
    """A compact HTML summary of an uploaded payment receipt."""
    return f"""<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
  <h2 style="margin:0 0 12px">Payment receipt for {proof.get('order_id', '')}</h2>
  <p style="margin:0 0 8px"><b>{proof.get('name', '')}</b> · {proof.get('phone', '')} · {proof.get('email', '')}</p>
  <p style="margin:0 0 8px">Method: {proof.get('method', '')} · Amount: {proof.get('amount', '') or proof.get('total', '')}</p>
  <p style="margin:0 0 8px">Items: {proof.get('items', '')}</p>
  <p style="margin:0 0 8px">The customer's receipt file is attached to this email.</p>
  <p style="margin:12px 0 0;color:#777">Confirm the payment in Admin &rarr; Orders.</p>
</div>"""


def test_email_html():
    return """<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
  <h2 style="margin:0 0 12px">Jaura Store test email</h2>
  <p style="margin:0 0 8px">Your shop email transport is live. New orders and
  customer payment receipts (with the file attached) will arrive at this
  address.</p>
  <p style="margin:12px 0 0;color:#777">Sent from Admin &rarr; Orders &rarr; "Email a test".</p>
</div>"""


# ------------------------------------------------------------------ triggers
def notify_new_order(order):
    """Email the shop about a completed checkout. Returns (ok, detail)."""
    order = order or {}
    subject = (f"New order {order.get('id', '')}"
               + (f" — {_money(order.get('total'), order.get('currency'))}"
                  if order.get("total") else ""))
    return send_mail(subject, order_email_html(order))


def notify_receipt(proof, filename, data, mime):
    """Email the shop a customer's payment receipt WITH THE CUSTOMER'S OWN
    FILE ATTACHED (the exact bytes they uploaded). Returns (ok, detail)."""
    proof = dict(proof or {})
    subject = (f"Payment receipt for {proof.get('order_id', '')}"
               + (f" — {proof.get('name', '')}" if proof.get("name") else ""))
    return send_mail(subject, receipt_email_html(proof),
                     attachments=[(filename, data, mime)])


def _fire(fn, *args):
    import threading

    def _run():
        try:
            fn(*args)
        except Exception:
            pass  # mail is best-effort; never break the request that triggers it

    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        _run()


def notify_new_order_async(order):
    """notify_new_order off the request path (fire-and-forget)."""
    _fire(notify_new_order, dict(order or {}))


def notify_receipt_async(proof, filename, data, mime):
    """notify_receipt off the request path (fire-and-forget)."""
    _fire(notify_receipt, dict(proof or {}), filename, data, mime)
