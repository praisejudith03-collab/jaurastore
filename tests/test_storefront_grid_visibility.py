"""The shop grid shows every product the API publishes - in both languages.

The report behind this file: "I add a product, it appears on the order form,
but not in the shop." tests/_store_sim.mjs proves the STORE keeps every row and
tests/test_catalog_visibility.py proves the API publishes every row. This file
closes the last gap: the GRID, in a real DOM, with the real shop.html and the
real js/ bundle.

Two defects were found and are pinned here:

  1. renderShop() captured price-range bounds on the FIRST paint - before
     /api/site delivered the live CFA rate - and then applied them as a hard
     filter. A French visitor's currency is locked to F CFA, every Naira row
     converted to 0 without a rate, so the floor became a price most of the
     catalogue sat below: 259 products rendered as ONE card. The same frozen
     bound hid a brand-new product with no price yet (the owner saw it on the
     admin order form, never in the shop).
  2. "Newest" sorted nothing, so a just-saved row kept its id position and
     landed on the last page.

The check boots the real shop.html in jsdom (a developer dependency: install
it with `npm install jsdom`, or set JA_JSDOM_DIR). It skips locally when jsdom
is absent and FAILS in CI, where the workflow installs it - a green run always
means the grid was actually rendered.

Run with:  python3 -m pytest tests/test_storefront_grid_visibility.py -q
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tests", "_storefront_grid_check.mjs")
sys.path.insert(0, ROOT)

import api as api_mod  # noqa: E402
import catalog as catalog_mod  # noqa: E402

NEW_ONE = {
    "id": "jau-grid-new-1",
    "name": "Brand New Bag",
    "nameFr": "Sac tout neuf",
    "slug": "brand-new-bag",
    "category": "bags",
    "priceNgn": 15000,
    "priceCfa": 0,
    "stock": 3,
    "image": "images/products/3-in-1-laptop-bag.jpg",
    "online": True,
}
NEW_TWO = {
    "id": "jau-grid-new-2",
    "name": "Mystery Gift Box",
    "nameFr": "Boîte cadeau mystère",
    "slug": "mystery-gift-box",
    "category": "brandnew-cat",       # not in the category table on purpose
    "priceNgn": 0,
    "priceCfa": 0,
    "stock": 0,
    "image": "",                      # placeholder on purpose
    "online": True,
}


def _saved_now(offset_seconds=0):
    """A timestamp for a row saved *right now*.

    The payload also carries the live catalogue, whose rows may be stamped
    during this same run. A hard-coded date stops being "newest" the moment
    the clock passes it - which is exactly how this check went red in a
    full-suite run after passing alone.
    """
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=2 + offset_seconds)
    return when.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _public_rows():
    """The live catalogue in exactly the shape /api/catalog publishes."""
    rows = []
    for product in catalog_mod.merged(include_hidden=True):
        if product.get("online") is False:
            continue
        rows.append(api_mod._public_product(product))
    for offset, row in enumerate((NEW_ONE, NEW_TWO)):
        merged = dict(row)
        merged["created_at"] = _saved_now(offset)
        merged.setdefault("priceCfa", 0)
        merged.setdefault("optionPrices", {})
        rows.append(api_mod._public_product(merged))
    return rows


def _write_payloads(tmpdir, rate):
    catalog_path = os.path.join(tmpdir, "catalog.json")
    categories_path = os.path.join(tmpdir, "categories.json")
    site_path = os.path.join(tmpdir, "site.json")
    rows = _public_rows()
    payload = {"ok": True, "products": rows,
               "meta": {"count": len(rows), "publishedCount": len(rows),
                        "hiddenCount": 0, "server": True},
               "homepageFeatured": {}}
    with open(catalog_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    try:
        categories = catalog_mod.categories()
    except Exception:
        categories = []
    with open(categories_path, "w", encoding="utf-8") as fh:
        json.dump({"ok": True, "categories": categories}, fh, ensure_ascii=False)
    with open(site_path, "w", encoding="utf-8") as fh:
        # store.js reads d.site - a rate at the top level is ignored, and the
        # page then keeps the fallback rate (exactly the stale-RSS bug the
        # exchange-rate work fixed).
        json.dump({"ok": True, "site": {"cfaRate": rate}}, fh, ensure_ascii=False)
    return catalog_path, categories_path, site_path


def _run_grid_check(tmpdir, *, lang, rate):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (storefront grid check needs Node >= 18)")
    catalog_path, categories_path, site_path = _write_payloads(tmpdir, rate)
    env = dict(os.environ)
    env.update({
        "JA_GRID_CATALOG": catalog_path,
        "JA_GRID_CATEGORIES": categories_path,
        "JA_GRID_SITE": site_path,
        "JA_GRID_LANG": lang,
        "JA_GRID_RATE": str(rate),
        "JA_GRID_NEW_IDS": NEW_ONE["id"] + "," + NEW_TWO["id"],
        "JA_GRID_UNKNOWN_CATEGORY": NEW_TWO["category"],
    })
    proc = subprocess.run([node, SCRIPT], cwd=ROOT, env=env, text=True,
                          capture_output=True, timeout=300)
    if proc.returncode == 3:
        message = ("jsdom is not installed (npm install jsdom, or set "
                   "JA_JSDOM_DIR); the storefront grid check did not run")
        if os.environ.get("JA_REQUIRE_BROWSER") == "1":
            pytest.fail(message + " - CI installs it, so this is a broken build")
        pytest.skip(message)
    assert proc.returncode == 0, (
        f"storefront grid check failed for lang={lang} rate={rate}:\n"
        + proc.stdout + "\n" + proc.stderr)
    assert "STOREFRONT GRID CHECKS PASSED" in proc.stdout


@pytest.fixture(scope="module")
def scratch():
    with tempfile.TemporaryDirectory(prefix="jau-grid-") as tmpdir:
        yield tmpdir


def test_every_online_product_renders_in_english(scratch):
    """The grid reaches the whole catalogue, with the new rows on page 1."""
    _run_grid_check(scratch, lang="en", rate=0)


def test_the_grid_survives_a_french_boot_before_the_exchange_rate(scratch):
    """The 259 -> 1 regression: no rate yet, French locks the currency to CFA.

    Every Naira-priced row reads 0 until the rate lands. The grid must still
    show the whole catalogue (unpriced rows read "price on request"), because
    an unknown price is not a cheap product.
    """
    _run_grid_check(scratch, lang="fr", rate=0)


def test_the_grid_prices_everything_once_the_live_rate_lands(scratch):
    """With a rate, the same catalogue is priced in CFA - still all of it."""
    _run_grid_check(scratch, lang="fr", rate=0.44)


def test_the_check_really_would_catch_a_dropped_card():
    """Guard the guard: a payload with one row must not pass as a full grid."""
    rows = _public_rows()
    assert len(rows) > 200, "the fixture must be a real catalogue, not a toy"
    ids = {str(r.get("id") or "") for r in rows}
    assert NEW_ONE["id"] in ids and NEW_TWO["id"] in ids
    assert NEW_TWO["priceNgn"] == 0, "the unpriced-row case must stay represented"
    assert NEW_TWO["category"] == "brandnew-cat"
    source = open(SCRIPT, encoding="utf-8").read()
    # If someone removes the page-1 assertion or the untouched-filter check,
    # this fails and says which guarantee was lost.
    assert "opens on page 1" in source
    assert "applyShopFilters(JA.products()).length" in source
