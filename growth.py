"""Growth tools: referral codes, discount coupons and abandoned-cart
email reminders.

Rules (owner-configurable from the admin panel, defaults per spec):
  * a customer whose order reaches `minSpendNgn` (default 20,000 NGN)
    automatically gets a unique referral code;
  * a shopper who types a valid referral code at checkout gets
    `buyerPercent` off (default 5%);
  * after EXACTLY `milestone` successful purchases with their code
    (default 2) the referrer is automatically issued a single-use
    `referrerPercent` coupon (default 10%, hard-capped at 10%);
  * checkouts that stall for `abandonedHours` (default 2) get one email
    reminder with a link that restores the saved cart.
"""
import datetime, json, secrets, string
from db import execute, one, query, audit
from config import Config

NGN_TO_CFA = 0.44          # the storefront's fixed display rate

DEFAULTS = {
    "referralEnabled": 1,
    "abandonedEnabled": 1,
    "minSpendNgn": 20000,      # order value that earns a referral code
    "buyerPercent": 5,         # discount for the referred buyer
    "referrerPercent": 10,     # reward coupon, hard-capped at 10
    "milestone": 2,            # successful purchases that trigger the reward
    "abandonedHours": 2,       # wait before the reminder email
    "abandonedSubject": "You left something lovely in your bag",
    "abandonedTemplate": (
        "Hello {name},\n\n"
        "Your Jaura Store bag is still waiting for you:\n\n{items}\n\n"
        "Pick up right where you left off — your cart is saved:\n{link}\n\n"
        "With love,\nJ Aura Store"
    ),
}

INT_KEYS = ("referralEnabled", "abandonedEnabled", "minSpendNgn",
            "buyerPercent", "referrerPercent", "milestone", "abandonedHours")


def _utcnow():
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


# ------------------------------------------------------------------ settings
def settings():
    rows = {r["key"]: r["value"] for r in query("SELECT key, value FROM growth_settings")}
    out = dict(DEFAULTS)
    for k, v in rows.items():
        if k not in DEFAULTS:
            continue
        out[k] = int(float(v)) if k in INT_KEYS else str(v)
    return _cap(out)


def _cap(s):
    """Keep every number sane. The referrer reward is STRICTLY capped at 10%."""
    s["referrerPercent"] = max(1, min(int(s.get("referrerPercent", 10)), 10))
    s["buyerPercent"] = max(1, min(int(s.get("buyerPercent", 5)), 50))
    s["milestone"] = max(1, min(int(s.get("milestone", 2)), 100))
    s["minSpendNgn"] = max(0, min(int(s.get("minSpendNgn", 20000)), 10**9))
    s["abandonedHours"] = max(1, min(int(s.get("abandonedHours", 2)), 168))
    s["referralEnabled"] = 1 if int(s.get("referralEnabled", 1)) else 0
    s["abandonedEnabled"] = 1 if int(s.get("abandonedEnabled", 1)) else 0
    return s


def save_settings(patch, actor=""):
    import security as sec
    cur = settings()
    for k in DEFAULTS:
        if k not in patch:
            continue
        if k in INT_KEYS:
            raw = patch.get(k)
            if isinstance(raw, bool):      # JSON true/false from the admin UI
                raw = 1 if raw else 0
            v = sec.clean_int(raw, None, 0, 10**9)
            if v is None:
                continue
            cur[k] = v
        else:
            cur[k] = sec.clean(str(patch.get(k) or ""), 4000)
    cur = _cap(cur)
    for k, v in cur.items():
        execute("INSERT INTO growth_settings (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    audit(actor or "admin", "growth.settings", json.dumps(cur)[:400], "")
    return cur


# ------------------------------------------------------------------ helpers
def total_in_ngn(total, currency):
    try:
        total = float(total or 0)
    except (TypeError, ValueError):
        return 0
    if (currency or "").upper() in ("CFA", "XOF", "FCFA"):
        return int(round(total / NGN_TO_CFA))
    return int(round(total))


_ALPHABET = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"


def _mint_code(prefix="JA"):
    for _ in range(30):
        code = prefix + "-" + "".join(secrets.choice(_ALPHABET) for _ in range(5))
        if not one("SELECT 1 FROM referral_codes WHERE code=?", (code,)) and \
           not one("SELECT 1 FROM coupons WHERE code=?", (code,)):
            return code
    return prefix + "-" + secrets.token_hex(4).upper()


def normalize_code(raw):
    import security as sec
    return sec.clean(str(raw or ""), 32).upper().strip()


# ----------------------------------------------------------- promo validation
def check_code(raw_code):
    """Validate a referral or coupon code. Returns a dict for the client:
    {ok, code, percent, kind} or {ok: False, error}."""
    s = settings()
    code = normalize_code(raw_code)
    if not code:
        return {"ok": False, "error": "Type a code first."}

    c = one("SELECT code, percent, kind, active, max_uses, uses, expires_at "
            "FROM coupons WHERE code=?", (code,))
    if c:
        if not c["active"]:
            return {"ok": False, "error": "That code is no longer active."}
        if c["expires_at"] and str(c["expires_at"]) < _utcnow():
            return {"ok": False, "error": "That code has expired."}
        if c["max_uses"] is not None and c["uses"] >= c["max_uses"]:
            return {"ok": False, "error": "That code has already been used."}
        pct = max(1, min(int(c["percent"]), 10 if c["kind"] == "reward" else 90))
        return {"ok": True, "code": code, "percent": pct, "kind": "coupon"}

    if s["referralEnabled"]:
        r = one("SELECT code FROM referral_codes WHERE code=?", (code,))
        if r:
            return {"ok": True, "code": code, "percent": s["buyerPercent"], "kind": "referral"}

    return {"ok": False, "error": "That code was not recognised."}


# ------------------------------------------------------------- order hooks
def record_code_use(code, buyer_email, order_id):
    """Count a successful purchase against a code. On the exact milestone,
    automatically issue the referrer their capped reward coupon.
    Returns a small report dict (used by tests and the admin log)."""
    s = settings()
    code = normalize_code(code)
    report = {"counted": False, "rewardIssued": False}
    if not code:
        return report

    c = one("SELECT code, kind, max_uses, uses, active FROM coupons WHERE code=?", (code,))
    if c:
        execute("UPDATE coupons SET uses=uses+1 WHERE code=?", (code,))
        if c["max_uses"] is not None and c["uses"] + 1 >= c["max_uses"]:
            execute("UPDATE coupons SET active=0 WHERE code=?", (code,))
        audit("system", "coupon.used", f"{code} on {order_id}", "")
        report["counted"] = True
        return report

    r = one("SELECT code, email, name, uses, reward_issued FROM referral_codes WHERE code=?", (code,))
    if not r or not s["referralEnabled"]:
        return report
    if (buyer_email or "").lower() == (r["email"] or "").lower():
        return report                     # your own code never counts
    uses = r["uses"] + 1
    execute("UPDATE referral_codes SET uses=? WHERE code=?", (uses, code))
    audit("system", "referral.used", f"{code} ({uses} so far) on {order_id}", "")
    report["counted"] = True

    # No reward at 1. At EXACTLY `milestone` successful purchases, mint the
    # referrer a one-time coupon — capped at 10%, no higher tiers ever.
    if uses == s["milestone"] and not r["reward_issued"]:
        pct = min(int(s["referrerPercent"]), 10)
        reward = _mint_code("THANKS")
        execute("INSERT INTO coupons (code, percent, kind, email, note, active, max_uses) "
                "VALUES (?,?,?,?,?,1,1)",
                (reward, pct, "reward", r["email"],
                 f"Automatic referrer reward for {code}"))
        execute("UPDATE referral_codes SET reward_issued=1, reward_coupon=? WHERE code=?",
                (reward, code))
        audit("system", "referral.reward", f"{code} -> {reward} ({pct}%)", "")
        report["rewardIssued"] = True
        report["rewardCoupon"] = reward
        _email_reward(r, reward, pct)
    return report


def _email_reward(r, reward, pct):
    try:
        import emailer
        if Config.MAIL_MODE == "none":
            return
        emailer.send(r["email"], "Your Jaura Store reward coupon",
                     f"Hello {r['name'] or ''},\n\n"
                     f"Two friends have now ordered with your referral code — thank you!\n"
                     f"Here is your {pct}% discount coupon for your next order:\n\n"
                     f"    {reward}\n\n"
                     f"Type it in the promo code box at checkout. It works once.\n\n"
                     f"With love,\nJ Aura Store")
    except Exception:
        pass


def maybe_issue_referral(email, name, total, currency):
    """Called after a successful order. Returns the customer's referral code
    (new or existing) when the order qualifies, else ''."""
    s = settings()
    if not s["referralEnabled"] or not email:
        return ""
    existing = one("SELECT code FROM referral_codes WHERE email=?", (email.lower(),))
    if existing:
        return existing["code"]
    if total_in_ngn(total, currency) < s["minSpendNgn"]:
        return ""
    code = _mint_code("JA")
    execute("INSERT INTO referral_codes (code, email, name) VALUES (?,?,?)",
            (code, email.lower(), name or ""))
    audit("system", "referral.minted", f"{code} for {email}", "")
    return code


# ------------------------------------------------------------ abandoned carts
def save_abandoned(token, email, items, currency=""):
    import security as sec
    token = sec.clean(token, 64)
    email = sec.clean_email(email)
    if not token or not email or not isinstance(items, list) or not items:
        return False
    clean = []
    for it in items[:60]:
        if not isinstance(it, dict):
            continue
        clean.append({"id": sec.clean(it.get("id"), 64),
                      "name": sec.clean(it.get("name"), 200),
                      "qty": sec.clean_int(it.get("qty"), 1, 1, 999),
                      "color": sec.clean(it.get("color"), 60)})
    if not clean:
        return False
    now = _utcnow()
    execute("INSERT INTO abandoned_carts (token, email, cart_json, currency, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET "
            "email=excluded.email, cart_json=excluded.cart_json, currency=excluded.currency, "
            "updated_at=excluded.updated_at, completed_at=NULL",
            (token, email, json.dumps(clean, ensure_ascii=False), (currency or "").upper()[:3], now, now))
    return True


def complete_abandoned(token="", email=""):
    """A finished checkout closes the abandoned-cart record."""
    now = _utcnow()
    if token:
        execute("UPDATE abandoned_carts SET completed_at=? WHERE token=? AND completed_at IS NULL",
                (now, token))
    if email:
        execute("UPDATE abandoned_carts SET completed_at=? WHERE email=? AND completed_at IS NULL",
                (now, email.lower() if email else email))


def recover_cart(token):
    import security as sec
    token = sec.clean(token, 64)
    row = one("SELECT cart_json, currency, completed_at FROM abandoned_carts WHERE token=?", (token,))
    if not row or row["completed_at"]:
        return None
    try:
        items = json.loads(row["cart_json"] or "[]")
    except ValueError:
        return None
    return {"items": items, "currency": row["currency"] or ""}


def send_abandoned_reminders(now=None):
    """One email per stalled checkout, after `abandonedHours`. Returns the
    number of reminders sent. Safe to call as often as you like."""
    s = settings()
    if not s["abandonedEnabled"]:
        return 0
    now_dt = now or datetime.datetime.utcnow()
    cutoff = (now_dt - datetime.timedelta(hours=s["abandonedHours"])).isoformat(timespec="seconds")
    rows = query("SELECT id, token, email, cart_json FROM abandoned_carts "
                 "WHERE reminded_at IS NULL AND completed_at IS NULL AND updated_at <= ? "
                 "LIMIT 50", (cutoff,))
    sent = 0
    for r in rows:
        try:
            items = json.loads(r["cart_json"] or "[]")
        except ValueError:
            items = []
        lines = "\n".join(f"  - {i.get('qty', 1)}x {i.get('name') or i.get('id')}" for i in items) or "  (your saved cart)"
        link = f"{Config.SITE_ORIGIN}/checkout.html?recover={r['token']}"
        body = (s["abandonedTemplate"]
                .replace("{name}", (r["email"] or "").split("@")[0])
                .replace("{items}", lines)
                .replace("{link}", link))
        ok = True
        if Config.MAIL_MODE != "none":
            try:
                import emailer
                ok = bool(emailer.send(r["email"], s["abandonedSubject"], body))
            except Exception:
                ok = False
        if ok:
            execute("UPDATE abandoned_carts SET reminded_at=? WHERE id=?", (_utcnow(), r["id"]))
            sent += 1
    if sent:
        audit("system", "abandoned.reminders", f"sent={sent}", "")
    return sent
