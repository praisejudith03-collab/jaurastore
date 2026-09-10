"""Brighten + saturate a colour palette without changing its hues.

The storefront theme is a greige "quiet luxury" palette (gold #b8956a is only
35% saturated, the surfaces are 28-31% saturated creams), which reads pale on
phone screens in daylight - the owner's complaint. This tool rewrites the
padding-and-greige end of a stylesheet towards the same hues at a higher
saturation and a slightly higher lightness, so the theme looks vivid while
staying the same brand.

Rules (deliberately conservative):

  * near-greys (S <= 3%) keep their neutrality and only gain lightness, so
    #111 text and #eee hairlines do not suddenly turn orange;
  * near-white surfaces (L >= 92%) gain saturation but stay essentially white
    (S capped at 55%) and get brighter;
  * light tints (60% <= L < 92%) - borders, blush panels - gain the most
    saturation per point of lightness;
  * mid tones (30% <= L < 60%) - the gold accent, prices, buttons - are the
    brand: saturation up to 62%, lightness +2;
  * dark tones (L < 30%) - espresso text and the footer - stay dark (that is
    where the page's contrast comes from) but pick up some chroma.

Alpha (4- and 8-digit hex, rgba) is preserved. Pure black/white and fully
transparent colours are left alone.

This is a one-shot migration: it was run over css/style.css for the 2026-09
"the site looks pale" fix, and the hand-tuned :root block in the stylesheet is
the source of truth from then on. Running it again over an already-vivid file
just keeps pushing colours up, so treat the output as a proposal.

Usage:
    python3 tools/vivid_palette.py css/style.css            # report only
    python3 tools/vivid_palette.py css/style.css --write    # rewrite in place
    python3 tools/vivid_palette.py css/style.css --map      # hex -> hex table
"""
import argparse
import colorsys
import re
import sys
from collections import Counter

HEX_RE = re.compile(r"#([0-9a-fA-F]{3,8})\b")

# The owner's rule for the theme: THE GOLD ACCENT never carries more than 40%
# saturation. It was 35% before the "pale" fix and 62% after the first pass;
# 40% is the agreed ceiling - warmer than the old greige, nowhere near neon.
#
# "The gold" means the accent ROLE, not the hue family: buttons, badges, links,
# stars, the gold hairlines and the gold motif. The cream / nude surfaces share
# that hue and are deliberately NOT capped - the owner's correction was "not
# everything is 40%, just the gold", and the surfaces are what make the shop
# look bright. GOLD_ACCENT_BEFORE below lists the exact role colours, taken
# from the bright pass, whose replacements are the ones that ship.
GOLD_BAND = (18.0, 52.0)        # hue degrees, for the blunter family-wide pass
GOLD_SAT_CAP = 0.40
GOLD_ACCENT_BEFORE = {
    "#d79d55": "buttons, badges, --gold",
    "#99672f": "--gold-deep token",
    "#a8753e": "--gold-deep in the !important override block",
    "#dbb893": "--champagne / --mauve accents",
    "#dcb06b": "stars, dashed accents, the gold motif",
    "#d9a95e": "admin chart bars",
    "#d49d46": "referral card dashed gold",
    "#edd1b2": "pale gold dot / motif tint",
    "#c18130": "the gold motif's mid tone (js/store.js)",
}
GOLD_CAP_EXEMPT = {
    "#feba02", "#eaa800", "#926000",   # warning / medal golds, vivid already
    "#ffc411", "#fff7dc", "#6d5303",   # bootstrap amber alert triple
}
RGB_RE = re.compile(r"\brgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})"
                    r"(?:\s*,\s*([0-9.]+%?))?\s*\)")


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def boost(h, s, l):
    """Return (h, s, l) after the brighten+saturate pass.

    Saturation is only ever raised (``max``), because a colour that is already
    vivid - the WhatsApp green, the amber warning chip - must not be dulled by
    the cap that protects the greige palette from going neon.
    """
    if l <= 0.05 or l >= 0.985:
        return h, s, l                      # pure black / white stay put
    if s <= 0.03:                           # true grey: no hue is invented
        return (h, s, l + 0.02) if 0.85 <= l < 0.98 else (h, s, l)
    if l >= 0.92:                           # near-white surface
        return h, max(s, min(s * 2.0, 0.55)), min(0.98, l + 0.03)
    if l >= 0.60:                           # light tint / border / blush
        return h, max(s, min(s * 1.9, 0.62)), min(0.95, l + 0.03)
    if l >= 0.30:                           # mid tone: accents and prices
        return h, max(s, min(s * 1.8, 0.62)), l + 0.02
    return h, max(s, min(s * 1.6, 0.60)), l + 0.015  # dark text / surfaces


def to_rgb(h, s, l):
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return tuple(int(round(v * 255)) for v in (r, g, b))


def to_hsl(r, g, b):
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return h, s, l


def cap_gold(rgb, band=GOLD_BAND, cap=GOLD_SAT_CAP):
    """Same colour, saturation pulled down to `cap` when it is gold-family."""
    h, s, l = to_hsl(*rgb)
    if band[0] <= h * 360 <= band[1] and s > cap:
        return to_rgb(h, cap, l)
    return rgb


def cap_gold_text(text, band=GOLD_BAND, cap=GOLD_SAT_CAP, exempt=GOLD_CAP_EXEMPT):
    """Rewrite every non-exempt gold-family colour down to `cap` saturation."""
    def hex_cap(match):
        digits = expand(match.group(1))
        if len(digits) not in (6, 8) or "#" + digits[:6] in exempt:
            return match.group(0)
        rgb = tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))
        alpha = digits[6:8] if len(digits) == 8 else ""
        return shrink(cap_gold(rgb, band, cap), alpha)

    def rgb_cap(match):
        rgb = tuple(int(match.group(i)) for i in (1, 2, 3))
        alpha = match.group(4)
        out = cap_gold(rgb, band, cap)
        if alpha is None:
            return "rgb(%d, %d, %d)" % out
        return "rgba(%d, %d, %d, %s)" % (out + (alpha,))

    return RGB_RE.sub(rgb_cap, HEX_RE.sub(hex_cap, text))


def cap_gold_accents_text(text, cap=GOLD_SAT_CAP, accents=None):
    """The shipped policy: cap only the gold ACCENT colours, nothing else."""
    accents = GOLD_ACCENT_BEFORE if accents is None else accents
    replacements = {}
    for before, _role in accents.items():
        rgb = tuple(int(before[i:i + 2], 16) for i in (1, 3, 5))
        replacements[before] = "#%02x%02x%02x" % cap_gold(rgb, GOLD_BAND, cap)

    def hex_cap(match):
        digits = expand(match.group(1))
        if len(digits) not in (6, 8):
            return match.group(0)
        after = replacements.get("#" + digits[:6])
        if not after:
            return match.group(0)
        return after + (digits[6:8] if len(digits) == 8 else "")

    return HEX_RE.sub(hex_cap, text)


def boost_rgb(r, g, b):
    h, s, l = to_hsl(r, g, b)
    return to_rgb(*boost(h, s, l))


def is_transparent_alpha(alpha):
    if alpha is None:
        return False
    if alpha.endswith("%"):
        try:
            return float(alpha[:-1]) <= 0.0
        except ValueError:
            return False
    try:
        return float(alpha) <= 0.0
    except ValueError:
        return False


def expand(hex_digits):
    """#abc -> abcabc, #abcd -> abcabc + alpha dd."""
    if len(hex_digits) in (3, 4):
        return "".join(c * 2 for c in hex_digits)
    return hex_digits


def shrink(rgb, alpha):
    return "#%02x%02x%02x%s" % (rgb + (alpha,)) if alpha else "#%02x%02x%02x" % rgb


def map_hex(match):
    digits = expand(match.group(1))
    if len(digits) not in (6, 8):
        return match.group(0)                   # var names, ids, urls: skip
    rgb = tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))
    alpha = digits[6:8] if len(digits) == 8 else ""
    if alpha and int(alpha, 16) == 0:
        return match.group(0)                   # fully transparent stop
    return shrink(boost_rgb(*rgb), alpha)


def map_rgb(match):
    if is_transparent_alpha(match.group(4)):
        return match.group(0)
    rgb = boost_rgb(*(int(match.group(i)) for i in (1, 2, 3)))
    alpha = match.group(4)
    if alpha is None:
        return "rgb(%d, %d, %d)" % rgb
    if alpha.endswith("%"):
        return "rgba(%d, %d, %d, %s)" % (rgb + (alpha,))
    return "rgba(%d, %d, %d, %s)" % (rgb + (alpha,))


def transform(text):
    return RGB_RE.sub(map_rgb, HEX_RE.sub(map_hex, text))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--write", action="store_true",
                    help="rewrite the files instead of printing a report")
    ap.add_argument("--map", action="store_true",
                    help="print every colour with its replacement")
    ap.add_argument("--cap-gold", action="store_true",
                    help="cap the whole gold HUE FAMILY, surfaces included "
                         "(the blunter pass; 40%% is the ceiling)")
    ap.add_argument("--cap-gold-accents", action="store_true",
                    help="the shipped policy: cap only the gold accent roles "
                         "listed in GOLD_ACCENT_BEFORE (40%%)")
    args = ap.parse_args(argv)

    colors = Counter()
    for path in args.paths:
        with open(path, encoding="utf-8") as fh:
            original = fh.read()
        if args.cap_gold_accents:
            updated = cap_gold_accents_text(original)
        elif args.cap_gold:
            updated = cap_gold_text(original)
        else:
            updated = transform(original)
        if args.map or not args.write:
            colors.update(m.group(1).lower() for m in HEX_RE.finditer(original))
            colors.update("%d,%d,%d" % tuple(int(m.group(i)) for i in (1, 2, 3))
                          for m in RGB_RE.finditer(original))
        if args.write and updated != original:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(updated)
            print("rewrote %s" % path)
        elif not args.write:
            delta = sum(1 for a, b in zip(original, updated) if a != b)
            print("%s: %s characters would change" % (path, delta))

    if args.cap_gold_accents and not args.write:
        print("\naccent role colours -> capped value (the shipped policy):")
        for before, role in sorted(GOLD_ACCENT_BEFORE.items()):
            rgb = tuple(int(before[i:i + 2], 16) for i in (1, 3, 5))
            after = "#%02x%02x%02x" % cap_gold(rgb, GOLD_BAND, GOLD_SAT_CAP)
            print("  %s -> %s   %s" % (before, after, role))
        return 0

    if args.map or not args.write:
        print("\n%-7s %-22s %-22s" % ("count", "before", "after"))
        for old, count in colors.most_common():
            if "," in old:                      # rgb()/rgba() triplet
                r, g, b = (int(x) for x in old.split(","))
                print("%-7d rgb(%s)%s-> rgb(%d, %d, %d)"
                      % (count, old, " " * 4, *boost_rgb(r, g, b)))
                continue
            before = "#" + expand(old)
            after = map_hex(re.match(HEX_RE, before)).lstrip("#")
            print("%-7d %-22s %-22s" % (count, before, after))
    return 0


if __name__ == "__main__":
    sys.exit(main())
