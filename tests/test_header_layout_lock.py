"""Header layout LOCK: logo LEFT, one flex row, small fixed currency pills, butterflies.

The owner's header (reference screenshot, 2026-09-10; restored 2026-09-11) is
ONE horizontal row:  LOGO LEFT | nav links | EN/FR + ₦/F CFA switches +
search + bag + menu, evenly spaced via
``display: flex; flex-direction: row; align-items: center;
justify-content: space-between`` on ``.header-inner``.

Past drift that broke that design and must never come back:

  * the "centred logo" grid (``.header-inner { grid-template-columns: 1fr auto 1fr }``
    plus ``justify-self: center`` on the logo), which scattered the controls
    off the owner's single left-anchored row,
  * the ≤380px fold that pushed the logo and switches onto two centred rows,
  * any non-zero ``order`` on ``.header .logo``,
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


# ------------------------------------------------------------- one flex row

def test_header_inner_is_a_flex_space_between_row():
    """The base header container must stay exactly what the owner pinned:
    display:flex, one row, vertically centred, space-between."""
    top_rules = [r for r in _all_rules() if r[0] is None]
    display = _last_setting(top_rules, ".header-inner", "display")
    assert display and display[1] == "flex", (
        f".header-inner display must stay flex, got {display!r}")
    direction = _last_setting(top_rules, ".header-inner", "flex-direction")
    assert direction and direction[1] == "row", (
        f".header-inner flex-direction must stay row, got {direction!r}")
    align = _last_setting(top_rules, ".header-inner", "align-items")
    assert align and align[1] == "center", (
        f".header-inner align-items must stay center, got {align!r}")
    justify = _last_setting(top_rules, ".header-inner", "justify-content")
    assert justify and justify[1] == "space-between", (
        f".header-inner justify-content must stay space-between, got {justify!r}")
    wrap = _last_setting(top_rules, ".header-inner", "flex-wrap")
    assert wrap and wrap[1] == "nowrap", (
        f".header-inner flex-wrap must stay nowrap (one row), got {wrap!r}")


def test_logo_is_the_first_flex_item_and_link_nav_stays_between():
    """DOM order inside .header-inner: logo first (left), then the nav links,
    then the controls - so `space-between` paints logo | links | controls."""
    src = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    logo = src.index('<a class="logo" href="index.html">')
    nav = src.index('<nav class="nav-left">')
    right = src.index('<div class="nav-right">')
    assert logo < nav < right, "header template must be: logo, nav-left, nav-right"
    # The butterflies stay in the header, exactly as the owner left them.
    assert '<div class="header-flies" aria-hidden="true">' in src
    assert src.count('class="hfly') == 2


def test_no_rule_ever_grids_or_centres_the_header_row():
    """grid-template-columns / justify-self:center on the header row or the
    logo was the scattered/centred layout the owner reverted. It must never
    reappear - in any media query, under any spelling."""
    for media, selector, props in _all_rules():
        parts = [p.strip() for p in selector.split(",")]
        if any(".header-inner" in p for p in parts):
            assert "grid-template-columns" not in props, (
                f"{selector} sets grid-template-columns (@media {media}) - "
                "the header row must stay a flex row")
            display = props.get("display")
            assert display != "grid", (
                f"{selector} sets display:grid (@media {media}) - "
                "the header row must stay flex")
        if any(p == ".header .logo" for p in parts):
            order = props.get("order")
            if order is not None:
                assert order == "0", (
                    f".header .logo order must be 0 (source order), got {order!r}")
            justify = props.get("justify-self")
            assert justify != "center", (
                f".header .logo justify-self:center found (@media {media}) - "
                "that is the centred-logo regression")
            assert justify != "end", (
                f".header .logo justify-self:end found (@media {media}) - "
                "the logo must stay at the left edge")


def test_desktop_block_locks_the_flex_row():
    """The >=981px lock at the end of style.css: flex row + space-between,
    logo order 0 - every key declaration !important so no later theme rule
    can out-specify it."""
    blocks = _media_blocks(_css(), "@media (min-width: 981px)")
    assert blocks, "the >=981px desktop header lock is missing"
    row = None
    logo = None
    for body in blocks:
        for selector, decl in _blocks(body):
            if selector.strip() == ".header-inner":
                row = _props(decl)
                row_raw = decl
            if selector.strip() == ".header .logo":
                logo = _props(decl)
    assert row, ">=981px .header-inner rule is missing"
    assert row.get("display") == "flex", ">=981px .header-inner must stay display:flex"
    assert row.get("flex-direction") == "row"
    assert row.get("align-items") == "center"
    assert row.get("justify-content") == "space-between"
    assert row.get("flex-wrap") == "nowrap"
    assert "!important" in row_raw, "the >=981px flex row lock must be !important"
    assert logo, ">=981px .header .logo rule is missing"
    assert logo.get("order") == "0"


# ------------------------------------------------------------ phone widths

def test_small_phones_keep_the_single_row():
    """<=380px: the old code folded the header into two centred rows and
    scattered the controls - the owner wants ONE row everywhere, so the
    <=380px block must shrink the controls instead of re-gridding the row."""
    blocks = _media_blocks(_css(), "@media (max-width: 380px)")
    assert blocks, "the <=380px one-row budget block is missing"
    icons = None
    logo = None
    for body in blocks:
        for selector, decl in _blocks(body):
            sel = selector.strip()
            props = _props(decl)
            assert not (".header-inner" in sel and "grid-template-columns" in props), (
                "<=380px must NOT re-grid the header row - one row everywhere")
            if sel == ".icon-btn":
                icons = props
            if sel == ".logo img":
                logo = props
    assert icons and icons.get("width") == "28px", (
        "<=380px icon buttons must shrink to 28px to keep the one-row budget")
    assert logo and logo.get("max-width") == "60px", (
        "<=380px logo must cap at 60px to keep the one-row budget")


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
