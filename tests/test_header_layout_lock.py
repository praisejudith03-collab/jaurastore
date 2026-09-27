"""Header layout LOCK ("Option A", owner request 2026-09-27): hamburger LEFT,
logo CENTRED on one grid row, search + cart RIGHT, and NO language or
currency dropdowns anywhere in the header. The currency lives in a floating
white pill stacked above the WhatsApp bubble (English storefront only);
language is auto-detected from the device.

This file supersedes the 2026-09-11 "logo LEFT, one flex row, header pills"
lock: the owner ordered the Option A redesign on 2026-09-27, so the lock now
pins THE NEW design. (The old `.lang-switch`/`.currency-switch` stylesheet
rules intentionally stay in css/style.css as dormant styling - other tests
assert their metrics - but nothing renders them any more.)

Past drift that must never come back:

  * language/currency switches rendered inside the top header or at the
    bottom of the slide-out menu (removed on purpose and guarded here),
  * a header row that stops being `grid-template-columns: 1fr auto 1fr` or
    lets the logo leave the horizontal centre,
  * the floating pill losing its pinned geometry (bottom:85px right:20px
    z-index:9999) or its white/lavender/violet styling,
  * the WhatsApp bubble drifting from bottom:20px right:20px z-index:9998,
  * the two golden butterflies (.header-flies/.hfly1/.hfly2) being removed
    or stopped - the owner wants the animation left exactly the way it is.

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


def _store_js():
    with open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8") as fh:
        return fh.read()


def _header_template(src):
    start = src.index('<header class="header" data-header>')
    end = src.index("</header>", start)
    return src[start:end]


# ------------------------------------------------------- one centered grid row

def test_header_inner_is_a_centered_grid_row():
    """The Option A header row: a 1fr auto 1fr grid at every width, items
    vertically centred - this is what keeps the logo dead-centre no matter
    how wide the menu/search/cart controls are."""
    top_rules = [r for r in _all_rules() if r[0] is None]
    display = _last_setting(top_rules, ".header-inner", "display")
    assert display and display[1] == "grid", (
        f".header-inner display must stay grid (Option A), got {display!r}")
    cols = _last_setting(top_rules, ".header-inner", "grid-template-columns")
    assert cols and cols[1] == "1fr auto 1fr", (
        f".header-inner must stay grid-template-columns: 1fr auto 1fr, got {cols!r}")
    align = _last_setting(top_rules, ".header-inner", "align-items")
    assert align and align[1] == "center", (
        f".header-inner align-items must stay center, got {align!r}")


def test_header_template_is_menu_left_logo_centre_search_cart_right():
    """DOM order inside .header-inner: the hamburger slot first (left), then
    the logo (centre auto column), then the search + cart slot (right)."""
    src = _store_js()
    tpl = _header_template(src)
    menu = tpl.index('data-open-menu')          # the only [≡] in the header
    logo = tpl.index('<a class="logo" href="index.html">')
    right = tpl.index('<div class="header-slot nav-right">')
    assert menu < logo < right, (
        "header template must be: menu slot, logo, then nav-right (menu left, "
        "logo centred, search+cart right)")
    assert '<div class="header-slot header-slot--left">' in tpl
    assert 'data-open-search' in tpl and 'data-cart-icon' in tpl
    # The butterflies stay in the header, exactly as the owner left them.
    assert '<div class="header-flies" aria-hidden="true">' in tpl
    assert tpl.count('class="hfly') == 2


def test_no_language_or_currency_switches_in_the_header_or_menu():
    """Removed on owner request 2026-09-27: the old EN|FR and ₦|F CFA
    dropdowns never come back to the top header, and the static twins at the
    bottom of the slide-out menu stay gone."""
    src = _store_js()
    tpl = _header_template(src)
    assert ".lang-switch" not in tpl and ".currency-switch" not in tpl, (
        "the header must not carry language/currency switches any more")
    assert 'data-lang' not in tpl, "no [data-lang] buttons in the header"
    assert 'data-cur="' not in tpl, "no [data-cur] buttons in the header"
    menu_start = src.index('<nav class="mobile-nav au-menu" data-mobile>')
    menu_end = src.index("</nav>", menu_start)
    menu = src[menu_start:menu_end]
    assert "au-menu-tools" not in menu, (
        "the EN|FR and ₦|F CFA toggles at the bottom of the slide-out menu "
        "were removed and must not return")
    assert 'data-lang' not in menu and 'data-cur="' not in menu


def test_the_floating_pill_is_the_only_currency_switch_left():
    """The ONE remaining storefront currency control is the floating pill:
    a [data-cur-float] group with ₦ / F CFA buttons riding the shared
    [data-cur] handler, mounted from footerHTML next to the WhatsApp bubble.
    The desktop>=981px block and the phone budget blocks must keep their
    invisible-to-shoppers geometry, which this also guards."""
    src = _store_js()
    pill = src.index('<div class="cur-float" data-cur-float')
    wa = src.index('<a class="wa-float"')
    assert pill < wa, "the currency pill mounts stacked above WhatsApp"
    block = src[pill:pill + 500]
    assert 'data-cur="NGN"' in block and 'data-cur="CFA"' in block


def test_desktop_block_locks_the_centered_grid_row():
    """The >=981px lock at the end of style.css pins Option A: grid row,
    centred logo - every key declaration !important so no later theme rule
    can out-specify it."""
    blocks = _media_blocks(_css(), "@media (min-width: 981px)")
    assert blocks, "the >=981px desktop header lock is missing"
    row = None
    logo = None
    for body in blocks:
        for selector, decl in _blocks(body):
            if selector.strip() == ".header-inner":
                if "grid-template-columns" in _props(decl):
                    row = _props(decl)
                    row_raw = decl
            if selector.strip() == ".header .logo":
                if "justify-self" in _props(decl):
                    logo = _props(decl)
    assert row, ">=981px .header-inner grid lock is missing"
    assert row.get("display") == "grid", ">=981px .header-inner must stay display:grid"
    assert row.get("grid-template-columns") == "1fr auto 1fr"
    assert row.get("align-items") == "center"
    assert "!important" in row_raw, "the >=981px grid row lock must be !important"
    assert logo, ">=981px .header .logo centring rule is missing"
    assert logo.get("justify-self") == "center", (
        "the desktop logo must stay centred (justify-self: center)")


def test_the_logo_is_never_de_centred_again():
    """No rule in any media query may hand the logo a left/right justify or
    an order that drags it out of the centre column."""
    for media, selector, props in _all_rules():
        parts = [p.strip() for p in selector.split(",")]
        if any(p == ".header .logo" or p == ".header-inner > .logo" for p in parts):
            justify = props.get("justify-self")
            if justify is not None:
                assert justify == "center", (
                    f"{selector} sets justify-self:{justify} (@media {media}) - "
                    "the logo must stay centred")
            order = props.get("order")
            assert order is None or order == "0", (
                f"{selector} sets order:{order} (@media {media}) - source order only")


# ------------------------------------------------------------ phone widths

def test_small_phones_keep_the_single_row():
    """<=380px: shrink the controls, never re-grid or fold the row."""
    blocks = _media_blocks(_css(), "@media (max-width: 380px)")
    assert blocks, "the <=380px one-row budget block is missing"
    icons = None
    logo = None
    for body in blocks:
        for selector, decl in _blocks(body):
            sel = selector.strip()
            if sel == ".icon-btn":
                icons = _props(decl)
            if sel == ".logo img":
                logo = _props(decl)
    assert icons and icons.get("width") == "28px", (
        "<=380px icon buttons must shrink to 28px to keep the one-row budget")
    assert logo and logo.get("max-width") == "60px", (
        "<=380px logo must cap at 60px to keep the one-row budget")


def test_normal_phones_keep_the_single_row():
    """381-480px phones keep one row: 84px logo + 4px gap on the controls."""
    blocks = _media_blocks(_css(), "@media (max-width: 480px)")
    logo = None; gap = None
    for body in blocks:
        for selector, decl in _blocks(body):
            if selector.strip() == ".logo img": logo = _props(decl)
            if selector.strip() == ".nav-right": gap = decl
    assert logo and logo.get("max-width") == "84px"
    assert gap and "!important" in gap and "4px" in gap


# --------------------------------------------------------- floating pill pins

def test_currency_pill_position_and_palette_are_pinned():
    """Owner spec 2026-09-27: fixed bottom-right, stacked directly above the
    WhatsApp bubble - bottom:85px right:20px z-index:9999 - a pure-white
    pill (#FFFFFF) with a thin lavender border (#D8B4FE), soft shadow, and
    the active currency bold white on vibrant purple (#7C3AED)."""
    top_rules = [r for r in _all_rules() if r[0] is None]
    pos = _last_setting(top_rules, ".cur-float", "position")
    assert pos and pos[1] == "fixed", ".cur-float must stay position:fixed"
    bottom = _last_setting(top_rules, ".cur-float", "bottom")
    # The value is wrapped for phone safe-areas — calc(150px + env(...)) —
    # so the pixel figure is searched for, not anchored to the start.
    assert bottom and int(re.search(r"(\d+)", bottom[1]).group(1)) >= 130
    assert "env(safe-area-inset-bottom" in bottom[1]
    right = _last_setting(top_rules, ".cur-float", "right")
    assert right and right[1].startswith("20px"), (
        f".cur-float must stay right:20px, got {right!r}")
    z = _last_setting(top_rules, ".cur-float", "z-index")
    assert z and z[1] == "9999", f".cur-float must stay z-index:9999, got {z!r}"
    bg = _last_setting(top_rules, ".cur-float", "background")
    assert bg and bg[1] == "#ffffff", ".cur-float must stay a pure-white pill"
    border = _last_setting(top_rules, ".cur-float", "border")
    assert border and "#d8b4fe" in border[1], (
        ".cur-float must keep its thin lavender border (#D8B4FE)")
    shadow = _last_setting(top_rules, ".cur-float", "box-shadow")
    assert shadow and "rgba(" in shadow[1], ".cur-float must keep its soft shadow"
    on = _last_setting(top_rules, ".cur-float button.is-on", "background")
    assert on and on[1] == "#7c3aed", (
        "the active currency is filled vibrant purple (#7C3AED)")
    on_color = _last_setting(top_rules, ".cur-float button.is-on", "color")
    assert on_color and on_color[1] == "#ffffff", "active currency text stays white"
    on_weight = _last_setting(top_rules, ".cur-float button.is-on", "font-weight")
    assert on_weight and on_weight[1] in ("700", "bold"), "active currency stays bold"


def test_currency_pill_hides_when_french_locks_fcfa():
    """French interface = FCFA locked = no pill. Both gates stay in place:
    the hidden attribute rule and the French body class rule."""
    top_rules = [r for r in _all_rules() if r[0] is None]
    found = []
    for media, sel, props in top_rules:
        if "display" in props and ("cur-float" in sel):
            found.append((sel, props["display"]))
    hides = [s for s, v in found if v.startswith("none")]
    assert any("hidden" in s for s in hides), ".cur-float[hidden] must display:none"
    assert any("ja-fr" in s for s in hides), "body.ja-fr .cur-float must display:none"
    assert any('data-page="admin"' in s for s in hides), (
        "the admin page keeps the floating pill hidden like the WhatsApp bubble")


def test_whatsapp_bubble_position_is_pinned_under_the_pill():
    """WhatsApp stays fixed at bottom:20px right:20px z-index:9998 - directly
    under the currency pill and nowhere else."""
    top_rules = [r for r in _all_rules() if r[0] is None]
    found = None
    for media, sel, props in top_rules:
        parts = [p.strip() for p in sel.split(",")]
        if all(p.endswith(".wa-float") for p in parts) and "bottom" in props:
            found = props
    assert found, "no .wa-float rule sets bottom"
    # Wrapped for phone safe-areas — calc(84px + env(...)) — search, don't anchor.
    assert int(re.search(r"(\d+)", found["bottom"]).group(1)) >= 72
    assert "env(safe-area-inset-bottom" in found["bottom"]
    assert found["right"].startswith("20px"), (
        f"wa-float must stay right:20px, got {found['right']!r}")
    assert found["z-index"] == "9998", (
        f"wa-float must stay z-index:9998, got {found['z-index']!r}")


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
