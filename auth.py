"""Environment-backed admin authentication and Flask sessions.

The shop has one permanent admin credential: ``ADMIN_BOOTSTRAP_PASSWORD``.
It is read from the process environment for every login attempt and is never
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


def admin_password_configured():
    """Whether the permanent environment credential is present."""
    return bool(os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", ""))


def verify_login(email, password):
    """Check the admin identity and permanent environment password.

    The environment is intentionally read at call time. A Render environment
    update followed by a process restart therefore changes the credential
    without a database migration or an admin-panel password write.
    """
    if not is_known_admin(email):
        return False
    configured = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")
    supplied = str(password or "")
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
