"""Publish the brand logo + favicon set to the Supabase `public-assets` bucket.

What it does, in order:

  1. regenerates the square logo and the favicon ladder from
     images/brand/logo.jpg (tools/favicon_assets.py) unless --no-build;
  2. creates the PUBLIC `public-assets` bucket if the project does not have
     it yet (an existing bucket is never reconfigured);
  3. uploads each file to `brand/<name>` with a long immutable Cache-Control
     (the object key changes when the artwork does, so the icons can be
     cached hard - Google re-crawls the URL, not the bytes);
  4. writes the resulting public URLs onto the id=1 `site_settings` row
     (brand_logo_url, favicon_32_url, favicon_48_url, apple_touch_icon_url,
     icon_192_url).

It is IDEMPOTENT: every upload is an upsert, and re-running it after an
artwork change simply overwrites the same keys and rewrites the same columns.

Requires the same credentials the app itself uses - SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY (the service-role key bypasses RLS and is the only
key allowed to create a bucket). They are read from the environment; never
pass a key on the command line.

    export SUPABASE_URL=https://<project>.supabase.co
    export SUPABASE_SERVICE_ROLE_KEY=<service role key>
    python3 tools/upload_brand_assets.py

    python3 tools/upload_brand_assets.py --dry-run   # print the plan + the
                                                     # URLs, touch nothing
    python3 tools/upload_brand_assets.py --no-db     # upload only
    python3 tools/upload_brand_assets.py --no-build  # skip regeneration

--dry-run needs no credentials at all: it prints exactly the URLs the HTML
<head> is expected to carry, which is how the two are kept in step.
"""
import argparse
import mimetypes
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

BUCKET = "public-assets"
PREFIX = "brand"

# Every asset published, and the site_settings column that records its URL
# (None = uploaded but not recorded; the 16px icon has no column of its own).
#   local file name                -> (settings column, )
ASSETS = (
    ("logo-square.png", "brand_logo_url"),
    ("favicon-16.png", None),
    ("favicon-32.png", "favicon_32_url"),
    ("favicon-48.png", "favicon_48_url"),
    ("apple-touch-180.png", "apple_touch_icon_url"),
    ("icon-192.png", "icon_192_url"),
)

# One year, immutable: the key is stable and the bytes only change with a
# deliberate re-run of this tool, at which point Storage serves the new object
# under the same URL and the CDN edge is purged by the upsert.
CACHE_CONTROL = "public, max-age=31536000, immutable"


def brand_dir():
    return os.path.join(ROOT, "images", "brand")


def object_key(name):
    return f"{PREFIX}/{name}"


def public_url(base_url, name):
    base = (base_url or "").rstrip("/")
    return f"{base}/storage/v1/object/public/{BUCKET}/{object_key(name)}"


def planned_urls(base_url):
    """{local file name: public URL} for every published asset."""
    return {name: public_url(base_url, name) for name, _col in ASSETS}


def settings_payload(base_url):
    """The site_settings columns this run writes."""
    return {col: public_url(base_url, name)
            for name, col in ASSETS if col}


def _ensure_bucket(client):
    """Create `public-assets` as a public bucket when it does not exist."""
    try:
        existing = {getattr(b, "name", None) or (isinstance(b, dict) and b.get("name"))
                    for b in (client.storage.list_buckets() or [])}
    except Exception as exc:
        print(f"[brand-upload] could not list buckets ({exc}); "
              f"assuming {BUCKET} exists")
        return False
    if BUCKET in existing:
        print(f"[brand-upload] bucket {BUCKET} already exists")
        return False
    client.storage.create_bucket(
        BUCKET, options={"public": True, "file_size_limit": 10 * 1024 * 1024})
    print(f"[brand-upload] created public bucket {BUCKET}")
    return True


def _upload(client, path, name):
    """Upsert one object. Returns its public URL."""
    with open(path, "rb") as fh:
        data = fh.read()
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    client.storage.from_(BUCKET).upload(
        object_key(name), data,
        {"content-type": ctype, "cache-control": CACHE_CONTROL, "upsert": "true"},
    )
    return len(data)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and the URLs; upload nothing")
    ap.add_argument("--no-build", action="store_true",
                    help="use the committed images/brand files as they are")
    ap.add_argument("--no-db", action="store_true",
                    help="upload the files but do not touch site_settings")
    args = ap.parse_args(argv)

    if not args.no_build and not args.dry_run:
        import favicon_assets
        favicon_assets.write_assets()

    missing = [n for n, _ in ASSETS
               if not os.path.isfile(os.path.join(brand_dir(), n))]
    if missing:
        print("[brand-upload] missing generated assets: " + ", ".join(missing)
              + "\n  run: python3 tools/favicon_assets.py", file=sys.stderr)
        return 1

    # config imports python-dotenv, which a bare checkout may not have. A dry
    # run must work anywhere (it is how the URLs in the HTML head are
    # verified), so fall back to the raw environment.
    try:
        from config import Config
        base_url = (Config.SUPABASE_URL or "").rstrip("/")
        service_key = Config.SUPABASE_SERVICE_ROLE_KEY
    except ImportError:
        if not args.dry_run:
            raise
        base_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    if args.dry_run:
        shown = base_url or "https://<project>.supabase.co"
        print(f"[brand-upload] DRY RUN - bucket {BUCKET!r}, prefix {PREFIX!r}/")
        for name, col in ASSETS:
            size = os.path.getsize(os.path.join(brand_dir(), name))
            print(f"  {name:<22} {size:>7} B  ->  {public_url(shown, name)}"
                  + (f"   [site_settings.{col}]" if col else ""))
        return 0

    if not base_url or not service_key:
        print("[brand-upload] SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be "
              "set in the environment.", file=sys.stderr)
        return 2

    import supabase_store
    client = supabase_store.client()
    if client is None:
        print("[brand-upload] could not build a Supabase client.", file=sys.stderr)
        return 2

    _ensure_bucket(client)

    for name, _col in ASSETS:
        path = os.path.join(brand_dir(), name)
        try:
            size = _upload(client, path, name)
        except Exception as exc:
            print(f"[brand-upload] upload of {name} failed: {exc}", file=sys.stderr)
            return 1
        print(f"[brand-upload] uploaded {object_key(name)} ({size} B) "
              f"-> {public_url(base_url, name)}")

    if args.no_db:
        print("[brand-upload] --no-db: site_settings left untouched")
        return 0

    import supabase_settings
    payload = settings_payload(base_url)
    try:
        supabase_settings.update_site_settings(payload)
    except Exception as exc:
        print(f"[brand-upload] site_settings update failed: {exc}\n"
              f"  the files ARE uploaded; run the ALTERs in "
              f"schema_sections/07_site_settings.sql and retry with --no-build",
              file=sys.stderr)
        return 1
    for col, url in sorted(payload.items()):
        print(f"[brand-upload] site_settings.{col} = {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
