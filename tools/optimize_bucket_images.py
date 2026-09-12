#!/usr/bin/env python3
"""One-shot re-encoder for the fat images already in the Supabase bucket.

The upload path (storage.optimize_image_bytes) now downscales + re-encodes a
phone original before it is stored, but every photo uploaded before that change
is still a multi-megabyte original in the bucket - on a 4G phone those objects
never finish downloading, which is exactly the "photo uploads but never shows"
report. This script walks every object URL the catalogue references, downloads
each one, and writes the optimized bytes back to the SAME key. No database rows
change and no URL changes: the bucket object is simply replaced in place.

Usage:
    python3 tools/optimize_bucket_images.py          # dry run (default)
    python3 tools/optimize_bucket_images.py --apply  # overwrite in place

Requires SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in the environment (from the
Render dashboard env or the owner). Exits 1 without them.

Safety:
  * only jpg/jpeg/webp objects are rewritten (the formats the optimizer can
    re-encode); anything else is skipped;
  * anything under uploads/proofs/ is never touched (payment evidence);
  * if the re-encode would change the file format (for example a JPEG that
    decodes with transparency would become WebP), the object is reported but
    NOT written - re-save that product's photo through the admin portal so the
    upload path stores the right bytes under the right extension;
  * the dry run (default) prints exactly what it would do without writing.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
import supabase_store  # noqa: E402
import catalog  # noqa: E402

# The object key keeps its original extension (the URL must stay identical).
# `.jpeg` and `.jpg` are the same format, so a JPEG re-encode of a `.jpeg`
# object is an in-place rewrite, not a format change.
_COMPAT_EXT = {"jpeg": "jpg"}

# Keys under these top-level folders are private and are never rewritten.
SENSITIVE_FOLDERS = ("proofs",)

PROCESS_EXT = ("jpg", "jpeg", "webp")
BUCKET = "uploads"


def _require_env():
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
               if not os.environ.get(k)]
    if missing:
        print("error: missing environment variable(s): " + ", ".join(missing))
        print("export SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=...")
        return False
    return True


def _collect_urls():
    """Every image URL the live catalogue references, de-duplicated."""
    seen = set()
    urls = []

    def add(value):
        value = (value or "").strip()
        if value and value not in seen:
            seen.add(value)
            urls.append(value)

    try:
        products = catalog.merged(include_hidden=True) or []
    except Exception as exc:                       # pragma: no cover
        print(f"warning: catalogue read failed: {exc}")
        products = []
    for p in products:
        add(p.get("image"))
        add(p.get("image_url"))
        for g in (p.get("images") or []):
            add(g)

    try:
        categories = supabase_store.load_categories() or []
    except Exception as exc:                       # pragma: no cover
        print(f"warning: categories read failed: {exc}")
        categories = []
    for c in categories:
        add(c.get("image"))
        add(c.get("image_url"))

    return urls


def _keys_from_urls(urls):
    """Map each catalogue URL to its bucket key, skipping proofs and foreign
    hosts. Duplicate keys collapse to one pass."""
    keys = {}
    for url in urls:
        key = supabase_store._storage_path_from_url(url)
        if not key:
            continue
        top = key.split("/", 1)[0]
        if top in SENSITIVE_FOLDERS:
            continue
        keys.setdefault(key, url)
    return keys


def _download(bucket, key):
    """Return the object bytes, or None on any failure."""
    try:
        data = bucket.download(key)
        if isinstance(data, dict):
            # older storage3 clients wrapped the body in {"data": ...} or a
            # response object; unwrap either shape defensively
            data = data.get("data") or data.get("body")
        if hasattr(data, "read"):
            data = data.read()
        if isinstance(data, memoryview):
            data = data.tobytes()
        return data if isinstance(data, (bytes, bytearray)) else None
    except Exception as exc:                       # pragma: no cover
        print(f"  download failed for {key}: {exc}")
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the optimized bytes back to the bucket")
    args = parser.parse_args(argv)

    if not _require_env():
        return 1

    client = supabase_store.client()
    if client is None:
        print("error: supabase client unavailable (bad URL/key?)")
        return 1
    bucket = client.storage.from_(BUCKET)

    urls = _collect_urls()
    keys = _keys_from_urls(urls)
    print(f"found {len(urls)} image URL(s) -> {len(keys)} bucket key(s) "
          f"({'dry run' if not args.apply else 'apply'})")

    total_before = 0
    total_after = 0
    written = 0
    skipped = 0
    reported = 0
    for key in sorted(keys):
        ext = os.path.splitext(key)[1].lstrip(".").lower()
        if ext not in PROCESS_EXT:
            skipped += 1
            continue
        data = _download(bucket, key)
        if data is None:
            continue
        before = len(data)
        out, out_ext = storage.optimize_image_bytes(bytes(data), ext)
        after = len(out)
        total_before += before
        total_after += after
        if out == bytes(data):
            print(f"  keep   {key}  ({before} bytes) - already optimized")
            skipped += 1
            continue
        canon_key = _COMPAT_EXT.get(ext, ext)
        canon_out = _COMPAT_EXT.get(out_ext, out_ext)
        if canon_out != canon_key:
            print(f"  REPORT {key}  {ext} would become {out_ext} - "
                  f"re-save that product's photo (no write)")
            reported += 1
            continue
        if args.apply:
            try:
                bucket.update(key, out,
                              {"content-type": storage.mime_for(ext)})
            except Exception as exc:               # pragma: no cover
                print(f"  FAIL   {key}: {exc}")
                continue
            print(f"  write  {key}  {before} -> {after} bytes "
                  f"(-{before - after})")
        else:
            print(f"  would  {key}  {before} -> {after} bytes "
                  f"(-{before - after})")
        written += 1

    saved = total_before - total_after
    print(f"\ntotal: {written} to {'write' if args.apply else 'rewrite'}, "
          f"{skipped} kept, {reported} report-only")
    print(f"sizes: {total_before} -> {total_after} bytes "
          f"(saving {saved} bytes, {saved / 1024:.1f} KB)"
          if total_before else "sizes: no eligible objects found")
    if not args.apply and written:
        print("dry run: nothing was written; re-run with --apply to overwrite")
    return 0


if __name__ == "__main__":
    sys.exit(main())
