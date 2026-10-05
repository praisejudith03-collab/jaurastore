"""Switching ₦ / F CFA must show the WHOLE catalogue, never a cached short list.

The report: the shop listed 27 products in Naira and 17 in CFA. Two causes,
both client-side, both covered here:

  * js/store.js repainted the grid from the localStorage catalogue box on a
    currency switch. A box written from a partial answer stayed on screen.
  * js/app.js kept the shop's price-filter bounds in the ACTIVE currency, so
    a Naira floor (1 ₦ = 0.44 CFA, i.e. roughly double) filtered products out
    of the grid the moment the shopper switched to CFA.

The browser half runs in tests/_currency_refresh_sim.mjs, which boots the real
js/store.js in a stubbed browser. The server half asserts the short private
HTTP cache + ETag contract: product saves invalidate the server snapshot, while
normal page transitions can reuse a verified catalogue response.

Run with:  python3 -m pytest tests/test_currency_refresh.py -q
"""
import os
import re
import shutil
import subprocess

import pytest

import app as appmod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(ROOT, "tests", "_currency_refresh_sim.mjs")


@pytest.fixture()
def client():
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def test_currency_switch_refetches_the_full_catalogue():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (frontend sim needs Node >= 18)")
    proc = subprocess.run([node, SIM], cwd=ROOT, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, (
        "currency refresh simulation failed:\n" + proc.stdout + proc.stderr)
    assert "all currency refresh checks passed" in proc.stdout


# ------------------------------------------------------------- server half

def test_catalog_answer_has_a_short_private_browser_cache(client):
    """Catalogues are safe to reuse briefly, never shared between sessions."""
    resp = client.get("/api/catalog")
    assert resp.status_code == 200
    cache = resp.headers.get("Cache-Control", "")
    for directive in ("private", "no-cache", "must-revalidate"):
        assert directive in cache, \
            f"/api/catalog serves Cache-Control={cache!r}; missing {directive!r}"
    assert "no-store" not in cache
    assert resp.headers.get("ETag"), "an ETag makes stale transitions cheap"


def test_catalog_answer_varies_by_session(client):
    """An admin sees hidden rows; a shared cache must never mix the two."""
    resp = client.get("/api/catalog")
    vary = resp.headers.get("Vary", "")
    assert "Cookie" in vary, f"/api/catalog serves Vary={vary!r}; must vary on Cookie"


def test_a_forced_refresh_is_still_served_normally(client):
    """The ?_fresh=<ts> cache-buster must not change the answer itself."""
    plain = client.get("/api/catalog")
    fresh = client.get("/api/catalog?_fresh=1700000000000")
    assert fresh.status_code == 200
    assert fresh.get_json()["products"] == plain.get_json()["products"]
    assert "no-cache" in fresh.headers.get("Cache-Control", "")


# ---------------------------------------------------- the shipped JS itself

def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_store_exposes_a_cache_invalidating_refresh():
    store = _read(os.path.join("js", "store.js"))
    assert "function invalidateCatalogCache()" in store
    assert "function refreshCatalog()" in store
    # and the currency listener actually calls it
    listener = store.split('document.addEventListener("ja:currency"', 1)[1][:1400]
    assert "refreshCatalog()" in listener, \
        "the ja:currency listener must refetch the catalogue, not just repaint"


def test_normal_navigation_uses_the_browser_catalogue_cache_but_refresh_bypasses_it():
    store = _read(os.path.join("js", "store.js"))
    # The loader now also consults the inline <head> prefetch before it builds
    # its own request, so the window has to reach past that block.
    load = store.split("async function loadSeed(", 1)[1][:3600]
    assert 'cache: opts.fresh ? "reload" : "default"' in load
    assert 'headers: opts.fresh ? { "Cache-Control": "no-cache", "Pragma": "no-cache" } : {}' in load
    assert "_fresh=" in load


def test_shop_price_bounds_never_survive_a_currency_switch():
    app_js = _read(os.path.join("js", "app.js"))
    assert re.search(r'const shopFilter = \{[^}]*\bcur:\s*""', app_js), \
        "shopFilter must record the currency its price bounds belong to"
    assert "function resetShopFilterForCurrency()" in app_js
    body = app_js.split("\nfunction renderShop() {", 1)[1][:600]
    assert "resetShopFilterForCurrency()" in body, \
        "renderShop() must drop stale bounds before it filters the catalogue"
