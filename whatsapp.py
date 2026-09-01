"""WhatsApp order notifications to the shop owner.

Sends automatically when either provider is configured (environment):

  Meta WhatsApp Cloud API (recommended, free tier):
      WHATSAPP_TOKEN      - permanent access token
      WHATSAPP_PHONE_ID   - the sender phone-number id
  CallMeBot (zero-setup fallback, one-time activation on the owner phone):
      WHATSAPP_CALLMEBOT_KEY

  WHATSAPP_NOTIFY_NUMBER - recipient, digits only (default 2290168953101,
                           i.e. +229 01 68 95 31 01).

Never raises and never blocks a sale: any failure is reported back as
(False, reason) and written to the audit log by the caller.
"""
import json
import urllib.parse
import urllib.request
from config import Config


def _order_text(order):
    c = order.get("customer") or {}
    items = order.get("items") or []
    lines = [
        f"🛍 NEW ORDER {order.get('id')}",
        f"Name: {c.get('name') or ''}",
        f"Phone: {c.get('phone') or ''}",
        f"Email: {c.get('email') or ''}",
        f"Deliver to: {', '.join(x for x in (c.get('address'), c.get('city'), c.get('zone'), c.get('country')) if x)}",
        "Items:",
    ]
    lines += [f"  - {i.get('qty')}x {i.get('name')}"
              + (f" ({i.get('color')})" if i.get("color") else "")
              for i in items]
    lines.append(f"Total: {order.get('total')} {order.get('currency')}")
    if c.get("note"):
        lines.append(f"Note: {c.get('note')}")
    lines.append(f"Admin: {Config.SITE_ORIGIN}/admin.html")
    return "\n".join(lines)


def _via_cloud_api(text):
    url = f"https://graph.facebook.com/v19.0/{Config.WHATSAPP_PHONE_ID}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": Config.WHATSAPP_NOTIFY_NUMBER,
        "type": "text",
        "text": {"body": text[:4000]},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Authorization": "Bearer " + Config.WHATSAPP_TOKEN,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return 200 <= r.status < 300


def _via_callmebot(text):
    qs = urllib.parse.urlencode({
        "phone": "+" + Config.WHATSAPP_NOTIFY_NUMBER,
        "text": text[:1800],
        "apikey": Config.WHATSAPP_CALLMEBOT_KEY,
    })
    req = urllib.request.Request("https://api.callmebot.com/whatsapp.php?" + qs,
                                 headers={"User-Agent": "jaurastore"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return 200 <= r.status < 300


def send_order_notification(order):
    """Returns (sent, detail). Configured provider is tried; nothing raises."""
    text = _order_text(order)
    try:
        if Config.WHATSAPP_TOKEN and Config.WHATSAPP_PHONE_ID:
            return _via_cloud_api(text), "cloud-api"
        if Config.WHATSAPP_CALLMEBOT_KEY:
            return _via_callmebot(text), "callmebot"
        return False, "not configured (set WHATSAPP_TOKEN + WHATSAPP_PHONE_ID or WHATSAPP_CALLMEBOT_KEY)"
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {exc}"
