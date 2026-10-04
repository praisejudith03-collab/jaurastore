"""Catalogue: the seed product list plus every admin edit.

The shop ships with `seed.json` (the base catalogue) and keeps admin-created /
admin-edited products in `catalog.json` (Config.CATALOG_PATH). `merged()` blends
the two into the live catalogue the store sees.

Two persistence backends are supported:

* **Local (default, no credentials).** Reads the seed file and the admin
  overrides file from disk. This is what the test suite and a fresh checkout
  exercise.
* **Supabase.** When ``SUPABASE_URL`` and ``SUPABASE_SERVICE_ROLE_KEY`` are set,
  the admin products table in Supabase is the source of truth and writes are
  mirrored there. The local override file is still used as a read-through cache
  so a momentarily unavailable Supabase never empties the shop.
"""
import os, sys, json, re, secrets, datetime, contextlib, hashlib
from config import Config

try:
    import threading
except ImportError:  # pragma: no cover
    threading = None

try:
    import fcntl                      # POSIX advisory locks (gunicorn workers)
except ImportError:                   # pragma: no cover - non-POSIX fallback
    fcntl = None

ROOT = os.path.dirname(os.path.abspath(__file__))

# The admin-override file. Module-level on purpose: tests monkeypatch this.
CATALOG_FILE = Config.CATALOG_PATH
SEED_PATH = os.environ.get(
    "SEED_PATH", os.path.join(ROOT, "data", "seed.json"))

# Local placeholder used when a product has no image file in the repo. It is a
# real committed path, so no card ever 404s or shows a broken-image icon.
PLACEHOLDER_IMG = "images/products/_placeholder.jpg"

# ------------------------------------------------------------ test fixtures
# The pytest suite (and e2e runs) create products for itself: ids jau-stock-*,
# jau-mirror-*, skus JAUSTOCK*, names "Stock Test <id>". They are not shop
# pieces. They kept coming back to the live storefront after the owner deleted
# them because
#   * the suite had been pointed at the production site once (see the guard in
#     tests/e2e.py), so the rows exist in the production products table, and
#   * re-saving a product deliberately clears its durable tombstone
#     (catalog.upsert -> clear_deleted_id), so the next run resurrected them.
# From now on the live environments refuse to save or serve one, and the boot
# pass tombstones whatever is already there.
FIXTURE_ID_PREFIXES = ("jau-stock", "jau-mirror")

# ------------------------------------------------------- permanent removals
# Products the owner deleted for good. They are hard-deleted from Supabase
# (see supabase_store.hard_delete_products and tools/purge_product.py) AND
# removed from the bundled seed, but a stale mirror, an old CSV re-import or
# a restored backup could still put a row back. Matching on id AND slug AND
# folded name here is the last line of defence: merged() drops them on every
# read, and upsert() refuses to save one, so they can never return - across
# redeploys, restarts or re-imports.
PERMANENTLY_REMOVED_IDS = {"wix-002"}
PERMANENTLY_REMOVED_SLUGS = {"100l-storage-bag"}
PERMANENTLY_REMOVED_NAMES = {"100lstoragebag"}


def _fold_name(value):
    return "".join(c for c in str(value or "").lower() if c.isalnum())


def is_permanently_removed(product):
    """True for a product the owner deleted for good (never re-servable)."""
    p = dict(product or {})
    if str(p.get("id") or "").strip() in PERMANENTLY_REMOVED_IDS:
        return True
    if str(p.get("legacyId") or "").strip() in PERMANENTLY_REMOVED_IDS:
        return True
    if str(p.get("slug") or "").strip().lower() in PERMANENTLY_REMOVED_SLUGS:
        return True
    return _fold_name(p.get("name")) in PERMANENTLY_REMOVED_NAMES
FIXTURE_SKU_PREFIX = "JAUSTOCK"
FIXTURE_NAME_PREFIX = "stock test"

# Prices the shop shows are entered in Naira and converted at the house rate.
# Naira is the exact base currency; the CFA figure is rounded UP to a clean
# 50/100 step by currency.to_cfa so no odd amount ever reaches a shopper.
from currency import NGN_TO_CFA, round_cfa, to_cfa  # noqa: F401

# Every field an admin-edited product may carry (with sensible defaults).
BASE_FIELDS = (
    "id", "sku", "slug", "name", "nameFr", "category", "priceCfa", "compareCfa",
    "priceNgn", "compareNgn", "image", "images", "description", "descriptionFr",
    "stock", "badge", "featured", "online", "colors", "options", "optionPrices",
    "optionCompareAt", "dimensions", "supplierId", "supplierSku",
    "optionSupplierSku", "optionSku", "reviews", "enableCustomNote",
    "customNotePrompt",
)

# Historical constant, kept for backward-compatible imports. Supplier URLs are
# owner-entered, but the in-process supplier_watchdog can now read those URLs
# in bounded batches and mirror matched variant availability.
KNOWN_SUPPLIERS = ("splendall",)


def _seed_candidates():
    return [
        os.path.join(ROOT, "data", "seed.json"),
        os.path.join(ROOT, "seed.json"),
    ]


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _seed_products():
    """The base catalogue exactly as shipped, before any admin edit."""
    for cand in _seed_candidates():
        data = _read_json(cand, None)
        if isinstance(data, list) and data:
            return data
    return []


def _file_exists(path):
    """True when a repo-relative asset path resolves to a real file.

    External URLs (http/https/data:/blob:) are never treated as a repository
    file - the site does not fetch product photos from anywhere but its own
    committed assets. Root-relative (/...) paths are accepted because they are
    served by the static layer of this origin.
    """
    if not path:
        return False
    if path.startswith(("http://", "https://", "data:", "blob:")):
        return False
    if path.startswith("/"):
        return True                      # root-relative, served from this origin
    candidate = os.path.join(ROOT, path)
    return os.path.isfile(candidate)


def _is_placeholder_path(path):
    """True for the branded 'PHOTO COMING SOON' card, whatever folder it is in.

    The placeholder is a legitimate final fallback, but it must never outrank a
    real photo: rows saved before the admin editor dropped placeholder tiles
    kept the placeholder in the cover slot, so a freshly added photo sat in the
    gallery while the card still said "PHOTO COMING SOON" (and stayed that way
    after every save).
    """
    return "_placeholder" in os.path.basename(str(path or "")).lower()


def _real_photo(entries):
    """The first entry of ``entries`` that is a real photo path (not the
    branded placeholder, not a foreign host, not empty)."""
    try:
        import storage as _storage
    except Exception:                                   # pragma: no cover
        _storage = None
    for entry in entries or []:
        s = str(entry or "").strip()
        if not s or _is_placeholder_path(s):
            continue
        if s.startswith(("http://", "https://", "data:", "blob:")):
            # only OUR bucket URL counts as ours; any other host is dropped
            if _storage is not None and _storage.own_upload_path(s):
                return s
            # Supabase Storage URLs are authoritative product assets. Keep an
            # absolute URL when it belongs to the configured project; older
            # rows may be readable even when the local upload adapter is not
            # configured (for example during a rolling deploy).
            base = (getattr(Config, "SUPABASE_URL", "") or "").rstrip("/")
            if base and s.startswith(base + "/storage/v1/object/public/"):
                return s
            continue
        return s
    return ""


def is_test_fixture(product):
    """True for a product the test suite created for itself.

    Matched on the id (jau-stock-*, jau-mirror-*), the sku (JAUSTOCK*) or the
    auto-generated name ("Stock Test <id>") - whichever the row carries. Never
    a merchandising decision: no shop piece is named or numbered this way.
    """
    p = dict(product or {})
    pid = str(p.get("id") or "").strip().lower()
    if any(pid.startswith(prefix) for prefix in FIXTURE_ID_PREFIXES):
        return True
    sku = str(p.get("sku") or "").strip().upper()
    if sku.startswith(FIXTURE_SKU_PREFIX):
        return True
    return str(p.get("name") or "").strip().lower().startswith(FIXTURE_NAME_PREFIX)


def _fixture_guard_active():
    """The fixture guard protects the live shop, not the suite that makes them.

    Under FLASK_ENV=testing the fixtures are the suite's own data (filters,
    dedupe, stock and catalogue simulators rely on them), so the guard is off
    there; every other environment - production, staging, local dev - must
    never serve or re-create one.
    """
    return str(getattr(Config, "ENV", "") or "") != "testing"


def _own_upload_path(path):
    """The same-origin /uploads/<key> for one of our stored photos, or ""."""
    try:
        import storage as _storage
        return _storage.own_upload_path(path)
    except Exception:                                   # pragma: no cover
        return ""


def resolve_image(product):
    """Guarantee a renderable image for one product.

    Returns a product whose `image` is a path the browser can actually show,
    using only the repository's own committed assets (never a third-party /
    Wix photo):

    * If the product has a committed repo photo (images/products/x.jpg that
      exists on disk) -> keep that repository path.
    * Else -> the committed branded placeholder repo path (never a 404).

    The placeholder path is also preserved on `placeholderImage` so the
    frontend `onerror` handler can swap to it if a photo ever fails.

    Every rewrite of the cover below also rewrites ``image_url`` to the same
    value. The two columns are one logical field: letting ``image_url`` keep
    the pre-resolution value is how a promoted (or replaced) photo used to
    be silently un-done by the NEXT save, whose payload carried the stale
    alias back in.
    """
    p = dict(product or {})
    img = p.get("image") or ""
    # A branded placeholder must never outrank a real photo this row already
    # holds (see _is_placeholder_path): promote the gallery's first real photo
    # into the cover slot and drop the placeholder from the gallery.
    if not str(img).strip() or _is_placeholder_path(img):
        better = _real_photo(p.get("images") or [])
        if better:
            img = better
            p["image"] = better
            p["image_url"] = better
    if _is_placeholder_path(img):
        p["images"] = [g for g in (p.get("images") or []) if not _is_placeholder_path(g)]
    try:
        import storage as _storage
        own = _storage.own_upload_path(img)
    except Exception:
        own = ""
    if own:
        # Keep complete URLs in production; legacy tests/static preview use the
        # same-origin compatibility route only in testing.
        resolved = own if __import__("config").Config.ENV == "testing" else img
        p["image"] = resolved
        p["image_url"] = resolved
        p["placeholderImage"] = PLACEHOLDER_IMG
        p["usesPlaceholder"] = False
        gal = []
        for g in (p.get("images") or []):
            g = str(g or "")
            if not g:
                continue
            if g.startswith(("http://", "https://", "data:", "blob:")):
                # URL-shaped entries: only OUR bucket becomes a same-origin
                # link; a foreign host (and data:/blob:) is dropped.
                own = _storage.own_upload_path(g)
                if own:
                    gal.append(own if __import__("config").Config.ENV == "testing" else g)
            else:
                # a committed repo path (or /uploads/ link) rides along untouched
                gal.append(g)
        p["images"] = gal
        return p
    # Strip any third-party / Wix URL that may still be present in the data.
    if img.startswith(("http://", "https://", "data:", "blob:")):
        img = ""
    p.pop("imageUrl", None)
    p.pop("usesRemoteImage", None)
    # A committed repo photo wins (the user asked to link repository paths).
    if _is_local(img) and _file_exists(img):
        p["image"] = img
        p["image_url"] = img
        p["placeholderImage"] = PLACEHOLDER_IMG
        p["usesPlaceholder"] = False
        return p
    # A matching committed photo (same slug / alt-<slug>) also wins. This is
    # the auto-wire that makes the owner's collected root photos appear on
    # their products without manually editing every product.
    for candidate in photo_repair_candidates(p):
        if _file_exists(candidate):
            p["image"] = candidate
            p["image_url"] = candidate
            p["placeholderImage"] = PLACEHOLDER_IMG
            p["usesPlaceholder"] = False
            return p
    # No usable local file: show the committed branded placeholder.
    p["image"] = PLACEHOLDER_IMG
    p["image_url"] = PLACEHOLDER_IMG
    p["placeholderImage"] = PLACEHOLDER_IMG
    p["usesPlaceholder"] = True
    return p


def _is_local(path):
    """True for a repo-relative asset path (not external / root-relative)."""
    return bool(path) and not path.startswith(("http://", "https://", "/", "data:", "blob:"))


_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _image_stem(path):
    """The filename's stem, e.g. ``smartwatch-with-game-pad.jpg`` -> ``...``."""
    base = os.path.basename(str(path or ""))
    lower = base.lower()
    for ext in _IMAGE_EXTS:
        if lower.endswith(ext):
            return base[: -len(ext)]
    return os.path.splitext(base)[0]


def _candidate_score(slug, stem):
    """A simple similarity used to wire committed photos to products.

    Prefers identical slug/basename matches, then alt-<slug> copies (kept when
    a root photo had the same name but different bytes), then stem/slug
    containment. Returns -1 when the two are not related enough to trust.
    """
    slug = (slug or "").lower()
    stem = (stem or "").lower()
    if not slug or not stem:
        return -1
    if slug == stem:
        return 120
    if stem.startswith("alt-") and slug == stem[4:]:
        return 110
    if stem in slug or slug in stem:
        # short containment would match tiny fragments (e.g. "bag" in "battery")
        shorter, longer = (stem, slug) if len(stem) <= len(slug) else (slug, stem)
        if len(shorter) >= 4:
            return 100 - abs(len(slug) - len(stem))
    return -1


def photo_repair_candidates(product):
    """Committed photos that could belong to a product.

    Returns repo-relative ``images/products/...`` paths whose filename relates
    to the product slug (or its ``alt-`` duplicate copy). Used by
    :func:`resolve_image` so owner photos uploaded loose at the repo root and
    collected by ``collect_uploaded_photos.py`` automatically appear on the
    matching products.
    """
    p = dict(product or {})
    slug = (p.get("slug") or _slugify(p.get("name") or "")).strip().lower()
    if not slug:
        return []
    folder = os.path.join(ROOT, "images", "products")
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(_IMAGE_EXTS)]
    except OSError:
        return []
    scored = []
    for name in names:
        stem = _image_stem(name)
        score = _candidate_score(slug, stem)
        if score < 0:
            continue
        rel = os.path.join("images", "products", name)
        scored.append((score, len(name), rel))
    # Highest score first, shortest name first for ties.
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [rel for _s, _n, rel in scored]


def primary_image(product):
    """The ONE photo that stands for a product in a list, an order line or an
    email thumbnail.

    A short, stable rule so the storefront card, the admin order row, the
    receipt and the confirmation email can never disagree about which photo
    represents a line: the cover (``image``) first, then the same field under
    its legacy aliases, then the first real entry of the gallery. Returns ""
    when the row genuinely has no photo - callers fall back to their own
    branded placeholder rather than inventing a path here.
    """
    p = dict(product or {})
    for key in ("image", "image_url", "imageUrl"):
        value = str(p.get(key) or "").strip()
        if value:
            return value
    gallery = p.get("images")
    if isinstance(gallery, (list, tuple)):
        for entry in gallery:
            value = str(entry or "").strip()
            if value:
                return value
    return ""


def resolve_images(products):
    """Apply resolve_image to a list of products."""
    return [resolve_image(p) for p in (products or [])]


def _sync_repo_async():
    """Best-effort, non-blocking sync of the repository data state.

    Runs after an admin product write so js/products-data.js (and the repo copy
    of data/catalog.json) reflects the new catalogue immediately. Never raises
    and never delays the product save - the shop must not be blocked by a git
    operation. The single gate in repo_sync.repo_sync_blocked_reason() decides
    whether this instance may publish at all: only a deployed production or
    staging instance with REPO_SYNC_ON_WRITE on, outside pytest, gets through -
    a development preview or a test run is silently skipped here, the nightly
    backup reports the same reason, and the manual "Sync to GitHub" button
    answers 409 with it.
    """
    try:
        import repo_sync
    except Exception:
        return
    reason = repo_sync.repo_sync_blocked_reason()
    if reason:
        return  # a dev preview / test run must never publish catalogue state
    # Import lazily so repo_sync (which imports catalog) is only loaded here,
    # and to avoid a circular import at module load time.

    def _run():
        try:
            # Re-ask the gate at EXECUTION time, not just at spawn time. The
            # daemon thread can be scheduled long after the request (or the
            # test) that spawned it returned, and the guards may since have
            # come back - a pytest run that temporarily lifted the blockers,
            # a preview that flipped to production for one write. When the
            # thread actually runs, the CURRENT process state decides; a late
            # thread must never regenerate the repository files against
            # guards that are back in place.
            if repo_sync.repo_sync_blocked_reason():
                return
            repo_sync.regenerate(commit=True, push=True)
        except Exception:
            pass  # sync is best-effort; a failure must never break a save

    if threading is not None:
        try:
            threading.Thread(target=_run, daemon=True).start()
            return
        except Exception:
            pass
    _run()


def _supabase_products():
    """Admin products from Supabase, or None when not configured / reachable."""
    from supabase_store import products_table_rows
    return products_table_rows()


def base_products():
    """The seed products. Never includes admin edits or deletions.

    The F CFA figures are ceilinged here too: GET /api/products serves this
    list directly and bypasses merged() entirely, so without the cleaning a
    seed row with an odd CFA price (wix-144 = 325) would reach the shopper
    unrounded on that route.
    """
    return [_clean_cfa_prices(p) for p in _seed_products()]


# --------------------------------------------------------------- local overrides
def _norm_filename(path):
    """Point CATALOG_FILE at a file that already exists.

    The repo ships flat (catalog.json at the root) but Config.CATALOG_PATH
    points at data/catalog.json. Prefer the existing flat file for the default
    data path so admin edits are never written to a fresh location the shop
    doesn't read. Never remap an explicit path (such as a test /tmp path) -
    those are always used exactly as given.
    """
    if os.path.isfile(path):
        return path
    if os.path.dirname(path).rstrip("/").endswith("data"):
        base = os.path.basename(path)
        flat = os.path.join(ROOT, base)
        if os.path.isfile(flat):
            return flat
    return path


def _load_overrides():
    """Return the overrides dict, falling back to the .bak on a corrupt file.

    Never returns an empty products list from a corrupt file - that would
    silently empty the shop. Returns (data, path_used).
    """
    path = _norm_filename(CATALOG_FILE)
    data = _read_json(path, None)
    if isinstance(data, dict) and isinstance(data.get("products"), list):
        return data, path
    bak = path + ".bak"
    data = _read_json(bak, None)
    if isinstance(data, dict) and isinstance(data.get("products"), list):
        return data, path
    return {"products": [], "deleted": [], "updatedAt": "", "updatedBy": ""}, path


def overrides():
    """The current admin overrides ({products, deleted, updatedAt, updatedBy})."""
    data, _path = _load_overrides()
    return data


@contextlib.contextmanager
def _catalog_lock(path):
    """Serialise read-modify-write across processes (gunicorn workers).

    Uses the catalogue's own ``<path>.lock`` file with an advisory POSIX lock.
    A ``threading.Lock`` is invisible to other workers; this one is not. Falls
    back to no-op on non-POSIX platforms so the site still runs.
    """
    lock_path = path + ".lock"
    try:
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        with open(lock_path, "a+") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError:
        yield                     # never fail a save because of locking


def _write_overrides(data, path=None):
    """Write overrides atomically (write temp, then keep a .bak)."""
    path = path or _norm_filename(CATALOG_FILE)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.isfile(path):
        try:
            with open(path, "rb") as fh:
                with open(path + ".bak", "wb") as bak:
                    bak.write(fh.read())
        except OSError:
            pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ------------------------------------------------------------------- cleaning
def _derive_cfa(product):
    """priceCfa is derived from priceNgn at the house rate when not explicit.

    Prices can never be negative: a negative value is treated as "not set"
    (and a negative NGN price therefore never ships to the storefront).
    """
    ngn = product.get("priceNgn")
    cfa = product.get("priceCfa")
    try:
        ngn = int(float(ngn)) if ngn is not None else None
    except (TypeError, ValueError):
        ngn = None
    try:
        cfa = int(float(cfa)) if cfa is not None else None
    except (TypeError, ValueError):
        cfa = None
    if ngn is not None and ngn < 0:
        ngn = 0
    if cfa is not None and cfa < 0:
        cfa = 0
    if ngn and ngn > 0 and (cfa is None or cfa <= 0):
        # Naira is the exact base currency; the converted CFA figure is
        # rounded UP to a clean 50/100 step.
        cfa = to_cfa(ngn)
    elif cfa:
        # An explicitly priced CFA amount still has to be a clean step.
        cfa = round_cfa(cfa)
    return ngn, cfa


def _slugify(name):
    slug = (name or "").lower().replace("&", "and").replace("/", " ")
    for ch in ".,()'\"":
        slug = slug.replace(ch, "")
    slug = "-".join(slug.split())
    return re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")


_IMPORT_SLUG_RE = re.compile(
    r"^(?:wix|shopify|import)(?:-|$)|^(?:product|item)-\d+$|^[0-9a-f]{24,}$", re.I)


def public_slug(product):
    """A clean, readable public URL identity; never an imported Wix ID.

    Internal product ids and legacyId aliases remain untouched and continue to
    resolve old links. Imported slugs are replaced with the product's readable
    title slug at the public boundary (and on the next normalized save).
    """
    p = product or {}
    raw = re.sub(r"[^a-z0-9-]+", "-", str(p.get("slug") or "").lower()).strip("-")
    if not raw or _IMPORT_SLUG_RE.search(raw):
        raw = _slugify(str(p.get("name") or ""))
    if not raw:
        raw = "jau-product"
    return raw


_SLUG_TRIES = 40


def _free_slug(slug, pid, taken):
    """Keep a slug unique across the live catalogue.

    The slug is a product's URL identity (js/store.js resolves a product by
    id OR slug) and catalogue dedupe matches on it, so two different pieces
    that happen to share a name used to make the second invisible on the shop
    while both saved fine. A collision becomes -2, -3, ... instead. `taken`
    is the set of other products' slugs, lowercased; empty slugs and an
    exhausted suffix run fall back to the id, which is unique.
    """
    slug = str(slug or "").strip().lower()
    if not slug or slug not in taken:
        return slug
    for n in range(2, 2 + _SLUG_TRIES):
        cand = f"{slug}-{n}"
        if cand not in taken:
            return cand
    # Never fall back to an imported internal id in a public slug, even in
    # the pathological case of forty same-title collisions.
    suffix = hashlib.sha1(str(pid or slug).encode("utf-8")).hexdigest()[:8]
    return f"{slug}-jau-{suffix}"


def normalize(product):
    """Clean an incoming product into a safe, complete shape.

    Emits the canonical Supabase columns (image_url, stock_quantity,
    updated_at) AND the legacy aliases (image, stock) so one row serves the
    schema, the local test/dev path and the storefront. Prices are
    non-negative; stock is a non-negative integer.

    The French copy (nameFr, descriptionFr) is folded from either spelling -
    the admin portal posts camelCase, mirror/import rows use name_fr and
    description_fr - and stays "" when unwritten, which tells the storefront
    to fall back to English rather than to blank.
    """
    import security as sec
    product = dict(product or {})
    name = sec.clean(product.get("name"), 200)
    if not name:
        return None
    # Per-product bulk discount: both values or neither (a lone half-config
    # pair can never fire and would only confuse the admin editor).
    _bulk_qty = _clean_bulk_qty(
        product.get("bulkQty"), product.get("bulk_qty"),
        product.get("bulkQuantity"), product.get("bulk_quantity"),
        product.get("bulkDiscountQty"), product.get("bulk_discount_qty"))
    _bulk_pct = _clean_bulk_percent(
        product.get("bulkPercent"), product.get("bulk_percent"),
        product.get("bulkDiscountPercent"), product.get("bulk_discount_percent"))
    if not (_bulk_qty and _bulk_pct):
        _bulk_qty = None
        _bulk_pct = None
    raw_id = sec.clean(product.get("id"), 64)
    pid = raw_id or ("jau-" + secrets.token_hex(5))
    ngn, cfa = _derive_cfa(product)
    compare_cfa = _int_or_none(product.get("compareCfa") or product.get("compare_cfa"))
    compare_ngn = _int_or_none(
        product.get("compareNgn") or product.get("compare_ngn")
        or product.get("compareAtPrice") or product.get("compare_at_price")
        or product.get("strikeThroughPrice") or product.get("strike_through_price"))
    compare_cfa = max(0, compare_cfa) if compare_cfa is not None else None
    compare_ngn = max(0, compare_ngn) if compare_ngn is not None else None
    # "Was" prices follow the same rule: a converted figure is rounded up,
    # and an explicit CFA figure is snapped to a clean step.
    if compare_ngn and not compare_cfa:
        compare_cfa = to_cfa(compare_ngn)
    elif compare_cfa:
        compare_cfa = round_cfa(compare_cfa)
    # Cover-image precedence. Every writer in the app (the admin product
    # editor, resolve_image, the photo-repair paths) treats ``image`` as the
    # intended cover; ``image_url`` is only the Supabase-canonical ALIAS of
    # the same value and is rewritten to match on every save below.
    #
    # Reading ``image_url`` FIRST was the "image replacement does not save"
    # bug: the admin editor posts ``{...existing, image: <new upload>}``, so
    # the payload carries the row's STALE ``image_url`` beside the fresh
    # ``image`` - and the stale alias silently won, leaving image and
    # image_url pinned to the old photo while the new upload only ever
    # reached the gallery. ``image`` now wins whenever it carries a real
    # photo; ``image_url`` is honoured only when ``image`` is blank (legacy
    # mirror/import rows) or holds nothing but the branded placeholder.
    image = sec.safe_url(product.get("image") or "")
    image_url = sec.safe_url(product.get("image_url") or "")
    if not image or (_is_placeholder_path(image) and image_url
                     and not _is_placeholder_path(image_url)):
        image = image_url or image
    images = [sec.safe_url(i) for i in (product.get("images") or []) if sec.safe_url(i)]
    # The branded placeholder is a fallback, never a photo: a row that carries
    # one in the cover slot while holding a real photo in its gallery (exactly
    # what the old admin editor produced when the owner added a photo to a
    # "photo coming soon" product) is normalised to the real photo here, so it
    # survives the save, the deploy and every device.
    real_photos = [i for i in images if not _is_placeholder_path(i)]
    if real_photos:
        images = real_photos
        if not image or _is_placeholder_path(image):
            image = real_photos[0]
    # stock_quantity is canonical. If it is present but NULL/blank/invalid,
    # that is zero stock - never fall back to a stale positive `stock` alias.
    # The legacy alias is read only for old rows that have no canonical key.
    stock_source = (product.get("stock_quantity") if "stock_quantity" in product
                    else product.get("stock") if "stock" in product else 0)
    stock_qty = sec.clean_int(stock_source, 0, 0, 10**7)
    option_stock = _clean_option_stock(product.get("optionStock"))
    if option_stock:
        # Per-variant stock is the truth for a variant product and the total
        # is the sum of its variants (the admin editor has always computed it
        # that way, but only client-side). Enforcing it server-side keeps
        # stock / stock_quantity and optionStock from ever disagreeing: a row
        # that said stock=24 while every variant was 0 still showed
        # "In Stock" on the storefront and stayed orderable.
        stock_qty = sum(int(v or 0) for v in option_stock.values())
    # ... UNLESS the admin explicitly switched the whole product off. An
    # "Out of stock" toggle (stockStatus / stock_status in the admin form,
    # is_in_stock / in_stock in an API write) is a final decision: it zeroes
    # the product quantity AND every per-variant quantity, so stale variant
    # numbers travelling in the same payload can never re-sum the row back
    # to "in stock". This was the "out of stock does not save" bug: the
    # editor wrote stock=0, the variant sum silently reverted it, and the
    # storefront kept showing the product as orderable.
    if _explicit_out_of_stock(product):
        stock_qty = 0
        if option_stock:
            option_stock = {k: 0 for k in option_stock}

    out = {
        "id": pid,
        "sku": sec.valid_sku(product.get("sku") or ""),
        "slug": public_slug({"slug": sec.safe_url(product.get("slug") or ""),
                             "name": name, "id": pid}),
        "name": name,
        "nameFr": sec.clean(product.get("nameFr") or product.get("name_fr"), 200),
        "category": sec.clean(product.get("category"), 40),
        "priceCfa": cfa if cfa is not None else 0,
        "compareCfa": compare_cfa,
        "priceNgn": ngn if ngn is not None else 0,
        "compareNgn": compare_ngn,
        "image": image,
        "image_url": image,
        "images": images,
        "description": sec.clean(product.get("description"), 2000),
        # French copy is optional: an empty string means "show English", never
        # "show nothing". Both spellings are accepted because the admin form
        # posts camelCase while older mirror/import rows use snake_case, and a
        # silently dropped French description looks like a translation bug to
        # the shopper rather than an unwritten field.
        "descriptionFr": sec.clean(
            product.get("descriptionFr") or product.get("description_fr"), 2000),
        # Free-text physical dimensions (e.g. "30 x 20 x 10 cm"). Optional; a
        # blank value is simply omitted from the storefront and WhatsApp
        # catalog caption. Accepts camelCase (admin form) and snake_case
        # (mirror/import) spellings.
        "dimensions": sec.clean(
            product.get("dimensions") or product.get("dimension"), 160),
        # Optional per-product customer prompt. The admin chooses which
        # products expose the note field and can edit the prompt for each.
        "enableCustomNote": bool(product.get("enableCustomNote")
                                 or product.get("customNoteEnabled")
                                 or product.get("allowCustomNote")),
        "customNotePrompt": sec.clean(
            product.get("customNotePrompt") or product.get("custom_note_prompt"), 160),
        "stock": stock_qty,
        "stock_quantity": stock_qty,
        "badge": sec.clean(product.get("badge"), 20),
        "featured": bool(product.get("featured", False)),
        "online": product.get("online", True) is not False,
        "colors": list(product.get("colors") or []),
        "options": list(product.get("options") or []),
        "optionStock": option_stock,
        "optionPrices": _clean_option_prices(
            product.get("optionPrices") or product.get("option_prices")
            or product.get("variantPrices") or product.get("variant_prices")
            or product.get("priceOverrides") or product.get("price_overrides")),
        # Per-option "was" (strike-through) prices, mirroring optionPrices.
        # A variant with an entry here shows the original price crossed out
        # next to its override; a blank entry inherits the product compareNgn.
        "optionCompareAt": _clean_option_prices(
            product.get("optionCompareAt") or product.get("option_compare_at")
            or product.get("variantCompareAt") or product.get("variant_compare_at")
            or product.get("variantComparePrices") or product.get("variant_compare_prices")),
        # Optional per-product bulk discount: order MORE than bulkQty units of
        # this product and bulkPercent is taken off its unit price at
        # checkout. Both values or neither - a lone percentage with no
        # threshold can never fire and would only confuse the admin editor.
        "bulkQty": _bulk_qty,
        "bulkPercent": _bulk_pct,
        # The id this row had before it was given a canonical one, so old
        # product links / order lines / reviews keep resolving. See
        # product_index().
        "legacyId": _clean_legacy_id(product.get("legacyId"), pid),
        # Plain, owner-entered supplier reference fields. The consolidated
        # in-process supplier watchdog may read a URL here to mirror variant
        # availability; it never guesses or overwrites the URL itself.
        "supplierId": sec.clean(
            product.get("supplierId") or product.get("supplier_id"), 40).lower(),
        "supplierSku": sec.clean(
            product.get("supplierSku") or product.get("supplier_sku")
            or product.get("supplierUrl") or product.get("supplier_url")
            or product.get("supplierURL"), 500),
        # Per-option supplier reference links (component -> URL). The
        # in-process supplier watchdog may use them to augment product-level
        # variant matching; blank entries are left alone.
        "optionSupplierSku": _clean_option_supplier_sku(
            product.get("optionSupplierSku") or product.get("option_supplier_sku")
            or product.get("optionSupplierUrls") or product.get("option_supplier_urls")
            or product.get("variantSupplierUrls") or product.get("variant_supplier_urls")),
        "optionSku": _clean_option_sku(
            product.get("optionSku") or product.get("option_sku")
            or product.get("optionSkus") or product.get("option_skus")
            or product.get("variantSku") or product.get("variant_sku")
            or product.get("variantSkus") or product.get("variant_skus")),
        "reviews": _clean_admin_reviews(product.get("reviews") or product.get("customerReviews") or product.get("customer_reviews")),
        "updated_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    return out


def _clean_legacy_id(raw, pid):
    """A trimmed legacyId alias, or None when there is none.

    Must be None (not "") when absent: the column carries a partial UNIQUE
    index, so a shared empty string would collide across every product that
    has no alias. It also must never equal the product's own id.
    """
    import security as sec
    val = sec.clean(raw, 64)
    if not val or val == pid:
        return None
    return val


def stock_of(product):
    """The authoritative integer stock of one product row.

    ONE precedence for every reader in the app (display, checkout
    validation, reservation, admin): the canonical ``stock_quantity``
    column when the row carries it, the legacy ``stock`` alias otherwise
    (seed rows and older mirrors). Before this helper existed the readers
    were split - the storefront displayed ``stock`` while the reservation
    RPC guarded ``stock_quantity`` - so a row whose two spellings
    disagreed (an import that left stock_quantity at the table default 0)
    showed "In Stock" and then failed checkout, or the reverse. Never
    negative; a non-numeric value reads as 0.
    """
    p = product if isinstance(product, dict) else {}
    # Presence of the canonical column is meaningful: NULL/blank/invalid is
    # an explicit zero, not permission to fall back to a stale legacy alias.
    # Only old seed/mirror rows that do not carry stock_quantity at all may
    # read their legacy `stock` field.
    raw = p.get("stock_quantity") if "stock_quantity" in p else p.get("stock")
    try:
        return max(0, int(str(raw).strip())) if raw is not None and str(raw).strip() else 0
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------- stock guardrails ---
# ONE derived availability for a stored row, used by the background sweep and
# by anything that has to answer "is this sellable?" from the quantities
# themselves. There is no persisted status column to go stale: the quantity is
# the truth, and an explicit whole-product OFF still wins over it.
def derived_stock_status(product):
    """``"out"`` / ``"in"`` for one row, derived from its own quantities.

    Mirrors api._public_product (the storefront's answer) and the /api/stock
    screen, so the database row, the admin view and the shop cannot disagree.
    A row whose EVERY variant is zero is out; a row with any variant left is
    in. A deliberate OFF (stockStatus "out", is_in_stock false) is out even if
    a stale number travels beside it.
    """
    p = product if isinstance(product, dict) else {}
    if _explicit_out_of_stock(p):
        return "out"
    os_map = p.get("optionStock")
    if isinstance(os_map, dict) and os_map:
        total = 0
        for value in os_map.values():
            try:
                total += max(0, int(str(value).strip() or 0))
            except (TypeError, ValueError):
                continue                      # a junk variant counts as zero
        return "in" if total > 0 else "out"
    return "in" if stock_of(p) > 0 else "out"


def derived_variant_statuses(product):
    """``{"Red": "out", "Black": "in"}`` for a variant-tracked row, else {}.

    The same map the storefront receives (option_stock_status) so a variant
    that just sold its last unit reads "out" for every reader, not only for
    the one that happened to recompute it.
    """
    p = product if isinstance(product, dict) else {}
    os_map = p.get("optionStock")
    if not isinstance(os_map, dict) or not os_map:
        return {}
    out = {}
    for key, value in os_map.items():
        try:
            qty = max(0, int(str(value).strip() or 0))
        except (TypeError, ValueError):
            qty = 0
        out[str(key)] = "in" if qty > 0 else "out"
    return out


def stock_guardrail_fix(product):
    """``(row, changed_fields)`` repairing only the invariants this app owns.

    Deliberately NOT repaired: a row whose legacy ``stock`` alias says a
    positive number while the canonical ``stock_quantity`` says 0 (or the
    reverse). That disagreement is ambiguous - on this shop it is usually a
    table created with ``stock_quantity default 0`` before the real numbers
    were imported into ``stock`` - and writing EITHER value over the other can
    destroy the only copy of the truth. Those rows are reported by the sweep
    instead (see ``stock_guardrail_report``), never rewritten.

    What IS repaired, because the app itself owns these and every write path
    already enforces them (``normalize``), so repairing only ever makes a row
    agree with what the shop is ALREADY serving:

      * ``optionStock`` values are non-negative integers (junk reads as 0),
      * a variant-tracked row's total IS the sum of its variants - the classic
        "shows In Stock, then fails checkout" drift.

    A row that has been deliberately switched off (``stockStatus: "out"``) is
    ALSO left exactly as written. Its availability is already derived as
    "out" on every read, and zeroing the numbers the owner typed would throw
    away the quantities she needs the moment she switches the product back
    on - the same data-loss trap as the stock-column clash.

    Returns the row unchanged with ``[]`` when it is already consistent, so a
    sweep over a healthy catalogue writes nothing at all.
    """
    p = dict(product or {})
    changed = []
    os_raw = p.get("optionStock")
    if isinstance(os_raw, dict) and os_raw:
        os_map = _clean_option_stock(os_raw)
        if os_map != os_raw:
            changed.append("optionStock")
        total = sum(int(v or 0) for v in os_map.values())
        for field in ("stock", "stock_quantity"):
            try:
                current = int(str(p.get(field)).strip())
            except (TypeError, ValueError):
                current = None
            if current != total:
                changed.append(field)
        if changed:
            p["optionStock"] = os_map
            p["stock"] = total
            p["stock_quantity"] = total
    return p, changed


def stock_guardrail_report():
    """Rows whose two stock spellings disagree. Report only; never rewrite.

    ``{"checked": n, "disagreements": [{"id", "stock", "stock_quantity",
    "status"}, ...]}``

    Reads the RAW backend rows, not ``merged()``: normalization collapses
    ``stock_quantity`` onto the ``stock`` column, so a merged row can no longer
    show the disagreement this is looking for. Both values are integers and
    they say different things, and which one the shop means is an operator
    decision - so the sweep surfaces them (health and stock screens) instead of
    guessing. This is the one class of drift a background pass must never
    "fix" silently: on this shop it is usually a table created with
    ``stock_quantity default 0`` before the real numbers were imported into
    ``stock``, and writing either value over the other destroys the only copy
    of the truth.
    """
    rows = []
    try:
        from supabase_store import products_table_rows
        live = products_table_rows()
        if isinstance(live, list):
            rows = live
    except Exception:
        rows = []
    if not rows:
        try:
            rows = (overrides() or {}).get("products") or []
        except Exception:
            rows = []
    out = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        if isinstance(row.get("optionStock"), dict) and row.get("optionStock"):
            continue                       # variant rows: the sum above is truth
        try:
            alias = int(str(row.get("stock")).strip())
            canon = int(str(row.get("stock_quantity")).strip())
        except (TypeError, ValueError):
            continue                       # a missing spelling is not a clash
        if alias == canon or alias < 0 or canon < 0:
            continue
        out.append({"id": str(row.get("id")), "stock": alias,
                    "stock_quantity": canon,
                    "status": derived_stock_status(row)})
    return {"checked": len(rows), "disagreements": out}


def stock_guardrail_sweep(actor="stock-guardrail", limit=0):
    """The BACKGROUND half of the stock guardrail. Never raises.

    The write paths fix the rows they touch; this pass catches the ones they
    did not - a row edited straight in the database, an import, an older
    mirror, a supplier write that landed outside the app - and brings them
    back to the invariants ``normalize`` enforces, so "In Stock" can never
    survive a row whose variants are all zero.

    It is cheap when there is nothing to do (one read, zero writes) and it
    writes ONLY rows that violate an invariant, so running it on a timer never
    churns ``updated_at`` across the catalogue. Rows whose two stock spellings
    merely disagree are counted in ``disagreements`` and left alone - see
    ``stock_guardrail_report``.

    Returns ``{"checked", "fixed", "ids", "disagreements", "disagreementIds",
    "skipped"}``.
    """
    report = {"checked": 0, "fixed": 0, "ids": [], "skipped": "",
              "disagreements": 0, "disagreementIds": []}
    try:
        rows = merged(include_hidden=True)
    except Exception as exc:                  # a read failure is never a repair
        report["skipped"] = f"catalogue unreadable: {exc}"
        return report
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("id")]
    if limit:
        rows = rows[:limit]
    for row in rows:
        report["checked"] += 1
        fixed, changed = stock_guardrail_fix(row)
        if not changed:
            continue
        try:
            saved = upsert(fixed, actor=actor)
        except Exception as exc:
            report["skipped"] = f"{row.get('id')}: {exc}"
            continue
        product = saved[0] if isinstance(saved, tuple) and saved else None
        action = saved[1] if isinstance(saved, tuple) and len(saved) > 1 else None
        mirrored = saved[2] if isinstance(saved, tuple) and len(saved) > 2 else True
        if product and action not in ("error", "rejected", "permanently-removed",
                                      "test-fixture") and mirrored is not False:
            report["fixed"] += 1
            report["ids"].append(str(row.get("id")))
        else:
            report["skipped"] = f"{row.get('id')}: not saved ({action})"
    flagged = stock_guardrail_report()
    report["disagreements"] = len(flagged.get("disagreements") or [])
    report["disagreementIds"] = [f["id"] for f in (flagged.get("disagreements") or [])[:20]]
    return report


def _clean_option_stock(raw):
    """Per-option-value stock, e.g. {"Red": 4, "Blue": 0}. The admin editor
    tracks quantity per value of the product's first option, like Wix."""
    import security as sec
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in list(raw.items())[:60]:
        key = sec.clean(k, 80)
        if not key:
            continue
        out[key] = sec.clean_int(v, 0, 0, 10**7) or 0
    return out


_OUT_WORDS = ("out", "sold out", "soldout", "sold-out", "unavailable",
              "false", "0", "no", "off")


def _explicit_out_of_stock(product):
    """True when the payload itself switches the WHOLE product off.

    Recognised spellings: the admin editor's ``stockStatus`` / API clients'
    ``stock_status`` set to "out", or an explicit ``is_in_stock`` /
    ``in_stock`` of false. Anything else - including the fields being absent
    entirely - means "no opinion", and availability then follows the
    quantity rules exactly as before (so plain patch writes and seed rows
    are untouched). Only a deliberate OFF is a hard override.
    """
    p = product if isinstance(product, dict) else {}
    for key in ("stockStatus", "stock_status"):
        raw = p.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        if str(raw).strip().lower() in _OUT_WORDS:
            return True
    for key in ("is_in_stock", "in_stock"):
        raw = p.get(key)
        if raw is None or raw is True:
            continue
        if raw is False:
            return True
        if isinstance(raw, (int, float)) and raw == 0:
            return True
        if not isinstance(raw, (dict, list)) and str(raw).strip().lower() in _OUT_WORDS:
            return True
    return False



def fold_option_value(value):
    """Case/punctuation-insensitive key for matching variant option values.

    The single canonical spelling matcher: the cart line, the optionStock
    map and the Supabase RPC argument are all folded through it, so
    "Colour: Red", "red" and "RED" all address the same stored key.
    """
    return "".join(c.lower() for c in str(value or "") if c.isalnum())


def variant_values(variant):
    """Option values from a cart variant string like "Color: Red · Size: M"."""
    v = str(variant or "")
    if not v or v == "__default__":
        return []
    out = []
    for part in re.split(r"[·;|]", v):
        p = str(part or "").strip()
        if not p:
            continue
        if ":" in p:
            _title, val = p.split(":", 1)
            val = val.strip()
            if val:
                out.append(val)
        else:
            out.append(p)
    return out



def _clean_bulk_qty(*raws):
    """A bulk-discount quantity threshold, or None when not configured.

    Zero (or a negative) means "no discount", never a threshold of 1 -
    clean_int alone would clamp it up and switch the discount on."""
    import security as sec
    for raw in raws:
        if raw is None or raw == "":
            continue
        try:
            if float(raw) <= 0:
                return None
        except (TypeError, ValueError):
            continue
        qty = sec.clean_int(raw, None, 1, 10**6)
        if qty is not None:
            return qty
    return None


def _clean_bulk_percent(*raws):
    """A bulk-discount percentage (1-90), or None when not configured.

    Zero means "no discount" - clean_int alone would clamp it up to 1%."""
    import security as sec
    for raw in raws:
        if raw is None or raw == "":
            continue
        try:
            if float(raw) <= 0:
                return None
        except (TypeError, ValueError):
            continue
        pct = sec.clean_int(raw, None, 1, 90)
        if pct is not None:
            return pct
    return None


def bulk_discount_for(product, quantity):
    """The bulk-discount percentage one product earns at ``quantity``.

    A per-product discount (bulkQty + bulkPercent, set in the admin editor)
    wins when configured; otherwise the shop-wide volume tiers from the
    growth settings apply. The per-product threshold fires when the customer
    orders MORE than bulkQty units of that one product (all its variants
    combined).
    """
    try:
        qty = int(quantity or 0)
    except (TypeError, ValueError):
        return 0
    if qty <= 0:
        return 0
    p = product if isinstance(product, dict) else {}
    threshold = _clean_bulk_qty(p.get("bulkQty"), p.get("bulk_qty"),
                                p.get("bulkQuantity"), p.get("bulk_quantity"),
                                p.get("bulkDiscountQty"), p.get("bulk_discount_qty"))
    percent = _clean_bulk_percent(p.get("bulkPercent"), p.get("bulk_percent"),
                                  p.get("bulkDiscountPercent"), p.get("bulk_discount_percent"))
    if threshold and percent:
        return percent if qty > int(threshold) else 0
    try:
        import growth
        return growth.bulk_discount_percent(qty)
    except Exception:
        return 0



def _clean_option_prices(raw):
    """Per-option prices from dicts or legacy JSON-string mappings."""
    import json
    import security as sec
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in list(raw.items())[:200]:
        label = sec.clean(key, 160)
        price = sec.clean_int(value, None, 0, 10**9)
        if label and price is not None:
            out[label] = price
    return out


def _clean_option_supplier_sku(raw):
    """Per-option supplier reference links, e.g. {"Serum":
    "https://supplier.example/serum", "Shampoo": "https://..."}. Each variant
    option (a distinct component such as Serum / Shampoo / Conditioner) can
    carry its own plain, owner-entered reference URL. The in-process supplier
    watchdog can read these links for stock matching, but blank values are
    dropped so an unlinked option is simply absent."""
    import json
    import security as sec
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in list(raw.items())[:200]:
        label = sec.clean(key, 160)
        # A component may be fulfilled by more than one Splendall listing.
        # Keep the list intact so the sync can audit each URL independently;
        # legacy scalar mappings remain scalar for backwards compatibility.
        values = value if isinstance(value, (list, tuple)) else [value]
        links = []
        for item in values[:20]:
            link = sec.clean(item, 500)
            if link and link not in links:
                links.append(link)
        if label and links:
            out[label] = links if isinstance(value, (list, tuple)) else links[0]
    return out


def _clean_option_sku(raw):
    """Per-option merchant/SKU identifiers. Blank values are dropped."""
    import json
    import security as sec
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in list(raw.items())[:200]:
        label = sec.clean(key, 160)
        sku = sec.clean(value, 120)
        if label and sku:
            out[label] = sku
    return out


def _clean_admin_reviews(raw):
    """Admin-entered display reviews stored with a product row.

    Verified customer reviews still live in product_reviews; this field keeps
    owner-entered notes from the product editor from being only localStorage.
    """
    import json
    import security as sec
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for row in raw[:80]:
        if not isinstance(row, dict):
            continue
        body = sec.clean(row.get("body") if row.get("body") is not None else row.get("note"), 600)
        if not body:
            continue
        name = sec.clean(row.get("name"), 60) or "Customer"
        title = sec.clean(row.get("title"), 120)
        rating = sec.clean_int(row.get("rating") if row.get("rating") is not None else row.get("stars"), 5, 1, 5)
        created = sec.clean(row.get("created_at") or row.get("at"), 40)
        item = {"name": name, "body": body, "rating": rating, "created_at": created}
        if title:
            item["title"] = title
        # Deprecated aliases keep older browser bundles rendering the note.
        item["note"] = body
        item["stars"] = rating
        item["at"] = created
        out.append(item)
    return out


def _int_or_none(v):
    try:
        n = int(float(v)) if v is not None else None
        return n
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------- merged
def _folded_category(cid):
    """Map a (possibly legacy / merged) category id onto its survivor.

    Read-time normalisation that mirrors ``_CATEGORY_FOLD`` so that no product
    ever leaks a category that no longer exists — regardless of whether the row
    came from Supabase, the seed, or the local override file. Unknown or already
    surviving ids are returned unchanged (preserving owner renames).
    """
    cid = str(cid or "").strip()
    return _CATEGORY_FOLD.get(cid.lower(), cid)


def _dedupe_products(primary, secondary):
    """Merge two product lists, keeping every distinct product exactly once.

    ``primary`` (Supabase - the live source of truth) wins. Two rows are the
    SAME product when their ids match, or when they share a slug (or sku)
    AND the same name - that is how a re-created product (saved again with a
    fresh id but the same name and slug) keeps rendering exactly once.

    A slug or sku clash alone NEVER hides another row. The old
    id-then-slug-then-sku match made ~30 seed products invisible whenever a
    mirrored Supabase row of a DIFFERENT product happened to share the slug
    or sku (the admin catalogue count dropped from 258 to 228). Reconciling
    Supabase rows against the seed and against the local overrides is
    therefore id-only in practice; only a name-confirmed slug/sku clash
    still collapses, which is what keeps the seed's own internal collapse
    (a re-created piece renders once) intact.
    """
    def _key(v):
        return str(v or "").strip().lower()

    by_id, by_slug, by_sku = {}, {}, {}
    out = []

    def _place(p):
        pid = str((p or {}).get("id") or "").strip()
        if not pid:
            return False
        name = _key(p.get("name"))
        slug = _key(p.get("slug"))
        sku = _key(p.get("sku"))
        if pid in by_id:
            return False
        # a slug/sku clash only collapses when the names agree too - a
        # different product must never be hidden by a shared slug or sku
        if (slug and (slug, name) in by_slug) or (sku and (sku, name) in by_sku):
            return False
        by_id[pid] = p
        if slug:
            by_slug[(slug, name)] = p
        if sku:
            by_sku[(sku, name)] = p
        out.append(p)
        return True

    for p in (primary or []):
        _place(p)
    for p in (secondary or []):
        _place(p)
    return out


def _fill_missing_fields(rows, local_rows):
    """Give a Supabase row back what its table could not store.

    supabase_store's resilient upsert drops any column the products table
    lacks, so the mirrored copy of a piece can be narrower than this
    server's own override row. Only keys the Supabase row does not HAVE are
    filled: a column present as null/"" was emptied on purpose, a column
    absent is one the table cannot hold. Supabase stays the source of truth
    for every column it has.
    """
    by_id = {str((r or {}).get("id") or ""): r for r in (local_rows or []) if r}
    out = []
    for r in rows or []:
        src = by_id.get(str((r or {}).get("id") or ""))
        if src:
            r = {**r, **{k: v for k, v in src.items() if k not in r}}
        out.append(r)
    return out


def _row_photos(row):
    """Every photo path one product row carries, cover first."""
    photos = [str((row or {}).get("image") or "").strip()]
    photos += [str(g or "").strip() for g in ((row or {}).get("images") or [])]
    return [p for p in photos if p]


def _prefer_real_photos(rows, local_rows):
    """Let a real photo beat a stale placeholder across the mirror split.

    Supabase is the source of truth for every column its table holds, and the
    local override only fills the columns the table could not store
    (``_fill_missing_fields``). Photos are the one exception, and only in this
    direction: if the mirrored row still carries the branded placeholder while
    the admin's own row has a real photo, the photo wins.

    Without this the owner's upload was live on the phone that made it (a local
    override is unioned in) but the storefront served the placeholder from
    Supabase - "I added an image and it is not there" - and because a redeploy
    wipes the override file, waiting never fixed it.
    """
    by_id = {str((r or {}).get("id") or "").strip(): r for r in (local_rows or []) if r}
    out = []
    for r in rows or []:
        src = by_id.get(str((r or {}).get("id") or "").strip())
        if src and _real_photo(_row_photos(src)) and not _real_photo(_row_photos(r)):
            r = dict(r or {})
            for key in ("image", "images", "placeholderImage", "usesPlaceholder"):
                if key in src:
                    r[key] = src[key]
        out.append(r)
    return out


def product_index(products=None, include_hidden=True):
    """Map every resolvable id -> the product row, canonical id first.

    A product can be addressed by two ids:

      * its `id` (the primary key; never renamed while orders reference it), and
      * its `legacyId` alias, which holds the id the row had before it was
        given a canonical jau-* id.

    Old product links (`product.html?id=wix-001`), saved carts, order lines,
    reviews and analytics events all carry whichever id was live when they
    were written, so both must resolve to the same row.

    The canonical `id` always wins: if one product's legacyId collides with
    another product's id, the real owner keeps the key and the collision is
    not silently aliased. Rows already keyed are never overwritten.
    """
    rows = merged(include_hidden=include_hidden) if products is None else products
    index = {}
    for p in rows or []:
        pid = str((p or {}).get("id") or "").strip()
        if pid and pid not in index:
            index[pid] = p
    for p in rows or []:
        legacy = str((p or {}).get("legacyId") or "").strip()
        if legacy and legacy not in index:
            index[legacy] = p
    return index


def resolve_product_id(wanted, products=None, include_hidden=True):
    """The canonical id for a requested id, or '' when nothing matches.

    Lets callers normalise an inbound id (a legacy wix-* link, a cart line
    saved before a rename) onto the row's primary key, so anything written
    from now on - orders, reviews, analytics - stores one consistent id.
    """
    wanted = str(wanted or "").strip()
    if not wanted:
        return ""
    prod = product_index(products, include_hidden=include_hidden).get(wanted)
    return str((prod or {}).get("id") or "").strip()


def _durable_deleted_ids():
    """Product ids the owner deleted, stored durably in Supabase.

    Soft-deleting a seed product only marks its products-table row
    ``source="deleted"``. ``merged()`` still unions the 258 bundled seed
    rows, so without a durable suppression list a deleted seed product
    reappears after every Render redeploy (the local ``data/catalog.json``
    ``deleted`` list lives on the same wiped disk).

    Returns an empty set on any failure so an outage neither resurrects a
    product the owner deleted nor empties the shop.
    """
    ids = set()
    try:
        from supabase_store import load_deleted_ids, load_hard_deleted_ids
        legacy = load_deleted_ids()
        if legacy:
            ids |= {str(x).strip() for x in legacy if str(x or "").strip()}
        hard = load_hard_deleted_ids()
        if hard:
            ids |= {str(x).strip() for x in hard if str(x or "").strip()}
    except Exception:
        # Preserve whatever was read from the other ledger. A failed database
        # read must never turn into a fabricated full catalogue wipe.
        pass
    return ids


def _supabase_dead_ids():
    """Ids of products-table rows already tombstoned (source="deleted"/
    "replaced"), or the empty set.

    The durable growth_settings tombstone list can fail to write (legacy
    table); the row's own ``source`` column cannot, because the soft delete
    wrote it FIRST. Folding these ids into the deleted set is the durable
    primary suppression: a deleted product stays deleted even when the
    tombstone list write never landed, and it clears naturally when a later
    save re-writes the row with source="admin".

    Returns an empty set on any failure so an outage neither resurrects a
    product the owner deleted nor empties the shop.
    """
    try:
        from supabase_store import dead_product_ids_table
        ids = dead_product_ids_table()
    except Exception:
        return set()
    if ids is None:
        return set()
    return {str(x).strip() for x in ids if str(x or "").strip()}


def merged(include_hidden=False):
    """Seed products + every admin edit, minus what was deleted.

    This is the live catalogue. When Supabase is configured it is the source
    of truth; otherwise the local override file supplies the edits.
    """
    # Local override list UNION durable Supabase tombstones (growth_settings
    # list + products-table rows whose source is a tombstone). The local list
    # alone is wiped on every Render redeploy; the durable sets alone are
    # empty when Supabase is unreachable. Together they cover both cases.
    durable = _durable_deleted_ids() | _supabase_dead_ids()
    sb = _supabase_products()
    if sb is not None:
        # Supabase rows are the live catalogue; the seed only supplies
        # products Supabase does not have. Local overrides (a phone save that
        # has not reached Supabase yet, e.g. stretch-marks oil) are unioned
        # last so they stay visible on every device. _dedupe_products keeps
        # one copy of each product: matched by id, or by a slug/sku clash
        # confirmed by the SAME name (a re-created piece). A slug or sku
        # clash with a different product never hides it.
        ov = overrides()
        deleted = set(ov.get("deleted") or []) | durable
        ov_products = ov.get("products") or []
        products = _dedupe_products(
            _prefer_real_photos(_fill_missing_fields(sb, ov_products), ov_products),
            _seed_products())
        # local rows are unioned the same way: by id, or by a name-confirmed
        # slug/sku clash (a re-creation). A Supabase row of the same id has
        # just been enriched from them; two DIFFERENT pieces must never hide
        # each other because they share a slug or sku.
        products = _dedupe_products(products, ov_products)
        products = [p for p in products if str(p.get("id")) not in deleted]
    else:
        data, _p = _load_overrides()
        overrides_list = data.get("products") or []
        deleted = set(data.get("deleted") or []) | durable
        by_id = {p["id"]: p for p in _seed_products()}
        for p in overrides_list:
            by_id[p["id"]] = p
        products = [p for pid, p in by_id.items() if pid not in deleted]

    # The test suite's own products (jau-stock-*, jau-mirror-*, "Stock Test …")
    # are never shop pieces. Filtering them here - on every read, whatever
    # source the row came from - is what makes a deleted test product stay
    # gone even when a stray save re-created it or the durable tombstone write
    # failed. Under FLASK_ENV=testing the guard is off: there the fixtures are
    # the suite's own data.
    if _fixture_guard_active():
        products = [p for p in products if not is_test_fixture(p)]

    # Permanently removed products never come back - not from a stale mirror,
    # not from a restored backup, not from a bulk re-import.
    products = [p for p in products if not is_permanently_removed(p)]

    if not include_hidden:
        products = [p for p in products if p.get("online") is not False]

    # Read-time category fold: never serve a merged / legacy category id
    # (nails, packaging, skincare) even when the source row still carries one.
    # This mirrors _fold_product_categories() but works for every backend
    # (Supabase rows and seed products pass through merged() unchanged).
    products = [_fold_p(p) for p in products]
    # Every F CFA figure served to a shopper is a clean 50 step, whichever
    # backend the row came from. This is the read-time half of the rounding
    # contract in currency.py; js/store.js ceilings the same figures.
    products = [_clean_cfa_prices(p) for p in products]
    return resolve_images(products)


def _fold_p(product):
    """Return a copy of ``product`` with any merged/legacy category remapped."""
    p = dict(product or {})
    p["category"] = _folded_category(p.get("category"))
    return p


def _clean_cfa_prices(product):
    """Round every F CFA figure on a product UP to the next 50 step.

    The seed and admin rows can carry an odd CFA amount (wix-144 is 325,
    wix-212 is 680) while the browser already ceilings whatever it is given
    (js/store.js roundCfa), so a raw serve makes the two sides disagree on
    the price of the same piece.

    An EXISTING CFA price is only ever ROUNDED, never re-derived from the
    Naira base: 247 of the 257 seed products are priced independently per
    currency (wix-005 is 8,550 NGN but 15,000 F CFA), so re-deriving would
    re-price the whole shop downward. The CFA figure is derived from Naira
    ONLY when it is missing entirely. Naira fields are never touched -
    Naira is the exact base currency and is never rounded.
    """
    p = dict(product or {})
    cfa = _int_or_none(p.get("priceCfa"))
    ngn = _int_or_none(p.get("priceNgn"))
    if cfa:
        p["priceCfa"] = round_cfa(cfa)
    elif ngn and ngn > 0:
        p["priceCfa"] = to_cfa(ngn)
    compare_cfa = _int_or_none(p.get("compareCfa"))
    compare_ngn = _int_or_none(p.get("compareNgn"))
    if compare_cfa:
        p["compareCfa"] = round_cfa(compare_cfa)
    elif compare_ngn and compare_ngn > 0:
        p["compareCfa"] = to_cfa(compare_ngn)
    option_cfa = p.get("optionPricesCfa")
    if isinstance(option_cfa, dict) and option_cfa:
        p["optionPricesCfa"] = {k: round_cfa(v) for k, v in option_cfa.items()}
    return p


HOMEPAGE_FEATURED_MAX = 12


def _homepage_featured_raw_from_supabase():
    """The durable homepage featured selector payload from Supabase, or None.

    None means unavailable/not configured; an empty dict means the owner has not
    selected any products yet. The caller may fall back to the local override
    copy when Supabase is unavailable.
    """
    try:
        from supabase_store import load_homepage_featured
        return load_homepage_featured()
    except Exception:
        return None


def _product_category_index(products):
    out = {}
    for p in products or []:
        pid = str((p or {}).get("id") or "").strip()
        if not pid:
            continue
        out[pid] = _folded_category((p or {}).get("category"))
    return out


def _normalize_homepage_featured(raw, products=None):
    """Return the canonical, ordered homepage featured selector.

    ``featured_products`` is the storefront contract: one ordered list, never
    grouped by category.  Older saved rows used ``categories``; accept those
    rows and flatten them in insertion order so existing admin selections are
    not lost.  The derived ``categories`` map is retained only as a backwards
    compatible admin/read-cache field and is not used by the storefront.
    """
    source = raw if isinstance(raw, dict) else {}
    ids_raw = raw if isinstance(raw, (list, tuple)) else source.get("featured_products")
    if not isinstance(ids_raw, (list, tuple)):
        cats = source.get("categories") if isinstance(source.get("categories"), dict) else source
        ids_raw = []
        if isinstance(cats, dict):
            for values in cats.values():
                ids_raw.extend(values if isinstance(values, (list, tuple, set)) else [values])
    by_pid = _product_category_index(products or [])
    seen, flat = set(), []
    for item in ids_raw:
        pid = str(item or "").strip()
        if not pid or pid in seen or len(flat) >= HOMEPAGE_FEATURED_MAX:
            continue
        if by_pid and pid not in by_pid:  # deleted/unknown products are not saved
            continue
        seen.add(pid)
        flat.append(pid)
    derived = {}
    for pid in flat:
        derived.setdefault(by_pid.get(pid, ""), []).append(pid)
    derived.pop("", None)
    return {"maxTotal": HOMEPAGE_FEATURED_MAX, "featured_products": flat,
            "categories": derived, "updatedAt": str(source.get("updatedAt") or ""),
            "updatedBy": str(source.get("updatedBy") or "")}


def homepage_featured(products=None):
    """Current Homepage Featured Products selector settings.

    ``products`` lets a caller which already resolved the catalogue reuse that
    list.  This avoids a second Supabase products query on the hot /api/catalog
    response path; callers without a list retain the original behaviour.
    """
    if products is None:
        try:
            products = merged(include_hidden=True)
        except Exception:
            products = []
    raw = None
    if _prod_source():
        raw = _homepage_featured_raw_from_supabase()
    if raw is None:
        raw = (overrides() or {}).get("homepageFeatured") or {}
    return _normalize_homepage_featured(raw, products)


def save_homepage_featured(raw, actor=None):
    """Persist Homepage Featured Products selections. Returns payload or None.

    In production, a failed Supabase write is a real failure: the Admin portal
    must not claim a homepage change is live unless the durable setting landed.
    The local override copy is refreshed after a successful write so a rolling
    process can still answer consistently while Supabase is momentarily slow.
    """
    clean = _normalize_homepage_featured(raw, merged(include_hidden=True))
    clean["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    clean["updatedBy"] = actor or ""

    if _prod_source():
        try:
            from supabase_store import save_homepage_featured as _save_featured
            if not _save_featured(clean):
                return None
        except Exception:
            return None

    def _apply(data, _path):
        data["homepageFeatured"] = clean
        data["updatedAt"] = clean["updatedAt"]
        data["updatedBy"] = actor or ""
        return data

    try:
        _mutate(actor, _apply)
    except Exception:
        if not _prod_source():
            return None
    return clean


def _home_rank(product):
    p = product or {}
    badge = str(p.get("badge") or "").lower()
    if badge == "new":
        rank = 0
    elif p.get("featured"):
        rank = 1
    elif badge == "bestseller":
        rank = 2
    elif badge == "sale":
        rank = 3
    else:
        rank = 4
    return (rank, str(p.get("name") or "").lower(), str(p.get("id") or ""))


def homepage_featured_groups(products=None, limit=HOMEPAGE_FEATURED_MAX):
    """Legacy API projection of the flat selector (storefront does not group)."""
    live = list(products if products is not None else merged())
    max_total = max(1, min(HOMEPAGE_FEATURED_MAX, int(limit or HOMEPAGE_FEATURED_MAX)))
    settings = homepage_featured()
    by_id = {str(p.get("id")): p for p in live if p.get("id")}
    ids = settings.get("featured_products") or []
    chosen = [by_id[pid] for pid in ids if pid in by_id][:max_total]
    if not chosen:
        chosen = sorted(live, key=_home_rank)[:max_total]
    groups, buckets = [], {}
    for product in chosen:
        cid = _folded_category(product.get("category"))
        buckets.setdefault(cid, []).append(product)
    for cid, rows in buckets.items():
        groups.append({"category": cid, "productIds": [p.get("id") for p in rows],
                       "products": rows, "custom": bool(ids)})
    return groups


def homepage_featured_products(products=None, limit=HOMEPAGE_FEATURED_MAX):
    """Resolve the ordered, admin-selected flat homepage product list."""
    live = list(products if products is not None else merged())
    max_total = max(1, min(HOMEPAGE_FEATURED_MAX, int(limit or HOMEPAGE_FEATURED_MAX)))
    settings = homepage_featured()
    by_id = {str(p.get("id")): p for p in live if p.get("id")}
    selected = [by_id[pid] for pid in settings.get("featured_products", []) if pid in by_id]
    if selected:
        return selected[:max_total]
    return sorted(live, key=_home_rank)[:max_total]

def meta():
    """Metadata blob used for ETag / change detection on the catalogue."""
    data, _p = _load_overrides()
    products = merged(include_hidden=True)
    latest_update = max((str(p.get("updated_at") or "") for p in products), default="")
    featured = homepage_featured()
    return {
        "updatedAt": max(str(data.get("updatedAt") or ""), latest_update, str(featured.get("updatedAt") or "")),
        "updatedBy": data.get("updatedBy") or "",
        "count": len(products),
        "homepageFeatured": featured,
    }


def _mutate(actor, fn):
    """Run a read-modify-write under the catalogue cross-process lock.

    ``fn(data, path)`` receives the latest on-disk overrides and returns the
    new overrides dict. Serialising here means two gunicorn workers saving at
    once cannot erase each other's product.
    """
    path = _norm_filename(CATALOG_FILE)
    with _catalog_lock(path):
        data, path = _load_overrides()
        data = fn(data, path) or data
        _write_overrides(data, path)
    return data, path


def apply_stock_delta(pid, qty_delta, option_key=None, actor=None):
    """Add ``qty_delta`` to a product's stock (negative decrements). Clamps at 0.

    When ``option_key`` matches an ``optionStock`` entry (exact or folded), that
    choice is adjusted by the same amount. Persists through :func:`upsert` so
    the live catalogue and Supabase both see the new quantity.
    """
    pid = str(pid or "")
    try:
        qty_delta = int(qty_delta)
    except (TypeError, ValueError):
        return None
    if not pid or qty_delta == 0:
        return None

    if _prod_source():
        from supabase_store import reserve_product_stock, release_product_stock
        if qty_delta < 0:
            # The variant key must travel with the reservation: without it
            # only the product-level total is guarded and a variant can be
            # sold past its own quantity.
            return reserve_product_stock(pid, -qty_delta, option=_match_option_key(pid, option_key))
        return release_product_stock(pid, qty_delta, option=_match_option_key(pid, option_key))

    found = None
    for p in merged(include_hidden=True):
        if str(p.get("id")) == pid:
            found = p
            break
    if found is None:
        return None
    rec = dict(found)
    new_stock = stock_of(rec) + qty_delta
    if new_stock < 0:
        new_stock = 0
    # keep the canonical Supabase column AND the legacy alias in sync, or
    # normalize() (which prefers stock_quantity) would silently revert it
    rec["stock"] = new_stock
    rec["stock_quantity"] = new_stock
    os_map = rec.get("optionStock")
    if option_key and isinstance(os_map, dict) and os_map:
        matched = _option_stock_key_for(rec, option_key)
        if matched is not None:
            os_map = dict(os_map)
            try:
                cur = int(os_map[matched] or 0)
            except (TypeError, ValueError):
                cur = 0
            os_map[matched] = max(0, cur + qty_delta)
            rec["optionStock"] = os_map
    upsert(rec, actor=actor or "stock")
    return rec


def _match_option_key(pid, option_key):
    """The exact optionStock key for ``option_key`` on product ``pid``.

    The Supabase RPC guards on the jsonb key itself, so the caller must hand
    it the key as stored on the row (not a folded/cart-side spelling). Best
    effort: when the row or the key cannot be resolved the reservation falls
    back to the product-level guard, exactly like a no-variant line.
    """
    if not option_key:
        return None
    try:
        for p in merged(include_hidden=True):
            if str(p.get("id")) == pid:
                return _option_stock_key_for(p, option_key)
    except Exception:
        return None
    return None


def _option_stock_key_for(product, variant):
    """The optionStock map key matching a cart variant spelling, or None."""
    if not isinstance(product, dict):
        return None
    os_map = product.get("optionStock")
    if not isinstance(os_map, dict) or not os_map:
        return None
    vals = variant_values(variant)
    if not vals:
        return None
    folded = {}
    for k in os_map:
        fk = fold_option_value(k)
        if fk:
            folded[fk] = k
    # Multi-dimensional supplier variants are stored as the complete cart
    # label ("Color: Red · Size: M"). Prefer that exact combination before
    # the legacy single-value fallback, otherwise Red/M could consume Red/L.
    whole = fold_option_value(variant)
    if whole and whole in folded:
        return folded[whole]
    for val in vals:
        fk = fold_option_value(val)
        if fk and fk in folded:
            return folded[fk]
    return None


def reserve_stock(pid, qty, option_key=None, actor=None):
    """Atomically reserve ``qty`` units of one product (local backend).

    The guarded counterpart of apply_stock_delta(-qty): it only succeeds when
    the product is online and BOTH the product total and the chosen variant
    still have the quantity. The read-modify-write runs under the catalogue's
    cross-process file lock, so two concurrent checkouts (two gunicorn
    workers) can never both take the last unit. Returns a truthy result on
    success, None when there is not enough stock, and False when the write
    itself failed.
    """
    pid = str(pid or "")
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        return None
    if not pid or qty <= 0:
        return None

    if _prod_source():
        # Production guards in PostgreSQL (single guarded UPDATE); the local
        # lock below would not span dynos anyway. A selected variant with no
        # assigned stock key must never fall back to the product total.
        matched = _match_option_key(pid, option_key) if option_key else None
        if option_key and matched is None:
            return None
        from supabase_store import reserve_product_stock
        return reserve_product_stock(pid, qty, option=matched)

    path = _norm_filename(CATALOG_FILE)
    with _catalog_lock(path):
        found = None
        for p in merged(include_hidden=True):
            if str(p.get("id")) == pid:
                found = p
                break
        if found is None:
            return None
        rec = dict(found)
        stock = stock_of(rec)
        if rec.get("online") is False or stock < qty:
            return None
        matched = _option_stock_key_for(rec, option_key) if option_key else None
        if option_key and matched is None:
            return None
        if matched is not None:
            try:
                variant_qty = int(rec["optionStock"][matched] or 0)
            except (TypeError, ValueError):
                variant_qty = 0
            if variant_qty < qty:
                return None
        rec["stock"] = max(0, stock - qty)
        rec["stock_quantity"] = rec["stock"]
        if matched is not None:
            os_map = dict(rec["optionStock"])
            os_map[matched] = max(0, int(os_map[matched] or 0) - qty)
            rec["optionStock"] = os_map
        # Write the override row directly (upsert() would re-take the lock we
        # already hold).
        try:
            clean = normalize(rec)
            if clean is None:
                return False
            data, path = _load_overrides()
            data["products"] = [p for p in (data.get("products") or []) if p.get("id") != pid]
            data["products"].append(clean)
            data["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
            data["updatedBy"] = actor or "stock"
            _write_overrides(data, path)
        except Exception as exc:                   # pragma: no cover - disk
            print(f"[catalog] local stock reserve failed: {exc}")
            return False
    return rec


def release_stock(pid, qty, option_key=None, actor=None):
    """Atomically return reserved units without racing another checkout."""
    pid = str(pid or "").strip()
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        return False
    if not pid or qty <= 0:
        return False
    if _prod_source():
        try:
            from supabase_store import release_product_stock
            return release_product_stock(pid, qty, option=_match_option_key(pid, option_key))
        except Exception as exc:
            print(f"[supabase] release stock failed for {pid}: {exc}")
            return False

    path = _norm_filename(CATALOG_FILE)
    with _catalog_lock(path):
        data, path = _load_overrides()
        deleted = {str(x or "").strip() for x in (data.get("deleted") or [])}
        if pid in deleted or pid in PERMANENTLY_REMOVED_IDS:
            return False
        current = next((dict(p) for p in (data.get("products") or [])
                        if str((p or {}).get("id") or "").strip() == pid), None)
        if current is None:
            current = next((dict(p) for p in _seed_products()
                            if str((p or {}).get("id") or "").strip() == pid), None)
        if current is None or is_permanently_removed(current):
            return False
        rec = dict(current)
        current_stock = stock_of(rec)
        rec["stock"] = rec["stock_quantity"] = min(10**7, current_stock + qty)
        matched = _option_stock_key_for(rec, option_key) if option_key else None
        if matched is not None:
            try:
                current_variant = max(0, int(rec["optionStock"].get(matched) or 0))
            except (TypeError, ValueError):
                current_variant = 0
            options = dict(rec["optionStock"])
            options[matched] = min(10**7, current_variant + qty)
            rec["optionStock"] = options
        clean = normalize(rec)
        if clean is None:
            return False
        data["products"] = [p for p in (data.get("products") or [])
                            if str((p or {}).get("id") or "").strip() != pid]
        data["products"].append(clean)
        data["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        data["updatedBy"] = actor or "stock-release"
        try:
            _write_overrides(data, path)
        except Exception as exc:
            print(f"[catalog] local stock release failed for {pid}: {exc}")
            return False
    return clean


def _supplier_option_key_is_current(product, key):
    """Whether a previously untracked supplier key still names a live option."""
    raw = str(key or "").strip()
    options = (product or {}).get("options")
    if not raw or not isinstance(options, list):
        return False

    def part_is_current(part):
        part = str(part or "").strip()
        if not part:
            return False
        title, value = (part.split(":", 1) if ":" in part else ("", part))
        wanted_title = re.sub(r"[^a-z0-9]", "", title.lower())
        wanted_value = re.sub(r"[^a-z0-9]", "", value.lower())
        for option in options:
            if not isinstance(option, dict):
                continue
            option_title = re.sub(r"[^a-z0-9]", "", str(option.get("title") or "").lower())
            if wanted_title and wanted_title != option_title:
                continue
            values = option.get("values") if isinstance(option.get("values"), list) else []
            if any(re.sub(r"[^a-z0-9]", "", str(v or "").lower()) == wanted_value
                   for v in values):
                return True
        return False

    parts = raw.split(" · ") if " · " in raw else [raw]
    return bool(parts) and all(part_is_current(part) for part in parts)


def apply_supplier_stock(pid, stock, option_changes=None, actor="supplier-watchdog",
                         allow_increase=False, option_snapshot_keys=None):
    """Apply supplier-observed stock only, without replaying a stale product row.

    Production uses an UPDATE-only Postgres RPC (never an upsert) that merges
    just the changed option keys under the same advisory lock as hard delete.
    Unless explicitly enabled, incoming counts are also capped at the latest
    database/file count so a slow supplier fetch cannot restore units sold by
    a checkout while that fetch was in flight. The local backend patches the
    latest product under its cross-process file lock. Returns ``(row, action,
    mirrored)`` like :func:`upsert`.
    """
    pid = str(pid or "").strip()
    if not pid:
        return None, "rejected", True
    try:
        stock = max(0, min(10**7, int(stock)))
    except (TypeError, ValueError):
        return None, "rejected", True
    changes = None
    if option_changes is not None:
        if not isinstance(option_changes, dict):
            return None, "rejected", True
        changes = {}
        for key, value in list(option_changes.items())[:60]:
            key = str(key or "").strip()
            if not key:
                continue
            try:
                changes[key] = max(0, min(10**7, int(value)))
            except (TypeError, ValueError):
                return None, "rejected", True

    if _prod_source():
        try:
            from supabase_store import apply_supplier_stock as _sb_apply
            ok = bool(_sb_apply(pid, stock, changes, allow_increase=allow_increase,
                                option_snapshot_keys=option_snapshot_keys))
        except Exception as exc:
            print(f"[supabase] supplier stock patch failed for {pid}: {exc}")
            ok = False
        if not ok:
            try:
                if pid in deleted_product_ids() or pid in PERMANENTLY_REMOVED_IDS:
                    return None, "permanently-removed", True
            except Exception:
                pass
            return None, "error", False
        row = _read_back_product(pid)
        if row is None:
            return None, "error", False
        return row, "updated", True

    path = _norm_filename(CATALOG_FILE)
    with _catalog_lock(path):
        data, path = _load_overrides()
        deleted = {str(x or "").strip() for x in (data.get("deleted") or [])}
        if pid in deleted or pid in PERMANENTLY_REMOVED_IDS:
            return None, "permanently-removed", True
        current = next((dict(p) for p in (data.get("products") or [])
                        if str((p or {}).get("id") or "").strip() == pid), None)
        if current is None:
            current = next((dict(p) for p in _seed_products()
                            if str((p or {}).get("id") or "").strip() == pid), None)
        if current is None or is_permanently_removed(current):
            return None, "permanently-removed", True
        if changes is not None:
            if not current.get("options") and not current.get("optionStock"):
                return None, "error", False
            option_stock = dict(current.get("optionStock") or {})
            snapshot_keys = None
            if option_snapshot_keys is not None:
                snapshot_keys = {str(k or "").strip() for k in option_snapshot_keys
                                 if str(k or "").strip()}
            accepted_changes = {}
            for key, value in changes.items():
                key = str(key or "").strip()
                if not key:
                    continue
                # If an option was tracked when the supplier fetch began but
                # the owner has since removed its optionStock key, this stale
                # snapshot may not put that key back. Untracked keys are only
                # accepted while they still exist in the latest product options.
                # Ignore keys which no longer exist in the current option
                # definitions, even if an obsolete optionStock entry remains.
                if not _supplier_option_key_is_current(current, key):
                    continue
                if key not in option_stock and snapshot_keys is not None and key in snapshot_keys:
                    continue
                try:
                    incoming = max(0, int(value or 0))
                    previous = max(0, int(option_stock.get(key, 0) or 0))
                except (TypeError, ValueError):
                    return None, "rejected", True
                accepted_changes[key] = incoming if allow_increase else min(previous, incoming)
            if not accepted_changes:
                return current, "updated", True
            option_stock.update(accepted_changes)
            current["optionStock"] = option_stock
            stock = 0
            for value in option_stock.values():
                try:
                    stock += max(0, int(value or 0))
                except (TypeError, ValueError):
                    continue
        else:
            if current.get("options") or current.get("optionStock"):
                return None, "error", False
            if not allow_increase:
                stock = min(stock_of(current), stock)
        current["stock"] = stock
        current["stock_quantity"] = stock
        current["updated_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        data["products"] = [p for p in (data.get("products") or [])
                            if str((p or {}).get("id") or "").strip() != pid]
        data["products"].append(current)
        data["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        data["updatedBy"] = actor or "supplier-watchdog"
        try:
            _write_overrides(data, path)
        except Exception as exc:
            print(f"[catalog] local supplier stock patch failed for {pid}: {exc}")
            return None, "error", False
    try:
        from supabase_store import invalidate_read_cache
        invalidate_read_cache("products_rows")
    except Exception:
        pass
    return current, "updated", True


def set_variant_stock(pid, qty, option_key=None, actor=None):
    """Set the ABSOLUTE quantity of one product, or of one variant of it.

    The admin stock manager used to write a separate variant_stock store the
    checkout never read: the manager said 10 while the product row (what
    reservations guard and decrement) still said 24, and the shopper could
    order stock the owner believed was gone. Every stock number now lives on
    the product row - stock / stock_quantity for the total, optionStock for
    the per-variant split - and this is the one setter.

    Semantics: a matching optionStock key sets that variant (and re-syncs the
    product total to the variant sum); anything else sets the product total.
    The local branch read-modify-writes under the catalogue's cross-process
    lock, exactly like reserve_stock. Production writes through the same
    upsert the product editor uses.
    """
    pid = str(pid or "")
    try:
        qty = max(0, int(qty))
    except (TypeError, ValueError):
        return None
    if not pid:
        return None

    if _prod_source():
        found = _read_back_product(pid)
        if found is None:
            return None
        rec = dict(found)
        matched = _option_stock_key_for(rec, option_key) if option_key else None
        if matched is not None:
            os_map = dict(rec.get("optionStock") or {})
            os_map[matched] = qty
            rec["optionStock"] = os_map
            rec["stock"] = max(0, sum(int(v or 0) for v in os_map.values()))
        else:
            rec["stock"] = qty
        rec["stock_quantity"] = rec["stock"]
        try:
            clean = normalize(rec)
            if clean is None:
                return False
            from supabase_store import upsert_products
            if not upsert_products([clean]):
                return False
        except Exception as exc:                   # pragma: no cover - network
            print(f"[catalog] product stock set failed: {exc}")
            return False
        return clean

    path = _norm_filename(CATALOG_FILE)
    with _catalog_lock(path):
        found = None
        for p in merged(include_hidden=True):
            if str(p.get("id")) == pid:
                found = p
                break
        if found is None:
            return None
        rec = dict(found)
        matched = _option_stock_key_for(rec, option_key) if option_key else None
        if matched is not None:
            os_map = dict(rec.get("optionStock") or {})
            os_map[matched] = qty
            rec["optionStock"] = os_map
            rec["stock"] = max(0, sum(int(v or 0) for v in os_map.values()))
        else:
            rec["stock"] = qty
        rec["stock_quantity"] = rec["stock"]
        try:
            clean = normalize(rec)
            if clean is None:
                return False
            data, wpath = _load_overrides()
            data["products"] = [p for p in (data.get("products") or []) if p.get("id") != pid]
            data["products"].append(clean)
            data["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
            data["updatedBy"] = actor or "stock"
            _write_overrides(data, wpath)
        except Exception as exc:                   # pragma: no cover - disk
            print(f"[catalog] local stock set failed: {exc}")
            return False
    return rec



def deleted_product_ids():
    """Every product id that must never come back or be re-created by an
    automated pass (supplier sync, mirror, cache re-hydration).

    Unions the local override deleted list with the durable Supabase
    tombstone list, so the answer is correct whichever side a delete landed
    on. A failed durable read returns the local side only: an unreachable
    database must never look like "nothing is deleted"."""
    ids = set()
    try:
        ids |= {str(x or "").strip() for x in (overrides().get("deleted") or []) if str(x or "").strip()}
    except Exception:
        pass
    try:
        durable = _durable_deleted_ids()
        if durable:
            ids |= {str(x or "").strip() for x in durable if str(x or "").strip()}
    except Exception:
        pass
    return ids


def local_only_products():
    """Admin overrides that are not yet in the live Supabase table.

    A row the owner deleted is NEVER one of these. Soft-deleting a product
    tombstones its products-table row (``source="deleted"``), and a tombstoned
    row is not a live row - so without this check the id looked "local only"
    and the scheduler's remirror pass pushed the disk copy straight back into
    Supabase as a live product. That is how a deleted product came back a few
    minutes after it was deleted. The fixture guard is the same story for the
    test suite's products.
    """
    sb = _supabase_products()
    if sb is None:
        return []

    def _key(v):
        return str(v or "").strip().lower()

    ov = overrides()
    deleted = {str(x or "").strip() for x in (ov.get("deleted") or [])} | _durable_deleted_ids()
    seen_ids = {str(p.get("id") or "") for p in sb}
    seen_slugs = {_key(p.get("slug")) for p in sb if p.get("slug")}
    seen_skus = {_key(p.get("sku")) for p in sb if p.get("sku")}
    out = []
    for p in (ov.get("products") or []):
        pid = str(p.get("id") or "")
        if not pid or pid in seen_ids or pid in deleted:
            continue
        if is_test_fixture(p) and _fixture_guard_active():
            continue
        slug = _key(p.get("slug"))
        sku = _key(p.get("sku"))
        if slug and slug in seen_slugs:
            continue
        if sku and sku in seen_skus:
            continue
        out.append(p)
    return out


def _prod_source():
    """True when Supabase is the production source of truth (not testing)."""
    try:
        from supabase_store import enabled
        return bool(enabled()) and Config.ENV != "testing"
    except Exception:
        return False


def purge_test_fixtures(actor="fixture_guard"):
    """Remove every test-suite product from the live shop, durably.

    Called on boot (production/staging). Finds the fixture rows wherever they
    are (Supabase products table, local overrides, the bundled seed) and

      * tombstones the Supabase row (``source="deleted"``),
      * records the id in the durable deleted-ids list, and
      * adds it to the local override ``deleted`` list,

    so the guard at the top of merged() is backed by real tombstones and a
    later ``push_catalog_to_supabase`` / remirror pass cannot re-publish one.
    Idempotent: a second run finds nothing to do.
    """
    try:
        import supabase_store
    except Exception:                                   # pragma: no cover
        supabase_store = None

    ids, local_ids = set(), set()
    for row in (_supabase_products() or []):
        if is_test_fixture(row):
            ids.add(str(row.get("id") or "").strip())
    for row in (overrides().get("products") or []):
        if is_test_fixture(row):
            pid = str(row.get("id") or "").strip()
            local_ids.add(pid)
            ids.add(pid)
    for row in _seed_products():
        if is_test_fixture(row):
            ids.add(str(row.get("id") or "").strip())
    ids.discard("")
    local_ids.discard("")

    report = {"found": sorted(ids), "tombstoned": [], "recorded": [], "local": []}
    if supabase_store is not None and _prod_source():
        for pid in sorted(ids):
            try:
                if supabase_store.delete_products_strict([pid]):
                    report["tombstoned"].append(pid)
            except Exception:
                pass
            try:
                if supabase_store.add_deleted_id(pid):
                    report["recorded"].append(pid)
            except Exception:
                pass
    if local_ids or ids:
        def _apply(data, _path):
            data["products"] = [p for p in (data.get("products") or [])
                                if not is_test_fixture(p)]
            deleted = list(data.get("deleted") or [])
            for pid in sorted(set(local_ids) | ids):
                if pid not in deleted:
                    deleted.append(pid)
            data["deleted"] = deleted
            data["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
            data["updatedBy"] = actor
            return data
        try:
            _mutate(actor, _apply)
            report["local"] = sorted(set(local_ids) | ids)
        except Exception:
            pass
    return report


def purge_permanently_removed(actor="purge_guard"):
    """Hard-delete every PERMANENTLY_REMOVED product from the live shop.

    Called on boot (production/staging) and by tools/purge_product.py:

      * deletes the products-table ROW in Supabase PostgreSQL (not a
        tombstone - the row is gone),
      * purges the files it referenced from Supabase Storage,
      * records the id in the durable deleted-ids list, and
      * drops it from the local override file.

    Idempotent, never raises: a second run finds nothing to do. Together
    with the read-time filter in merged() this is what makes an "absolute
    deletion" survive deploys, restarts and re-imports.
    """
    report = {"ids": sorted(PERMANENTLY_REMOVED_IDS), "deleted": [],
              "files": 0, "errors": []}
    ids = set(PERMANENTLY_REMOVED_IDS)
    # Pick up re-created copies that carry a new id but the same slug/name.
    for row in (_supabase_products() or []):
        if is_permanently_removed(row):
            ids.add(str(row.get("id") or "").strip())
    for row in (overrides().get("products") or []):
        if is_permanently_removed(row):
            ids.add(str(row.get("id") or "").strip())
    ids.discard("")
    if not ids:
        return report

    if _prod_source():
        try:
            import supabase_store
            result = supabase_store.hard_delete_products(sorted(ids))
            report["deleted"] = result.get("deleted") or []
            report["files"] = result.get("files") or 0
            report["errors"] = result.get("errors") or []
        except Exception as exc:                        # pragma: no cover
            report["errors"].append(str(exc))

    def _apply(data, _path):
        data["products"] = [p for p in (data.get("products") or [])
                            if not is_permanently_removed(p)]
        deleted = list(data.get("deleted") or [])
        for pid in sorted(ids):
            if pid not in deleted:
                deleted.append(pid)
        data["deleted"] = deleted
        data["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        data["updatedBy"] = actor
        return data

    try:
        _mutate(actor, _apply)
    except Exception as exc:
        report["errors"].append(str(exc))
    return report


def _own_photo_ref(value):
    """A normalised reference for one of OUR stored photos, or "".

    Both shapes a row can carry resolve to the same thing: a same-origin
    ``/uploads/<key>`` link and one of our bucket URLs are the same object.
    Foreign hosts (and data:/blob: values) return "" and are never touched.
    """
    return _own_upload_path(value)


def _media_refs(product):
    """Every upload URL a product row currently points at."""
    p = dict(product or {})
    refs = []
    for key in ("image", "image_url", "imageUrl", "video", "video_url"):
        value = p.get(key)
        if isinstance(value, str) and value.strip():
            refs.append(value.strip())
    images = p.get("images")
    if isinstance(images, str):
        try:
            images = json.loads(images)
        except Exception:
            images = []
    if isinstance(images, (list, tuple)):
        for value in images:
            if isinstance(value, str) and value.strip():
                refs.append(value.strip())
            elif isinstance(value, dict):
                for key in ("url", "src", "image", "video"):
                    if isinstance(value.get(key), str) and value[key].strip():
                        refs.append(value[key].strip())
    out, seen = [], set()
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def _upload_key(value):
    try:
        import storage as _storage
        return _storage._key_from_url(str(value or ""))
    except Exception:                                   # pragma: no cover
        return ""


def _purge_removed_media(before, after=None):
    """Hard-delete uploaded media that an edit/delete has unlinked.

    The row is saved first, then this runs. storage.delete_upload() refuses to
    delete an object still referenced by another live product, so shared media
    is not purged out from under the remaining product.
    """
    before_map = {}
    for ref in _media_refs(before):
        key = _upload_key(ref)
        if key:
            before_map.setdefault(key, ref)
    after_keys = {_upload_key(ref) for ref in _media_refs(after)} if after else set()
    removed = 0
    if not before_map:
        return removed
    try:
        import storage as _storage
    except Exception:                                   # pragma: no cover
        return 0
    for key, ref in before_map.items():
        if key in after_keys:
            continue
        try:
            if _storage.delete_upload(ref):
                removed += 1
        except Exception:
            pass
    return removed


def _purge_catalog_media_diff(before_rows, after_rows):
    after_by_id = {str((p or {}).get("id") or ""): p for p in (after_rows or [])}
    removed = 0
    for before in (before_rows or []):
        pid = str((before or {}).get("id") or "")
        removed += _purge_removed_media(before, after_by_id.get(pid))
    return removed


def repair_dead_photos(limit=60, actor="photo_repair", dry_run=False):
    """Re-point products whose stored photo is missing from the bucket.

    Supabase Storage keeps the bytes across a redeploy, but an object can be
    gone (deleted by an old cleanup, an interrupted upload, a bucket that was
    recreated). The product row then points at a URL that 404s, the browser
    swaps in the branded placeholder and the card reads "PHOTO COMING SOON"
    forever - even though the row may still hold the same photo under another
    entry, or a committed copy under images/products/.

    For every product whose cover is one of OUR uploads that no longer exists
    this picks the first replacement that does exist - a sibling gallery photo,
    otherwise a committed repo photo for the slug - and saves it, so the fix
    reaches every device and survives the next deploy. Products with a working
    cover are never touched, and a product with nothing to fall back to is
    reported (``unrecoverable``) instead of being changed.
    """
    try:
        import storage as _storage
    except Exception:                                   # pragma: no cover
        return {"checked": 0, "missing": [], "repaired": [], "unrecoverable": [],
                "skipped": "storage unavailable"}
    report = {"checked": 0, "missing": [], "repaired": [], "unrecoverable": []}

    def _exists(ref):
        return _storage.object_exists(ref) is not False      # True or unknown

    for p in merged(include_hidden=True):
        if report["checked"] >= limit:
            break
        cover = str(p.get("image") or "")
        key = _own_photo_ref(cover)
        if not key:
            continue                      # committed repo path / not ours
        report["checked"] += 1
        if _storage.object_exists(cover) is not False:
            continue
        report["missing"].append(str(p.get("id") or ""))
        alt = ""
        for entry in (p.get("images") or []):
            entry = str(entry or "")
            if not entry or entry == cover or _is_placeholder_path(entry):
                continue
            if _own_photo_ref(entry) and _exists(entry):
                alt = entry
                break
            if not _own_photo_ref(entry) and _is_local(entry) and _file_exists(entry):
                alt = entry
                break
        if not alt:
            for cand in photo_repair_candidates(p):
                if _file_exists(cand):
                    alt = cand
                    break
        if not alt:
            report["unrecoverable"].append(str(p.get("id") or ""))
            continue
        entry = {"id": str(p.get("id") or ""), "image": alt}
        report["repaired"].append(entry)
        if dry_run:
            continue
        row = dict(p)
        row["image"] = alt
        row["image_url"] = alt
        row["images"] = [alt] + [str(g) for g in (p.get("images") or [])
                                 if str(g) and str(g) != alt and not _is_placeholder_path(g)]
        try:
            upsert(row, actor)
        except Exception:
            pass
    return report


def repair_product_photo(pid, actor="photo_report"):
    """Repair one product's dead photo. Returns the new image path or ""."""
    pid = str(pid or "").strip()
    if not pid:
        return ""
    for p in merged(include_hidden=True):
        if str(p.get("id") or "") != pid:
            continue
        try:
            import storage as _storage
        except Exception:                               # pragma: no cover
            return ""
        cover = str(p.get("image") or "")
        if not _own_photo_ref(cover) or _storage.object_exists(cover) is not False:
            return ""
        alt = ""
        for g in (p.get("images") or []):
            g = str(g or "")
            if g and g != cover and not _is_placeholder_path(g) and _storage.object_exists(g) is not False:
                alt = g
                break
        if not alt:
            for cand in photo_repair_candidates(p):
                if _file_exists(cand):
                    alt = cand
                    break
        if not alt:
            return ""
        row = dict(p)
        row["image"] = alt
        row["image_url"] = alt
        row["images"] = [alt] + [str(x) for x in (p.get("images") or [])
                                 if str(x) and str(x) != alt and not _is_placeholder_path(x)]
        try:
            saved, _action, _mirrored = upsert(row, actor)
        except Exception:
            return ""
        return alt if saved else ""
    return ""


def _read_back_product(pid):
    """Re-query one product from Supabase and canonicalise it."""
    try:
        from supabase_store import product_by_id, _canonicalize_product
        row = product_by_id(pid)
        if row is None:
            return None
        return _canonicalize_product(row)
    except Exception:
        return None


def remirror_strays(actor=None):
    """Push local-only products to Supabase. Returns how many were sent."""
    from supabase_store import upsert_products, enabled
    if not enabled():
        return 0
    strays = local_only_products()
    if not strays:
        return 0
    ok = upsert_products(strays)
    return len(strays) if ok else 0


def upsert(product, actor=None):
    """Save (create or edit) one product. Returns (product, action, mirrored).

    In production (Supabase enabled, not testing) the write goes straight to
    PostgreSQL: no local override file is touched, and a failed Supabase
    write returns (None, "error", False) so the route can surface a clear
    error instead of reporting success. The returned product is the row
    re-queried from Supabase.
    """
    clean = normalize(product)
    if clean is None:
        return None, "rejected", True
    # A test-fixture product may not be created or re-saved on a live shop:
    # doing so cleared its durable tombstone (below) and put it back on the
    # storefront - the "stock test products came back" complaint. Deleting one
    # still works, so the owner can clear anything already there.
    if _fixture_guard_active() and is_test_fixture(clean):
        return None, "test-fixture", True
    # A permanently removed product (the owner deleted it for good) can never
    # be re-created - by an admin save, a CSV import or a mirror pass. The
    # dedicated SQL ledger is distinct from the older soft-delete JSON list:
    # a deliberate legacy re-save can still clear a soft tombstone, but no
    # writer may resurrect a hard-deleted id.
    if is_permanently_removed(clean):
        return None, "permanently-removed", True
    try:
        from supabase_store import load_hard_deleted_ids
        hard_deleted = load_hard_deleted_ids() or []
        if clean["id"] in {str(x or "").strip() for x in hard_deleted}:
            return None, "permanently-removed", True
    except Exception:
        # The PostgreSQL trigger is the final race-safe guard if the read path
        # is unavailable; a transient read never turns into a false save error.
        pass

    live = []
    try:
        live = merged(include_hidden=True)
    except Exception:
        live = []                     # never block a save on a Supabase read
    # An ordinary admin edit (a price change, a photo swap) does not re-emit
    # legacyId. Dropping it would silently break every old wix-* link, order
    # line and review pointing at this row, so carry the stored alias forward.
    if not clean.get("legacyId"):
        for p in live:
            if str((p or {}).get("id") or "") == clean["id"]:
                keep = str((p or {}).get("legacyId") or "").strip()
                if keep:
                    clean["legacyId"] = keep
                break
    taken = {str(p.get("slug") or "").strip().lower() for p in live
             if p and str(p.get("id") or "") != clean["id"] and p.get("slug")}
    wanted = str(clean.get("slug") or "")
    clean["slug"] = _free_slug(wanted, clean["id"], taken)

    previous = next((p for p in live if str((p or {}).get("id") or "") == clean["id"]), None)
    action = "updated" if previous else "created"

    if _prod_source():
        try:
            from supabase_store import upsert_products, clear_deleted_id
            ok = bool(upsert_products([clean]))
        except Exception:
            ok = False
            clear_deleted_id = None
        if not ok:
            return None, "error", False
        # A re-created / re-saved product must not stay filtered out by the
        # durable tombstone list — clear it only AFTER the row lands.
        try:
            from supabase_store import clear_deleted_id as _clear_del
            _clear_del(clean["id"])
        except Exception:
            pass
        row = _read_back_product(clean["id"])
        if row is None:
            return None, "error", False
        clean = row
        if previous:
            _purge_removed_media(previous, clean)
        _sync_repo_async()
        return clean, action, True

    path = _norm_filename(CATALOG_FILE)
    with _catalog_lock(path):
        data, path = _load_overrides()
        existing = [p for p in (data.get("products") or []) if p.get("id") == clean["id"]]
        action = "updated" if existing else "created"
        data["products"] = [p for p in (data.get("products") or []) if p.get("id") != clean["id"]]
        data["products"].append(clean)
        # Re-saving (or re-creating) a product un-deletes it if it had been
        # soft-deleted before.
        data["deleted"] = [pid for pid in (data.get("deleted") or []) if pid != clean["id"]]
        data["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        data["updatedBy"] = actor or ""
        _write_overrides(data, path)
    # Durable tombstone must clear too, or a re-created product stays hidden
    # after the next deploy even though the local deleted list was cleaned.
    try:
        from supabase_store import clear_deleted_id
        clear_deleted_id(clean["id"])
    except Exception:
        pass
    # Mirror BEFORE purging removed media: storage.delete_upload() refuses to
    # delete an object a LIVE product row still references, and when a
    # Supabase mirror is active merged() reads the mirror. Purging first made
    # the guard see the OLD row on the mirror and refuse, so every replaced
    # photo leaked in the bucket forever. The production (_prod_source) path
    # already mirrors-then-purges; this keeps both paths in the same order.
    #
    # The purge is also skipped entirely when the mirror rejected the row: the
    # old photo is still the saved one then, and "a failed save keeps the
    # previous image" has to hold on this path too.
    mirrored = True
    try:
        from supabase_store import upsert_products, enabled
        ok = upsert_products([clean])
        mirrored = bool(ok) if enabled() else True
    except Exception:
        try:
            from supabase_store import enabled
            mirrored = not enabled()
        except Exception:
            mirrored = True
    if previous and mirrored:
        _purge_removed_media(previous, clean)
    _sync_repo_async()
    return clean, action, mirrored


def remove(pid, actor=None):
    """Delete one product from the live catalogue, and report what happened.

    In production (Supabase is the source of truth) the delete is the atomic
    ``hard_delete_products(text[])`` RPC, and the RPC's OWN report decides the
    outcome. A row that was not confirmed removed - a missing migration, a
    failed call, a concurrent writer that got there first - comes back with
    ``deleted: False`` and the durable tombstone is deliberately NOT written,
    so a caller can never mistake a local catalogue edit for a real Supabase
    delete. There is no row-by-row REST fallback: it cannot share the
    transaction lock with a supplier upsert already in flight and could report
    success while a stale write recreates the row (see
    supabase_store.hard_delete_products).

    The local/development path keeps its own semantics - the override file is
    the source of truth there - and reports ``mode: "local"``, so the
    Supabase path is always distinguishable from a local-only delete. An
    active Supabase mirror is still brought in step, but a mirror failure is
    reported rather than hidden.

    Returns ``None`` for an empty id, otherwise::

        {"id": str, "mode": "hard"|"local", "deleted": bool,
         "files": int, "errors": [str]}
    """
    pid = str(pid or "").strip()
    if not pid:
        return None
    existing_product = None
    try:
        existing_product = next((p for p in merged(include_hidden=True)
                                 if str((p or {}).get("id") or "") == pid), None)
    except Exception:
        existing_product = None

    report = {"id": pid, "mode": "local", "deleted": False, "files": 0,
              "errors": []}

    def _tombstone():
        """Record the durable deleted-ids entry; never hide a failed write."""
        try:
            from supabase_store import add_deleted_id
            if not add_deleted_id(pid):
                report["errors"].append(
                    "durable tombstone write was not confirmed")
        except Exception as exc:
            report["errors"].append(f"durable tombstone: {exc}")

    if _prod_source():
        report["mode"] = "hard"
        try:
            from supabase_store import hard_delete_products
            result = hard_delete_products([pid]) or {}
        except Exception as exc:
            # Fail closed: nothing is deleted, so nothing is claimed deleted
            # and no tombstone is written for a row that may still be live.
            report["errors"].append(f"hard delete RPC raised: {exc}")
            return report
        report["deleted"] = pid in {str(x) for x in (result.get("deleted") or [])}
        report["files"] = int(result.get("files") or 0)
        for err in (result.get("errors") or []):
            report["errors"].append(str(err))
        if not report["deleted"]:
            # The atomic RPC removed nothing. Never answer "deleted" for a row
            # that survived; the caller retries.
            return report
        _tombstone()
        _sync_repo_async()
        return report

    def _apply(data, _path):
        data["products"] = [p for p in (data.get("products") or []) if p.get("id") != pid]
        deleted = list(data.get("deleted") or [])
        if pid not in deleted:
            deleted.append(pid)
        data["deleted"] = deleted
        data["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        data["updatedBy"] = actor or ""
        return data

    _mutate(actor, _apply)
    report["deleted"] = True
    report["files"] = int(_purge_removed_media(existing_product, None) or 0)
    _purge_local_product_rows(pid)
    # When a Supabase mirror is active, bring it in step with the local
    # catalogue. The local file is the source of truth here, so an unreachable
    # mirror is reported, not treated as a failed local delete.
    try:
        from supabase_store import enabled as _sb_enabled
        mirrored = bool(_sb_enabled())
    except Exception:
        mirrored = False
    if mirrored:
        try:
            from supabase_store import hard_delete_products
            result = hard_delete_products([pid]) or {}
            report["files"] = max(report["files"],
                                  int(result.get("files") or 0))
            for err in (result.get("errors") or []):
                report["errors"].append(str(err))
            if pid not in {str(x) for x in (result.get("deleted") or [])}:
                raise RuntimeError("the mirror row was not removed")
        except Exception as exc:
            report["errors"].append(f"supabase mirror hard delete: {exc}")
            # Best-effort legacy soft tombstone so older readers still hide a
            # mirror row that could not be removed outright.
            try:
                from supabase_store import delete_products
                delete_products([pid])
            except Exception as inner:
                report["errors"].append(f"supabase soft tombstone: {inner}")
    _tombstone()
    _sync_repo_async()
    return report


def hide_now(pid, actor=None):
    """Hide a product from the live catalogue at once, without purging anything.

    The async delete needs the shop to stop selling a product on the very next
    read - the Supabase RPC that removes the row and its media is queued and may
    take seconds. This writes exactly the local ``deleted`` list the synchronous
    path writes (and drops any local override row for the id), so ``merged()``
    stops serving it immediately; media and the products-table row are left to
    the worker. Returns True when the local hide landed.
    """
    pid = str(pid or "").strip()
    if not pid:
        return False

    def _apply(data, _path):
        data["products"] = [p for p in (data.get("products") or [])
                            if str((p or {}).get("id") or "") != pid]
        deleted = list(data.get("deleted") or [])
        if pid not in deleted:
            deleted.append(pid)
        data["deleted"] = deleted
        data["updatedAt"] = (datetime.datetime.utcnow()
                             .isoformat(timespec="seconds") + "Z")
        data["updatedBy"] = actor or ""
        return data

    try:
        _mutate(actor, _apply)
        return True
    except Exception as exc:                       # pragma: no cover - best effort
        print(f"[catalog] immediate hide skipped: {exc}")
        return False


def _purge_local_product_rows(pid):
    """Hard-delete the local database rows a product owned.

    variant_stock / product_views / product_reviews rows survive a catalogue
    remove unless they are deleted explicitly - orphaned rows for a gone
    product id (and a views row still "referencing" it confused later
    housekeeping). Best effort: the catalogue removal itself must never be
    blocked by a database hiccup.
    """
    for sql in ("DELETE FROM variant_stock WHERE product_id=?",
                "DELETE FROM product_views WHERE product_id=?",
                "DELETE FROM product_reviews WHERE product_id=?"):
        try:
            from db import execute as _execute
            _execute(sql, (pid,))
        except Exception:
            pass


def replace_all(products, actor=None):
    """Replace the whole admin catalogue (bulk / CSV import).

    Returns (kept, rejected, mirrored). Invalid rows are rejected, never silently dropped.
    In production the replacement lands in Supabase only - the local override
    file is never written.
    """
    kept, rejected = [], []
    live = []
    try:
        live = merged(include_hidden=True)
    except Exception:
        live = []                     # never block a bulk import on a Supabase read
    # Uniquify inside the batch too: a CSV carrying two rows with the same name
    # would otherwise land two products sharing one slug, and _dedupe_products
    # would serve only the first. Rows whose id is in this batch are the same
    # pieces being rewritten, so they are not "taken" - re-importing the same
    # CSV must not churn every product's slug (it is the URL identity).
    incoming = {str((p or {}).get("id") or "") for p in products or [] if p}
    taken = {str(p.get("slug") or "").strip().lower() for p in live
             if p and str(p.get("id") or "") not in incoming and p.get("slug")}
    for i, p in enumerate(products, start=2):
        clean = normalize(p)
        if clean is None:
            rejected.append({"row": i, "name": _name_or(p), "errors": ["missing name"]})
            continue
        clean["slug"] = _free_slug(str(clean.get("slug") or ""), clean["id"], taken)
        if clean["slug"]:
            taken.add(clean["slug"])
        kept.append(clean)

    if _prod_source():
        from supabase_store import replace_all_products
        if not replace_all_products(kept):
            return kept, rejected, False
        _purge_catalog_media_diff(live, kept)
        _sync_repo_async()
        return kept, rejected, True

    def _apply(data, _path):
        data["products"] = kept
        data["deleted"] = []
        data["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        data["updatedBy"] = actor or ""
        return data

    _mutate(actor, _apply)
    _purge_catalog_media_diff(live, kept)
    from supabase_store import replace_all_products
    mirrored = replace_all_products(kept)
    _sync_repo_async()
    return kept, rejected, bool(mirrored)


def _name_or(p):
    return (p or {}).get("name", "") or ""


# ------------------------------------------------------------- category merge
# One-shot boot migration (marker `category_merge_v2`) that folds the old
# `nails` and `packaging` categories into `beauty` and `gift-set`, renames
# `gift-set` to "Gift Sets & Packaging", locks `beauty`'s display name to
# "Beauty", and re-points any product still carrying a merged / legacy category
# (including the old `skincare` id) onto the surviving category. It edits the
# live category table in place and preserves every other owner rename and the
# French translations.

MERGE_MARKER = "category_merge_v2"

# legacy category id -> surviving category id
_CATEGORY_FOLD = {
    "nails": "beauty",
    "packaging": "gift-set",
    "skincare": "beauty",
}

# category id -> display name forced by the merge
_CATEGORY_RENAME = {
    "beauty": "Beauty",
    "gift-set": "Gift Sets & Packaging",
}


def _categories_path():
    import os as _os
    return _os.environ.get(
        "CATEGORIES_PATH",
        _os.path.join(ROOT, "data", "categories.json"))


def _read_categories_file():
    path = _categories_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None, path
    if isinstance(data, dict) and isinstance(data.get("categories"), list):
        return data, path
    if isinstance(data, list):
        return {"categories": data, "updatedAt": "", "updatedBy": ""}, path
    return None, path


def _write_categories_file(data, path):
    import os as _os
    tmp = path + ".tmp"
    _os.makedirs(_os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    _os.replace(tmp, path)


def _merge_marker_get():
    """Read the category-merge marker. Prefer Supabase (survives redeploys).

    The marker used to live only in the local SQLite ``growth_settings``
    table, which sits on Render's ephemeral disk — every redeploy re-ran
    the merge and overwrote the owner's category renames. Supabase is the
    durable home; SQLite is consulted as a same-process / offline fallback
    so a just-written marker is visible even before the next Supabase read,
    and so the test suite (no Supabase) stays idempotent between calls.
    """
    # 1. Durable source of truth.
    try:
        from supabase_store import load_growth_settings, enabled
        if enabled():
            gs = load_growth_settings()
            if isinstance(gs, dict) and gs.get(MERGE_MARKER):
                return gs.get(MERGE_MARKER)
            # Key absent or load failed: fall through to SQLite. A wiped
            # disk + present Supabase key is handled by the branch above; a
            # first boot has neither and correctly runs the merge.
    except Exception:
        pass
    # 2. Local SQLite fallback (tests / offline dev / same-process).
    try:
        from db import one
        row = one("SELECT value FROM growth_settings WHERE key=?", (MERGE_MARKER,))
        if row:
            return row["value"] if isinstance(row, dict) else (
                row[0] if isinstance(row, (tuple, list)) else row)
    except Exception:
        pass
    return None


def _merge_marker_set(value):
    """Persist the category-merge marker to Supabase AND local SQLite."""
    stamp = str(value or "")
    # Durable write first — this is what stops the next deploy from re-running.
    try:
        from supabase_store import mirror_growth_settings, enabled
        if enabled():
            mirror_growth_settings({MERGE_MARKER: stamp})
    except Exception:
        pass
    # Local write so the in-process / test path is idempotent too.
    try:
        from db import execute
        execute("INSERT OR REPLACE INTO growth_settings (key, value) VALUES (?, ?)",
                (MERGE_MARKER, stamp))
    except Exception:
        pass


def merge_categories(actor=None):
    """Apply the category merge once (idempotent, marker `category_merge_v2`).

    Returns True when the merge ran on this boot, False when it had already
    been applied. The marker lives in Supabase growth_settings so a Render
    redeploy cannot re-run the force-renames over the owner's category names.
    """
    if _merge_marker_get():
        return False

    table, path = _read_categories_file()
    changed = False
    if table is not None:
        cats = table.get("categories") or []
        out = []
        for c in cats:
            cid = str(c.get("id") or "").strip()
            if cid in ("nails", "packaging"):     # folded into an existing row
                changed = True
                continue
            if cid in _CATEGORY_RENAME and (c.get("name") or "") != _CATEGORY_RENAME[cid]:
                c["name"] = _CATEGORY_RENAME[cid]
                changed = True
            out.append(c)
        table["categories"] = out
        table["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        table["updatedBy"] = actor or "category_merge_v2"
        if changed:
            _write_categories_file(table, path)

    _fold_product_categories(actor)

    _merge_marker_set(
        datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z")
    return True


def _fold_product_categories(actor=None):
    """Re-point products carrying a merged / legacy category onto the
    surviving category. Admin overrides are edited in place; any seed product
    that still points at a folded category gets an override entry so the live
    merged catalogue is consistent (the seed file itself is never rewritten)."""
    path = _norm_filename(CATALOG_FILE)
    with _catalog_lock(path):
        data, _path = _load_overrides()
        products = [dict(p) for p in (data.get("products") or [])]
        by_id = {p.get("id"): p for p in products}
        changed = False

        for p in products:
            cid = str(p.get("category") or "").strip().lower()
            new = _CATEGORY_FOLD.get(cid)
            if new and str(p.get("category") or "").strip() != new:
                p["category"] = new
                changed = True

        # Fold seed-only products by adding (or fixing) an override entry.
        for s in _seed_products():
            sid = str(s.get("id") or "")
            if not sid:
                continue
            cid = str(s.get("category") or "").strip().lower()
            new = _CATEGORY_FOLD.get(cid)
            if not new:
                continue
            existing = by_id.get(sid)
            if existing is None:
                rec = dict(s)
                rec["category"] = new
                products.append(rec)
                by_id[sid] = rec
                changed = True
            elif str(existing.get("category") or "").strip() != new:
                existing["category"] = new
                changed = True

        if changed:
            data["products"] = products
            data["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
            data["updatedBy"] = actor or "category_merge_v2"
            _write_overrides(data, path)
