"""Environment-backed admin authentication and Flask sessions.

The shop has permanent admin credentials held in the environment only:
``ADMIN_MASTER_PASSWORD`` (the primary master password) and
``ADMIN_BOOTSTRAP_PASSWORD`` (the secondary/permanent fallback). Both are
read from the process environment for every login attempt and are never
stored in SQLite, Supabase, a cookie, or an audit record. ``ADMIN_EMAILS``
continues to identify which email addresses may open the admin session; it is
not a password-recovery mechanism.
"""
import hmac
import os
import re
from functools import wraps

from flask import jsonify, session

from config import Config

_MIN_PW = 8


def password_strong(password):
    """Return ``(ok, message)`` for customer-created passwords.

    Admin passwords are environment-managed and are intentionally not changed
    through the application. This helper remains for customer accounts.
    """
    value = str(password or "")
    if len(value) < _MIN_PW:
        return False, f"Password must be at least {_MIN_PW} characters long."
    if not re.search(r"[A-Za-z]", value):
        return False, "Password needs at least one letter."
    if not re.search(r"[0-9]", value):
        return False, "Password needs at least one number."
    return True, ""


def is_known_admin(email):
    """Return whether ``email`` is one of the configured admin identities."""
    return (email or "").strip().lower() in Config.ADMIN_EMAILS


def sole_admin_email():
    """Return the only configured admin email, when there is exactly one."""
    return Config.ADMIN_EMAILS[0] if len(Config.ADMIN_EMAILS) == 1 else None


def master_password():
    """The live master password from ``ADMIN_MASTER_PASSWORD`` ('' when unset).

    Read from the environment on EVERY call, never cached at import time:
    saving a new value in the host dashboard takes effect on the very next
    login attempt, with no restart, no database write, nothing else. The
    variable has no default - unset simply means "no master password", and
    every login is checked against ``ADMIN_BOOTSTRAP_PASSWORD`` (the
    secondary/permanent credential) only.
    """
    return str(os.environ.get("ADMIN_MASTER_PASSWORD", "") or "").strip()


def master_password_matches(supplied):
    want = master_password()
    if not want or not supplied:
        return False
    return hmac.compare_digest(str(supplied).encode("utf-8"), want.encode("utf-8"))


def bootstrap_password():
    """The live permanent credential from ``ADMIN_BOOTSTRAP_PASSWORD``."""
    return str(os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "") or "").strip()


def admin_password_configured():
    """Whether any permanent environment credential is present."""
    return bool(master_password() or bootstrap_password())


def verify_login(email, password):
    """Check the admin identity and the environment credentials.

    ``ADMIN_MASTER_PASSWORD`` is the PRIMARY master password;
    ``ADMIN_BOOTSTRAP_PASSWORD`` is the secondary (permanent fallback).

    Both are intentionally read from the environment at call time. A Render
    environment update followed by a process restart changes either
    credential with no database migration and no admin-panel password write.
    Neither credential is ever stored in SQLite, Supabase, a cookie, or an
    audit record.
    """
    if not is_known_admin(email):
        return False
    supplied = str(password or "")
    if master_password_matches(supplied):
        return True
    configured = bootstrap_password()
    return bool(configured) and hmac.compare_digest(supplied, configured)


def login(email):
    """Open an admin session; no credential material is persisted."""
    email = (email or "").strip().lower()
    session.permanent = True
    session["admin_email"] = email
    session.pop("customer_id", None)


def logout():
    """End the current admin session."""
    session.pop("admin_email", None)


def current_admin():
    """Return the signed-in admin email, or ``None``."""
    email = session.get("admin_email")
    return email or None


def require_admin(f):
    """Decorator returning 401 unless an admin session is active."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_admin():
            return jsonify(ok=False, error="Please sign in as the shop admin."), 401
        return f(*args, **kwargs)

    return wrapper
