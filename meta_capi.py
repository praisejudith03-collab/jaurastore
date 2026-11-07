"""Meta Conversions API (CAPI) — server-side standard events for the shop.

Why this module exists
----------------------
The storefront pixel only reports what a browser manages to send. On a phone
in Ibadan that means an ad blocker, an Instagram/Facebook in-app browser, a
dead battery or a dropped 3G connection silently deletes the event — and with
it the attribution for a December campaign. A server-side event is written by
the machine that actually took the order, so it survives all of that.

What is sent
------------
Standard Meta events, with commerce data taken from the server's own numbers
(never from the browser):

  * ``ViewContent``       — a product page was opened
  * ``AddToCart``         — a line was added (quantity + price)
  * ``InitiateCheckout``  — the checkout page was opened
  * ``Purchase``          — an order row was durably written

Personal data (email, phone, name, city, country) is SHA-256 hashed, lowercased
and trimmed exactly as Meta's customer-information spec requires; raw PII never
leaves this process. Browsing events add the click identifiers Meta itself set
(``_fbp`` / ``_fbc``) and the request's IP + user agent, which is what lets Meta
match a guest who has never typed an email address.

Deduplication (the important part)
----------------------------------
Every event carries an ``event_id``. The browser pixel is fired with the SAME
id as the fourth ``fbq`` argument (``{eventID: …}``), so Meta collapses the two
copies into one conversion. For purchase the id is derived from the order id
(``ja-purchase-JA-1A2B3C``) — both sides can compute it without talking to each
other, which is what keeps deduplication working even when the checkout
response never reached the phone. ``meta_events`` has a UNIQUE(event_name,
event_id) index as well: a retried analytics batch, a double-submitted form or
a resumed worker cannot insert (and therefore cannot send) the same event twice.

Delivery
--------
The request path does one INSERT and one queue wake-up; the HTTP POST to
graph.facebook.com happens on the task-queue worker, in batches of up to
``MAX_BATCH``, with a bounded timeout. Failed events stay in the table and are
retried by the scheduler sweep with attempts capped, so an outage at Meta never
turns into a burst of retries on the next page view.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import secrets
import time
import urllib.error

from config import Config
from db import execute, one, query

GRAPH_HOST = "https://graph.facebook.com"
HTTP_TIMEOUT = 8.0
MAX_BATCH = 40
MAX_ATTEMPTS = 5

# The client may report these; Purchase is deliberately NOT accepted from a
# browser — a purchase is only ever counted from the order row the server
# itself wrote (see api.create_order), so a scripted POST cannot inflate
# December's conversion count.
CLIENT_EVENTS = ("ViewContent", "AddToCart", "InitiateCheckout", "PageView")
PURCHASE = "Purchase"

# Meta wants an ISO-4217 code. The shop quotes the West-African franc as
# "CFA"; the code Meta accepts is XOF. Sending "CFA" is silently dropped from
# the ad report's value column, which is exactly the kind of quiet gap this
# module exists to remove.
CURRENCY_ALIASES = {"CFA": "XOF", "FCFA": "XOF", "XOF": "XOF", "NGN": "NGN",
                    "NAIRA": "NGN", "₦": "NGN"}

_TABLE_READY = False


# ------------------------------------------------------------------ utilities
def _utcnow_iso():
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


def currency_code(raw, default="NGN"):
    """The ISO-4217 code Meta expects, from whatever the shop calls it."""
    text = str(raw or "").strip().upper()
    return CURRENCY_ALIASES.get(text, default)


def normalize_number(value):
    """A positive money amount with two decimals, or 0."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number < 0 or number != number:            # negative / NaN
        return 0.0
    return round(number, 2)


def normalize_phone(raw):
    """Digits only, with the West-African trunk zero resolved to a country code.

    Meta matches on the full international number. A Nigerian number typed as
    ``0803…`` must become ``234803…`` or it matches nothing at all; Benin and
    Togo numbers are already stored with their +229/+228 prefix by checkout.
    """
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) in (10, 11):
        digits = "234" + digits[1:]          # Nigeria's trunk prefix
    return digits[:25]


def hash_value(raw):
    """SHA-256 of a normalised matching value (Meta's required format)."""
    text = re.sub(r"\s+", " ", str(raw or "").strip().lower())
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_phone(raw):
    digits = normalize_phone(raw)
    if not digits:
        return ""
    return hashlib.sha256(digits.encode("utf-8")).hexdigest()


def split_name(raw):
    """(first, last) from the one name field the checkout collects."""
    parts = [p for p in re.split(r"\s+", str(raw or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def event_id(prefix="evt"):
    """A fresh, unique dedupe id for a browser-originated event."""
    return f"ja-{prefix}-{int(time.time() * 1000):x}-{secrets.token_hex(5)}"


def purchase_event_id(order_id):
    """The deterministic Purchase dedupe id, shared with the pixel.

    Both the server (when the order row lands) and the browser (when the
    confirmation screen paints) derive the SAME string from the order id, so
    Meta counts one purchase even if the checkout response was lost on a slow
    connection and the confirmation came from a reload.
    """
    oid = str(order_id or "").strip().upper()
    if not oid:
        return ""
    return "ja-purchase-" + oid


# ------------------------------------------------------------------ config
def configured():
    checker = getattr(Config, "meta_configured", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return bool(getattr(Config, "META_CAPI_ENABLED", True)
                and getattr(Config, "META_PIXEL_ID", "")
                and getattr(Config, "META_CAPI_ACCESS_TOKEN", ""))


def pixel_id():
    return str(getattr(Config, "META_PIXEL_ID", "") or "").strip()


def api_version():
    return str(getattr(Config, "META_CAPI_API_VERSION", "v21.0") or "v21.0").strip()


def graph_url():
    return f"{GRAPH_HOST}/{api_version()}/{pixel_id()}/events"


def _ensure_table():
    """Create the outbox if this database predates it (no-op afterwards)."""
    global _TABLE_READY
    if _TABLE_READY:
        return True
    try:
        execute("CREATE TABLE IF NOT EXISTS meta_events ("
                "  id TEXT PRIMARY KEY, event_name TEXT NOT NULL,"
                "  event_id TEXT NOT NULL, event_time INTEGER NOT NULL,"
                "  payload TEXT NOT NULL,"
                "  status TEXT NOT NULL DEFAULT 'queued',"
                "  attempts INTEGER NOT NULL DEFAULT 0, detail TEXT,"
                "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
                "  sent_at TEXT)")
        execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_meta_events_dedupe "
                "ON meta_events(event_name, event_id)")
        execute("CREATE INDEX IF NOT EXISTS idx_meta_events_status "
                "ON meta_events(status, attempts)")
        _TABLE_READY = True
    except Exception as exc:                        # pragma: no cover
        print(f"[meta] outbox table unavailable: {exc}")
        return False
    return True


# ------------------------------------------------------------- user data
def _request_context():
    """(ip, user agent, _fbp, _fbc, referrer) for the CURRENT request, or nils."""
    try:
        from flask import has_request_context, request
    except Exception:                               # pragma: no cover
        return "", "", "", "", ""
    if not has_request_context():
        return "", "", "", "", ""
    try:
        import security as sec
        ip = sec.client_ip()
    except Exception:
        ip = ""
    try:
        ua = request.headers.get("User-Agent", "")[:400]
        cookies = request.cookies
        fbp = (cookies.get("_fbp") or "")[:200]
        fbc = (cookies.get("_fbc") or "")[:400]
        referrer = (request.headers.get("Referer") or "")[:800]
    except Exception:                               # pragma: no cover
        ua = fbp = fbc = referrer = ""
    if not fbc:
        # A first click may arrive with ?fbclid= before the pixel has written
        # the _fbc cookie. Meta's own format is fb.1.<ts>.<fbclid>.
        try:
            fbclid = request.args.get("fbclid") or ""
        except Exception:
            fbclid = ""
        if fbclid:
            fbc = f"fb.1.{int(time.time() * 1000)}.{str(fbclid)[:300]}"
    return ip, ua, fbp, fbc, referrer


def current_customer():
    """The signed-in shopper's row, or {} — used for browsing-event matching."""
    try:
        import customers
        return customers.current_customer() or {}
    except Exception:
        return {}


def build_user_data(customer=None, request_meta=None, external_id=""):
    """Hashed matching fields for one event. Raw PII never leaves this function.

    ``customer`` may be the checkout's customer dict or a ``customers`` row;
    both are read by key so a caller cannot accidentally ship a password hash
    (only the listed fields are ever copied).
    """
    customer = customer or {}
    meta = dict(request_meta or {})
    email = customer.get("email") or meta.get("email") or ""
    phone = customer.get("phone") or meta.get("phone") or ""
    name = customer.get("name") or ""
    if not name:
        name = " ".join(p for p in (customer.get("firstName"),
                                    customer.get("lastName")) if p)
    first, last = split_name(name or meta.get("name") or "")
    ip, ua, fbp, fbc, _referrer = _request_context()
    # A caller-supplied request context (a background job, or a test) wins.
    ip = meta.get("clientIp") or ip
    ua = meta.get("userAgent") or ua
    fbp = meta.get("fbp") or fbp
    fbc = meta.get("fbc") or fbc

    user = {
        "em": hash_value(email),
        "ph": hash_phone(phone),
        "fn": hash_value(first),
        "ln": hash_value(last),
        "ct": hash_value(customer.get("city") or meta.get("city") or ""),
        "st": hash_value(customer.get("zone") or meta.get("state") or ""),
        "zp": hash_value(customer.get("zip") or meta.get("zip") or ""),
        "country": hash_value(iso_country(customer.get("country")
                                          or meta.get("country") or "")),
        "external_id": hash_value(external_id or meta.get("externalId") or ""),
        "client_ip_address": str(ip or "")[:64],
        "client_user_agent": str(ua or "")[:400],
        "fbp": str(fbp or "")[:200],
        "fbc": str(fbc or "")[:400],
    }
    return {k: v for k, v in user.items() if v}


_COUNTRY_NAMES = {
    "nigeria": "ng", "ng": "ng", "benin": "bj", "bj": "bj",
    "togo": "tg", "tg": "tg", "ghana": "gh", "gh": "gh",
    "ivory coast": "ci", "côte d'ivoire": "ci", "cote d'ivoire": "ci",
    "cameroon": "cm", "niger": "ne", "senegal": "sn", "sénégal": "sn",
    "united kingdom": "gb", "uk": "gb", "gb": "gb",
    "united states": "us", "usa": "us", "us": "us",
    "france": "fr", "fr": "fr", "canada": "ca", "ca": "ca",
}


def iso_country(raw):
    """Two-letter country code from whatever the checkout stored."""
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    if len(text) == 2 and text.isalpha():
        return text
    return _COUNTRY_NAMES.get(text, "")


def contents_from_items(items):
    """Meta ``contents`` + ``content_ids`` from validated order/cart lines."""
    contents, ids = [], []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()[:100]
        if not pid:
            continue
        try:
            qty = int(item.get("qty") or item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        qty = max(1, min(1000000, qty))
        try:
            price = normalize_number(item.get("price"))
        except Exception:
            price = 0.0
        contents.append({"id": pid, "quantity": qty, "item_price": price})
        ids.append(pid)
    return contents, ids


# ------------------------------------------------------------------- queue
def _event_payload(event_name, custom_data, user_data, source_url,
                   action_source="website", at=None):
    event_time = int(at or time.time())
    return {
        "event_name": event_name,
        "event_time": event_time,
        "action_source": action_source,
        "event_source_url": str(source_url or "")[:800],
        "user_data": user_data or {},
        "custom_data": custom_data or {},
    }


def queue(event_name, event_payload, dedupe_id):
    """Write one event to the outbox and wake the sender. Never raises.

    Returns ``{"queued": bool, "duplicate": bool, "eventId": str}``. A duplicate
    (same event name + same dedupe id already stored) is not sent again — that
    is the server half of pixel/CAPI deduplication.
    """
    result = {"queued": False, "duplicate": False, "eventId": str(dedupe_id or ""),
              "reason": ""}
    if not configured():
        result["reason"] = "not configured"
        return result
    if not event_name or not dedupe_id or not isinstance(event_payload, dict):
        result["reason"] = "invalid event"
        return result
    if not _ensure_table():
        result["reason"] = "outbox unavailable"
        return result
    row_id = hashlib.sha256(f"{event_name}|{dedupe_id}".encode("utf-8")).hexdigest()[:40]
    try:
        existing = one("SELECT id, status FROM meta_events WHERE event_name=? AND event_id=?",
                       (event_name, str(dedupe_id)))
        if existing:
            result["duplicate"] = True
            result["reason"] = "already recorded"
            return result
        execute("INSERT INTO meta_events (id, event_name, event_id, event_time, "
                "payload, status, attempts, created_at) VALUES (?,?,?,?,?,?,0,?)",
                (row_id, event_name, str(dedupe_id),
                 int(event_payload.get("event_time") or time.time()),
                 json.dumps(event_payload, ensure_ascii=False), "queued",
                 _utcnow_iso()))
    except Exception as exc:
        message = str(exc)
        if "UNIQUE" in message.upper():
            # A concurrent request (or a retried batch) already queued it.
            result["duplicate"] = True
            result["reason"] = "already recorded"
            return result
        print(f"[meta] event write skipped: {message[:200]}")
        result["reason"] = message[:200]
        return result
    result["queued"] = True
    try:
        import task_queue
        task_queue.enqueue("meta.capi.send", {"event": row_id}, dedupe=False)
    except Exception as exc:                        # pragma: no cover - queue down
        # The row is durable; the scheduler sweep will send it.
        print(f"[meta] queue wake skipped: {exc}")
    return result


def track(name, custom_data=None, user_data=None, source_url="",
          dedupe_id=None, action_source="website"):
    """Queue one already-built event."""
    payload = _event_payload(name, custom_data or {}, user_data or {},
                             source_url, action_source=action_source)
    return queue(name, payload, dedupe_id or event_id("srv"))


# ---------------------------------------------------------- event builders
def view_content(product_id, name="", value=0, currency="NGN", source_url="",
                 dedupe_id=None, user_data=None):
    contents = [{"id": str(product_id), "quantity": 1,
                 "item_price": normalize_number(value)}]
    custom = {"content_type": "product", "content_ids": [str(product_id)],
              "contents": contents, "value": normalize_number(value),
              "currency": currency_code(currency)}
    if name:
        custom["content_name"] = str(name)[:200]
    return track("ViewContent", custom, user_data, source_url, dedupe_id)


def add_to_cart(items, currency="NGN", source_url="", dedupe_id=None,
                user_data=None, value=None):
    contents, ids = contents_from_items(items)
    total = normalize_number(value)
    if not total:
        total = normalize_number(sum((c["item_price"] * c["quantity"])
                                     for c in contents))
    num_items = sum(c["quantity"] for c in contents) or 0
    custom = {"content_type": "product", "content_ids": ids, "contents": contents,
              "value": total, "currency": currency_code(currency),
              "num_items": num_items}
    return track("AddToCart", custom, user_data, source_url, dedupe_id)


def initiate_checkout(items, currency="NGN", source_url="", dedupe_id=None,
                      user_data=None, value=None):
    contents, ids = contents_from_items(items)
    total = normalize_number(value)
    if not total:
        total = normalize_number(sum((c["item_price"] * c["quantity"])
                                     for c in contents))
    custom = {"content_type": "product", "content_ids": ids, "contents": contents,
              "value": total, "currency": currency_code(currency),
              "num_items": sum(c["quantity"] for c in contents) or 0}
    return track("InitiateCheckout", custom, user_data, source_url, dedupe_id)


def purchase(order, user_data=None, source_url=""):
    """The authoritative conversion: server-computed total, order id as dedupe.

    ``order`` is the payload this app just wrote to its own orders table — the
    items, currency and total in the event are the SAME numbers the shop will
    bill, not what the browser claimed.
    """
    order = order or {}
    oid = str(order.get("id") or "").strip().upper()
    if not oid:
        return {"queued": False, "reason": "no order id"}
    currency = currency_code(order.get("currency"))
    contents, ids = contents_from_items(order.get("items"))
    total = normalize_number(order.get("total"))
    if not total and contents:
        total = normalize_number(sum((c["item_price"] * c["quantity"])
                                     for c in contents))
    num_items = sum(c["quantity"] for c in contents) or 0
    promo = order.get("promo") if isinstance(order.get("promo"), dict) else {}
    custom = {
        "content_type": "product",
        "content_ids": ids,
        "contents": contents,
        "value": total,
        "currency": currency,
        "num_items": num_items,
        "order_id": oid,
        "payment_method": str(order.get("paymentMethod")
                              or order.get("payment") or "")[:60],
    }
    if promo.get("code"):
        custom["coupon"] = str(promo["code"])[:60]
    user = user_data if user_data is not None else build_user_data(
        order.get("customer") or {})
    return track(PURCHASE, custom, user, source_url or "",
                 purchase_event_id(oid))


# --------------------------------------------------------------- delivery
def _post(batch):
    """POST one batch to Meta. Returns (ok, detail, sent_count)."""
    body = {"data": batch}
    token = str(getattr(Config, "META_CAPI_ACCESS_TOKEN", "") or "").strip()
    if not token:
        return False, "no access token", 0
    body["access_token"] = token
    test_code = str(getattr(Config, "META_CAPI_TEST_EVENT_CODE", "") or "").strip()
    if test_code:
        body["test_event_code"] = test_code
    try:
        import security
        raw = security.safe_fetch(
            graph_url(), data=json.dumps(body).encode("utf-8"), method="POST",
            timeout=HTTP_TIMEOUT, max_bytes=8192,
            headers={"Content-Type": "application/json",
                     "User-Agent": "jaurastore-capi/1.0"})
        text = raw.decode("utf-8", "replace")[:600]
    except urllib.error.HTTPError as exc:
        try:
            detail = (exc.read() or b"").decode("utf-8", "replace")[:400]
        except Exception:
            detail = ""
        return False, f"HTTP {exc.code}: {detail}".strip(), 0
    except Exception as exc:
        return False, str(exc)[:400], 0
    try:
        parsed = json.loads(text)
    except ValueError:
        return False, f"unparseable response: {text[:200]}", 0
    received = int((parsed or {}).get("events_received") or 0)
    if received:
        return True, text[:300], received
    # Meta answers 200 with an `error` object for a bad token / bad field.
    return False, text[:300], 0


def flush(limit=MAX_BATCH, retry_failed=False, logger=None):
    """Send due events in one batch. Returns a small counters dict.

    Safe to call from anywhere: a Meta outage is recorded per row and never
    raised, so the task-queue worker and the scheduler sweep both stay alive.
    """
    stats = {"sent": 0, "failed": 0, "skipped": 0}
    if not configured() or not _ensure_table():
        return stats
    try:
        if retry_failed:
            rows = query("SELECT * FROM meta_events WHERE status IN ('queued','failed') "
                         "AND attempts < ? ORDER BY created_at LIMIT ?",
                         (MAX_ATTEMPTS, int(limit)))
        else:
            rows = query("SELECT * FROM meta_events WHERE status='queued' "
                         "ORDER BY created_at LIMIT ?", (int(limit),))
    except Exception as exc:
        if logger:
            logger.info("meta flush read failed: %s", exc)
        return stats
    if not rows:
        return stats
    batch, row_ids = [], []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except ValueError:
            execute("UPDATE meta_events SET status='skipped', detail=? WHERE id=?",
                    ("corrupt payload", row["id"]))
            stats["skipped"] += 1
            continue
        if not isinstance(payload, dict) or not payload.get("event_name"):
            execute("UPDATE meta_events SET status='skipped', detail=? WHERE id=?",
                    ("empty payload", row["id"]))
            stats["skipped"] += 1
            continue
        entry = dict(payload)
        entry["event_id"] = row["event_id"]
        batch.append(entry)
        row_ids.append(row["id"])
    if not batch:
        return stats
    ok, detail, received = _post(batch)
    stamp = _utcnow_iso()
    if ok:
        for row_id in row_ids:
            execute("UPDATE meta_events SET status='sent', attempts=attempts+1, "
                    "detail=?, sent_at=? WHERE id=?", (detail[:300], stamp, row_id))
        stats["sent"] = received or len(batch)
        return stats
    for row_id in row_ids:
        execute("UPDATE meta_events SET status='failed', attempts=attempts+1, "
                "detail=? WHERE id=?", (str(detail)[:300], row_id))
    stats["failed"] = len(row_ids)
    if logger:
        logger.info("meta flush failed: %s", str(detail)[:200])
    return stats


def task_send(payload):
    """Task-queue handler: send the events that are due right now."""
    stats = flush()
    if stats.get("failed"):
        raise RuntimeError("Meta CAPI rejected the batch: "
                           + str(stats.get("failed")) + " event(s)")
    return stats


def sweep(limit=MAX_BATCH, logger=None):
    """Scheduler step: retry whatever is still outstanding, bounded."""
    if not configured():
        return {"skipped": "not configured"}
    return flush(limit=limit, retry_failed=True, logger=logger)


def register():
    """Bind the sender to the task queue (idempotent)."""
    try:
        import task_queue
        task_queue.register("meta.capi.send", task_send, label="Send Meta events")
        return True
    except Exception as exc:                        # pragma: no cover
        print(f"[meta] task registration skipped: {exc}")
        return False


def status(limit=20):
    """Owner-facing diagnostics: what is configured and what happened last."""
    out = {
        "configured": configured(),
        "pixelId": pixel_id(),
        "apiVersion": api_version(),
        "testEventCode": bool(str(getattr(Config, "META_CAPI_TEST_EVENT_CODE", "") or "")),
        "enabled": bool(getattr(Config, "META_CAPI_ENABLED", True)),
        "hasToken": bool(str(getattr(Config, "META_CAPI_ACCESS_TOKEN", "") or "")),
        "counts": {},
        "recent": [],
    }
    if not _ensure_table():
        return out
    try:
        for row in query("SELECT status, COUNT(*) n FROM meta_events GROUP BY status"):
            out["counts"][row["status"]] = int(row["n"] or 0)
        out["recent"] = [
            {"eventName": r["event_name"], "eventId": r["event_id"],
             "status": r["status"], "attempts": int(r["attempts"] or 0),
             "createdAt": r["created_at"], "sentAt": r["sent_at"] or "",
             "detail": (r["detail"] or "")[:160]}
            for r in query("SELECT event_name, event_id, status, attempts, "
                           "created_at, sent_at, detail FROM meta_events "
                           "ORDER BY created_at DESC LIMIT ?", (int(limit),))
        ]
    except Exception as exc:
        out["error"] = str(exc)[:200]
    return out


register()
