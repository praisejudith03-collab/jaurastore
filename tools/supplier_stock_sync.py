#!/usr/bin/env python3
"""Fully autonomous supplier catalog + stock sync for splendall.com.

No manual per-product mapping is required. Runs twice a day, morning and
evening (see .github/workflows/supplier-stock-sync.yml), and each run,
unattended:

  1. CRAWLS splendall's entire live storefront catalog itself (every page of
     https://www.splendall.com/wp-json/wc/store/v1/products) - new products
     the supplier adds are discovered automatically, with no admin action.
  2. MATCHES each of your still-unmapped products against that catalog by
     PHOTO, not by name. Owner request 2026-09-28: "Never match products
     based on generic names or recurring titles ... perform an exact
     image-to-image check ... to confirm a 100% visual match before
     updating any product variant." A product is only ever auto-linked when
     its own photo and the supplier's photo are, for all practical
     purposes, the exact same image (a near-zero Hamming distance on a
     64-bit perceptual hash - see IMAGE_EXACT_SCORE_FLOOR); product name
     similarity is used only as a cheap pre-filter (so one store product
     cannot trigger a photo download for all 1,000+ supplier items every
     run) and to rank the human review queue - it can NEVER, by itself,
     however strong, grant an auto-link. Anything not verified by photo is
     left alone and written to a review file instead of being guessed.
  3. AUDITS every linked product at option level. Variant names, colour/hex
     codes, shade numbers and exact stock quantities are read from Splendall's
     product/variation responses. Every local option combination must match
     exactly one supplier variation (and vice versa) before the complete
     optionStock map is replaced atomically. A partial match changes nothing.

  4. MARKS a previously-linked product OUT OF STOCK the instant it
     disappears from the supplier's live catalog - sets its stock to 0 and
     otherwise leaves it completely alone (still online, still visible, the
     storefront's normal "Out of stock" card is what the shopper sees).
     Nothing is auto-hidden and nothing is auto-deleted; the owner reviews a
     zeroed product and decides whether to hide or delete it themselves.
  5. PERSISTS every uncertainty (missing image, changed reply layout, unknown
     quantity, ambiguous option mapping, or possible new match) to Supabase.
     Admin shows a warning badge, a Needs attention card, highlighted product
     cards and an editor alert. Uncertain stock is never guessed or changed.
     Possible new matches are also emailed through the existing order-alert
     transport as a backup notification.

Usage
-----
    python3 tools/supplier_stock_sync.py                 # full autonomous run
    python3 tools/supplier_stock_sync.py --dry-run        # report only, write nothing
    python3 tools/supplier_stock_sync.py --no-discover     # skip crawl/auto-link/discontinued check;
                                                             # only re-sync already-linked products
    python3 tools/supplier_stock_sync.py jau-abc123        # sync one already-linked product only
                                                             # (also skips discovery, for diagnostics)

Runs safely on a schedule (cron / GitHub Actions), exactly like
tools/catalog_watchdog.py. Without SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
configured it still works: every write falls back to the local catalogue
override file, the same path an admin edit uses. It never raises past
main() and always exits 0 on a clean run - a supplier site that is briefly
unreachable, slow, or serving a broken page must never fail a deploy or a
scheduled job; it just skips this run's autonomous steps and tries again
next time.

Retail price independence
--------------------------
This script NEVER reads or writes priceNgn, priceCfa, compareNgn or
compareCfa. It only ever changes: stock / stock_quantity / optionStock
(the exact supplier count, or 0 once a linked item disappears from the
supplier), and supplierId / supplierSku (only when linking a new product).
`online` is NEVER touched by this script. You keep full, untouched control
of your own retail markup - drop-shipped stock quantity and your selling
price are completely independent of one another.

Safety guardrails - what can NEVER be touched
-----------------------------------------------
* A product already carrying a DIFFERENT non-empty supplierId (i.e. you
  sourced it from somewhere else, or explicitly marked it "not this
  supplier") is never reconsidered by auto-discovery.
* PROTECTED_KEYWORDS (below) is a hard denylist checked before ANY
  discovery, auto-link or discontinue action: a product whose name,
  category, slug or SKU contains one of these words - "ankara" today - is
  refused outright, loudly, even if it would otherwise score a perfect
  match. This is not how a product is EVER added to scope; it is only ever
  a way to keep one OUT of scope.
* Auto-linking a brand-new product requires a VERIFIED near-identical photo
  (both images readable, Hamming distance <= IMAGE_EXACT_HAMMING_MAX) -
  name similarity is never sufficient by itself, no matter how strong.
  Anything merely plausible (by name, or by a photo that is similar but not
  a verified exact match) is written to supplier_match_review.json for a
  human to confirm - never linked on a guess.
* The supplier's live catalog is only trusted for auto-linking and
  discontinuation once it returns a healthy number of products
  (MIN_CATALOG_SIZE) - a partial or failed crawl never triggers a mass
  zero-out, and never blocks the ordinary per-product stock refresh below.
* A discontinued item is only ever marked OUT OF STOCK (stock: 0) - never
  hidden (online is untouched) and never hard deleted. Owner request
  2026-09-28: the product stays fully visible, exactly like any other item
  that sells out, until the owner decides by hand whether to hide or delete
  it.


Config (env vars, all optional)
--------------------------------
  SPLENDALL_BASE_URL      default "https://www.splendall.com"
  SUPPLIER_SYNC_TIMEOUT   default "12" (seconds per HTTP request)
  STORE_BASE_URL          default "https://jaurastore.com.ng" (used only to
                           fetch OUR OWN product photo when its stored path
                           is relative, for the visual-fingerprint step)
  MAIL_FROM, ADMIN_EMAIL, RESEND_API_KEY / BREVO_API_KEY / SMTP_* - the
                           review-needed email (step 5). Reused as-is from
                           mailer.py/config.py; nothing supplier-sync-
                           specific to set up if the shop's mail is already
                           configured (e.g. on Render).
"""
import datetime
import difflib
import io
import itertools
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import catalog  # noqa: E402
import supabase_store  # noqa: E402

SUPPLIER_ID = "splendall"
assert SUPPLIER_ID in catalog.KNOWN_SUPPLIERS, (
    "supplier_stock_sync.SUPPLIER_ID must be one of catalog.KNOWN_SUPPLIERS")

BASE_URL = os.environ.get("SPLENDALL_BASE_URL", "https://www.splendall.com").rstrip("/")
STORE_BASE_URL = os.environ.get("STORE_BASE_URL", "https://jaurastore.com.ng").rstrip("/")
TIMEOUT = float(os.environ.get("SUPPLIER_SYNC_TIMEOUT", "12") or 12)
USER_AGENT = "JauraStoreStockSync/1.0 (+https://jaurastore.com.ng)"

CACHE_FILE = os.path.join(ROOT, "supplier_sync_cache.json")
REVIEW_FILE = os.path.join(ROOT, "supplier_match_review.json")
WARNING_FILE = os.path.join(ROOT, "supplier_sync_warnings.json")

_STOCK_NUMBER_RE = re.compile(r"([\d][\d,]*)\s*in stock", re.IGNORECASE)

# A crawl smaller than this is treated as broken/partial, not "the supplier
# genuinely only has a handful of products" - it never drives auto-linking
# or the discontinued-item check, so a supplier hiccup can never mass-hide
# your catalogue. The ordinary per-product refresh (already-linked items)
# is a separate, independent code path and is unaffected by this gate.
MIN_CATALOG_SIZE = 10

# Matching thresholds (0..1). A candidate below MIN_NAME_FLOOR is discarded
# before its photo is even downloaded - purely a cheap pre-filter so one
# store product cannot trigger a fingerprint download/hash for all 1,000+
# supplier photos every run. It is NOT how a product earns an auto-link
# (see IMAGE_EXACT_SCORE_FLOOR below) - a name, however similar, never
# grants one on its own.
MIN_NAME_FLOOR = 0.35
REVIEW_THRESHOLD = 0.55

# Owner request 2026-09-28: "Never match products based on generic names or
# recurring titles (e.g. words like 'bag'). ... perform an exact
# image-to-image check ... to confirm a 100% visual match before updating
# any product variant." Auto-linking (writing supplierId/supplierSku
# unattended) is therefore gated ENTIRELY on the product photo, never on
# name similarity, however strong: a dHash Hamming distance this low, out
# of 64 bits, only happens when the two pictures are - for all practical
# purposes, allowing for the resizing/recompression every CDN applies -
# the exact same photo. A name match is still used to rank the REVIEW
# queue (a human decides those) and as the cheap pre-filter above, but it
# can never, by itself, cross into "auto".
IMAGE_EXACT_HAMMING_MAX = 6
IMAGE_EXACT_SCORE_FLOOR = 1.0 - (IMAGE_EXACT_HAMMING_MAX / 64.0)   # ~0.906
MAX_IMAGE_BYTES = 6 * 1024 * 1024

# Belt-and-braces guard: even if a product were ever mistakenly linked to a
# supplier, a name/category/slug/sku that matches one of these keywords is
# refused, loudly, instead of silently synced. This can only ever REMOVE a
# product from scope - it is never how a product is added to scope (a
# confident auto-match, or an existing supplierId/supplierSku, is).
PROTECTED_KEYWORDS = ("ankara",)


class SupplierFetchError(Exception):
    """Any reason the supplier's exact stock number could not be trusted.

    ``code`` is deliberately stable: the admin badge can distinguish a
    supplier layout break from an option-mapping ambiguity without parsing a
    human sentence.
    """
    def __init__(self, message, code="supplier_unconfirmed", variants=None):
        super().__init__(message)
        self.code = str(code or "supplier_unconfirmed")
        self.variants = list(variants or [])


# ------------------------------------------------------------- scope guard
def _is_protected(product):
    haystack = " ".join(
        str(product.get(k) or "") for k in ("name", "nameFr", "category", "slug", "sku")
    ).lower()
    return any(kw in haystack for kw in PROTECTED_KEYWORDS)


def _slug_from_sku(supplier_sku):
    """Return the bare WooCommerce product slug from a slug or a full URL."""
    raw = str(supplier_sku or "").strip()
    if not raw:
        return ""
    path = urllib.parse.urlparse(raw).path if "://" in raw else raw
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""
    # https://www.splendall.com/product/<slug>/ -> the slug is the last segment.
    return parts[-1]


# ------------------------------------------------------------------- HTTP
def _http_get_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status = getattr(resp, "status", 200)
            if status != 200:
                raise SupplierFetchError(f"HTTP {status}")
            body = resp.read()
    except urllib.error.URLError as exc:
        raise SupplierFetchError(f"network error: {exc}") from exc
    except TimeoutError as exc:
        raise SupplierFetchError(f"timed out: {exc}") from exc
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SupplierFetchError(f"could not parse supplier reply as JSON: {exc}") from exc


def _fetch_bytes(ref):
    """Best-effort binary fetch for a photo. Never raises - returns None."""
    ref = str(ref or "").strip()
    if not ref:
        return None
    candidates = []
    if ref.startswith(("http://", "https://")):
        candidates.append(ref)
    else:
        local = os.path.join(ROOT, ref.lstrip("/"))
        if os.path.isfile(local):
            try:
                with open(local, "rb") as fh:
                    data = fh.read(MAX_IMAGE_BYTES + 1)
                return data if len(data) <= MAX_IMAGE_BYTES else None
            except OSError:
                return None
        candidates.append(f"{STORE_BASE_URL}/{ref.lstrip('/')}")
    for url in candidates:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = resp.read(MAX_IMAGE_BYTES + 1)
            if len(data) <= MAX_IMAGE_BYTES:
                return data
        except Exception:
            continue
    return None


# -------------------------------------------------------- stock parsing
def _quantity_from_row(row):
    """Shared by the single-product fetch and the bulk crawl - one parser,
    so the two paths can never quietly disagree about what a reply means."""
    in_stock = row.get("is_in_stock")
    if in_stock is False:
        return 0, "supplier reports out of stock"

    text = str((row.get("stock_availability") or {}).get("text") or "")
    match = _STOCK_NUMBER_RE.search(text)
    if match:
        try:
            return int(match.group(1).replace(",", "")), text
        except ValueError:
            pass  # fall through to the next signal instead of guessing

    # A plain "In stock" boolean confirms availability but not quantity.
    # add_to_cart.maximum can be a merchant's per-order cap rather than the
    # remaining stock, so treating it as inventory would only look precise.
    # Fail closed unless Splendall publishes the real number.
    raise SupplierFetchError(f"could not read an exact stock number from supplier reply: {text!r}",
                             code="exact_quantity_unavailable")


def _supplier_product_detail(supplier_sku):
    """Fetch one exact product row, then its detail endpoint when possible."""
    slug = _slug_from_sku(supplier_sku)
    if not slug:
        raise SupplierFetchError(f"could not read a product slug out of {supplier_sku!r}",
                                 code="missing_supplier_slug")
    url = f"{BASE_URL}/wp-json/wc/store/v1/products?" + urllib.parse.urlencode({"slug": slug})
    data = _http_get_json(url)
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise SupplierFetchError(
            f"supplier did not return one unambiguous product for slug {slug!r}",
            code="supplier_product_ambiguous")
    row = dict(data[0])
    if str(row.get("slug") or "") != slug:
        raise SupplierFetchError(f"supplier returned a different slug for {slug!r}",
                                 code="supplier_product_mismatch")
    product_id = row.get("id")
    if product_id is not None:
        detail_url = f"{BASE_URL}/wp-json/wc/store/v1/products/{urllib.parse.quote(str(product_id))}"
        try:
            detail = _http_get_json(detail_url)
            if isinstance(detail, dict) and str(detail.get("slug") or "") == slug:
                row = detail
        except SupplierFetchError:
            # The collection result still contains the stock contract. A
            # variation product is stricter below and cannot use this fallback.
            pass
    images = row.get("images") or []
    if not (isinstance(images, list) and any(isinstance(i, dict) and i.get("src") for i in images)):
        raise SupplierFetchError("supplier product has no confirmable image",
                                 code="missing_supplier_image")
    return row


def fetch_supplier_quantity(supplier_sku):
    """Return an exact simple-product quantity; never infer an option total."""
    row = _supplier_product_detail(supplier_sku)
    variations = row.get("variations") or []
    if row.get("has_variations") is True or variations:
        raise SupplierFetchError("supplier product has variants; option-level audit is required",
                                 code="variants_require_exact_mapping")
    return _quantity_from_row(row)


def _attribute_name(raw):
    name = str(raw or "").strip().lower().replace("colour", "color")
    name = re.sub(r"^(attribute_|pa[_-])", "", name)
    return re.sub(r"[^a-z0-9]+", "", name)


def _attribute_value(raw):
    # Keep the actual shade name/number/hex code in the audit payload. This
    # folded form is used only for an exact punctuation/case-insensitive key.
    return re.sub(r"[^a-z0-9]+", "", str(raw or "").strip().lower())


def _variant_attributes(row):
    attrs = row.get("attributes") if isinstance(row, dict) else None
    pairs = []
    if isinstance(attrs, dict):
        attrs = [{"name": k, "value": v} for k, v in attrs.items()]
    if not isinstance(attrs, list):
        return []
    for attr in attrs:
        if not isinstance(attr, dict):
            continue
        title = attr.get("name") or attr.get("attribute") or attr.get("taxonomy")
        value = attr.get("value") or attr.get("option") or attr.get("term")
        if isinstance(value, dict):
            value = value.get("name") or value.get("slug")
        clean_title, clean_value = str(title or "").strip(), str(value or "").strip()
        if clean_title and clean_value:
            pairs.append((clean_title, clean_value))
    return pairs


def _variation_detail(ref):
    if isinstance(ref, dict) and _variant_attributes(ref) and (
            "is_in_stock" in ref or "stock_availability" in ref or "add_to_cart" in ref):
        return dict(ref)
    variation_id = ref.get("id") if isinstance(ref, dict) else ref
    if variation_id is None or str(variation_id).strip() == "":
        raise SupplierFetchError("supplier variation has no id", code="variation_layout_changed")
    quoted = urllib.parse.quote(str(variation_id))
    attempts = [
        f"{BASE_URL}/wp-json/wc/store/v1/products/{quoted}",
        f"{BASE_URL}/wp-json/wc/store/v1/products?" + urllib.parse.urlencode({"include": variation_id}),
    ]
    for url in attempts:
        try:
            data = _http_get_json(url)
        except SupplierFetchError:
            continue
        row = data[0] if isinstance(data, list) and len(data) == 1 else data
        if isinstance(row, dict) and _variant_attributes(row):
            return row
    raise SupplierFetchError(f"could not fetch exact supplier variation {variation_id}",
                             code="variation_detail_unavailable")


def _store_variant_rows(product):
    options = []
    for opt in product.get("options") or []:
        if not isinstance(opt, dict):
            continue
        title = str(opt.get("title") or "").strip()
        values = [str(v).strip() for v in (opt.get("values") or []) if str(v).strip()]
        if title and values:
            options.append((title, values))
    if not options and product.get("colors"):
        values = [str(v).strip() for v in product.get("colors") if str(v).strip()]
        if values:
            options = [("Color", values)]
    if not options:
        return []
    rows = []
    for values in itertools.product(*(vals for _title, vals in options)):
        raw_pairs = list(zip((title for title, _vals in options), values))
        signature = tuple(sorted((_attribute_name(k), _attribute_value(v)) for k, v in raw_pairs))
        key = values[0] if len(values) == 1 else " · ".join(
            f"{title}: {value}" for (title, _vals), value in zip(options, values))
        rows.append({"key": key, "signature": signature, "options": [
            {"name": title, "value": value} for (title, _vals), value in zip(options, values)
        ]})
    return rows


def fetch_supplier_variant_stock(product):
    """Return an exact optionStock map plus a transparent supplier audit.

    Every local option combination must map bijectively to one supplier
    variation. Missing, duplicate or extra variants fail the entire product;
    no partial stock update is ever written.
    """
    row = _supplier_product_detail(product.get("supplierSku"))
    refs = row.get("variations") or []
    store_rows = _store_variant_rows(product)
    if not store_rows:
        raise SupplierFetchError("store product has no options to map to supplier variants",
                                 code="store_options_missing")
    if not isinstance(refs, list) or not refs:
        raise SupplierFetchError("supplier detail contains no variation records",
                                 code="supplier_variations_missing")
    supplier = {}
    audit = []
    for ref in refs:
        detail = _variation_detail(ref)
        attrs = _variant_attributes(detail)
        signature = tuple(sorted((_attribute_name(k), _attribute_value(v)) for k, v in attrs))
        if not signature or any(not k or not v for k, v in signature):
            raise SupplierFetchError("supplier variation has ambiguous option attributes",
                                     code="variant_attributes_ambiguous", variants=audit)
        if signature in supplier:
            raise SupplierFetchError("supplier returned duplicate option variants",
                                     code="duplicate_supplier_variant", variants=audit)
        qty, stock_detail = _quantity_from_row(detail)
        item = {
            "supplier_variant_id": detail.get("id"),
            "name": str(detail.get("name") or row.get("name") or ""),
            "options": [{"name": k, "value": v} for k, v in attrs],
            "quantity": qty,
            "status": "in_stock" if qty > 0 else "out_of_stock",
            "detail": stock_detail,
            "image": str(((detail.get("images") or [{}])[0] or {}).get("src") or
                         ((row.get("images") or [{}])[0] or {}).get("src") or ""),
        }
        supplier[signature] = item
        audit.append(item)
    expected = {r["signature"] for r in store_rows}
    if len(expected) != len(store_rows) or len({r["key"] for r in store_rows}) != len(store_rows):
        raise SupplierFetchError("store options collapse into duplicate variant keys",
                                 code="store_option_mapping_ambiguous", variants=audit)
    actual = set(supplier)
    if expected != actual:
        missing = len(expected - actual)
        extra = len(actual - expected)
        raise SupplierFetchError(
            f"option mapping is not exact ({missing} store variant(s) missing, {extra} supplier variant(s) extra)",
            code="option_mapping_ambiguous", variants=audit)
    option_stock = {r["key"]: supplier[r["signature"]]["quantity"] for r in store_rows}
    return option_stock, audit


# -------------------------------------------------- autonomous catalog crawl
def _parse_supplier_row(row):
    if not isinstance(row, dict):
        return None
    slug = str(row.get("slug") or "").strip()
    name = str(row.get("name") or "").strip()
    if not slug or not name:
        return None
    try:
        qty, detail = _quantity_from_row(row)
    except SupplierFetchError:
        qty, detail = None, "stock unknown"
    images = row.get("images") or []
    image_url = ""
    if isinstance(images, list) and images and isinstance(images[0], dict):
        image_url = str(images[0].get("src") or "")
    return {
        "slug": slug, "name": name, "qty": qty, "detail": detail,
        "image_url": image_url, "permalink": str(row.get("permalink") or ""),
    }


def crawl_supplier_catalog(max_pages=60, per_page=100):
    """Autonomously fetch splendall's ENTIRE live catalog. None on any failure.

    No admin ever has to tell this function which products exist - every
    page of the supplier's own public product listing is walked until a
    short page (or an empty one) says the end has been reached. Any HTTP or
    parse failure on ANY page aborts and returns None rather than reporting
    a partial catalog as if it were complete - a partial view could wrongly
    make a still-selling product look "discontinued".
    """
    rows = []
    page = 1
    while page <= max_pages:
        url = f"{BASE_URL}/wp-json/wc/store/v1/products?" + urllib.parse.urlencode(
            {"page": page, "per_page": per_page})
        try:
            data = _http_get_json(url)
        except SupplierFetchError as exc:
            print(f"warning: supplier catalog crawl failed on page {page}: {exc}")
            return None
        if not isinstance(data, list):
            print(f"warning: supplier catalog crawl got an unexpected reply shape "
                  f"on page {page} - aborting this run's crawl")
            return None
        if not data:
            break
        for row in data:
            parsed = _parse_supplier_row(row)
            if parsed:
                rows.append(parsed)
        if len(data) < per_page:
            break
        page += 1
    else:
        print(f"warning: supplier catalog crawl stopped at the {max_pages}-page "
              "safety cap - some products may not have been seen this run")
    return rows


# -------------------------------------------------------------- fingerprint
def _dhash(data, hash_size=8):
    """A 64-bit perceptual "difference hash" of an image - cheap, needs only
    Pillow (already a dependency), and tolerant of resizing/re-compression."""
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("L").resize((hash_size + 1, hash_size))
    pixels = img.tobytes()  # one byte per pixel in "L" mode, row-major order
    value = 0
    for row in range(hash_size):
        offset = row * (hash_size + 1)
        for col in range(hash_size):
            value = (value << 1) | (1 if pixels[offset + col] > pixels[offset + col + 1] else 0)
    return value


def _hamming(a, b):
    return bin(a ^ b).count("1")


def _load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        return True
    except OSError as exc:
        print(f"warning: could not save {CACHE_FILE}: {exc}")
        return False


def _image_hash_for_ref(ref, cache, cache_key):
    """A cached perceptual hash for one photo. None on any failure - image
    matching is always best-effort and never blocks a run."""
    ref = str(ref or "").strip()
    if not ref:
        return None
    entry = cache.get(cache_key)
    if isinstance(entry, dict) and entry.get("url") == ref and isinstance(entry.get("hash"), int):
        return entry["hash"]
    data = _fetch_bytes(ref)
    if not data:
        return None
    try:
        h = _dhash(data)
    except Exception:
        return None
    cache[cache_key] = {"url": ref, "hash": h}
    return h


# ----------------------------------------------------------------- matching
def _normalize_name(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _name_similarity(a, b):
    a, b = _normalize_name(a), _normalize_name(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _combined_score(name_score, image_score):
    """A blended score used ONLY to rank/gate the human review queue - it
    has no say over auto-linking (see IMAGE_EXACT_SCORE_FLOOR, checked
    separately and exclusively on the raw image_score in discover_matches).
    Without a readable photo pair the name carries the whole score, damped
    so a name-only candidate can still reach the review band but is never
    mistaken for a verified visual match."""
    if image_score is None:
        return name_score * 0.85
    return 0.45 * name_score + 0.55 * image_score


def discover_matches(store_products, supplier_rows, cache):
    """Score every eligible, still-unmapped store product against the crawl.

    Returns (auto, review):
      * auto = [(product, supplier_row, score), ...] - a product only ever
        lands here because ITS OWN PHOTO and the supplier's photo are, for
        all practical purposes, the exact same image (a dHash Hamming
        distance <= IMAGE_EXACT_HAMMING_MAX). Name similarity, however
        strong, NEVER earns a place here by itself - owner request
        2026-09-28: no auto-link on a generic/recurring name alone.
      * review = the same shape, ranked by the blended name+image score,
        for a human to confirm - never applied automatically. A product
        with no verified photo match still reaches this list on a strong
        enough name (or a plausible-but-not-quite-exact photo), exactly as
        before.
    """
    auto, review = [], []
    for p in store_products:
        if not p or catalog.is_permanently_removed(p):
            continue
        if str(p.get("supplierId") or "").strip():
            continue  # already mapped (to this or any supplier) - not reconsidered
        if _is_protected(p):
            continue
        store_ref = p.get("image_url") or p.get("image") or ""
        store_hash = _image_hash_for_ref(store_ref, cache, f"store:{p.get('id')}") if store_ref else None

        visual_match = None     # (row, combined) - a verified, near-identical photo
        review_best = None      # (row, combined) - best blended score, for review ranking
        for row in supplier_rows:
            name_score = _name_similarity(p.get("name"), row.get("name"))
            if name_score < MIN_NAME_FLOOR:
                continue
            image_score = None
            if store_hash is not None and row.get("image_url"):
                supplier_hash = _image_hash_for_ref(row["image_url"], cache, f"supplier:{row['slug']}")
                if supplier_hash is not None:
                    image_score = max(0.0, 1.0 - (_hamming(store_hash, supplier_hash) / 64.0))
            combined = _combined_score(name_score, image_score)
            if review_best is None or combined > review_best[1]:
                review_best = (row, combined)
            if image_score is not None and image_score >= IMAGE_EXACT_SCORE_FLOOR:
                if visual_match is None or image_score > visual_match[2]:
                    visual_match = (row, combined, image_score)

        if visual_match is not None:
            row, combined, _image_score = visual_match
            auto.append((p, row, combined))
        elif review_best is not None and review_best[1] >= REVIEW_THRESHOLD:
            row, combined = review_best
            review.append((p, row, combined))
    return auto, review



# ------------------------------------------------------------------ writes
def _patch_product(pid, changes, actor):
    """Read-modify-write ONE product: only `changes` differs from the row
    already on file - every other field (price above all) rides through
    catalog.normalize()/upsert() completely untouched."""
    current = None
    for p in catalog.merged(include_hidden=True):
        if str(p.get("id")) == str(pid):
            current = dict(p)
            break
    if current is None:
        return None
    current.update(changes)
    result = catalog.upsert(current, actor=actor)
    product = result[0] if result else None
    return product


def apply_auto_match(product, supplier_row, actor):
    """Link one product to the supplier. Only supplierId/supplierSku change -
    name, photo, category and (always) price stay exactly as the owner set
    them."""
    return _patch_product(str(product.get("id")), {
        "supplierId": SUPPLIER_ID,
        "supplierSku": supplier_row["slug"],
    }, actor=actor)


def mapped_products():
    """Every product explicitly linked to this supplier - and only those."""
    out = []
    for p in catalog.merged(include_hidden=True):
        if not p or catalog.is_permanently_removed(p):
            continue
        if str(p.get("supplierId") or "").strip().lower() != SUPPLIER_ID:
            continue
        if not str(p.get("supplierSku") or "").strip():
            continue
        out.append(p)
    return out


def mark_discontinued_out_of_stock(supplier_rows, dry_run=False):
    """Owner's explicit rule (2026-09-28): when a linked item leaves the
    supplier's live catalog, set its stock to 0 and leave it OTHERWISE
    completely alone - still `online`, still fully visible in the shop, the
    storefront's existing "Out of stock" card state (card.oos / stockFor)
    is what tells the shopper, exactly like any other item that sells out.
    Nothing is auto-hidden and nothing is auto-deleted; the owner reviews
    the (now zero-stock) product themselves and decides whether to hide or
    delete it. Only ever touches products already linked to this supplier -
    an unmapped product is never in this list at all."""
    present = {r["slug"] for r in supplier_rows}
    counts = {}
    for p in mapped_products():
        slug = _slug_from_sku(p.get("supplierSku"))
        if slug and slug in present:
            continue
        pid = str(p.get("id"))
        name = str(p.get("name") or pid)
        if _is_protected(p):
            print(f"warning: {name!r} ({pid}) is linked to a supplier but matches a "
                  f"protected keyword {PROTECTED_KEYWORDS} - refusing to touch it. "
                  "Check the mapping in the admin editor.")
            counts["protected"] = counts.get("protected", 0) + 1
            continue
        if catalog.stock_of(p) == 0:
            counts["already-zero"] = counts.get("already-zero", 0) + 1
            continue
        if dry_run:
            print(f"would mark out of stock (gone from supplier): {name!r} ({pid})")
            counts["would-zero"] = counts.get("would-zero", 0) + 1
            continue
        changes = {"stock": 0, "stock_quantity": 0}
        if isinstance(p.get("optionStock"), dict) and p["optionStock"]:
            changes["optionStock"] = {k: 0 for k in p["optionStock"]}
        result = _patch_product(pid, changes, actor=f"supplier-sync:{SUPPLIER_ID}:discontinued")
        if result:
            print(f"marked out of stock (gone from supplier): {name!r} ({pid})")
            counts["zeroed"] = counts.get("zeroed", 0) + 1
        else:
            print(f"warning: failed to zero discontinued product {name!r} ({pid})")
            counts["zero-failed"] = counts.get("zero-failed", 0) + 1

    return counts


def _save_review(rows):
    try:
        import datetime
        with open(REVIEW_FILE, "w", encoding="utf-8") as fh:
            json.dump({
                "generated_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "candidates": rows,
            }, fh, indent=2, ensure_ascii=False)
    except OSError as exc:
        print(f"warning: could not write {REVIEW_FILE}: {exc}")


def _email_review_needed(rows):
    """Owner request 2026-09-28: the instant this run finds a supplier
    product it is NOT fully confident matches one of yours, email the shop
    inbox directly - so a possible match never just sits inside
    supplier_match_review.json or a GitHub Actions log until someone
    happens to go looking. Uses the same mailer.send_mail() every order
    alert already goes through: same transport (Resend/Brevo/SMTP), same
    shop inbox, and it NEVER raises - a mail problem must never fail this
    run or block the ordinary sync that follows it. A shop with no mail
    transport configured (local/dev/test) is a silent, expected no-op,
    exactly like every other mailer call in this codebase.
    """
    if not rows:
        return
    try:
        import mailer
        from html import escape as esc

        def _row_html(r):
            product_name = str(r.get("product_name") or r.get("product_id") or "")
            candidate_name = str(r.get("candidate_name") or r.get("candidate_slug") or "")
            try:
                score_text = f"{float(r.get('score') or 0):.2f}"
            except (TypeError, ValueError):
                score_text = str(r.get("score") or "")
            return (
                "<tr>"
                f"<td style=\"padding:8px 10px;border-bottom:1px solid #eee\">{esc(product_name)}</td>"
                f"<td style=\"padding:8px 10px;border-bottom:1px solid #eee\">{esc(candidate_name)}</td>"
                f"<td style=\"padding:8px 10px;border-bottom:1px solid #eee;text-align:right\">{esc(score_text)}</td>"
                "</tr>"
            )

        items_html = "".join(_row_html(r) for r in rows)
        body = (
            "<p style=\"margin:0 0 12px\">The nightly Splendall stock sync found "
            f"<b>{len(rows)}</b> possible product match(es) it is not fully confident "
            "about. Nothing was linked or changed automatically - confirm or reject "
            "each one in Admin &rarr; Products by setting (or clearing) that product's "
            "Supplier stock sync field.</p>"
            "<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\" "
            "style=\"border-collapse:collapse;width:100%;margin:12px 0;font-size:14px\">"
            "<thead><tr style=\"background:#faf6f1\">"
            "<th style=\"padding:8px 10px;text-align:left;border-bottom:2px solid #e4d7c6\">Your product</th>"
            "<th style=\"padding:8px 10px;text-align:left;border-bottom:2px solid #e4d7c6\">Possible Splendall match</th>"
            "<th style=\"padding:8px 10px;text-align:right;border-bottom:2px solid #e4d7c6\">Confidence</th>"
            "</tr></thead><tbody>" + items_html + "</tbody></table>"
            "<p style=\"margin:12px 0 0;color:#777\">This never affects your prices, and "
            "no product is ever hidden or deleted automatically.</p>"
        )
        subject = f"Jaura Store: {len(rows)} supplier match(es) need your review"
        ok, detail = mailer.send_mail(subject, body)
        if not ok:
            print(f"warning: could not email the supplier match review: {detail}")
    except Exception as exc:                       # pragma: no cover - defensive
        print(f"warning: could not email the supplier match review: {exc}")



def _warning_row(product, code, reason, variants=None):
    return {
        "product_id": str((product or {}).get("id") or ""),
        "product_name": str((product or {}).get("name") or "Supplier catalog"),
        "supplier_sku": str((product or {}).get("supplierSku") or ""),
        "code": str(code or "supplier_unconfirmed"),
        "reason": str(reason or "Supplier stock could not be confirmed."),
        "variants": list(variants or [])[:100],
        "at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def _load_warnings():
    rows = supabase_store.load_supplier_sync_warnings()
    if rows:
        return rows
    try:
        with open(WARNING_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("warnings", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def _save_warnings(rows):
    payload = list(rows or [])
    try:
        with open(WARNING_FILE, "w", encoding="utf-8") as fh:
            json.dump({"generated_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                       "warnings": payload}, fh, indent=2, ensure_ascii=False)
    except OSError as exc:
        print(f"warning: could not write uncertainty report: {exc}")
    if supabase_store.enabled() and not supabase_store.save_supplier_sync_warnings(payload):
        print("warning: could not persist supplier uncertainty alerts to Supabase")


# ------------------------------------------------------------- per-product
def sync_one(product, dry_run=False, warnings=None):
    """Refresh one linked product, atomically and fail-closed at option level."""
    pid = str(product.get("id") or "")
    name = str(product.get("name") or pid)
    warning_map = warnings if isinstance(warnings, dict) else {}

    if _is_protected(product):
        reason = (f"linked to supplier '{SUPPLIER_ID}' but matches protected keyword "
                  f"{PROTECTED_KEYWORDS}; mapping must be checked")
        print(f"warning: {name!r} ({pid}) is {reason}")
        warning_map[pid] = _warning_row(product, "protected_product_mapping", reason)
        return "protected"

    try:
        store_variants = _store_variant_rows(product)
        if store_variants:
            option_stock, audit = fetch_supplier_variant_stock(product)
            qty = sum(option_stock.values())
            detail = f"{len(option_stock)} exact option variant(s), total={qty}"
        else:
            qty, detail = fetch_supplier_quantity(product.get("supplierSku"))
            option_stock, audit = None, []
    except SupplierFetchError as exc:
        print(f"warning: skipping {name!r} ({pid}) - could not confirm supplier stock: {exc}")
        warning_map[pid] = _warning_row(product, exc.code, str(exc), exc.variants)
        return "uncertain"
    except Exception as exc:            # pragma: no cover - unexpected, still fail closed
        print(f"warning: skipping {name!r} ({pid}) - unexpected fetch error: {exc}")
        warning_map[pid] = _warning_row(product, "unexpected_supplier_reply", str(exc))
        return "uncertain"

    current = catalog.stock_of(product)
    current_options = product.get("optionStock") if isinstance(product.get("optionStock"), dict) else {}
    unchanged = current == qty and (option_stock is None or current_options == option_stock)
    if unchanged:
        warning_map.pop(pid, None)
        print(f"ok: {name!r} ({pid}) already matches supplier ({qty}) [{detail}]")
        return "unchanged"

    if dry_run:
        print(f"would set: {name!r} ({pid}) {current} -> {qty} [{detail}]")
        return "would-update"

    if option_stock is not None:
        result = _patch_product(pid, {"optionStock": option_stock,
                                     "stock": qty, "stock_quantity": qty},
                                actor=f"supplier-sync:{SUPPLIER_ID}:variants")
    else:
        result = catalog.set_variant_stock(pid, qty, actor=f"supplier-sync:{SUPPLIER_ID}")
    if result in (None, False):
        reason = "exact supplier stock was confirmed but could not be saved"
        print(f"warning: failed to write stock for {name!r} ({pid}) - left at {current}")
        warning_map[pid] = _warning_row(product, "stock_write_failed", reason, audit)
        return "write-failed"
    warning_map.pop(pid, None)
    print(f"updated: {name!r} ({pid}) {current} -> {qty} [{detail}]")
    return "updated"


# ------------------------------------------------------------------- main
def main(argv):
    args = argv[1:]
    dry_run = "--dry-run" in args
    no_discover = "--no-discover" in args
    only_ids = {a for a in args if a not in ("--dry-run", "--no-discover") and a.strip()}
    warning_map = {str(row.get("product_id") or f"warning:{i}"): row
                   for i, row in enumerate(_load_warnings()) if isinstance(row, dict)}

    # Autonomous discovery (crawl + auto-link + discontinue) runs on a full,
    # unfiltered pass only - the single-id / --no-discover modes are for
    # diagnostics on already-linked products and never touch the wider
    # catalogue.
    if not no_discover and not only_ids:
        supplier_rows = crawl_supplier_catalog()
        cache = _load_cache()
        if supplier_rows is not None and len(supplier_rows) >= MIN_CATALOG_SIZE:
            warning_map.pop("__supplier_crawl__", None)
            # Candidate warnings are rebuilt from this complete crawl; stale
            # possibilities disappear immediately when they no longer match.
            warning_map = {key: row for key, row in warning_map.items()
                           if row.get("code") != "supplier_match_unconfirmed"}
            print(f"crawled {len(supplier_rows)} live product(s) from {BASE_URL}")
            store_products = catalog.merged(include_hidden=True)
            auto, review = discover_matches(store_products, supplier_rows, cache)
            for product, row, score in auto:
                pname = product.get("name")
                if dry_run:
                    print(f"would auto-link: {pname!r} -> {row['name']!r} "
                          f"({row['slug']}) score={score:.2f}")
                    continue
                linked = apply_auto_match(product, row, actor=f"supplier-sync:{SUPPLIER_ID}:auto-link")
                if linked:
                    print(f"auto-linked: {pname!r} -> {row['name']!r} "
                          f"({row['slug']}) score={score:.2f}")
                else:
                    print(f"warning: failed to save auto-link for {pname!r}")
            if review:
                review_rows = [{
                    "product_id": product.get("id"), "product_name": product.get("name"),
                    "candidate_slug": row["slug"], "candidate_name": row["name"],
                    "score": round(score, 3),
                } for product, row, score in review]
                _save_review(review_rows)
                for product, row, score in review:
                    warning_map[str(product.get("id") or row.get("slug"))] = _warning_row(
                        product, "supplier_match_unconfirmed",
                        f"Possible supplier match {row.get('name')!r} is only {score:.1%} certain; no mapping or stock was changed.")
                print(f"{len(review)} possible match(es) need manual review "
                      f"(see {os.path.basename(REVIEW_FILE)})")
                if not dry_run:
                    _email_review_needed(review_rows)
            else:
                _save_review([])
            zero_counts = mark_discontinued_out_of_stock(supplier_rows, dry_run=dry_run)
            if zero_counts:
                print("discontinued check:", ", ".join(f"{k}={v}" for k, v in sorted(zero_counts.items())))
        elif supplier_rows is not None:
            reason = (f"Supplier crawl returned only {len(supplier_rows)} products; the layout or response may be incomplete. "
                      f"At least {MIN_CATALOG_SIZE} are required.")
            warning_map["__supplier_crawl__"] = _warning_row(
                {"id": "__supplier_crawl__", "name": "Splendall catalog"},
                "supplier_catalog_incomplete", reason)
            print(f"warning: supplier crawl returned only {len(supplier_rows)} product(s) - "
                  f"too few to trust for auto-discovery or discontinued checks this run "
                  f"(needs at least {MIN_CATALOG_SIZE}); already-linked products are "
                  "still refreshed individually below.")
        else:
            warning_map["__supplier_crawl__"] = _warning_row(
                {"id": "__supplier_crawl__", "name": "Splendall catalog"},
                "supplier_catalog_unavailable",
                "The complete supplier catalog could not be confirmed; discovery and discontinued checks were not run.")
            print("warning: could not crawl the supplier's full catalog this run - "
                  "auto-discovery and discontinued checks are skipped; already-linked "
                  "products are still refreshed individually below.")
        _save_cache(cache)

    products = mapped_products()
    if only_ids:
        products = [p for p in products if str(p.get("id")) in only_ids]

    if not products:
        if not dry_run:
            _save_warnings(list(warning_map.values()))
        print(f"no products are linked to supplier '{SUPPLIER_ID}' - nothing to sync.")
        return 0

    if not supabase_store.enabled():
        print("Supabase is not configured - stock will be mirrored to the local "
              "catalogue override file only (the same path an admin edit uses).")

    counts = {}
    for p in products:
        outcome = sync_one(p, dry_run=dry_run, warnings=warning_map)
        counts[outcome] = counts.get(outcome, 0) + 1

    if not dry_run:
        _save_warnings(list(warning_map.values()))
    print("summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing to report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
