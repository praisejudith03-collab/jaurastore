"""Admin mobile layout + mobile header fit (css/style.css source pins).

Two phone regressions, fixed in CSS:

3. Mobile admin: a fixed dark-blue bottom tab bar (.admin-app-nav) used to
   ride up over the form fields the moment the keyboard opened while typing.
   The bar is now an in-flow, horizontally scrollable tab strip that can
   never cover an input; the shop #site-header is hidden on the admin page;
   admin inputs are >=16px so iOS never auto-zooms; the safe-area inset is
   honoured.

4. #68 restores the mobile logo and search button. Keep those visible
   while preserving the comfortable language/currency switch sizes.

These are source assertions: they pin the shipped stylesheet, because a
phone is the only place the bugs actually show.

Run with:  python3 -m pytest tests/test_admin_mobile_css.py -q
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _css():
    with open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8") as fh:
        return fh.read()


def _block_span(css, start):
    """Index just past the brace-balanced block opening at `start`."""
    depth = 0
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(css)


def _media_blocks(css, query):
    """Bodies of every `query` @media block, in file order."""
    out = []
    for m in re.finditer(re.escape(query) + r"\s*\{", css):
        end = _block_span(css, m.end() - 1)
        out.append(css[m.end():end - 1])
    return out


def _rule(text, selector):
    """Declarations of the LAST rule whose selector matches exactly."""
    css = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    found = None
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if re.sub(r"\s+", " ", match.group(1)).strip() == selector:
            found = match.group(2)
    return (found or "").lower()


def _prop(decl, name):
    m = re.search(re.escape(name) + r"\s*:\s*([^;]+);", decl)
    return (m.group(1) or "").strip().replace("!important", "").strip() if m else ""


# ------------------------------------------------------------- issue 3: admin
def test_admin_page_hides_the_shop_header():
    css = _css()
    assert re.search(
        r'body\[data-page="admin"\]\s+#site-header\s*\{\s*display:\s*none',
        css), "the shop #site-header must be hidden on the admin page"


def test_admin_mobile_tab_bar_is_in_flow_and_scrollable():
    """The bottom tab strip must be in the document flow (position: static)
    with horizontal scrolling - a fixed bar overlays the inputs while the
    keyboard is open."""
    css = _css()
    blocks = _media_blocks(css, "@media (max-width: 920px)")
    assert blocks, "expected an admin mobile media block"
    hit = None
    for body in blocks:
        rule = _rule(body, ".admin-app-nav")
        if _prop(rule, "position") == "static":
            hit = rule
    assert hit is not None, "no admin-app-nav rule sets position: static"
    assert _prop(hit, "overflow-x") == "auto", \
        "the strip must scroll horizontally, not wrap or overlay"


def test_admin_mobile_tab_bar_honours_the_safe_area():
    css = _css()
    blocks = _media_blocks(css, "@media (max-width: 920px)")
    ok = any(
        "env(safe-area-inset-bottom)" in _rule(body, ".admin-app-nav")
        for body in blocks)
    assert ok, "the admin tab strip must keep the safe-area inset padding"


def test_admin_inputs_are_sixteen_pixels_to_stop_ios_zoom():
    css = _css()
    blocks = _media_blocks(css, "@media (max-width: 920px)")
    ok = False
    for body in blocks:
        rule = _rule(body, "body[data-page=\"admin\"] input, "
                           "body[data-page=\"admin\"] select, "
                           "body[data-page=\"admin\"] textarea")
        if _prop(rule, "font-size").startswith("16px"):
            ok = True
    assert ok, "admin inputs/selects/textareas must be >=16px on mobile"


def test_admin_mobile_body_no_longer_reserves_a_fixed_bar():
    """The 148px bottom padding only existed to clear the fixed bar; with
    the in-flow strip the content is padded normally again."""
    css = _css()
    blocks = _media_blocks(css, "@media (max-width: 920px)")
    hit = None
    for body in blocks:
        rule = _rule(body, 'body[data-page="admin"]')
        if _prop(rule, "padding-bottom").startswith("24px"):
            hit = rule
    assert hit is not None, \
        "body[data-page=admin] mobile padding must no longer reserve 148px"


# ------------------------------------------------------------- issue 4: header
def test_mobile_header_keeps_logo_and_search_visible():
    blocks = _media_blocks(_css(), "@media (max-width: 640px)")
    assert any(_prop(_rule(body, ".header .logo"), "display") == "flex"
               for body in blocks), "#68 requires the mobile logo"
    assert any(_prop(_rule(body, ".nav-right [data-open-search]"), "display") == "grid"
               for body in blocks), "#68 requires a visible mobile search button"


def test_between_641_and_700_logo_stays_visible():
    blocks = _media_blocks(_css(), "@media (min-width: 641px) and (max-width: 700px)")
    assert any(_prop(_rule(body, ".header .logo"), "display") == "flex"
               for body in blocks)


def test_mobile_switch_buttons_keep_a_comfortable_tap_size():
    """The owner's requirement: >=12px font, 8-12px padding, 36-44px tap
    height on the language/currency switches - the last 640px rule for them
    pins those metrics (later rules win the cascade)."""
    css = _css()
    rule = ""
    for body in _media_blocks(css, "@media (max-width: 640px)"):
        candidate = _rule(body, ".lang-switch button, .currency-switch button, "
                                ".lang-switch button.is-on, "
                                ".currency-switch button.is-on")
        if _prop(candidate, "height"):
            rule = candidate          # keep the LAST one: the cascade winner
    assert _prop(rule, "font-size").startswith("12px")
    assert _prop(rule, "height").startswith("40px"), \
        "40px tap height is within the 36-44px requirement"
    assert re.match(r"0\s+10px", _prop(rule, "padding")), \
        "8-12px horizontal padding on a fixed-height button"
