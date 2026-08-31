#!/usr/bin/env python3
"""CLI: the ONLY way admin accounts are created.

    python3 seed_admin.py                       -> list admins
    python3 seed_admin.py you@email.com         -> set/reset that admin's password
    python3 seed_admin.py you@email.com --pw PASSPHRASE
    python3 seed_admin.py --all --pw PASSPHRASE -> set the shared password for every admin
    ADMIN_PASSWORD=... python3 seed_admin.py    -> same as --pw via env var

The shop uses ONE shared admin password, so `--all` (or `--pw` on any admin
email) applies the password to every admin account. Non-interactive mode is
intended for deployments / cron: pass the password via `--pw`, the
`ADMIN_PASSWORD` env var, or a piped stdin.
"""
import sys, getpass, os, argparse
from config import Config
from db import init_db, query, one, execute
import auth as authmod

init_db(); authmod.ensure_seed_admins()


def _all_emails():
    return Config.ADMIN_EMAILS


def _set_shared(pw):
    ok, msg = authmod.password_strong(pw)
    if not ok:
        print("Weak password:", msg); sys.exit(1)
    authmod.set_shared_password(pw)
    print("Password set. NOTE: the shop uses ONE shared admin password - it now")
    print(f"applies to every admin account ({', '.join(_all_emails())}).")
    print("Sign in at /admin.html using any of those emails with this password.")
    sys.exit(0)


# ---- argument parsing -------------------------------------------------------
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("email", nargs="?")
parser.add_argument("--all", action="store_true")
parser.add_argument("--pw", dest="pw", default=None)
parser.add_argument("--help", action="store_true")
args, _ = parser.parse_known_args(sys.argv[1:])

if args.help:
    parser.print_help(); sys.exit(0)

# Password source order: --pw, ADMIN_PASSWORD env, piped stdin, interactive.
pw = args.pw
if pw is None:
    pw = os.environ.get("ADMIN_PASSWORD", "").strip() or None
if not pw and not sys.stdin.isatty():
    try:
        pw = sys.stdin.read().strip() or None
    except Exception:
        pw = None

if args.all:
    if not pw:
        pw = getpass.getpass("New shared password: ")
        if getpass.getpass("Confirm: ") != pw:
            print("Passwords do not match."); sys.exit(1)
    _set_shared(pw)

if args.email is None and not args.all:
    print("Configured ADMIN_EMAILS:", ", ".join(Config.ADMIN_EMAILS))
    print("\nExisting admin accounts:")
    for r in query("SELECT email, created_at, last_login_at FROM admins"):
        print(f"  - {r['email']}  created {r['created_at']}  last login {r['last_login_at']}")
    print("Set a password with: python3 seed_admin.py you@email.com [--pw PASS]")
    print("Set the one shared password for everyone: python3 seed_admin.py --all [--pw PASS]")
    sys.exit(0)

email = (args.email or "").strip().lower()
if not authmod.is_known_admin(email):
    print(f"'{email}' is not in ADMIN_EMAILS ({', '.join(Config.ADMIN_EMAILS)}).")
    print("Add it to .env first, then re-run. Accounts cannot be created any other way.")
    sys.exit(1)

if not pw:
    pw = getpass.getpass("New password: ")
    if getpass.getpass("Confirm: ") != pw:
        print("Passwords do not match."); sys.exit(1)
_set_shared(pw)
