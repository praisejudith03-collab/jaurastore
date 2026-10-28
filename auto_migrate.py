"""Boot-time schema auto-migration for the live Supabase project.

The storefront talks to Supabase through PostgREST, which can read and write
rows but cannot run DDL. A column the live `site_settings` table never got -
`popup_banner_active` on a project created before the welcome pop-up shipped -
therefore failed EVERY Admin save that carried it, and the owner saw a
"database schema" error pop-up instead of a saved toggle.

This module closes that gap the same way `db.migrate()` closes it for SQLite:
on every boot (and on demand from a failed Admin save) it applies the
known-missing `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements to the
live database. Every statement is additive and idempotent, so running them on
a healthy database writes nothing.

Backends, tried in order, all optional:
  1. a direct Postgres connection (psycopg / psycopg2) using SUPABASE_DB_URL
     or DATABASE_URL;
  2. `psql` (the same tool migrate_supabase.py --schema uses);
  3. a Supabase RPC named exec_sql / execute_sql / run_sql /
     apply_schema_migration when the project defines one.

If none is available the migration is a no-op that only logs the exact
statement to run in the Supabase SQL editor - it never blocks the boot, and
`supabase_settings` has its own legacy-column fallback so the Admin toggle
still saves.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time

from config import Config

# Every site_settings column the Admin portal can write, with the DDL used to
# create it when an older table lacks it. Additive and idempotent.
SITE_SETTINGS_COLUMNS = {
    "popup_banner_active": "boolean not null default true",
    "store_active": "boolean not null default true",
    "conv_banner": "text not null default ''",
    "conv_banner_fr": "text not null default ''",
    "conv_bold": "text not null default ''",
    "shipping_note": "text not null default ''",
    "welcome_enabled": "text not null default ''",
    "welcome_title": "text not null default ''",
    "welcome_title_fr": "text not null default ''",
    "welcome_body": "text not null default ''",
    "welcome_body_fr": "text not null default ''",
    "welcome_image_url": "text not null default ''",
    "welcome_cta_label": "text not null default ''",
    "welcome_cta_label_fr": "text not null default ''",
    "welcome_cta_href": "text not null default ''",
    "social_whatsapp_url": "text not null default ''",
    "social_instagram_url": "text not null default ''",
    "social_tiktok_url": "text not null default ''",
    "social_facebook_url": "text not null default ''",
    "cfa_payment_provider": "text not null default ''",
    "cfa_payment_name": "text not null default ''",
    "cfa_payment_account": "text not null default ''",
    "cfa_payment_instructions": "text not null default ''",
    "togo_payment_provider": "text not null default ''",
    "togo_payment_name": "text not null default ''",
    "togo_payment_account": "text not null default ''",
    "togo_payment_instructions": "text not null default ''",
    "naira_payment_bank": "text not null default ''",
    "naira_payment_name": "text not null default ''",
    "naira_payment_account": "text not null default ''",
    "naira_payment_instructions": "text not null default ''",
    "brand_logo_url": "text not null default ''",
    "favicon_32_url": "text not null default ''",
    "favicon_48_url": "text not null default ''",
    "apple_touch_icon_url": "text not null default ''",
    "icon_192_url": "text not null default ''",
    "whatsapp_number_ng": "text not null default ''",
    "whatsapp_number_bj": "text not null default ''",
    "bank_name": "text not null default ''",
    "account_number": "text not null default ''",
    "account_name": "text not null default ''",
}

# The one statement the owner asked for, first in the list so the log and any
# manual repair show exactly this line.
POPUP_BANNER_STATEMENT = (
    "alter table site_settings add column if not exists "
    "popup_banner_active boolean not null default true"
)

# The products columns this codebase writes on every save. A live table that
# predates one of them makes `supabase_store._upsert_products_resilient`
# REFUSE the write (that is deliberate: silently dropping an admin-entered
# field is data loss), and the owner sees "The product could not be saved to
# Supabase" on a photo change. Healing them at boot is what turns that from a
# manual SQL chore into something a redeploy fixes by itself.
#
# The DO block is one statement so a database without a products table (a
# fresh project, or the test suite's SQLite cache) is a no-op instead of
# aborting the site_settings heals that follow it.
# Kept only so older imports/notes still resolve. NEVER executed: a boot pass
# that sets availability is a data change, and this module is a schema healer.
PRODUCT_RECOVERY_STATEMENT = "UPDATE products SET is_active = true, is_deleted = false;"

# SCHEMA ONLY - additive, idempotent DDL. No row is ever written here.
#
# This statement used to end with
#     perform set_config('jaura.catalog_admin', 'on', true);
#     update public.products set is_active = true, is_deleted = false,
#            online = true, source = 'admin' where ...
# i.e. EVERY boot (and every redeploy, restart, crash-recovery, or a boot pass
# retried after an Admin save failure) force-published the whole catalogue and
# emptied the Trash Bin. Two consequences the owner kept reporting:
#   * a product they had just hidden came back on the website, and an item in
#     the Trash reappeared as a live product - the "Restore" button then said
#     "That product is not in the Trash" because the row had been un-deleted
#     behind the portal's back;
#   * hiding/deleting anything looked unreliable, because the next restart
#     silently undid it.
# Availability belongs to the owner. The sanctioned recovery path is the
# explicit Admin action (catalog.recover_unavailable_products ->
# recover_unavailable_products.sql, which sets the same flag INSIDE its own
# audited transaction and records every row it touches).
PRODUCT_SCHEMA_STATEMENT = """do $$ begin
  if to_regclass('public.products') is null then return; end if;
  alter table public.products add column if not exists category_id text;
  alter table public.products add column if not exists supplier_status text not null default 'ok';
  alter table public.products add column if not exists is_active boolean not null default true;
  alter table public.products add column if not exists is_deleted boolean not null default false;
  alter table public.products add column if not exists "supplierCost" numeric;
  alter table public.products add column if not exists "optionSupplierCost" jsonb;
  alter table public.products add column if not exists "enableCustomNote" boolean not null default false;
  alter table public.products add column if not exists "customNotePrompt" text not null default '';
  alter table public.products add column if not exists "placeholderImage" text;
  alter table public.products add column if not exists "usesPlaceholder" boolean default false;
  alter table public.products add column if not exists dimensions text;
  alter table public.products add column if not exists "bulkQty" integer;
  alter table public.products add column if not exists "bulkPercent" integer;
end $$;"""

RPC_NAMES = ("exec_sql", "execute_sql", "run_sql", "apply_schema_migration")

_lock = threading.Lock()
_ran = False
_backend = ""
_last_error = ""
_last_attempt = 0.0
# A failed attempt (no connection configured, transient network error) is not
# retried on every single call: a save-triggered heal gets one attempt, then
# the next one waits this long so a broken DSN cannot turn into a request
# storm.
RETRY_SECONDS = 300.0


def statements(columns=None):
    """The ALTER statements for every managed column (or the ones named).

    `popup_banner_active` is first in the full list so the log line and any
    manual repair show exactly the statement the owner asked for.
    """
    if columns is None:
        names = list(SITE_SETTINGS_COLUMNS)
    elif isinstance(columns, str):
        names = [columns]
    else:
        names = list(columns)
    out = []
    for column in names:
        ddl = SITE_SETTINGS_COLUMNS.get(column)
        if ddl:
            out.append(f"alter table site_settings add column if not exists {column} {ddl}")
    # A full boot pass also heals the products columns. A targeted heal (an
    # Admin save naming the one column it needs) does not: it must stay a
    # single-statement, minimal intervention.
    if columns is None:
        out.append(PRODUCT_SCHEMA_STATEMENT)
    return out


def enabled():
    """True when a backend could exist: Supabase configured and not pytest."""
    if Config.ENV == "testing" and os.environ.get("AUTO_MIGRATE_IN_TESTS") != "1":
        return False
    if _dsn():
        return True
    try:
        from supabase_store import enabled as sb_enabled
        return bool(sb_enabled())
    except Exception:
        return False


def _dsn():
    return (os.environ.get("SUPABASE_DB_URL")
            or os.environ.get("DATABASE_URL") or "").strip()


def _log(logger, level, message):
    if logger is not None:
        try:
            getattr(logger, level)(message)
            return
        except Exception:
            pass
    print(f"[auto-migrate] {message}")


def _apply_with_psycopg(dsn, sql_list):
    """Run the statements over ONE short-lived Postgres connection.

    The boot pass is the only direct-Postgres consumer in the app (every other
    read/write goes through PostgREST, which pools server-side). It therefore
    opens exactly one connection, never keeps it, and bounds it three ways so a
    slow or unreachable database can never hold the worker open:

      * connect_timeout - a hanging TCP handshake;
      * a per-statement ``statement_timeout`` - a lock waiting behind a long
        transaction on the Supabase side;
      * an overall ``application_name`` marker, so the connection is
        identifiable in the Supabase dashboard / pg_stat_activity.

    No pool is created on purpose: pooling a once-per-boot DDL pass would leak
    idle connections that count against the free-tier connection limit.
    """
    try:
        import psycopg  # psycopg 3
        connect = lambda: psycopg.connect(
            dsn, connect_timeout=8,
            options="-c statement_timeout=15000 -c application_name=jaurastore-boot")
        execute = lambda conn, sql: conn.execute(sql)
    except ImportError:
        try:
            import psycopg2  # psycopg 2
            connect = lambda: psycopg2.connect(
                dsn, connect_timeout=8,
                options="-c statement_timeout=15000 -c application_name=jaurastore-boot")
            execute = lambda conn, sql: conn.cursor().execute(sql)
        except ImportError:
            return False, "psycopg is not installed"
    try:
        conn = connect()
    except Exception as exc:
        return False, f"Postgres connection failed: {exc}"
    try:
        for sql in sql_list:
            execute(conn, sql)
        conn.commit()
        return True, ""
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f"statement failed: {exc}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _apply_with_psql(dsn, sql_list):
    import shutil
    if not shutil.which("psql"):
        return False, "psql is not installed"
    for sql in sql_list:
        try:
            subprocess.run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-c", sql],
                           check=True, capture_output=True, timeout=30)
        except Exception as exc:
            return False, f"psql failed: {exc}"
    return True, ""


def _apply_with_rpc(sql_list):
    """Use an exec_sql-style RPC when the project defines one."""
    try:
        from supabase_store import client, enabled as sb_enabled
    except Exception:
        return False, "supabase client unavailable"
    if not sb_enabled():
        return False, "Supabase is not configured"
    c = client()
    if c is None:
        return False, "Supabase is not configured"
    last = ""
    for name in RPC_NAMES:
        try:
            for sql in sql_list:
                c.rpc(name, {"sql": sql}).execute()
            return True, ""
        except Exception as exc:
            last = str(exc)
            continue
    return False, f"no exec_sql RPC ({last[:160]})"


def run(logger=None, columns=None, force=False):
    """Apply the schema statements once per process. Never raises.

    Returns ``{"applied": bool, "backend": str, "error": str, "statements":
    [...]}``. ``applied`` is True only when every statement reached the
    database (or when everything was already handled by an earlier call).
    """
    global _ran, _backend, _last_error, _last_attempt
    sql_list = statements(columns)
    result = {"applied": _ran, "backend": _backend, "error": _last_error,
              "statements": sql_list}
    if not sql_list:
        return result
    if not enabled():
        result["error"] = "no database backend configured"
        return result
    with _lock:
        if _ran and not force:
            result.update(applied=True, backend=_backend, error="")
            return result
        now = time.monotonic()
        if (not force and _last_error and _last_attempt
                and now - _last_attempt < RETRY_SECONDS):
            result["error"] = _last_error
            return result
        _last_attempt = now
        dsn = _dsn()
        attempts = []
        if dsn:
            attempts.append(("psycopg", _apply_with_psycopg(dsn, sql_list)))
            if dsn.startswith(("postgres://", "postgresql://")):
                attempts.append(("psql", _apply_with_psql(dsn, sql_list)))
        attempts.append(("rpc", _apply_with_rpc(sql_list)))
        for name, (ok, error) in attempts:
            if ok:
                _ran, _backend, _last_error = True, name, ""
                _log(logger, "info",
                     "site_settings schema is up to date (%s): %s"
                     % (name, sql_list[0]))
                return {"applied": True, "backend": name, "error": "",
                        "statements": sql_list}
            _log(logger, "info", "auto-migration backend %s unavailable: %s"
                 % (name, error))
        _last_error = attempts[-1][1] if attempts else "no backend"
        _log(logger, "warning",
             "site_settings auto-migration could not run (%s). Run this once "
             "in the Supabase SQL editor: %s" % (_last_error, sql_list[0]))
        return {"applied": False, "backend": "", "error": _last_error,
                "statements": sql_list}


def ensure_site_settings_columns(columns=None, logger=None):
    """Targeted heal used by an Admin save that hit a missing column.

    Answers True when the columns are known to exist now (or the migration
    was already applied this process), False when no backend could run it.
    """
    outcome = run(logger=logger, columns=columns, force=True)
    return bool(outcome.get("applied"))


def reset_for_tests():
    """Clear the once-per-process guard (tests only)."""
    global _ran, _backend, _last_error, _last_attempt
    with _lock:
        _ran, _backend, _last_error, _last_attempt = False, "", "", 0.0
