"""Mail delivery: resend | smtp | none(kept on disk + console)."""
import datetime, re, socket, ssl, smtplib
from urllib.parse import quote
from email.message import EmailMessage
from config import Config

# Sanitized categories written to Render. Never a password, token, or key.
# These slugs are the only failure labels the operator inspects after deploy.
SMTP_AUTHENTICATION = "smtp_authentication"
SMTP_TLS = "smtp_tls"
SMTP_CONNECTION = "smtp_connection"
SMTP_SENDER = "smtp_sender"
SMTP_RECIPIENT = "smtp_recipient"
SMTP_PROVIDER = "smtp_provider"
CONFIGURATION = "configuration"
# Aliases kept so older tests/callers still import a stable name.
MAIL_MODE_NOT_SMTP = CONFIGURATION
SMTP_AUTH_FAILURE = SMTP_AUTHENTICATION
GMAIL_APP_PASSWORD_REJECTED = SMTP_AUTHENTICATION
SMTP_TLS_FAILURE = SMTP_TLS
SENDER_MISMATCH = SMTP_SENDER
RECIPIENT_MISMATCH = SMTP_RECIPIENT
RESET_TOKEN_DB_FAILURE = CONFIGURATION

_SECRETISH = re.compile(
    r"(?i)(password|passwd|smtp_pass|app password|secret|token|api[_-]?key|"
    r"service.role|bearer|authorization)\s*[:=]\s*\S+")


def _smtp_user():
    return (Config.SMTP_USER or "").strip()


def _smtp_pass():
    """App passwords are 16 chars; dashboard paste often includes spaces or quotes."""
    raw = (Config.SMTP_PASS or "").strip().strip('"').strip("'")
    host = (Config.SMTP_HOST or "").lower()
    if "gmail.com" in host or "google.com" in host:
        raw = "".join(raw.split())
    return raw


def _email_addr(value):
    s = (value or "").strip()
    if "<" in s and ">" in s:
        s = s[s.find("<") + 1:s.rfind(">")].strip()
    return s


def mail_config_fields():
    """Safe snapshot for logs: host/port/mode and whether secrets exist."""
    return {
        "mail_mode": (Config.MAIL_MODE or "none").strip().lower() or "none",
        "smtp_host": Config.SMTP_HOST or "",
        "smtp_port": int(Config.SMTP_PORT or 0),
        "smtp_user_configured": bool(_smtp_user()),
        "smtp_password_configured": bool(_smtp_pass()),
    }


def sanitize_mail_text(text):
    s = str(text or "")
    secrets = [Config.SMTP_PASS, _smtp_pass(), Config.RESEND_API_KEY,
               Config.SUPABASE_SERVICE_ROLE_KEY]
    for secret in secrets:
        if secret:
            s = s.replace(str(secret), "<redacted>")
            compact = "".join(str(secret).split())
            if compact and compact != str(secret):
                s = s.replace(compact, "<redacted>")
    s = _SECRETISH.sub(r"\1=<redacted>", s)
    s = re.sub(r"(?i)\b(code|token)\s*[:=]\s*\S+", r"\1=<redacted>", s)
    return s[:240]


def log_mail_event(category, extra=""):
    """Stdout line for Render. extra is accepted but never printed."""
    f = mail_config_fields()
    print(
        "[mail] mode={mode} host={host} port={port} "
        "user_configured={user} password_configured={pw} category={cat}".format(
            mode=f["mail_mode"], host=f["smtp_host"] or "-",
            port=f["smtp_port"], user="yes" if f["smtp_user_configured"] else "no",
            pw="yes" if f["smtp_password_configured"] else "no",
            cat=category),
        flush=True)


def classify_smtp_failure(exc=None, info=""):
    """Map an SMTP exception / info string onto one sanitized category slug."""
    mode = (Config.MAIL_MODE or "").strip().lower()
    if mode and mode not in ("smtp", "resend"):
        return CONFIGURATION
    blob = " ".join(
        str(x) for x in (
            info, exc, type(exc).__name__ if exc is not None else "",
            getattr(exc, "smtp_code", ""), getattr(exc, "smtp_error", ""),
        )).lower()
    if "resend" in blob:
        return SMTP_PROVIDER
    if any(k in blob for k in (
            "application-specific password", "app password", "badcredentials",
            "5.7.8", "5.7.9", "username and password not accepted",
            "please log in through your web browser",
            "please log in via your web browser")):
        return SMTP_AUTHENTICATION
    if isinstance(exc, smtplib.SMTPAuthenticationError) or "smtp authentication" in blob:
        return SMTP_AUTHENTICATION
    if "auth" in blob and any(k in blob for k in ("required", "failed", "invalid", "not configured")):
        return SMTP_AUTHENTICATION
    if isinstance(exc, smtplib.SMTPSenderRefused) or any(k in blob for k in (
            "sender refused", "not allowed to send", "from address",
            "syntactically incorrect", "5.7.1")):
        return SMTP_SENDER
    if isinstance(exc, smtplib.SMTPRecipientsRefused) or any(k in blob for k in (
            "recipient refused", "user unknown", "mailbox unavailable", "5.1.1")):
        return SMTP_RECIPIENT
    if isinstance(exc, ssl.SSLError) or any(k in blob for k in (
            "starttls", "ssl error", "tls handshake", "certificate verify",
            "wrong version number")):
        return SMTP_TLS
    if isinstance(exc, (socket.timeout, socket.gaierror, ConnectionRefusedError,
                        ConnectionError, TimeoutError, smtplib.SMTPConnectError,
                        smtplib.SMTPServerDisconnected)) or any(k in blob for k in (
            "timed out", "timeout", "connection refused", "name or service not known",
            "network is unreachable", "eof occurred", "disconnected",
            "smtp not configured", "getaddrinfo", "gaierror")):
        return SMTP_CONNECTION
    if isinstance(exc, OSError):
        return SMTP_CONNECTION
    if "smtp error" in blob or isinstance(exc, smtplib.SMTPException):
        return SMTP_CONNECTION
    return SMTP_CONNECTION


def _ipv4_connect(address, timeout):
    """IPv4 first: broken IPv6 on some hosts black-holes smtp.gmail.com until 502."""
    host, port = address
    last = None
    infos = socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_STREAM)
    for family, socktype, proto, _canon, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last = exc
            sock.close()
    if last:
        raise last
    raise socket.gaierror("no IPv4 address for %s" % host)


class _SMTP4(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        return _ipv4_connect((host, port), timeout)


class _SMTP4_SSL(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        raw = _ipv4_connect((host, port), timeout)
        context = self.context or ssl.create_default_context()
        return context.wrap_socket(raw, server_hostname=host)


def _open_smtp(host, port, use_ssl, timeout=12):
    """Port 465 = SMTP_SSL. Port 587 = STARTTLS before the caller may AUTH."""
    ctx = ssl.create_default_context()
    port = int(port)
    if use_ssl or port == 465:
        s = _SMTP4_SSL(host, port, timeout=timeout, context=ctx)
        s.ehlo()
        s._jaura_tls = True
        return s
    s = _SMTP4(timeout=timeout)
    s.connect(host, port)
    s.ehlo()
    gmail = "gmail.com" in (host or "").lower() or "google.com" in (host or "").lower()
    need_tls = port == 587 or gmail
    try:
        s.starttls(context=ctx)
        s.ehlo()
        s._jaura_tls = True
    except smtplib.SMTPException:
        s._jaura_tls = False
        if need_tls:
            try:
                s.close()
            except Exception:
                pass
            raise
    return s


def _gmail_from_header():
    """Gmail rejects or rewrites a From that is not the authenticated account."""
    user = _email_addr(_smtp_user()) or _email_addr(Config.MAIL_FROM)
    header = (Config.MAIL_FROM or "").strip() or user
    if user and _email_addr(header).lower() != user.lower():
        return user
    return header or user


def _deliver_smtp(msg, recipients):
    host = (Config.SMTP_HOST or "").strip()
    if not host:
        return False, "SMTP not configured"
    port = int(Config.SMTP_PORT or 587)
    user, password = _smtp_user(), _smtp_pass()
    gmail = "gmail.com" in host.lower() or "google.com" in host.lower()
    if gmail and not password:
        return False, "SMTP authentication required"
    attempts = [(port, port == 465)]
    if gmail and port == 587:
        attempts.append((465, True))
    elif gmail and port == 465:
        attempts.append((587, False))
    last = None
    for p, use_ssl in attempts:
        s = None
        try:
            s = _open_smtp(host, p, use_ssl)
            if user and password:
                if int(p) == 587 and not getattr(s, "_jaura_tls", False):
                    raise smtplib.SMTPException("STARTTLS required before authentication")
                s.login(user, password)
            envelope = _email_addr(user or Config.MAIL_FROM)
            s.send_message(msg, from_addr=envelope or None, to_addrs=list(recipients))
            return True, "smtp sent"
        except Exception as exc:
            last = exc
            continue
        finally:
            if s is not None:
                try:
                    s.quit()
                except Exception:
                    try:
                        s.close()
                    except Exception:
                        pass
    return False, f"smtp error: {last}"

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
    mode = (Config.MAIL_MODE or "none").strip().lower()
    if mode == "resend":
        ok, info = _via_resend(to, subject, body)
        if not ok:
            log_mail_event(classify_smtp_failure(info=info), info)
        return ok, info
    if mode == "smtp":
        ok, info = _via_smtp_attached(to, subject, body, None, "", "", "", None)
        if not ok:
            log_mail_event(classify_smtp_failure(info=info), info)
        return ok, info
    if mode not in ("none", "", "stub", "log"):
        log_mail_event(MAIL_MODE_NOT_SMTP)
        return False, MAIL_MODE_NOT_SMTP
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
    if Config.MAIL_MODE == "resend":
        return _via_resend_attached(to, subject, body, None, "", "application/octet-stream", reply_to)
    if Config.MAIL_MODE != "smtp":
        return send(to, subject, body)
    return _via_smtp_attached(to, subject, body, None, "", "", reply_to, None)


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


WHATSAPP_NUMBER = "22968953110"


def _whatsapp_text(order):
    """The one message a customer sends to ask for their transport fare.

    Fixed wording: the shop asked for these exact sentences, and the order
    ID plus the delivery location are filled in so the answer can be looked
    up without a single extra question.
    """
    c = order.get("customer") or {}
    where = ", ".join([str(x).strip() for x in (
        c.get("city") or "", c.get("zone") or "", c.get("address") or "") if str(x).strip()])
    return ("Hello Jaura Store, I have paid for order "
            f"{order.get('id') or ''}. I would like to know my specific "
            f"transport fare. My delivery location: {where}. I understand that "
            "transportation fare ranges depending on location and the weight "
            "of the products. Thank you.")


def send_receipt(order):
    """Customer receipt, sent when an admin confirms the payment.

    Carries everything the customer needs to recognise the order and to ask
    for their transport fare without another round of emails: order id, date,
    status, amount and method, the full delivery details, the item list with
    quantities, colours and prices, and the fixed WhatsApp message.
    """
    c = order.get("customer") or {}
    to = (c.get("email") or "").strip()
    if not to:
        return False, "no customer email"
    items = order.get("items") or []
    currency = order.get("currency") or ""
    oid = order.get("id") or ""

    def money(value):
        try:
            return f"{float(value):,.0f}"
        except (TypeError, ValueError):
            return str(value or "").strip()

    total = order.get("total")
    lines = [
        "Thank you for patronising Jaura Store.",
        "",
        "ORDER DETAILS",
        f"Order ID: {oid}",
        f"Date: {order.get('at') or ''}",
        "Status: Confirmed",
        f"Total paid: {money(total)} {currency}".rstrip(),
        f"Payment method: {order.get('payment') or ''}",
        "",
        "CUSTOMER DETAILS",
        f"Name: {c.get('name') or ''}",
        f"Phone: {c.get('phone') or ''}",
        f"Email: {c.get('email') or ''}",
        f"Country: {c.get('country') or ''}",
        f"City / Zone: {c.get('city') or ''}{(' / ' + c['zone']) if c.get('zone') else ''}",
        f"Address: {c.get('address') or ''}",
        f"Note: {c.get('note') or ''}",
        "",
        "ITEMS",
    ]
    for i in items or []:
        row = f"  - {i.get('qty')}x {i.get('name')}"
        if i.get("color"):
            row += f" ({i.get('color')})"
        price = i.get("price") or i.get("priceCfa") or i.get("priceNgn") or ""
        if price not in ("", None):
            row += f" — {money(price)} {currency}".rstrip()
        lines.append(row)
    lines += [
        "",
        f"Total: {money(total)} {currency}".rstrip(),
        "",
        "DELIVERY & TRANSPORT FARE",
        "Transportation fare ranges depending on your location and the weight "
        "of the products. Send us the message below on WhatsApp with your "
        "order ID and we will tell you your specific fare before we dispatch.",
        "",
        _whatsapp_text(order),
        "",
        f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(_whatsapp_text(order))}",
        f"Order ID: {oid}",
    ]
    ref_code = order.get("referralCode") or ""
    if not ref_code and to:
        try:
            from db import one
            row = one("SELECT code FROM referral_codes WHERE email=?", (to.lower(),))
            if row:
                ref_code = row["code"]
        except Exception:
            pass
    if ref_code:
        lines += [
            "",
            f"Your referral code: {ref_code}",
            "Share it with friends — they get 5% off, and you earn a 10% discount after 2 friends order!",
        ]
    lines += ["", "With thanks,", "Jaura Store", Config.MAIL_FROM]
    return send(to, f"Jaura Store · payment confirmed · {oid}", "\n".join(lines))


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
    msg["From"] = _gmail_from_header()
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    if data:
        _attach(msg, data, filename, mime)
    recipients = [to] + ([cc] if cc else [])
    ok, info = _deliver_smtp(msg, recipients)
    if ok:
        return True, f"smtp sent ({len(data or b'')} bytes attached)"
    return False, info


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
