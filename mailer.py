"""Email the shop: new-order alerts + customer receipts (file attached),
and email the customer when an order is confirmed.

Every paid order is emailed to the shop inbox, and when a customer uploads a
payment receipt the shop is emailed WITH THE CUSTOMER'S OWN FILE ATTACHED - the
exact bytes they uploaded, unmodified - so the payment can be confirmed from
the phone on the road without opening the admin portal. When an admin marks an
order CONFIRMED in the portal, the customer gets a confirmation email at the
address they checked out with.

Render's free/starter instances block outbound SMTP ports (25/465/587), so the
plain smtplib path cannot be relied on there. Mail therefore goes over HTTPS
first and only falls back to SMTP:

  1. Resend - RESEND_API_KEY        POST https://api.resend.com/emails
  2. Brevo  - BREVO_API_KEY         POST https://api.brevo.com/v3/smtp/email
  3. SMTP   - SMTP_HOST/PORT/USER/PASS (smtplib; covers local runs and any
       host with open SMTP - it will NOT work on Render free/starter instances)

Off until MAIL_FROM and one provider are configured - local dev and the test
suite send nothing (see ENVIRONMENT_VARIABLES.md for the Render setup). The
shop inbox is MAIL_TO when set, otherwise the primary ADMIN_EMAILS address
(jaurastore@gmail.com by default), so order alerts reach the owner without any
extra setup. The admin Orders tab reads transport_status() to show which
transport is live and offers an "Email a test" button backed by
POST /admin/mail/test.

Nothing here may break a sale, a receipt upload or an admin action:
  * send_mail / send_mail_to NEVER raise - they return (ok, detail);
  * every notify_* helper fired from a request path goes through the
    *_async wrappers, which run on a daemon thread (fire-and-forget), so
    email dispatch can never block an HTTP response or time the worker out;
  * failed attempts log one quiet "[mailer]" line to stdout (Render's log
    console) and nothing else.
"""
import base64
import json
import re
import threading
from html import escape as _esc

TIMEOUT = 20                 # seconds per provider attempt
PROBE_JOIN_SECONDS = 25      # the admin "Email a test" request waits at most this long

_EMAIL_IN_BRACKETS = re.compile(r"^\s*(.*?)\s*<([^>]+)>\s*$")
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# Where order alerts go when MAIL_TO is not set (the shop owner's inbox).
DEFAULT_SHOP_INBOX = "jaurastore@gmail.com"


def _cfg(name, default=""):
    """Read one mail setting.

    Looks at the config module first (tests monkeypatch module attributes
    there), then at config.Config where the environment variables land.
    The Config class is the production source: mail used to read only the
    module level and silently saw "" for every setting, which is why no
    email ever left the server even with Render fully configured.
    """
    import config as config_mod
    val = getattr(config_mod, name, None)
    if val is None or val == "":
        cfg_obj = getattr(config_mod, "Config", None)
        if cfg_obj is not None:
            val = getattr(cfg_obj, name, None)
    if val is None or val == "":
        return default
    return val


def _shop_inbox():
    """The inbox every shop email lands in: MAIL_TO when set, else the
    primary admin address (ADMIN_EMAILS[0], default jaurastore@gmail.com)."""
    to = str(_cfg("MAIL_TO", "") or "").strip()
    if to:
        return to
    admins = _cfg("ADMIN_EMAILS", "")
    if isinstance(admins, (list, tuple)):
        admins = ",".join(str(a) for a in admins)
    first = str(admins or "").split(",")[0].strip()
    return first or DEFAULT_SHOP_INBOX


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
    """True when a transport AND a sender address are set (mail may go out).

    The destination always resolves (MAIL_TO, else the primary admin inbox),
    so only MAIL_FROM and a provider key are required.
    """
    return bool(provider() and _cfg("MAIL_FROM"))


def transport_status():
    """What the admin Orders tab shows. Never returns a secret value."""
    prov = provider()
    missing = []
    if not _cfg("MAIL_FROM"):
        missing.append("MAIL_FROM")
    if not prov:
        missing.append("RESEND_API_KEY (or BREVO_API_KEY / SMTP_HOST)")
    return {
        "enabled": not missing,
        "provider": prov,
        "to": _shop_inbox(),
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


def _send_resend(to, subject, html, attachments):
    payload = {
        "from": _cfg("MAIL_FROM"),
        "to": [to],
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


def _send_brevo(to, subject, html, attachments):
    sender_name, sender_email = _split_address(_cfg("MAIL_FROM"))
    sender = {"email": sender_email}
    if sender_name:
        sender["name"] = sender_name
    payload = {
        "sender": sender,
        "to": [{"email": to}],
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


def _build_message(to, subject, html, attachments):
    from email.message import EmailMessage
    msg = EmailMessage()
    _name, sender_email = _split_address(_cfg("MAIL_FROM"))
    msg["From"] = sender_email or _cfg("MAIL_FROM")
    msg["To"] = to
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


def _send_smtp(to, subject, html, attachments):
    import smtplib
    host = _cfg("SMTP_HOST")
    port = int(_cfg("SMTP_PORT", "587") or 587)
    msg = _build_message(to, subject, html, attachments)
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


def send_mail_to(to, subject, html, attachments=()):
    """Send one email to ANY address over the first configured transport.

    Used for the customer confirmation email (the recipient is the shopper).
    `attachments` is a list of (filename, bytes, mime). Returns
    (ok, detail); NEVER raises - a mail problem must never break a sale,
    a receipt upload, or an admin action.
    """
    try:
        to = str(to or "").strip()
        if not _ADDRESS.fullmatch(to):
            return False, "invalid recipient: " + to[:80]
        if not configured():
            missing = transport_status()["missing"]
            return False, "not configured: " + ", ".join(missing)
        prov = provider()
        if prov == "resend":
            return _send_resend(to, subject, html, attachments)
        if prov == "brevo":
            return _send_brevo(to, subject, html, attachments)
        return _send_smtp(to, subject, html, attachments)
    except Exception as exc:                      # pragma: no cover
        return False, "mailer error: " + str(exc)[:300]


def send_mail(subject, html, attachments=()):
    """Send one email to the SHOP inbox (MAIL_TO, else the primary admin
    address) over the first configured transport. Returns (ok, detail);
    NEVER raises."""
    return send_mail_to(_shop_inbox(), subject, html, attachments)


# ------------------------------------------------------------------ contents
def _money(amount, currency):
    return f"{amount or ''} {currency or ''}".strip()


def _qty(item):
    return item.get("quantity") or item.get("qty") or 1


def _absolute_url(url):
    """Root-relative upload URLs (local/test storage) become absolute using
    SITE_ORIGIN; Supabase public URLs are already absolute and pass through."""
    url = str(url or "").strip()
    if not url or url.startswith(("http://", "https://")):
        return url
    origin = str(_cfg("SITE_ORIGIN", "") or "").rstrip("/")
    if not origin:
        return url
    return origin + (url if url.startswith("/") else "/" + url)


def order_email_html(order):
    """A compact HTML summary of a completed checkout, for the shop inbox:
    customer details, items, payment method, total and the receipt link."""
    order = order or {}
    rows = "".join(
        f"<tr><td>{(i.get('name') or '')[:80]}</td>"
        f"<td style='text-align:center'>{_qty(i)}</td></tr>"
        for i in (order.get("items") or [])[:30])
    cust = order.get("customer") or {}
    proof = _absolute_url(order.get("proofUrl"))
    proof_row = (f"<p style='margin:0 0 8px'>Receipt: "
                 f"<a href='{_esc(proof, quote=True)}'>{_esc(proof)}</a></p>"
                 if proof else "")
    return f"""<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
  <h2 style="margin:0 0 12px">New order {order.get('id', '')}</h2>
  <p style="margin:0 0 8px"><b>{cust.get('name', '')}</b> · {cust.get('phone', '')} · {cust.get('email', '')}</p>
  <p style="margin:0 0 8px">{cust.get('city', '')} {cust.get('address', '')}</p>
  <p style="margin:0 0 8px">Payment: {order.get('payment', '')}</p>
  {proof_row}
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


def order_confirmed_email_html(order):
    """The customer-facing 'your order is confirmed' email."""
    order = order or {}
    cust = order.get("customer") or {}
    rows = "".join(
        f"<tr><td>{(i.get('name') or '')[:80]}</td>"
        f"<td style='text-align:center'>{_qty(i)}</td></tr>"
        for i in (order.get("items") or [])[:30])
    delivery = order.get("delivery") or {}
    zone = delivery.get("zone_name") or order.get("zone") or ""
    origin = str(_cfg("SITE_ORIGIN", "") or "").rstrip("/")
    account = _esc(origin + "/account.html", quote=True) if origin else ""
    account_row = (f"<p style='margin:12px 0 0'>"
                   f"<a href='{account}'>See your order details</a></p>"
                   if account else "")
    return f"""<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
  <h2 style="margin:0 0 12px">Your order {order.get('id', '')} is confirmed ✅</h2>
  <p style="margin:0 0 8px">Hi {cust.get('name', '')},</p>
  <p style="margin:0 0 8px">Good news — we have confirmed your order
    <b>{order.get('id', '')}</b> and it is being prepared for delivery.</p>
  <table style="border-collapse:collapse;margin:12px 0">{rows}</table>
  <p style="margin:0 0 4px"><b>Total: {_money(order.get('total'), order.get('currency'))}</b></p>
  <p style="margin:0 0 8px">Payment: {order.get('payment', '')}</p>
  {f"<p style='margin:0 0 8px'>Delivery zone: {zone}</p>" if zone else ""}
  <p style="margin:12px 0 0;color:#777">If you have any questions, reply to this
  email or contact the shop. Thank you for shopping with Jaura Store.</p>
  {account_row}
</div>"""


def test_email_html():
    return """<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
  <h2 style="margin:0 0 12px">Jaura Store test email</h2>
  <p style="margin:0 0 8px">Your shop email transport is live. New orders and
  customer payment receipts (with the file attached) will arrive at this
  address, and customers will receive a confirmation email when you mark an
  order as confirmed.</p>
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


def notify_order_confirmed(order):
    """Email the CUSTOMER that their order has been confirmed by the shop.
    Returns (ok, detail); a missing/invalid customer email is a quiet no-op."""
    order = dict(order or {})
    cust = order.get("customer") or {}
    to = str(cust.get("email") or order.get("email") or "").strip()
    if not _ADDRESS.fullmatch(to):
        return False, "no valid customer email on the order"
    subject = f"Your Jaura Store order {order.get('id', '')} is confirmed"
    return send_mail_to(to, subject, order_confirmed_email_html(order))


# ------------------------------------------------------------------ dispatch
def _log(message):
    """One quiet line per mail event - stdout is Render's log console."""
    try:
        print(f"[mailer] {message}", flush=True)
    except Exception:
        pass


def _fire(fn, *args):
    """Run fn(*args) on a daemon thread (fire-and-forget).

    Never raises and never blocks the caller: this is what keeps SMTP/HTTPS
    dispatch off the HTTP request path so a slow provider cannot time a
    worker out. A failure inside fn is logged quietly and swallowed.
    """
    def _run():
        try:
            result = fn(*args)
            # the notify_* helpers return (ok, detail) - log quiet failures
            if (isinstance(result, tuple) and len(result) == 2
                    and result[0] is False):
                _log(f"{getattr(fn, '__name__', 'mail')} failed: {result[1]}")
        except Exception as exc:
            _log(f"{getattr(fn, '__name__', 'mail')} error: {str(exc)[:200]}")

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


def notify_order_confirmed_async(order):
    """notify_order_confirmed off the request path (fire-and-forget)."""
    _fire(notify_order_confirmed, dict(order or {}))
