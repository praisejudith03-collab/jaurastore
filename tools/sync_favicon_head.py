"""Write the favicon <link> block into every shipped HTML page.

The block is generated from ONE source of truth - the asset table in
tools/upload_brand_assets.py - so the URLs in the markup can never drift from
the objects that tool publishes to the Supabase `public-assets` bucket.
tests/test_favicon_assets.py asserts the two agree.

    python3 tools/sync_favicon_head.py            # rewrite the pages
    python3 tools/sync_favicon_head.py --check    # exit 1 on drift, write nothing

The block replaces the old single `<link rel="icon" href="/static/logo.png">`
pair, which pointed browsers and Google's site-icon crawler at the full
1024px brand board - unreadable once scaled down to the 32-48px a search
result actually draws.
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import upload_brand_assets as pub  # noqa: E402

# The live project. Same origin the pages already <link rel="preconnect"> to
# for product photography, so the icons cost no extra DNS/TLS handshake.
SUPABASE_URL = "https://rvkweyipqgsggcnimhxf.supabase.co"

# The shared asset token, bumped whenever a shipped asset changes. Only the
# same-origin fallback carries it: the Supabase objects are immutable at their
# key and are re-uploaded in place.
TOKEN = "149"

START = "  <!-- brand icons: Supabase public-assets bucket (tools/upload_brand_assets.py) -->"
END = "  <!-- /brand icons -->"


def block():
    u = lambda name: pub.public_url(SUPABASE_URL, name)  # noqa: E731
    return "\n".join([
        START,
        f'  <link rel="icon" type="image/png" sizes="32x32" href="{u("favicon-32.png")}" />',
        f'  <link rel="icon" type="image/png" sizes="48x48" href="{u("favicon-48.png")}" />',
        f'  <link rel="apple-touch-icon" sizes="180x180" href="{u("apple-touch-180.png")}" />',
        f'  <link rel="icon" type="image/png" sizes="192x192" href="{u("icon-192.png")}" />',
        "  <!-- same-origin fallback: still a legible mark if the bucket is unreachable -->",
        f'  <link rel="icon" type="image/png" sizes="16x16" href="/images/brand/favicon-16.png?v={TOKEN}" />',
        END,
    ])


# What is being replaced: the generated block itself (re-runs are idempotent)
# or, on the first run, the legacy /static/logo.png icon pair.
GENERATED = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
LEGACY = re.compile(
    r'[ \t]*<link rel="icon"[^>]*href="/static/logo\.png"[^>]*/?>\s*\n'
    r'[ \t]*<link rel="apple-touch-icon"[^>]*href="/static/logo\.png"[^>]*/?>[ \t]*\n')


def rewrite(html):
    """Return the page with the current icon block, or None when unchanged."""
    new = block() + "\n"
    if GENERATED.search(html):
        out = GENERATED.sub(lambda _m: block(), html)
    elif LEGACY.search(html):
        out = LEGACY.sub(new, html, count=1)
    elif "</head>" in html:
        # A page that never had icons at all (404.html). Browsers fall back to
        # /favicon.ico when a document declares none, which on this host is a
        # 404 - so every page gets the block, not just the indexed ones.
        out = html.replace("</head>", new + "</head>", 1)
    else:
        return None
    return out if out != html else None


def pages():
    return sorted(n for n in os.listdir(ROOT) if n.endswith(".html"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report pages that are out of date; write nothing")
    args = ap.parse_args(argv)

    stale = []
    for name in pages():
        path = os.path.join(ROOT, name)
        with open(path, encoding="utf-8") as fh:
            html = fh.read()
        out = rewrite(html)
        if out is None:
            continue
        stale.append(name)
        if not args.check:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(out)
            print(f"[favicon-head] updated {name}")
    if args.check:
        for name in stale:
            print(f"[favicon-head] out of date: {name}")
        return 1 if stale else 0
    if not stale:
        print("[favicon-head] every page already carries the current block")
    return 0


if __name__ == "__main__":
    sys.exit(main())
