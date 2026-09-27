"""The compact currency pill, the cart-drawer collision, and "Naira first".

Owner request (2026-09-27), three complaints about the floating ₦ / F CFA
pill and one about what English shoppers see first:

  * the pill looked stretched and too long - it is now a small, fine badge
    (10px labels, 5px side padding, a 2px shell) whose two currencies own
    fixed minimum widths, so tapping it can never resize or reflow it;
  * the pill (z-index 9999) and the WhatsApp bubble (9998) floated ABOVE the
    slide-out bag (5000) and sat on top of its "View bag" / "Checkout"
    buttons - while the pane is open both now drop below the drawer and fade
    smoothly away (CSS), and are taken out of the accessibility tree (JS);
  * English must open in ₦ Naira STRICTLY: a stale "CFA" left in
    localStorage by an older visit - or by the French storefront, which
    forces CFA - no longer paints an English shopper's first page in F CFA.
    A manual tap on the pill still holds for the rest of the visit.

The runtime half boots the real js/i18n.js + js/store.js across several
simulated page loads in tests/_naira_first_cart_pill_sim.mjs. The static
half is pinned here so a future edit cannot quietly undo the wiring.

Run with:  python3 -m pytest tests/test_naira_first_and_cart_pill.py -q
"""
import os
import re
import shutil
import subprocess

import pytest

from test_french_catalog import _blocks, _css, _media_blocks, _props, _strip_media

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(ROOT, "tests", "_naira_first_cart_pill_sim.mjs")


def _store_js():
    with open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8") as fh:
        return fh.read()


def _decls(decl):
    """{property: (value, is_important)} - !important decides the cascade."""
    out = {}
    for item in decl.split(";"):
        if ":" not in item:
            continue
        name, _, value = item.partition(":")
        raw = value.strip().lower()
        out[name.strip().lower()] = (raw.replace("!important", "").strip(),
                                     "!important" in raw)
    return out


def _top_rules():
    """(selector, declarations, position) for every rule outside @media."""
    css = _strip_media(_css())
    rules = []
    for offset, (selector, decl) in enumerate(_blocks(css)):
        rules.append((re.sub(r"\s+", " ", selector).strip(), _decls(decl), offset))
    return rules


def _last(rules, selector, prop):
    """What the browser actually resolves for an exact selector match:
    the last !important declaration, or the last normal one."""
    normal = important = None
    for sel, props, _pos in rules:
        if sel == selector and prop in props:
            value, is_important = props[prop]
            if is_important:
                important = value
            else:
                normal = value
    return important if important is not None else normal


def _px(value):
    m = re.match(r"^(-?[\d.]+)px$", str(value or "").strip())
    return float(m.group(1)) if m else None


def _px_in(value):
    """The first pixel figure inside a value, plain ("18px") or wrapped for
    phone safe-areas ("calc(84px + env(safe-area-inset-bottom, 0px))")."""
    raw = value[0] if isinstance(value, tuple) else value
    m = re.search(r"(-?[\d.]+)px", str(raw or ""))
    return float(m.group(1)) if m else None


def _rules_touching(rules, needle):
    return [(sel, props, pos) for sel, props, pos in rules if needle in sel]


# ------------------------------------------------------------ runtime proof

def test_naira_first_and_cart_pill_simulation_passes():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (frontend sim needs Node >= 18)")
    proc = subprocess.run([node, SIM], cwd=ROOT, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, (
        "Naira-first / cart-pill simulation failed:\n" + proc.stdout + proc.stderr)
    assert "all Naira-first and cart-pill checks passed" in proc.stdout


# --------------------------------------------------------- 1. compact pill

def test_the_currency_pill_is_compact():
    """Smaller type, tighter padding, a thin shell - and a hard width budget.

    The numbers are asserted, not the adjective: the reserved width is the
    two fixed button widths + the gap + the shell's padding and border, and
    it has to stay well under the ~107px the stretched version took.
    """
    rules = _top_rules()
    shell_pad = _px(_last(rules, ".cur-float", "padding"))
    assert shell_pad is not None and shell_pad <= 2, (
        f".cur-float padding must stay <= 2px (compact shell), got {shell_pad!r}")
    gap = _px(_last(rules, ".cur-float", "gap"))
    assert gap is not None and gap <= 2

    font = _px(_last(rules, ".cur-float button", "font-size"))
    assert font is not None and font <= 11, (
        f"the pill labels must stay <= 11px, got {font!r}")
    padding = str(_last(rules, ".cur-float button", "padding") or "")
    parts = [_px(p) for p in padding.split()]
    assert len(parts) == 2 and parts[1] is not None and parts[1] <= 8, (
        f"the pill buttons must keep <= 8px of side padding, got {padding!r}")

    ngn = _px(_last(rules, '.cur-float button[data-cur="NGN"]', "min-width"))
    cfa = _px(_last(rules, '.cur-float button[data-cur="CFA"]', "min-width"))
    assert ngn and cfa, (
        "each currency needs its own fixed min-width so the bold active "
        "state cannot resize the pill")
    assert ngn < cfa, "₦ is a narrower label than F CFA"
    reserved = ngn + cfa + gap + shell_pad * 2 + 2   # + the 1px border either side
    assert reserved <= 100, (
        f"the pill now reserves {reserved}px - it must stay under 100px "
        "(it used to read as a long stretched bar)")


def test_the_pill_keeps_its_pinned_corner():
    """Compact, but not moved: the owner's geometry is unchanged."""
    rules = _top_rules()
    assert str(_last(rules, ".cur-float", "right")).startswith("20px")
    assert _last(rules, ".cur-float", "z-index") == "9999"
    assert _last(rules, ".cur-float", "background") == "#ffffff"


# ------------------------------------------- 2. the slide-out bag collision

def test_the_pill_and_bubble_drop_behind_the_open_cart_pane():
    """While the bag is open nothing floats over "View bag" / "Checkout"."""
    rules = _top_rules()
    drawer_z = int(float(_last(rules, ".mini-cart", "z-index")))
    mask_z = int(float(_last(rules, ".mini-mask", "z-index")))
    assert drawer_z >= 5000 and mask_z >= 4999, (
        f"the cart pane/mask stack changed: drawer {drawer_z}, mask {mask_z}")

    for floater in (".cur-float", ".wa-float"):
        hits = [(sel, props) for sel, props, _pos in rules
                if "mini-open" in sel and floater in sel and "opacity" in props]
        assert hits, f"no body.mini-open rule moves {floater} out of the way"
        sel, props = hits[-1]
        z, z_important = props.get("z-index", (None, False))
        assert z and int(float(z)) < mask_z, (
            f"{floater} must drop BELOW the cart mask while the bag is open, "
            f"got z-index {z!r}")
        assert z_important, (
            f"{floater}'s pinned z-index is !important, so the override must "
            "be too")
        assert props.get("opacity", (None,))[0] == "0", f"{floater} must fade out ({sel})"
        assert props.get("visibility", (None,))[0] == "hidden", (
            f"{floater} must leave the tab order while the bag is open ({sel})")
        assert props.get("pointer-events", (None,))[0] == "none", (
            f"{floater} must not swallow taps meant for the cart ({sel})")


def test_the_hide_is_smooth_not_a_jump():
    """"hide smoothly" - the pill animates instead of blinking out."""
    rules = _top_rules()
    transition = _last(rules, ".cur-float", "transition") or ""
    assert "opacity" in transition, (
        "the floating pill needs an opacity transition so it fades rather "
        f"than blinks, got {transition!r}")
    assert "prefers-reduced-motion" in _css(), (
        "the reduced-motion escape hatch must stay in the stylesheet")


def test_store_js_pushes_the_floats_behind_the_open_bag():
    """The CSS hook is driven by the bag itself, and mirrored for a11y."""
    src = _store_js()
    assert "function floatsBehindCart(" in src, (
        "the helper that parks the floating chrome behind the cart is missing")
    helper = src.split("function floatsBehindCart(", 1)[1].split("\n  }", 1)[0]
    assert '".cur-float, .wa-float"' in helper, (
        "both floating controls must be handled")
    assert '"is-behind-cart"' in helper, "the CSS hook class is missing"
    assert 'aria-hidden' in helper, (
        "a covered control must also leave the accessibility tree")

    open_body = src.split("function openMini()", 1)[1].split("}", 1)[0]
    close_body = src.split("function closeMini()", 1)[1].split("}", 1)[0]
    assert "floatsBehindCart(true)" in open_body, (
        "opening the bag must push the floating chrome behind it")
    assert "floatsBehindCart(false)" in close_body, (
        "closing the bag must bring the floating chrome back")


# ------------------------------------------------------- 3. Naira first

def test_english_always_opens_in_naira():
    """A stored CFA (an old visit, or the French lock) may not win a fresh
    English page load - only a tap inside the current visit may."""
    src = _store_js()
    body = src.split("function currency()", 1)[1][:400]
    assert 'currencyLocked()) return "CFA"' in body, "the French lock comes first"
    assert 'if (!manualCurrency()) return "NGN"' in body, (
        "without a manual choice in THIS visit, English opens in Naira")
    assert 'localStorage.getItem(KEYS.currency) || "NGN"' in body, (
        "the manual choice still resolves through the stored currency")

    manual = src.split("function manualCurrency()", 1)[1][:400]
    assert "sessionStorage.getItem(KEYS.currencyManual)" in manual, (
        "the manual choice is scoped to the visit (sessionStorage)")

    remember = src.split("function rememberManualCurrency(", 1)[1][:600]
    assert "if (currencyLocked())" in remember and "removeItem" in remember, (
        "the French FCFA lock must never be recorded as a manual choice")
    assert "sessionStorage.setItem(KEYS.currencyManual" in remember

    keys = src.split("const KEYS = {", 1)[1].split("};", 1)[0]
    assert 'currencyManual: "jaura_currency_manual"' in keys, (
        "the per-visit marker needs its own storage key")


def test_the_pill_is_still_the_way_out_to_fcfa():
    """Naira first, but the compact toggle still switches - and is still the
    only currency control on the storefront."""
    src = _store_js()
    assert '<div class="cur-float" data-cur-float' in src
    pill = src.index('<div class="cur-float" data-cur-float')
    block = src[pill:pill + 400]
    assert 'data-cur="NGN"' in block and 'data-cur="CFA"' in block
    handler = src.split("function bindChrome()", 1)[1][:600]
    assert "setCurrency(btn.dataset.cur)" in handler, (
        "tapping the pill must still switch the shop's currency")


def test_the_floats_clear_the_bottom_dock():
    """Cascade-aware, not a raw text search: the stylesheet accumulated
    several superseded `.wa-float` blocks from earlier redesigns, so a plain
    "first match wins" regex can silently grab a dead rule instead of the
    one the browser actually applies (the LAST declaration for an exact
    selector, since every candidate carries the same !important weight)."""
    rules = _top_rules()
    desktop_pill = _px_in(_last(rules, ".cur-float", "bottom"))
    desktop_wa = _px_in(_last(rules, ".wa-float, body[data-page=\"home\"] .wa-float", "bottom"))
    assert desktop_wa >= 72 and desktop_pill >= desktop_wa + 58

    phone_css = _media_blocks(_css(), "@media (max-width: 640px)")
    assert phone_css, "the <=640px floating-control budget block is missing"
    phone_wa = phone_pill = None
    for body in phone_css:
        for selector, decl in _blocks(body):
            sel = re.sub(r"\s+", " ", selector).strip()
            props = _decls(decl)
            if "bottom" not in props:
                continue
            if sel == '.wa-float, body[data-page="home"] .wa-float':
                phone_wa = _px_in(props["bottom"][0])
            elif sel == ".cur-float":
                phone_pill = _px_in(props["bottom"][0])
    assert phone_wa is not None and phone_pill is not None, (
        "the <=640px block must set both .wa-float and .cur-float bottom offsets")
    assert phone_wa >= 76 and phone_pill >= phone_wa + 50
