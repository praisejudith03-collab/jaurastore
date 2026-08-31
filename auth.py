"""Admin accounts, sessions, password rules and OTP reset.

Two backends:

* **Local (default).** Admin accounts live in the SQLite ``admins`` table and
  a 6-digit reset code in ``otp_codes`` (both created by ``db.init_db``). This
  is what the test suite and a fresh checkout exercise.
* **Supabase.** When ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` are set,
  admin login is verified against Supabase Auth (GoTrue) and the admin is
  mirrored in the local ``admins`` table so the rest of the app (audit, session
  cookie, per-device password change) keeps working unchanged.
"""
import html, re, secrets, time, datetime
import sqlite3
from flask import session
from config import Config
from db import connect, execute, one, query, init_db
from werkzeug.security import generate_password_hash, check_password_hash

# A password must be at least this long and mix letters with numbers.
_MIN_PW = 8
OTP_TTL = Config.OTP_TTL_SECONDS or 600
OTP_MAX_ATTEMPTS = Config.OTP_MAX_ATTEMPTS or 5
OTP_COOLDOWN = Config.OTP_RESEND_COOLDOWN or 60

# ------------------------------------------------------------------ password
def password_strong(pw):
    """Return (ok, message). Rejects anything too short or too predictable."""
    pw = str(pw or "")
    if len(pw) < _MIN_PW:
        return False, f"Password must be at least {_MIN_PW} characters long."
    if not re.search(r"[A-Za-z]", pw):
        return False, "Password needs at least one letter."
    if not re.search(r"[0-9]", pw):
        return False, "Password needs at least one number."
    return True, ""


# ------------------------------------------------------------------- accounts
def ensure_seed_admins():
    """Make sure every ADMIN_EMAILS address has a row in the admins table.

    The row is created with a hash that can never match (so no one can log in
    until seed_admin.py / set_password() gives them one), and the caller still
    owns the only way to set the password.
    """
    init_db()
    for email in Config.ADMIN_EMAILS:
        row = one("SELECT id FROM admins WHERE email=?", (email,))
        if not row:
            execute(
                "INSERT INTO admins (email, password_hash, role) VALUES (?,?,?)",
                (email, generate_password_hash(secrets.token_urlsafe(32)), "admin"),
            )


def is_known_admin(email):
    """True if this address is in the admin email list (never enumerate)."""
    return (email or "").strip().lower() in Config.ADMIN_EMAILS


def sole_admin_email():
    """The single admin address when exactly one is configured, else None."""
    return Config.ADMIN_EMAILS[0] if len(Config.ADMIN_EMAILS) == 1 else None


def _hash_for(email):
    row = one("SELECT password_hash FROM admins WHERE email=?",
              ((email or "").strip().lower(),))
    return row["password_hash"] if row else None


def verify_login(email, pw):
    """Check an email + password. Local DB hash, or Supabase Auth when enabled."""
    email = (email or "").strip().lower()
    if not is_known_admin(email) or not pw:
        return False
    if Config.SUPABASE_ENABLED:
        from supabase_store import supabase_verify_login
        if supabase_verify_login(email, pw):
            _ensure_local_admin(email)
            return True
        return False
    h = _hash_for(email)
    if not h:
        return False
    return check_password_hash(h, str(pw))


def set_password(email, pw, shared=True):
    """Set (or reset) an admin's password.

    The shop uses ONE shared admin password across every admin account, so
    several staff can sign in at once with the same password and any of their
    admin emails. `set_password` applies the new password to every known admin
    email (when `shared=True`, the default), and always stores a local hash so
    the app keeps working even when Supabase is not configured.
    """
    email = (email or "").strip().lower()
    if email and not is_known_admin(email):
        return False
    ok, _msg = password_strong(pw)
    if not ok:
        return False
    hash_ = generate_password_hash(str(pw))
    targets = Config.ADMIN_EMAILS if shared else ([email] if email else [])
    for e in targets:
        execute(
            "INSERT INTO admins (email, password_hash, role) VALUES (?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash",
            (e, hash_, "admin"),
        )
    # When Supabase Auth is the login backend, mirror the same (shared) password
    # onto every admin account there so all of them sign in with it. Best effort.
    if Config.SUPABASE_ENABLED:
        try:
            from supabase_store import supabase_set_shared_password
            supabase_set_shared_password(str(pw))
        except Exception:
            pass
    return True


def set_shared_password(pw):
    """Set the ONE password every admin account shares.

    Convenience wrapper for the admin "Change password" flow: it does not need
    an email because the password is shared by all admin emails.
    """
    return set_password("", pw, shared=True)


def _ensure_local_admin(email):
    """Mirror a Supabase-authenticated admin into the local table."""
    if one("SELECT id FROM admins WHERE email=?", (email,)):
        return
    execute(
        "INSERT INTO admins (email, password_hash, role) VALUES (?,?,?)",
        (email, generate_password_hash(secrets.token_urlsafe(32)), "admin"),
    )


# ------------------------------------------------------------------- session
def login(email):
    """Open an admin session (Flask session cookie, held by the server)."""
    email = (email or "").strip().lower()
    session["admin_email"] = email
    execute("UPDATE admins SET last_login_at=? WHERE email=?",
            (datetime.datetime.utcnow().isoformat(timespec="seconds"), email))


def logout():
    """End the admin session."""
    session.pop("admin_email", None)


def current_admin():
    """The signed-in admin email, or None."""
    email = session.get("admin_email")
    return email or None


def require_admin(f):
    """Decorator: 401 unless an admin is signed in."""
    from functools import wraps
    from flask import jsonify

    @wraps(f)
    def wrapper(*a, **kw):
        if not current_admin():
            return jsonify(ok=False, error="Please sign in as the shop admin."), 401
        return f(*a, **kw)

    return wrapper


# ----------------------------------------------------------------------- OTP
def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


def _expiry():
    return datetime.datetime.utcnow() + datetime.timedelta(seconds=OTP_TTL)


def otp_requested_recently(email):
    """True if a code was sent within the cooldown window."""
    email = (email or "").strip().lower()
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(seconds=OTP_COOLDOWN)
    row = one(
        "SELECT created_at FROM otp_codes WHERE email=? AND purpose='reset' "
        "AND consumed_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (email,))
    if not row:
        return False
    try:
        created = datetime.datetime.fromisoformat(row["created_at"])
    except ValueError:
        return False
    return created > cutoff


def create_otp(email):
    """Generate and store a reset code. Returns the plain code to email."""
    email = (email or "").strip().lower()
    code = f"{secrets.randbelow(1000000):06d}"
    execute(
        "INSERT INTO otp_codes (email, code_hash, purpose, expires_at) "
        "VALUES (?,?,?,?)",
        (email, generate_password_hash(code), "reset", _expiry().isoformat(timespec="seconds")),
    )
    return code


def verify_otp(email, code):
    """Check a submitted code. Returns (ok, message)."""
    email = (email or "").strip().lower()
    row = one(
        "SELECT id, code_hash, expires_at, attempts, consumed_at FROM otp_codes "
        "WHERE email=? AND purpose='reset' AND consumed_at IS NULL "
        "ORDER BY created_at DESC LIMIT 1",
        (email,))
    if not row:
        return False, "No verification code pending. Request a new one."
    if row["attempts"] >= OTP_MAX_ATTEMPTS:
        return False, "Too many attempts. Request a new code."
    if not check_password_hash(row["code_hash"], str(code or "")):
        execute("UPDATE otp_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
        return False, "That code is not correct."
    try:
        expires = datetime.datetime.fromisoformat(row["expires_at"])
    except (ValueError, TypeError):
        return False, "That code has expired."
    if datetime.datetime.utcnow() > expires:
        return False, "That code has expired. Request a new one."
    execute("UPDATE otp_codes SET consumed_at=? WHERE id=?",
            (_now(), row["id"]))
    return True, ""
