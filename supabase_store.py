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
import copy as _copy
import threading as _threading
import time as _time
from config import Config
try:
    import urllib.parse
except ImportError:             # pragma: no cover
    urllib = None
    urllib.parse = None

_client = None
_loaded = False
_probe_client = None
_probe_loaded = False

# Monotonic timestamp of the last SHOP read. Used by memory_manager to decide
# when the connection pools have gone idle. The health probe deliberately does
# not touch this: a 15-minute monitor ping must not be the reason an otherwise
# idle instance keeps its pools open.
_last_activity = _time.monotonic()
_activity_lock = _threading.Lock()

# Every PostgREST request is bounded. The supabase package's own default is
# 120 seconds, which is unusable twice over: gunicorn kills the worker at 45s
# (Procfile --timeout 45), so a request still waiting at 120s can never
# complete; and on /healthz it pinned the probe thread for up to two minutes,
# during which the probe guard answered "previous database probe is still
# pending" to every single request - one stalled read became a sustained
# health-check outage instead of a single slow one.
#
# Two budgets, because the two consumers have very different deadlines:
#
#   HEALTH_PROBE_TIMEOUT_SECONDS - the /healthz ping, under the 3.75s health
#       deadline (health_checks.DATABASE_PING_TIMEOUT_SECONDS, Render 5s).
#   SUPABASE_HTTP_TIMEOUT_SECONDS - the shop's own reads, which legitimately
#       take seconds on a cold free-tier database, so they get real headroom
#       while still staying under the gunicorn worker timeout.
#
# Only the PostgREST timeout is set here. Storage uploads move multi-megabyte
# photos and function calls have their own shorter bound, so both keep the
# library defaults.
HEALTH_PROBE_TIMEOUT_SECONDS = 3.0
SUPABASE_HTTP_TIMEOUT_SECONDS = 20.0


# ---------------------------------------------------------------- read cache
# One Render web service, ONE gunicorn worker (threads share this process):
# a single /api/catalog answer runs merged() several times (the route, then
# meta(), then homepage_featured()), and every merged() used to walk the
# products table, the tombstone ids AND the deleted-ids key as three
# SEQUENTIAL PostgREST roundtrips - ~11 network calls for one page's data.
# On a cold free-tier database that is the slow "shop takes seconds to open".
#
# A short in-process TTL cache collapses the repeats to one read per TTL
# window. Staleness safety, by construction:
#   * every write that can change any of these reads goes through THIS module
#     and calls invalidate_read_cache() immediately (admin product save,
#     delete, bulk replace, tombstones, homepage featured, stock RPCs), and
#   * with the single-worker deployment there is no second process that could
#     keep answering from its own stale copy after a write, and
#   * pytest (Config.ENV == "testing") never caches, so test isolation and
#     the live-Postgres pgserver suites are completely untouched, and
#   * only SUCCESSFUL reads are remembered: a Supabase blip is never cached
#     as "empty shop".
# The 10s TTL only bounds out-of-band edits (someone hand-editing rows in the
# Supabase dashboard), which the app never performs.
_READ_TTL_SECONDS = 10.0
_read_cache_lock = _threading.Lock()
_read_cache = {}


def _read_cache_enabled():
    # Never cache under pytest (a test may fake Config.ENV == "production";
    # the module-level cache would otherwise leak one test's mock-client rows
    # into the next). This mirrors the pytest guard in catalog._sync_repo_async.
    try:
        import sys
        if "pytest" in sys.modules:
            return False
        return Config.ENV != "testing"
    except Exception:
        return True


def cached_read(key, fetch):
    """Run ``fetch()`` or reuse its result for the TTL window.

    The cached value is deep-copied on the way out so a caller mutating the
    answer can never poison the next reader (merged()/resolve_image produce
    per-call copies, and callers rely on that contract).
    """
    if not _read_cache_enabled():
        return fetch()
    now = _time.monotonic()
    with _read_cache_lock:
        hit = _read_cache.get(key)
    if hit and (now - hit[0]) < _READ_TTL_SECONDS:
        return _copy.deepcopy(hit[1])
    value = fetch()
    if value is not None:
        with _read_cache_lock:
            _read_cache[key] = (_time.monotonic(), _copy.deepcopy(value))
    return value


def invalidate_read_cache(*keys):
    """Drop cached reads. Called by every write path in this module."""
    with _read_cache_lock:
        if keys:
            for key in keys:
                _read_cache.pop(key, None)
        else:
            _read_cache.clear()


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
        # The probe client, not the shop's: this read must stay inside the
        # health-check deadline. See probe_client().
        c = probe_client()
        if c is None:
            return "unreachable"
        c.table("products").select("id").limit(1).execute()
        return "ok"
    except Exception as exc:
        print(f"[supabase] ping failed: {exc}")
        return "unreachable"


def _client_options(timeout):
    """Client options bounding PostgREST HTTP calls to ``timeout``, or None.

    Returns None when the installed supabase package does not expose the
    options object this version expects, so an older or newer client still
    builds with its own defaults instead of the shop losing its database
    outright over a health-check hardening detail.
    """
    try:
        from supabase.lib.client_options import SyncClientOptions
    except Exception:
        return None
    try:
        return SyncClientOptions(postgrest_client_timeout=timeout)
    except Exception as exc:
        print(f"[supabase] client options unavailable: {exc}")
        return None


def _build_client(timeout):
    """Create a client whose PostgREST calls are bounded, or None."""
    if not enabled():
        return None
    try:
        from supabase import create_client
        # options=None is the package's own default, so falling back to it
        # keeps behaviour unchanged on a client whose options differ.
        return create_client(Config.SUPABASE_URL, Config.SUPABASE_SERVICE_ROLE_KEY,
                             _client_options(timeout))
    except Exception as exc:                     # never crash the shop
        print(f"[supabase] client unavailable: {exc}")
        return None


def client():
    """Return a cached supabase client, or None when not configured.

    Imported lazily so the package is only required on hosts that use
    Supabase (it is a heavier dependency than the rest of the stack).
    """
    global _client, _loaded, _last_activity
    with _activity_lock:
        _last_activity = _time.monotonic()
    if _loaded:
        return _client
    _loaded = True
    _client = _build_client(SUPABASE_HTTP_TIMEOUT_SECONDS)
    return _client


def last_activity():
    """Monotonic time of the last shop-side Supabase access."""
    with _activity_lock:
        return _last_activity


def idle_seconds(now=None):
    now = _time.monotonic() if now is None else float(now)
    return max(0.0, now - last_activity())


def _close_client(c):
    """Close every httpx pool a supabase client holds, best-effort."""
    closed = 0
    if c is None:
        return closed
    # postgrest/functions/storage each own a SyncClient with a ``session``;
    # auth keeps its own httpx client under ``_http_client``.
    for attr in ("postgrest", "functions", "storage", "auth"):
        sub = getattr(c, attr, None)
        if sub is None:
            continue
        for holder in (sub, getattr(sub, "session", None),
                       getattr(sub, "_http_client", None)):
            closer = getattr(holder, "close", None) if holder is not None else None
            if not callable(closer):
                continue
            try:
                closer()
                closed += 1
            except Exception:
                pass
    return closed


def recycle_clients(reason="idle"):
    """Close and forget both cached clients (the shop's and the probe's).

    A Supabase client is a thin PostgREST wrapper over a long-lived httpx
    connection pool. On a free instance that never sleeps, those pools - and
    the read cache beside them - are the only structures that outlive a
    request by design. Dropping them when the shop is idle returns the file
    descriptors and the pool memory, and the next read rebuilds a client in a
    few milliseconds.

    Returns ``{"recycled": bool, "closed": n, "reason": reason}``. Safe to call
    concurrently: the swap is done under a lock, and a client already handed
    to an in-flight request keeps working (closing its pool only makes the
    *next* call on it reconnect) because supabase-py creates connections per
    request.
    """
    global _client, _loaded, _probe_client, _probe_loaded
    global _last_activity
    with _activity_lock:
        shop, probe = _client, _probe_client
        had = bool(_loaded or _probe_loaded or shop or probe)
        _client, _probe_client = None, None
        _loaded, _probe_loaded = False, False
        _last_activity = _time.monotonic()
    closed = _close_client(shop) + _close_client(probe)
    invalidate_read_cache()
    return {"recycled": bool(had), "closed": closed, "reason": str(reason or "")}


def probe_client():
    """A separate client for the /healthz ping, on the health-check budget.

    Deliberately NOT the shop's own client. The ping has to answer inside the
    3.75s health deadline while a cold free-tier catalogue read legitimately
    takes seconds, so sharing one client would mean either the health check
    inherits the shop's generous budget and pins the probe guard (the outage
    this exists to prevent), or the shop's reads inherit 3s and start failing
    on a cold start. Two cached clients, each on its own deadline.
    """
    global _probe_client, _probe_loaded
    if _probe_loaded:
        return _probe_client
    _probe_loaded = True
    _probe_client = _build_client(HEALTH_PROBE_TIMEOUT_SECONDS)
    return _probe_client


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


def _page(builder, limit, offset=0):
    """Apply a bounded page window to a PostgREST query builder.

    `range()` is the paginated form; a builder (or a test double) that only
    knows `limit()` still gets a bounded, never-unbounded query.
    """
    limit = max(1, int(limit or 1))
    offset = max(0, int(offset or 0))
    if offset and hasattr(builder, "range"):
        return builder.range(offset, offset + limit - 1)
    if hasattr(builder, "limit"):
        return builder.limit(limit)
    if hasattr(builder, "range"):
        return builder.range(offset, offset + limit - 1)
    return builder


def _fetch_product_pages(c, include_dead=False):
    """Yield product rows from the products table, page by page.

    Orders by id so a page boundary can never shift between requests (without
    a deterministic order, rows can repeat or vanish across .range() pages).
    The first response also carries the exact table count (count="exact"): if
    a page comes back short but the count says rows remain, the server capped
    the response below our page size, so the window shrinks to what the
    server will actually send and the walk continues to the very last row.

    Rows whose ``source`` marks a tombstone (soft delete / superseded import)
    are skipped unless ``include_dead`` is set - the tombstone-id walker
    needs them even though no live catalogue may contain them.
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
                if (isinstance(r, dict)
                        and r.get("is_active") is True
                        and r.get("is_deleted") is False):
                    r = dict(r, source="admin")
                else:
                    if include_dead:
                        yield r
                    continue                 # a tombstone, not a live product
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
    the same piece appearing under two ids (a re-created product) is
    reconciled by id, or by a slug/sku clash confirmed by the same name, in
    catalog.merged().
    """
    def _fetch():
        try:
            c = client()
        except Exception as exc:
            print(f"[supabase] products read failed: {exc}")
            return None
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
                rows.append(_canonicalize_product(r, c))
            # Reconcile each row's image to a path the browser can display (a
            # committed repo file when present, else the branded placeholder).
            # No third-party / Wix photo is ever referenced.
            from catalog import resolve_image
            return [resolve_image(r) for r in rows]
        except Exception as exc:
            print(f"[supabase] products read failed: {exc}")
            return None

    return cached_read("products_rows", _fetch)


def dead_product_ids_table():
    """Ids of products-table rows tombstoned (source in DEAD_SOURCES), or None.

    A soft delete writes source="deleted" on the products-table row itself;
    catalog.merged() folds these ids into the deleted set so a deleted seed
    product stays gone even when the growth_settings tombstone write failed
    (the legacy-table failure that used to resurrect products after every
    redeploy). None means 'could not read', which callers treat as an empty
    set so an outage neither resurrects a product nor empties the shop.
    """
    def _fetch():
        try:
            c = client()
        except Exception as exc:
            print(f"[supabase] dead product ids read failed: {exc}")
            return None
        if c is None:
            return None
        try:
            ids = set()
            for r in _fetch_product_pages(c, include_dead=True):
                source = str((r or {}).get("source") or "").strip().lower()
                if source not in DEAD_SOURCES:
                    continue
                pid = str((r or {}).get("id") or "").strip()
                if pid:
                    ids.add(pid)
            return ids
        except Exception as exc:
            print(f"[supabase] dead product ids read failed: {exc}")
            return None

    return cached_read("dead_product_ids", _fetch)


def product_by_id(pid, strict=False):
    """One live product row from Supabase, dict-shaped, or None.

    Looks the id up as a primary key first, then as a `legacyId` alias, so an
    old wix-* product link or order line still resolves after the row has been
    given a canonical jau-* id.

    Never raises by default: an unreachable Supabase returns None and the
    caller decides how to surface that (checkout must not fall back to a stale
    local price). ``strict=True`` is for the admin save paths, where "the row
    is gone" and "the database did not answer" are DIFFERENT answers - telling
    the owner a product no longer exists because a read timed out sent them
    re-uploading a photo that was never lost.
    """
    c = client()
    if c is None:
        if strict:
            raise RuntimeError("Supabase is not configured")
        return None
    key = str(pid or "").strip()
    if not key:
        return None
    for column in ("id", "legacyId"):
        try:
            res = (c.table("products").select("*")
                   .eq(column, key).limit(1).execute())
        except Exception as exc:
            # The legacyId column may not exist yet on an un-migrated table;
            # that must not break the primary-key lookup that already ran.
            if column == "legacyId":
                return None
            if strict:
                raise
            print(f"[supabase] product read failed for {pid!r}: {exc}")
            return None
        rows = _res_data(res)
        if rows:
            row = _canonicalize_product(rows[0], c)
            from catalog import resolve_image
            return resolve_image(row)
    return None


# Canonical column names the acceptance spec requires (image_url,
# stock_quantity) plus the legacy aliases the storefront/overrides still use
# (image, stock). The row returned to callers always carries BOTH, so a
# live Supabase row survives every consumer untouched.
_PRODUCT_ALIASES = (
    ("image_url", "image"),
    ("stock_quantity", "stock"),
)


def _fold_french_aliases(p):
    """Fold snake_case French copy onto the camelCase keys the app reads.

    The production products table grew BOTH a quoted "nameFr" column and an
    unquoted name_fr one - the schema comments call the latter dead legacy
    junk and forbid dropping it, but live rows still carry it. Which spelling
    a row answers with therefore depends on how it was written, and a French
    description stored under description_fr would read back as missing, so the
    shop would serve English while everything around it was French.

    camelCase always wins; the snake_case value is only used when the
    camelCase one is absent or blank, so an owner's translation is never
    overwritten by a stale column.
    """
    for camel, snake in (("nameFr", "name_fr"), ("descriptionFr", "description_fr")):
        have = p.get(camel)
        if have is None or not str(have).strip():
            fallback = p.get(snake)
            if fallback is not None and str(fallback).strip():
                p[camel] = fallback
    return p


def _canonicalize_product(row, _c=None):
    p = _fold_french_aliases(dict(row or {}))
    if p.get("image_url") is None and p.get("image") is not None:
        p["image_url"] = p["image"]
    if p.get("image") is None and p.get("image_url") is not None:
        p["image"] = p["image_url"]
    # The canonical column wins by presence, not by truthiness. A NULL/blank
    # stock_quantity is zero stock; reading the legacy `stock` alias in that
    # case could silently resurrect inventory after a bad import.
    if "stock_quantity" not in p:
        p["stock_quantity"] = p.get("stock") if "stock" in p else 0
    try:
        stock_qty = max(0, int(str(p.get("stock_quantity") or "0").strip()))
    except (TypeError, ValueError):
        stock_qty = 0
    p["stock_quantity"] = stock_qty
    # Keep the legacy alias in lock-step for older storefront/mirror readers.
    p["stock"] = stock_qty
    supplier = (p.get("supplierSku") or p.get("supplier_sku") or
                p.get("supplierUrl") or p.get("supplier_url") or "")
    p["supplierSku"] = supplier
    p["supplierUrl"] = supplier
    p["supplier_url"] = supplier
    if p.get("compareNgn") is None:
        p["compareNgn"] = (p.get("compare_ngn") or p.get("compareAtPrice") or
                            p.get("compare_at_price") or p.get("strikeThroughPrice") or
                            p.get("strike_through_price"))
    if p.get("compareCfa") is None:
        p["compareCfa"] = p.get("compare_cfa")
    p["optionPrices"] = (p.get("optionPrices") or p.get("option_prices") or
                          p.get("variantPrices") or p.get("variant_prices") or {})
    p["optionCompareAt"] = (p.get("optionCompareAt") or p.get("option_compare_at") or
                             p.get("variantCompareAt") or p.get("variant_compare_at") or {})
    p["optionSupplierSku"] = (p.get("optionSupplierSku") or p.get("option_supplier_sku") or
                               p.get("optionSupplierUrls") or p.get("option_supplier_urls") or {})
    p["optionSku"] = (p.get("optionSku") or p.get("option_sku") or
                       p.get("optionSkus") or p.get("option_skus") or {})
    # Supplier costs (owner/reference data): product-level Naira figure plus
    # the per-option map. Read through the same aliases the writers use so a
    # mirror row or an import never silently loses them.
    p["supplierCost"] = (p.get("supplierCost") if p.get("supplierCost") is not None
                         else p.get("supplier_cost") if p.get("supplier_cost") is not None
                         else p.get("supplierPrice") if p.get("supplierPrice") is not None
                         else p.get("supplier_price"))
    p["optionSupplierCost"] = (p.get("optionSupplierCost") or p.get("option_supplier_cost") or
                                p.get("optionSupplierCosts") or p.get("option_supplier_costs") or
                                p.get("variantSupplierCosts") or p.get("variant_supplier_costs") or
                                p.get("optionCosts") or p.get("option_costs") or {})
    p["bulkQty"] = (p.get("bulkQty") if p.get("bulkQty") is not None else
                    p.get("bulk_qty") if p.get("bulk_qty") is not None else
                    p.get("bulkQuantity") if p.get("bulkQuantity") is not None else
                    p.get("bulk_quantity") if p.get("bulk_quantity") is not None else
                    p.get("bulkDiscountQty") if p.get("bulkDiscountQty") is not None else
                    p.get("bulk_discount_qty"))
    p["bulkPercent"] = (p.get("bulkPercent") if p.get("bulkPercent") is not None else
                        p.get("bulk_percent") if p.get("bulk_percent") is not None else
                        p.get("bulkDiscountPercent") if p.get("bulkDiscountPercent") is not None else
                        p.get("bulk_discount_percent"))
    if not isinstance(p.get("reviews"), list):
        p["reviews"] = p.get("customerReviews") if isinstance(p.get("customerReviews"), list) else (
            p.get("customer_reviews") if isinstance(p.get("customer_reviews"), list) else [])
    return p


# Every field the admin editor can collect must either reach PostgreSQL or the
# save must fail loudly. A retry that drops a "non-critical" column is data
# loss: after reload the owner sees blanks/defaults even though the portal said
# "saved". Keep this list broad and explicit so schema drift blocks the save
# instead of silently discarding supplier URLs, variant prices/SKUs, reviews,
# bulk tiers, media, availability or publication flags.
_CRITICAL_PRODUCT_COLUMNS = frozenset({
    "id", "legacyId", "sku", "slug", "name", "nameFr", "descriptionFr",
    "category", "category_id", "supplier_status", "priceCfa", "compareCfa", "priceNgn", "compareNgn",
    "image", "image_url", "images", "description", "stock",
    "stock_quantity", "badge", "featured", "online", "colors", "options",
    "optionStock", "optionPrices", "optionCompareAt", "optionSupplierSku",
    "optionSku", "reviews", "dimensions", "bulkQty", "bulkPercent",
    "placeholderImage", "usesPlaceholder", "source", "supplierId",
    "supplierSku", "updated_at", "enableCustomNote", "customNotePrompt",
    # Variant-level supplier costs: the owner's per-unit sourcing figures.
    # Losing them on a save would silently blank the costing desk, so a
    # missing column blocks the save exactly like a lost price.
    "supplierCost", "optionSupplierCost",
})
_MISSING_COLUMN_RE = re.compile(r"Could not find the '([^']+)' column")


def _enforce_public_catalog_images():
    """Only enforce the production storage contract for real Supabase projects.

    Local test bridges and self-hosted development endpoints deliberately use
    non-Supabase origins. Render's configured project ref is always hosted at
    ``*.supabase.co``; there we fail closed on every new catalog write.
    """
    try:
        host = (urllib.parse.urlsplit((Config.SUPABASE_URL or "").strip()).hostname
                or "").lower()
        return Config.ENV != "testing" and host.endswith(".supabase.co")
    except (TypeError, ValueError):
        return False


def _public_image_url_error(value):
    """Return a reason when a catalog cover is not a durable public object.

    Product/category image_url values written to PostgreSQL must point at this
    configured Supabase project's public ``uploads`` bucket. Empty covers
    remain valid for intentionally image-less records.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        base = urllib.parse.urlsplit((Config.SUPABASE_URL or "").strip())
        parsed = urllib.parse.urlsplit(raw)
        base_host = (base.hostname or "").lower()
        path = urllib.parse.unquote(parsed.path or "")
        if (base.scheme != "https" or not base_host.endswith(".supabase.co")
                or parsed.scheme != "https"
                or (parsed.hostname or "").lower() != base_host
                or parsed.port != base.port
                or parsed.username or parsed.password
                or not path.startswith(f"/storage/v1/object/public/{_bucket()}/")
                or any(part in (".", "..") for part in path.split("/"))):
            return "must be an absolute public Supabase Storage URL for this project"
        if not path.rsplit("/", 1)[-1]:
            return "must include a Storage object key"
    except (TypeError, ValueError):
        return "must be an absolute public Supabase Storage URL for this project"
    return ""


# A cover may also be an asset that ships inside the deployed application.
# catalog.resolve_image() serves these, the storefront renders them and
# catalog_health() counts them as valid covers, so the writer must accept the
# same class it publishes - otherwise every bundled-catalogue product becomes
# unsaveable in production (see the regression notes below).
_COMMITTED_ASSET_RE = re.compile(
    r"^(?:images/)(?!.*(?:^|/)\.\.?(?:/|$))[^\\\x00-\x1f]+"
    r"\.(?:jpe?g|png|webp|gif|avif|heic|svg|mp4|webm|mov|pdf|docx?)$",
    re.IGNORECASE)


def _is_committed_catalog_asset(value):
    """True for a cover that is a repository asset shipped with the app.

    These are the paths the bundled catalogue uses (``images/products/x.jpg``)
    and the last-resort fallback photo repair chooses. They are durable: the
    file is part of the deploy, not an ephemeral Render upload, and no bucket
    or object key is involved.

    The path must ALSO exist on disk right now. A made-up relative path
    (``images/categories/temporary.jpg``) is not an asset - it is exactly the
    kind of dangling reference this validator exists to reject - while a real
    committed file is served by the app for the life of the deploy.
    """
    raw = str(value or "").strip()
    if not raw:
        return False
    if raw.startswith(("/", "\\")) or "\\" in raw:
        return False
    if re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.IGNORECASE):
        return False                    # http:, data:, blob:, file: ...
    if not _COMMITTED_ASSET_RE.match(raw.split("?", 1)[0]):
        return False
    try:
        import storage
        return bool(storage._is_repo_asset(raw.split("?", 1)[0]))
    except Exception:                                # pragma: no cover
        return False


def _canonical_catalog_image_url(value):
    """Return an absolute public URL, migrating a legacy /uploads alias in place.

    Relative upload links are accepted only if the object is verified in the
    configured Supabase bucket; they are immediately rewritten to its absolute
    URL so no new database reference depends on the Render filesystem.

    A committed repository asset (``images/products/x.jpg``) is accepted
    unchanged. It is already durable and same-origin, and refusing it used to
    make every bundled-catalogue product unsaveable on the live shop: an admin
    Hide/Show toggle posts the whole product, so the write was refused with
    "image_url must be an absolute public Supabase Storage URL" and the portal
    answered 503 ("could not be saved") for a row that was perfectly valid.
    """
    raw = str(value or "").strip()
    if not raw:
        return "", ""
    if "_placeholder" in raw:
        return "", ""       # display fallback, not a catalog object
    if _is_committed_catalog_asset(raw):
        return raw, ""
    if not _public_image_url_error(raw):
        return raw, ""
    path = raw.split("?", 1)[0]
    if path.startswith("/uploads/"):
        key = path[len("/uploads/"):]
    elif path.startswith("uploads/"):
        key = path[len("uploads/"):]
    else:
        return "", _public_image_url_error(raw)
    if (not key or key.startswith("/") or "\\" in key
            or any(part in (".", "..") for part in key.split("/"))
            or key.split("/", 1)[0] not in {"products", "categories", "misc"}):
        return "", "legacy upload path is not a public catalog object"
    try:
        import storage
        if storage.object_exists(raw) is True:
            absolute = storage.supabase_public_url(key)
            if absolute and not _public_image_url_error(absolute):
                return absolute, ""
    except Exception:
        pass
    return "", "relative upload path is not verified in Supabase Storage; re-upload the image"


# The reason the most recent product/category write was refused. Every refusal
# used to be a print() and a bare `False` upstream, so the Admin portal could
# only say "could not be saved" with no trace of WHY - the single most
# expensive part of diagnosing the disappearing-image reports. The API now
# returns this alongside the 503 and writes it to the audit log.
_LAST_WRITE_BLOCKER = ""


def _note_write_blocker(reason):
    global _LAST_WRITE_BLOCKER
    _LAST_WRITE_BLOCKER = str(reason or "")[:300]
    return False


def last_write_blocker():
    """Why the last product/category write was refused ("" when none was)."""
    return _LAST_WRITE_BLOCKER


def clear_write_blocker():
    global _LAST_WRITE_BLOCKER
    _LAST_WRITE_BLOCKER = ""


def _upsert_products_resilient(rows):
    pending = [dict(r) for r in (rows or []) if r]
    if not pending:
        return False
    global _LAST_WRITE_BLOCKER
    _LAST_WRITE_BLOCKER = ""
    for row in pending:
        image_url = row.get("image_url") or row.get("image") or ""
        if _enforce_public_catalog_images():
            image_url, reason = _canonical_catalog_image_url(image_url)
            if reason:
                print(f"[supabase] products upsert refused: image_url {reason}; "
                      "upload the image to Supabase Storage before saving.")
                return _note_write_blocker(
                    f"product {row.get('id') or '(no id)'}: cover {reason}")
            gallery = []
            for item in row.get("images") or []:
                canonical, image_error = _canonical_catalog_image_url(item)
                if image_error:
                    # A stale/missing gallery member is not a durable reference.
                    # Omit it rather than re-saving an ephemeral URL; the
                    # cover remains independently validated above. Committed
                    # repo photos and absolute bucket URLs have no error and
                    # are therefore KEPT - dropping them silently emptied the
                    # gallery on every save (the "my extra photos vanished"
                    # report).
                    continue
                if canonical and canonical not in gallery:
                    gallery.append(canonical)
            row["images"] = gallery
        row["image_url"] = image_url
        row["image"] = image_url
    c = client()
    if c is None:
        return _note_write_blocker("the Supabase client is not configured")
    try:
        c.table("products").upsert(pending).execute()
        return True
    except Exception as exc:
        match = _MISSING_COLUMN_RE.search(str(exc))
        col = match.group(1) if match else ""
        if col:
            _warn_missing_product_columns([col])
            print("[supabase] products upsert refused: products table is missing "
                  f"column {col!r}; no retry was attempted because that would "
                  "silently discard admin-entered data. Run supabase_schema.sql.")
            return _note_write_blocker(
                f"the products table is missing the column {col!r}; "
                "run supabase_schema.sql")
        else:
            print(f"[supabase] products upsert failed: {exc}")
        return _note_write_blocker(f"database write failed: {str(exc)[:200]}")


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
        # ALWAYS a fresh timestamp. A save is an update: carrying the row's
        # previous value forward made a later Hide look like a stale background
        # write to the catalogue write-lock, which then reverted it.
        r["updated_at"] = _now()
        # A save through the live catalogue IS a live product. Restoring a
        # trashed piece runs through here, so the soft-delete markers its old
        # row carried (source='deleted', is_deleted=true) must be cleared in
        # the same write - otherwise the revived row stays hidden from every
        # reader that honours the flag. catalog.upsert has already refused
        # permanently-removed ids before this point, so clearing the SOFT
        # markers can never resurrect a hard delete.
        r["is_deleted"] = False
        if str(r.get("source") or "").strip().lower() in ("deleted", "replaced"):
            r["source"] = "admin"
        rows.append(r)
    if not rows:
        return True
    ok = _upsert_products_resilient(rows)
    # A confirmed (or ambiguous) write must never leave the read cache serving
    # the pre-save row: the very next /api/catalog has to carry the new photo,
    # price and stock the admin just saved.
    invalidate_read_cache()
    return ok


# Logical edit fields from the Admin form to the canonical products columns.
# This powers the hot edit path: a description/stock/note save updates only
# touched columns, preserving every concurrent value without a catalogue read.
_PRODUCT_PATCH_COLUMNS = {
    "name": {"name"}, "nameFr": {"nameFr"}, "description": {"description"},
    "descriptionFr": {"descriptionFr"}, "sku": {"sku"}, "category": {"category"}, "category_id": {"category_id"},
    "online": {"online"}, "featured": {"featured"}, "badge": {"badge"},
    "dimensions": {"dimensions"}, "enableCustomNote": {"enableCustomNote"},
    "customNotePrompt": {"customNotePrompt"}, "priceNgn": {"priceNgn", "priceCfa"},
    "priceCfa": {"priceCfa"}, "compareNgn": {"compareNgn", "compareCfa"},
    "compareCfa": {"compareCfa"}, "stock": {"stock", "stock_quantity"},
    "stock_quantity": {"stock", "stock_quantity"},
    "stockStatus": {"stock", "stock_quantity", "optionStock"},
    "optionStock": {"optionStock", "stock", "stock_quantity"},
    "options": {"options"}, "colors": {"colors"},
    "optionPrices": {"optionPrices"}, "optionCompareAt": {"optionCompareAt"},
    "supplierSku": {"supplierSku"}, "supplierUrl": {"supplierSku"},
    "supplier_url": {"supplierSku"}, "bulkQty": {"bulkQty"},
    "bulkPercent": {"bulkPercent"}, "placeholderImage": {"placeholderImage"},
    "usesPlaceholder": {"usesPlaceholder"}, "supplierId": {"supplierId"},
    "optionSupplierSku": {"optionSupplierSku"},
    "optionSupplierUrls": {"optionSupplierSku"},
    "option_supplier_urls": {"optionSupplierSku"}, "optionSku": {"optionSku"},
    "optionSkus": {"optionSku"}, "option_sku": {"optionSku"},
    "variantSku": {"optionSku"}, "reviews": {"reviews"},
    "supplierCost": {"supplierCost"}, "supplier_cost": {"supplierCost"},
    "supplierPrice": {"supplierCost"}, "supplier_price": {"supplierCost"},
    "optionSupplierCost": {"optionSupplierCost"},
    "option_supplier_cost": {"optionSupplierCost"},
    "optionSupplierCosts": {"optionSupplierCost"},
    "option_supplier_costs": {"optionSupplierCost"},
    "variantSupplierCosts": {"optionSupplierCost"},
    "variant_supplier_costs": {"optionSupplierCost"},
    "optionCosts": {"optionSupplierCost"}, "option_costs": {"optionSupplierCost"},
}


def update_product_fields(product, dirty_fields):
    """Patch one existing product and return its saved row in one DB request.

    The response is ``("updated", row)``, ``("missing", None)`` when a
    seed-only row has not yet been materialized, ``("permanently-removed",
    None)`` when the SQL tombstone guard wins, or ``("error", None)`` for a
    real persistence/schema failure. Callers may full-upsert only the missing
    seed-row case; other failures must remain visible to the Admin.
    """
    row = dict(product or {})
    pid = str(row.get("id") or "").strip()
    c = client()
    if not pid or c is None:
        return "error", None
    columns = {"source", "updated_at"}
    for field in dirty_fields or ():
        columns.update(_PRODUCT_PATCH_COLUMNS.get(str(field), ()))
    # Media never uses the fast path (it has a separate before/after cleanup
    # contract), and an empty patch should not silently acknowledge a write.
    patch = {key: row[key] for key in columns if key in row}
    patch["source"] = "admin"
    if not patch.get("updated_at"):
        patch["updated_at"] = _now()
    if len(patch) <= 2:
        return "error", None
    # A targeted `online: false` patch is exactly what the catalogue write-lock
    # reverts, because it cannot prove an admin asked for it. Send the flag
    # through the guarded RPC instead, then persist the rest of the columns.
    hide = patch.get("online") is False
    if hide:
        patch.pop("online", None)
        if len(patch) <= 2:
            if not set_product_online([pid], False):
                return "error", None
            row = product_by_id(pid)
            return ("updated", row) if row else ("missing", None)
    try:
        result = (c.table("products").update(patch).eq("id", pid)
                  .select("*").execute())
        rows = _res_data(result)
        if not rows:
            return "missing", None
        if hide:
            if not set_product_online([pid], False):
                return "error", None
            # The update above was read back before the flag moved; report the
            # row the way it now exists so the portal's "Hidden" tag is true.
            rows[0] = dict(rows[0] or {}, online=False, is_active=False)
        invalidate_read_cache()
        return "updated", _canonicalize_product(rows[0], c)
    except Exception as exc:
        message = str(exc)
        if "permanently deleted" in message.lower():
            return "permanently-removed", None
        missing_column = _MISSING_COLUMN_RE.search(message)
        if missing_column:
            _warn_missing_product_columns([missing_column.group(1)])
        print(f"[supabase] product field update failed for {pid!r}: {exc}")
        invalidate_read_cache()
        return "error", None


def delete_products(ids):
    """Soft-remove admin products in Supabase (via deleted flag)."""
    c = client()
    if c is None or not ids:
        return
    try:
        c.table("products").update({"source": "deleted"}).in_("id", list(ids)).execute()
        invalidate_read_cache()
    except Exception as exc:
        print(f"[supabase] products delete failed: {exc}")


def delete_products_strict(ids):
    """Soft-remove admin products in Supabase; True only on success.

    The admin Delete button needs to know whether the deletion actually
    reached PostgreSQL - a best-effort mirror must never let the portal
    report "deleted" while Supabase still serves the product.

    The tombstone is TWO markers on the row: the legacy ``source='deleted'``
    flag every reader already understood, and an explicit ``is_deleted=true``
    boolean. ``catalog.is_deleted_product`` treats both the same way, so a
    trashed row is hidden from the storefront, search, sitemaps and broadcast
    pickers even if a mirror or an older reader only looks at one spelling.
    Media and the row itself are kept so Restore can bring the piece back.
    """
    c = client()
    if c is None or not ids:
        return False
    ids = [str(i) for i in ids if str(i or "").strip()]
    if not ids:
        return False
    # Prefer the guarded RPC: it sets the transaction-scoped admin flag that
    # the catalogue write-lock (recover_unavailable_products.sql) requires, and
    # it logs the action in product_guard_events. A one-column REST update of
    # `is_deleted` is exactly the shape the lock reverts, by design.
    try:
        c.rpc("admin_trash_products", {"p_ids": ids}).execute()
        invalidate_read_cache()
        return True
    except Exception as exc:
        if not _function_missing(exc):
            print(f"[supabase] admin_trash_products failed: {exc}")
            return False
        # The migration has not been applied on this database, so the lock is
        # not installed either: the legacy REST write is still safe.
    try:
        c.table("products").update({"source": "deleted",
                                    "is_deleted": True,
                                    "is_active": False,
                                    "updated_at": _now()}).in_("id", ids).execute()
        invalidate_read_cache()
        return True
    except Exception as exc:
        # A legacy products table may not have the is_deleted / is_active
        # columns yet. The source='deleted' marker alone still hides the row
        # from every reader, so the trash must not fail.
        match = _MISSING_COLUMN_RE.search(str(exc))
        if match and match.group(1) in ("is_deleted", "is_active"):
            try:
                c.table("products").update({"source": "deleted"}).in_(
                    "id", ids).execute()
                invalidate_read_cache()
                return True
            except Exception as exc2:
                print(f"[supabase] products delete failed: {exc2}")
                return False
        print(f"[supabase] products delete failed: {exc}")
        return False


def _function_missing(exc):
    """True when PostgREST says the RPC itself does not exist (PGRST202).

    Only that error may fall back to a plain REST write. Any other failure -
    permission denied, the write-lock rejecting the statement, a dropped
    connection - must stay visible to the caller instead of being retried
    through a weaker path.
    """
    message = str(exc or "")
    if "PGRST202" in message or "404" in message and "function" in message.lower():
        return True
    lowered = message.lower()
    # A client double / an older supabase-py without .rpc() means the same
    # thing as a missing function: this deployment has no guarded RPC, so the
    # legacy REST path is the only one available.
    if "has no attribute 'rpc'" in lowered or "object has no attribute" in lowered:
        return True
    return ("could not find the function" in lowered
            or "function public." in lowered and "does not exist" in lowered)


def set_product_online(ids, online):
    """Hide / show products through the guarded RPC. True only on success.

    Used by the admin editor's fast patch path. A plain REST update of the
    `online` column is reverted by the catalogue write-lock, so the column
    must travel through the RPC that carries the admin intent.
    """
    c = client()
    ids = [str(i) for i in (ids or []) if str(i or "").strip()]
    if c is None or not ids:
        return False
    try:
        c.rpc("admin_set_product_online", {"p_ids": ids,
                                           "p_online": bool(online)}).execute()
        invalidate_read_cache()
        return True
    except Exception as exc:
        if not _function_missing(exc):
            print(f"[supabase] admin_set_product_online failed: {exc}")
            return False
    try:
        c.table("products").update({"online": bool(online),
                                    "is_active": bool(online),
                                    "updated_at": _now()}).in_("id", ids).execute()
        invalidate_read_cache()
        return True
    except Exception as exc:
        print(f"[supabase] product availability update failed: {exc}")
        return False


def restore_products(ids, reason="owner recovery"):
    """Restore trashed / unavailable rows through the guarded RPC."""
    c = client()
    ids = [str(i) for i in (ids or []) if str(i or "").strip()]
    if c is None or not ids:
        return False
    try:
        c.rpc("admin_restore_products", {"p_ids": ids}).execute()
        invalidate_read_cache()
        return True
    except Exception as exc:
        if not _function_missing(exc):
            print(f"[supabase] admin_restore_products failed: {exc}")
            return False
    try:
        c.table("products").update({"source": "admin", "is_deleted": False,
                                    "is_active": True, "online": True,
                                    "updated_at": _now()}).in_("id", ids).execute()
        invalidate_read_cache()
        return True
    except Exception as exc:
        print(f"[supabase] product restore failed: {exc}")
        return False


def _unavailable_rows():
    """Rows currently hidden from the shop, as raw products-table dicts.

    Returns None when the table cannot be read at all. Never raises: the
    recovery endpoint reports an unreachable database as its own error instead
    of pretending there was nothing to restore.
    """
    c = client()
    if c is None:
        return None
    try:
        rows = []
        for query in ("is_deleted.eq.true", "is_active.eq.false", "online.eq.false",
                      "source.eq.deleted", "source.eq.replaced"):
            try:
                res = c.table("products").select("*").or_(query).execute()
            except Exception:
                # A column this database does not have yet: skip that filter
                # rather than losing the other two.
                continue
            rows.extend(_res_data(res) or [])
    except Exception as exc:
        print(f"[supabase] unavailable product read failed: {exc}")
        return None
    seen, unique = set(), []
    for row in rows:
        pid = str((row or {}).get("id") or "").strip()
        if pid and pid not in seen:
            seen.add(pid)
            unique.append(row)
    return unique


def recover_unavailable_products(ids=None, reason="owner recovery"):
    """Bring every soft-deleted / unavailable product back to the shop.

    One call, idempotent, auditable. Prefers the SQL function so the write-lock
    and the audit ledger stay in charge; falls back to the REST path only when
    that function has not been installed. Rows in the permanent
    ``deleted_products`` ledger are never resurrected.
    """
    wanted = [str(i).strip() for i in (ids or []) if str(i or "").strip()]
    c = client()
    report = {"ok": False, "restored": 0, "restoredIds": [],
              "skippedPermanent": 0, "repairedImages": 0,
              "prunedDeletedIds": 0, "uncategorized": 0, "reason": reason}
    if c is None:
        report["error"] = "Supabase is not configured."
        return report
    # Always prefer the SQL function - with ids or without - so the write-lock
    # context, the audit ledger and the legacy-tombstone pruning all run in one
    # transaction. The REST path below is only for a database that has not had
    # the migration applied yet.
    try:
        params = {"p_reason": reason}
        if wanted:
            params["p_ids"] = wanted
        res = c.rpc("recover_unavailable_products", params).execute()
        data = _res_data(res)
        payload = data[0] if isinstance(data, list) and data else data
        if isinstance(payload, dict) and payload.get("ok"):
            report.update({k: payload.get(k, report[k]) for k in list(report)})
            report["ok"] = True
            report["method"] = "rpc"
            invalidate_read_cache()
            return report
    except Exception as exc:
        if not _function_missing(exc):
            report["error"] = str(exc)[:240]
            return report

    # REST fallback (migration not applied). Restores only what the read
    # returns, prunes the durable tombstone list per id, and never touches a
    # hard-deleted id.
    try:
        from catalog import is_permanently_removed
        hard = set(load_hard_deleted_ids() or [])
    except Exception:
        is_permanently_removed = None
        hard = set()
    rows = _unavailable_rows()
    if rows is None:
        report["error"] = "The products table could not be read."
        return report
    restored = []
    for row in rows:
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        if wanted and pid not in wanted:
            continue
        if pid in hard or (is_permanently_removed and is_permanently_removed(row)):
            report["skippedPermanent"] += 1
            continue
        patch = {"is_deleted": False, "is_active": True, "online": True,
                 "updated_at": _now()}
        if str(row.get("source") or "").strip().lower() in ("deleted", "replaced") \
                or not str(row.get("source") or "").strip():
            patch["source"] = "admin"
        if not str(row.get("image_url") or "").strip():
            cover = str(row.get("image") or "").strip()
            gallery = row.get("images")
            if not cover and isinstance(gallery, list) and gallery:
                cover = str(gallery[0] or "").strip()
            if cover:
                patch["image_url"] = cover
                patch["image"] = cover
                report["repairedImages"] += 1
        try:
            c.table("products").update(patch).eq("id", pid).execute()
        except Exception as exc:
            # A legacy table may lack is_active. Retry without the new column
            # instead of leaving the row unavailable.
            if "is_active" in str(exc):
                patch.pop("is_active", None)
                try:
                    c.table("products").update(patch).eq("id", pid).execute()
                except Exception as exc2:
                    print(f"[supabase] recovery failed for {pid!r}: {exc2}")
                    continue
            else:
                print(f"[supabase] recovery failed for {pid!r}: {exc}")
                continue
        restored.append(pid)
        try:
            clear_deleted_id(pid)
            report["prunedDeletedIds"] += 1
        except Exception:
            pass
    report.update({"ok": True, "restored": len(restored),
                   "restoredIds": restored, "method": "rest"})
    invalidate_read_cache()
    return report


def catalog_health():
    """Counts the recovery screen shows before anything is changed.

    ``unavailable`` is the number of rows the shop is currently hiding;
    ``missingCover`` counts rows with no cover at all; ``relativeCover`` counts
    rows whose cover is still a legacy same-origin ``/uploads/`` path instead
    of an absolute public bucket URL.
    """
    rows = products_table_rows()
    health = {"ok": rows is not None, "products": 0, "unavailable": 0,
              "softDeleted": 0, "inactive": 0, "hidden": 0,
              "missingCover": 0, "relativeCover": 0, "nonAbsoluteCover": 0,
              "missingCategory": 0, "writeLock": False, "storageBase": ""}
    base = (Config.SUPABASE_URL or "").rstrip("/")
    health["storageBase"] = f"{base}/storage/v1/object/public/uploads/" if base else ""
    # Is the recovery/write-lock migration installed? A missing function is
    # the answer, not an error: the screen then offers the SQL to run.
    try:
        c = client()
        if c is not None:
            c.rpc("jaura_catalog_admin", {}).execute()
            health["writeLock"] = True
    except Exception:
        health["writeLock"] = False
    if rows is None:
        return health
    health["products"] = len(rows)
    base = (Config.SUPABASE_URL or "").rstrip("/")
    for row in rows:
        row = dict(row or {})
        cover = str(row.get("image_url") or row.get("image") or "").strip()
        deleted = bool(row.get("is_deleted"))
        inactive = row.get("is_active") is False
        hidden = row.get("online") is False
        if deleted:
            health["softDeleted"] += 1
        if inactive:
            health["inactive"] += 1
        if hidden:
            health["hidden"] += 1
        if deleted or inactive or hidden:
            health["unavailable"] += 1
        if not cover:
            health["missingCover"] += 1
        elif cover.startswith(("/uploads/", "uploads/")):
            health["relativeCover"] += 1
        elif cover.startswith(("http://", "https://")):
            if "/storage/v1/object/" in cover and base and not cover.startswith(base + "/"):
                health["nonAbsoluteCover"] += 1
        elif not cover.startswith(("images/", "data:", "blob:")):
            health["nonAbsoluteCover"] += 1
        if not str(row.get("category_id") or row.get("category") or "").strip():
            health["missingCategory"] += 1
    return health


def repair_relative_image_urls():
    """Rewrite legacy `/uploads/<key>` covers to absolute public bucket URLs.

    Returns the number of rows changed. Requires SUPABASE_URL, because the
    absolute address of the bucket is a property of the project, not of the
    row. Never rewrites a value that is already absolute.
    """
    base = (Config.SUPABASE_URL or "").rstrip("/")
    c = client()
    if not base or c is None:
        return 0
    try:
        res = (c.table("products").select("id,image,image_url")
               .or_("image_url.like./uploads/%,image_url.like.uploads/%").execute())
    except Exception as exc:
        print(f"[supabase] relative cover read failed: {exc}")
        return 0
    fixed = 0
    for row in _res_data(res) or []:
        pid = str((row or {}).get("id") or "").strip()
        current = str((row or {}).get("image_url") or "").strip()
        if not pid or not current:
            continue
        key = current[len("/uploads/"):] if current.startswith("/uploads/") else (
            current[len("uploads/"):] if current.startswith("uploads/") else "")
        key = key.lstrip("/")
        if not key:
            continue
        absolute = f"{base}/storage/v1/object/public/uploads/{key}"
        patch = {"image_url": absolute}
        if not str(row.get("image") or "").strip():
            patch["image"] = absolute
        try:
            c.table("products").update(patch).eq("id", pid).execute()
            fixed += 1
        except Exception as exc:
            print(f"[supabase] cover repair failed for {pid!r}: {exc}")
    if fixed:
        invalidate_read_cache()
    return fixed


def _product_media_urls(row):
    """Every stored file a product row points at (photos + videos)."""
    out = []
    row = dict(row or {})
    for key in ("image", "image_url", "imageUrl", "video", "video_url"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    images = row.get("images")
    if isinstance(images, str):
        try:
            images = json.loads(images)
        except Exception:
            images = []
    if isinstance(images, (list, tuple)):
        for value in images:
            if isinstance(value, str) and value.strip():
                out.append(value.strip())
            elif isinstance(value, dict):
                for k in ("url", "src", "image"):
                    if isinstance(value.get(k), str) and value[k].strip():
                        out.append(value[k].strip())
    seen, unique = set(), []
    for value in out:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


# Every table that hangs off a product row and must go with it. `products`
# itself is deleted by the atomic SQL function; the follow-up sweep also clears
# historical orphan rows from legacy tables whose existing data prevented a
# foreign key from being validated. Tables that do not exist are skipped - the
# list is a superset, not a schema manifest.
PRODUCT_CHILD_TABLES = (
    ("product_variants", "product_id"),
    ("product_prices", "product_id"),
    ("product_options", "product_id"),
    ("variant_stock", "product_id"),
    ("product_reviews", "product_id"),
    ("product_views", "product_id"),
    ("featured_products", "product_id"),
)


def _purge_product_children(c, ids):
    """Delete every child row of these products. Returns a list of errors.

    A table that is not part of the schema (PostgREST answers 404 / "relation
    does not exist") is not an error - it is simply not there to purge.
    """
    errors = []
    for table, column in PRODUCT_CHILD_TABLES:
        try:
            c.table(table).delete().in_(column, ids).execute()
        except Exception as exc:
            text = str(exc or "")
            missing = ("does not exist" in text or "not found" in text.lower()
                       or "PGRST205" in text or "404" in text)
            if not missing:
                errors.append(f"{table}: {exc}")
    return errors


def hard_delete_products(ids):
    """PERMANENTLY delete product rows from PostgreSQL and purge their files.

    Unlike ``delete_products`` (which only tombstones ``source="deleted"``)
    this removes the row itself, and every file it referenced in Supabase
    Storage, so the product cannot be resurrected by a mirror pass, a bulk
    re-import or a redeploy. The id is also recorded in the durable
    deleted-ids list, which is what stops the bundled seed copy from being
    served again.

    The deletion and SQL tombstones must be committed together by the
    ``hard_delete_products`` Postgres function (see hard_delete_products.sql).
    A REST delete fallback is deliberately forbidden: it cannot share the
    transaction/advisory lock with a concurrent supplier upsert, so it could
    report success while a stale write recreates the row. If the migration is
    missing or the RPC fails, fail closed and leave the row and its media
    untouched. The follow-up child sweep clears historical orphan rows that
    could not be attached to a foreign key during migration.

    Returns {"deleted": [ids], "files": n, "errors": [str]}. Never raises.
    """
    report = {"deleted": [], "files": 0, "errors": []}
    ids = [str(i or "").strip() for i in (ids or []) if str(i or "").strip()]
    if not ids:
        return report
    c = client()
    if c is None:
        report["errors"].append("supabase not configured")
        return report

    # 1. collect the Storage objects the rows point at while we can still read
    #    them. The bytes are purged after the row is gone so storage.delete_upload
    #    can still protect media shared by another live product.
    urls = []
    for pid in ids:
        try:
            res = c.table("products").select("*").eq("id", pid).execute()
            for row in (getattr(res, "data", None) or []):
                urls.extend(_product_media_urls(row))
        except Exception as exc:
            report["errors"].append(f"read {pid}: {exc}")

    # 2. Delete AND tombstone in one Postgres transaction. A table-by-table
    #    REST fallback is not race-safe: it cannot share the advisory lock with
    #    a supplier write already in flight, so the stale writer could land
    #    after the parent delete. The SQL migration is therefore required.
    try:
        res = c.rpc("hard_delete_products", {"product_ids": ids}).execute()
        raw_deleted = getattr(res, "data", None)
        if isinstance(raw_deleted, dict):
            raw_deleted = raw_deleted.get("hard_delete_products")
        if isinstance(raw_deleted, str):
            raw_deleted = [raw_deleted]
        if not isinstance(raw_deleted, (list, tuple)):
            raise RuntimeError("hard_delete_products returned an invalid result")
        deleted_ids = [str(pid or "").strip() for pid in raw_deleted
                       if str(pid or "").strip()]
    except Exception as exc:
        report["errors"].append(
            "atomic hard delete unavailable or failed; apply/check hard_delete_products.sql: "
            + str(exc))
        return report

    report["deleted"] = deleted_ids
    invalidate_read_cache()

    # 3. Mirror the durable SQL tombstone into the legacy list only after the
    #    atomic RPC succeeds. The SQL ledger is authoritative; this preserves
    #    compatibility with older readers and seed suppression across deploys.
    for pid in ids:
        try:
            if not add_deleted_id(pid):
                report["errors"].append(f"tombstone mirror {pid}: write was not confirmed")
        except Exception as exc:
            report["errors"].append(f"tombstone mirror {pid}: {exc}")

    # 4. Clear historical orphans as well as the rows cascaded by the RPC.
    report["errors"].extend(_purge_product_children(c, ids))

    # 5. purge unreferenced files. storage.delete_upload() refuses to remove an
    #    object still used by another product, but does delete receipts/videos
    #    and already-unlinked product media immediately.
    seen = set()
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            import storage as _storage
            if _storage.delete_upload(url):
                report["files"] += 1
        except Exception as exc:
            report["errors"].append(f"storage: {exc}")
    return report


def replace_all_products(products):
    """Replace the admin product set in Supabase (bulk import).

    Returns True only when the tombstone pass and every replacement row land.
    Bulk import is also zero-data-loss: a missing products column refuses the
    import instead of acknowledging a catalogue with fields removed.
    """
    c = client()
    if c is None:
        return False
    rows = []
    for p in products:
        r = dict(p)
        r.setdefault("source", "admin")
        r.setdefault("updated_at", _now())
        rows.append(r)
    try:
        existing = c.table("products").select("id").eq("source", "admin").execute()
        existing_ids = {str((r or {}).get("id") or "") for r in (_res_data(existing) or [])
                        if str((r or {}).get("id") or "")}
    except Exception as exc:
        print(f"[supabase] products replace read failed: {exc}")
        return False
    if rows and not _upsert_products_resilient(rows):
        return False
    incoming_ids = {str((r or {}).get("id") or "") for r in rows if str((r or {}).get("id") or "")}
    stale = sorted(existing_ids - incoming_ids)
    if stale:
        try:
            c.table("products").update({"source": "replaced"}).in_("id", stale).execute()
        except Exception as exc:
            print(f"[supabase] products replace tombstone failed: {exc}")
            return False
    invalidate_read_cache()
    return True


def reserve_product_stock(product_id, qty, option=None):
    """Atomically reserve ``qty`` of one product in PostgreSQL.

    Uses the ``reserve_product_stock`` RPC (single guarded UPDATE), so two
    concurrent checkouts can never oversell: only one of them gets True.
    When ``option`` names a stored optionStock key, the guard ALSO covers
    that variant's own quantity, and the reservation decrements the variant
    alongside the product total - without it a sold-out variant kept its
    stale number and could be sold again.
    Returns the fresh row (dict) on success, False on failure/None on
    unavailable (Supabase down, out of stock or offline).
    """
    c = client()
    if c is None:
        return False
    try:
        pid = str(product_id or "").strip()
        qty = int(qty or 0)
        opt = str(option or "").strip() or None
        if not pid or qty <= 0:
            return False
        res = c.rpc("reserve_product_stock",
                    {"p_id": pid, "p_qty": qty, "p_option": opt}).execute()
        ok = _res_data(res)
        reserved = bool(ok and (ok[0] if isinstance(ok, list) else ok))
        # A failed guard can mean another shopper just took stock. Invalidate
        # too, so the 409 response and the next public read report that fresh
        # remaining quantity rather than a cached pre-race number.
        invalidate_read_cache("products_rows")
        if not reserved:
            return None          # out of stock / offline product
        # The RPC's successful UPDATE is authoritative. Do not turn a later
        # read-back timeout into a false reservation failure: the caller would
        # miss this line while rolling back earlier ones and leak its units.
        return True
    except Exception as exc:
        print(f"[supabase] reserve_product_stock failed: {exc}")
        return False


def set_supplier_status(product_id, status):
    """Update only the isolated supplier_status flag for an existing product."""
    if status not in ("out_of_stock", "low_stock", "ok"):
        return False
    c = client()
    if c is None:
        return False
    try:
        pid = str(product_id or "").strip()
        if not pid:
            return False
        result = c.rpc("set_supplier_status", {"p_id": pid, "p_status": status}).execute()
        data = _res_data(result)
        invalidate_read_cache("products_rows")
        return bool(data and (data[0] if isinstance(data, list) else data))
    except Exception as exc:
        print(f"[supabase] set supplier status failed: {exc}")
        return False


def apply_supplier_stock(product_id, stock, option_changes=None, allow_increase=False,
                         option_snapshot_keys=None):
    """Patch only supplier-observed inventory on an existing product row.

    The SQL RPC takes the product's advisory lock, merges only changed variant
    keys, and performs UPDATE (never upsert). A deleted/stale product therefore
    cannot be recreated by a watchdog run, and concurrent title/price/image
    edits are untouched. By default, observed counts are also capped at the
    latest database count to avoid restoring a checkout reservation made while
    the supplier page was loading.
    """
    c = client()
    if c is None:
        return False
    try:
        pid = str(product_id or "").strip()
        qty = max(0, int(stock))
        if option_changes is not None and not isinstance(option_changes, dict):
            return False
        changes = option_changes
        if not pid or qty > 10**7:
            return False
        result = c.rpc("sync_supplier_stock", {
            "p_id": pid,
            "p_stock": qty,
            "p_option_stock_changes": changes,
            "p_allow_increase": bool(allow_increase),
            "p_option_snapshot_keys": option_snapshot_keys,
        }).execute()
        data = _res_data(result)
        applied = bool(data and (data[0] if isinstance(data, list) else data))
        invalidate_read_cache("products_rows")
        return applied
    except Exception as exc:
        print(f"[supabase] apply supplier stock failed: {exc}")
        invalidate_read_cache("products_rows")
        return False


def release_product_stock(product_id, qty, option=None):
    """Return reserved stock to a product. Best-effort; never raises.

    ``option`` (a stored optionStock key) restores the variant's quantity
    together with the product total, mirroring reserve_product_stock.
    """
    c = client()
    if c is None:
        return False
    try:
        pid = str(product_id or "").strip()
        qty = int(qty or 0)
        opt = str(option or "").strip() or None
        if not pid or qty <= 0:
            return False
        res = c.rpc("release_product_stock",
                    {"p_id": pid, "p_qty": qty, "p_option": opt}).execute()
        ok = _res_data(res)
        released = bool(ok and (ok[0] if isinstance(ok, list) else ok))
        if released:
            invalidate_read_cache()
        return released
    except Exception as exc:
        print(f"[supabase] release_product_stock failed: {exc}")
        return False


# ------------------------------------------------------------------ categories
# The Supabase `categories` table is the production source of truth (id, name,
# name_fr, image_url, hidden, updated_at). The legacy growth_settings JSON
# mirror is kept only as a boot-time fallback for the test/dev local path.


# --------------------------------------------------------------------------
# The live categories table is legacy and NARROWER than the row the app
# writes (sometimes with extra NOT NULL columns): a plain upsert used to die
# with PGRST204 ("Could not find the 'name_fr' column ...") and every Admin
# category save answered 503 - while the client had already written
# localStorage, so the rename LOOKED saved and then "went back" on reload.
# The write therefore repairs its own payload against the LIVE table, the
# way delivery._upsert_zone_resilient does for delivery_zones:
#   23502 "null value in column X"   -> fill X from the category being saved
#   PGRST204/42703 unknown column X  -> drop X (id and name are never dropped)
#   22P02 wrong type                 -> walk X through 0 -> False -> ""
# The discovered columns AND the constant that finally satisfied a typed
# column are cached per worker, so the next save starts from the repaired
# shape and lands on its first attempt.
# --------------------------------------------------------------------------

_CAT_CRITICAL_COLUMNS = frozenset({"id", "name"})
_CATS_SHAPE = {"fill": [], "drop": [], "values": {}}
_NULL_VALUE_RE = re.compile(r'null value in column "([^"]+)"')
_PG_MISSING_COLUMN_RE = re.compile(r'column "([^"]+)" of relation')
_BOOL_HINT_RE = re.compile(
    r"active|available|require|enabled|visible|flag|hidden|public|confirmed|"
    r"^is_|^has_")
_CAT_TYPED_FILL_CHAIN = (0, False, "")


def _value_for_cat_column(column, cat):
    """Best-guess value for a legacy NOT NULL column the app never writes."""
    c = str(column or "").strip().lower()
    if c in ("name_fr", "label_fr", "title_fr", "fr"):
        return cat.get("name_fr") or cat["name"]
    if c in ("image", "image_url", "photo", "picture", "icon", "thumbnail"):
        return cat.get("image_url") or ""
    if _BOOL_HINT_RE.search(c):
        return bool(cat.get("hidden"))
    if "time" in c or "date" in c:
        return cat.get("updated_at") or _now()
    if c in ("name", "label", "title", "slug", "category", "sort_key"):
        return cat["name"]
    # Unrecognised column: the category name is the least-wrong non-null
    # filler, and a wrong type is corrected by the 22P02 repair below.
    return cat["name"]


def _cat_repair_statement(column, missing=False):
    if missing:
        return f"alter table categories add column if not exists {column} text"
    return f"alter table categories alter column {column} drop not null"


def _cats_unsatisfiable(column, original, missing=False):
    return (f'categories still rejects the save at column "{column}" '
            f"({str(original)[:160]}). One statement repairs the live table: "
            f"{_cat_repair_statement(column, missing)}")


def _remember_cat_shape(kind, column):
    if column and column not in _CATS_SHAPE[kind]:
        _CATS_SHAPE[kind].append(column)


def _upsert_categories_resilient(c, rows):
    """Upsert the category batch, repairing the payload against the live
    table. One repair per attempt, bounded; the table shape is identical for
    every row so each repair is applied to the whole batch. Raises
    RuntimeError naming the offending column and the repair statement when
    the table cannot be satisfied.
    """
    pending = [dict(r) for r in (rows or []) if r]
    if not pending:
        return False
    for row in pending:
        image_url = row.get("image_url") or row.get("image") or ""
        if _enforce_public_catalog_images():
            image_url, reason = _canonical_catalog_image_url(image_url)
            if reason:
                _note_write_blocker(
                    f"category {row.get('id') or '(unknown)'}: cover {reason}")
                raise RuntimeError(
                    f"category {row.get('id') or '(unknown)'} image_url {reason}; "
                    "upload the image to Supabase Storage before saving.")
        row["image_url"] = image_url
    for r in pending:
        for col in _CATS_SHAPE["drop"]:
            if col not in _CAT_CRITICAL_COLUMNS:
                r.pop(col, None)
        for col in _CATS_SHAPE["fill"]:
            r.setdefault(col, _CATS_SHAPE["values"].get(
                col, _value_for_cat_column(col, r)))

    width0 = max(len(pending[0]), 1)
    last_column = None
    last_typed = None
    typed_step = {}
    for _attempt in range(2 * width0 + 8):
        try:
            c.table("categories").upsert(pending).execute()
            if last_typed:
                _CATS_SHAPE["values"][last_typed] = pending[0][last_typed]
            if _CATS_SHAPE["drop"] or _CATS_SHAPE["fill"]:
                print("[supabase] categories upsert: table shape "
                      f"fill={sorted(_CATS_SHAPE['fill'])} "
                      f"drop={sorted(_CATS_SHAPE['drop'])}")
            return True
        except Exception as exc:
            text = str(exc)
            m = _NULL_VALUE_RE.search(text)
            if m and "23502" in text:
                col = m.group(1)
                for r in pending:
                    value = _CATS_SHAPE["values"].get(
                        col, _value_for_cat_column(col, r))
                    r[col] = value
                _remember_cat_shape("fill", col)
                last_column = col
                continue
            m = _MISSING_COLUMN_RE.search(text)
            if m is None and "42703" in text:
                m = _PG_MISSING_COLUMN_RE.search(text)
            if m:
                col = m.group(1)
                image_column_required = (col == "image_url"
                    and _enforce_public_catalog_images())
                if col in _CAT_CRITICAL_COLUMNS or image_column_required or all(
                        col not in r for r in pending):
                    raise RuntimeError(
                        _cats_unsatisfiable(
                            col, text,
                            missing=all(col not in r for r in pending))
                    ) from exc
                for r in pending:
                    r.pop(col, None)
                _remember_cat_shape("drop", col)
                last_column = col
                continue
            if ("22P02" in text or "invalid input syntax" in text) and last_column:
                col = last_column
                step = typed_step.get(col, -1) + 1
                if step >= len(_CAT_TYPED_FILL_CHAIN):
                    raise RuntimeError(_cats_unsatisfiable(col, text)) from exc
                for r in pending:
                    r[col] = _CAT_TYPED_FILL_CHAIN[step]
                typed_step[col] = step
                last_typed = col
                continue
            raise
    raise RuntimeError(
        _cats_unsatisfiable(last_column or next(iter(pending[0])),
                            "too many columns to repair"))


DELETED_CATEGORIES_TABLE = "deleted_categories"


def load_hard_deleted_category_ids():
    """Return immutable category tombstones, or None when the ledger is unreadable."""
    c = client()
    if c is None:
        return None
    try:
        out, offset = set(), 0
        while True:
            res = (c.table(DELETED_CATEGORIES_TABLE).select("category_id")
                   .order("category_id").range(offset, offset + 499).execute())
            batch = _res_data(res)
            for row in batch:
                cid = str((row or {}).get("category_id") or "").strip()
                if cid:
                    out.add(cid)
            if len(batch) < 500:
                break
            offset += len(batch)
        return out
    except Exception as exc:
        print(f"[supabase] deleted category ids load failed: {exc}")
        return None


def hard_delete_categories(ids):
    """Atomically remove category rows and append permanent ID tombstones."""
    clean = _clean_deleted_ids(ids)
    if not clean:
        return True
    c = client()
    if c is None:
        return False
    try:
        result = c.rpc("hard_delete_categories", {"category_ids": clean}).execute()
        confirmed = _res_data(result)
        if isinstance(confirmed, str):
            confirmed = [confirmed]
        received = {str(item).strip() for item in (confirmed or []) if str(item).strip()}
        if not set(clean).issubset(received):
            print("[supabase] category hard delete response did not confirm every requested id")
            return False
        return True
    except Exception as exc:
        print(f"[supabase] category hard delete failed: {exc}")
        return False


def restore_categories_table(categories):
    """Compensate a not-yet-committed category save without adding tombstones."""
    c = client()
    if c is None:
        return False
    try:
        current = load_categories_table()
        if current is None:
            return False
        rows = [dict(row) for row in (categories or []) if isinstance(row, dict)]
        if rows:
            _upsert_categories_resilient(c, rows)
        keep = [str(row.get("id") or "").strip() for row in rows
                if str(row.get("id") or "").strip()]
        if keep:
            c.table("categories").delete().not_.in_("id", keep).execute()
        else:
            c.table("categories").delete().neq("id", "").execute()
        return True
    except Exception as exc:
        print(f"[supabase] category rollback failed: {exc}")
        return False


def save_categories_table(categories, prune=True):
    """Upsert category rows and permanently tombstone ids omitted from a set.

    The schema migration supplies an atomic hard-delete RPC and an insert
    trigger; if either is missing, a destructive replacement fails closed.
    ``prune=False`` is used by the API to save companion details first, then
    commit all removals as the final operation.
    """
    c = client()
    if c is None:
        return False
    previous = None
    if prune:
        previous = load_categories_table()
        if previous is None:
            return False
    rows = []
    for cat in (categories or []):
        if not isinstance(cat, dict):
            continue
        row = {
            "id": str(cat.get("id") or "").strip(),
            "name": str(cat.get("name") or "").strip(),
            "name_fr": str(cat.get("nameFr") or cat.get("name_fr") or "").strip(),
            "image_url": str(cat.get("image_url") or cat.get("image") or "").strip(),
            "hidden": bool(cat.get("hidden")),
            "updated_at": _now(),
        }
        if not row["id"] or not row["name"]:
            continue
        rows.append(row)
    try:
        if rows:
            _upsert_categories_resilient(c, rows)
        if prune:
            keep = {r["id"] for r in rows}
            removed = [str((row or {}).get("id") or "").strip()
                       for row in (previous or [])]
            removed = [pid for pid in removed if pid and pid not in keep]
            if removed and not hard_delete_categories(removed):
                raise RuntimeError(
                    "Category hard-delete/tombstone migration is missing or unavailable")
    except RuntimeError as exc:
        print(f"[supabase] categories save failed: {exc}")
        return False
    except Exception as exc:
        print(f"[supabase] categories prune failed: {exc}")
        return False
    return True


def load_categories_table():
    """The category rows from the Supabase categories table, or None."""
    c = client()
    if c is None:
        return None
    try:
        rows = []
        # PostgREST caps each response. Page by a stable unique key rather
        # than silently hiding categories beyond an arbitrary 500-row limit.
        start = 0
        while True:
            res = (c.table("categories").select("*").order("id")
                   .range(start, start + 499).execute())
            batch = _res_data(res)
            rows.extend(batch)
            if len(batch) < 500:
                break
            start += len(batch)
        if not rows:
            return []
        out = []
        for r in rows:
            out.append({
                "id": str(r.get("id") or "").strip(),
                "name": str(r.get("name") or "").strip(),
                "nameFr": str(r.get("name_fr") or r.get("nameFr") or "").strip(),
                "image": str(r.get("image_url") or r.get("image") or "").strip(),
                "image_url": str(r.get("image_url") or r.get("image") or "").strip(),
                "hidden": bool(r.get("hidden")),
            })
        return out
    except Exception as exc:
        print(f"[supabase] categories load failed: {exc}")
        return None


# ------------------------------------------------------------------ orders
# `engine` is a legacy argument (the SQLite handle the caller used to pass
# for a read-back that no longer happens). It MUST stay optional: api.py's
# checkout calls this with the order row alone, and a required-but-unpassed
# parameter raised TypeError inside the route - after the order was already
# written to SQLite - so every live checkout answered 500 and the purchase event was skipped;
# the order never reached the orders table.
# Columns a completed sale cannot be recorded without. Everything else (a
# newer optional flag like proof_upload_failed on an older, narrower orders
# table) may be dropped so the SALE is never lost to a schema mismatch - the
# same resilience the products upsert already has. The full order is always
# preserved inside the payload JSON regardless.
_CRITICAL_ORDER_COLUMNS = frozenset(
    {"id", "total", "currency", "status", "payload", "at"})


def _upsert_order_resilient(row, strict):
    """Upsert one order row, dropping only non-critical unknown columns.

    Returns True on success. When `strict` is False a total failure is
    swallowed (best-effort mirror); when True the caller surfaces it.
    """
    c = client()
    if c is None:
        return False
    pending = dict(row)
    for _ in range(len(pending) + 1):
        try:
            c.table("orders").upsert(pending).execute()
            return True
        except Exception as exc:
            match = _MISSING_COLUMN_RE.search(str(exc))
            col = match.group(1) if match else ""
            if not col or col in _CRITICAL_ORDER_COLUMNS or col not in pending:
                print(f"[supabase] order upsert failed: {exc}")
                return False
            # A non-critical column the table does not have: drop and retry so
            # the sale still lands. The dropped value survives in payload JSON.
            pending.pop(col, None)
            print(f"[supabase] order upsert: retrying without missing column '{col}'")
    print("[supabase] order upsert failed: too many missing columns")
    return False


def create_order(order, engine=None):
    """Persist a completed checkout into Supabase (best-effort mirror)."""
    c = client()
    if c is None:
        return
    row = dict(order)
    row["payload"] = json.dumps(order.get("payload", order), ensure_ascii=False)
    row["updated_at"] = _now()
    _upsert_order_resilient(row, strict=False)


def create_order_strict(order):
    """Persist a completed checkout into Supabase; True only on success.

    This is the production write path: Supabase PostgreSQL is the record of
    the sale and a failure is surfaced, never swallowed. A missing OPTIONAL
    column (e.g. proof_upload_failed on an older table) never fails the sale -
    only a genuine write failure or a missing CRITICAL column does.
    """
    c = client()
    if c is None:
        return False
    row = dict(order)
    row["payload"] = json.dumps(order.get("payload", order), ensure_ascii=False)
    row["updated_at"] = _now()
    return _upsert_order_resilient(row, strict=True)


# ---------------------------------------------------------- abandoned_carts
# `public.abandoned_carts` is OPTIONAL. Captured carts live in local SQLite and
# the Supabase copy is only a supplement, so a project that never ran that
# migration is healthy rather than broken. Supabase answers PGRST205 ("could
# not find the table") when it is absent, and every helper below used to treat
# that as a failure - so one missing table printed an error on every 5-minute
# reminder tick, on every capture and on every purge, forever. Detect the
# absent table once, skip the roundtrip that cannot succeed, and re-probe on an
# interval so creating the table later heals itself without a restart.
_ABANDONED_RECHECK_SECONDS = 900.0
_abandoned_missing_until = 0.0
_abandoned_missing_logged = False


def _abandoned_table_missing(exc):
    """True when Supabase reports abandoned_carts is absent from the schema."""
    text = str(exc).lower()
    return "pgrst205" in text or "could not find the table" in text


def _abandoned_table_usable():
    """Whether an abandoned_carts roundtrip is worth attempting right now."""
    return _time.monotonic() >= _abandoned_missing_until


def _abandoned_table_absent():
    """Record the table as missing so the next calls skip it."""
    global _abandoned_missing_until, _abandoned_missing_logged
    _abandoned_missing_until = _time.monotonic() + _ABANDONED_RECHECK_SECONDS
    if not _abandoned_missing_logged:
        _abandoned_missing_logged = True
        print("[supabase] abandoned_carts is absent from the Supabase schema "
              "(PGRST205); remote abandoned-cart sync is skipped and local "
              "carts keep working. Re-checked every 15 minutes.")


def _abandoned_table_present():
    """A roundtrip succeeded, so forget any earlier missing-table verdict."""
    global _abandoned_missing_until, _abandoned_missing_logged
    _abandoned_missing_until = 0.0
    _abandoned_missing_logged = False


def mirror_abandoned_cart(row):
    """Upsert one email-captured abandoned cart. Returns False if the
    configured Supabase table rejected the write, but never raises."""
    c = client()
    if c is None or not row or not _abandoned_table_usable():
        return False
    data = dict(row)
    items = data.get("items")
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (TypeError, ValueError):
            items = []
    data["items"] = items if isinstance(items, list) else []
    data["reminder_sent"] = bool(data.get("reminder_sent"))
    try:
        c.table("abandoned_carts").upsert(data).execute()
        _abandoned_table_present()
        return True
    except Exception as exc:
        if _abandoned_table_missing(exc):
            _abandoned_table_absent()
            return False
        print(f"[supabase] abandoned cart upsert failed: {exc}")
        return False


def load_due_abandoned_carts(cutoff, limit=500, offset=0):
    """Load ONE bounded page of carts eligible for a reminder.

    `offset` lets a worker walk a backlog page by page instead of pulling
    every due cart into memory at once. Returns [] when unconfigured or
    unavailable; the local cache remains the fallback.
    """
    if not _abandoned_table_usable():
        return []
    c = client()
    if c is None:
        return []
    try:
        offset = max(0, int(offset or 0))
        limit = max(1, int(limit or 1))
        res = _page((c.table("abandoned_carts").select("*")
                     .eq("reminder_sent", False)
                     .is_("converted_at", "null")
                     .lte("last_activity_at", cutoff)
                     .order("last_activity_at")), limit, offset).execute()
        _abandoned_table_present()
        return _res_data(res) or []
    except Exception as exc:
        if _abandoned_table_missing(exc):
            _abandoned_table_absent()
            return []
        print(f"[supabase] abandoned carts load failed: {exc}")
        return []


def mark_abandoned_converted(token, at):
    """Mark a cart converted in Supabase; never raises."""
    c = client()
    if c is None or not token or not _abandoned_table_usable():
        return False
    try:
        c.table("abandoned_carts").update({"converted_at": at,
                                            "updated_at": at}).eq("token", token).execute()
        _abandoned_table_present()
        return True
    except Exception as exc:
        if _abandoned_table_missing(exc):
            _abandoned_table_absent()
            return False
        print(f"[supabase] abandoned conversion mark failed: {exc}")
        return False


def mark_abandoned_reminder_sent(token, at):
    """Persist the one-shot reminder flag in Supabase; never raises."""
    c = client()
    if c is None or not token or not _abandoned_table_usable():
        return False
    try:
        c.table("abandoned_carts").update({"reminder_sent": True,
                                            "reminder_sent_at": at,
                                            "updated_at": at}).eq("token", token).execute()
        _abandoned_table_present()
        return True
    except Exception as exc:
        if _abandoned_table_missing(exc):
            _abandoned_table_absent()
            return False
        print(f"[supabase] abandoned reminder mark failed: {exc}")
        return False




def delete_abandoned_carts_for_tokens(tokens):
    """Delete abandoned-cart rows by token. Best effort, never raises."""
    c = client()
    tokens = [str(t or "").strip() for t in (tokens or []) if str(t or "").strip()]
    if c is None or not tokens or not _abandoned_table_usable():
        return 0
    try:
        c.table("abandoned_carts").delete().in_("token", tokens).execute()
        _abandoned_table_present()
        return len(tokens)
    except Exception as exc:
        if _abandoned_table_missing(exc):
            _abandoned_table_absent()
            return 0
        print(f"[supabase] abandoned cart token purge failed: {exc}")
        return 0


def delete_abandoned_carts_for_email(email, converted_only=True):
    """Delete abandoned carts tied to an order email. Best effort."""
    c = client()
    email = str(email or "").strip().lower()
    if c is None or not email or not _abandoned_table_usable():
        return 0
    try:
        q = c.table("abandoned_carts").delete().eq("email", email)
        if converted_only:
            q = q.not_.is_("converted_at", "null")
        q.execute()
        _abandoned_table_present()
        return 1
    except Exception as exc:
        if _abandoned_table_missing(exc):
            _abandoned_table_absent()
            return 0
        print(f"[supabase] abandoned cart email purge failed: {exc}")
        return 0


def delete_abandoned_carts_for_product(product_id, product_name=""):
    """Delete abandoned carts whose JSON items mention a deleted product.

    PostgREST JSON containment cannot cover every legacy item shape, so this
    uses safe ilike filters against the JSON text. It is best-effort and only
    runs after an admin intentionally deletes the product.
    """
    c = client()
    pid = str(product_id or "").strip()
    name = str(product_name or "").strip()
    if c is None or not (pid or name) or not _abandoned_table_usable():
        return 0
    deleted = 0

    def _absent(exc):
        """True when this failure was just the missing table; stop trying."""
        if _abandoned_table_missing(exc):
            _abandoned_table_absent()
            return True
        return False

    if pid:
        for shape in ({"id": pid}, {"productId": pid}, {"product_id": pid}):
            try:
                c.table("abandoned_carts").delete().contains("items", [shape]).execute()
                deleted += 1
            except Exception as exc:
                if _absent(exc):
                    return deleted
                print(f"[supabase] abandoned cart product containment purge failed: {exc}")
        try:
            c.table("abandoned_carts").delete().ilike("items", f"%{pid}%").execute()
            deleted += 1
        except Exception as exc:
            if _absent(exc):
                return deleted
            print(f"[supabase] abandoned cart product-id purge failed: {exc}")
    try:
        if name:
            c.table("abandoned_carts").delete().ilike("items", f"%{name[:80]}%").execute()
            deleted += 1
    except Exception as exc:
        if not _absent(exc):
            print(f"[supabase] abandoned cart product-name purge failed: {exc}")
    if deleted:
        _abandoned_table_present()
    return deleted


def mirror_marketing_campaign(row):
    """Mirror one campaign audit row without storing recipient addresses."""
    c = client()
    if c is None or not row:
        return False
    try:
        from campaign_types import serialize_campaign
        c.table("marketing_campaigns").upsert(serialize_campaign(row)).execute()
        return True
    except Exception as exc:
        print(f"[supabase] campaign upsert failed: {exc}")
        return False


def load_marketing_campaigns(limit=100):
    """Campaign audit rows, newest first. Never raises."""
    c = client()
    if c is None:
        return []
    try:
        from campaign_types import serialize_campaign
        res = (c.table("marketing_campaigns").select("*")
               .order("sent_at", desc=True).limit(limit).execute())
        rows = []
        for row in _res_data(res) or []:
            try:
                rows.append(serialize_campaign(row))
            except (TypeError, ValueError) as exc:
                print(f"[supabase] invalid campaign row skipped: {exc}")
        return rows
    except Exception as exc:
        print(f"[supabase] campaigns load failed: {exc}")
        return []


def suppress_marketing_email(email):
    """Persist one promotional-email opt-out; never raises."""
    c = client()
    email = str(email or "").strip().lower()
    if c is None or not email:
        return False
    try:
        c.table("marketing_suppressions").upsert({"email": email}).execute()
        return True
    except Exception as exc:
        print(f"[supabase] marketing suppression failed: {exc}")
        return False


def load_marketing_suppressions(limit=10000, offset=0):
    """Return one page of promotional opt-outs, or [] when unconfigured."""
    c = client()
    if c is None:
        return []
    try:
        offset = max(0, int(offset or 0))
        limit = max(1, int(limit or 1))
        res = _page(c.table("marketing_suppressions").select("email"),
                    limit, offset).execute()
        return _res_data(res) or []
    except Exception as exc:
        print(f"[supabase] marketing suppression load failed: {exc}")
        return []


def save_customer(row):
    """Upsert one customer account. No-op when unconfigured."""
    c = client()
    if c is None or not row:
        return False
    data = dict(row)
    try:
        c.table("customers").upsert(data).execute()
        return True
    except Exception as exc:
        print(f"[supabase] customer upsert failed: {exc}")
        return False


def load_customers(limit=2000, offset=0, columns="*"):
    """One page of customer rows, or [] when unconfigured. Never raises.

    `offset`/`columns` keep batch jobs (marketing broadcasts) inside a small,
    predictable memory budget: they can ask for just `email`, one page at a
    time, instead of every column of every customer.
    """
    c = client()
    if c is None:
        return []
    try:
        offset = max(0, int(offset or 0))
        limit = max(1, int(limit or 1))
        res = _page((c.table("customers").select(columns or "*")
                     .order("updated_at", desc=True)), limit, offset).execute()
        return _res_data(res) or []
    except Exception as exc:
        print(f"[supabase] load_customers failed: {exc}")
        return []


def link_guest_orders(customer_id, email):
    """Attach unowned orders that match email. No-op when unconfigured."""
    c = client()
    if c is None or not customer_id or not email:
        return 0
    try:
        res = (c.table("orders").update({"customer_user_id": customer_id})
               .eq("email", str(email).strip().lower())
               .is_("customer_user_id", "null")
               .execute())
        data = _res_data(res)
        return len(data) if data else 0
    except Exception as exc:
        print(f"[supabase] link_guest_orders failed: {exc}")
        return 0


def load_orders_for_customer(customer_id, limit=200):
    """Orders owned by this customer id, or None when unconfigured."""
    c = client()
    if c is None:
        return None
    try:
        res = (c.table("orders").select("*")
               .eq("customer_user_id", customer_id)
               .order("at", desc=True).limit(limit).execute())
        return _res_data(res) or []
    except Exception as exc:
        print(f"[supabase] load_orders_for_customer failed: {exc}")
        return None


def load_order_for_customer(order_id, customer_id):
    """One owned order, or None when missing/unconfigured."""
    c = client()
    if c is None:
        return None
    try:
        res = (c.table("orders").select("*")
               .eq("id", order_id)
               .eq("customer_user_id", customer_id)
               .limit(1).execute())
        rows = _res_data(res)
        return rows[0] if rows else None
    except Exception as exc:
        print(f"[supabase] load_order_for_customer failed: {exc}")
        return None


def update_order(order_id, status=None, payload=None):
    """Mirror an order status / payload change into Supabase.

    Returns True only after Supabase accepts the update. Admin order actions use
    this as a durability gate so a status/note/payment-review edit is never
    acknowledged while production will reload the old value.
    """
    c = client()
    if c is None:
        return False
    row = {}
    if status is not None:
        row["status"] = status
    if payload is not None:
        row["payload"] = json.dumps(payload, ensure_ascii=False)
    row["updated_at"] = _now()
    if not row:
        return False
    try:
        c.table("orders").update(row).eq("id", order_id).execute()
        return True
    except Exception as exc:
        print(f"[supabase] order update failed: {exc}")
        return False


def create_receipt(receipt):
    """Mirror a payment-proof submission into Supabase (best effort)."""
    c = client()
    if c is None:
        return
    row = dict(receipt)
    row["created_at"] = _now()
    try:
        c.table("receipts").upsert(row).execute()
    except Exception as exc:
        print(f"[supabase] receipt upsert failed: {exc}")


def create_receipt_strict(receipt):
    """Persist a payment receipt row into Supabase; True only on success."""
    c = client()
    if c is None:
        return False
    row = dict(receipt)
    row["created_at"] = _now()
    try:
        c.table("receipts").upsert(row).execute()
        return True
    except Exception as exc:
        print(f"[supabase] receipt upsert failed: {exc}")
        return False


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


def delete_receipt_strict(receipt_id=None, order_id=None, file_url=""):
    """Remove a mirrored payment receipt row from Supabase; True on success."""
    c = client()
    if c is None:
        return False
    try:
        q = c.table("receipts").delete()
        if receipt_id is not None:
            q = q.eq("id", receipt_id)
        elif file_url:
            q = q.eq("file_url", file_url)
        elif order_id:
            q = q.eq("order_id", order_id)
        else:
            return False
        q.execute()
        return True
    except Exception as exc:
        print(f"[supabase] receipt delete failed: {exc}")
        return False


# ------------------------------------------------------------------ orders
def _bucket():
    """The sole supported Storage bucket (environment overrides are ignored)."""
    return "uploads"


_OBJECT_URL_RE = re.compile(
    r"^/storage/v1/object/(?:public|sign)/([^/]+)/(.+)$")


def _storage_url_parts(url):
    """Validate the configured project origin before trusting an object URL."""
    try:
        base = urllib.parse.urlsplit((Config.SUPABASE_URL or "").strip())
        parsed = urllib.parse.urlsplit((url or "").strip())
        if (base.scheme not in ("http", "https") or not base.hostname
                or base.username or base.password
                or parsed.username or parsed.password
                or (parsed.scheme, parsed.hostname, parsed.port) !=
                   (base.scheme, base.hostname, base.port)):
            return None
        return _OBJECT_URL_RE.fullmatch(parsed.path)
    except (ValueError, TypeError):
        return None


def _bucket_from_url(url):
    """Return a supported bucket only for this project's Storage URLs."""
    m = _storage_url_parts(url)
    return m.group(1) if m and m.group(1) in {_bucket(), "receipts"} else ""


def _storage_path_from_url(url, bucket=None):
    """Resolve a supported project object URL; reject foreign origins."""
    supported = {_bucket(), "receipts"}
    if not Config.SUPABASE_URL or bucket not in (None, "", *supported):
        return ""
    raw = (url or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme or parsed.netloc:
        m = _storage_url_parts(raw)
        if not m or m.group(1) not in supported or (bucket and m.group(1) != bucket):
            return ""
        path = m.group(2)
    else:
        path = parsed.path.lstrip("/")
        if path.startswith("uploads/"):
            if bucket not in (None, "", _bucket()):
                return ""
            path = path[len("uploads/"):]
        elif path.startswith("receipts/"):
            if bucket != "receipts":
                return ""
            path = path[len("receipts/"):]
        if path.startswith("storage/"):
            return ""
    path = urllib.parse.unquote(path)
    if not path or any(ord(c) < 32 for c in path) or "\\" in path or any(p in ("", ".", "..") for p in path.split("/")):
        return ""
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
    bucket = bucket or _bucket_from_url(url) or _bucket()
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
        # `receipts` stores the uploaded object in `file_url`; `proof_url`
        # belongs to the parent `orders` row and is not present in this table.
        res = (c.table("receipts").select("id, file_url")
               .eq("order_id", order_id).execute())
        for row in _res_data(res):
            u = str((row or {}).get("file_url") or "").strip()
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
    # coupon redemptions and referral uses belong to the order too: a hard
    # delete must not leave their rows orphaned behind a gone order id.
    try:
        c.table("coupon_uses").delete().eq("order_id", order_id).execute()
    except Exception as exc:
        print(f"[supabase] coupon_uses delete failed: {exc}")
    try:
        c.table("referral_uses").delete().eq("order_id", order_id).execute()
    except Exception as exc:
        print(f"[supabase] referral_uses delete failed: {exc}")
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


def mirror_coupon_use(row):
    """Upsert one coupon redemption into coupon_uses. Never raises.

    The conflict target is (code, order_id), matching the table's unique
    constraint, so a retried order upserts the same row instead of adding a
    second redemption. Returns True only when Supabase accepted the write.
    """
    c = client()
    if c is None:
        return False
    try:
        c.table("coupon_uses").upsert(
            dict(row), on_conflict="code,order_id").execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] coupon_uses upsert failed: {exc}")
        return False


def load_coupon_uses(code=None):
    """Redemption log, newest first. None when Supabase is unavailable."""
    c = client()
    if c is None:
        return None
    try:
        q = c.table("coupon_uses").select("*")
        if code:
            q = q.eq("code", code)
        res = q.order("used_at", desc=True).execute()
        return _res_data(res) or []
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] coupon_uses read failed: {exc}")
        return None


def mirror_growth_settings(settings_dict):
    """Upsert the whole growth_settings key/value map."""
    c = client()
    if c is None:
        return
    try:
        rows = [{"key": k, "value": (json.dumps(v, separators=(",", ":")) if isinstance(v, (list, dict)) else str(v))} for k, v in dict(settings_dict).items()]
        c.table("growth_settings").upsert(rows).execute()
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] growth settings upsert failed: {exc}")


# Supplier-sync uncertainty warnings use the existing durable key/value table
# so the scheduled GitHub runner and the Render admin dashboard share one
# source of truth without requiring a new production table migration.
SUPPLIER_SYNC_WARNINGS_KEY = "supplier_sync_warnings_json"


def save_supplier_sync_warnings(warnings):
    """Replace the current supplier uncertainty queue. True on persistence."""
    c = client()
    if c is None:
        return False
    try:
        payload = json.dumps(list(warnings or [])[:1000], ensure_ascii=False,
                             separators=(",", ":"))
        c.table("growth_settings").upsert([
            {"key": SUPPLIER_SYNC_WARNINGS_KEY, "value": payload}
        ]).execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] supplier warnings save failed: {exc}")
        return False


# Product columns are an all-or-nothing persistence surface: when Supabase says
# one is missing, the save is refused instead of retrying with that field
# removed. The durable warning drives the admin ⚠️ badge to run the migration.
_SCHEMA_WARNING_KEY = "products_schema_warning"


def _warn_missing_product_columns(dropped):
    """Record a durable warning when any product column could not persist."""
    cols = sorted({str(col or "").strip() for col in (dropped or []) if str(col or "").strip()})
    if not cols:
        return
    try:
        warnings = [w for w in (load_supplier_sync_warnings() or [])
                    if isinstance(w, dict) and w.get("product_id") != _SCHEMA_WARNING_KEY]
        warnings.append({
            "product_id": _SCHEMA_WARNING_KEY,
            "product_name": "Product table columns",
            "code": "products_table_missing_columns",
            "reason": ("The Supabase products table is missing column(s) "
                       + ", ".join(cols) + ". The admin save was refused so "
                       "entered data cannot be silently dropped. Run the "
                       "products migration in supabase_schema.sql, then save again."),
            "at": _now(),
        })
        save_supplier_sync_warnings(warnings)
    except Exception as exc:                        # pragma: no cover - defensive
        print(f"[supabase] schema warning record failed: {exc}")


def load_supplier_sync_warnings():
    """Return the current supplier uncertainty queue; [] if absent/unavailable."""
    c = client()
    if c is None:
        return []
    try:
        res = (c.table("growth_settings").select("value")
               .eq("key", SUPPLIER_SYNC_WARNINGS_KEY).limit(1).execute())
        rows = _res_data(res)
        if not rows:
            return []
        raw = (rows[0] or {}).get("value")
        data = json.loads(raw) if isinstance(raw, str) else raw
        return [dict(row) for row in data if isinstance(row, dict)] if isinstance(data, list) else []
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] supplier warnings load failed: {exc}")
        return []


# Durable product-delete tombstones. Soft-deleting a seed product (source=
# "deleted" on its products-table row) only hides rows that live in the
# products table. catalog.merged() still unions the 258 bundled seed rows on
# every read, so a deleted seed id came back after every Render redeploy —
# the local data/catalog.json "deleted" list lives on the same ephemeral
# disk and is wiped with it. Keep the id list in growth_settings so a delete
# survives deploys the same way categories and the Delivery page do.
DELETED_IDS_KEY = "deleted_product_ids_json"
HARD_DELETED_PRODUCTS_TABLE = "deleted_products"


def _clean_deleted_ids(ids):
    out = []
    seen = set()
    for item in ids or []:
        pid = str(item or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def _upsert_hard_deleted_ids(c, ids):
    """Write transaction-safe permanent tombstones to the SQL ledger."""
    clean = _clean_deleted_ids(ids)
    if not clean:
        return True
    if c is None:
        return False
    c.table(HARD_DELETED_PRODUCTS_TABLE).upsert(
        [{"product_id": pid} for pid in clean]
    ).execute()
    invalidate_read_cache("hard_deleted_ids")
    return True


def load_hard_deleted_ids():
    """Load immutable SQL tombstones, or None if the ledger is unavailable.

    This dedicated table is the transaction-safe source for absolute deletes.
    The legacy JSON list below remains as a compatibility mirror for earlier
    deployments, but only the SQL ledger + trigger can serialize a delete
    against a concurrent upsert.
    """
    def _fetch():
        c = client()
        if c is None:
            return None
        try:
            out = []
            seen = set()
            offset = 0
            while True:
                query = c.table(HARD_DELETED_PRODUCTS_TABLE).select("product_id")
                res = _page(query, min(PAGE_SIZE, 500), offset).execute()
                rows = _res_data(res)
                for row in rows:
                    pid = str((row or {}).get("product_id") or "").strip()
                    if pid and pid not in seen:
                        seen.add(pid)
                        out.append(pid)
                if len(rows) < min(PAGE_SIZE, 500):
                    break
                offset += len(rows)
            return out
        except Exception as exc:
            print(f"[supabase] hard-deleted ids load failed: {exc}")
            return None

    return cached_read("hard_deleted_ids", _fetch)


def load_deleted_ids():
    """Return the durable deleted-product id set, or None when unavailable.

    None means 'could not read' (not configured / unreachable / parse error).
    Callers must treat that as an empty set so an outage neither resurrects a
    product nor empties the shop. An empty list means the key is present and
    genuinely empty.
    """
    def _fetch():
        c = client()
        if c is None:
            return None
        try:
            res = (c.table("growth_settings")
                   .select("value")
                   .eq("key", DELETED_IDS_KEY)
                   .limit(1)
                   .execute())
            rows = _res_data(res)
            if not rows:
                return []
            raw = (rows[0] or {}).get("value")
            if raw is None or raw == "":
                return []
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, list):
                return []
            out = []
            seen = set()
            for item in data:
                pid = str(item or "").strip()
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                out.append(pid)
            return out
        except Exception as exc:                       # pragma: no cover
            print(f"[supabase] deleted ids load failed: {exc}")
            return None

    return cached_read("deleted_ids", _fetch)


def save_deleted_ids(ids):
    """Persist the durable deleted-product id list. Never raises.

    Returns True only when Supabase accepted the write. Deduplicates and
    drops blanks so a bad caller cannot bloat the row.
    """
    c = client()
    if c is None:
        return False
    clean = []
    seen = set()
    for item in (ids or []):
        pid = str(item or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        clean.append(pid)
    try:
        payload = json.dumps(clean, ensure_ascii=False)
        c.table("growth_settings").upsert(
            [{"key": DELETED_IDS_KEY, "value": payload}]
        ).execute()
        invalidate_read_cache("deleted_ids")
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] deleted ids save failed: {exc}")
        return False


def add_deleted_id(pid):
    """Record one permanent tombstone in the SQL ledger and legacy JSON list.

    The SQL table is the race-safe source of truth; the old growth_settings
    array is retained for compatibility with older readers. Never raises and
    returns True if either durable write confirms the id.
    """
    pid = str(pid or "").strip()
    if not pid:
        return False
    ledger_written = False
    c = client()
    if c is not None:
        try:
            ledger_written = _upsert_hard_deleted_ids(c, [pid])
        except Exception as exc:
            print(f"[supabase] hard-delete tombstone write failed: {exc}")
    current = load_deleted_ids()
    if current is None:
        # Best-effort compatibility write: the dedicated SQL ledger remains
        # authoritative if the legacy JSON row is unavailable.
        legacy_written = save_deleted_ids([pid])
    elif pid in current:
        legacy_written = True
    else:
        legacy_written = save_deleted_ids(list(current) + [pid])
    return bool(ledger_written or legacy_written)


def clear_deleted_id(pid):
    """Drop one product id from the durable tombstone list. Never raises.

    Called when a product is re-created / re-saved under the same id so the
    durable filter does not hide the new row. Returns True when the id is
    gone (or was never there). A failed read is a no-op: we must not wipe
    the whole list on a blip.
    """
    pid = str(pid or "").strip()
    if not pid:
        return False
    current = load_deleted_ids()
    if current is None:
        return False
    if pid not in current:
        return True
    return save_deleted_ids([x for x in current if x != pid])


# ------------------------------------------------- manual out-of-stock locks
# The owner switching a product to "Out of stock" is a decision, not a
# quantity. It used to survive only as `stock_quantity = 0`, which the supplier
# watchdog is explicitly allowed to raise again the moment a supplier page
# printed "In stock" - so a hand-sold-out product quietly came back to
# "In Stock" (the reported lock failure). The products table has no column for
# a lock and adding one would need a schema section on a phone, so the locks
# live in one growth_settings JSON row, exactly like the deleted-id tombstones:
# durable across deploys, no DDL, and never rewritten by a catalogue upsert.
#
# Shape: {"<product id>": {"at": "<iso>", "by": "<admin email>", "why": str}}
MANUAL_STOCK_LOCKS_KEY = "manual_stock_locks_json"
MANUAL_STOCK_LOCK_MAX = 5000


def _clean_stock_locks(rows):
    """Normalise an arbitrary mapping/list into the storable lock dict."""
    out = {}
    items = rows.items() if isinstance(rows, dict) else [
        (r, {}) for r in (rows or []) if isinstance(r, str)
    ]
    for pid, meta in list(items)[:MANUAL_STOCK_LOCK_MAX]:
        key = str(pid or "").strip()[:64]
        if not key:
            continue
        meta = meta if isinstance(meta, dict) else {}
        entry = {"at": str(meta.get("at") or "")[:40],
                 "by": str(meta.get("by") or "")[:120],
                 "why": str(meta.get("why") or "")[:200]}
        out[key] = entry
    return out


def load_manual_stock_locks():
    """The durable manual out-of-stock locks, ``{}`` when none, ``None`` on failure.

    ``None`` (an unreadable table) is distinct from ``{}`` (read it, there are
    none): callers must never treat a network blip as "nothing is locked".
    """
    def _fetch():
        c = client()
        if c is None:
            return None
        try:
            res = (c.table("growth_settings")
                   .select("value")
                   .eq("key", MANUAL_STOCK_LOCKS_KEY)
                   .limit(1)
                   .execute())
            rows = _res_data(res)
            if not rows:
                return {}
            raw = (rows[0] or {}).get("value")
            if raw is None or raw == "":
                return {}
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and isinstance(data.get("locks"), dict):
                data = data["locks"]
            return _clean_stock_locks(data if isinstance(data, dict) else [])
        except Exception as exc:                       # pragma: no cover
            print(f"[supabase] manual stock lock load failed: {exc}")
            return None

    return cached_read("manual_stock_locks", _fetch)


def save_manual_stock_locks(rows):
    """Persist the whole lock map. True only when Supabase confirmed the write."""
    c = client()
    if c is None:
        return False
    try:
        payload = json.dumps(_clean_stock_locks(rows), ensure_ascii=False)
        c.table("growth_settings").upsert(
            [{"key": MANUAL_STOCK_LOCKS_KEY, "value": payload}]
        ).execute()
        invalidate_read_cache("manual_stock_locks")
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] manual stock lock save failed: {exc}")
        return False


def add_manual_stock_lock(pid, actor="", why=""):
    """Lock one product out of sale until the owner puts stock back."""
    pid = str(pid or "").strip()
    if not pid:
        return False
    current = load_manual_stock_locks()
    locks = dict(current) if isinstance(current, dict) else {}
    if pid in locks:
        return True
    import datetime as _datetime
    locks[pid] = {"at": _datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                  "by": str(actor or "")[:120], "why": str(why or "")[:200]}
    return save_manual_stock_locks(locks)


def clear_manual_stock_lock(pid):
    """Remove one lock. True when it is gone (or was never there)."""
    pid = str(pid or "").strip()
    if not pid:
        return False
    current = load_manual_stock_locks()
    if not isinstance(current, dict):
        return False
    if pid not in current:
        return True
    return save_manual_stock_locks({k: v for k, v in current.items() if k != pid})


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


# Homepage "More from the house" product picks. They share the same durable
# growth_settings pattern as categories: one JSON row, no table migration, and
# the value survives Render's ephemeral disk.
HOMEPAGE_FEATURED_KEY = "homepage_featured_json"


def save_homepage_featured(featured):
    """Persist the homepage featured-product selector payload. Never raises."""
    c = client()
    if c is None:
        return False
    try:
        payload = json.dumps(featured or {"categories": {}}, ensure_ascii=False)
        row = [{"key": HOMEPAGE_FEATURED_KEY, "value": payload}]
        try:
            c.table("growth_settings").upsert(row, on_conflict="key").execute()
        except TypeError:
            c.table("growth_settings").upsert(row).execute()
        invalidate_read_cache("homepage_featured")
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] homepage featured save failed: {exc}")
        return False


def load_homepage_featured():
    """Return the homepage featured selector payload, {} if unset, or None
    when Supabase is unavailable."""
    def _fetch():
        c = client()
        if c is None:
            return None
        try:
            res = (c.table("growth_settings")
                   .select("value")
                   .eq("key", HOMEPAGE_FEATURED_KEY)
                   .limit(1)
                   .execute())
            rows = _res_data(res)
            if not rows:
                return {"categories": {}}
            raw = (rows[0] or {}).get("value")
            if raw is None or raw == "":
                return {"categories": {}}
            data = json.loads(raw) if isinstance(raw, str) else raw
            return data if isinstance(data, dict) else {"categories": {}}
        except Exception as exc:                       # pragma: no cover
            print(f"[supabase] homepage featured load failed: {exc}")
            return None

    return cached_read("homepage_featured", _fetch)


# The Delivery page the owner edits in Admin -> Delivery. Same growth_settings
# pattern as the categories above: one JSON row, no new schema, survives a
# Render redeploy (the repo copy of delivery.html is only the fallback).
DELIVERY_PAGE_KEY = "delivery_page_json"


def save_delivery_page(page):
    """Persist the Delivery page as one growth_settings row. Never raises."""
    c = client()
    if c is None:
        return False
    try:
        payload = json.dumps(page, ensure_ascii=False)
        c.table("growth_settings").upsert(
            [{"key": DELIVERY_PAGE_KEY, "value": payload}]
        ).execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] delivery page save failed: {exc}")
        return False


def load_delivery_page():
    """Return the Delivery page stored under DELIVERY_PAGE_KEY, or None."""
    c = client()
    if c is None:
        return None
    try:
        res = (c.table("growth_settings")
               .select("value")
               .eq("key", DELIVERY_PAGE_KEY)
               .limit(1)
               .execute())
        rows = _res_data(res)
        if not rows:
            return None
        raw = (rows[0] or {}).get("value")
        if raw is None or raw == "":
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, dict) and data:
            return data
        return None
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] delivery page load failed: {exc}")
        return None


# Customer care is another small owner-editable document. It deliberately uses
# growth_settings (an existing durable key/value table) rather than a new
# site_settings column set: an Admin can update live contact details straight
# away even on an older production schema, and the values survive Render's
# ephemeral application disk.
CUSTOMER_CARE_KEY = "customer_care_json"


def save_customer_care(customer_care):
    """Persist the public customer-care document. Returns True on success."""
    c = client()
    if c is None:
        return False
    try:
        c.table("growth_settings").upsert([{
            "key": CUSTOMER_CARE_KEY,
            "value": json.dumps(customer_care, ensure_ascii=False),
        }]).execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] customer care save failed: {exc}")
        return False


def load_customer_care():
    """Return the saved public customer-care document, or None when absent."""
    c = client()
    if c is None:
        return None
    try:
        res = (c.table("growth_settings").select("value")
               .eq("key", CUSTOMER_CARE_KEY).limit(1).execute())
        rows = _res_data(res)
        if not rows:
            return None
        raw = (rows[0] or {}).get("value")
        if raw is None or raw == "":
            return None
        value = json.loads(raw) if isinstance(raw, str) else raw
        return value if isinstance(value, dict) and value else None
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] customer care load failed: {exc}")
        return None


def load_categories_state():
    """Return ``(readable, found, categories)`` for the durable category JSON.

    A missing key is a valid empty result, while a failed query or malformed
    payload is unavailable. Callers that might overwrite or delete media must
    keep those states distinct; treating a read failure as an empty list can
    roll back good data or purge a still-referenced category cover.
    """
    c = client()
    if c is None:
        return False, False, None
    try:
        res = (c.table("growth_settings")
               .select("value")
               .eq("key", CATEGORIES_KEY)
               .limit(1)
               .execute())
        rows = _res_data(res)
        if not rows:
            return True, False, []
        raw = (rows[0] or {}).get("value")
        if raw is None or raw == "":
            return True, True, []
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, dict) and isinstance(data.get("categories"), list):
            data = data["categories"]
        if isinstance(data, list):
            return True, True, data
        print("[supabase] categories load failed: invalid categories payload")
        return False, True, None
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] categories load failed: {exc}")
        return False, False, None


def load_categories():
    """Return the saved non-empty category list, or None for absent/empty/error."""
    readable, found, categories = load_categories_state()
    return categories if readable and found and categories else None


def load_orders(limit=500, offset=0, columns="*"):
    """Return ONE page of the Supabase orders table, or []. Never raises.

    `offset`/`columns` allow paginated, low-memory scans (e.g. the marketing
    recipient walk asks only for `email`, 200 rows at a time).
    """
    c = client()
    if c is None:
        return []
    try:
        offset = max(0, int(offset or 0))
        limit = max(1, int(limit or 1))
        res = _page((c.table("orders")
                     .select(columns or "*")
                     .order("updated_at", desc=True)), limit, offset).execute()
        data = _res_data(res)
        return data or []
    except Exception as exc:
        print(f"[supabase] load_orders failed: {exc}")
        return []


# The Google refresh token is Fernet-encrypted by google_sheets.py before it
# reaches this existing durable key/value table. Never return it to a browser.
ACCOUNTING_GOOGLE_CREDENTIALS_KEY = "accounting_google_sheets_credentials_v1"


def load_accounting_google_credentials():
    """Return the encrypted Google credential blob, or None on read failure."""
    c = client()
    if c is None:
        return None
    try:
        rows = _res_data(c.table("growth_settings").select("value")
                         .eq("key", ACCOUNTING_GOOGLE_CREDENTIALS_KEY)
                         .limit(1).execute()) or []
        return str((rows[0] or {}).get("value") or "") if rows else ""
    except Exception as exc:
        print(f"[supabase] Google accounting credentials read failed: {exc}")
        return None


def save_accounting_google_credentials(encrypted_blob):
    """Strictly persist an encrypted Google credential blob."""
    c = client()
    if c is None:
        return False
    try:
        c.table("growth_settings").upsert({
            "key": ACCOUNTING_GOOGLE_CREDENTIALS_KEY,
            "value": str(encrypted_blob or ""),
        }).execute()
        return True
    except Exception as exc:
        print(f"[supabase] Google accounting credentials save failed: {exc}")
        return False


ACCOUNTING_BATCHES_KEY = "accounting_batches_json"


def load_confirmed_orders_for_accounting(page_size=500, max_rows=100_000):
    """Return every confirmed order needed by the dedicated accounting page.

    The orders table is already the durable accounting source: confirmation
    snapshots are stored in each order's JSON payload. Read in stable PostgREST
    pages so long-running multi-year ledgers are not capped by the first 500
    rows. ``None`` means the production read failed; an empty list is healthy.
    """
    c = client()
    if c is None:
        return None
    rows, offset = [], 0
    page_size = max(1, min(1000, int(page_size or 500)))
    max_rows = max(page_size, int(max_rows or 100_000))
    try:
        while True:
            builder = (c.table("orders")
                       .select("id,status,payload,total,currency,at,updated_at,customer_name")
                       .eq("status", "confirmed")
                       .order("at", desc=True))
            page = _res_data(_page(builder, page_size, offset).execute()) or []
            rows.extend(page)
            if len(rows) > max_rows:
                raise RuntimeError("confirmed-order ledger exceeded the safety ceiling")
            if len(page) < page_size:
                break
            offset += len(page)
        return rows
    except Exception as exc:
        print(f"[supabase] confirmed accounting orders read failed: {exc}")
        return None


def load_order_for_accounting(order_id):
    """Read one order for an authenticated accounting edit.

    ``None`` means the read failed, ``{}`` means the order does not exist.
    """
    c = client()
    if c is None:
        return None
    try:
        rows = _res_data(c.table("orders").select("*")
                         .eq("id", str(order_id)).limit(1).execute()) or []
        return dict(rows[0]) if rows else {}
    except Exception as exc:
        print(f"[supabase] accounting order read failed: {exc}")
        return None


def load_accounting_batches():
    """Read the archived batch snapshots from the existing key/value store."""
    c = client()
    if c is None:
        return None
    try:
        rows = _res_data(c.table("growth_settings").select("value")
                         .eq("key", ACCOUNTING_BATCHES_KEY).limit(1).execute()) or []
        if not rows:
            return []
        raw = rows[0].get("value")
        value = json.loads(raw) if isinstance(raw, str) and raw else raw
        return value if isinstance(value, list) else []
    except Exception as exc:
        print(f"[supabase] accounting batches read failed: {exc}")
        return None


def save_accounting_batches(batches):
    """Strictly persist an archive list; True only after Supabase confirms."""
    c = client()
    if c is None:
        return False
    try:
        value = json.dumps(list(batches or []), ensure_ascii=False,
                           separators=(",", ":"))
        c.table("growth_settings").upsert({
            "key": ACCOUNTING_BATCHES_KEY,
            "value": value,
        }).execute()
        return True
    except Exception as exc:
        print(f"[supabase] accounting batches save failed: {exc}")
        return False


# The owner's manual expense logger (bulk stock purchases, payouts and bank
# transfer / withdrawal charges) plus the accounting desk settings (starting
# bank balances, reference spreadsheet). Same durable key/value row pattern as
# the batch archive: one JSON row, mirrored by the local SQLite cache.
ACCOUNTING_EXPENSES_KEY = "accounting_manual_expenses_json"
ACCOUNTING_SETTINGS_KEY = "accounting_settings_json"
# The Trash / Recycler: full snapshots of products the owner deleted, kept out
# of every active listing until restored or permanently purged.
PRODUCT_TRASH_KEY = "product_trash_json"


def _load_growth_json(key):
    """Read one JSON row from growth_settings; None means the read failed."""
    c = client()
    if c is None:
        return None
    try:
        rows = _res_data(c.table("growth_settings").select("value")
                         .eq("key", key).limit(1).execute()) or []
        if not rows:
            return []
        raw = rows[0].get("value")
        value = json.loads(raw) if isinstance(raw, str) and raw else raw
        return value if isinstance(value, list) else []
    except Exception as exc:
        print(f"[supabase] growth json read failed ({key}): {exc}")
        return None


def _save_growth_json(key, value):
    """Strictly persist one JSON row; True only after Supabase confirms."""
    c = client()
    if c is None:
        return False
    try:
        c.table("growth_settings").upsert({
            "key": key,
            "value": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        }).execute()
        return True
    except Exception as exc:
        print(f"[supabase] growth json save failed ({key}): {exc}")
        return False


def load_accounting_expenses():
    """Manual expense records, or None when the production read fails."""
    return _load_growth_json(ACCOUNTING_EXPENSES_KEY)


def save_accounting_expenses(expenses):
    return _save_growth_json(ACCOUNTING_EXPENSES_KEY, list(expenses or []))


def load_accounting_settings():
    """The accounting desk settings row (dict), or None on a failed read."""
    c = client()
    if c is None:
        return None
    try:
        rows = _res_data(c.table("growth_settings").select("value")
                         .eq("key", ACCOUNTING_SETTINGS_KEY).limit(1).execute()) or []
        if not rows:
            return {}
        raw = rows[0].get("value")
        value = json.loads(raw) if isinstance(raw, str) and raw else raw
        return value if isinstance(value, dict) else {}
    except Exception as exc:
        print(f"[supabase] accounting settings read failed: {exc}")
        return None


def save_accounting_settings(settings):
    """Strictly persist the accounting settings dict."""
    c = client()
    if c is None:
        return False
    try:
        c.table("growth_settings").upsert({
            "key": ACCOUNTING_SETTINGS_KEY,
            "value": json.dumps(dict(settings or {}), ensure_ascii=False,
                                separators=(",", ":")),
        }).execute()
        return True
    except Exception as exc:
        print(f"[supabase] accounting settings save failed: {exc}")
        return False


def load_product_trash():
    """Trashed product snapshots, or None when the production read fails."""
    return _load_growth_json(PRODUCT_TRASH_KEY)


def save_product_trash(rows):
    return _save_growth_json(PRODUCT_TRASH_KEY, list(rows or []))


def load_receipts(limit=500):
    """Return receipts from Supabase, or None when the production read fails."""
    c = client()
    if c is None:
        return None
    try:
        res = (c.table("receipts")
               .select("*")
               .order("created_at", desc=True)
               .limit(limit)
               .execute())
        return _res_data(res) or []
    except Exception as exc:
        print(f"[supabase] load_receipts failed: {exc}")
        return None


def load_receipt(receipt_id):
    """Read one receipt from Supabase for an authenticated admin action."""
    c = client()
    if c is None:
        return None
    try:
        res = c.table("receipts").select("*").eq("id", str(receipt_id)).limit(1).execute()
        rows = _res_data(res)
        return rows[0] if rows else None
    except Exception as exc:
        print(f"[supabase] load_receipt failed: {exc}")
        return None


# ------------------------------------------------------- durable analytics
# Store insights used to live only in the SQLite file on the Render disk, so
# every redeploy wiped the dashboard. Each page view and engagement event is
# now mirrored into the analytics_events table (schema_sections/
# 17_analytics.sql), and create_app() copies the retention window back into
# the local tables on boot. Writes are best-effort: when Supabase is
# unreachable the shop keeps counting locally and the mirror simply has a
# gap for that batch.
ANALYTICS_EVENTS_TABLE = "analytics_events"

# PostgREST page size for the restore reads, and a sane stop against a
# runaway pagination loop (mirrors the watchdog's DB_ROW_CEILING).
ANALYTICS_PAGE = 2000
ANALYTICS_ROW_CEILING = 200_000


def mirror_analytics_events(rows):
    """Insert analytics rows into Supabase in one call. Never raises.

    `rows` is a list of dicts shaped like the analytics_events table (see
    analytics.record). Returns True only when Supabase accepted the write;
    an empty batch is trivially mirrored without touching the client.
    """
    rows = [dict(r) for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return True
    c = client()
    if c is None:
        return False
    try:
        c.table(ANALYTICS_EVENTS_TABLE).insert(rows).execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] analytics mirror failed: {exc}")
        return False


def load_analytics_events(since_day, limit=None):
    """Rows mirrored on/after `since_day` (YYYY-MM-DD), NEWEST first.

    Paged through the WHOLE window: the restore after a disk wipe must never
    lose the newest rows - today's visitors are the dashboard's headline
    number, and a hard row cap ordered oldest-first silently dropped them
    (the "1 visitor today right after a deploy" defect). Pages are read
    newest-first so even a configured ceiling would shed the oldest days,
    never the current one.

    Returns [] when Supabase is unconfigured or the read fails - the caller
    treats that as "nothing to restore" (and, because nothing was written,
    the next boot simply tries again). Never raises.
    """
    c = client()
    if c is None:
        return []
    ceiling = int(limit or ANALYTICS_ROW_CEILING)
    rows = []
    start = 0
    try:
        while start < ceiling:
            end = min(start + ANALYTICS_PAGE, ceiling) - 1
            res = (c.table(ANALYTICS_EVENTS_TABLE)
                   .select("*")
                   .gte("day", str(since_day))
                   .order("at", desc=True)
                   .range(start, end)
                   .execute())
            page = _res_data(res) or []
            if not isinstance(page, list):
                raise RuntimeError("PostgREST returned an unexpected payload shape")
            rows.extend(page)
            if len(page) < (end - start + 1):
                break                        # short page: the window ended
            start += len(page)
        return rows
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] load_analytics_events failed: {exc}")
        return []


# ------------------------------------------------- durable search history
SEARCH_QUERIES_TABLE = "search_queries"


def mirror_search_queries(rows):
    """Insert customer search rows into Supabase. Never raises."""
    rows = [dict(r) for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return True
    c = client()
    if c is None:
        return False
    try:
        c.table(SEARCH_QUERIES_TABLE).insert(rows).execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] search mirror failed: {exc}")
        return False


def load_search_queries(since_day, limit=None):
    """Mirrored search rows on/after `since_day`, NEWEST first, fully paged
    (see load_analytics_events for why the newest rows must never be the ones
    a cap drops). Never raises."""
    c = client()
    if c is None:
        return []
    ceiling = int(limit or ANALYTICS_ROW_CEILING)
    rows = []
    start = 0
    try:
        while start < ceiling:
            end = min(start + ANALYTICS_PAGE, ceiling) - 1
            res = (c.table(SEARCH_QUERIES_TABLE)
                   .select("*")
                   .gte("day", str(since_day))
                   .order("at", desc=True)
                   .range(start, end)
                   .execute())
            page = _res_data(res) or []
            if not isinstance(page, list):
                break
            rows.extend(page)
            if len(page) < (end - start + 1):
                break
            start += len(page)
        return rows
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] load_search_queries failed: {exc}")
        return []


# --------------------------------------------------- lifetime counters
# The store's odometer: totals that must never restart at zero because a
# deploy replaced the disk. Stored one row per counter and merged with the
# local value by taking the maximum, so neither side can roll it back.
ANALYTICS_COUNTERS_TABLE = "analytics_counters"


def save_analytics_counters(rows):
    """Upsert [{name, value, updated_at}, ...]. Never raises."""
    rows = [dict(r) for r in (rows or []) if isinstance(r, dict) and r.get("name")]
    if not rows:
        return True
    c = client()
    if c is None:
        return False
    try:
        c.table(ANALYTICS_COUNTERS_TABLE).upsert(rows, on_conflict="name").execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] counter save failed: {exc}")
        return False


def load_analytics_counters():
    """Every stored counter row, or [] when unavailable. Never raises."""
    c = client()
    if c is None:
        return []
    try:
        res = c.table(ANALYTICS_COUNTERS_TABLE).select("*").limit(500).execute()
        return _res_data(res) or []
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] load_analytics_counters failed: {exc}")
        return []


# ------------------------------------------------- background crash reports
JOB_FAILURES_TABLE = "job_failures"


def mirror_job_failure(record):
    """Store one background-job crash report. Never raises.

    The dyno that crashed is the one thing we cannot trust to keep the
    evidence, so the report is pushed off-box immediately.
    """
    if not isinstance(record, dict) or not record.get("job"):
        return False
    c = client()
    if c is None:
        return False
    row = {k: record.get(k) for k in (
        "job", "worker", "payload_id", "error_type", "message", "traceback",
        "rss_mb", "attempt", "host", "at") if record.get(k) not in (None, "")}
    try:
        c.table(JOB_FAILURES_TABLE).insert([row]).execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] job failure mirror failed: {exc}")
        return False


def load_job_failures(limit=50):
    """The most recent stored crash reports, newest first. Never raises."""
    c = client()
    if c is None:
        return []
    try:
        res = (c.table(JOB_FAILURES_TABLE)
               .select("*")
               .order("at", desc=True)
               .limit(limit)
               .execute())
        return _res_data(res) or []
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] load_job_failures failed: {exc}")
        return []


# Per-variant stock is kept in Supabase PostgreSQL as one JSON value. This
# avoids a local-first write in production while preserving the existing
# growth_settings schema.
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



def load_variant_stock_strict():
    """Read production stock and distinguish an empty table from a failed read."""
    c = client()
    if c is None:
        return None
    try:
        res = (c.table("growth_settings").select("value")
               .eq("key", VARIANT_STOCK_KEY).limit(1).execute())
        rows = _res_data(res)
        if not rows:
            return []
        raw = (rows[0] or {}).get("value")
        if raw in (None, ""):
            return []
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"[supabase] variant stock read failed: {exc}")
        return None


def replace_variant_stock_strict(rows):
    """Write stock to Supabase and return the exact post-write read-back."""
    c = client()
    if c is None:
        return None
    try:
        payload = json.dumps(list(rows or []), ensure_ascii=False)
        c.table("growth_settings").upsert(
            [{"key": VARIANT_STOCK_KEY, "value": payload}]
        ).execute()
        return load_variant_stock_strict()
    except Exception as exc:
        print(f"[supabase] variant stock write failed: {exc}")
        return None

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


# ---------------------------------------------------------------------------
# product_reviews as a REAL table.
#
# The blob in growth_settings was durable but not queryable, and it was
# last-write-wins: two reviews arriving together meant one silently vanished,
# because the whole table was re-serialised and upserted as a single value.
# These functions make the table the source of truth. The blob is kept as a
# read-only backup until a migration has been verified - it is never deleted
# here.
# ---------------------------------------------------------------------------

_REVIEW_COLUMNS = ("product_id", "order_id", "email", "name", "rating", "title",
                   "body", "hidden", "created_at", "updated_at")

# The table was first drafted as stars / note / at. The legacy blob in
# growth_settings still uses those keys, so reading accepts both - dropping a
# restored review over a key name would be silent data loss.
_LEGACY_REVIEW_KEYS = {"stars": "rating", "note": "body", "at": "created_at"}


def _clean_review(row):
    """Normalise one review to the table's columns. Returns None if unusable."""
    row = row or {}
    for old_key, new_key in _LEGACY_REVIEW_KEYS.items():
        if row.get(new_key) is None and row.get(old_key) is not None:
            row = dict(row)
            row[new_key] = row[old_key]
            break
    product_id = str(row.get("product_id") or "").strip()
    email = str(row.get("email") or "").strip().lower()
    if not product_id or not email:
        # The table's unique key is (product_id, email); a row missing either
        # cannot be stored or de-duplicated, so it is skipped, not guessed at.
        return None
    try:
        rating = int(row.get("rating") or 5)
    except (TypeError, ValueError):
        rating = 5
    rating = max(1, min(5, rating))        # matches the check constraint
    created = str(row.get("created_at") or "") or None
    updated = str(row.get("updated_at") or "") or created
    return {
        "product_id": product_id,
        "order_id": (str(row.get("order_id")).strip() or None
                     if row.get("order_id") is not None else None),
        "email": email,
        "name": (str(row.get("name")).strip() or None
                 if row.get("name") is not None else None),
        "rating": rating,
        "title": (str(row.get("title")).strip() or None
                  if row.get("title") is not None else None),
        "body": (str(row.get("body")).strip() or None
                 if row.get("body") is not None else None),
        "hidden": bool(row.get("hidden")),
        "created_at": created,
        "updated_at": updated,
    }


def save_product_reviews_table(rows):
    """Upsert reviews into the real product_reviews table.

    Returns (saved_count, error). The conflict target is (product_id, email),
    so re-saving the same review updates it instead of failing or duplicating.
    """
    c = client()
    if c is None:
        return 0, "Supabase is unavailable"
    clean = [r for r in (_clean_review(x) for x in (rows or [])) if r]
    if not clean:
        return 0, None
    for row in clean:
        # Let the database defaults fill the timestamps when we have none.
        for ts in ("created_at", "updated_at"):
            if not row.get(ts):
                row.pop(ts, None)
    try:
        c.table("product_reviews").upsert(
            clean, on_conflict="product_id,email").execute()
        return len(clean), None
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] product_reviews upsert failed: {exc}")
        return 0, str(exc)


def load_product_reviews_table(product_id=None):
    """Reviews from the real table. None when Supabase is unavailable -
    callers must not read that as 'no reviews exist'."""
    c = client()
    if c is None:
        return None
    try:
        q = c.table("product_reviews").select("*")
        if product_id:
            q = q.eq("product_id", product_id)
        res = q.order("created_at", desc=True).execute()
        return _res_data(res) or []
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] product_reviews read failed: {exc}")
        return None


def delete_product_review(product_id, email):
    """Delete exactly one review. Returns True only if a row was removed."""
    c = client()
    if c is None:
        return False
    try:
        c.table("product_reviews").delete() \
            .eq("product_id", product_id) \
            .eq("email", str(email or "").strip().lower()).execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] product_reviews delete failed: {exc}")
        return False


def set_product_review_hidden(product_id, email, hidden=True):
    """Moderate one review: hide or unhide it without deleting it.

    Returns True only if Supabase accepted the update.
    """
    c = client()
    if c is None:
        return False
    try:
        c.table("product_reviews").update({"hidden": bool(hidden)}) \
            .eq("product_id", product_id) \
            .eq("email", str(email or "").strip().lower()).execute()
        return True
    except Exception as exc:                       # pragma: no cover
        print(f"[supabase] product_reviews moderation failed: {exc}")
        return False


def migrate_product_reviews_from_blob(dry_run=True):
    """Copy reviews from the legacy growth_settings blob into the real table.

    Never deletes the blob: it stays until the copy has been verified, so a
    failed migration cannot lose review data. Returns a report dict.
    """
    report = {"source": "growth_settings:" + PRODUCT_REVIEWS_KEY,
              "dry_run": bool(dry_run), "found": 0, "usable": 0,
              "skipped": 0, "written": 0, "verified": 0, "error": None,
              "blob_deleted": False}
    blob = load_product_reviews()
    if blob is None:
        report["error"] = "no legacy blob present (or Supabase unavailable)"
        return report
    report["found"] = len(blob)
    clean = []
    for row in blob:
        c = _clean_review(row)
        if c is None:
            report["skipped"] += 1
        else:
            clean.append(c)
    report["usable"] = len(clean)
    if dry_run or not clean:
        return report
    written, err = save_product_reviews_table(clean)
    if err:
        report["error"] = err
        return report
    report["written"] = written
    # Verify by reading back per product, so "written" is not taken on trust.
    verified = 0
    for row in clean:
        back = load_product_reviews_table(row["product_id"])
        if back is None:
            continue
        if any(str(r.get("email") or "").lower() == row["email"] for r in back):
            verified += 1
    report["verified"] = verified
    if verified < len(clean):
        report["error"] = (f"only {verified}/{len(clean)} rows verified - the "
                           "legacy blob has been left in place")
    return report
