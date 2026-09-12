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
# Money in this shop is always a whole unit (naira / F CFA), so every figure
# below is an integer formatted with thousands separators. The line maths is
# done here, once: unit price x quantity = subtotal, and the sum of the
# subtotals is checked against the order total the server stored.
CURRENCY_SYMBOL = {"NGN": "\u20a6", "CFA": "F CFA", "XOF": "F CFA"}


def _amount(value):
    """A whole-unit amount from anything an order row may carry."""
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _money(amount, currency):
    """\u20a612,000 / 5,000 F CFA - never a bare number glued to a code."""
    cur = str(currency or "").strip().upper()
    n = _amount(amount)
    text = f"{n:,}"
    if cur == "NGN":
        return "\u20a6" + text
    if cur in ("CFA", "XOF"):
        return text + " F CFA"
    return (text + " " + cur).strip()


def _qty(item):
    """The quantity on one line, never below 1."""
    for key in ("quantity", "qty"):
        if item.get(key) is not None:
            n = _amount(item.get(key))
            if n > 0:
                return n
    return 1


def _variant(item):
    """The selected option/variant label for one line, or ""."""
    for key in ("variant", "color", "option", "selectedOption"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _line_maths(item):
    """(quantity, unit price, subtotal) for one order line.

    Orders store ``price`` as the LINE total (qty x unit). Older rows and
    hand-written payloads sometimes store the unit price instead, so the
    unit is derived defensively and the subtotal is always recomputed as
    unit x quantity - the two figures printed in the email can never
    disagree with each other.
    """
    qty = _qty(item)
    if item.get("unitPrice") is not None or item.get("unit_price") is not None:
        unit = _amount(item.get("unitPrice") if item.get("unitPrice") is not None
                       else item.get("unit_price"))
    else:
        line = _amount(item.get("price") if item.get("price") is not None
                       else item.get("total"))
        unit = int(round(line / qty)) if qty else line
    return qty, unit, unit * qty


def _items_table(order):
    """The line-item table: Product, Option, Unit price, Qty, Subtotal.

    Returns (html, computed_total). Every cell is HTML-escaped and every
    column is explicitly aligned, so no email client can run two values
    together or leave a stray character between them.
    """
    currency = order.get("currency")
    rows = []
    computed = 0
    for item in (order.get("items") or [])[:60]:
        item = dict(item or {})
        qty, unit, subtotal = _line_maths(item)
        computed += subtotal
        name = _esc(str(item.get("name") or "Item")[:120])
        variant = _variant(item)
        variant_html = (f"<div style=\"color:#6b6b6b;font-size:12px;margin-top:2px\">"
                        f"{_esc(variant[:80])}</div>" if variant else "")
        rows.append(
            "<tr>"
            f"<td style=\"padding:10px 12px;border-bottom:1px solid #eee;text-align:left\">"
            f"<div style=\"font-weight:600\">{name}</div>{variant_html}</td>"
            f"<td style=\"padding:10px 12px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap\">"
            f"{_esc(_money(unit, currency))}</td>"
            f"<td style=\"padding:10px 12px;border-bottom:1px solid #eee;text-align:center;white-space:nowrap\">"
            f"{qty}</td>"
            f"<td style=\"padding:10px 12px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap\">"
            f"<b>{_esc(_money(subtotal, currency))}</b></td>"
            "</tr>")
    if not rows:
        rows.append("<tr><td colspan=\"4\" style=\"padding:12px;color:#777\">"
                    "No items recorded on this order.</td></tr>")

    subtotal_stored = _amount(order.get("subtotal")) or computed
    discount = _amount(order.get("discount"))
    total = _amount(order.get("total"))
    if not total:
        total = max(0, subtotal_stored - discount)

    def _foot(label, value, bold=False, muted=False):
        weight = "600" if bold else "400"
        colour = "#777" if muted else "#222"
        size = "15px" if bold else "14px"
        return (
            "<tr>"
            f"<td colspan=\"3\" style=\"padding:8px 12px;text-align:right;color:{colour};"
            f"font-weight:{weight};font-size:{size}\">{_esc(label)}</td>"
            f"<td style=\"padding:8px 12px;text-align:right;color:{colour};"
            f"font-weight:{weight};font-size:{size};white-space:nowrap\">"
            f"{_esc(_money(value, order.get('currency')))}</td>"
            "</tr>")

    foot = _foot("Subtotal", subtotal_stored)
    if discount:
        foot += _foot("Discount", -discount, muted=True)
    foot += (
        "<tr>"
        "<td colspan=\"3\" style=\"padding:12px;text-align:right;font-size:16px;"
        "font-weight:700;border-top:2px solid #222\">Total</td>"
        "<td style=\"padding:12px;text-align:right;font-size:16px;font-weight:700;"
        f"border-top:2px solid #222;white-space:nowrap\">"
        f"{_esc(_money(total, order.get('currency')))}</td>"
        "</tr>")

    html = f"""<table role="presentation" cellpadding="0" cellspacing="0" width="100%"
    style="border-collapse:collapse;width:100%;margin:16px 0;font-size:14px;color:#222">
  <thead>
    <tr style="background:#faf6f1">
      <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e4d7c6;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#6b6b6b">Product</th>
      <th style="padding:10px 12px;text-align:right;border-bottom:2px solid #e4d7c6;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#6b6b6b">Unit price</th>
      <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #e4d7c6;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#6b6b6b">Qty</th>
      <th style="padding:10px 12px;text-align:right;border-bottom:2px solid #e4d7c6;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#6b6b6b">Subtotal</th>
    </tr>
  </thead>
  <tbody>{''.join(rows)}</tbody>
  <tfoot>{foot}</tfoot>
</table>"""
    return html, total


def _detail_rows(pairs):
    """A label / value block. Empty values are dropped entirely, so the
    email never shows a dangling label with nothing after it."""
    rows = []
    for label, value in pairs:
        text = str(value or "").strip()
        if not text:
            continue
        rows.append(
            "<tr>"
            f"<td style=\"padding:4px 12px 4px 0;color:#6b6b6b;white-space:nowrap;"
            f"vertical-align:top\">{_esc(label)}</td>"
            f"<td style=\"padding:4px 0;color:#222;vertical-align:top\">{_esc(text)}</td>"
            "</tr>")
    if not rows:
        return ""
    return ("<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" "
            "style=\"border-collapse:collapse;font-size:14px\">"
            + "".join(rows) + "</table>")


def _customer_name(order):
    cust = order.get("customer") or {}
    name = str(cust.get("name") or "").strip()
    if name:
        return name
    parts = [str(cust.get("firstName") or "").strip(),
             str(cust.get("lastName") or "").strip()]
    return " ".join(p for p in parts if p).strip()


def _shipping_address(order):
    cust = order.get("customer") or {}
    parts = [cust.get("address"), cust.get("city"), cust.get("zone"),
             cust.get("country")]
    return ", ".join(str(p).strip() for p in parts if str(p or "").strip())


def _payment_label(order):
    """A readable payment method, never a bare currency code."""
    payment = str(order.get("payment") or "").strip()
    currency = str(order.get("currency") or "").strip().upper()
    if payment and payment.upper() not in ("NGN", "CFA", "XOF"):
        return payment
    if currency == "NGN":
        return "Bank transfer (Naira)"
    if currency in ("CFA", "XOF"):
        return "Mobile money (F CFA)"
    return payment or "Bank transfer"


def _currency_label(currency):
    cur = str(currency or "").strip().upper()
    if cur == "NGN":
        return "Naira (\u20a6)"
    if cur in ("CFA", "XOF"):
        return "F CFA"
    return cur or ""


def _shell(title, intro, body):
    """The shared email frame: one centred card, safe fonts, no stray glyphs."""
    return f"""<div style="margin:0;padding:24px 12px;background:#f6f2ed;
  font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.6;color:#222">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
      style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:12px;
             border:1px solid #ece3d8;border-collapse:separate">
    <tr><td style="padding:24px 24px 8px">
      <div style="font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:#a97e48">Jaura Store</div>
      <h1 style="margin:8px 0 0;font-size:20px;line-height:1.3;color:#1c1c1c">{title}</h1>
      {f'<p style="margin:8px 0 0;color:#555">{intro}</p>' if intro else ''}
    </td></tr>
    <tr><td style="padding:8px 24px 24px">{body}</td></tr>
    <tr><td style="padding:0 24px 24px;color:#8a8a8a;font-size:12px;border-top:1px solid #f0e8de">
      <p style="margin:14px 0 0">Jaura Store &middot; Lagos, Nigeria &amp; Cotonou, Benin.
      Reply to this email if anything above looks wrong.</p>
    </td></tr>
  </table>
</div>"""


def _section(title):
    return (f'<h2 style="margin:22px 0 8px;font-size:14px;letter-spacing:.08em;'
            f'text-transform:uppercase;color:#a97e48">{_esc(title)}</h2>')


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


def _order_body(order, include_receipt=True):
    """The shared order summary body: who, where, how, and the item table."""
    cust = order.get("customer") or {}
    delivery = order.get("delivery") or {}
    items_html, _total = _items_table(order)

    who = _detail_rows([
        ("Name", _customer_name(order)),
        ("Email", cust.get("email") or order.get("email")),
        ("Phone", cust.get("phone")),
    ])
    where = _detail_rows([
        ("Address", _shipping_address(order)),
        ("Delivery zone", delivery.get("zone_name") or cust.get("zone") or order.get("zone")),
        ("Note", cust.get("note")),
    ])
    how = _detail_rows([
        ("Order ID", order.get("id")),
        ("Placed", order.get("at")),
        ("Payment method", _payment_label(order)),
        ("Currency", _currency_label(order.get("currency"))),
        ("Status", str(order.get("status") or "").title()),
    ])

    parts = []
    if who:
        parts.append(_section("Customer") + who)
    if where:
        parts.append(_section("Shipping address") + where)
    if how:
        parts.append(_section("Payment") + how)
    parts.append(_section("Items") + items_html)

    if include_receipt:
        proof = _absolute_url(order.get("proofUrl"))
        if proof:
            parts.append(
                '<p style="margin:12px 0 0">Payment receipt: '
                f'<a href="{_esc(proof, quote=True)}" style="color:#a97e48">'
                "view the file the customer uploaded</a></p>")
    return "".join(parts)


def order_email_html(order):
    """The shop-inbox copy of a completed checkout."""
    order = dict(order or {})
    origin = str(_cfg("SITE_ORIGIN", "") or "").rstrip("/")
    admin = (f'<p style="margin:18px 0 0"><a href="{_esc(origin, quote=True)}/admin.html" '
             'style="color:#a97e48">Open it in Admin &rarr; Orders</a></p>' if origin else "")
    return _shell(
        f"New order {_esc(str(order.get('id') or ''))}",
        "A customer has completed checkout. The full summary is below.",
        _order_body(order) + admin)


def receipt_email_html(proof):
    """A compact HTML summary of an uploaded payment receipt."""
    proof = dict(proof or {})
    body = _detail_rows([
        ("Order ID", proof.get("order_id")),
        ("Name", proof.get("name")),
        ("Email", proof.get("email")),
        ("Phone", proof.get("phone")),
        ("Method", proof.get("method")),
        ("Amount", proof.get("amount") or proof.get("total")),
        ("Items", proof.get("items")),
    ])
    return _shell(
        f"Payment receipt for {_esc(str(proof.get('order_id') or ''))}",
        "The customer's receipt file is attached to this email.",
        body + '<p style="margin:18px 0 0;color:#555">Confirm the payment in '
               "Admin &rarr; Orders.</p>")


def order_confirmed_email_html(order):
    """The customer-facing 'your order is confirmed' email."""
    order = dict(order or {})
    name = _customer_name(order)
    origin = str(_cfg("SITE_ORIGIN", "") or "").rstrip("/")
    account = (f'<p style="margin:18px 0 0"><a href="{_esc(origin, quote=True)}/account.html" '
               'style="color:#a97e48">See your order details</a></p>' if origin else "")
    greeting = (f'<p style="margin:0 0 12px">Hi {_esc(name)},</p>' if name else "")
    intro = (f'{greeting}<p style="margin:0 0 4px">Good news \u2014 we have confirmed your '
             f'order <b>{_esc(str(order.get("id") or ""))}</b> and it is being prepared '
             "for delivery.</p>")
    return _shell(
        f"Your order {_esc(str(order.get('id') or ''))} is confirmed",
        "",
        intro + _order_body(order, include_receipt=False) + account)


def order_received_email_html(order):
    """The customer-facing 'we have received your order' email."""
    order = dict(order or {})
    name = _customer_name(order)
    greeting = (f'<p style="margin:0 0 12px">Hi {_esc(name)},</p>' if name else "")
    intro = (f'{greeting}<p style="margin:0 0 4px">Thank you \u2014 we have received your '
             f'order <b>{_esc(str(order.get("id") or ""))}</b>. We will confirm your '
             "payment and agree your transport fare with you on WhatsApp.</p>")
    return _shell(
        f"Your order {_esc(str(order.get('id') or ''))} was received",
        "",
        intro + _order_body(order, include_receipt=False))


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


def notify_order_received(order):
    """Email the CUSTOMER their order summary the moment checkout completes.

    Same beautifully-structured summary as the confirmation email, worded as
    "we have received it". A missing/invalid customer email is a quiet no-op,
    and this never raises - checkout must never depend on mail.
    """
    order = dict(order or {})
    cust = order.get("customer") or {}
    to = str(cust.get("email") or order.get("email") or "").strip()
    if not _ADDRESS.fullmatch(to):
        return False, "no valid customer email on the order"
    subject = f"We received your Jaura Store order {order.get('id', '')}"
    return send_mail_to(to, subject, order_received_email_html(order))


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


def notify_order_received_async(order):
    """notify_order_received off the request path (fire-and-forget)."""
    _fire(notify_order_received, dict(order or {}))


def notify_order_confirmed_async(order):
    """notify_order_confirmed off the request path (fire-and-forget)."""
    _fire(notify_order_confirmed, dict(order or {}))
