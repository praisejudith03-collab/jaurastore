#!/usr/bin/env python3
"""Upload the repository's committed product photos to Supabase Storage and
point `products.image_url` at the complete HTTPS public URL of each upload.

This script only ever MOVES IMAGES and SETS ONE COLUMN. It never deletes
anything - not a local file, not a Storage object, not a database row - and it
never renames or creates a product.

    python3 migrate_images.py --dry-run --report migration-dry-run.json
    python3 migrate_images.py --bucket uploads --report migration-result.json

Environment (never hardcoded, never printed):

    SUPABASE_URL                 https://<project>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY    the service-role key (server-side only)
    SUPABASE_BUCKET              public bucket, defaults to `uploads`

Every product / category / banner / logo / hero image goes to the single
PUBLIC `uploads` bucket, so a browser can load it with no server round-trip:

    https://<project>.supabase.co/storage/v1/object/public/uploads/<key>

SAFETY CONTRACT (each line is enforced in code, not just promised here):

 1. No credentials in this file - they are read from the environment.
 2. The service-role key is never printed, never written to a report.
 3. No local file is ever deleted (there is no unlink/rmtree in this module).
 4. No database row is ever deleted (no .delete() on any table).
 5. No product id is ever renamed: every write is `.update(...).eq("id", id)`
    and the patched payload is whitelisted to image columns only.
 6. No product is ever created: this script issues UPDATE, never INSERT, and
    refuses to touch an id that is not already a row in `products`.
 7. `prices` / `stock` are untouched: price and stock keys are never present
    in a patch, and the report records the before/after values it observed so
    a reviewer can prove nothing drifted.
 8. Legacy `wix-*` ids stay exactly as they are. They are the primary keys of
    live rows that orders, reviews, carts and old product links reference, so
    they are carried through untouched and only reported on.
 9. Storage keys are deterministic and content-addressed
    (`products/<id>/<sha256[:16]>-<stem>.<ext>`), so a second run finds the
    object it wrote the first time and skips it: the script is idempotent.
10. `image_url` is written only AFTER the upload succeeded AND the object was
    verified to exist in the bucket. An unverified upload leaves the row alone.
11. In `--dry-run` no bytes are uploaded and no database write is issued; the
    run only reads and reports what the real run would do.

IMAGE -> PRODUCT MAPPING

A product's photo is taken from that product's own `image` field (then its
`images` gallery), never guessed from a filename. Two independent checks make
a wrong mapping visible instead of silent:

  * the resolved path must live inside this repository's `images/` tree, and
  * the filename stem must equal the product's `slug` (true for all 182 real
    photos in data/seed.json), so a photo belonging to another product shows
    up as `stem_mismatch` and is skipped rather than uploaded to the wrong id.

Nothing is published by this script. It reads the rows that already exist in
Supabase (or, offline, the merged local catalogue) and never decides that a
product should go live.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import storage                                    # noqa: E402  (mime_for)

DEFAULT_BUCKET = "uploads"
PUBLIC_URL_TEMPLATE = "/storage/v1/object/public/{bucket}/{key}"
IMAGE_EXT = ("jpg", "jpeg", "png", "webp", "gif", "avif")
MAX_BYTES = 6 * 1024 * 1024          # same ceiling as storage.MAX_BYTES
PLACEHOLDER_STEM = "_placeholder"

# Admin-override ids that the pytest suite writes into the tracked catalogue
# (tests/test_stock_confirm.py, tests/test_catalog_mirror.py). They are test
# scaffolding, not merchandising decisions, and must never be published.
_FIXTURE_ID_RE = re.compile(r"^jau-(stock|mirror|unit|sync|opt)")

# The ONLY columns this script is allowed to write. Price, stock, id, name,
# category and every other column are deliberately absent, so a stray key can
# never reach the database.
WRITABLE_COLUMNS = ("image_url", "updated_at")

# Columns the report echoes back so a reviewer can prove the real run left
# prices and stock exactly as they were. Read-only.
OBSERVED_COLUMNS = ("id", "name", "slug", "category", "online",
                    "priceNgn", "priceCfa", "compareNgn", "compareCfa",
                    "stock_quantity", "stock", "image_url")

_MISSING_COLUMN_RE = re.compile(r'column "?([\w]+)"? .*does not exist|'
                                r'Could not find the .*?\'([\w]+)\'')


# --------------------------------------------------------------- utilities
def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _ext(path):
    return os.path.splitext(path)[1].lstrip(".").lower()


def _slugify(value):
    """Deterministic, filesystem-safe key fragment for a product id."""
    out = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return out[:80] or "product"


def _stem(path):
    """Filename stem without the responsive-suffix (.400w) and extension."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r"\.\d+w$", "", stem)


def _public_url(supabase_url, bucket, key):
    """The complete HTTPS public URL, or '' when the base is unknown.

    Never returns a relative path: a half URL written into products.image_url
    would render as a broken image and look like a successful migration.
    """
    base = (supabase_url or "").rstrip("/")
    if not base.startswith("http"):
        return ""
    return f"{base}{PUBLIC_URL_TEMPLATE.format(bucket=bucket, key=key)}"


def _template_url(bucket, key):
    """The URL shape with the project ref visibly unresolved.

    Used only in an offline dry-run, so a reviewer can see exactly what would
    be written once SUPABASE_URL is set - and can never mistake it for a real
    URL, because `<SUPABASE_URL>` is not a hostname.
    """
    return ("https://<SUPABASE_URL>"
            f"{PUBLIC_URL_TEMPLATE.format(bucket=bucket, key=key)}")


# A complete public object URL: real scheme, real hostname (no unresolved
# `<SUPABASE_URL>` placeholder, no whitespace), our public object path, a
# bucket segment and a non-empty key.
_PUBLIC_URL_RE = re.compile(
    r"^https://[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}"
    r"/storage/v1/object/public/[^/\s]+/\S+$", re.IGNORECASE)


def _is_public_supabase_url(url):
    """True only for a complete, fully-resolved HTTPS public-bucket URL.

    This is the last gate before a value reaches products.image_url, so it
    must reject the offline template form too - `https://<SUPABASE_URL>/...`
    starts with https:// and contains the right path, but saving it would put
    a broken image on the storefront while the report claimed success.
    """
    return bool(url) and bool(_PUBLIC_URL_RE.match(str(url).strip()))


_HOST_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)")


def _url_host(url):
    """The scheme://HOST part of a URL, or "" when it is not absolute."""
    m = _HOST_RE.match(str(url or "").strip())
    return m.group(1).lower() if m else ""


def classify_image_url(url, bucket="uploads"):
    """Bucket a row's current image_url for the report's HTTPS-URL tally.

    A migration report that only says "N already uploaded" hides the case that
    actually matters on review: rows that ALREADY point at a complete HTTPS
    URL. Those are not touched by this script, and an operator reading the
    report needs to see how many there are and where they point.
    """
    u = str(url or "").strip()
    if not u:
        return "blank", ""
    # Check the template form FIRST. `https://<SUPABASE_URL>/...` does start
    # with https://, so testing that first would file it as a real host and
    # hide a row that is actually broken.
    if "<" in u or "{" in u:
        return "unresolved_template", ""
    host = _url_host(u)
    if u.lower().startswith("https://"):
        if re.search(r"/storage/v1/object/public/" + re.escape(bucket) + r"/", u):
            return "already_public_supabase_uploads", host
        return "other_https_host", host
    if u.lower().startswith("http://"):
        return "insecure_http", host          # would be a mixed-content bug
    return "relative_or_other", ""


def _mask(text):
    """Redact anything that could be a secret before it is printed/stored.

    Two layers, because an exception string from a failing HTTP call can carry
    the Authorization header:

      * the configured service-role key itself, replaced verbatim, and
      * any JWT-shaped token. The character class MUST include '.' - a JWT's
        third segment is its signature, and masking only the header segment
        would leave the sensitive part readable.
    """
    s = str(text)
    try:
        from config import Config
        key = getattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "") or ""
    except Exception:
        key = ""
    if key:
        s = s.replace(key, "<service-role-key redacted>")
    return re.sub(r"eyJ[A-Za-z0-9_\-.]{8,}", "eyJ…REDACTED", s)


# ------------------------------------------------------------ local images
def _inside_repo_images(path):
    """Resolve `path` and require it to sit inside <repo>/images/.

    Guards against an absolute path or a `../` escape in catalogue data: a
    product row can only ever point at an image this repository ships.
    """
    try:
        images_root = os.path.realpath(os.path.join(ROOT, "images"))
        full = os.path.realpath(os.path.join(ROOT, str(path).lstrip("/")))
    except (OSError, ValueError):
        return ""
    if not full.startswith(images_root + os.sep):
        return ""
    return full if os.path.isfile(full) else ""


def _candidate_images(product):
    """The product's own image fields, in priority order. Never guessed."""
    seen, out = set(), []
    primary = (product.get("image") or product.get("image_url") or "").strip()
    gallery = product.get("images") or []
    if isinstance(gallery, str):
        gallery = [gallery]
    for cand in [primary] + list(gallery):
        cand = str(cand or "").strip()
        if not cand or cand in seen:
            continue
        seen.add(cand)
        out.append(cand)
    return out


def resolve_source_image(product):
    """(local_abs_path, repo_relative_path, status, detail) for one product.

    status is one of: found, placeholder, missing, external, blank, unsafe.
    """
    cands = _candidate_images(product)
    if not cands:
        return "", "", "blank", "no image field on the product"
    for cand in cands:
        if cand.startswith(("http://", "https://", "data:", "blob:")):
            # Already a URL: nothing local to upload. Recorded, never fetched.
            return "", cand, "external", "product already references a URL"
    for cand in cands:
        if PLACEHOLDER_STEM in os.path.basename(cand):
            return "", cand, "placeholder", "points at the branded placeholder"
    for cand in cands:
        full = _inside_repo_images(cand)
        if full:
            return full, cand, "found", ""
    # Distinguish "unsafe path" from "file is simply not in the repo".
    for cand in cands:
        if os.path.isabs(cand) or ".." in cand.split("/"):
            return "", cand, "unsafe", "path escapes the images/ tree"
    return "", cands[0], "missing", "file is not present under images/"


def storage_key_for(product_id, local_path, digest):
    """Deterministic, content-addressed key: same product + same bytes => same
    key, which is what makes a second run skip instead of duplicate."""
    return (f"products/{_slugify(product_id)}/"
            f"{digest[:16]}-{_slugify(_stem(local_path))}.{_ext(local_path)}")


# ------------------------------------------------------------ local data
def _read_json(rel_path, default):
    try:
        with open(os.path.join(ROOT, rel_path), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def load_local_catalogue():
    """data/seed.json (258 legacy source rows) with data/catalog.json (admin
    overrides) applied on top - the same merge catalog.py performs at boot."""
    seed = _read_json("data/seed.json", [])
    seed = [p for p in seed if isinstance(p, dict) and p.get("id")]
    cat = _read_json("data/catalog.json", {})
    overrides = {p["id"]: p for p in (cat.get("products") or [])
                 if isinstance(p, dict) and p.get("id")}
    merged = {p["id"]: dict(p) for p in seed}
    for pid, row in overrides.items():
        base = merged.get(pid, {})
        base.update({k: v for k, v in row.items() if v is not None})
        base.setdefault("id", pid)
        merged[pid] = base
    return merged, len(seed), overrides, (cat.get("deleted") or [])


def load_local_categories():
    cat = _read_json("data/categories.json", {})
    rows = cat.get("categories") if isinstance(cat, dict) else cat
    return [c for c in (rows or []) if isinstance(c, dict) and c.get("id")]


# ----------------------------------------------------------- supabase side
def _client():
    """A Supabase client, or None when the credentials are not configured."""
    from config import Config
    if not (Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY):
        return None, Config.SUPABASE_URL
    try:
        import supabase_store
        c = supabase_store.client()
    except Exception:
        return None, Config.SUPABASE_URL
    return c, Config.SUPABASE_URL


def fetch_products(client):
    """Every existing row in `products`. This script adds no rows of its own.

    Follows the repository's own paging convention (supabase_store
    ._fetch_product_pages): select("*"), ordered by id so a page boundary can
    never shift rows, with count="exact" so a server-truncated page is
    detected instead of silently ending the walk.
    """
    rows, start, page_size, total = [], 0, 1000, None
    while True:
        res = (client.table("products").select("*", count="exact")
               .order("id").range(start, start + page_size - 1).execute())
        data = getattr(res, "data", None)
        if data is None and isinstance(res, dict):
            data = res.get("data")
        data = data or []
        if total is None:
            total = getattr(res, "count", None)
        if not data:
            break
        rows.extend(data)
        start += len(data)
        if len(data) < page_size:
            if total is None or len(rows) >= int(total or 0):
                break
            page_size = max(1, len(data))
    return rows


def fetch_categories(client):
    try:
        res = client.table("categories").select("id,name,image_url").execute()
        data = getattr(res, "data", res) or []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def object_exists(client, bucket, key):
    """(exists, meta) - a key is only reused when the bucket really holds it."""
    try:
        res = client.storage.from_(bucket).info(key)
        meta = res if isinstance(res, dict) else getattr(res, "__dict__", {})
        return True, (meta or {})
    except Exception:
        return False, {}


def list_existing_keys(client, bucket, prefix):
    """Object names already present under `prefix` (idempotency look-ahead)."""
    try:
        res = client.storage.from_(bucket).list(prefix, {"limit": 1000})
        data = res if isinstance(res, list) else getattr(res, "data", []) or []
        return {os.path.basename(str(o.get("name") or "")) for o in data
                if isinstance(o, dict)}
    except Exception:
        return set()


def upload_object(client, bucket, key, data, content_type):
    """Upload without overwriting. Returns (ok, detail)."""
    try:
        client.storage.from_(bucket).upload(
            key, data, {"content-type": content_type, "upsert": False})
        return True, "uploaded"
    except Exception as exc:
        msg = _mask(exc)
        # An existing object at the exact same content-addressed key means the
        # bytes are already there: treat that as success, not a duplicate.
        if "already exists" in msg.lower() or "duplicate" in msg.lower():
            return True, "already present (content-addressed key)"
        return False, f"upload failed ({exc.__class__.__name__})"


def update_image_url(client, product_id, url, when=None):
    """Set image_url on ONE existing row. Never inserts, never renames.

    `when` re-checks the current value so a concurrent admin edit cannot be
    silently clobbered.
    """
    patch = {"image_url": url, "updated_at": _now()}
    assert set(patch) <= set(WRITABLE_COLUMNS), "patch left the whitelist"
    assert "id" not in patch and "priceNgn" not in patch, "forbidden column"
    try:
        q = client.table("products").update(patch).eq("id", product_id)
        res = q.execute()
        data = getattr(res, "data", res)
        return True, (data if isinstance(data, list) else [])
    except Exception as exc:
        return False, _mask(exc)


def update_category_image(client, cat_id, url):
    try:
        res = (client.table("categories").update({"image_url": url,
                                                  "updated_at": _now()})
               .eq("id", cat_id).execute())
        data = getattr(res, "data", res)
        return True, (data if isinstance(data, list) else [])
    except Exception as exc:
        return False, _mask(exc)


def http_check(url, timeout=10):
    """HEAD the public URL. Optional: only run with --http-check."""
    try:
        import requests
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        return r.status_code
    except Exception as exc:
        return f"error ({exc.__class__.__name__})"


# ----------------------------------------------------------------- planning
def _price_missing(row):
    return row.get("priceNgn") is None or row.get("priceCfa") is None


def _price_non_positive(row):
    """A price that exists but is 0 or negative.

    Kept separate from _price_missing on purpose: "missing" and "free" are
    different problems with different fixes, and a report that lumps them
    together lets a 0 slip through as merely absent. A 0 price on a row
    intended to be live would put a free product on the storefront.
    """
    bad = []
    for col in ("priceNgn", "priceCfa"):
        raw = row.get(col)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue          # absent/unparseable is _price_missing's business
        if value <= 0:
            bad.append((col, raw))
    return bad


def _stock_missing(row):
    """True when NO stock value exists on the row.

    `stock_quantity` is the canonical column; `stock` is the legacy alias the
    schema deliberately keeps (and the only key data/seed.json carries). A row
    holding either one has stock - reporting it as missing would be noise.
    """
    return row.get("stock_quantity") is None and row.get("stock") is None


def _stock_key(row):
    """Which key supplied the stock value, so the report is auditable."""
    if row.get("stock_quantity") is not None:
        return "stock_quantity"
    if row.get("stock") is not None:
        return "stock (legacy alias)"
    return None


def plan_products(products, bucket, supabase_url, client=None, only_ids=None,
                  limit=None, live_only=False):
    """Build the full report. Read-only: performs no upload and no write."""
    rep = {
        "products_examined": 0,
        "products_matched": 0,
        "products_skipped": 0,
        "images_discovered": 0,
        "images_missing": 0,
        "images_already_uploaded": 0,
        "existing_https_urls": {
            "count": 0,
            "already_public_supabase_uploads": 0,
            "other_https_host": 0,
            "insecure_http": 0,
            "unresolved_template": 0,
            "relative_or_other": 0,
            "blank": 0,
            "host_breakdown": {},
            "samples": [],
        },
        "images_to_upload": 0,
        "duplicate_products": [],
        "duplicate_row_count": 0,
        "missing_prices": [],
        "non_positive_prices": [],
        "missing_stock": [],
        "stock_value_source": {},
        "distinct_local_files": 0,
        "legacy_id_references": [],
        "blank_ids": [],
        "tombstone_rows_skipped": [],
        "mapping_warnings": [],
        "proposed_database_updates": [],
        "proposed_storage_paths": [],
        "skipped_detail": [],
    }
    seen_ids = {}
    file_owners = {}
    rows = []

    for row in products:
        pid = str(row.get("id") or "").strip()
        if not pid:
            rep["blank_ids"].append({k: row.get(k) for k in ("name", "slug")})
            continue
        if pid in seen_ids:
            # One entry per duplicated id, plus how many extra rows carried it,
            # so the report says "these ids repeat" without repeating them.
            rep["duplicate_row_count"] = rep.get("duplicate_row_count", 0) + 1
            if pid not in rep["duplicate_products"]:
                rep["duplicate_products"].append(pid)
            continue
        seen_ids[pid] = True
        if only_ids and pid not in only_ids:
            continue
        # A soft-deleted row is not a live product: never spend an upload on
        # it, and never resurrect it by writing a fresh image_url.
        if str(row.get("source") or "").strip().lower() in ("deleted", "replaced"):
            rep["tombstone_rows_skipped"].append(pid)
            continue
        if live_only and row.get("online") is False:
            rep["products_skipped"] += 1
            rep["skipped_detail"].append({"id": pid, "reason": "online=false"})
            continue
        rep["products_examined"] += 1
        rows.append(row)
        if str(pid).startswith("wix-"):
            rep["legacy_id_references"].append(pid)
        if _price_missing(row):
            rep["missing_prices"].append(
                {"id": pid, "name": row.get("name"),
                 "priceNgn": row.get("priceNgn"), "priceCfa": row.get("priceCfa")})
        bad_prices = _price_non_positive(row)
        if bad_prices:
            rep["non_positive_prices"].append(
                {"id": pid, "name": row.get("name"),
                 "offenders": {c: v for c, v in bad_prices}})
        if _stock_missing(row):
            rep["missing_stock"].append({"id": pid, "name": row.get("name")})
        else:
            src = _stock_key(row) or ""
            rep["stock_value_source"][src] = rep["stock_value_source"].get(src, 0) + 1
        if limit and len(rows) >= limit:
            break

    for row in rows:
        pid = str(row["id"]).strip()
        # --- Existing HTTPS URLs: rows that already point somewhere. ---
        # Counted for EVERY examined row, before any skip, so the tally is
        # about the data rather than about what this run would do to it.
        current_url = str(row.get("image_url") or "").strip()
        kind, host = classify_image_url(current_url, bucket)
        tally = rep["existing_https_urls"]
        tally[kind] += 1
        if host:
            tally["host_breakdown"][host] = tally["host_breakdown"].get(host, 0) + 1
        if kind in ("already_public_supabase_uploads", "other_https_host",
                    "insecure_http", "unresolved_template"):
            tally["count"] += 1
            if len(tally["samples"]) < 25:
                tally["samples"].append({"id": pid, "kind": kind,
                                         "image_url": current_url})

        if kind == "already_public_supabase_uploads":
            # Already points at a complete HTTPS public URL in `uploads`, so
            # there is nothing to do. Checked BEFORE the local file, because
            # requiring the file would report "missing image" for a row that
            # is in fact fully migrated - and idempotency means a second run
            # must re-plan nothing.
            rep["products_skipped"] += 1
            rep["images_already_uploaded"] += 1
            rep["skipped_detail"].append({
                "id": pid, "reason": "url_already_current",
                "image_url": current_url, "image": str(row.get("image") or "")})
            continue

        local, rel, status, detail = resolve_source_image(row)
        rep["images_discovered"] += 1

        if status != "found":
            rep["products_skipped"] += 1
            rep["skipped_detail"].append({"id": pid, "reason": status,
                                          "image": rel, "detail": detail})
            if status in ("missing", "blank"):
                rep["images_missing"] += 1
            continue

        # --- mapping correctness: the photo must belong to THIS product ---
        stem, slug = _stem(local), str(row.get("slug") or "").strip()
        if slug and stem != slug:
            rep["mapping_warnings"].append(
                {"id": pid, "slug": slug, "file_stem": stem, "image": rel,
                 "reason": "stem_mismatch"})
            rep["products_skipped"] += 1
            rep["skipped_detail"].append(
                {"id": pid, "reason": "stem_mismatch", "image": rel})
            continue
        owner = file_owners.setdefault(rel, pid)
        if owner != pid:
            rep["mapping_warnings"].append(
                {"id": pid, "shares_image_with": owner, "image": rel,
                 "reason": "shared_image_file"})

        try:
            size = os.path.getsize(local)
        except OSError:
            size = -1
        if size > MAX_BYTES:
            rep["products_skipped"] += 1
            rep["skipped_detail"].append({"id": pid, "reason": "too_large",
                                          "bytes": size, "image": rel})
            continue
        if _ext(local) not in IMAGE_EXT:
            rep["products_skipped"] += 1
            rep["skipped_detail"].append({"id": pid, "reason": "unsupported_extension",
                                          "image": rel})
            continue

        digest = _sha256(local)
        key = storage_key_for(pid, local, digest)
        url = _public_url(supabase_url, bucket, key)
        url_is_template = not url
        if url_is_template:
            url = _template_url(bucket, key)
        already = False
        if client is not None:
            names = list_existing_keys(client, bucket, f"products/{_slugify(pid)}")
            already = os.path.basename(key) in names

        current = str(row.get("image_url") or "")
        if current == url and _is_public_supabase_url(current):
            rep["products_skipped"] += 1
            rep["images_already_uploaded"] += 1
            rep["skipped_detail"].append({"id": pid, "reason": "url_already_current",
                                          "image_url": url})
            continue
        if already:
            rep["images_already_uploaded"] += 1
        else:
            rep["images_to_upload"] += 1

        rep["products_matched"] += 1
        rep["proposed_storage_paths"].append({
            "product_id": pid, "local_file": rel, "sha256": digest,
            "bytes": size, "bucket": bucket, "key": key,
            "public_url": url, "url_is_template": url_is_template,
            "object_already_in_bucket": already,
            "current_image_url": current,
            "would_upload": not already,
            "would_update_image_url": current != url,
        })
        rep["proposed_database_updates"].append({
            "table": "products", "operation": "UPDATE (never INSERT)",
            "match": {"id": pid},
            "set": {"image_url": url, "updated_at": "<run timestamp>"},
            "url_is_template": url_is_template,
            "columns_never_written": ["id", "priceNgn", "priceCfa",
                                      "compareNgn", "compareCfa",
                                      "stock_quantity", "stock", "name",
                                      "category", "online"],
            "observed_before": {k: row.get(k) for k in
                                ("priceNgn", "priceCfa", "stock_quantity",
                                  "stock", "online", "image_url")},
        })
    rep["distinct_local_files"] = len(file_owners)
    return rep


def plan_categories(categories, bucket, supabase_url, client=None):
    rep = {"examined": 0, "to_upload": 0, "already_uploaded": 0, "missing": 0,
           "proposed_storage_paths": [], "proposed_database_updates": []}
    for cat in categories:
        cid = str(cat.get("id") or "").strip()
        if not cid:
            continue
        rep["examined"] += 1
        local, rel, status, detail = resolve_source_image(
            {"image": cat.get("image") or cat.get("image_url")})
        if status != "found":
            rep["missing"] += 1
            continue
        digest = _sha256(local)
        key = (f"categories/{_slugify(cid)}/{digest[:16]}-"
               f"{_slugify(_stem(local))}.{_ext(local)}")
        url = _public_url(supabase_url, bucket, key)
        already = False
        if client is not None:
            names = list_existing_keys(client, bucket, f"categories/{_slugify(cid)}")
            already = os.path.basename(key) in names
        if already or cat.get("image_url") == url:
            rep["already_uploaded"] += 1
            continue
        rep["to_upload"] += 1
        rep["proposed_storage_paths"].append(
            {"category_id": cid, "local_file": rel, "key": key, "public_url": url})
        rep["proposed_database_updates"].append(
            {"table": "categories", "operation": "UPDATE (never INSERT)",
             "match": {"id": cid}, "set": {"image_url": url}})
    return rep


# ---------------------------------------------------------------- executing
def execute_product_plan(client, plan, bucket, supabase_url, http_verify=False):
    """Upload + patch. Runs ONLY outside --dry-run."""
    out = {"uploaded": 0, "reused": 0, "rows_updated": 0, "verified": 0,
           "failed": [], "verified_urls": [], "not_verified": []}
    for item in plan["proposed_storage_paths"]:
        pid = item["product_id"]
        url = item["public_url"]
        # Hard stop: a row may only ever receive a complete HTTPS public URL.
        # Without this a misconfigured run would write a relative path and the
        # storefront would show a broken image while reporting "migrated".
        if not _is_public_supabase_url(url):
            out["failed"].append({"id": pid,
                                  "reason": "refusing to save a URL that is not "
                                            "a complete HTTPS public URL"})
            continue
        local = _inside_repo_images(item["local_file"])
        if not local:
            out["failed"].append({"id": pid, "reason": "local file vanished"})
            continue
        key = item["key"]
        with open(local, "rb") as fh:
            data = fh.read()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            out["failed"].append({"id": pid, "reason": "file changed mid-run"})
            continue

        if item["object_already_in_bucket"]:
            exists, _meta = object_exists(client, bucket, key)
            if not exists:
                item["object_already_in_bucket"] = False
        if not item["object_already_in_bucket"]:
            ok, detail = upload_object(client, bucket, key, data,
                                       storage.mime_for(_ext(local)))
            if not ok:
                out["failed"].append({"id": pid, "key": key, "reason": detail})
                continue
            out["uploaded"] += 1
        else:
            out["reused"] += 1

        # --- verify the object landed before touching the database ---
        exists, _meta = object_exists(client, bucket, key)
        if not exists:
            out["failed"].append({"id": pid, "key": key,
                                  "reason": "object missing after upload"})
            continue

        ok, res = update_image_url(client, pid, url)
        if not ok:
            out["failed"].append({"id": pid, "key": key, "reason": f"db: {res}"})
            continue
        out["rows_updated"] += 1

        # --- verify the saved URL reads back from the row ---
        try:
            back = (client.table("products").select("id,image_url")
                    .eq("id", pid).execute())
            rows = getattr(back, "data", back) or []
            saved = (rows[0].get("image_url") if rows else "") or ""
        except Exception as exc:
            saved = ""
            out["not_verified"].append({"id": pid, "reason": _mask(exc)})
        if saved == url and _is_public_supabase_url(saved):
            out["verified"] += 1
            entry = {"id": pid, "image_url": saved}
            if http_verify:
                entry["http_status"] = http_check(saved)
            out["verified_urls"].append(entry)
        else:
            out["not_verified"].append({"id": pid, "expected": url, "saved": saved})
    return out


def execute_category_plan(client, plan, bucket):
    import storage
    out = {"uploaded": 0, "rows_updated": 0, "failed": []}
    for item in plan["proposed_storage_paths"]:
        local = _inside_repo_images(item["local_file"])
        if not local:
            out["failed"].append({"id": item["category_id"], "reason": "file gone"})
            continue
        with open(local, "rb") as fh:
            data = fh.read()
        ok, detail = upload_object(client, bucket, item["key"], data,
                                   storage.mime_for(_ext(local)))
        if not ok:
            out["failed"].append({"id": item["category_id"], "reason": detail})
            continue
        out["uploaded"] += 1
        ok, res = update_category_image(client, item["category_id"],
                                        item["public_url"])
        if not ok:
            out["failed"].append({"id": item["category_id"], "reason": f"db: {res}"})
            continue
        out["rows_updated"] += 1
    return out


def live_intent_report(products, seed_count, overrides, deleted):
    """State which products are intended to be live - with the evidence.

    This exists because "publish the catalogue" is not a decision this script
    is allowed to make, and the local data does not actually make it either:

      * data/seed.json is the Wix import source. NONE of its rows carry an
        `online` flag, and the schema defaults `online` to true - so importing
        all 258 would put all 258 on the storefront. That is why the count in
        Supabase (a subset) is the only real statement of intent.
      * data/catalog.json's overrides are almost entirely TEST FIXTURES that
        the pytest suite wrote into the tracked file (jau-stock-*, jau-mirror-*,
        and the ids in `deleted`). They are not merchandising decisions and
        must never be published to production.

    This script only sets image_url on rows that already exist, so it cannot
    publish any of them. The report says so explicitly rather than leaving a
    reviewer to infer it.
    """
    ids = [str(p.get("id") or "") for p in products or []]
    fixtures = sorted(pid for pid in overrides if _FIXTURE_ID_RE.match(pid))
    explicit_offline = [str(p.get("id")) for p in products or []
                        if p.get("online") is False]
    seed_rows = [p for p in products or []
                 if str(p.get("id") or "").startswith("wix-")]
    with_photo = sum(1 for p in seed_rows
                     if resolve_source_image(p)[2] == "found")
    return {
        "local_rows_considered": len(products or []),
        "local_seed_rows": seed_count,
        "seed_rows_carrying_an_explicit_online_flag":
            sum(1 for p in seed_rows if p.get("online") is not None),
        "seed_rows_with_a_real_committed_photo": with_photo,
        "seed_rows_on_the_placeholder_only": len(seed_rows) - with_photo,
        "rows_explicitly_marked_offline": explicit_offline,
        "catalog_overrides": len(overrides),
        "catalog_overrides_that_are_test_fixtures": fixtures,
        "catalog_overrides_that_are_real_products":
            sorted(set(overrides) - set(fixtures)),
        "catalog_deleted_ids": len(deleted),
        "decision": (
            "NOT DECIDED BY THIS SCRIPT. No local row carries an online flag, "
            "and the admin overrides are test fixtures, so the repository does "
            "not express a publish intent. The rows already present in "
            "Supabase are the live set; this migration only sets image_url on "
            "them and inserts nothing. Importing all 258 seed rows would put "
            "all 258 on the storefront, because the schema defaults online to "
            "true - do not do that without an explicit merchandising decision."),
    }


# Operator decisions recorded explicitly, so the live set is a documented
# decision rather than something a reviewer has to reverse-engineer from the
# classifier's output. Every entry keeps the row intact: no delete, no rename,
# no price change - only `online`, and only via the separate SQL the report
# emits, never by this script.
FORCED_OFFLINE = {
    "wix-001": ("operator decision: placeholder image and stock_quantity=0; it "
                "was flagged online=true before it was ready"),
    "wix-012": ("operator decision: priceNgn=0 is not a valid retail price; the "
                "intended price is unknown and must not be guessed"),
}


def classify_live_intent(product):
    """Apply the operator's live-product policy to ONE row.

    Policy (agreed, and deliberately conservative):
      * a real committed photo + a valid price + valid stock  -> online = True
      * placeholder-only                                     -> online = False
      * anything else that cannot be verified                -> online = False
        and reported for a human decision, never silently published

    Returning online=False for the uncertain cases is the safe direction: a
    product missing from the storefront is a visible, fixable problem, while a
    broken product page with no photo and no price is a live one.
    """
    pid = str(product.get("id") or "")
    if _FIXTURE_ID_RE.match(pid):
        return False, "fixture", "pytest fixture row, never published"
    if pid in FORCED_OFFLINE:
        return False, "operator_offline", FORCED_OFFLINE[pid]

    _local, rel, status, detail = resolve_source_image(product)
    if status != "found":
        if status == "placeholder":
            return False, "placeholder_only", rel or detail
        return False, "no_image", f"image status={status}: {detail}"

    reasons = []
    prices = {}
    for col in ("priceNgn", "priceCfa"):
        raw = product.get(col)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        prices[col] = raw
        # 0 is not a usable price - it would put a free product on the store.
        if value is None or value <= 0:
            reasons.append(f"invalid {col}={raw!r}")

    key = _stock_key(product)
    stock = product.get("stock_quantity")
    if stock is None:
        stock = product.get("stock")
    try:
        stock_value = int(stock)
    except (TypeError, ValueError):
        stock_value = None
    if key is None or stock_value is None:
        reasons.append("stock missing")
    elif stock_value <= 0:
        reasons.append(f"stock is {stock_value}")

    evidence = {"image": rel, "stock_source": key, "stock": stock_value,
                **prices}
    if reasons:
        return False, "needs_review", "; ".join(reasons)
    return True, "live", rel


def _live_set_sql(live, operator_offline, placeholder, no_image,
                  needs_review, fixtures):
    """UPDATE-only SQL for the live-set decision.

    Deliberately narrow: it touches `online` and `updated_at` and nothing else.
    No DELETE, no rename, no price or stock column. Idempotent, because setting
    a boolean to the value it already has is a no-op.
    """
    def ids(entries):
        return ", ".join("'" + e["id"].replace("'", "''") + "'" for e in entries)

    offline = list(operator_offline) + list(placeholder) + list(no_image) \
        + list(needs_review)
    parts = [
        "-- Generated by migrate_images.py. REVIEW BEFORE RUNNING.",
        "-- Applies the approved live-product decision. UPDATE only:",
        "-- no row is deleted, no id is renamed, no price or stock is touched.",
        "",
    ]
    if live:
        parts += [
            "-- Approved live products: real photo + valid price + valid stock.",
            "update products set online = true, updated_at = now()",
            " where id in (" + ids(live) + ");",
            "",
        ]
    if offline:
        parts += [
            "-- Offline: operator decisions, placeholder-only, no image, or",
            "-- awaiting review. Rows are kept intact.",
            "update products set online = false, updated_at = now()",
            " where id in (" + ids(offline) + ");",
            "",
        ]
    if fixtures:
        parts += [
            "-- pytest fixtures. These must never exist in production; if any",
            "-- do, investigate - do not publish them.",
            "-- fixture ids: " + ", ".join(e["id"] for e in fixtures),
        ]
    return "\n".join(parts)


def live_set_report(products, overrides):
    """Exactly which products are intended to be live, and why - per id.

    'Document exactly which products are intended to be live before
    migration' means a reviewer must be able to read the list, not a count.
    So this emits every id in each bucket plus the reason it landed there.
    """
    buckets = {}
    for product in products or []:
        online, reason, detail = classify_live_intent(product)
        entry = {"id": str(product.get("id") or ""),
                 "name": str(product.get("name") or "")[:120],
                 "reason": detail}
        buckets.setdefault(reason, []).append(entry)

    live = sorted(buckets.get("live", []), key=lambda e: e["id"])
    operator_offline = sorted(buckets.get("operator_offline", []),
                              key=lambda e: e["id"])
    fixtures = sorted(buckets.get("fixture", []), key=lambda e: e["id"])
    placeholder = sorted(buckets.get("placeholder_only", []), key=lambda e: e["id"])
    no_image = sorted(buckets.get("no_image", []), key=lambda e: e["id"])
    needs_review = sorted(buckets.get("needs_review", []), key=lambda e: e["id"])

    # wix-001 is worth calling out by name: it is the only local row carrying
    # an explicit online flag, and it is set to True while the row points at
    # the placeholder image with stock_quantity 0. Under this policy it must
    # NOT be live, so the conflict is stated rather than left to inference.
    conflicts = []
    for product in products or []:
        pid = str(product.get("id") or "")
        if _FIXTURE_ID_RE.match(pid):
            continue
        if product.get("online") is True:
            online, reason, detail = classify_live_intent(product)
            if not online:
                conflicts.append({"id": pid, "flagged_online": True,
                                  "policy_says": reason, "detail": detail})

    # Per-criterion tallies. A reviewer asked "how many rows pass each check"
    # should not have to reconstruct it from the buckets.
    total = len(products or [])
    valid_image = valid_price = valid_stock = 0
    for product in products or []:
        if str(product.get("id") or "") in FORCED_OFFLINE:
            continue
        if _FIXTURE_ID_RE.match(str(product.get("id") or "")):
            continue
        if resolve_source_image(product)[2] == "found":
            valid_image += 1
        if not _price_missing(product) and not _price_non_positive(product):
            valid_price += 1
        if not _stock_missing(product):
            try:
                stock = product.get("stock_quantity")
                if stock is None:
                    stock = product.get("stock")
                if int(stock) > 0:
                    valid_stock += 1
            except (TypeError, ValueError):
                pass

    return {
        "total_source_rows": total,
        "rows_with_a_valid_image": valid_image,
        "rows_with_a_valid_price": valid_price,
        "rows_with_valid_stock": valid_stock,
        "policy": ("real committed photo + priceNgn>0 + priceCfa>0 + stock>0 "
                   "-> online=true; placeholder-only or unverifiable -> "
                   "online=false and reported for a human decision"),
        "counts": {
            "approved_live": len(live),
            "operator_offline": len(operator_offline),
            "placeholder_only": len(placeholder),
            "no_image": len(no_image),
            "needs_review": len(needs_review),
            "test_fixtures_excluded": len(fixtures),
        },
        "approved_live_count": len(live),
        "operator_offline": operator_offline,
        "operator_offline_ids": [e["id"] for e in operator_offline],
        "live_ids": [e["id"] for e in live],
        "live": live,
        "placeholder_only_ids": [e["id"] for e in placeholder],
        "no_image_ids": [e["id"] for e in no_image],
        "needs_review": needs_review,
        "test_fixtures_excluded_ids": [e["id"] for e in fixtures],
        "existing_online_flags_that_conflict_with_the_policy": conflicts,
        # The SQL that WOULD apply this decision. Emitted as text so it is
        # reviewed with the report - migrate_images.py never executes it, and
        # never writes `online` itself.
        "apply_sql": _live_set_sql(live, operator_offline, placeholder,
                                   no_image, needs_review, fixtures),
        "applied_by_this_script": False,
        "note": ("This is a RECOMMENDATION, not an action. migrate_images.py "
                 "writes only image_url and updated_at, so it never changes "
                 "`online`. Apply the live set separately, after review, and "
                 "never by importing all 258 seed rows - the schema defaults "
                 "`online` to true, which would publish every one of them."),
    }


# ------------------------------------------------------------------- report
def write_report(report, path):
    """Write the JSON report, masked.

    Serialise first, then mask the whole document: a credential can only reach
    the file inside a string somewhere, so redacting the serialised form
    catches every path it could have arrived by.
    """
    try:
        text = _mask(json.dumps(report, indent=2, ensure_ascii=False))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"\nReport written to {path}")
        return True
    except OSError as exc:
        print(f"\nCould not write the report: {exc}")
        return False


def summarize(report):
    p = report["products"]
    lines = [
        "",
        "=" * 74,
        f"  Jaura Store image migration — {'DRY RUN (no writes)' if report['dry_run'] else 'REAL RUN'}",
        "=" * 74,
        f"  mode                : {'dry-run' if report['dry_run'] else 'apply'}",
        f"  data source         : {report['data_source']}",
        f"  bucket              : {report['bucket']}",
        f"  supabase configured : {report['supabase_configured']}",
        "",
        f"  products examined        : {p['products_examined']}",
        f"  products matched         : {p['products_matched']}",
        f"  products skipped         : {p['products_skipped']}",
        f"  images discovered        : {p['images_discovered']}",
        f"  images missing           : {p['images_missing']}",
        f"  images already uploaded  : {p['images_already_uploaded']}",
        f"  images to upload         : {p['images_to_upload']}",
        f"  non-positive prices      : {len(p['non_positive_prices'])}",
        f"  duplicate products       : {len(p['duplicate_products'])}",
        f"  blank product ids        : {len(p['blank_ids'])}",
        f"  distinct local files     : {p.get('distinct_local_files', 0)}",
        f"  missing prices           : {len(p['missing_prices'])}",
        f"  missing stock            : {len(p['missing_stock'])}",
        f"  stock value source       : {p.get('stock_value_source', {})}",
        f"  legacy wix-* ids         : {len(p['legacy_id_references'])}",
        f"  tombstone rows skipped   : {len(p.get('tombstone_rows_skipped', []))}",
        f"  mapping warnings         : {len(p['mapping_warnings'])}",
        f"  proposed DB updates      : {len(p['proposed_database_updates'])}",
        f"  proposed storage paths   : {len(p['proposed_storage_paths'])}",
    ]
    if report.get("categories"):
        c = report["categories"]
        lines += [f"  categories examined      : {c['examined']}",
                  f"  category images to upload: {c['to_upload']}"]
    for w in p["mapping_warnings"][:10]:
        lines.append(f"  !! mapping: {w}")
    for b in p["blank_ids"][:5]:
        lines.append(f"  !! blank id: {b}")
    if report.get("environment_blockers"):
        lines.append("")
        lines.append("  environment blockers (cannot apply until resolved):")
        for e in report["environment_blockers"]:
            lines.append(f"    - {e}")
    lines.append(f"  safe to apply          : {report.get('safe_to_apply')}")
    lines.append("=" * 74)
    return "\n".join(lines)


def discrepancy_report(seed_count, override_count, overrides, deleted,
                       supabase_rows=None):
    """Explain the 258 / 53 / 18 numbers instead of glossing over them."""
    fixture_like = sorted(pid for pid in overrides
                          if re.match(r"^jau-(stock|mirror|unit|sync|opt)", str(pid)))
    info = {
        "local_seed_rows": seed_count,
        "local_catalog_overrides": override_count,
        "local_catalog_deleted": len(deleted),
        "override_ids": sorted(overrides.keys()),
        "override_ids_that_look_like_test_fixtures": fixture_like,
        "note": (
            "data/seed.json holds 258 legacy wix-* SOURCE rows scraped from the "
            "old Wix site; it is an import source, not a publish list. "
            "data/catalog.json holds admin overrides. This script never inserts "
            "a product, so it cannot publish any of them - it only sets "
            "image_url on rows that already exist in Supabase."),
    }
    if supabase_rows is None:
        info["supabase_product_count"] = None
        info["supabase_verification"] = (
            "NOT VERIFIED: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set "
            "in this environment, so the production 53 / 35 wix-* / 18 jau-* "
            "counts from the earlier audit could not be re-checked. Re-run this "
            "dry-run with credentials to confirm them.")
    else:
        wix = [r["id"] for r in supabase_rows if str(r.get("id", "")).startswith("wix-")]
        jau = [r["id"] for r in supabase_rows if str(r.get("id", "")).startswith("jau-")]
        other = [r["id"] for r in supabase_rows
                 if not str(r.get("id", "")).startswith(("wix-", "jau-"))]
        info["supabase_product_count"] = len(supabase_rows)
        info["supabase_wix_ids"] = len(wix)
        info["supabase_jau_ids"] = len(jau)
        info["supabase_other_ids"] = len(other)
        info["supabase_jau_ids_matching_test_fixtures"] = sorted(
            set(jau) & set(fixture_like))
        info["supabase_live_rows"] = len(
            [r for r in supabase_rows if r.get("online") is not False])
        info["supabase_rows_with_https_image"] = len(
            [r for r in supabase_rows if _is_public_supabase_url(r.get("image_url"))])
    return info


# --------------------------------------------------------------------- main
def build_parser():
    ap = argparse.ArgumentParser(
        prog="migrate_images.py",
        description="Upload committed product/category photos to the public "
                    "Supabase 'uploads' bucket and set products.image_url to "
                    "the complete HTTPS public URL. Never deletes, never "
                    "renames, never inserts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python3 migrate_images.py --dry-run --report migration-dry-run.json\n"
            "  python3 migrate_images.py --bucket uploads --report migration-result.json\n"
            "\n"
            "environment:\n"
            "  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_BUCKET=uploads\n"
            "  The service-role key is read from the environment and is never\n"
            "  printed or written into a report.\n"))
    ap.add_argument("--dry-run", action="store_true",
                    help="read and report only: no uploads, no database writes")
    ap.add_argument("--bucket", default=os.environ.get("SUPABASE_BUCKET") or DEFAULT_BUCKET,
                    help=f"public Storage bucket (default: {DEFAULT_BUCKET})")
    ap.add_argument("--report", default="",
                    help="write the JSON report to this path")
    ap.add_argument("--source", choices=("auto", "supabase", "local"),
                    default="auto",
                    help="auto = Supabase when configured, else the local "
                         "catalogue (offline analysis only)")
    ap.add_argument("--categories", action="store_true",
                    help="also plan/apply category images")
    ap.add_argument("--only-ids", default="",
                    help="comma-separated product ids to restrict the run to")
    ap.add_argument("--live-only", action="store_true",
                    help="skip rows whose online flag is false")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap the number of products planned (0 = no cap)")
    ap.add_argument("--http-check", action="store_true",
                    help="HEAD each saved public URL to confirm HTTP 200")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    bucket = (args.bucket or DEFAULT_BUCKET).strip() or DEFAULT_BUCKET
    if bucket != DEFAULT_BUCKET:
        print("error: only the uploads Storage bucket is supported", file=sys.stderr)
        return 2

    client, supabase_url = _client()
    configured = client is not None

    if args.source == "supabase" and not configured:
        print("error: --source supabase needs SUPABASE_URL + "
              "SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
        return 2
    if args.source == "local" and configured:
        client = None            # explicit offline analysis

    seed_count = overrides = deleted = 0
    categories = []
    if client is not None:
        products = fetch_products(client)
        data_source = "supabase:products (live rows)"
    else:
        merged, seed_count, overrides, deleted = load_local_catalogue()
        products = list(merged.values())
        data_source = "local catalogue (data/seed.json + data/catalog.json)"
        if args.source == "supabase":
            return 2
    if args.categories:
        categories = (fetch_categories(client) if client is not None
                      else load_local_categories())

    only_ids = {s.strip() for s in args.only_ids.split(",") if s.strip()} or None

    product_plan = plan_products(products, bucket, supabase_url, client=client,
                                 only_ids=only_ids,
                                 limit=(args.limit or None),
                                 live_only=args.live_only)
    cat_plan = plan_categories(categories, bucket, supabase_url, client=client) \
        if args.categories else None

    report = {
        "generated_at": _now(),
        "dry_run": bool(args.dry_run),
        "script": "migrate_images.py",
        "bucket": bucket,
        "supabase_url": supabase_url,
        "supabase_configured": configured,
        "data_source": data_source,
        "safety": {
            "deletes_local_files": False,
            "deletes_storage_objects": False,
            "deletes_database_rows": False,
            "inserts_database_rows": False,
            "renames_product_ids": False,
            "writes_price_or_stock_columns": False,
            "writable_columns": list(WRITABLE_COLUMNS),
            "credentials_in_report": False,
        },
        "catalogue": discrepancy_report(
            seed_count if isinstance(seed_count, int) else 0,
            len(overrides) if isinstance(overrides, dict) else 0,
            overrides if isinstance(overrides, dict) else {},
            deleted if isinstance(deleted, list) else [],
            supabase_rows=products if client is not None else None),
        "live_set": live_set_report(
            products,
            overrides if isinstance(overrides, dict) else {}),
        "live_intent": live_intent_report(
            products,
            seed_count if isinstance(seed_count, int) else 0,
            overrides if isinstance(overrides, dict) else {},
            deleted if isinstance(deleted, list) else []),
        "products": product_plan,
        "categories": cat_plan,
    }

    # Blocking conditions: never let a suspicious plan through silently.
    blockers = []
    if product_plan["blank_ids"]:
        blockers.append(f"{len(product_plan['blank_ids'])} product rows have a blank id")
    if product_plan["duplicate_products"]:
        blockers.append(f"duplicate product ids: {product_plan['duplicate_products'][:10]}")
    _urls = product_plan["existing_https_urls"]
    if _urls["insecure_http"]:
        blockers.append(f"{_urls['insecure_http']} row(s) already hold a plain "
                        "http:// image_url - mixed content, fix before migrating")
    if _urls["unresolved_template"]:
        blockers.append(f"{_urls['unresolved_template']} row(s) hold an "
                        "unresolved <SUPABASE_URL> template instead of a real URL")
    if product_plan["mapping_warnings"]:
        blockers.append(f"{len(product_plan['mapping_warnings'])} image->product "
                        "mapping warning(s) - see mapping_warnings")
    # Environment problems are separate from plan problems: the plan can be
    # perfectly sound and still be impossible to apply here.
    env_blockers = []
    if not supabase_url:
        env_blockers.append("SUPABASE_URL is not set, so proposed image_url "
                            "values are templates, not real URLs")
    if not configured:
        env_blockers.append("Supabase credentials are not configured in this "
                            "environment, so the production row counts and the "
                            "existing bucket contents could not be verified")
    report["blockers"] = blockers
    report["environment_blockers"] = env_blockers
    report["safe_to_apply"] = (not blockers and not env_blockers
                               and product_plan["products_matched"] > 0)

    if not args.dry_run:
        if blockers or env_blockers:
            print("\nREFUSING TO RUN: unresolved blockers\n  - "
                  + "\n  - ".join(blockers + env_blockers))
            if args.report:
                write_report(report, args.report)
            return 3
        if not configured:
            print("error: a real run needs SUPABASE_URL + "
                  "SUPABASE_SERVICE_ROLE_KEY. Use --dry-run to plan offline.",
                  file=sys.stderr)
            if args.report:
                write_report(report, args.report)
            return 2
        report["execution"] = {
            "products": execute_product_plan(client, product_plan, bucket,
                                             supabase_url, args.http_check),
            "categories": (execute_category_plan(client, cat_plan, bucket)
                           if cat_plan else None),
        }

    print(_mask(summarize(report)))
    if args.report:
        write_report(report, args.report)
    if args.dry_run:
        print("\nDRY RUN: nothing was uploaded and no database row was written.")
    if blockers:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
