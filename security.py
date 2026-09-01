"""CSRF, rate limiting, input sanitisation, security headers."""
import hashlib, hmac, html, re, time, secrets, sqlite3
from flask import request, jsonify, current_app, session
from db import execute, one, connect

# ---------------------------------------------------------------- sanitising
_TAG = re.compile(r"<[^>]*>")
_JS_URL = re.compile(r"(?i)\b(?:javascript|data|vbscript)\s*:")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

def clean(value, max_len=2000, allow_newlines=True):
    """Strip tags / control chars. Returns a plain string safe to store."""
    if value is None:
        return ""
    s = str(value)
    s = _CTRL.sub("", s)
    s = _TAG.sub("", s)                 # drop markup -> neutralises stored XSS
    if not allow_newlines:
        s = s.replace("\r", " ").replace("\n", " ")
    s = html.unescape(s)
    s = _JS_URL.sub("", s)
    return s.strip()[:max_len]

def clean_int(value, default=0, lo=None, hi=None):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if lo is not None and n < lo: return lo
    if hi is not None and n > hi: return hi
    return n

def clean_email(value):
    e = clean(value, 254).lower()
    return e if re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", e) else ""

def safe_url(value, max_len=500):
    """Allow http(s), root-relative (/) and relative asset paths such as
    images/products/x.jpg. Reject javascript:, data:, vbscript: and every
    other URI scheme outright."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if _JS_URL.search(raw):          # dangerous scheme -> reject, never strip
        return ""
    u = clean(raw, max_len)
    if not u:
        return ""
    if u.startswith(("http://", "https://", "/")):
        return u
    if ":" in u.split("/")[0]:       # ftp:, mailto:, ... -> reject
        return ""
    return u

def valid_sku(value):
    s = clean(value, 64).upper()
    return s if re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{1,63}", s) else ""

# ---------------------------------------------------------------------- CSRF
def issue_csrf():
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf"] = tok
    return tok

def csrf_ok():
    supplied = request.headers.get("X-CSRF-Token", "") or ""
    if request.is_json:
        supplied = supplied or (request.get_json(silent=True) or {}).get("_csrf", "")
    expected = session.get("_csrf", "")
    return bool(expected) and hmac.compare_digest(str(supplied), str(expected))

def require_csrf(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*a, **kw):
        if not csrf_ok():
            return jsonify(ok=False, error="Invalid or missing CSRF token. Refresh the page."), 403
        return f(*a, **kw)
    return wrapper

# --------------------------------------------------------------- rate limits
def _client_key():
    fwd = request.headers.get("X-Forwarded-For", "")
    ip = (fwd.split(",")[0].strip() if fwd else "") or request.remote_addr or "unknown"
    return hashlib.sha256(ip.encode()).hexdigest()[:32]

def rate_limit(action, limit=5, window=300, key_extra=""):
    """Sliding-ish window counter. Returns (allowed, retry_after_seconds)."""
    key = _client_key() + (":" + key_extra.strip().lower() if key_extra else "")
    now = time.time()
    try:
        row = one("SELECT hits, window_end FROM rate_limits WHERE key=? AND action=?", (key, action))
        if row and row["window_end"] > now:
            if row["hits"] >= limit:
                return False, int(row["window_end"] - now)
            execute("UPDATE rate_limits SET hits=hits+1 WHERE key=? AND action=?", (key, action))
        else:
            execute(
                "INSERT INTO rate_limits (key, action, hits, window_end) VALUES (?,?,1,?) "
                "ON CONFLICT(key, action) DO UPDATE SET hits=1, window_end=excluded.window_end",
                (key, action, now + window),
            )
    except sqlite3.Error:
        return True, 0
    return True, 0

def clear_rate(action, key_extra=""):
    key = _client_key() + (":" + key_extra.strip().lower() if key_extra else "")
    execute("DELETE FROM rate_limits WHERE key=? AND action=?", (key, action))

def guard(action, limit=5, window=300, key_extra=""):
    ok, retry = rate_limit(action, limit, window, key_extra)
    if not ok:
        return jsonify(ok=False, error=f"Too many attempts. Try again in {retry}s.", retry_after=retry), 429
    return None

# ------------------------------------------------------- Google reCAPTCHA v3
def _recaptcha_token():
    """The token can arrive as a header (set per attempt, so an offline
    retry gets a fresh one), a JSON field, or a form field."""
    tok = request.headers.get("X-Recaptcha-Token", "").strip()
    if tok:
        return tok
    d = request.get_json(silent=True)
    if isinstance(d, dict) and d.get("recaptchaToken"):
        return str(d["recaptchaToken"]).strip()
    return (request.form.get("recaptchaToken") or "").strip()

def verify_recaptcha(token, action=""):
    """Ask Google to score the token. Returns (ok, reason). Network problems
    reaching Google never block a real customer - they resolve to ok."""
    from config import Config
    secret = Config.RECAPTCHA_SECRET_KEY
    if not secret:
        return True, "not configured"
    if not token:
        return (not Config.RECAPTCHA_REQUIRED), "missing token"
    import json as _json
    import urllib.parse
    import urllib.request
    fwd = request.headers.get("X-Forwarded-For", "")
    ip = (fwd.split(",")[0].strip() if fwd else "") or request.remote_addr or ""
    body = urllib.parse.urlencode({
        "secret": secret,
        "response": token[:2048],
        "remoteip": ip,
    }).encode()
    try:
        req = urllib.request.Request(
            "https://www.google.com/recaptcha/api/siteverify",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = _json.loads(resp.read().decode("utf-8") or "{}")
    except Exception:
        # Google unreachable from the server: fail open so a paying customer
        # is never turned away by our own outage.
        return True, "verify unreachable"
    if not data.get("success"):
        return False, "verification failed"
    score = data.get("score")
    if score is not None and float(score) < Config.RECAPTCHA_MIN_SCORE:
        return False, f"low score {score}"
    if action and data.get("action") and data.get("action") != action:
        return False, "action mismatch"
    return True, "ok"

def recaptcha_gate(action=""):
    """Drop-in guard for a route: returns an error response to bounce the
    request, or None to let it through."""
    ok, _why = verify_recaptcha(_recaptcha_token(), action)
    if ok:
        return None
    return jsonify(ok=False, error="Checkout security could not be verified. Please try again."), 400

# ------------------------------------------------------------------- headers
CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob: https:; "
    "media-src 'self' https:; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com data:; "
    "script-src 'self' https://www.google.com/recaptcha/ https://www.gstatic.com/recaptcha/; "
    "frame-src 'self' https://www.google.com/recaptcha/ https://recaptcha.google.com/recaptcha/; "
    "connect-src 'self' https://formsubmit.co https://open.er-api.com https://www.google.com; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://formsubmit.co"
)

def apply_headers(resp):
    prod = current_app.config.get("ENV") == "production"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if prod:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


# ------------------------------------------------- one-tap links from email

def order_token(order_id, action="confirm"):
    """A signature for the confirm / decline link we email to the shop.

    The link has to work from a mail client, so it cannot carry a session
    cookie or a CSRF token. Instead every link is signed with SECRET_KEY and
    only permits that one action on that one order - guessing or editing the
    order id invalidates it.
    """
    from config import Config
    msg = ("%s:%s" % (str(order_id or "").strip().upper(), str(action).lower())).encode()
    return hmac.new(str(Config.SECRET_KEY or "").encode(), msg, hashlib.sha256).hexdigest()[:40]


def order_token_ok(order_id, action, token):
    if not token:
        return False
    return hmac.compare_digest(str(token).strip(), order_token(order_id, action))
