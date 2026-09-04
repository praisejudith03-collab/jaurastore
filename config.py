import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

ROOT = os.path.dirname(os.path.abspath(__file__))

def _emails():
    raw = os.environ.get("ADMIN_EMAILS", "jaurastore@gmail.com")
    out = []
    for part in raw.split(","):
        e = part.strip().lower()
        if e and e not in out:
            out.append(e)
    return out or ["jaurastore@gmail.com"]

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-insecure-key-change-me"
    ENV = os.environ.get("FLASK_ENV", "development")
    DB_PATH = os.environ.get("DB_PATH", os.path.join(ROOT, "data", "jaura.db"))
    # Every product an admin adds or edits lives here. On a host with an
    # ephemeral filesystem (Render, Heroku) point this at the persistent disk
    # or the catalogue resets on every deploy.
    CATALOG_PATH = os.environ.get("CATALOG_PATH", os.path.join(ROOT, "data", "catalog.json"))
    ADMIN_EMAILS = _emails()

    MAIL_MODE = os.environ.get("MAIL_MODE", "none").lower()
    MAIL_FROM = os.environ.get("MAIL_FROM", "jaurastore@gmail.com")
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASS = os.environ.get("SMTP_PASS", "")

    # WhatsApp order notifications (either provider; see whatsapp.py)
    WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
    WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
    WHATSAPP_CALLMEBOT_KEY = os.environ.get("WHATSAPP_CALLMEBOT_KEY", "")
    WHATSAPP_NOTIFY_NUMBER = "".join(
        c for c in os.environ.get("WHATSAPP_NOTIFY_NUMBER", "2290168953101") if c.isdigit())

    # In-process scheduler (abandoned-cart reminders + midnight backup)
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "1") != "0"

    SITE_ORIGIN = os.environ.get("SITE_ORIGIN", "http://localhost:8080")
    # Keep the free host awake: the scheduler pings SITE_ORIGIN/healthz on each
    # tick. Defaults to on when SITE_ORIGIN is set; "0" disables it.
    KEEP_ALIVE = os.environ.get("KEEP_ALIVE", "1" if SITE_ORIGIN and SITE_ORIGIN != "http://localhost:8080" else "0")

    # ------------------------------------- Google reCAPTCHA v2 (checkbox)
    # The shop uses the reCAPTCHA v2 "I'm not a robot" CHECKBOX. Register the
    # site at https://www.google.com/recaptcha/admin as
    #   label:   Jaura Store checkout v2
    #   type:    reCAPTCHA v2 -> "I'm not a robot" Checkbox
    #   domains: jaurastore.com.ng AND www.jaurastore.com.ng
    # then set both keys below (Render -> Environment). The site key is public
    # (sent to the browser via /api/config); the secret key stays server-side.
    #
    # A v3 key with the v2 widget is exactly what makes the box say
    # "Invalid key type" - the keys must come from a v2 Checkbox site.
    #
    # When the keys are not configured the widget never renders and the checks
    # are skipped entirely, so local dev, static hosting and the test suite
    # keep working without any Google account.
    # The shop's own reCAPTCHA v2 Checkbox site key. A site key is public —
    # /api/config hands it to the browser and it lands in the rendered page
    # anyway — so keeping it here is safe, and it means the "I'm not a robot"
    # box still renders on a host where the environment variable was never
    # filled in. A value set in the environment (Render dashboard) wins.
    RECAPTCHA_SITE_KEY = os.environ.get(
        "RECAPTCHA_SITE_KEY",
        "6LciyqUtAAAAALVwLeDPSeXedLAE6ziEU8knio5h")
    RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY", "")
    # v2 checkbox responses carry no score (only success true/false), so this
    # threshold is inert for v2 and kept only so a v3 key still behaves
    # sensibly if one is ever configured by mistake.
    RECAPTCHA_MIN_SCORE = float(os.environ.get("RECAPTCHA_MIN_SCORE", "0.3") or 0.3)
    # When "1", requests WITHOUT a token are rejected too. LEAVE THIS OFF:
    # queued offline orders (whose token expired while the phone had no
    # signal) must still arrive, and checkout is never blocked behind the
    # checkbox. Failed verifications are always rejected.
    RECAPTCHA_REQUIRED = os.environ.get("RECAPTCHA_REQUIRED", "") == "1"

    # ------------------------------------------------------------ Supabase
    # When SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_KEY) are set,
    # the catalogue, carts, orders and admin auth are stored in Supabase
    # instead of the local SQLite DB / JSON files. The app keeps working (with
    # the existing local persistence) when they are not configured, so a fresh
    # checkout and the test suite run without any credentials.
    #
    # SUPABASE_SERVICE_ROLE_KEY is the canonical server-side key. `SUPABASE_KEY`
    # is accepted as an alias (some providers / dashboards name it that way) so
    # either spelling enables the Supabase backend. The value is read at import
    # time, so it also picks up runtime/process environment variables in
    # production where .env is not present.
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
    SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
    SUPABASE_SERVICE_ROLE_KEY = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        or os.environ.get("SUPABASE_KEY", "")
    )
    # True only when a URL + service role key are configured. Access it as a
    # plain class attribute (not a @property, which the class access would
    # return the descriptor object for and always be truthy).
    SUPABASE_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)

    # ---------------------------------------------------------- uploads
    UPLOAD_MODE = os.environ.get("UPLOAD_MODE", "local").lower()
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "data/uploads")
    S3_BUCKET = os.environ.get("S3_BUCKET", "")
    S3_REGION = os.environ.get("S3_REGION", "auto")
    S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
    S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
    S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
    S3_PUBLIC_BASE = os.environ.get("S3_PUBLIC_BASE", "")

    # ------------------------------------------------- GitHub repo sync
    # When the shop is configured to push its product/data state back to the
    # GitHub repository (so admin edits survive a redeploy and stay in sync
    # with Supabase), these are read by repo_sync.py. They are optional: the
    # data files are still regenerated and committed locally without them.
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GITHUB_API_TOKEN", "")
    GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")
    GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
    # Optional "Name <email>" used by repo_sync for the commit author.
    GITHUB_COMMITTER = os.environ.get("GITHUB_COMMITTER", "")
    # When set, admin product mutations also trigger a best-effort repo sync
    # (regenerate js/products-data.js + commit any changed data files).
    REPO_SYNC_ON_WRITE = (os.environ.get("REPO_SYNC_ON_WRITE", "1") or "1") == "1"

    # --------------------------------------------------------- analytics
    ANALYTICS_RETENTION_DAYS = int(os.environ.get("ANALYTICS_RETENTION_DAYS", "400") or 400)
    LIVE_WINDOW_SECONDS = 120      # a visitor counts as "on the site" this long

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = ENV == "production"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 365

    OTP_TTL_SECONDS = 600
    OTP_MAX_ATTEMPTS = 5
    OTP_RESEND_COOLDOWN = 60

    # ------------------------------------------------- admin access recovery
    # Last-resort way back into the admin portal when the password is lost and
    # no reset code can be received. On the FIRST boot after this ships, the
    # shared admin password is forced to this value exactly once (see
    # auth.apply_bootstrap_password, which stamps an `admin_bootstrap_applied`
    # marker so it never fires again).
    #
    # SECURITY: this default is public (it lives in the repo), so sign in,
    # change the password from the admin portal immediately, and set
    # ADMIN_BOOTSTRAP_PASSWORD in the host dashboard if you ever need a second
    # recovery. The marker makes it one-shot per database.
    BOOTSTRAP_ADMIN_PASSWORD = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "") or "Jaura@Admin#2026x"

    LOW_STOCK_THRESHOLD = 5
