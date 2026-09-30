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

# Keep automatic restocks conservative by default. Only positive, confirmed
# quantities are capped; a confirmed zero remains zero and existing unlinked
# variants are never touched by the sync.
SUPPLIER_STOCK_CAP = max(0, int(os.environ.get("SUPPLIER_STOCK_CAP", "20") or 20))
# Discovery is report-only. Supplier mappings are merchant-owned data and may
# only be created or changed through the Admin product editor.
AUTO_LINK_ENABLED = False


def capped_supplier_qty(quantity):
    """Cap positive confirmed supplier quantities, leaving zero unchanged."""
    try:
        quantity = max(0, int(quantity))
    except (TypeError, ValueError):
        return 0
    return min(quantity, SUPPLIER_STOCK_CAP) if quantity > 0 else 0

BASE_URL = os.environ.get("SPLENDALL_BASE_URL", "https://www.splendall.com").rstrip("/")
SHOP_URL = f"{BASE_URL}/shop/"
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


def _http_get_text(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if getattr(resp, "status", 200) != 200:
                raise SupplierFetchError(f"HTTP {getattr(resp, 'status', 0)}")
            return resp.read(4 * 1024 * 1024).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SupplierFetchError(f"network error: {exc}") from exc


def _html_meta(html, prop):
    match = re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(prop) +
                      r'["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if not match:
        match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+'
                          r'(?:property|name)=["\']' + re.escape(prop) + r'["\']', html, re.I)
    return match.group(1).replace("&amp;", "&") if match else ""


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
    try:
        data = _http_get_json(url)
    except SupplierFetchError:
        # Some WooCommerce/WAF configurations block Store API clients while
        # the official product pages remain public. Parse the canonical page
        # directly so a visible Sold out badge still zeroes Jaura inventory.
        product_url = f"{BASE_URL}/product/{urllib.parse.quote(slug)}/"
        html = _http_get_text(product_url)
        low = html.lower()
        sold_out = ("out-of-stock" in low or "sold out" in low or
                    "stock out" in low)
        image = _html_meta(html, "og:image")
        title = _html_meta(html, "og:title") or slug.replace("-", " ")
        row = {"slug": slug, "name": title, "permalink": product_url,
               "is_in_stock": not sold_out,
               "stock_availability": {"text": "Out of stock" if sold_out else "In stock"},
               "images": [{"src": image}] if image else []}
        if sold_out:
            return row
        # In-stock HTML does not disclose an exact quantity. Keep existing
        # Jaura stock rather than inventing one; the uncertainty badge asks for
        # review while the API is unavailable.
        raise SupplierFetchError("product page confirms in stock but does not publish an exact quantity",
                                 code="exact_quantity_unavailable")
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


def _option_link_candidates(row):
    """Spellings a per-option link may be keyed by: the bare optionStock key
    ("Serum"), each option value on its own, or the admin's composed
    "Title: Value" form ("Type: Serum")."""
    cands = [row.get("key")]
    for opt in row.get("options") or []:
        if isinstance(opt, dict) and opt.get("value"):
            cands.append(opt.get("value"))
            if opt.get("name"):
                cands.append(f"{opt.get('name')}: {opt.get('value')}")
    return [c for c in cands if c]


def _label_tokens(label):
    """Alphanumeric word tokens of an option label, lowercased."""
    return [t for t in re.split(r"[^a-z0-9]+", str(label or "").lower()) if t]


def _labels_match(store_label, supplier_label):
    """Confidence that a storefront custom label and a supplier label name the
    same option. Returns 'exact', 'partial', or None.

    'exact'   - identical once folded ("Mustard Yellow" == "mustard yellow").
    'partial' - one label's word tokens are a subset of the other's and they
                share at least one token, so a merchant custom name like
                "Mustard Yellow" still recognises the supplier's basic "Yellow"
                (and vice versa) WITHOUT ever renaming the storefront option.
    """
    if not str(store_label or "").strip() or not str(supplier_label or "").strip():
        return None
    if catalog.fold_option_value(store_label) == catalog.fold_option_value(supplier_label):
        return "exact"
    a, b = set(_label_tokens(store_label)), set(_label_tokens(supplier_label))
    if a and b and (a <= b or b <= a):
        return "partial"
    return None


# Generic, non-colour variation labels a supplier might use when a colour is
# not spelled out. Only these unlabelled/ambiguous cases may be assigned by
# positional elimination - a clearly named colour ("Teal") never is.
_AMBIGUOUS_LABEL_TOKENS = {
    "default", "variation", "variations", "variant", "variants", "option",
    "options", "choice", "style", "type", "na", "none", "color", "colour",
}


def _is_ambiguous_label(label):
    """True when a supplier label carries no distinguishing colour/word - e.g.
    it is blank, purely numeric/slug-like, or a generic "Variation #2"."""
    tokens = _label_tokens(label)
    if not tokens:
        return True
    return all(t.isdigit() or t in _AMBIGUOUS_LABEL_TOKENS for t in tokens)


def infer_remaining_option_links(store_labels, candidate_links):
    """Map remaining storefront option labels to a pool of supplier links.

    Reconciliation order, each preserving the merchant's custom option NAMES
    (only the URL is ever assigned, never the label):
      1. fold-exact label match ("Mustard Yellow" -> a "Mustard Yellow" link),
      2. token-subset match ("Mustard Yellow" -> a basic "Yellow" link),
      3. positional elimination ONLY when a single storefront colour and a
         single AMBIGUOUS/unlabelled supplier link remain (e.g. "Variation 2").

    A clearly named leftover colour is never force-matched: it lands in
    ``review`` so the merchant confirms it.

    Returns ``{"links": {store_label: url}, "review": [store_label, ...]}``.
    A label lands in ``review`` (⚠️ Check Variant Color Mapping) only when a
    link is still available but cannot be confidently attached to it.
    """
    links = {}
    remaining = {k: str(v).strip() for k, v in (candidate_links or {}).items()
                 if str(v or "").strip()}
    pending = list(store_labels)
    for confidence in ("exact", "partial"):
        for label in list(pending):
            matches = [k for k in remaining if _labels_match(label, k) == confidence]
            if len(matches) == 1:
                links[label] = remaining.pop(matches[0])
                pending.remove(label)
    # Positional elimination - safe only when the lone leftover supplier label
    # is ambiguous/unlabelled, so we are filling in a blank, not renaming a
    # deliberately different colour.
    if len(pending) == 1 and len(remaining) == 1:
        only_label = next(iter(remaining))
        if _is_ambiguous_label(only_label):
            links[pending.pop()] = remaining.pop(only_label)
    review = list(pending) if remaining else []
    return {"links": links, "review": review}


def resolve_option_links(store_rows, links):
    """Attach each storefront option to a Splendall URL from the pasted pool.

    Two layers, both keeping the storefront's custom labels intact:
      * an exact spelling pass (the option key, its value, or the admin's
        "Title: Value" form) that honours a merchant's deliberate mapping, then
      * intelligent inference over whatever URLs are left (see
        infer_remaining_option_links) so unlabelled/ambiguous colours are
        assigned by token overlap and elimination.

    Returns ``{"links": {store_key: url}, "missing": [...], "review": [...]}``.
    ``missing`` are options with no link at all (⚠️ Missing supplier link);
    ``review`` are options/colours that need a one-tap manual confirmation
    (⚠️ Check Variant Color Mapping), including brand-new Splendall colours
    with no storefront home.
    """
    folded = {}
    for label, url in (links or {}).items():
        # Preserve a list of listings as a list. Converting it with str() here
        # produces one invalid URL like "['https://a', 'https://b']" and
        # prevents independent auditing of each source.
        if isinstance(url, (list, tuple)):
            clean = [str(item).strip() for item in url[:20]
                     if str(item or "").strip()]
        else:
            value = str(url or "").strip()
            clean = value if value else []
        if clean:
            folded[catalog.fold_option_value(label)] = (str(label), clean)

    assigned, used_folds, pending = {}, set(), []
    for row in store_rows:
        hit = None
        for cand in _option_link_candidates(row):
            fk = catalog.fold_option_value(cand)
            if fk in folded and fk not in used_folds:
                hit = fk
                break
        if hit:
            assigned[row["key"]] = folded[hit][1]
            used_folds.add(hit)
        else:
            pending.append(row["key"])

    remaining = {folded[fk][0]: folded[fk][1] for fk in folded if fk not in used_folds}
    review, missing = [], []
    if pending and remaining:
        inferred = infer_remaining_option_links(pending, remaining)
        assigned.update(inferred["links"])
        review.extend(inferred["review"])
        homed = list(inferred["links"].values())
        # A leftover supplier link with no storefront home is a NEW colour the
        # merchant has not created yet - flag it for manual confirmation.
        # Lists are deliberately compared by value, never stringified.
        leftover = [lab for lab, url in remaining.items() if url not in homed]
        review.extend(leftover)
        missing.extend([k for k in pending if k not in assigned and k not in inferred["review"]])
    elif pending:
        missing.extend(pending)

    return {"links": assigned, "missing": missing, "review": sorted(set(review))}


def fetch_per_option_supplier_stock(product, fetch=None):
    """Mirror stock per variant option using each option's OWN supplier link.

    Unlike fetch_supplier_variant_stock (one supplier product, many
    variations), this reads ``optionSupplierSku`` - a pool of Splendall URLs -
    and reconciles it to the storefront's custom option labels (see
    resolve_option_links), then fetches each option's stock from its own
    simple supplier product. An option whose link runs out is set to 0 while
    the others stay active. Options with no link are left untouched and
    reported so the Admin can raise a "Missing supplier link" badge; colours it
    cannot confidently match raise a "Check Variant Color Mapping" alert.

    Returns ``(option_stock, audit, missing_links, review_links)``.
    ``option_stock`` only contains the options confirmed from the supplier;
    callers merge it onto the existing map so unlinked options keep their
    current quantity, and custom labels are never rewritten.
    """
    fetch = fetch or fetch_supplier_quantity
    raw_links = product.get("optionSupplierSku")
    links = raw_links if isinstance(raw_links, dict) else {}

    def supplier_urls(value):
        """Return every listing for one option, including legacy scalars."""
        values = value if isinstance(value, (list, tuple)) else [value]
        return [str(v).strip() for v in values[:20] if str(v or "").strip()]
    store_rows = _store_variant_rows(product)
    if not store_rows:
        raise SupplierFetchError("store product has no options to map to supplier links",
                                 code="store_options_missing")
    resolved = resolve_option_links(store_rows, links)
    option_stock, audit = {}, []
    for row in store_rows:
        key = row["key"]
        url = resolved["links"].get(key)
        if not url:
            continue
        urls = supplier_urls(url)
        quantities = []
        for listing in urls:
            qty, detail = fetch(listing)
            try:
                confirmed_qty = max(0, int(qty or 0))
            except (TypeError, ValueError):
                confirmed_qty = 0
            quantities.append(confirmed_qty)
            audit.append({
                "option": key,
                "supplier_sku": listing,
                "quantity": confirmed_qty,
                "status": "in_stock" if confirmed_qty > 0 else "out_of_stock",
                "detail": detail,
            })
        # Multiple listings are independent sources for the same storefront
        # option. Combine confirmed quantities first, then apply ONE cap to
        # the aggregate so several URLs cannot bypass the default limit.
        option_stock[key] = capped_supplier_qty(sum(quantities))
    missing, review = resolved["missing"], resolved["review"]
    if not option_stock and (missing or review):
        raise SupplierFetchError(
            f"none of the {len(store_rows)} variant option(s) has a supplier link",
            code="option_links_missing")
    return option_stock, audit, missing, review


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


def _crawl_shop_html(max_pages=60):
    """Fallback crawler for Splendall's official WooCommerce /shop/ layout."""
    found = {}
    for page in range(1, max_pages + 1):
        url = SHOP_URL if page == 1 else f"{SHOP_URL}page/{page}/"
        html = _http_get_text(url)
        page_slugs = set()
        for match in re.finditer(r'href=["\'](' + re.escape(BASE_URL) +
                                 r'/product/([^/"\']+)/?)["\']', html, re.I):
            permalink, slug = match.group(1).rstrip("/") + "/", urllib.parse.unquote(match.group(2))
            page_slugs.add(slug)
            window = html[max(0, match.start() - 900):min(len(html), match.end() + 1400)]
            image_match = re.search(r'(?:src|data-src)=["\']([^"\']+(?:jpg|jpeg|png|webp)[^"\']*)', window, re.I)
            title_match = re.search(r'<h[23][^>]*>.*?<a[^>]*>(.*?)</a>', window, re.I | re.S)
            title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else slug.replace('-', ' ')
            found.setdefault(slug, {"slug": slug, "name": title,
                                    "image_url": image_match.group(1).replace("&amp;", "&") if image_match else "",
                                    "permalink": permalink, "qty": None,
                                    "detail": "WooCommerce /shop/ listing"})
        if not page_slugs:
            break
    return list(found.values())


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
            if page == 1:
                try:
                    print(f"Store API unavailable ({exc}); falling back to {SHOP_URL}")
                    return _crawl_shop_html(max_pages=max_pages)
                except SupplierFetchError as html_exc:
                    print(f"warning: supplier /shop/ crawl also failed: {html_exc}")
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
    """Bind to the canonical splendall.com product URL.

    Storing the full official link (rather than a bare/blank slug) makes the
    Admin mapping directly reviewable while _slug_from_sku keeps lookups
    backward compatible with older rows.
    """
    slug = str(supplier_row.get("slug") or "").strip()
    permalink = str(supplier_row.get("permalink") or "").strip()
    if not permalink and slug:
        permalink = f"{BASE_URL}/product/{urllib.parse.quote(slug)}/"
    if not slug or not permalink.startswith(BASE_URL + "/product/"):
        return None
    return _patch_product(str(product.get("id")), {
        "supplierId": SUPPLIER_ID,
        "supplierSku": permalink,
    }, actor=actor)


def mapped_products():
    """Every product explicitly linked to this supplier - and only those."""
    out = []
    for p in catalog.merged(include_hidden=True):
        if not p or catalog.is_permanently_removed(p):
            continue
        if str(p.get("supplierId") or "").strip().lower() != SUPPLIER_ID:
            continue
        # A variant-only product may intentionally have no product-level URL;
        # its explicit optionSupplierSku links are sufficient scope.
        option_links = p.get("optionSupplierSku")
        has_option_link = isinstance(option_links, dict) and any(
            (isinstance(value, (list, tuple)) and any(str(url or "").strip() for url in value))
            or (not isinstance(value, (list, tuple)) and str(value or "").strip())
            for value in option_links.values())
        if not str(p.get("supplierSku") or "").strip() and not has_option_link:
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

    per_option_links = product.get("optionSupplierSku")
    has_per_option_links = isinstance(per_option_links, dict) and any(
        str(v or "").strip() for v in per_option_links.values())
    missing_links, review_links = [], []
    try:
        store_variants = _store_variant_rows(product)
        if store_variants and has_per_option_links:
            # Each variant option carries its OWN Splendall link: mirror stock
            # per option so a sold-out component zeroes only itself. Confirmed
            # options merge onto the existing map; unlinked ones stay as-is and
            # custom option labels ("Mustard Yellow") are never rewritten.
            confirmed, audit, missing_links, review_links = fetch_per_option_supplier_stock(product)
            existing = product.get("optionStock") if isinstance(product.get("optionStock"), dict) else {}
            option_stock = dict(existing)
            option_stock.update(confirmed)
            qty = sum(option_stock.values())
            detail = (f"{len(confirmed)} per-option link(s) confirmed"
                      + (f", {len(missing_links)} unlinked" if missing_links else "")
                      + (f", {len(review_links)} to review" if review_links else "")
                      + f", total={qty}")
        elif store_variants:
            option_stock, audit = fetch_supplier_variant_stock(product)
            option_stock = {key: capped_supplier_qty(value)
                            for key, value in option_stock.items()}
            qty = sum(option_stock.values())
            detail = f"{len(option_stock)} exact option variant(s), total={qty}"
        else:
            qty, detail = fetch_supplier_quantity(product.get("supplierSku"))
            qty = capped_supplier_qty(qty)
            option_stock, audit = None, []
    except SupplierFetchError as exc:
        print(f"warning: skipping {name!r} ({pid}) - could not confirm supplier stock: {exc}")
        warning_map[pid] = _warning_row(product, exc.code, str(exc), exc.variants)
        return "uncertain"
    except Exception as exc:            # pragma: no cover - unexpected, still fail closed
        print(f"warning: skipping {name!r} ({pid}) - unexpected fetch error: {exc}")
        warning_map[pid] = _warning_row(product, "unexpected_supplier_reply", str(exc))
        return "uncertain"

    def _note_option_warnings():
        """Keep a non-fatal per-option alert visible in Admin even when the
        confirmed options needed no stock change. A colour-mapping ambiguity
        (⚠️ Check Variant Color Mapping) takes precedence over a plain missing
        link because it needs a human decision, not just a paste."""
        if review_links:
            reason = ("Check Variant Color Mapping: could not confidently match "
                      "these Splendall colours/links to a storefront option - "
                      f"confirm manually: {', '.join(str(x) for x in review_links[:20])}")
            warning_map[pid] = _warning_row(product, "variant_color_mapping", reason, audit)
        elif missing_links:
            reason = ("some variant option(s) have no Splendall link and were "
                      f"left unchanged: {', '.join(missing_links[:20])}")
            warning_map[pid] = _warning_row(product, "option_link_missing", reason, audit)
        else:
            warning_map.pop(pid, None)

    current = catalog.stock_of(product)
    current_options = product.get("optionStock") if isinstance(product.get("optionStock"), dict) else {}
    unchanged = current == qty and (option_stock is None or current_options == option_stock)
    if unchanged:
        _note_option_warnings()
        print(f"ok: {name!r} ({pid}) already matches supplier ({qty}) [{detail}]")
        return "unchanged"

    if dry_run:
        print(f"would set: {name!r} ({pid}) {current} -> {qty} [{detail}]")
        return "would-update-out-of-stock" if qty == 0 and current != 0 else "would-update"

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
    _note_option_warnings()
    print(f"updated: {name!r} ({pid}) {current} -> {qty} [{detail}]")
    return "updated-out-of-stock" if qty == 0 and current != 0 else "updated"


# ------------------------------------------------------------------- main
def main(argv):
    args = argv[1:]
    dry_run = "--dry-run" in args
    no_discover = "--no-discover" in args
    report_path = "supplier-sync-report.json"
    if "--report" in args:
        try: report_path = args[args.index("--report") + 1]
        except IndexError: report_path = "supplier-sync-report.json"
    value_args = {report_path} if "--report" in args else set()
    only_ids = {a for a in args if a not in ("--dry-run", "--no-discover", "--report")
                and a not in value_args and a.strip()}
    audit = {"generated_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
             "supplier_base_url": BASE_URL, "dry_run": dry_run, "catalog_items_checked": 0,
             "auto_linked": 0, "would_auto_link": 0, "updated_out_of_stock": 0,
             "would_update_out_of_stock": 0, "unlinked_warning_count": 0,
             "outcomes": {}, "complete": False}
    def save_audit():
        audit["unlinked_warning_count"] = sum(
            1 for row in warning_map.values()
            if isinstance(row, dict) and row.get("code") == "supplier_match_unconfirmed")
        try:
            with open(report_path, "w", encoding="utf-8") as fh:
                json.dump(audit, fh, indent=2, ensure_ascii=False)
        except OSError as exc: print(f"warning: could not write audit report: {exc}")
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
            audit["catalog_items_checked"] = len(store_products)
            audit["supplier_items_crawled"] = len(supplier_rows)
            auto, review = discover_matches(store_products, supplier_rows, cache)
            # Even an exact image match is report-only now. Never call the
            # mapping writer from the background worker; the merchant must
            # paste and save every supplier URL manually in Admin.
            if auto:
                review.extend(auto)
                for product, row, score in auto:
                    print(f"possible manual supplier mapping: {product.get('name')!r} -> {row['name']!r} "
                          f"({row['slug']}) score={score:.2f}; no mapping was changed")
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
            audit["updated_out_of_stock"] += zero_counts.get("zeroed", 0)
            audit["would_update_out_of_stock"] += zero_counts.get("would-zero", 0)
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
        audit["complete"] = bool(audit.get("supplier_items_crawled"))
        save_audit()
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
    audit["outcomes"] = counts
    audit["updated_out_of_stock"] += counts.get("updated-out-of-stock", 0)
    audit["would_update_out_of_stock"] += counts.get("would-update-out-of-stock", 0)
    audit["linked_items_checked"] = len(products)
    audit["complete"] = bool(audit.get("supplier_items_crawled"))
    save_audit()
    print("summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing to report")
    print(f"audit report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
