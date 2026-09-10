"""The French storefront: one vocabulary, both languages, no lost stock.

Switching the shop to French used to translate the chrome (menus, buttons)
while the catalogue stayed English, because the product row only ever carried
``nameFr`` and nothing read a French description at all. These tests pin the
pieces that make the catalogue itself follow the language switch, plus the two
ways that can go wrong and cost money or trust:

  * a French label reaching the VARIANT IDENTITY (optionStock is keyed by the
    raw value, so translating it would sell a variant that does not exist);
  * an unwritten French field rendering as BLANK instead of falling back to
    English.

The currency/language pill sizing is checked here too: the stylesheet carried
six competing ``.currency-switch button`` rules and no width at all, so the
pill visibly resized when tapped. See the block at the end of css/style.css.

Run with:  python3 -m pytest tests/test_french_catalog.py -q
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("SITE_CONFIG_PATH", "/tmp/jaura_test_site_fr.json")

import pytest  # noqa: E402

import catalog as catmod  # noqa: E402
import supabase_store  # noqa: E402


# ============================================================ catalog.normalize
@pytest.mark.parametrize("key", ["descriptionFr", "description_fr"])
def test_normalize_keeps_the_french_description_under_either_spelling(key):
    """The admin form posts camelCase; mirror/import rows use snake_case."""
    out = catmod.normalize({"name": "Tote", key: "Sac en toile"})
    assert out["descriptionFr"] == "Sac en toile"


@pytest.mark.parametrize("key", ["nameFr", "name_fr"])
def test_normalize_keeps_the_french_name_under_either_spelling(key):
    out = catmod.normalize({"name": "Tote", key: "Sac"})
    assert out["nameFr"] == "Sac"


def test_normalize_prefers_camelcase_over_a_stale_snake_case_column():
    out = catmod.normalize({"name": "Tote", "nameFr": "Gagnant", "name_fr": "Perdant"})
    assert out["nameFr"] == "Gagnant"


def test_unwritten_french_copy_is_empty_not_missing():
    """"" means "fall back to English". A missing key would make the storefront
    read ``undefined`` and render a blank description."""
    out = catmod.normalize({"name": "Tote"})
    assert out["descriptionFr"] == ""
    assert out["nameFr"] == ""


def test_french_description_is_sanitised_like_the_english_one():
    out = catmod.normalize({"name": "Tote", "descriptionFr": "<script>x</script>"})
    assert "<script>" not in out["descriptionFr"]


def test_base_fields_documents_the_french_columns():
    assert "nameFr" in catmod.BASE_FIELDS
    assert "descriptionFr" in catmod.BASE_FIELDS


# ====================================================== supabase_store folding
def test_canonicalize_folds_snake_case_french_onto_camelcase():
    row = {"id": "wix-1", "name_fr": "Sac", "description_fr": "En toile"}
    out = supabase_store._canonicalize_product(row)
    assert out["nameFr"] == "Sac"
    assert out["descriptionFr"] == "En toile"


def test_canonicalize_lets_camelcase_win():
    out = supabase_store._canonicalize_product(
        {"id": "wix-1", "nameFr": "Gagnant", "name_fr": "Perdant"})
    assert out["nameFr"] == "Gagnant"


def test_canonicalize_ignores_a_blank_camelcase_and_uses_the_snake_case_one():
    out = supabase_store._canonicalize_product(
        {"id": "wix-1", "nameFr": "   ", "name_fr": "Sac"})
    assert out["nameFr"] == "Sac"


def test_canonicalize_does_not_invent_french_copy():
    out = supabase_store._canonicalize_product({"id": "wix-1", "name": "Bag"})
    assert "descriptionFr" not in out


def test_canonicalize_keeps_its_existing_stock_and_image_contract():
    """Folding must not disturb what this helper already guarantees."""
    out = supabase_store._canonicalize_product({"id": "wix-1", "image": "a.jpg",
                                                "stock_quantity": 7})
    assert out["image_url"] == "a.jpg"
    assert out["stock"] == 7 and out["stock_quantity"] == 7


# ==================================================================== schema
def _schema() -> str:
    with open(os.path.join(ROOT, "supabase_schema.sql"), encoding="utf-8") as fh:
        return fh.read()


def test_schema_creates_the_french_description_column():
    assert re.search(r'^\s*"descriptionFr"\s+text,', _schema(), re.M)


def test_schema_repairs_an_existing_table_with_the_french_description_column():
    """The production table already exists, so the create-table alone never
    reaches it - without this ALTER the upsert would drop the column."""
    assert re.search(
        r'alter table products add column if not exists "descriptionFr"\s+text;',
        _schema())


def test_products_section_stays_mobile_pasteable():
    """schema_sections are copy-pasted into the Supabase SQL Editor on a
    phone, so each has to stay under 6 KB (tests/test_schema_sections.py
    enforces the same limit for every section)."""
    path = os.path.join(ROOT, "schema_sections", "01_products.sql")
    size = os.path.getsize(path)
    assert size < 6000, f"01_products.sql grew to {size} bytes"
    with open(path, encoding="utf-8") as fh:
        assert '"descriptionFr"' in fh.read()


# ============================================================ category labels
_JS_CAT_RE = re.compile(
    r'\{\s*id:\s*"(?P<id>[a-z-]+)"\s*,\s*name:\s*"(?P<name>[^"]*)"\s*,'
    r'\s*nameFr:\s*"(?P<nameFr>[^"]*)"')


def _js_default_cats() -> dict:
    with open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8") as fh:
        src = fh.read()
    block = src[src.index("const DEFAULT_CATS = ["):]
    block = block[:block.index("];")]
    return {m.group("id"): m.groupdict() for m in _JS_CAT_RE.finditer(block)}


def test_every_default_category_has_a_french_name_on_the_server():
    import api
    missing = [c["id"] for c in api.DEFAULT_CATEGORIES
               if not str(c.get("nameFr") or "").strip()]
    assert not missing, f"api.DEFAULT_CATEGORIES missing nameFr: {missing}"


def test_every_default_category_has_a_french_name_in_the_storefront():
    cats = _js_default_cats()
    assert len(cats) == 14, f"expected 14 default categories, parsed {len(cats)}"
    missing = [cid for cid, c in cats.items() if not c["nameFr"].strip()]
    assert not missing, f"js/store.js DEFAULT_CATS missing nameFr: {missing}"


def test_server_and_storefront_agree_on_the_french_category_names():
    """Two hard-coded copies of the same table drift apart silently; a shopper
    would see one label in the nav and another on the category page."""
    import api
    js = _js_default_cats()
    server = {c["id"]: c for c in api.DEFAULT_CATEGORIES}
    assert set(js) == set(server), (
        f"id sets differ: only-js={sorted(set(js) - set(server))} "
        f"only-server={sorted(set(server) - set(js))}")
    for cid, row in js.items():
        assert row["nameFr"] == server[cid]["nameFr"], (
            f"{cid}: js={row['nameFr']!r} server={server[cid]['nameFr']!r}")


def test_committed_category_file_has_no_untranslated_label():
    with open(os.path.join(ROOT, "data", "categories.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data.get("categories") or []
    assert rows, "data/categories.json has no categories"
    missing = [c["id"] for c in rows if not str(c.get("nameFr") or "").strip()]
    assert not missing, f"data/categories.json missing nameFr: {missing}"


# ====================================================== option-value vocabulary
def test_colour_vocabulary_covers_the_shipped_catalogue():
    """A colour the shop actually sells must not fall through untranslated.
    Read from the real vocabulary file, not a copy of it."""
    with open(os.path.join(ROOT, "js", "i18n-phrases.js"), encoding="utf-8") as fh:
        src = fh.read()
    block = src[src.index("window.I18N_OPTION_VALUES"):]
    block = block[:block.index("return base;")]
    vocab = set(re.findall(r'"([^"]+)":\s*"[^"]*"', block))

    with open(os.path.join(ROOT, "data", "seed.json"), encoding="utf-8") as fh:
        seed = json.load(fh)
    sold = set()
    for p in seed:
        for opt in (p.get("options") or []):
            sold.update(str(v) for v in (opt.get("values") or []))
        sold.update(str(v) for v in (p.get("colors") or []))

    hexes = {v for v in sold if re.match(r"^#[0-9a-fA-F]{3,8}$", v)}
    numerics = {v for v in sold if re.match(r"^\d+$", v)}
    sizes = {"S", "M", "L", "XL"}          # identical in French, pass through
    need = sold - hexes - numerics - sizes
    gaps = sorted(v for v in need if v not in vocab and v.lower() not in vocab)
    assert not gaps, f"sold option values with no French label: {gaps}"


def test_vocabulary_never_remaps_a_value_onto_itself_in_english():
    """A French table that answers "Black" for "Black" would look correct in
    a test and be wrong on screen."""
    with open(os.path.join(ROOT, "js", "i18n-phrases.js"), encoding="utf-8") as fh:
        src = fh.read()
    block = src[src.index("window.I18N_OPTION_VALUES"):]
    block = block[:block.index("return base;")]
    pairs = re.findall(r'"([^"]+)":\s*"([^"]*)"', block)
    assert pairs, "no entries parsed out of I18N_OPTION_VALUES"
    untranslated = [k for k, v in pairs
                    if k != k.lower() and v.strip().lower() == k.strip().lower()
                    and v.strip() != k.strip()]
    assert not untranslated, f"case-only 'translations': {untranslated}"


# ======================================================== currency pill sizing
_SWITCH_METRICS = ("padding", "font-size", "font-weight", "height", "min-height",
                   "max-height", "min-width", "letter-spacing", "margin",
                   "border-width", "line-height")
_COLOUR_ONLY = {"background", "background-color", "color", "border-color",
                "border", "outline", "box-shadow", "opacity"}


def _css() -> str:
    with open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8") as fh:
        return fh.read()


def _at_block_span(css: str, start: int) -> int:
    """Index just past the brace-balanced block that opens at `start`."""
    depth = 0
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(css)


def _strip_media(css: str) -> str:
    """CSS with every @media block removed.

    A rule inside `@media (max-width: 640px)` does NOT apply on a desktop
    viewport, so it must not be mistaken for the top-level rule of the same
    selector - that is exactly the confusion that makes a sizing assertion
    pass for the wrong reason.
    """
    out = []
    i = 0
    while True:
        m = re.compile(r"@(?:media|supports)[^{]*\{").search(css, i)
        if not m:
            out.append(css[i:])
            break
        out.append(css[i:m.start()])
        i = _at_block_span(css, m.end() - 1)
    return "".join(out)


def _media_blocks(css: str, query: str) -> list:
    """Contents of every `query` @media block, in file order."""
    found = []
    for m in re.finditer(re.escape(query) + r"\s*\{", css):
        end = _at_block_span(css, m.end() - 1)
        found.append(css[m.end():end - 1])
    return found


def _blocks(css: str):
    """Yield (selector_text, declarations) for every rule in the given text.

    Comments are removed first: `[^{}]+` would otherwise glue a preceding
    `/* ... */` onto the selector it introduces and every exact-selector match
    would miss.
    """
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        yield match.group(1).strip(), match.group(2)


def _is_switch_is_on(selector: str) -> bool:
    parts = [p.strip() for p in selector.split(",")]
    return bool(parts) and all(
        ("lang-switch" in p or "currency-switch" in p) and ".is-on" in p
        for p in parts)


def _props(decl: str) -> dict:
    out = {}
    for item in decl.split(";"):
        if ":" not in item:
            continue
        name, _, value = item.partition(":")
        value = value.strip().lower().replace("!important", "").strip()
        out[name.strip().lower()] = value
    return out


def test_is_on_rules_for_the_switches_only_change_colour():
    """The pill resized when tapped because a selection state could alter its
    box. Every `.is-on` rule for these two switches must stay colour-only.

    Scanned at top level AND inside every @media block, so a future theme
    cannot reintroduce the bug behind a breakpoint.
    """
    css = _css()
    scopes = [("top", _strip_media(css))]
    scopes += [(q, body) for q in ("@media (max-width: 640px)",
                                   "@media (max-width: 800px)")
               for body in _media_blocks(css, q)]

    seen, offenders = 0, []
    for scope, body in scopes:
        for selector, decl in _blocks(body):
            if not _is_switch_is_on(selector):
                continue
            seen += 1
            for name in _props(decl):
                if name in _COLOUR_ONLY:
                    continue
                if name in _SWITCH_METRICS:
                    offenders.append(f"[{scope}] {selector} {{ {name} }}")
    assert seen >= 8, f"expected the themed .is-on blocks, found {seen}"
    assert not offenders, "`.is-on` must not size the switch: " + "; ".join(offenders)


def _rule(css: str, selector: str) -> dict:
    """Declarations of the rule whose selector matches exactly.

    The authoritative block is last in the file, so walking the rules in order
    and keeping the last match is what the browser would resolve to.
    """
    wanted = re.sub(r"\s+", " ", selector).strip()
    found = None
    for sel, decl in _blocks(css):
        if re.sub(r"\s+", " ", sel).strip() == wanted:
            found = _props(decl)
    assert found is not None, f"no rule for selector {selector!r}"
    return found


def test_final_block_pins_the_switch_box():
    """The authoritative block is last in the file so it wins the cascade."""
    css = _css()
    assert "FIXED SIZE, colour-only selection" in css, \
        "the pinned-switch block is missing from css/style.css"

    top = _strip_media(css)          # desktop viewport: no @media rule applies
    container = _rule(top, ".lang-switch, .currency-switch")
    assert container.get("height", "").startswith("34px")
    assert container.get("max-height", "").startswith("34px")
    assert container.get("overflow", "").startswith("hidden")
    assert container.get("flex-shrink") == "0", "the nav row must never squeeze a switch"

    # The base selector and its .is-on state share ONE metric rule: that is
    # what makes it impossible for a selection to change the box.
    button = _rule(top, ".lang-switch button, .currency-switch button, "
                        ".lang-switch button.is-on, .currency-switch button.is-on")
    assert button.get("height", "").startswith("32px")
    assert button.get("padding", "").startswith("0 "), \
        "vertical padding fights the fixed height"
    assert button.get("font-size", "").startswith("12px")
    assert button.get("font-weight") == "600"


def test_every_label_gets_its_own_fixed_width():
    """₦ and "F CFA" are different strings; without a per-label min-width the
    pill changes width as the highlight moves between them. The width belongs
    to the LABEL, so both states of a button resolve to the same box."""
    top = _strip_media(_css())
    short = _rule(top, '.lang-switch button, .currency-switch button[data-cur="NGN"]')
    long = _rule(top, '.currency-switch button[data-cur="CFA"]')
    assert short.get("min-width", "").startswith("40px")
    assert long.get("min-width", "").startswith("64px")


def test_small_screens_get_a_smaller_but_still_fixed_switch():
    """The phone keeps the same FIXED metrics as the desktop pill (the owner
    needs a comfortable 40px tap target and 12px labels even on the smallest
    screens) - the switch must never become auto-sized just because the
    viewport is narrow. The last 640px block that still sizes the switch is
    the cascade winner."""

    def _maybe_rule(css, selector):
        """Declarations of the last exact-selector match, or ''."""
        wanted = re.sub(r"\s+", " ", selector).strip()
        found = None
        for sel, decl in _blocks(css):
            if re.sub(r"\s+", " ", sel).strip() == wanted:
                found = _props(decl)
        return found or {}

    css = _css()
    container = {}
    button = {}
    short = {}
    long = {}
    for body in _media_blocks(css, "@media (max-width: 640px)"):
        c = _maybe_rule(body, ".lang-switch, .currency-switch")
        b = _maybe_rule(body, ".lang-switch button, .currency-switch button, "
                              ".lang-switch button.is-on, "
                              ".currency-switch button.is-on")
        s = _maybe_rule(body, '.lang-switch button, '
                              '.currency-switch button[data-cur="NGN"]')
        l = _maybe_rule(body, '.currency-switch button[data-cur="CFA"]')
        if c:
            container = c
        if b:
            button = b
        if s:
            short = s
        if l:
            long = l

    assert container.get("height", "").startswith("42px")
    assert button.get("height", "").startswith("40px")
    assert button.get("padding", "").startswith("0 ")
    assert button.get("font-size", "").startswith("12px")
    assert short.get("min-width", "").startswith("40px")
    assert long.get("min-width", "").startswith("64px")
