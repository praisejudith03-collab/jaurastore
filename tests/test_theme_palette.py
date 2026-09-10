"""The owner's rule for the theme: the gold family never goes past 40%.

The storefront started as a greige palette - ``--gold: #b8956a`` carried only
35% saturation and the surfaces 28-31%, which read pale and washed out on a
phone. The first fix pass took the accent to 62%; the owner rejected that and
asked for 40%. That number is the middle ground: clearly warmer than the old
greige, nowhere near neon.

``tools/vivid_palette.py`` owns the policy (the hue band, the cap, the list of
semantic ambers that must stay vivid), so these tests lock the same rule into
the shipped files:

  * no gold / nude / cream colour in the stylesheet sits above the cap - a
    tolerance covers 8-bit rounding, because HSL saturation is hypersensitive
    near white, where one channel step is worth several points;
  * the accent is still gold: ``--gold`` did not drift back towards grey;
  * the decorative gold SVGs in js/store.js follow the same cap;
  * the exemption list is justified - every exempt colour really is vivid.

Run with:  python3 -m pytest tests/test_theme_palette.py -q
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import vivid_palette as vp  # noqa: E402

HEX6 = re.compile(r"#[0-9a-fA-F]{6}\b")

# 8-bit rounding: the cap is hit to within a few points, never exactly.
TOLERANCE = 0.05


def _hsl(hex6):
    r, g, b = (int(hex6[i:i + 2], 16) for i in (1, 3, 5))
    h, s, l = vp.to_hsl(r, g, b)
    return h * 360, s, l


def _stylesheet():
    """The stylesheet without comments - the token block documents the colours
    it replaced, and those "was #..." values must not be read as live ones."""
    with open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8") as fh:
        return re.sub(r"/\*.*?\*/", "", fh.read(), flags=re.S)


def test_no_gold_family_colour_exceeds_the_cap():
    css = _stylesheet()
    checked = 0
    offenders = []
    for hex6 in {m.group(0).lower() for m in HEX6.finditer(css)}:
        if hex6 in vp.GOLD_CAP_EXEMPT:
            continue
        hue, sat, _ = _hsl(hex6)
        if not (vp.GOLD_BAND[0] <= hue <= vp.GOLD_BAND[1]):
            continue
        checked += 1
        if sat > vp.GOLD_SAT_CAP + TOLERANCE:
            offenders.append("%s at %.0f%% saturation" % (hex6, sat * 100))
    assert checked > 50, "the palette scan went blind: only %d colours" % checked
    assert not offenders, (
        "gold-family colours above the %.0f%% cap: %s"
        % (vp.GOLD_SAT_CAP * 100, ", ".join(sorted(offenders))))


def test_the_accent_stayed_gold_and_did_not_slip_back_to_greige():
    css = _stylesheet()
    for token in ("--gold", "--gold-deep", "--champagne"):
        m = re.search(re.escape(token) + r":\s*(#[0-9a-fA-F]{6})", css)
        assert m, "%s is missing from :root" % token
        hue, sat, _ = _hsl(m.group(1))
        assert vp.GOLD_BAND[0] <= hue <= vp.GOLD_BAND[1], \
            "%s is no longer a warm gold (hue %.0f)" % (token, hue)
        assert sat >= 0.35, \
            "%s fell back to grey (%.0f%% saturation)" % (token, sat * 100)
        assert sat <= vp.GOLD_SAT_CAP + TOLERANCE, \
            "%s is above the cap (%.0f%%)" % (token, sat * 100)


def test_the_decorative_gold_svgs_follow_the_same_cap():
    with open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8") as fh:
        js = fh.read()
    checked = 0
    for block in ("gold-bf", "foot-wavez"):
        start = js.index(block)
        chunk = js[start:js.index("</svg>", start)]
        for hex6 in set(HEX6.findall(chunk.lower())):
            hue, sat, _ = _hsl(hex6)
            # the waves are rose rather than gold, so the band starts at 0 here
            if not (0 <= hue <= vp.GOLD_BAND[1]):
                continue
            checked += 1
            assert sat <= vp.GOLD_SAT_CAP + TOLERANCE, \
                "%s in js/store.js %s is %.0f%% saturated" % (hex6, block, sat * 100)
    assert checked >= 5, "no decorative SVG colours were checked"


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
