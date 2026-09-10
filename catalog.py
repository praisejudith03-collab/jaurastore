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
import os, sys, json, secrets, datetime, contextlib
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
FIXTURE_SKU_PREFIX = "JAUSTOCK"
FIXTURE_NAME_PREFIX = "stock test"

# Prices the shop shows are entered in Naira and converted at the house rate.
NGN_TO_CFA = 0.44

# Every field an admin-edited product may carry (with sensible defaults).
BASE_FIELDS = (
    "id", "sku", "slug", "name", "nameFr", "category", "priceCfa", "compareCfa",
    "priceNgn", "compareNgn", "image", "images", "description", "descriptionFr",
    "stock", "badge", "featured", "online", "colors", "options",
)


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
        p["image"] = own if __import__("config").Config.ENV == "testing" else img
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
        p["placeholderImage"] = PLACEHOLDER_IMG
        p["usesPlaceholder"] = False
        return p
    # A matching committed photo (same slug / alt-<slug>) also wins. This is
    # the auto-wire that makes the owner's collected root photos appear on
    # their products without manually editing every product.
    for candidate in photo_repair_candidates(p):
        if _file_exists(candidate):
            p["image"] = candidate
            p["placeholderImage"] = PLACEHOLDER_IMG
            p["usesPlaceholder"] = False
            return p
    # No usable local file: show the committed branded placeholder.
    p["image"] = PLACEHOLDER_IMG
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


def resolve_images(products):
    """Apply resolve_image to a list of products."""
    return [resolve_image(p) for p in (products or [])]


def _sync_repo_async():
    """Best-effort, non-blocking sync of the repository data state.

    Runs after an admin product write so js/products-data.js (and the repo copy
    of data/catalog.json) reflects the new catalogue immediately. Never raises
    and never delays the product save - the shop must not be blocked by a git
    operation. Only actually runs when REPO_SYNC_ON_WRITE is enabled and the
    app is not running the test suite (which must never touch the git repo).
    """
    if not getattr(Config, "REPO_SYNC_ON_WRITE", True):
        return
    if getattr(Config, "ENV", "development") == "testing":
        return  # never touch the git repo from the test suite
    # ENV alone is NOT a sufficient guard. A test may legitimately flip
    # Config.ENV to "production" to exercise the production code path -
    # test_admin_product_delete_ok_when_supabase_confirms does exactly that -
    # and this function would then spawn a daemon thread running a real
    # `repo_sync.regenerate(commit=True, push=True)` against the live
    # checkout. That rewrote the tracked data/catalog.json and
    # js/products-data.js mid-suite, and the CI harness then committed the
    # test artefacts. Detect the test runner itself instead: pytest sets
    # PYTEST_CURRENT_TEST and stays in sys.modules for the whole process,
    # while no production process ever does either.
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return
    # Import lazily so repo_sync (which imports catalog) is only loaded here,
    # and to avoid a circular import at module load time.
    try:
        import repo_sync
    except Exception:
        return

    def _run():
        try:
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
    """The seed products. Never includes admin edits or deletions."""
    return _seed_products()


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
        cfa = round(ngn * NGN_TO_CFA)
    return ngn, cfa


def _slugify(name):
    slug = (name or "").lower().replace("&", "and").replace("/", " ")
    for ch in ".,()'\"":
        slug = slug.replace(ch, "")
    return "-".join(slug.split())


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
    return str(pid or slug)


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
    raw_id = sec.clean(product.get("id"), 64)
    pid = raw_id or ("jau-" + secrets.token_hex(5))
    ngn, cfa = _derive_cfa(product)
    compare_cfa = _int_or_none(product.get("compareCfa"))
    compare_ngn = _int_or_none(product.get("compareNgn"))
    compare_cfa = max(0, compare_cfa) if compare_cfa is not None else None
    compare_ngn = max(0, compare_ngn) if compare_ngn is not None else None
    image = sec.safe_url(product.get("image_url") or product.get("image") or "")
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
    stock_qty = sec.clean_int(product.get("stock_quantity"),
                              sec.clean_int(product.get("stock"), 24), 0, 10**7)
    out = {
        "id": pid,
        "sku": sec.valid_sku(product.get("sku") or ""),
        "slug": sec.safe_url(product.get("slug") or "") or _slugify(name),
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
        "stock": stock_qty,
        "stock_quantity": stock_qty,
        "badge": sec.clean(product.get("badge"), 20),
        "featured": bool(product.get("featured", False)),
        "online": product.get("online", True) is not False,
        "colors": list(product.get("colors") or []),
        "options": list(product.get("options") or []),
        "optionStock": _clean_option_stock(product.get("optionStock")),
        # The id this row had before it was given a canonical one, so old
        # product links / order lines / reviews keep resolving. See
        # product_index().
        "legacyId": _clean_legacy_id(product.get("legacyId"), pid),
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
    try:
        from supabase_store import load_deleted_ids
        ids = load_deleted_ids()
    except Exception:
        return set()
    if ids is None:
        return set()
    return {str(x).strip() for x in ids if str(x or "").strip()}


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

    if not include_hidden:
        products = [p for p in products if p.get("online") is not False]

    # Read-time category fold: never serve a merged / legacy category id
    # (nails, packaging, skincare) even when the source row still carries one.
    # This mirrors _fold_product_categories() but works for every backend
    # (Supabase rows and seed products pass through merged() unchanged).
    products = [_fold_p(p) for p in products]
    return resolve_images(products)


def _fold_p(product):
    """Return a copy of ``product`` with any merged/legacy category remapped."""
    p = dict(product or {})
    p["category"] = _folded_category(p.get("category"))
    return p


def meta():
    """Metadata blob used for ETag / change detection on the catalogue."""
    data, _p = _load_overrides()
    products = merged(include_hidden=True)
    latest_update = max((str(p.get("updated_at") or "") for p in products), default="")
    return {
        "updatedAt": max(str(data.get("updatedAt") or ""), latest_update),
        "updatedBy": data.get("updatedBy") or "",
        "count": len(products),
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
            return reserve_product_stock(pid, -qty_delta)
        return release_product_stock(pid, qty_delta)

    found = None
    for p in merged(include_hidden=True):
        if str(p.get("id")) == pid:
            found = p
            break
    if found is None:
        return None
    rec = dict(found)
    try:
        stock = int(rec.get("stock_quantity", rec.get("stock")) or 0)
    except (TypeError, ValueError):
        stock = 0
    new_stock = max(0, stock + qty_delta)
    # keep the canonical Supabase column AND the legacy alias in sync, or
    # normalize() (which prefers stock_quantity) would silently revert it
    rec["stock"] = new_stock
    rec["stock_quantity"] = new_stock
    os_map = rec.get("optionStock")
    if option_key and isinstance(os_map, dict) and os_map:
        os_map = dict(os_map)
        key = str(option_key)
        matched = key if key in os_map else None
        if matched is None:
            want = "".join(c.lower() for c in key if c.isalnum())
            for k in os_map:
                fk = "".join(c.lower() for c in str(k) if c.isalnum())
                if fk and fk == want:
                    matched = k
                    break
        if matched is not None:
            try:
                cur = int(os_map[matched] or 0)
            except (TypeError, ValueError):
                cur = 0
            os_map[matched] = max(0, cur + qty_delta)
            rec["optionStock"] = os_map
    upsert(rec, actor=actor or "stock")
    return rec


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


def _own_photo_ref(value):
    """A normalised reference for one of OUR stored photos, or "".

    Both shapes a row can carry resolve to the same thing: a same-origin
    ``/uploads/<key>`` link and one of our bucket URLs are the same object.
    Foreign hosts (and data:/blob: values) return "" and are never touched.
    """
    return _own_upload_path(value)


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

    action = "updated" if any(str((p or {}).get("id") or "") == clean["id"]
                              for p in live) else "created"

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
    _sync_repo_async()
    return clean, action, mirrored


def remove(pid, actor=None):
    """Soft-delete one product: it is dropped from the live catalogue.

    Always records the id in the durable Supabase tombstone list so a seed
    product stays invisible across Render redeploys (the local override
    ``deleted`` list alone is wiped with the disk).
    """
    pid = str(pid or "").strip()
    if not pid:
        return None

    def _tombstone():
        try:
            from supabase_store import add_deleted_id
            add_deleted_id(pid)
        except Exception:
            pass

    if _prod_source():
        from supabase_store import delete_products
        delete_products([pid])
        _tombstone()
        _sync_repo_async()
        return None

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
    from supabase_store import delete_products
    delete_products([pid])
    _tombstone()
    _sync_repo_async()
    return None


def replace_all(products, actor=None):
    """Replace the whole admin catalogue (bulk / CSV import).

    Returns (kept, rejected). Invalid rows are rejected, never silently dropped.
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
        replace_all_products(kept)
        _sync_repo_async()
        return kept, rejected

    def _apply(data, _path):
        data["products"] = kept
        data["deleted"] = []
        data["updatedAt"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        data["updatedBy"] = actor or ""
        return data

    _mutate(actor, _apply)
    from supabase_store import replace_all_products
    replace_all_products(kept)
    _sync_repo_async()
    return kept, rejected


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
