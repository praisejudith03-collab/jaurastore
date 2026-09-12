"""Admin mobile layout + mobile header fit (css/style.css source pins).

Two phone regressions, fixed in CSS:

3. Mobile admin: the bottom tab bar (.admin-app-nav) is PINNED to the
   bottom of the screen at every width (owner directive 2026-09-12) and
   styled with the storefront dock's tokens. Two behaviours keep a fixed
   bar safe on a phone: while the keyboard is open (focus inside a field)
   the dock and the "More" sheet slide out of view (body.admin-kb-open),
   and the page body reserves the dock's height so no control sits under
   it. The shop #site-header stays hidden on the admin page; admin inputs
   are >=16px so iOS never auto-zooms; the safe-area inset is honoured.

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


def _first_rule(text, selector):
    """Declarations of the FIRST rule whose selector matches exactly (the
    base token rule, before later cascade overrides specialise it)."""
    css = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if re.sub(r"\s+", " ", match.group(1)).strip() == selector:
            return match.group(2).lower()
    return ""


def _prop(decl, name):
    m = re.search(re.escape(name) + r"\s*:\s*([^;]+);", decl)
    return (m.group(1) or "").strip().replace("!important", "").strip() if m else ""


# ------------------------------------------------------------- issue 3: admin
def test_admin_page_hides_the_shop_header():
    css = _css()
    assert re.search(
        r'body\[data-page="admin"\]\s+#site-header\s*\{\s*display:\s*none',
        css), "the shop #site-header must be hidden on the admin page"


def test_admin_dock_is_pinned_to_the_bottom_of_the_screen():
    """Owner directive 2026-09-12: the admin dock is position: fixed at the
    bottom of the screen — the storefront dock's thumb-friendly shape."""
    css = _css()
    rule = _rule(css, ".admin-app-nav")
    assert _prop(rule, "position") == "fixed", \
        "the admin dock must be pinned with position: fixed"
    assert _prop(rule, "bottom") == "0"
    assert _prop(rule, "left") == "0" and _prop(rule, "right") == "0"
    assert _prop(rule, "z-index"), "the dock needs a z-index above content"


def test_no_media_query_unpins_or_hides_the_admin_dock():
    """The dock is pinned at EVERY width: no media block may set it back to
    static/absolute or hide it (the pre-2026-09-12 build hid it >=921px)."""
    css = _css()
    assert not re.search(r"\.admin-app-nav\s*\{[^}]*display\s*:\s*none", css), \
        "the admin dock must never be display: none"
    for query in ("@media (max-width: 920px)", "@media (min-width: 921px)",
                  "@media (max-width: 640px)"):
        for body in _media_blocks(css, query):
            rule = _rule(body, ".admin-app-nav")
            assert _prop(rule, "position") in ("", "fixed"), \
                f"{query} must not unpin the admin dock"
            assert _prop(rule, "display") != "none"


def test_admin_dock_adopts_the_storefront_dock_tokens():
    """Pinned AND identical to the storefront bottom dock: cream card, warm
    border, equal-width icon-over-label targets."""
    css = _css()
    rule = _rule(css, ".admin-app-nav")
    assert _prop(rule, "background") == "#fcf8f4"
    assert "border-top" in rule
    assert _prop(rule, "grid-template-columns").startswith("repeat(6")
    btn = _first_rule(css, ".admin-app-nav button")
    assert _prop(btn, "display") == "flex"
    assert _prop(btn, "flex-direction") == "column"
    assert int(_prop(btn, "min-height").rstrip("px") or 0) >= 44, \
        "dock targets must stay thumb-friendly (>=44px)"


def test_admin_dock_honours_the_safe_area():
    css = _css()
    rule = _rule(css, ".admin-app-nav")
    assert "env(safe-area-inset-bottom)" in rule, \
        "the pinned dock must keep the safe-area inset padding"


def test_admin_keyboard_open_slides_the_dock_away():
    """A pinned bar must never cover the field being typed in: while the
    keyboard is open (body.admin-kb-open, set by js/admin.js on focusin)
    both the dock and the More sheet slide out of view."""
    css = re.sub(r"/\*.*?\*/", " ", _css(), flags=re.S)
    m = re.search(r"body\.admin-kb-open[^{;]*\.admin-app-nav[^{]*\{([^}]*)\}", css)
    assert m and "translatey" in m.group(1).lower(), \
        "the dock must slide away while the phone keyboard is open"
    m = re.search(r"body\.admin-kb-open[^{;]*\.admin-more-sheet[^{]*\{([^}]*)\}", css)
    assert m and "translatey" in m.group(1).lower(), \
        "the More sheet must slide away too"


def test_admin_more_sheet_starts_hidden_and_sits_above_the_dock():
    css = _css()
    assert re.search(r"\.admin-more-sheet\[hidden\]\s*\{\s*display:\s*none", css), \
        "the sheet ships hidden and display:none keeps it that way"
    rule = _first_rule(css, ".admin-more-sheet")
    assert _prop(rule, "position") == "fixed"
    assert _prop(rule, "bottom").startswith("calc("), \
        "the sheet anchors just above the dock"


def test_admin_mobile_body_reserves_the_pinned_dock_space():
    """The dock is fixed, so the page body must reserve its height inside
    every <=920px block — no control may sit underneath it."""
    css = _css()
    blocks = _media_blocks(css, "@media (max-width: 920px)")
    assert blocks, "expected an admin mobile media block"
    ok = False
    for body in blocks:
        rule = _rule(body, 'body[data-page="admin"]')
        pad = _prop(rule, "padding-bottom")
        m = re.match(r"calc\((\d+)px", pad)
        if pad and (m and int(m.group(1)) >= 70):
            ok = True
    assert ok, "the mobile admin body must reserve >=70px (+safe-area) for the dock"


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
    pins those metrics (later rules win the cascade). The 2026-09-10 size
    reduction keeps every band: 12px labels, 36px tap height, 0 8px padding."""
    css = _css()
    rule = ""
    for body in _media_blocks(css, "@media (max-width: 640px)"):
        candidate = _rule(body, ".lang-switch button, .currency-switch button, "
                                ".lang-switch button.is-on, "
                                ".currency-switch button.is-on")
        if _prop(candidate, "height"):
            rule = candidate          # keep the LAST one: the cascade winner
    assert _prop(rule, "font-size").startswith("12px")
    assert _prop(rule, "height").startswith("36px"), \
        "36px tap height is within the 36-44px requirement"
    assert re.match(r"0\s+8px", _prop(rule, "padding")), \
        "8-12px horizontal padding on a fixed-height button"
