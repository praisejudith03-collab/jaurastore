"""Header layout LOCK: centred logo, small fixed currency pills, butterflies.

The owner's header is:  nav links | CENTRED LOGO | language/currency controls.
A past change ("HEADER + ADMIN NAV v132") dragged the logo to the far-left
edge on desktop by giving `.header .logo { order: -1 }` and swapping the
desktop grid to `auto minmax(0, 1fr) auto`. The owner asked for the centred
logo back - and asked that it can never quietly change back again. These
tests fail on exactly that drift:

  * any `order: -1` on `.header .logo` (the regression itself),
  * any desktop (>=981px) `.header-inner` grid that is not 1fr auto 1fr,
  * the logo losing `justify-self: center` on desktop,
  * the currency pills growing past their locked 30px/50px fixed widths,
  * the two golden butterflies (.header-flies/.hfly1/.hfly2) being removed or
    stopped - the owner wants the animation left exactly the way it is.

The browser-level pixel check lives in tests/test_browser_smoke.py (real
Chromium, CI) and tools/browser_smoke.py (deployed site after every push);
this file is the fast static guard that runs on every push.

Run with:  python3 -m pytest tests/test_header_layout_lock.py -q
"""
import os
import re

from test_french_catalog import (
    _at_block_span,
    _blocks,
    _css,
    _media_blocks,
    _props,
    _strip_media,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _all_rules():
    """Every (media_query_or_None, selector, props) in css/style.css, in order.

    Values come back lowercased with !important stripped (the shared _props
    normalisation), so assertions compare plain values.
    """
    css = _css()
    rules = []

    def harvest(text, media):
        for selector, decl in _blocks(text):
            rules.append((media, re.sub(r"\s+", " ", selector).strip(), _props(decl)))

    # Walk the file so each rule keeps the @media query it lives in.
    pos = 0
    for m in re.finditer(r"@media([^{]*)\{", css):
        if m.start() > pos:
            harvest(_strip_media(css[pos:m.start()]), None)
        end = _at_block_span(css, m.end() - 1)
        harvest(css[m.end():end - 1], m.group(1).strip())
        pos = end
    harvest(_strip_media(css[pos:]), None)
    return rules


def _last_setting(rules, selector, prop):
    """The last value a selector sets for a property (the cascade winner)."""
    found = None
    for media, sel, props in rules:
        if sel == selector and prop in props:
            found = (media, props[prop])
    return found


# ---------------------------------------------------------------- logo place

def test_desktop_header_grid_is_links_logo_controls():
    """The base header grid puts the logo in the CENTRE column, and the last
    TOP-LEVEL word on the grid columns says the same. (The <=640px block may
    re-shape the grid for phones; desktop keeps links | logo | controls.)"""
    winner = _last_setting(_all_rules(), ".header-inner", "grid-template-columns")
    assert winner is not None, ".header-inner never sets grid-template-columns"
    top = _last_setting([r for r in _all_rules() if r[0] is None],
                        ".header-inner", "grid-template-columns")
    assert top is not None, "no top-level .header-inner grid"
    value = top[1].replace(" ", "")
    assert value == "1frauto1fr", (
        f".header-inner grid must stay '1fr auto 1fr' (links | logo | controls), "
        f"got {value!r}")


def test_no_rule_ever_puts_the_logo_back_at_the_left_edge():
    """order:-1 on .header .logo was the v132 regression that yanked the centred
    logo to the left edge on desktop. It must never reappear - in any media
    query, under any spelling."""
    for media, selector, props in _all_rules():
        parts = [p.strip() for p in selector.split(",")]
        if any(p == ".header .logo" for p in parts):
            order = props.get("order")
            assert order != "-1", (
                f".header .logo {{ order: -1 }} found "
                f"(@media {media}) - that is the centred-logo regression")
            if order is not None:
                assert order == "0", (
                    f".header .logo order must be 0 (source order), got {order!r}")


def test_desktop_block_centres_the_logo_and_locks_the_grid():
    """The >=981px lock at the end of style.css: grid 1fr auto 1fr, logo order 0
    + justify-self center - every declaration !important so no later theme rule
    can out-specify it."""
    blocks = _media_blocks(_css(), "@media (min-width: 981px)")
    assert blocks, "the >=981px desktop header lock is missing"
    grid = None
    logo = None
    for body in blocks:
        for selector, decl in _blocks(body):
            if selector.strip() == ".header-inner":
                grid = _props(decl)
                grid_raw = decl
            if selector.strip() == ".header .logo":
                logo = _props(decl)
    assert grid and grid.get("grid-template-columns", "").replace(" ", "") == "1frauto1fr", (
        ">=981px .header-inner must be '1fr auto 1fr'")
    assert "!important" in grid_raw, "the >=981px grid must be !important"
    assert logo, ">=981px .header .logo rule is missing"
    assert logo.get("order") == "0"
    assert logo.get("justify-self") == "center"


def test_header_template_keeps_the_logo_between_links_and_controls():
    """js/store.js headerHTML paints nav-left, then the logo, then nav-right."""
    src = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    nav = src.index('<nav class="nav-left">')
    logo = src.index('<a class="logo" href="index.html">')
    right = src.index('<div class="nav-right">')
    assert nav < logo < right, "header template must be: nav-left, logo, nav-right"
    # The butterflies stay in the header, exactly as the owner left them.
    assert '<div class="header-flies" aria-hidden="true">' in src
    assert src.count('class="hfly') == 2


def test_small_phones_get_the_two_row_centred_fallback():
    """<=380px: one row cannot hold logo + EN/FR + ₦/F CFA + search + bag +
    menu, so the header folds to two centred rows instead of overlapping."""
    blocks = _media_blocks(_css(), "@media (max-width: 380px)")
    assert blocks, "the <=380px two-row phone fallback is missing"
    grid = None
    for body in blocks:
        for selector, decl in _blocks(body):
            if selector.strip() == ".header-inner":
                grid = _props(decl)
    assert grid, "<=380px .header-inner rule is missing"
    assert grid.get("grid-template-columns", "").startswith("minmax(0, 1fr)"), (
        "<=380px must fold to a single centred column (two rows)")


def test_normal_phones_keep_the_single_row_logo_first():
    """381–480px phones stay on ONE row: logo first, then the switches.
    84px logo + 4px gap (both !important where needed) is what lets
    logo + EN/FR + ₦/F CFA + search + bag + menu fit one row on 390–430px
    phones."""
    blocks = _media_blocks(_css(), "@media (max-width: 480px)")
    logo = None; gap = None
    for body in blocks:
        for selector, decl in _blocks(body):
            if selector.strip() == ".logo img": logo = _props(decl)
            if selector.strip() == ".nav-right": gap = decl
    assert logo and logo.get("max-width") == "84px"
    assert gap and "!important" in gap and "4px" in gap


# ------------------------------------------------------------------ pills

def test_currency_pills_keep_their_locked_small_widths():
    """Owner request 2026-09-10: ₦ = 30px, F CFA = 50px, EN/FR = 40px - all
    fixed per label so tapping cannot resize the header. (The <=640px block
    repeats the same per-label widths for phones.)"""
    top_rules = [r for r in _all_rules() if r[0] is None]
    ngn = _last_setting(top_rules, '.currency-switch button[data-cur="NGN"]', "min-width")
    cfa = _last_setting(top_rules, '.currency-switch button[data-cur="CFA"]', "min-width")
    lang = _last_setting(top_rules, ".lang-switch button", "min-width")
    assert ngn and ngn[1] == "30px"
    assert cfa and cfa[1] == "50px"
    assert lang and lang[1] == "40px"


def test_pill_heights_stay_fixed():
    top_rules = [r for r in _all_rules() if r[0] is None]
    height = _last_setting(top_rules, ".lang-switch, .currency-switch", "height")
    assert height and height[1] == "34px", height
    height = _last_setting(
        top_rules,
        ".lang-switch button, .currency-switch button, "
        ".lang-switch button.is-on, .currency-switch button.is-on", "height")
    assert height and height[1] == "32px", height


# ------------------------------------------------------------- butterflies

def test_header_butterflies_are_untouched():
    """'Leave the butterfly animation the way it is' - the two golden
    butterflies keep their positions, sizes and meet-in-the-middle paths."""
    top_rules = _all_rules()
    flies = _last_setting(top_rules, ".header-flies", "position")
    assert flies and flies[1] == "absolute", (
        ".header-flies must stay absolutely positioned inside the header")
    hfly1 = _last_setting(top_rules, ".hfly1", "animation")
    hfly2 = _last_setting(top_rules, ".hfly2", "animation")
    assert hfly1 and "meetleft" in hfly1[1], ".hfly1 must keep the meetLeft animation"
    assert hfly2 and "meetright" in hfly2[1], ".hfly2 must keep the meetRight animation"
    css = _css()
    assert "@keyframes meetLeft" in css and "@keyframes meetRight" in css
