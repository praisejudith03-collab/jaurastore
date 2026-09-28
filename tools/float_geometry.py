"""Resolve the CSS cascade for the two floating controls (currency pill and
WhatsApp bubble) at a given viewport width and page.

Shared by tests/test_header_layout_lock.py (the permanent no-overlap lock)
and usable by hand:

    python3 tools/float_geometry.py

It is a deliberately small, explicit resolver: it walks css/style.css in
source order, keeps every width-only @media query that applies at the given
viewport, and ranks the declarations for one property by
(!important, specificity, source order) - the same way a browser does for
these simple selectors. Only the RESTING state is modelled: transient
selectors (body.mini-open, .is-behind-cart, body.ja-fr, the admin page) are
skipped, because that is the state a shopper sees.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_PATH = os.path.join(ROOT, "css", "style.css")

_BLOCK = re.compile(r"([^{}]+)\{([^{}]*)\}")
_MEDIA = re.compile(r"@media([^{]*)\{")


def _block_span(css, open_brace):
    """Index just past the matching close brace of the block at open_brace."""
    depth = 0
    i = open_brace
    while i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(css)


def _strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def declarations(css=None):
    """[(media, selector, prop, value, important, order)] in source order."""
    css = _strip_comments(css if css is not None else open(CSS_PATH, encoding="utf-8").read())
    out = []
    order = 0

    def harvest(text, media):
        nonlocal order
        for match in _BLOCK.finditer(text):
            selector = re.sub(r"\s+", " ", match.group(1)).strip()
            if not selector or selector.startswith("@"):
                continue
            for decl in match.group(2).split(";"):
                if ":" not in decl:
                    continue
                prop, _, value = decl.partition(":")
                prop = prop.strip().lower()
                value = value.strip()
                important = "!important" in value.lower()
                value = re.sub(r"!\s*important", "", value, flags=re.I).strip()
                order += 1
                out.append((media, selector, prop, value, important, order))

    pos = 0
    for match in _MEDIA.finditer(css):
        if match.start() > pos:
            harvest(css[pos:match.start()], None)
        end = _block_span(css, match.end() - 1)
        harvest(css[match.end():end - 1], match.group(1).strip())
        pos = end
    harvest(css[pos:], None)
    return out


def media_applies(media, width):
    """True when a width-only @media query applies at `width` px."""
    if not media:
        return True
    query = media.strip().lower()
    features = re.findall(r"\(([^)]*)\)", query)
    if not features or " or " in query or "," in query:
        return False
    for feature in features:
        m = re.match(r"(min|max)-width\s*:\s*(\d+(?:\.\d+)?)px", feature.strip())
        if not m:
            return False                      # hover/reduced-motion/... : skip
        limit = float(m.group(2))
        if m.group(1) == "min" and width < limit:
            return False
        if m.group(1) == "max" and width > limit:
            return False
    return True


def specificity(selector):
    ids = len(re.findall(r"#[\w-]+", selector))
    classes = (len(re.findall(r"\.[\w-]+", selector))
               + len(re.findall(r"\[[^\]]*\]", selector))
               + len(re.findall(r":(?!:)[\w-]+", selector)))
    elements = len(re.findall(r"(?:^|[\s>+~])([a-z][\w-]*)", selector))
    return (ids, classes, elements)


def _matches(part, element, page):
    """Does one comma-separated selector target `element` at rest on `page`?"""
    part = part.strip()
    if not part.endswith(element):
        return False
    prefix = part[:-len(element)].strip()
    if prefix == "":
        return True
    if prefix == 'body[data-page="home"]':
        return page == "home"
    return False        # transient / other-page states are not the resting one


def resolve(prop, element, page="home", width=1280, css=None):
    """The value a browser would use for `prop` on `element`, or None."""
    best = None
    best_key = None
    for media, selector, name, value, important, order in declarations(css):
        if name != prop or not media_applies(media, width):
            continue
        hits = [p for p in selector.split(",") if _matches(p, element, page)]
        if not hits:
            continue
        key = (1 if important else 0, max(specificity(p) for p in hits), order)
        if best_key is None or key > best_key:
            best_key, best = key, value
    return best


def px(value, default=0.0):
    """First pixel figure in a value such as calc(86px + env(...))."""
    if not value:
        return default
    m = re.search(r"(-?\d+(?:\.\d+)?)px", value)
    return float(m.group(1)) if m else default


def geometry(page="home", width=1280, css=None):
    """Resting geometry of both floats, measured from the bottom edge."""
    wa_bottom = px(resolve("bottom", ".wa-float", page, width, css))
    wa_size = px(resolve("height", ".wa-float", page, width, css), 58.0)
    pill_bottom = px(resolve("bottom", ".cur-float", page, width, css))
    return {
        "page": page, "width": width,
        "wa_bottom": wa_bottom, "wa_size": wa_size,
        "wa_top": wa_bottom + wa_size,
        # The glow ring animates to scale(1.85), so it reaches 0.425 x the
        # bubble beyond every edge; the pill must clear that envelope too.
        "wa_glow_top": wa_bottom + wa_size * 1.425,
        "pill_bottom": pill_bottom,
        "gap": pill_bottom - (wa_bottom + wa_size),
    }


CONTEXTS = (("home", 1280), ("shop", 1280), ("home", 390), ("shop", 390))


if __name__ == "__main__":
    for page, width in CONTEXTS:
        g = geometry(page, width)
        print(f"{page:>5} @{width:>5}px  wa bottom {g['wa_bottom']:6.1f}  "
              f"size {g['wa_size']:5.1f}  bubble top {g['wa_top']:6.1f}  "
              f"glow top {g['wa_glow_top']:6.1f}  pill bottom "
              f"{g['pill_bottom']:6.1f}  gap {g['gap']:6.1f}")
