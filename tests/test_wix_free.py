"""The shipped frontend must be Wix-free; the legacy product ids must not be.

Two different things both start with "wix-", and only one of them is allowed
to survive:

  * UI class names (.wix-tile, .wix-menu, .wix-footer, ...) were leftovers from
    the Wix export. They are pure presentation and have been renamed to the
    neutral `au-*` prefix, so nothing in the bundle advertises the old
    platform.
  * Product ids (wix-001 … wix-258) are live primary keys that orders,
    reviews, carts, analytics and old product links reference. Renaming those
    would break every existing order, so they stay exactly as they are.

Run with:  python3 -m pytest tests/test_wix_free.py -q
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_ENV", "testing")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Everything the browser can download.
SHIPPED = (["css/style.css", "js/admin.js", "js/store.js", "js/app.js",
            "js/i18n.js", "js/products-data.js"]
           + sorted(glob.glob(os.path.join(ROOT, "*.html")))
           + sorted(glob.glob(os.path.join(ROOT, "templates", "*.html"))))

PRODUCT_ID_RE = re.compile(r"^wix-\d{3}$")


def _tokens(body):
    return set(re.findall(r"wix-[a-z0-9-]+", body))


def test_no_wix_ui_class_names_in_shipped_frontend():
    offenders = {}
    for path in SHIPPED:
        body = open(path, encoding="utf-8").read()
        ui = {t for t in _tokens(body) if not PRODUCT_ID_RE.match(t)}
        if ui:
            offenders[os.path.relpath(path, ROOT)] = sorted(ui)[:8]
    assert not offenders, f"Wix UI classes still shipped: {offenders}"


def test_css_has_no_wix_selectors():
    css = open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8").read()
    assert not re.search(r"\.wix-[a-z]", css), "css/style.css still has .wix- selectors"


def test_au_classes_replaced_them():
    """The rename must have produced real classes, not deleted the styling."""
    css = open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8").read()
    au_css = set(re.findall(r"\.au-([a-z0-9-]+)", css))
    assert len(au_css) >= 50, f"only {len(au_css)} au- selectors survived the rename"
    for name in ("tile", "media-row", "opt", "menu", "footer", "link", "cat-card"):
        assert name in au_css, f".au-{name} is missing from the stylesheet"


def test_every_au_class_used_in_markup_has_a_css_rule():
    """A partial rename would silently unstyle an element."""
    css = open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8").read()
    defined = set(re.findall(r"\.au-([a-z0-9-]+)", css))
    used = set()
    for path in SHIPPED:
        body = open(path, encoding="utf-8").read()
        # only class usage, not the product ids in products-data.js
        # `(?<![a-z0-9])` stops the pattern matching the tail of a jau-* product
        # id, which contains "au-" and would otherwise show up as a fake orphan.
        used |= set(re.findall(r"(?<![a-z0-9])au-([a-z0-9-]+)", body))
    # au-opt-presets has never had a rule (checked at the pre-rename commit);
    # it is a hook the JS toggles, so it is allowed to be unstyled.
    orphans = sorted(used - defined - {"opt-presets"})
    assert not orphans, f"au- classes with no CSS rule: {orphans}"


def test_legacy_product_ids_are_preserved():
    """The rename must not have touched the wix-* primary keys."""
    seed = open(os.path.join(ROOT, "data", "seed.json"), encoding="utf-8").read()
    snapshot = open(os.path.join(ROOT, "js", "products-data.js"),
                    encoding="utf-8").read()
    assert len(set(re.findall(r'"wix-\d{3}"', seed))) == 258
    assert len(set(re.findall(r"wix-\d{3}", snapshot))) == 258


def test_e2e_selectors_followed_the_rename():
    """tests/e2e.py drives the real DOM, so its selectors must match."""
    e2e = open(os.path.join(ROOT, "tests", "e2e.py"), encoding="utf-8").read()
    assert ".wix-tile" not in e2e and ".wix-save" not in e2e
    assert ".au-tile" in e2e and ".au-save" in e2e
    # but it still navigates to a legacy product id
    assert "wix-001" in e2e
