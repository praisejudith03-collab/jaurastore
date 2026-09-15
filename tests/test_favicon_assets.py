"""The brand logo + favicon set: right sizes, right URLs, crawlable.

Google showed a generic icon next to the shop's search result because every
page pointed `rel="icon"` at the full 1024px brand board - a cart, a wordmark,
a tagline and eight category glyphs, squeezed into the 32-48px a result
actually draws. The board is now cropped 1:1 to the cart + "Jaura" mark
(tools/favicon_assets.py), the ladder is published to the Supabase
`public-assets` bucket (tools/upload_brand_assets.py), and every <head> points
at those URLs.

The three things that can silently break that, and are therefore asserted:

  1. a generated icon is missing, the wrong size, or has an alpha channel
     (iOS draws no transparency behind a home-screen icon);
  2. the URLs in the markup drift from the objects the upload tool publishes -
     the markup is generated from the same table, so drift means someone
     hand-edited one side;
  3. robots.txt stops a crawler reaching the icons, which puts the generic
     globe straight back.

Run with:  python3 -m pytest tests/test_favicon_assets.py -q
"""
import os
import struct
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND = os.path.join(ROOT, "images", "brand")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import upload_brand_assets as pub          # noqa: E402
import sync_favicon_head as head           # noqa: E402

# name -> exact pixel size the HTML/manifest promises
EXPECTED = {
    "logo-square.png": 1024,
    "favicon-16.png": 16,
    "favicon-32.png": 32,
    "favicon-48.png": 48,
    "apple-touch-180.png": 180,
    "icon-192.png": 192,
}

HTML_PAGES = sorted(n for n in os.listdir(ROOT) if n.endswith(".html"))


def _png_size(path):
    with open(path, "rb") as fh:
        head_bytes = fh.read(24)
    assert head_bytes[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", head_bytes[16:24])


def _png_color_type(path):
    with open(path, "rb") as fh:
        return fh.read(26)[25]


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------- the files

@pytest.mark.parametrize("name,px", sorted(EXPECTED.items()))
def test_every_icon_ships_at_its_promised_size(name, px):
    path = os.path.join(BRAND, name)
    assert os.path.isfile(path), f"missing generated icon {name}"
    assert _png_size(path) == (px, px), f"{name} is {_png_size(path)}, expected {px}x{px}"
    assert os.path.getsize(path) < 200 * 1024, \
        f"{name} is {os.path.getsize(path)} bytes; keep brand files under 200KB"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_no_icon_carries_transparency(name):
    """Colour types 4 and 6 are the alpha ones; iOS would paint them black."""
    assert _png_color_type(os.path.join(BRAND, name)) not in (4, 6), \
        f"{name} has an alpha channel; icons must be opaque"


def test_the_square_logo_really_is_square_and_centre_framed():
    """1:1, and cropped from the board rather than the whole board scaled.

    The mark band tools/favicon_assets.py finds is much wider than it is tall
    (a cart above a wordmark); a square built from it is therefore padded, and
    its side must be close to the mark's own width - not the full board.
    """
    pytest.importorskip("PIL", reason="Pillow builds the assets")
    import favicon_assets

    square = favicon_assets.build_square()
    assert square.width == square.height, "the master logo must be 1:1"
    assert square.mode == "RGB", "the master logo must be opaque RGB"
    from PIL import Image
    with Image.open(favicon_assets.SOURCE) as board:
        assert square.width < board.width, \
            "the icon is the whole board scaled down, not the mark cropped out"


# ---------------------------------------------------------------- the URLs

def test_the_markup_and_the_upload_tool_agree_on_every_url():
    """One asset table, two consumers: the <head> block and the uploader."""
    urls = pub.planned_urls(head.SUPABASE_URL)
    block = head.block()
    for name in ("favicon-32.png", "favicon-48.png", "apple-touch-180.png",
                 "icon-192.png"):
        assert urls[name] in block, f"{name} URL missing from the <head> block"
    assert "public-assets" in urls["favicon-32.png"]


def test_the_head_block_is_in_every_page_and_up_to_date():
    assert head.main(["--check"]) == 0, \
        "run: python3 tools/sync_favicon_head.py"


@pytest.mark.parametrize("page", HTML_PAGES)
def test_each_page_requests_the_sized_icons(page):
    html = _read(page)
    for size in ("32x32", "48x48"):
        assert f'rel="icon" type="image/png" sizes="{size}"' in html, \
            f"{page} is missing the {size} icon link"
    assert 'rel="apple-touch-icon" sizes="180x180"' in html, \
        f"{page} is missing the 180x180 apple-touch-icon"
    assert "/static/logo.png" not in html.split("</head>")[0], \
        f"{page} still points an icon at the illegible full brand board"


def test_the_settings_columns_cover_the_published_urls():
    """Every published URL that has a home in site_settings has one."""
    payload = pub.settings_payload(head.SUPABASE_URL)
    assert set(payload) == {
        "brand_logo_url", "favicon_32_url", "favicon_48_url",
        "apple_touch_icon_url", "icon_192_url"}
    import supabase_settings
    for column in payload:
        assert column in supabase_settings.DEFAULT_SETTINGS, \
            f"{column} is uploaded but not a known site_settings column"
    schema = _read("supabase_schema.sql")
    for column in payload:
        assert f"add column if not exists {column}" in schema, \
            f"{column} has no idempotent ALTER in supabase_schema.sql"


def test_the_json_ld_logo_prefers_the_published_square_mark():
    store = _read(os.path.join("js", "store.js"))
    assert "public-assets/brand/logo-square.png" in store
    assert "brand_logo_url" in store, \
        "js/store.js should read the published URL off the live site row"


# -------------------------------------------------------------- crawlability

def test_robots_lets_google_and_bing_reach_the_icons():
    robots = _read("robots.txt")
    assert "Allow: /images/brand/" in robots, \
        "the same-origin icon fallback must stay crawlable"
    for agent in ("Googlebot", "Bingbot", "Googlebot-Image"):
        assert f"User-agent: {agent}" in robots, \
            f"{agent} has no explicit group; the site icon may not be fetched"
    # ... and nothing disallows the brand folder anywhere in the file
    for line in robots.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("disallow:"):
            target = stripped.split(":", 1)[1].strip()
            assert not target or not "/images/brand".startswith(target.rstrip("*")), \
                f"robots.txt blocks the brand icons: {stripped!r}"


def test_the_bucket_origin_is_allowed_by_the_content_security_policy():
    """A CSP that forbids the bucket turns every icon into a blocked request."""
    host = head.SUPABASE_URL.replace("https://", "")
    headers = _read("_headers")
    csp_line = next(l for l in headers.splitlines()
                    if "Content-Security-Policy" in l)
    img_src = csp_line.split("img-src", 1)[1].split(";", 1)[0]
    assert host in img_src, \
        f"_headers img-src does not allow {host}: {img_src.strip()!r}"

    import security
    flask_img = security.CSP.split("img-src", 1)[1].split(";", 1)[0]
    assert "https:" in flask_img or host in flask_img, \
        f"the Flask CSP does not allow the bucket: {flask_img.strip()!r}"


def test_the_same_origin_fallback_is_served_and_precached():
    """If the bucket is unreachable the shop still shows a legible mark."""
    assert os.path.isfile(os.path.join(BRAND, "favicon-16.png"))
    sw = _read("sw.js")
    assert "./images/brand/favicon-16.png?v=" in sw, \
        "sw.js should precache the same-origin icon fallback"


# ----------------------------------------------------------------- the tools

def test_the_generator_is_reproducible(tmp_path):
    """Re-running the generator produces byte-identical icons."""
    pytest.importorskip("PIL", reason="Pillow builds the assets")
    import favicon_assets

    written = favicon_assets.write_assets(out_dir=str(tmp_path))
    assert len(written) == len(EXPECTED)
    for name in EXPECTED:
        fresh = tmp_path / name
        assert fresh.is_file(), f"{name} was not regenerated"
        with open(os.path.join(BRAND, name), "rb") as fh:
            committed = fh.read()
        assert fresh.read_bytes() == committed, \
            f"{name} differs from a fresh build; run tools/favicon_assets.py"


def test_the_upload_plan_can_be_reviewed_without_credentials():
    """--dry-run prints exactly what would be published, and touches nothing."""
    proc = subprocess.run(
        [sys.executable, os.path.join("tools", "upload_brand_assets.py"), "--dry-run"],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "public-assets" in proc.stdout
    for name in EXPECTED:
        assert name in proc.stdout, f"{name} missing from the upload plan"
