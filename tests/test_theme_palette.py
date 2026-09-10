"""The owner's rule for the theme: only the GOLD is capped, at 40% saturation.

The storefront started as a greige palette - ``--gold: #b8956a`` carried 35%
saturation and the surfaces 28-31%, which read pale and washed out on a phone.
The bright pass fixed that; a first attempt then capped the whole gold / nude /
cream hue family at 40%, and the owner's correction was "not everything is 40%,
just the gold".

So the rule these tests lock in is about ROLES, not hues:

  * the gold accent - buttons, badges, links, stars, the gold hairlines, the
    gold motif - stays at or under the 40% ceiling and still reads as gold;
  * the surfaces that merely share the accent's hue (page, sections, cards,
    borders, blush panels, footer waves) are NOT capped: they stay bright and
    colourful, which is the whole point of the "it looks pale" fix.

``tools/vivid_palette.py`` owns the policy (``GOLD_ACCENT_BEFORE`` lists the
role colours and ``--cap-gold-accents`` applies it), so these tests hold the
shipped files to the same rule.

Run with:  python3 -m pytest tests/test_theme_palette.py -q
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import vivid_palette as vp  # noqa: E402

HEX6 = re.compile(r"#[0-9a-fA-F]{6}\b")

# 8-bit rounding: the ceiling is met to within a few points, never exactly.
TOLERANCE = 0.05

# The accent roles, as shipped (see GOLD_ACCENT_BEFORE for what they replaced).
GOLD_ACCENT_TOKENS = ("--gold", "--gold-deep", "--champagne", "--mauve")

# Surfaces that share the accent's hue and must stay brighter than a 40% cap
# would allow - the owner's "not everything is 40%" correction.
SURFACE_TOKENS = ("--ivory", "--cream", "--paper", "--line", "--blush",
                  "--blush-deep")


def _hsl(hex6):
    r, g, b = (int(hex6[i:i + 2], 16) for i in (1, 3, 5))
    h, s, l = vp.to_hsl(r, g, b)
    return h * 360, s, l


def _stylesheet():
    """The stylesheet without comments - the token block documents the colours
    it replaced, and those "was #..." values must not be read as live ones."""
    with open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8") as fh:
        return re.sub(r"/\*.*?\*/", "", fh.read(), flags=re.S)


def _token(css, name):
    m = re.search(re.escape(name) + r":\s*(#[0-9a-fA-F]{6})", css)
    assert m, "%s is missing from :root" % name
    return m.group(1)


def test_the_gold_accents_are_capped_at_forty_percent():
    css = _stylesheet()
    for token in GOLD_ACCENT_TOKENS:
        hex6 = _token(css, token)
        hue, sat, _ = _hsl(hex6)
        assert 0.35 <= sat <= vp.GOLD_SAT_CAP + TOLERANCE, \
            "%s (%s) is %.0f%% saturated; the gold accent is held at ~40%%" \
            % (token, hex6, sat * 100)
        assert vp.GOLD_BAND[0] <= hue <= vp.GOLD_BAND[1], \
            "%s is no longer a warm gold (hue %.0f)" % (token, hue)


def test_the_surfaces_were_not_capped():
    """Brightening the shop was the original request - the cap must not undo it.

    A cream surface one channel step from white reports a very high HSL
    saturation, so the bar is generous: anything at or under the gold ceiling
    means somebody capped the surfaces again.
    """
    css = _stylesheet()
    for token in SURFACE_TOKENS:
        hex6 = _token(css, token)
        _, sat, light = _hsl(hex6)
        assert sat > vp.GOLD_SAT_CAP + TOLERANCE, \
            "%s (%s) is only %.0f%% saturated - the surfaces were capped again" \
            % (token, hex6, sat * 100)
        assert light >= 0.75, \
            "%s (%s) is not bright (L %.0f%%)" % (token, hex6, light * 100)


def test_the_accent_roles_are_the_capped_ones_and_the_rest_kept_the_bright_pass():
    """Every colour the accent policy rewrites really is in the stylesheet, and
    its replacement - not the uncapped original - is what ships."""
    css = _stylesheet()
    with open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8") as fh:
        shipped = css + fh.read()
    missing, uncapped = [], []
    for before, role in vp.GOLD_ACCENT_BEFORE.items():
        rgb = tuple(int(before[i:i + 2], 16) for i in (1, 3, 5))
        after = "#%02x%02x%02x" % vp.cap_gold(rgb, vp.GOLD_BAND, vp.GOLD_SAT_CAP)
        if after not in shipped:
            missing.append("%s (%s) is not in the stylesheet" % (after, role))
        if before in shipped:
            uncapped.append("%s (%s) is still there next to its capped value"
                            % (before, role))
    assert not missing, "; ".join(missing)
    assert not uncapped, "; ".join(uncapped)


def test_the_footer_waves_kept_their_colour():
    """Rose, not gold: these were restored to the bright pass by the owner's
    correction, so a future family-wide cap must not catch them again."""
    with open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8") as fh:
        js = fh.read()
    start = js.index("foot-wavez")
    chunk = js[start:js.index("</svg>", start)]
    shades = set(HEX6.findall(chunk))
    assert len(shades) == 3, shades
    for hex6 in shades:
        _, sat, light = _hsl(hex6)
        assert sat > vp.GOLD_SAT_CAP + TOLERANCE, \
            "%s in the footer waves is only %.0f%% saturated" % (hex6, sat * 100)
        assert light > 0.60, "%s is too dark for the footer waves" % hex6


def test_the_gold_motif_is_capped():
    """The decorative butterfly is a gold accent, so it follows the ceiling."""
    with open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8") as fh:
        js = fh.read()
    start = js.index("gold-bf")
    chunk = js[start:js.index("</svg>", start)]
    shades = set(HEX6.findall(chunk))
    assert len(shades) >= 3, shades
    for hex6 in shades:
        hue, sat, _ = _hsl(hex6)
        assert vp.GOLD_BAND[0] <= hue <= vp.GOLD_BAND[1], \
            "%s in the gold motif is not a gold hue" % hex6
        assert sat <= vp.GOLD_SAT_CAP + TOLERANCE, \
            "%s in the gold motif is %.0f%% saturated" % (hex6, sat * 100)


def test_the_exempt_ambers_really_are_vivid():
    """An exemption has to be earned: these were vivid before any colour work."""
    css = _stylesheet()
    present = [hex6 for hex6 in vp.GOLD_CAP_EXEMPT
               if hex6 in css.lower() or hex6 in open(
                   os.path.join(ROOT, "js", "app.js"), encoding="utf-8").read().lower()]
    assert present, "the exemption list no longer matches anything in the theme"
    for hex6 in present:
        _, sat, _ = _hsl(hex6)
        assert sat > vp.GOLD_SAT_CAP, \
            "%s is exempt but only %.0f%% saturated" % (hex6, sat * 100)
