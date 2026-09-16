"""CSRF, rate limiting, input sanitisation, security headers, client IP."""
import hashlib, hmac, html, ipaddress, os, re, time, secrets, sqlite3
from flask import request, jsonify, current_app, session
from db import execute, one, connect

# ------------------------------------------------------------- client IP
# Render, Cloudflare and Vercel all terminate TLS in front of the dyno, so
# request.remote_addr is the *proxy* - never the shopper. Reading it as the
# visitor address is what put shoppers on the analytics map in Finland (the
# datacentre the edge/monitoring traffic comes from) instead of Lagos or
# Lome. The true client address is carried in a header; these are the ones
# our hosts actually set, most trustworthy first.
#
# CF-Connecting-IP / True-Client-IP are written BY Cloudflare (a client
# cannot forge them through the edge). X-Real-IP is set by Render's router.
# X-Forwarded-For is a chain - "client, proxy1, proxy2" - and only the
# left-most PUBLIC address can be the visitor.
CLIENT_IP_HEADERS = (
    "CF-Connecting-IP",      # Cloudflare
    "True-Client-IP",        # Cloudflare Enterprise / Akamai
    "Fly-Client-IP",         # Fly.io
    "X-Vercel-Forwarded-For",  # Vercel
    "X-Real-IP",             # Render / nginx
    "X-Client-IP",
)


def _parse_ip(value):
    """Return an ip_address for `value`, or None. Tolerates "ip:port"."""
    raw = str(value or "").strip().strip('"')
    if not raw:
        return None
    if raw.lower().startswith("for="):
        raw = raw[4:].strip().strip('"')
    if raw.startswith("[") and "]" in raw:               # [2001:db8::1]:443
        raw = raw[1:raw.index("]")]
    elif raw.count(":") == 1 and "." in raw:             # 1.2.3.4:5678
        raw = raw.split(":", 1)[0]
    try:
        return ipaddress.ip_address(raw)
    except ValueError:
        return None


def is_public_ip(value):
    """True for a routable internet address (not LAN / loopback / CGNAT)."""
    ip = _parse_ip(value)
    if ip is None:
        return False
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return False
    if ip.is_multicast or ip.is_unspecified:
        return False
    # 100.64.0.0/10 (carrier NAT) is how Render addresses its own mesh.
    if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
        return False
    return True


def forwarded_chain():
    """Every address in X-Forwarded-For / Forwarded, left to right."""
    chain = []
    for header in ("X-Forwarded-For", "Forwarded"):
        raw = request.headers.get(header, "") or ""
        for part in raw.split(","):
            part = part.strip()
            if header == "Forwarded":
                # Forwarded: for=1.2.3.4;proto=https, for=5.6.7.8
                for bit in part.split(";"):
                    bit = bit.strip()
                    if bit.lower().startswith("for="):
                        part = bit
                        break
                else:
                    continue
            ip = _parse_ip(part)
            if ip is not None:
                chain.append(str(ip))
    return chain


def client_ip(default=""):
    """The visitor's own IP address behind a CDN / proxy.

    Order: the headers our edge writes itself, then the left-most public
    address of the X-Forwarded-For chain (the client; everything after it
    is a proxy that appended itself), then remote_addr. A private address
    is only returned when nothing public exists - local dev and the test
    suite talk to the app from 127.0.0.1 and must keep working.
    """
    try:
        for header in CLIENT_IP_HEADERS:
            value = request.headers.get(header, "")
            first = str(value or "").split(",")[0]
            if is_public_ip(first):
                return str(_parse_ip(first))
        for candidate in forwarded_chain():
            if is_public_ip(candidate):
                return candidate
        # Nothing public: fall back to whatever we were given so rate
        # limiting still has a key in development.
        chain = forwarded_chain()
        if chain:
            return chain[0]
        remote = _parse_ip(request.remote_addr)
        if remote is not None:
            return str(remote)
    except Exception:                      # never let IP parsing break a route
        pass
    return default


# --------------------------------------------------------- bot filtering
# Crawlers, uptime monitors and scrapers are not customers. Counting them
# put "visitors" in Helsinki/Ashburn on the live map (cheap VPS estates in
# Finland and Virginia are where most of that traffic is hosted) and
# inflated every page-view number. They are dropped from analytics - never
# from the storefront itself, which still answers them normally.
BOT_UA_PATTERNS = (
    "bot", "crawl", "spider", "slurp", "search engine", "scraper", "scrapy",
    "curl/", "wget", "python-requests", "python-urllib", "aiohttp", "httpx",
    "go-http-client", "okhttp", "java/", "apache-httpclient", "libwww",
    "node-fetch", "axios/", "guzzlehttp", "postmanruntime", "insomnia",
    "headlesschrome", "phantomjs", "puppeteer", "playwright", "selenium",
    "lighthouse", "pagespeed", "gtmetrix", "pingdom", "uptimerobot",
    "statuscake", "site24x7", "betteruptime", "newrelicpinger", "datadog",
    "monitoring", "healthcheck", "keepalive", "jaura-keepalive",
    "facebookexternalhit", "whatsapp", "telegrambot", "discordbot",
    "slackbot", "twitterbot", "linkedinbot", "embedly", "quora link preview",
    "feedfetcher", "google-read-aloud", "google favicon", "mediapartners",
    "censys", "zgrab", "masscan", "nmap", "netcraft", "expanse", "paloalto",
    "semrush", "ahrefs", "mj12", "dotbot", "blexbot", "seekport", "serpstat",
    "dataforseo", "petalbot", "bytespider", "gptbot", "claudebot", "ccbot",
    "perplexitybot", "amazonbot", "applebot", "yandex", "baiduspider",
    "sogou", "exabot", "duckduckbot", "bingpreview", "adsbot",
)

# Networks that only ever originate automated traffic for a retail shop:
# crawler ranges plus the cheap-VPS/cloud estates that host scrapers and
# uptime monitors. Hetzner Helsinki (Finland) is the single biggest source
# of the phantom "Finland visitors" this shop was seeing.
BOT_IP_NETWORKS = (
    # Googlebot / Google infrastructure
    "66.249.64.0/19", "64.233.160.0/19", "72.14.192.0/18", "209.85.128.0/17",
    "216.239.32.0/19", "35.190.247.0/24", "34.64.0.0/10", "35.192.0.0/12",
    # Bing / Microsoft crawlers
    "40.77.167.0/24", "13.66.139.0/24", "157.55.39.0/24", "207.46.13.0/24",
    "204.79.180.0/24",
    # Hetzner (FI/DE) - the "Finland" visitors
    "65.108.0.0/16", "65.109.0.0/16", "65.21.0.0/16", "95.216.0.0/16",
    "95.217.0.0/16", "135.181.0.0/16", "37.27.0.0/16", "157.90.0.0/16",
    "138.201.0.0/16", "159.69.0.0/16", "168.119.0.0/16", "116.202.0.0/16",
    "144.76.0.0/16", "78.46.0.0/15", "88.99.0.0/16", "94.130.0.0/16",
    # DigitalOcean / Linode / OVH / Contabo scraper estates
    "104.131.0.0/16", "159.65.0.0/16", "165.227.0.0/16", "167.71.0.0/16",
    "178.62.0.0/16", "45.55.0.0/16", "139.59.0.0/16",
    "172.104.0.0/15", "45.79.0.0/16", "139.162.0.0/16",
    "51.178.0.0/16", "51.75.0.0/16", "141.94.0.0/16",
    "161.35.0.0/16", "146.190.0.0/16",
    # Amazon crawler / Alexa style estates commonly used by scrapers
    "54.36.0.0/16", "5.188.0.0/16",
)


def _networks(raw_list):
    out = []
    for item in raw_list:
        try:
            out.append(ipaddress.ip_network(str(item).strip(), strict=False))
        except ValueError:
            continue
    return tuple(out)


def _extra_networks():
    """ANALYTICS_BOT_IP_NETWORKS lets an operator add ranges without a deploy."""
    raw = os.environ.get("ANALYTICS_BOT_IP_NETWORKS", "")
    return _networks([p for p in re.split(r"[,\s]+", raw) if p.strip()])


_BOT_NETWORKS = _networks(BOT_IP_NETWORKS)


def is_bot_ip(value):
    """True when the address belongs to a known crawler / datacentre range."""
    ip = _parse_ip(value)
    if ip is None:
        return False
    for net in _BOT_NETWORKS + _extra_networks():
        try:
            if ip.version == net.version and ip in net:
                return True
        except (TypeError, ValueError):
            continue
    return False


def is_bot_ua(value):
    """True when the User-Agent names a crawler, monitor or HTTP library."""
    ua = str(value or "").strip().lower()
    if not ua:
        return True                       # a real browser always sends one
    return any(pattern in ua for pattern in BOT_UA_PATTERNS)


def bot_reason(ua=None, ip=None):
    """Why this request is not a customer visit - "" when it looks human."""
    ua = request.headers.get("User-Agent", "") if ua is None else ua
    ip = client_ip() if ip is None else ip
    if is_bot_ua(ua):
        return "ua"
    if is_bot_ip(ip):
        return "ip"
    # Chrome/Edge tell us directly, and honest crawlers set it too.
    purpose = (request.headers.get("Sec-Purpose", "") or
               request.headers.get("Purpose", "")).lower()
    if "prefetch" in purpose:
        return "prefetch"
    return ""


def is_bot_request():
    return bool(bot_reason())

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
    # The true client address (see client_ip) - a proxy IP would put every
    # shopper behind the CDN into one shared rate-limit bucket.
    ip = client_ip() or "unknown"
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
    ip = client_ip()
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
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "script-src 'self' https://www.google.com/recaptcha/ https://www.gstatic.com/recaptcha/; "
    "frame-src 'self' https://www.google.com/recaptcha/ https://recaptcha.google.com/recaptcha/; "
    "connect-src 'self' https://open.er-api.com https://www.google.com; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
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
