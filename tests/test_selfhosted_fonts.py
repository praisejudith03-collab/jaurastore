"""Guard the self-hosted fonts release (v141).

The three Google Fonts families (Allura, Cormorant Garamond, Outfit) now ship
from /static/fonts as latin-subset woff2 and are wired through css/fonts.css
with the shared ?v= token. These checks pin that:

  * every page that links the stylesheet also links css/fonts.css?v=<token>
    and never mentions fonts.googleapis.com / fonts.gstatic.com;
  * css/fonts.css declares the three families with exactly 10 @font-face
    rules, every URL /static/fonts/...woff2?v=<token>;
  * each font file is a real woff2 on disk (magic "wOF2") and small;
  * the CSP (security.CSP) serves fonts from 'self' only;
  * sw.js precaches every font and the server answers each tokened font URL
    with Cache-Control: public, max-age=31536000, immutable.

The token is derived from sw.js VERSION so these fail the moment the pages,
the worker and the fonts drift apart.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import security  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS_DIR = os.path.join(ROOT, "static", "fonts")

FONT_FILES = [
    "allura-latin-400-normal.woff2",
    "cormorant-garamond-latin-400-normal.woff2",
    "cormorant-garamond-latin-500-normal.woff2",
    "cormorant-garamond-latin-600-normal.woff2",
    "cormorant-garamond-latin-700-normal.woff2",
    "cormorant-garamond-latin-400-italic.woff2",
    "outfit-latin-300-normal.woff2",
    "outfit-latin-400-normal.woff2",
    "outfit-latin-500-normal.woff2",
    "outfit-latin-600-normal.woff2",
]


def _token():
    sw = open(os.path.join(ROOT, "sw.js"), encoding="utf-8").read()
    m = re.search(r'const VERSION = "jaura-v(\d+)";', sw)
    assert m, "sw.js VERSION constant"
    return m.group(1)


@pytest.fixture()
def client():
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def test_every_page_links_fonts_css_and_never_google_fonts():
    token = _token()
    pages = []
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".html"):
            continue
        html = open(os.path.join(ROOT, name), encoding="utf-8").read()
        if "css/style.css" not in html:
            continue
        pages.append(name)
        assert f"css/fonts.css?v={token}" in html, \
            f"{name} links css/style.css but not css/fonts.css?v={token}"
        assert "fonts.googleapis.com" not in html, \
            f"{name} still references fonts.googleapis.com"
        assert "fonts.gstatic.com" not in html, \
            f"{name} still references fonts.gstatic.com"
    assert len(pages) >= 15, f"expected >= 15 pages, saw {len(pages)}"


def test_fonts_css_declares_three_families_ten_rules_token():
    token = _token()
    css = open(os.path.join(ROOT, "css", "fonts.css"), encoding="utf-8").read()
    for family in ("Allura", "Cormorant Garamond", "Outfit"):
        assert f'font-family: "{family}"' in css, f"missing family {family}"
    rules = re.findall(r"@font-face", css)
    assert len(rules) == 10, f"expected 10 @font-face rules, saw {len(rules)}"
    urls = re.findall(r'url\("([^"]+)"\)', css)
    assert len(urls) == 10, f"expected 10 font URLs, saw {len(urls)}"
    for url in urls:
        assert re.fullmatch(rf"/static/fonts/[a-z0-9-]+\.woff2\?v={token}", url), \
            f"bad font URL {url!r}"


def test_every_font_file_exists_is_woff2_and_small():
    assert sorted(os.listdir(FONTS_DIR)) == sorted(FONT_FILES), \
        "static/fonts/ must contain exactly the 10 latin-subset woff2 files"
    for name in FONT_FILES:
        path = os.path.join(FONTS_DIR, name)
        raw = open(path, "rb").read()
        assert raw[:4] == b"wOF2", f"{name} is not woff2 (magic bytes)"
        assert len(raw) < 120 * 1024, f"{name} is {len(raw)} bytes (>= 120KB)"


def test_csp_serves_fonts_from_self_and_drops_google_fonts():
    assert "font-src 'self'" in security.CSP
    assert "fonts.gstatic.com" not in security.CSP
    assert "fonts.googleapis.com" not in security.CSP
    assert "style-src 'self' 'unsafe-inline';" in security.CSP


def test_sw_precaches_fonts_and_server_answers_them_immutable(client):
    token = _token()
    sw = open(os.path.join(ROOT, "sw.js"), encoding="utf-8").read()
    for name in FONT_FILES:
        ref = f"./static/fonts/{name}?v={token}"
        assert ref in sw, f"sw.js CORE missing {ref}"
        response = client.get(f"/static/fonts/{name}?v={token}")
        assert response.status_code == 200, f"/static/fonts/{name} -> 404"
        cache_control = response.headers.get("Cache-Control", "")
        assert "max-age=31536000" in cache_control and "immutable" in cache_control, \
            f"/static/fonts/{name} serves Cache-Control={cache_control!r}; " \
            "fonts must be immutable for the life of the token"
