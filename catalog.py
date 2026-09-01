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
import os, json, secrets, datetime, contextlib
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

# Prices the shop shows are entered in Naira and converted at the house rate.
NGN_TO_CFA = 0.44

# Every field an admin-edited product may carry (with sensible defaults).
BASE_FIELDS = (
    "id", "sku", "slug", "name", "nameFr", "category", "priceCfa", "compareCfa",
    "priceNgn", "compareNgn", "image", "images", "description", "stock", "badge",
    "featured", "online", "colors", "options",
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
    # Strip any third-party / Wix URL that may still be present in the data.
    if img.startswith(("http://", "https://", "data:", "blob:")):
        img = ""
    p.pop("imageUrl", None)
    p.pop("usesRemoteImage", None)
    # A committed repo photo wins (the user asked to link repository paths).
    if _is_local(img) and _file_exists(img):
        p["image"] = img
        p["placeholderImage"] = PLACEHOLDER_IMG
        p.pop("usesPlaceholder", None)
        return p
    # A matching committed photo (same slug / alt-<slug>) also wins. This is
    # the auto-wire that makes the owner's collected root photos appear on
    # their products without manually editing every product.
    for candidate in photo_repair_candidates(p):
        if _file_exists(candidate):
            p["image"] = candidate
            p["placeholderImage"] = PLACEHOLDER_IMG
            p.pop("usesPlaceholder", None)
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
    """priceCfa is derived from priceNgn at the house rate when not explicit."""
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
    if ngn and ngn > 0 and (cfa is None or cfa <= 0):
        cfa = round(ngn * NGN_TO_CFA)
    return ngn, cfa


def _slugify(name):
    slug = (name or "").lower().replace("&", "and").replace("/", " ")
    for ch in ".,()'\"":
        slug = slug.replace(ch, "")
    return "-".join(slug.split())


def normalize(product):
    """Clean an incoming product into a safe, complete shape."""
    import security as sec
    product = dict(product or {})
    name = sec.clean(product.get("name"), 200)
    if not name:
        return None
    raw_id = sec.clean(product.get("id"), 64)
    pid = raw_id or ("jau-" + secrets.token_hex(5))
    ngn, cfa = _derive_cfa(product)
    out = {
        "id": pid,
        "sku": sec.valid_sku(product.get("sku") or ""),
        "slug": sec.safe_url(product.get("slug") or "") or _slugify(name),
        "name": name,
        "nameFr": sec.clean(product.get("nameFr"), 200),
        "category": sec.clean(product.get("category"), 40),
        "priceCfa": cfa if cfa is not None else 0,
        "compareCfa": _int_or_none(product.get("compareCfa")),
        "priceNgn": ngn if ngn is not None else 0,
        "compareNgn": _int_or_none(product.get("compareNgn")),
        "image": sec.safe_url(product.get("image") or ""),
        "images": [sec.safe_url(i) for i in (product.get("images") or []) if sec.safe_url(i)],
        "description": sec.clean(product.get("description"), 2000),
        "stock": sec.clean_int(product.get("stock"), 24, 0, 10**7),
        "badge": sec.clean(product.get("badge"), 20),
        "featured": bool(product.get("featured", False)),
        "online": product.get("online", True) is not False,
        "colors": list(product.get("colors") or []),
        "options": list(product.get("options") or []),
        "optionStock": _clean_option_stock(product.get("optionStock")),
    }
    return out


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
def _dedupe_products(primary, secondary):
    """Merge two product lists, keeping every distinct product exactly once.

    ``primary`` (Supabase - the live source of truth) wins: when the same
    piece appears in both lists it is matched by id, then slug, then sku, and
    the primary version is kept. Products only present in ``secondary`` (the
    shipped seed) are appended, so nothing is lost when one side has not
    synced yet - but the same product never renders twice.

    This is what removes the duplicates shoppers saw: a Supabase row whose id
    differs from the seed row of the same product (a re-created or re-imported
    item) used to be unioned in as a second copy of that product.
    """
    def _key(v):
        return str(v or "").strip().lower()

    by_id, by_slug, by_sku = {}, {}, {}
    out = []

    def _place(p):
        pid = str((p or {}).get("id") or "").strip()
        if not pid:
            return False
        slug = _key(p.get("slug"))
        sku = _key(p.get("sku"))
        if pid in by_id or (slug and slug in by_slug) or (sku and sku in by_sku):
            return False
        by_id[pid] = p
        if slug:
            by_slug[slug] = p
        if sku:
            by_sku[sku] = p
        out.append(p)
        return True

    for p in (primary or []):
        _place(p)
    for p in (secondary or []):
        _place(p)
    return out


def merged(include_hidden=False):
    """Seed products + every admin edit, minus what was deleted.

    This is the live catalogue. When Supabase is configured it is the source
    of truth; otherwise the local override file supplies the edits.
    """
    sb = _supabase_products()
    if sb is not None:
        # Supabase rows are the live catalogue; the seed only supplies
        # products Supabase does not have. _dedupe_products keeps one copy of
        # each product (matched by id, then slug, then sku), so a row saved
        # under a different id never shows up next to its own duplicate.
        deleted = set(overrides().get("deleted") or [])
        products = _dedupe_products(sb, _seed_products())
        products = [p for p in products if str(p.get("id")) not in deleted]
    else:
        data, _p = _load_overrides()
        overrides_list = data.get("products") or []
        deleted = set(data.get("deleted") or [])
        by_id = {p["id"]: p for p in _seed_products()}
        for p in overrides_list:
            by_id[p["id"]] = p
        products = [p for pid, p in by_id.items() if pid not in deleted]

    if not include_hidden:
        products = [p for p in products if p.get("online") is not False]
    return resolve_images(products)


def meta():
    """Metadata blob used for ETag / change detection on the catalogue."""
    data, _p = _load_overrides()
    products = merged(include_hidden=True)
    return {
        "updatedAt": data.get("updatedAt") or "",
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


def upsert(product, actor=None):
    """Save (create or edit) one product. Returns (product, action)."""
    clean = normalize(product)
    if clean is None:
        return None, "rejected"

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
    from supabase_store import upsert_products
    upsert_products([clean])
    _sync_repo_async()
    return clean, action


def remove(pid, actor=None):
    """Soft-delete one product: it is dropped from the live catalogue."""
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
    _sync_repo_async()
    return None


def replace_all(products, actor=None):
    """Replace the whole admin catalogue (bulk / CSV import).

    Returns (kept, rejected). Invalid rows are rejected, never silently dropped.
    """
    kept, rejected = [], []
    for i, p in enumerate(products, start=2):
        clean = normalize(p)
        if clean is None:
            rejected.append({"row": i, "name": _name_or(p), "errors": ["missing name"]})
            continue
        kept.append(clean)

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
