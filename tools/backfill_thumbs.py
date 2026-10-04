#!/usr/bin/env python3
"""Write the missing .400w.webp companion for photos already in the bucket.

New uploads get their companion from the background queue
(storage.save_image -> enqueue_thumbnail), but every photo stored BEFORE that
existed is still a full-size object: a phone opening the shop pulls the whole
file for a 300px card. This tool walks the bucket once and gives every public
photo the same 400px WebP sibling the catalogue photos ship in the repo
(tools/make_thumbs.py), so a thumbnail-first storefront and the small
thumbnails in the admin/email receipts both have something small to load.

Nothing else changes: the original object stays exactly where it is, under the
same URL, with the same bytes, and no database row is touched.

Usage:
    python3 tools/backfill_thumbs.py                 # dry run (default)
    python3 tools/backfill_thumbs.py --apply         # write the companions
    python3 tools/backfill_thumbs.py --apply --limit 25   # a small first batch

Requires SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in the environment (the same
pair the app runs with). Exits 1 without them.

Safety:
  * only public image objects are considered; anything under a sensitive
    folder (uploads/proofs/...) is never read and never written, and neither
    is any object that already has a companion, is not an image, or is not
    decodable;
  * a companion write that fails is reported and the run continues - the tool
    is re-runnable and idempotent (an existing companion is skipped);
  * the dry run (default) prints exactly what it would write and writes
    nothing;
  * it never deletes or rewrites the original.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
import supabase_store  # noqa: E402
import catalog  # noqa: E402

BUCKET = "uploads"

# Same rule the upload path uses: a payment proof is private evidence, never
# artwork, and it must not be copied into a second object.
SENSITIVE_FOLDERS = ("proofs",)

PHOTO_EXTS = ("jpg", "jpeg", "png", "webp")


def _require_env():
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
               if not os.environ.get(k)]
    if missing:
        print("error: missing environment variable(s): " + ", ".join(missing))
        print("export SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=...")
        return False
    return True


def list_objects(bucket, prefix=""):
    """Every object path under ``prefix`` (folders are walked, pages are read).

    Same shape as tools/cleanup_supabase_storage.py: a Supabase folder entry
    carries no id and no metadata, which is how it is told apart from a file.
    """
    out = []
    offset = 0
    while True:
        try:
            rows = bucket.list(prefix, {"limit": 1000, "offset": offset}) or []
        except Exception as exc:                   # pragma: no cover - network
            print(f"  list failed for {prefix or '/'}: {exc}")
            break
        if not rows:
            break
        for row in rows:
            name = row.get("name", "") if isinstance(row, dict) else ""
            path = f"{prefix}/{name}".strip("/")
            if not name:
                continue
            if row.get("id") is None and not row.get("metadata"):
                out.extend(list_objects(bucket, path))
            else:
                out.append(path)
        if len(rows) < 1000:
            break
        offset += len(rows)
    return out


def catalogue_keys():
    """Fallback source: the bucket keys the live catalogue references.

    Used when the bucket cannot be listed (an older storage client, a
    restricted key). It only covers photos that are actually referenced by a
    product or a category, which is the part a shopper downloads anyway, and
    it cannot see which companions already exist - so in that mode the run is
    idempotent rather than skip-aware and rewrites identical bytes.
    """
    keys = set()

    def add(value):
        key = storage._key_from_url(value) or ""
        if key:
            keys.add(key)

    try:
        for product in catalog.merged(include_hidden=True) or []:
            add((product or {}).get("image"))
            add((product or {}).get("image_url"))
            for photo in ((product or {}).get("images") or []):
                add(photo)
    except Exception as exc:                       # pragma: no cover
        print(f"warning: catalogue read failed: {exc}")
    try:
        for category in supabase_store.load_categories() or []:
            add((category or {}).get("image"))
            add((category or {}).get("image_url"))
    except Exception as exc:                       # pragma: no cover
        print(f"warning: category read failed: {exc}")
    return sorted(keys)


def _is_public_photo(key):
    if (os.path.splitext(key)[1].lstrip(".").lower() not in PHOTO_EXTS):
        return False
    return key.split("/", 1)[0] not in SENSITIVE_FOLDERS


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the missing companions")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many photos (default: all)")
    args = parser.parse_args(argv)

    if not _require_env():
        return 1
    client = supabase_store.client()
    if client is None:
        print("error: supabase client unavailable (bad URL/key?)")
        return 1
    bucket = client.storage.from_(BUCKET)

    keys = [k for k in list_objects(bucket) if k]
    source = "bucket listing"
    if not keys:
        keys = catalogue_keys()
        source = "catalogue URLs"
    # A photo whose companion already exists is skipped: the tool is
    # idempotent, so a second run (or a resumed one) writes nothing.
    companions = {k for k in keys if k.endswith(storage.THUMB_SUFFIX)}
    photos = [k for k in keys
              if _is_public_photo(k) and storage.thumb_key(k) not in companions]
    if args.limit and args.limit > 0:
        photos = photos[:args.limit]
    print(f"scanned {len(keys)} object(s) via {source} -> "
          f"{len(photos)} public photo(s) without a companion "
          f"({'apply' if args.apply else 'dry run'})")

    written = skipped = failed = 0
    saved = 0
    for key in sorted(photos):
        companion = storage.thumb_key(key)
        data = storage.fetch_object(key)
        if not data:
            print(f"  skip   {key}  (object unreadable)")
            skipped += 1
            continue
        thumb = storage.make_thumb_bytes(data)
        if not thumb:
            print(f"  skip   {key}  ({len(data)} bytes, not a decodable image)")
            skipped += 1
            continue
        saved += max(0, len(data) - len(thumb))
        if not args.apply:
            print(f"  would  {key}  {len(data)} -> {len(thumb)} bytes "
                  f"({round(len(thumb) * 100.0 / max(1, len(data)))}%)")
            written += 1
            continue
        url = storage.store_thumbnail_bytes(thumb, key)
        if url:
            print(f"  write  {key}  {len(data)} -> {len(thumb)} bytes")
            written += 1
        else:
            print(f"  FAIL   {key}  (companion write failed)")
            failed += 1

    print(f"\ntotal: {written} {'written' if args.apply else 'to write'}, "
          f"{skipped} skipped, {failed} failed")
    if written:
        print(f"smaller payload: {saved / 1024:.1f} KB less per full pass over "
              f"those photos")
    if not args.apply and written:
        print("dry run: nothing was written; re-run with --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
