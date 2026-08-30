"""Mail delivery: resend | smtp | none(kept on disk + console)."""
import smtplib, ssl, datetime
from email.message import EmailMessage
from config import Config

def _via_resend(to, subject, body):
    import urllib.request, json
    if not Config.RESEND_API_KEY:
        return False, "RESEND_API_KEY not set"
    payload = json.dumps({
        "from": Config.MAIL_FROM, "to": [to],
        "subject": subject, "text": body,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload,
        headers={"Authorization": f"Bearer {Config.RESEND_API_KEY}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status in (200, 201), f"resend {r.status}"
    except Exception as exc:
        return False, f"resend error: {exc}"

def send(to, subject, body):
    """Returns (delivered: bool, info: str). Never raises."""
    to = (to or "").strip()
    if not to:
        return False, "no recipient"
    mode = Config.MAIL_MODE
    if mode == "resend":
        return _via_resend(to, subject, body)
    if mode == "smtp":
        return _via_smtp_attached(to, subject, body, None, "", "", "", None)
    print(f"\n--- MAIL (MAIL_MODE=none, not actually sent) ---\nTo: {to}\nSubject: {subject}\n{body}\n------------------------------------------------\n", flush=True)
    return True, "stubbed (MAIL_MODE=none - logged to server console)"

def order_links(order_id):
    """Signed confirm / decline links for the email we send the shop.

    The link works from any mail client, so it cannot rely on a session - it
    is signed with SECRET_KEY instead, and only permits that one action on
    that one order.
    """
    import security as sec_          # imported here to keep imports one-way
    oid = str(order_id or "").strip().upper()
    if not oid or oid == "NO-ID":
        return ""
    base = str(Config.SITE_ORIGIN or "").rstrip("/")
    return (
        f"Confirm this payment  : {base}/confirm.html?id={oid}"
        f"&action=confirm&token={sec_.order_token(oid, 'confirm')}\n"
        f"Decline / refund it   : {base}/confirm.html?id={oid}"
        f"&action=decline&token={sec_.order_token(oid, 'decline')}"
    )


def send_with_reply_to(to, subject, body, reply_to):
    """A plain message whose Reply-To points somewhere else (the customer)."""
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"] = Config.MAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    if Config.MAIL_MODE == "resend":
        return _via_resend_attached(to, subject, body, None, "", "application/octet-stream", reply_to)
    if Config.MAIL_MODE != "smtp":
        return send(to, subject, body)
    try:
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=30) as s_:
            s_.ehlo()
            try:
                s_.starttls(context=ssl.create_default_context())
                s_.ehlo()
            except smtplib.SMTPException:
                pass
            if Config.SMTP_USER and Config.SMTP_PASS:
                s_.login(Config.SMTP_USER, Config.SMTP_PASS)
            s_.send_message(msg, from_addr=Config.MAIL_FROM, to_addrs=[to])
        return True, "sent"
    except Exception as exc:
        return False, f"smtp error: {exc}"


def send_order_notice(order, data=None, filename="", mime="application/octet-stream"):
    """Tell the shop owner that a checkout finished.

    When the customer uploaded a receipt with the order, the original file is
    attached - so the proof arrives by email, not only in the admin portal.
    """
    c = order.get("customer") or {}
    items = order.get("items") or []
    lines = [
        f"NEW ORDER {order.get('id')}",
        f"Date: {order.get('at')}",
        f"Name: {c.get('name') or ''}",
        f"Phone: {c.get('phone') or ''}",
        f"Email: {c.get('email') or ''}",
        f"Country: {c.get('country') or ''}",
        f"City: {c.get('city') or ''} / {c.get('zone') or ''}",
        f"Address: {c.get('address') or ''}",
        f"Note: {c.get('note') or ''}",
        f"Pay by: {order.get('payment') or ''}",
        f"Total: {order.get('total')} {order.get('currency')}",
        "Items:",
    ]
    lines += [f"  - {i.get('qty')}x {i.get('name')}"
              + (f" ({i.get('color')})" if i.get("color") else "")
              for i in items]
    if order.get("proofUrl"):
        lines.append(f"Payment screenshot: {Config.SITE_ORIGIN}{order['proofUrl']}"
                     if order["proofUrl"].startswith("/") else str(order["proofUrl"]))
    if data:
        lines.append(f"The customer's file ({filename}) is attached to this email, "
                     "exactly as they uploaded it.")
    lines.append("")
    links = order_links(order.get("id"))
    if links:
        lines += ["Confirm or decline this order right here - one tap, no sign in:", links, ""]
    lines.append(f"Admin portal: {Config.SITE_ORIGIN}/admin.html")
    subject = f"JauraStore order {order.get('id')}"
    body = "\n".join(lines)
    reply_to = (c.get("email") or "").strip()
    if reply_to:
        # the shop's own address is both sender and recipient here, so without
        # this, pressing Reply would write to yourself instead of the customer
        lines.append(f"Reply to this email to reach the customer: {c.get('name') or ''} <{reply_to}>")
        body = "\n".join(lines)
    if data:
        return send_with_attachment(Config.ADMIN_EMAILS[0], subject, body, data, filename,
                                    mime, reply_to=reply_to)
    if reply_to:
        return send_with_reply_to(Config.ADMIN_EMAILS[0], subject, body, reply_to)
    return send(Config.ADMIN_EMAILS[0], subject, body)


def send_receipt(order):
    """Customer receipt, sent when an admin confirms the payment."""
    c = order.get("customer") or {}
    to = (c.get("email") or "").strip()
    if not to:
        return False, "no customer email"
    items = order.get("items") or []
    lines = [
        "Thank you for patronising Jaura Store.",
        "",
        f"Your payment for order {order.get('id')} has been confirmed.",
        "",
        f"Name: {c.get('name') or ''}",
        f"Deliver to: {c.get('address') or ''}, {c.get('city') or ''} {c.get('zone') or ''}",
        f"Total paid: {order.get('total')} {order.get('currency')}",
        "",
        "ITEMS",
    ]
    lines += [f"  - {i.get('qty')}x {i.get('name')}"
              + (f" ({i.get('color')})" if i.get("color") else "")
              for i in items]
    lines += ["", "We will confirm your transport fare on WhatsApp using this order ID.",
              "", "With thanks,", "Jaura Store", Config.MAIL_FROM]
    return send(to, f"Jaura Store · payment confirmed · {order.get('id')}", "\n".join(lines))


def send_order_declined(order):
    """Tell the customer we could not confirm their payment."""
    c = order.get("customer") or {}
    to = (c.get("email") or "").strip()
    if not to:
        return False, "no customer email"
    lines = [
        f"Hello {c.get('name') or ''},".strip() + "\n",
        f"We could not confirm the payment for order {order.get('id')}.",
        "",
        "Most of the time this means the transfer had not reached us yet, or the",
        "reference did not match the order. Please reply to this email or message us",
        "on WhatsApp with your order ID and we will sort it out straight away.",
        "",
        f"Order total: {order.get('total')} {order.get('currency')}",
        "",
        "With thanks,", "Jaura Store", Config.MAIL_FROM,
    ]
    return send(to, f"Jaura Store · about order {order.get('id')}", "\n".join(lines))


def _attach(msg, data, filename, mime):
    """Attach the ORIGINAL bytes - never a re-rendered copy."""
    maintype, _, subtype = (mime or "application/octet-stream").partition("/")
    msg.add_attachment(data, maintype=maintype or "application",
                       subtype=subtype or "octet-stream", filename=filename)


def send_with_attachment(to, subject, body, data, filename, mime="application/pdf",
                         reply_to="", cc=None):
    """Email a message with the uploaded file attached, exactly as received.

    Returns (delivered: bool, info: str).
    """
    to = (to or "").strip()
    if not to:
        return False, "no recipient"
    mode = Config.MAIL_MODE
    if mode == "resend":
        return _via_resend_attached(to, subject, body, data, filename, mime, reply_to)
    if mode == "smtp":
        return _via_smtp_attached(to, subject, body, data, filename, mime, reply_to, cc)
    return _stub_with_attachment(to, subject, body, data, filename, mime)


def _via_smtp_attached(to, subject, body, data, filename, mime, reply_to, cc=None):
    if not Config.SMTP_HOST:
        return False, "SMTP not configured"
    msg = EmailMessage()
    msg["From"] = Config.MAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    if data:
        _attach(msg, data, filename, mime)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=30) as s:
            s.ehlo()
            try:
                s.starttls(context=ctx)
                s.ehlo()
            except smtplib.SMTPException:
                pass                      # plain relay (local testing only)
            if Config.SMTP_USER and Config.SMTP_PASS:
                s.login(Config.SMTP_USER, Config.SMTP_PASS)
            recipients = [to] + ([cc] if cc else [])
            s.send_message(msg, from_addr=Config.MAIL_FROM, to_addrs=recipients)
        return True, f"smtp sent ({len(data or b'')} bytes attached)"
    except Exception as exc:
        return False, f"smtp error: {exc}"


def _via_resend_attached(to, subject, body, data, filename, mime, reply_to):
    import base64, json, urllib.request
    if not Config.RESEND_API_KEY:
        return False, "RESEND_API_KEY not set"
    payload = {
        "from": Config.MAIL_FROM,
        "to": [to],
        "subject": subject,
        "text": body,
    }
    if reply_to:
        payload["reply_to"] = reply_to
    if data:
        payload["attachments"] = [{
            "filename": filename,
            "content": base64.b64encode(data).decode("ascii"),
            "content_type": mime,
        }]
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {Config.RESEND_API_KEY}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status in (200, 201), f"resend {r.status}"
    except Exception as exc:
        return False, f"resend error: {exc}"


def _stub_with_attachment(to, subject, body, data, filename, mime):
    """MAIL_MODE=none: keep the message AND the attachment on disk so nothing
    is silently lost while mail is not configured yet."""
    import os
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "outbox")
    try:
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
        path = os.path.join(folder, f"{stamp}-{filename}")
        with open(path, "wb") as fh:
            fh.write(data or b"")
        with open(path + ".txt", "w", encoding="utf-8") as fh:
            fh.write(f"To: {to}\nSubject: {subject}\nAttachment: {filename} ({mime})\n\n{body}\n")
    except Exception as exc:
        return False, f"could not store the message: {exc}"
    print(f"\n--- MAIL (MAIL_MODE=none, not sent) ---\nTo: {to}\nSubject: {subject}\n"
          f"Attachment kept at: {path}\n---------------------------------------\n", flush=True)
    return False, f"mail not configured (MAIL_MODE=none); copy kept at data/outbox"


def _amount_line(details):
    """Print the amount once, even if the customer already typed the currency."""
    amount = (details.get("amount") or "").strip()
    cur = (details.get("currency") or "").strip()
    if not amount:
        return cur or "-"
    low = amount.lower()
    if not cur or cur.lower() in low or any(
            token in low for token in ("cfa", "naira", "₦", "ngn", "fcfa", "f cfa")):
        return amount
    return f"{amount} {cur}"


def send_payment_proof(details, data, filename, mime):
    """The money email: everything the shop needs, plus the receipt attached."""
    to = Config.ADMIN_EMAILS[0]
    subject = f"Payment receipt · {details.get('orderId') or 'no order id'} · {details.get('name') or 'Customer'}"
    lines = [
        "A customer just uploaded a payment receipt.",
        "",
        f"Customer name : {details.get('name', '')}",
        f"Phone         : {details.get('phone', '')}",
        f"Email         : {details.get('email', '')}",
        f"Order ID      : {details.get('orderId', '')}"
        + (" (customer did not have one)" if str(details.get("orderId", "")).upper() == "NO-ID" else ""),
        f"Product(s)    : {details.get('items', '')}",
        f"Quantity      : {details.get('quantity', '')}",
        f"Payment method: {details.get('method', '')}",
        f"Amount paid   : {_amount_line(details)}",
    ]
    if details.get("total"):
        lines.append(f"Order total   : {details['total']}")
    lines.append(f"Sent at       : {details.get('at', '')}")
    if details.get("email"):
        lines.insert(1, f"Reply to this email to reach the customer: "
                        f"{details.get('name') or ''} <{details.get('email')}>")
    links = order_links(details.get("orderId"))
    if links:
        lines += ["", "Confirm or decline this order - one tap, no sign in:", links]
    if details.get("note"):
        lines += ["", f"Note from the customer: {details['note']}"]
    lines += [
        "",
        f"The receipt ({filename}) is attached to this email exactly as it was uploaded.",
        f"It is also stored in the admin portal: {Config.SITE_ORIGIN}/admin.html",
    ]
    delivered, info = send_with_attachment(
        to, subject, "\n".join(lines), data, filename, mime,
        reply_to=(details.get("email") or ""),
    )
    # a short confirmation back to the customer (no attachment)
    if details.get("email"):
        send(
            details["email"],
            f"Jaura Store · we received your payment receipt · {details.get('orderId', '')}",
            "Thank you. We have your payment receipt and will confirm your payment shortly.\n\n"
            f"Order ID: {details.get('orderId', '')}\n"
            f"Name: {details.get('name', '')}\n"
            f"Paid by: {details.get('method', '')}\n\n"
            "With thanks,\nJ Aura Store\n" + Config.MAIL_FROM,
        )
    return delivered, info


def send_otp(to, code, purpose="reset"):
    return send(
        to,
        "J Aura Store - admin verification code",
        f"Your admin verification code is: {code}\n\n"
        f"It expires in 10 minutes and can be used once.\n"
        f"If you did not request this, you can safely ignore this email.\n\n"
        f"- J Aura Store",
    )
