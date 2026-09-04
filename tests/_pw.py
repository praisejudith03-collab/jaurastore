"""One admin password for the whole pytest run.

The suite signs in through the shop's own admin API, and the three modules
that do (`test_api`, `test_sitemap`, `test_storage_supabase`) share one SQLite
file - /tmp/jaura_test.db - so they have to agree on the password.

It is generated afresh on every run rather than committed: this repository is
public, and a password written into it is a password anyone can read. The two
that used to live here (the `BOOTSTRAP_ADMIN_PASSWORD` default in config.py and
this suite's own login password) were published that way; both are dead values
now and `tests/test_static_exposure.py` fails the build if either comes back.

Pin a value when a run has to be reproducible:

    ADMIN_PW='...' python -m pytest tests/ -q
"""
import os
import secrets
import string

_ALPHABET = string.ascii_letters + string.digits


def _generate():
    """A password that clears auth.password_strong(): 8+ chars, a letter, a digit."""
    while True:
        pw = "Jaura-" + "".join(secrets.choice(_ALPHABET) for _ in range(18))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def _pinned():
    pw = (os.environ.get("ADMIN_PW") or "").strip()
    if not pw:
        return ""
    if len(pw) < 8 or not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):
        raise SystemExit(
            "ADMIN_PW must be at least 8 characters and mix letters with digits "
            "(auth.password_strong). Unset it to let the suite generate one."
        )
    return pw


PW = _pinned() or _generate()
