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
import os, json, re
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


def ping():
    """Reachability of the products table.

    Returns one of:
      - ``ok``             configured and a cheap read succeeded
      - ``unreachable``    configured but the client/read failed
      - ``not_configured`` no URL / service-role key
    """
    if not enabled():
        return "not_configured"
    try:
        c = client()
        if c is None:
            return "unreachable"
        c.table("products").select("id").limit(1).execute()
        return "ok"
    except Exception as exc:
        print(f"[supabase] ping failed: {exc}")
        return "unreachable"


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
# Rows whose `source` marks them as a tombstone (a soft delete or a superseded
# bulk import) are not live products. Everything else is: 'admin' (saved from
# the admin portal), 'seed' (imported by migrate_supabase.py) and rows created
# directly in the Supabase dashboard (source empty / anything else). Filtering
# to `source = 'admin'` only ever saw a subset of the catalogue, which capped
# the shop at fewer products than the database actually holds.
DEAD_SOURCES = ("deleted", "replaced")

# Rows are fetched page by page so the shop always sees the whole table.
# PostgREST applies a server-side max-rows limit (Supabase defaults to 1000)
# and silently truncates oversized responses, so a single unbounded select
# would quietly cap the catalogue. A page size well under that limit, a
# deterministic order, and the exact row count (used to detect a truncation
# and retry with a smaller window) keep every row coming back exactly once.
PAGE_SIZE = 500
MAX_ROWS = 100_000        # a sane ceiling against a runaway loop


def _res_data(res):
    """The data list off a PostgREST response, whatever shape it comes in."""
    if hasattr(res, "data"):
        return res.data or []
    return (res or {}).get("data") or []


def _fetch_product_pages(c):
    """Yield every live product row from the products table, page by page.

    Orders by id so a page boundary can never shift between requests (without
    a deterministic order, rows can repeat or vanish across .range() pages).
    The first response also carries the exact table count (count="exact"): if
    a page comes back short but the count says rows remain, the server capped
    the response below our page size, so the window shrinks to what the
    server will actually send and the walk continues to the very last row.
    """
    start = 0
    page_size = PAGE_SIZE
    total = None
    collected = 0
    while collected < MAX_ROWS:
        res = (c.table("products").select("*", count="exact")
               .order("id")
               .range(start, start + page_size - 1)
               .execute())
        rows = _res_data(res)
        if total is None:
            try:
                total = getattr(res, "count", None)
            except Exception:
                total = None
        if not rows:
            return
        for r in rows:
            source = str((r or {}).get("source") or "").strip().lower()
            if source in DEAD_SOURCES:
                continue                     # a tombstone, not a live product
            yield r
        got = len(rows)
        collected += got
        start += got
        if got < page_size:
            # Short page: either the table ended, or the server truncated the
            # response. Only stop when the count agrees everything is fetched.
            if total is None or collected >= int(total or 0):
                return
            page_size = max(1, got)          # adapt to the server's row cap


def products_table_rows():
    """Every live product from the Supabase products table, or None.

    None means 'not configured / unreachable', which the catalogue falls back
    to the local override file. Returns an empty list only when the table is
    genuinely reachable but empty.

    Duplicates are impossible on the way out: rows are keyed by id (a table
    without a primary key could theoretically return the same row twice), and
    the same piece appearing under two ids is reconciled by slug/sku in
    catalog.merged().
    """
    c = client()
    if c is None:
        return None
    try:
        seen_ids = set()
        rows = []
        for r in _fetch_product_pages(c):
            pid = str((r or {}).get("id") or "").strip()
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            rows.append(r)
        # Reconcile each row's image to a path the browser can display (a
        # committed repo file when present, else the branded placeholder).
        # No third-party / Wix photo is ever referenced.
        from catalog import resolve_image
        return [resolve_image(r) for r in rows]
    except Exception as exc:
        print(f"[supabase] products read failed: {exc}")
        return None


# The products table was hand-built and is NARROWER than the row the app
# writes, so a full-row upsert used to be rejected outright: PostgREST answers
# PGRST204 "Could not find the 'compareCfa' column of 'products' in the schema
# cache" and EVERY product save died on it (the message only names the first
# missing column - more lurk behind it). Reads kept working, so the shop looked
# healthy while saves landed server-local-only and were wiped by the next
# deploy, invisible to the other phone.
#
# The helper below writes the row the table can actually accept: try the full
# row, and on that exact "missing column" error drop the named column and
# retry - bounded, one drop per column. The five columns a product cannot be
# sold without are never dropped: if the table rejects one of those, the write
# fails loudly so the caller reports mirrored=False instead of quietly
# losing the product.
_CRITICAL_PRODUCT_COLUMNS = frozenset(
    {"id", "name", "priceCfa", "priceNgn", "stock"})
_MISSING_COLUMN_RE = re.compile(r"Could not find the '([^']+)' column")


def _upsert_products_resilient(rows):
    pending = [dict(r) for r in (rows or []) if r]
    if not pending:
        return False
    c = client()
    if c is None:
        return False
    dropped = []
    for _ in range(len(pending[0]) + 1):
        try:
            c.table("products").upsert(pending).execute()
            if dropped:
                print("[supabase] products upsert: stored without columns "
                      f"{sorted(set(dropped))} (table lacks them)")
            return True
        except Exception as exc:
            match = _MISSING_COLUMN_RE.search(str(exc))
            col = match.group(1) if match else ""
            if not col or col in _CRITICAL_PRODUCT_COLUMNS:
                print(f"[supabase] products upsert failed: {exc}")
                return False
            if all(col not in r for r in pending):
                print(f"[supabase] products upsert failed: {exc}")
                return False
            dropped.append(col)
            for r in pending:
                r.pop(col, None)
    print("[supabase] products upsert failed: too many missing columns")
    return False


def upsert_products(products):
    """Mirror admin product writes into Supabase. Never blocks a sale.

    Returns True when the write succeeded or when Supabase is not configured
    (nothing to mirror). Returns False when Supabase is enabled but the
    client is missing or the write failed — callers must surface that.
    """
    if not products:
        return True
    if not enabled():
        return True
    if client() is None:
        return False           # configured but unreachable: the caller must know
    rows = []
    for p in products:
        if not p:
            continue
        from catalog import resolve_image
        r = resolve_image(dict(p))
        r.setdefault("source", "admin")
        r.setdefault("updated_at", _now())
        rows.append(r)
    if not rows:
        return True
    return _upsert_products_resilient(rows)


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
    """Replace the admin product set in Supabase (bulk import).

    The bulk write goes through the same resilient path as a single save, so a
    narrower products table no longer wipes the whole catalogue mirror: the
    tombstone pass still runs, and the import lands with whatever columns the
    table actually has.
    """
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
    except Exception as exc:
        print(f"[supabase] products replace failed: {exc}")
        return
    if rows:
        _upsert_products_resilient(rows)


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
# `engine` is a legacy argument (the SQLite handle the caller used to pass
# for a read-back that no longer happens). It MUST stay optional: api.py's
# checkout calls this with the order row alone, and a required-but-unpassed
# parameter raised TypeError inside the route - after the order was already
# written to SQLite - so every live checkout answered 500, the confirmation
# e-mail and the purchase event were skipped, and the order never reached
# the orders table.
def create_order(order, engine=None):
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


def delete_receipt(receipt_id=None, order_id=None, file_url=""):
    """Remove a mirrored payment receipt from Supabase. Best effort."""
    c = client()
    if c is None:
        return
    try:
        q = c.table("receipts").delete()
        if receipt_id is not None:
            q = q.eq("id", receipt_id)
        elif file_url:
            q = q.eq("file_url", file_url)
        elif order_id:
            q = q.eq("order_id", order_id)
        else:
            return
        q.execute()
    except Exception as exc:
        print(f"[supabase] receipt delete failed: {exc}")


# ------------------------------------------------------------------ orders
def _bucket():
    """Storage bucket the uploaded files live in (see .env.example)."""
    return os.environ.get("SUPABASE_BUCKET", "uploads").strip() or "uploads"


def _storage_path_from_url(url, bucket=None):
    """The bucket-relative object path inside one of our URLs, or ''.

    Accepts every URL shape the app can hold: a public object URL
    (…/storage/v1/object/public/<bucket>/<path>), a signed URL
    (…/storage/v1/object/sign/<bucket>/<path>?token=…), or a bare path such
    as "/uploads/receipts/abc.jpg". A foreign URL (someone else's host)
    yields '' - it is left alone.
    """
    url = (url or "").strip()
    if not url:
        return ""
    bucket = bucket or _bucket()
    path = ""
    public = "/object/public/%s/" % bucket
    signed = "/object/sign/%s/" % bucket
    if public in url:
        path = url.split(public, 1)[1]
    elif signed in url:
        path = url.split(signed, 1)[1]
    elif "/object/" in url:
        tail = url.split("/object/", 1)[1]
        parts = tail.split("/", 1)
        path = parts[1] if len(parts) == 2 else ""
    elif url.startswith("http"):
        return ""                         # not ours; nothing to do
    else:
        path = url.lstrip("/")
        if path.startswith("uploads/"):
            path = path[len("uploads/"):]
    path = path.split("?", 1)[0].split("#", 1)[0]
    if urllib and urllib.parse:
        path = urllib.parse.unquote(path)
    return path


def _delete_storage_object_from_url(url, bucket=None):
    """Remove an uploaded file from Supabase Storage, best effort.

    Accepts every URL shape the app can hold (see _storage_path_from_url).
    A foreign URL (someone else's host) is left alone. Never raises: a
    deleted order must never be blocked by an unreachable bucket, because
    the SQLite row is going anyway.
    """
    path = _storage_path_from_url(url, bucket)
    if not path:
        return False
    bucket = bucket or _bucket()
    c = client()
    if c is None:
        return False
    try:
        # remove() returns the objects that were actually removed; an empty
        # list (object already gone) is "nothing to delete", not a success
        res = c.storage.from_(bucket).remove([path])
        return bool(res) if isinstance(res, (list, tuple)) else True
    except Exception as exc:
        print(f"[supabase] storage delete failed: {exc}")
        return False


def delete_order(order_id):
    """Delete an order for good — receipts, uploaded files and the order row.

    This is what stops a deleted order from coming back: the admin portal
    deletes from SQLite, and the next boot restores everything Supabase still
    holds, so without deleting the mirrored rows the order (and its receipt)
    resurrected itself a few minutes later.
    """
    c = client()
    if c is None:
        return False
    order_id = str(order_id or "").strip().upper()
    if not order_id:
        return False

    urls = []
    try:
        res = (c.table("receipts").select("id, file_url, proof_url")
               .eq("order_id", order_id).execute())
        for row in _res_data(res):
            for key in ("file_url", "proof_url"):
                u = str((row or {}).get(key) or "").strip()
                if u:
                    urls.append(u)
    except Exception as exc:
        print(f"[supabase] receipt lookup failed: {exc}")
    try:
        res = c.table("orders").select("proof_url").eq("id", order_id).execute()
        for row in _res_data(res):
            u = str((row or {}).get("proof_url") or "").strip()
            if u:
                urls.append(u)
    except Exception as exc:
        print(f"[supabase] order lookup failed: {exc}")

    for u in urls:
        _delete_storage_object_from_url(u)

    try:
        c.table("receipts").delete().eq("order_id", order_id).execute()
    except Exception as exc:
        print(f"[supabase] receipts delete failed: {exc}")
    try:
        c.table("orders").delete().eq("id", order_id).execute()
    except Exception as exc:
        print(f"[supabase] order delete failed: {exc}")
        return False
    return True


# ------------------------------------------------------------------ helpers
def _now():
    import datetime
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


# ---------------------------------------------------------- growth mirroring
# Referral codes, their usage log, coupons and the growth settings are the
# shop's marketing memory. SQLite stays the working copy; every write is
# mirrored into Supabase (PostgreSQL) so the data also survives outside the
# Render disk. Table DDL lives in supabase_schema.sql (committed to the repo).
def mirror_referral_code(row):
    """Upsert one referral_codes row: {code, email, name, uses,
    reward_issued, reward_coupon, created_at}."""
    c = client()
    if c is None:
        return
    try:
        c.table("referral_codes").upsert(dict(row)).execute()
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] referral upsert failed: {exc}")


def mirror_referral_use(row):
    """Append one referral_uses row: {code, order_id, buyer_email, at}."""
    c = client()
    if c is None:
        return
    try:
        c.table("referral_uses").insert(dict(row)).execute()
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] referral use insert failed: {exc}")


def mirror_coupon(row):
    """Upsert one coupons row: {code, percent, kind, email, note, active,
    max_uses, uses, expires_at, created_at}."""
    c = client()
    if c is None:
        return
    try:
        c.table("coupons").upsert(dict(row)).execute()
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] coupon upsert failed: {exc}")


def mirror_growth_settings(settings_dict):
    """Upsert the whole growth_settings key/value map."""
    c = client()
    if c is None:
        return
    try:
        rows = [{"key": k, "value": str(v)} for k, v in dict(settings_dict).items()]
        c.table("growth_settings").upsert(rows).execute()
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] growth settings upsert failed: {exc}")


# The category table lives in data/categories.json on disk. A Render redeploy
# wipes that file, so we also keep a JSON copy in growth_settings (no new
# schema). CATEGORIES_KEY / save_categories / load_categories are the only
# names the rest of the app should use — there is no replace_categories.
CATEGORIES_KEY = "categories_json"


def save_categories(categories):
    """Persist the category table as one growth_settings row. Never raises."""
    c = client()
    if c is None:
        return False
    try:
        payload = json.dumps(categories, ensure_ascii=False)
        c.table("growth_settings").upsert(
            [{"key": CATEGORIES_KEY, "value": payload}]
        ).execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] categories save failed: {exc}")
        return False


def load_categories():
    """Return the category list stored under CATEGORIES_KEY, or None."""
    c = client()
    if c is None:
        return None
    try:
        res = (c.table("growth_settings")
               .select("value")
               .eq("key", CATEGORIES_KEY)
               .limit(1)
               .execute())
        rows = _res_data(res)
        if not rows:
            return None
        raw = (rows[0] or {}).get("value")
        if raw is None or raw == "":
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, dict) and isinstance(data.get("categories"), list):
            data = data["categories"]
        if isinstance(data, list) and data:
            return data
        return None
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] categories load failed: {exc}")
        return None


def load_orders(limit=500):
    """Return the order list stored in Supabase orders table, or []. Never raises."""
    c = client()
    if c is None:
        return []
    try:
        res = (c.table("orders")
               .select("*")
               .order("updated_at", desc=True)
               .limit(limit)
               .execute())
        data = _res_data(res)
        return data or []
    except Exception as exc:
        print(f"[supabase] load_orders failed: {exc}")
        return []


def load_receipts(limit=500):
    """Return the receipt list stored in Supabase receipts table, or []. Never raises."""
    c = client()
    if c is None:
        return []
    try:
        res = (c.table("receipts")
               .select("*")
               .order("created_at", desc=True)
               .limit(limit)
               .execute())
        data = _res_data(res)
        return data or []
    except Exception as exc:
        print(f"[supabase] load_receipts failed: {exc}")
        return []


# The variant_stock table (per-variant stock levels from the admin's Stock
# panel) exists only in the SQLite database on the Render disk, which a
# redeploy wipes. Like the category table, we keep a JSON copy in
# growth_settings (no new schema): admin_stock_set mirrors the whole table
# after every change, and the boot restore in app.py writes it back before
# the first request is served.
VARIANT_STOCK_KEY = "variant_stock_json"


def save_variant_stock(rows):
    """Persist the whole variant_stock table as one growth_settings row. Never raises."""
    c = client()
    if c is None:
        return False
    try:
        payload = json.dumps(list(rows or []), ensure_ascii=False)
        c.table("growth_settings").upsert(
            [{"key": VARIANT_STOCK_KEY, "value": payload}]
        ).execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] variant stock save failed: {exc}")
        return False


def load_variant_stock():
    """Return the variant_stock rows stored under VARIANT_STOCK_KEY, or None."""
    c = client()
    if c is None:
        return None
    try:
        res = (c.table("growth_settings")
               .select("value")
               .eq("key", VARIANT_STOCK_KEY)
               .limit(1)
               .execute())
        rows = _res_data(res)
        if not rows:
            return None
        raw = (rows[0] or {}).get("value")
        if raw is None or raw == "":
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, list) and data:
            return data
        return None
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] variant stock load failed: {exc}")
        return None


# ------------------------------------------------------------------ growth
# The growth module (referral codes, coupons, the owner-configured referral
# settings and product reviews) keeps its working copy in SQLite on the
# Render disk. Every write is already mirrored into Supabase (see the
# mirror_* functions); these loaders are the other half - the boot restore
# in app.py writes the mirrored rows back so a redeploy that wipes the disk
# does not reset the referral settings the owner configured, and does not
# lose issued referral codes, coupons or customer reviews.
def load_growth_settings():
    """Return the growth_settings key/value map from Supabase, or None."""
    c = client()
    if c is None:
        return None
    try:
        res = c.table("growth_settings").select("key, value").execute()
        rows = _res_data(res)
        out = {str(r.get("key")): str(r.get("value") or "")
               for r in rows if r.get("key")}
        return out or None
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] growth settings load failed: {exc}")
        return None


def load_coupons():
    """Return the coupon rows from the Supabase coupons table, or []. Never raises."""
    c = client()
    if c is None:
        return []
    try:
        res = c.table("coupons").select("*").limit(1000).execute()
        return _res_data(res) or []
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] load_coupons failed: {exc}")
        return []


def load_referral_codes():
    """Return the referral code rows from Supabase, or []. Never raises."""
    c = client()
    if c is None:
        return []
    try:
        res = c.table("referral_codes").select("*").limit(5000).execute()
        return _res_data(res) or []
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] load_referral_codes failed: {exc}")
        return []


# Product reviews have no dedicated Supabase table (the committed schema was
# applied to the live project without one), so - like the category table and
# the variant stock - they ride in growth_settings as one JSON row.
PRODUCT_REVIEWS_KEY = "product_reviews_json"


def save_product_reviews(rows):
    """Persist the whole product_reviews table as one growth_settings row. Never raises."""
    c = client()
    if c is None:
        return False
    try:
        payload = json.dumps(list(rows or []), ensure_ascii=False)
        c.table("growth_settings").upsert(
            [{"key": PRODUCT_REVIEWS_KEY, "value": payload}]
        ).execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] product reviews save failed: {exc}")
        return False


def load_product_reviews():
    """Return the product reviews stored under PRODUCT_REVIEWS_KEY, or None."""
    c = client()
    if c is None:
        return None
    try:
        res = (c.table("growth_settings")
               .select("value")
               .eq("key", PRODUCT_REVIEWS_KEY)
               .limit(1)
               .execute())
        rows = _res_data(res)
        if not rows:
            return None
        raw = (rows[0] or {}).get("value")
        if raw is None or raw == "":
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, list) and data:
            return data
        return None
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] product reviews load failed: {exc}")
        return None
