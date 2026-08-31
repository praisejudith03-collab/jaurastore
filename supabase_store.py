"""Supabase gateway shared by catalog.py / auth.py (and the order/cart layer).

Everything here is a safe no-op when Supabase is not configured, so the app
keeps working on a fresh checkout and during tests without any credentials.

The Flask app talks to Supabase server-side (service role key) - the browser
never sees the key and never talks to Supabase directly. This keeps the
existing JSON API contract and the storefront untouched.

Needed environment variables (see .env.example):
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY      (server only, never in the browser)
  SUPABASE_ANON_KEY              (reserved; not required for these calls)
"""
import os, json
from config import Config
try:
    import urllib.parse
except ImportError:             # pragma: no cover
    urllib = None
    urllib.parse = None

_client = None
_loaded = False


def enabled():
    return bool(Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY)


def client():
    """Return a cached supabase client, or None when not configured.

    Imported lazily so the package is only required on hosts that use
    Supabase (it is a heavier dependency than the rest of the stack).
    """
    global _client, _loaded
    if _loaded:
        return _client
    _loaded = True
    if not enabled():
        _client = None
        return None
    try:
        from supabase import create_client
        _client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SERVICE_ROLE_KEY)
    except Exception as exc:                     # never crash the shop
        print(f"[supabase] client unavailable: {exc}")
        _client = None
    return _client


# ------------------------------------------------------------------ products
def products_table_rows():
    """Admin products from the Supabase products table, or None.

    None means 'not configured / unreachable', which the catalogue falls back
    to the local override file. Returns an empty list only when the table is
    genuinely reachable but empty.
    """
    c = client()
    if c is None:
        return None
    try:
        res = c.table("products").select("*").eq("source", "admin").execute()
        rows = res.data or []
        # Reconcile each row's image to a path the browser can display (a
        # committed repo file when present, else the branded placeholder).
        # No third-party / Wix photo is ever referenced.
        from catalog import resolve_image
        return [resolve_image(r) for r in rows]
    except Exception as exc:
        print(f"[supabase] products read failed: {exc}")
        return None


def upsert_products(products):
    """Mirror admin product writes into Supabase. Never blocks a sale."""
    c = client()
    if c is None or not products:
        return []
    rows = []
    for p in products:
        if not p:
            continue
        from catalog import resolve_image
        r = resolve_image(dict(p))
        r.setdefault("source", "admin")
        r.setdefault("updated_at", _now())
        rows.append(r)
    try:
        c.table("products").upsert(rows).execute()
    except Exception as exc:
        print(f"[supabase] products upsert failed: {exc}")
    return rows


def delete_products(ids):
    """Soft-remove admin products in Supabase (via deleted flag)."""
    c = client()
    if c is None or not ids:
        return
    try:
        c.table("products").update({"source": "deleted"}).in_("id", list(ids)).execute()
    except Exception as exc:
        print(f"[supabase] products delete failed: {exc}")


def replace_all_products(products):
    """Replace the admin product set in Supabase (bulk import)."""
    c = client()
    if c is None:
        return
    rows = []
    for p in products:
        r = dict(p)
        r.setdefault("source", "admin")
        r.setdefault("updated_at", _now())
        rows.append(r)
    try:
        c.table("products").update({"source": "replaced"}).eq("source", "admin").execute()
        if rows:
            c.table("products").upsert(rows).execute()
    except Exception as exc:
        print(f"[supabase] products replace failed: {exc}")


# ------------------------------------------------------------------ auth
def supabase_verify_login(email, password):
    """Verify an admin email + password against Supabase Auth (GoTrue)."""
    c = client()
    if c is None:
        return False
    try:
        res = c.auth.sign_in_with_password({"email": email, "password": password})
        user = res.user if hasattr(res, "user") else (res or {}).get("user")
        return bool(user)
    except Exception:
        return False


def supabase_set_shared_password(password):
    """Set the SAME password on every admin Supabase Auth account.

    The shop uses one shared admin password across all admin emails, so when
    Supabase Auth is the login backend we must update every admin user there -
    not just the local mirror. Best effort: a Supabase failure is logged and
    swallowed so a password change never blocks the admin.
    """
    if not enabled():
        return False
    import urllib.request, urllib.error
    base = Config.SUPABASE_URL
    key = Config.SUPABASE_SERVICE_ROLE_KEY
    ok_total = False
    for email in Config.ADMIN_EMAILS:
        try:
            # Resolve the user id by looking up the email (admin API).
            req = urllib.request.Request(
                base + "/auth/v1/admin/users?filter=email%20eq%20" +
                urllib.parse.quote(str(email)),
                headers={"apikey": key, "Authorization": "Bearer " + key})
            with urllib.request.urlopen(req, timeout=15) as resp:
                users = json.loads(resp.read().decode("utf-8", "replace"))
            usr = (users.get("users") or [{}])[0]
            uid = usr.get("id")
            if not uid:
                continue
            body = json.dumps({"password": password}).encode("utf-8")
            req2 = urllib.request.Request(
                base + "/auth/v1/admin/users/" + urllib.parse.quote(str(uid)),
                data=body, method="PUT",
                headers={"apikey": key, "Authorization": "Bearer " + key,
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                resp2.read()
            ok_total = True
        except Exception as exc:
            print(f"[supabase] password update failed for {email}: {exc}")
    return ok_total


# ------------------------------------------------------------------ orders
def create_order(order, engine):
    """Persist a completed checkout into Supabase.

    The SQLite row is still written (the shop's local copy and the source the
    tests and admin portal use); this mirrors it into Supabase so orders also
    live there. Never blocks the sale on a Supabase failure.
    """
    c = client()
    if c is None:
        return
    row = dict(order)
    row["payload"] = json.dumps(order.get("payload", order), ensure_ascii=False)
    row["updated_at"] = _now()
    try:
        c.table("orders").upsert(row).execute()
    except Exception as exc:
        print(f"[supabase] order upsert failed: {exc}")


def update_order(order_id, status=None, payload=None):
    """Mirror an order status / payload change into Supabase."""
    c = client()
    if c is None:
        return
    row = {}
    if status is not None:
        row["status"] = status
    if payload is not None:
        row["payload"] = json.dumps(payload, ensure_ascii=False)
    row["updated_at"] = _now()
    if not row:
        return
    try:
        c.table("orders").update(row).eq("id", order_id).execute()
    except Exception as exc:
        print(f"[supabase] order update failed: {exc}")


def create_receipt(receipt):
    """Mirror a payment-proof submission into Supabase."""
    c = client()
    if c is None:
        return
    row = dict(receipt)
    row["created_at"] = _now()
    try:
        c.table("receipts").upsert(row).execute()
    except Exception as exc:
        print(f"[supabase] receipt upsert failed: {exc}")


# ------------------------------------------------------------------ helpers
def _now():
    import datetime
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
