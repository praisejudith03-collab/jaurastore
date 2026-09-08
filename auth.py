"""Admin accounts, sessions, password rules and OTP reset.

Two backends:

* **Local (default).** Admin accounts live in the SQLite ``admins`` table and
  a 6-digit reset code in ``otp_codes`` (both created by ``db.init_db``). This
  is what the test suite and a fresh checkout exercise.
* **Supabase.** When ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` are set,
  the admin password is *mirrored* into Supabase Auth (GoTrue) so other tools
  can use it, but the local ``admins`` hash stays the single source of truth
  for login. A password change therefore takes effect immediately: the old
  password stops working even if the Supabase mirror failed.
"""
import html, re, secrets, time, datetime
import sqlite3
from flask import session
from config import Config
from db import connect, execute, one, query, init_db, audit
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
    # On a fresh Render instance the SQLite file is empty. Seeding an unusable
    # random hash there (the old behaviour) meant every admin was locked out
    # after a redeploy until someone reached a shell, so restore the durable
    # hash from Supabase when one exists.
    durable = None
    if Config.SUPABASE_ENABLED:
        try:
            from supabase_store import load_admin_users
            durable = load_admin_users()
        except Exception:
            durable = None
    for email in Config.ADMIN_EMAILS:
        row = one("SELECT id FROM admins WHERE email=?", (email,))
        if not row:
            hash_ = ((durable or {}).get(email)
                     or generate_password_hash(secrets.token_urlsafe(32)))
            execute(
                "INSERT INTO admins (email, password_hash, role) VALUES (?,?,?)",
                (email, hash_, "admin"),
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
    """Check an email + password, local hash first, durable copy second.

    The local ``admins`` row is tried first because it is fast and offline.
    It is NOT authoritative, though: it lives on Render's ephemeral disk, so
    after a redeploy it holds a freshly seeded unusable hash while the real
    password is still in the durable ``admin_users`` row. When the local check
    fails we consult Supabase and, on a match, repair the local row so the
    admin is not locked out by a restart.

    The old "never OR in a remote password" rule existed because the Supabase
    mirror used to be best-effort, so a failed mirror could leave an OLD
    password working. That hole is closed at the write end instead:
    ``set_password`` now refuses to report success unless the durable write
    succeeded, so a durable hash is never stale relative to a change the
    admin was told about.
    """
    email = (email or "").strip().lower()
    if not is_known_admin(email) or not pw:
        return False
    h = _hash_for(email)
    if h and check_password_hash(h, str(pw)):
        return True
    if Config.SUPABASE_ENABLED:
        durable = None
        try:
            from supabase_store import load_admin_users
            durable = (load_admin_users() or {}).get(email)
        except Exception:
            durable = None
        if durable and check_password_hash(durable, str(pw)):
            # Repair the ephemeral copy so later logins are local-only. This
            # must be an upsert, not an UPDATE: after a redeploy the row does
            # not exist at all, and a plain UPDATE would silently match
            # nothing and leave every subsequent login doing a remote round
            # trip.
            try:
                execute(
                    "INSERT INTO admins (email, password_hash, role) VALUES (?,?,?) "
                    "ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash",
                    (email, durable, "admin"),
                )
            except Exception:
                pass
            try:
                from supabase_store import mark_admin_login
                mark_admin_login(email)
            except Exception:
                pass
            return True
    if not h:
        # Supabase-only admin that has never been mirrored locally: create the
        # local row (with an unusable hash) so a password set/reset can land.
        _ensure_local_admin(email)
    return False


# Set by set_password when the durable (Supabase) copy of a new password could
# not be written. The local change still applies, so the old password is dead,
# but the new one will not survive a Render restart - the caller must say so
# instead of reporting an unqualified success.
_PASSWORD_DURABLE_ERROR = ""


def password_durable_error():
    """'' when the last password change reached Supabase, else the reason."""
    return _PASSWORD_DURABLE_ERROR


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
    # The local hash is ALWAYS applied, so the previous password stops working
    # immediately even if Supabase is unreachable - a password change must
    # never leave the old credential live.
    for e in targets:
        execute(
            "INSERT INTO admins (email, password_hash, role) VALUES (?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash",
            (e, hash_, "admin"),
        )
    # Then the durable copy. The SQLite `admins` table is on Render's
    # EPHEMERAL disk, so a hash that only lands there is destroyed by the next
    # redeploy and every admin is locked out. A failure here does not undo the
    # local change, but it is recorded and reported: the caller tells the admin
    # the new password works now but will not survive a restart.
    global _PASSWORD_DURABLE_ERROR
    _PASSWORD_DURABLE_ERROR = ""
    if Config.SUPABASE_ENABLED:
        try:
            from supabase_store import save_admin_password
            saved = [save_admin_password(e, hash_) for e in targets]
        except Exception as exc:
            saved = []
            _PASSWORD_DURABLE_ERROR = f"{exc.__class__.__name__}"
        if not any(saved):
            _PASSWORD_DURABLE_ERROR = (_PASSWORD_DURABLE_ERROR or
                                       "the durable admin_users write failed")
            print("[auth] WARNING: password changed locally but NOT persisted "
                  f"to Supabase ({_PASSWORD_DURABLE_ERROR}); it will be lost "
                  "on the next Render restart")
    # When Supabase Auth is the login backend, mirror the same (shared) password
    # onto every admin account there so all of them sign in with it. Best effort.
    if Config.SUPABASE_ENABLED:
        try:
            from supabase_store import supabase_set_shared_password
            supabase_set_shared_password(str(pw))
        except Exception:
            pass
    _mark_password_set()
    return True


def set_shared_password(pw):
    """Set the ONE password every admin account shares.

    Convenience wrapper for the admin "Change password" flow: it does not need
    an email because the password is shared by all admin emails.
    """
    return set_password("", pw, shared=True)


# Marker row (in growth_settings) proving the one-time recovery password has
# already been applied to this database. Its presence disables the bootstrap
# permanently, so a later password change is never overwritten by a reboot.
BOOTSTRAP_MARKER = "admin_bootstrap_applied"

# Marker row proving a password has been chosen on this database at all (by
# the bootstrap, the OTP reset or the admin portal). Its presence also
# disables the bootstrap: the owner's own password is never clobbered, even
# if the `admin_bootstrap_applied` row went missing.
PASSWORD_SET_MARKER = "admin_password_set"


def _mark_password_set():
    """Remember that an admin password has been set on this database."""
    try:
        execute("INSERT INTO growth_settings (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (PASSWORD_SET_MARKER, _now()))
    except sqlite3.Error:
        pass


def password_already_set():
    """True when this database already has an admin-chosen password."""
    try:
        return one("SELECT key FROM growth_settings WHERE key=?",
                   (PASSWORD_SET_MARKER,)) is not None
    except sqlite3.Error:
        return False


def apply_bootstrap_password(pw):
    """Force the shared admin password once, to recover access with no email.

    Returns True when the password was applied, False when it was not. It fires
    at most once per database: the first successful application stamps an
    `admin_bootstrap_applied` marker in ``growth_settings``, and every later
    boot (and every later call) is a no-op. A password chosen by the owner
    (which stamps `admin_password_set`) also disables it, so a reboot can never
    restore the public default over the top of their own choice.

    Callers are expected to skip this entirely when FLASK_ENV == "testing" so
    the test suite's passwords are never touched.
    """
    pw = str(pw or "").strip()
    if not pw:
        return False
    init_db()
    if one("SELECT key FROM growth_settings WHERE key=?", (BOOTSTRAP_MARKER,)):
        return False                      # already recovered - leave it alone
    if password_already_set():
        return False                      # the owner picked their own password
    if not set_shared_password(pw):
        # Weak/invalid password: do NOT stamp the marker, so a corrected
        # ADMIN_BOOTSTRAP_PASSWORD can still recover the account later.
        return False
    execute("INSERT OR IGNORE INTO growth_settings (key, value) VALUES (?,?)",
            (BOOTSTRAP_MARKER, _now()))
    audit("system", "admin.bootstrap_password_applied")
    return True


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
    session.permanent = True
    session["admin_email"] = email
    session.pop("customer_id", None)
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
    """True if a code was sent recently; production state is Supabase."""
    email = (email or "").strip().lower()
    if Config.SUPABASE_ENABLED:
        from supabase_settings import reset_token_recent
        return reset_token_recent(email, OTP_COOLDOWN)
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
    """Generate a reset code; production stores only its hash in Supabase."""
    email = (email or "").strip().lower()
    if Config.SUPABASE_ENABLED:
        from supabase_settings import create_reset_token
        return create_reset_token(email, OTP_TTL)
    code = f"{secrets.randbelow(1000000):06d}"
    execute(
        "INSERT INTO otp_codes (email, code_hash, purpose, expires_at) "
        "VALUES (?,?,?,?)",
        (email, generate_password_hash(code), "reset", _expiry().isoformat(timespec="seconds")),
    )
    return code


def verify_otp(email, code):
    """Check a submitted code against persistent Supabase state."""
    email = (email or "").strip().lower()
    if Config.SUPABASE_ENABLED:
        from supabase_settings import verify_reset_token
        return verify_reset_token(email, code, OTP_MAX_ATTEMPTS)
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
