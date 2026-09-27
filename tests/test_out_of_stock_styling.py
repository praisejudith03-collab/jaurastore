"""A sold-out piece must LOOK sold out (owner request 2026-09-27).

The shop paints its "Add to cart" buttons white/cream, and the pale grey
out-of-stock rule near the top of css/style.css was out-specified by the
later !important skins - so an unavailable product kept the exact same
button as a buyable one and shoppers went on tapping a dead control.

What is pinned here:

  * the stylesheet owns a single out-of-stock red (--oos / --oos-deep /
    --oos-soft) and it really is a red, not another cream;
  * every control that cannot be bought resolves to that red with white
    text - the card + most-viewed mini buttons, the product page's
    "Add to cart", and their hover states - and the rule wins the cascade
    (it is last in the file and !important, like the skins it overrides);
  * the ribbon on the photo and a sold-out variant chip speak the same
    colour language;
  * the markup still ships `disabled` plus the "Out of stock" label, in
    both English and French, so the colour is not the only signal.

Run with:  python3 -m pytest tests/test_out_of_stock_styling.py -q
"""
import os
import re

from test_french_catalog import _blocks, _css, _strip_media

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _decls(decl):
    out = {}
    for item in decl.split(";"):
        if ":" not in item:
            continue
        name, _, value = item.partition(":")
        raw = value.strip().lower()
        out[name.strip().lower()] = (raw.replace("!important", "").strip(),
                                     "!important" in raw)
    return out


def _rules():
    """(selector_parts, declarations, position) outside every @media block."""
    css = _strip_media(_css())
    rules = []
    for offset, (selector, decl) in enumerate(_blocks(css)):
        parts = [re.sub(r"\s+", " ", p).strip() for p in selector.split(",")]
        rules.append((parts, _decls(decl), offset))
    return rules


def _token(name):
    m = re.search(re.escape(name) + r":\s*(#[0-9a-fA-F]{6})", _css())
    assert m, f"{name} is missing from :root"
    return m.group(1).lower()


def _hsl(hex6):
    r, g, b = (int(hex6[i:i + 2], 16) / 255 for i in (1, 3, 5))
    high, low = max(r, g, b), min(r, g, b)
    light = (high + low) / 2
    if high == low:
        return 0.0, 0.0, light
    delta = high - low
    sat = delta / (2 - high - low) if light > 0.5 else delta / (high + low)
    if high == r:
        hue = ((g - b) / delta) % 6
    elif high == g:
        hue = (b - r) / delta + 2
    else:
        hue = (r - g) / delta + 4
    return hue * 60, sat, light


def _winner(selector, prop):
    """The value a browser resolves for an element matching `selector`
    exactly, considering only rules that name it (last !important wins)."""
    normal = important = None
    for parts, props, _pos in _rules():
        if selector in parts and prop in props:
            value, is_important = props[prop]
            if is_important:
                important = value
            else:
                normal = value
    return important if important is not None else normal


# ------------------------------------------------------------- the colour

def test_the_stylesheet_owns_one_out_of_stock_red():
    for name in ("--oos", "--oos-deep", "--oos-soft"):
        hue, sat, light = _hsl(_token(name))
        assert hue <= 25 or hue >= 340, (
            f"{name} ({_token(name)}) is not a red (hue {hue:.0f})")
        assert sat >= 0.25, (
            f"{name} ({_token(name)}) is too washed out to read as a warning")
    base = _hsl(_token("--oos"))
    deep = _hsl(_token("--oos-deep"))
    soft = _hsl(_token("--oos-soft"))
    assert deep[2] < base[2] < soft[2], (
        "--oos-deep is the pressed/hover tone, --oos-soft the chip background")
    # It must not read as the shop's normal neutral button.
    assert base[2] < 0.6, "the out-of-stock fill has to be a solid, dark red"


# ------------------------------------------------- the buttons themselves

DEAD_BUTTONS = (
    ".card.is-oos .add-mini",       # shop / homepage card
    ".mv-card.is-oos .add-mini",    # "Most viewed right now" rail
    ".add-mini:disabled",           # any mini add button the JS disabled
    "[data-buy]:disabled",          # the product page's Add to cart
)


def test_every_unbuyable_button_resolves_to_the_red():
    for selector in DEAD_BUTTONS:
        background = _winner(selector, "background")
        assert background == "var(--oos)", (
            f"{selector} must be filled with the out-of-stock red, "
            f"got {background!r}")
        colour = _winner(selector, "color")
        assert colour == "#ffffff", (
            f"{selector} needs white text on the red, got {colour!r}")
        cursor = _winner(selector, "cursor")
        assert cursor == "not-allowed", (
            f"{selector} must show the not-allowed cursor, got {cursor!r}")
        opacity = _winner(selector, "opacity")
        assert opacity in (None, "1"), (
            f"{selector} must stay legible, not be faded out ({opacity!r})")


def test_the_red_is_declared_important_and_last():
    """The shop's button skins are themselves !important, so the disabled
    state has to be too - and it has to come after them in the file."""
    rules = _rules()
    oos_pos, skin_pos = [], []
    for parts, props, pos in rules:
        if "background" not in props:
            continue
        value, is_important = props["background"]
        if any(p in DEAD_BUTTONS for p in parts):
            assert is_important, (
                f"the out-of-stock fill must be !important ({parts})")
            oos_pos.append(pos)
        if ".add-mini" in parts or "[data-buy]" in parts:
            skin_pos.append(pos)
    assert oos_pos, "no out-of-stock background rule found at all"
    assert min(oos_pos) > max(skin_pos or [-1]), (
        "the out-of-stock block must sit AFTER the normal button skins, "
        "or the white background wins the cascade again")


def test_hover_does_not_light_a_dead_button_up():
    for selector in DEAD_BUTTONS:
        hover = _winner(selector + ":hover", "background")
        assert hover == "var(--oos-deep)", (
            f"{selector}:hover must stay in the red family, got {hover!r}")


def test_the_ribbon_and_the_sold_out_variant_match():
    assert _winner(".pill.oos", "background") == "var(--oos)", (
        "the OUT OF STOCK ribbon on the photo must use the same red")
    assert _winner(".opt-chip.is-oos", "border-color") == "var(--oos)", (
        "a sold-out variant chip must be outlined in the same red")
    assert _winner(".opt-chip.is-oos", "background") == "var(--oos-soft)"
    assert _winner(".opt-chip.is-oos", "cursor") == "not-allowed"


# ------------------------------------------------------------- the markup

def test_the_buttons_are_really_disabled_and_labelled():
    store = _read("js", "store.js")
    app = _read("js", "app.js")
    card = store.split("function cardHTML(", 1)[1][:2000]
    assert 'class="add-mini" ${sold ? "disabled" : ""}' in card, (
        "a sold-out card button must carry the disabled attribute")
    assert '${sold ? tx("card.oos") : tx("card.add")}' in card, (
        'a sold-out card button must read "Out of stock"')
    assert 'class="card${sold ? " is-oos" : ""}"' in card, (
        "the card needs the is-oos hook the stylesheet targets")
    assert '<button class="add-mini mv-add" ${sold ? "disabled" : ""}' in app, (
        "the most-viewed rail must disable sold-out buttons too")
    assert '<button class="btn" data-buy ${p.stock <= 0 ? "disabled" : ""}>' in app, (
        "the product page must disable Add to cart when stock is gone")
    assert 't("pdp.oos")' in app


def test_the_label_exists_in_both_languages():
    i18n = _read("js", "i18n.js")
    assert '"card.oos": "Out of stock"' in i18n
    assert '"pdp.oos": "Out of stock"' in i18n
    assert '"pdp.oos": "Rupture de stock"' in i18n
    assert '"card.oos": "Rupture"' in i18n
