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
        return res.data or []
    except Exception as exc:
        print(f"[supabase] products read failed: {exc}")
        return None


def upsert_products(products):
    """Mirror admin product writes into Supabase. Never blocks a sale."""
    c = client()
    if c is None or not products:
        return []
    rows = [dict(p) for p in products if p]
    for r in rows:
        r.setdefault("source", "admin")
        r.setdefault("updated_at", _now())
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
