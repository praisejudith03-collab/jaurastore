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

    SITE_ORIGIN = os.environ.get("SITE_ORIGIN", "http://localhost:8080")

    # ---------------------------------------------------------- uploads
    UPLOAD_MODE = os.environ.get("UPLOAD_MODE", "local").lower()
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "data/uploads")
    S3_BUCKET = os.environ.get("S3_BUCKET", "")
    S3_REGION = os.environ.get("S3_REGION", "auto")
    S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
    S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
    S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
    S3_PUBLIC_BASE = os.environ.get("S3_PUBLIC_BASE", "")

    # --------------------------------------------------------- analytics
    ANALYTICS_RETENTION_DAYS = int(os.environ.get("ANALYTICS_RETENTION_DAYS", "400") or 400)
    LIVE_WINDOW_SECONDS = 120      # a visitor counts as "on the site" this long

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = ENV == "production"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8

    OTP_TTL_SECONDS = 600
    OTP_MAX_ATTEMPTS = 5
    OTP_RESEND_COOLDOWN = 60

    LOW_STOCK_THRESHOLD = 5
