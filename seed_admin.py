#!/usr/bin/env python3
"""CLI: the ONLY way admin accounts are created.
    python3 seed_admin.py                 -> list admins
    python3 seed_admin.py you@email.com   -> set/reset that admin's password
"""
import sys, getpass
from config import Config
from db import init_db, query, one, execute
import auth as authmod

init_db(); authmod.ensure_seed_admins()

if len(sys.argv) == 1:
    print("Configured ADMIN_EMAILS:", ", ".join(Config.ADMIN_EMAILS))
    print("\nExisting admin accounts:")
    for r in query("SELECT email, created_at, last_login_at FROM admins"):
        print(f"  - {r['email']}  created {r['created_at']}  last login {r['last_login_at']}")
    print("\nSet a password with: python3 seed_admin.py you@email.com")
    sys.exit(0)

email = authmod and sys.argv[1].strip().lower()
if not authmod.is_known_admin(email):
    print(f"'{email}' is not in ADMIN_EMAILS ({', '.join(Config.ADMIN_EMAILS)}).")
    print("Add it to .env first, then re-run. Accounts cannot be created any other way.")
    sys.exit(1)

pw = getpass.getpass("New password: ")
confirm = getpass.getpass("Confirm: ")
if pw != confirm:
    print("Passwords do not match."); sys.exit(1)
ok, msg = authmod.password_strong(pw)
if not ok:
    print("Weak password:", msg); sys.exit(1)
authmod.set_password(email, pw)
print(f"Password set for {email}. Sign in at /admin.html")
