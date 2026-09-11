"""Brand icons: legible at 32-48px, right sizes, token-stamped everywhere.

Google showed an unreadable auto-cropped site icon because favicon.png /
apple-touch.png were the full brand board. tools/brand_icons.py re-derives
them from images/brand/logo.jpg (centred cart + "Jaura" wordmark, no
tagline, no category icons) and refreshes the og-cover on brand cream.

The regenerated files shipped with the shared cache token bumped from v=126
to v=127 (the photo-display fix has since moved it to v=128), and every HTML
reference to a brand image must carry that token - otherwise phones and Google
keep serving the stale illegible icon.

Run with:  python3 -m pytest tests/test_brand_icons.py -q
"""
import os
import re
import struct

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The shared cache token. Bumped for the brand-icon regeneration (126 -> 127),
# again for the photo-display fix (127 -> 128), and again for the storefront
# catalogue fix - name-confirmed dedupe + server-side delete reconciliation in
# js/store.js (128 -> 129). Bumped to 132 for the six-production-fix release
# (stock/categories persistence, deleted products, FR banner, mobile admin +
# header): every asset reference carries ?v=132.
# Bumped to 134 for the brighter, more saturated theme: every asset reference
# carries ?v=134 so a phone holding the v133 stylesheet in its service-worker
# cache downloads the new palette on the first visit instead of the second.
# Bumped to 136 for the centred-header-logo lock + smaller fixed currency
# pills + Perfume/Parfum default category: style.css, js/store.js and sw.js
# all changed, so phones must fetch the new ones on the first visit.
# Bumped to 138 for the one-row phone header (84px logo) + instant product
# photo repaint release: sw.js was still on v136 while pages said v137 (a
# stale-token drift); everything now ships v138 so no phone keeps a v136/v137
# cache after the service worker swaps its VERSION.
# Bumped to 139 for the left-logo one-row header restore (owner request
# 2026-09-11): the header flex-row fix in css/style.css and the logo-first
# markup in js/store.js must reach every phone on the first visit, so all
# pages, scripts and the service worker ship v139 together.
# Bumped to 140 for the speed + shop-banner release (owner request
# 2026-09-11): immutable Cache-Control for tokened assets + the service
# worker's cache-first branch in sw.js, and the bigger full-width shop
# banner with the wordmark anchored to the bottom in css/style.css. All
# pages, scripts and the service worker ship v140 together.
SHARED_TOKEN = "140"


def _image_size(path):
    """Width/height from the file header - no Pillow dependency."""
    with open(path, "rb") as fh:
        head = fh.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
        if head[:2] == b"\xff\xd8":                       # JPEG: walk the markers
            fh.seek(2)
            while True:
                b = fh.read(1)
                while b and b != b"\xff":
                    b = fh.read(1)
                marker = fh.read(1)
                while marker == b"\xff":
                    marker = fh.read(1)
                if not marker:
                    return None
                if marker[0] in range(0xC0, 0xCF) and marker[0] not in (0xC4, 0xC8, 0xCC):
                    fh.read(3)
                    h, w = struct.unpack(">HH", fh.read(4))
                    return (w, h)
                seg = struct.unpack(">H", fh.read(2))[0]
                fh.seek(seg - 2, 1)
    return None


def test_brand_icons_exist_at_the_right_sizes():
    expected = {
        os.path.join("images", "brand", "favicon.png"): (512, 512),
        os.path.join("images", "brand", "apple-touch.png"): (512, 512),
        os.path.join("images", "brand", "og-cover.jpg"): (1200, 630),
    }
    for rel, size in expected.items():
        path = os.path.join(ROOT, rel)
        assert os.path.isfile(path), f"missing brand icon {rel}"
        assert os.path.getsize(path) < 200 * 1024, \
            f"{rel} is {os.path.getsize(path)} bytes; keep it under 200KB"
        assert _image_size(path) == size, \
            f"{rel} is {_image_size(path)}, expected {size}"


def test_no_html_references_a_brand_image_without_the_shared_token():
    ref = re.compile(r"images/brand/[\w.\-]+\.(?:png|jpe?g|webp)([^\"'\s)>]*)")
    checked = 0
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".html"):
            continue
        html = open(os.path.join(ROOT, name), encoding="utf-8").read()
        for m in ref.finditer(html):
            checked += 1
            assert m.group(1) == f"?v={SHARED_TOKEN}", \
                f"{name}: brand reference {m.group(0)!r} must carry ?v={SHARED_TOKEN}"
    assert checked, "no brand-image references found at all - the test is blind"


def test_service_worker_precaches_the_new_brand_icons():
    sw = open(os.path.join(ROOT, "sw.js"), encoding="utf-8").read()
    m = re.search(r'const VERSION = "jaura-v(\d+)";', sw)
    assert m, "sw.js VERSION constant"
    assert m.group(1) == SHARED_TOKEN, \
        f"sw.js VERSION is jaura-v{m.group(1)}; the icon regeneration ships v{SHARED_TOKEN}"
    core = sw.split("const CORE = [", 1)[1].split("];", 1)[0]
    for name in ("favicon.png", "apple-touch.png", "og-cover.jpg"):
        assert f"./images/brand/{name}?v={SHARED_TOKEN}" in core, \
            f"sw.js CORE must precache {name} with the shared token"
