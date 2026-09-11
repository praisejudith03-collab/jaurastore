"""#68 regressions: public headers, durable categories and browser ordering."""
from pathlib import Path
import shutil
import subprocess

import pytest
import app as appmod
import supabase_store
from test_categories_resilient import _LegacyCatTable, _FakeClient, _cats

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client():
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as client:
        yield client


@pytest.mark.parametrize("path", ["/", "/shop.html", "/categories.html", "/faq.html",
                                  "/cart.html", "/checkout.html", "/product.html"])
def test_public_header_and_no_admin_portal(client, path):
    # Public pages must work WITHOUT invented authentication headers.
    response = client.get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="site-header"' in html
    assert 'src="js/store.js?' in html
    # Actual injected logo/search visibility is checked in test_browser_smoke.py.
    assert "admin portal" not in html.lower()


def test_category_persistence_after_redeploy(monkeypatch):
    store = {}
    table = _LegacyCatTable(store)
    monkeypatch.setattr(supabase_store, "client", lambda: _FakeClient(store, table))
    monkeypatch.setattr(supabase_store, "_CATS_SHAPE", {"fill": {}, "drop": [], "values": {}})
    categories = _cats() + [{"id": "perfume", "name": "Perfume", "name_fr": "Parfum"}]
    assert supabase_store.save_categories_table(categories) is True
    assert store["perfume"]["name_fr"] == "Parfum"
    # A fresh client/schema cache still operates on the durable table.
    monkeypatch.setattr(supabase_store, "_CATS_SHAPE", {"fill": {}, "drop": [], "values": {}})
    assert supabase_store.save_categories_table(list(store.values())) is True
    assert store["perfume"]["name"] == "Perfume"


def test_household_and_explicit_category_order_in_real_store():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    result = subprocess.run([node, "tests/_store_sim.mjs"], cwd=ROOT,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "category ordering preserves every category" in result.stdout


# ------------------------------------------------- cache freshness (no-store)
@pytest.mark.parametrize("path", [
    "/healthz",
    "/api/site",
    "/api/catalog",
    "/api/products",
    "/api/categories",
    "/api/config",
    "/api/csrf",
    "/api/stock",
    "/api/most-viewed",
    "/api/payment-methods",
])
def test_dynamic_routes_are_never_served_from_a_cache(client, path):
    """Every dynamic page/API answer carries no-store: a CDN (or the browser
    disk cache) holding a stale copy after an admin save is exactly the
    "saved product / new photo not visible on my phone" complaint. Static
    assets are exempt - they carry the ?v= shared token instead."""
    response = client.get(path)
    assert response.status_code == 200, f"{path} -> {response.status_code}"
    cache_control = response.headers.get("Cache-Control", "")
    assert "no-store" in cache_control, \
        f"{path} serves Cache-Control={cache_control!r}; dynamic answers must be no-store"


def test_html_asset_refs_carry_the_shared_token_and_sw_evicts_old_caches():
    """Every page references the versioned assets, and the service worker is
    network-first for pages and deletes every cache that is not the current
    VERSION on activate - so no phone keeps serving the previous storefront
    after the token is bumped."""
    import re
    sw = (ROOT / "sw.js").read_text(encoding="utf-8")
    m = re.search(r'const VERSION = "jaura-v(\d+)";', sw)
    assert m, "sw.js VERSION constant"
    ver = m.group(1)
    # network-first for navigations, old caches evicted on activate
    assert "networkFirst(req, true)" in sw
    assert 'req.mode === "navigate"' in sw
    assert "k !== VERSION" in sw and "caches.delete(k)" in sw
    # every HTML page stamps its asset refs with the same token
    for page in sorted(ROOT.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        versions = set(re.findall(r"\?v=(\d+)", html))
        assert versions <= {ver}, f"{page.name} still references {versions - {ver}}"
        for asset in ("css/style.css", "js/store.js", "js/app.js"):
            assert f"{asset}?v={ver}" in html or f"{asset}?v=" not in html, \
                f"{page.name} references {asset} without the shared token"


def test_tokened_static_assets_are_immutable_untokened_ones_revalidate(client):
    """The shared ?v= token is an immutability promise, and only that.

    A URL like /css/style.css?v=140 serves exactly one build of the file, so
    it must answer `public, max-age=31536000, immutable`: on a 4G phone the
    stylesheet, the scripts and the logo then come straight from the local
    cache instead of a conditional round-trip per asset (the ~5s repeat
    visit). Everything a visitor must see fresh - HTML pages, the service
    worker itself, and any asset link WITHOUT a numeric token - keeps
    no-cache. The token is derived from sw.js so this test fails if the
    pages, the worker and the header ever drift apart."""
    import re
    sw = (ROOT / "sw.js").read_text(encoding="utf-8")
    m = re.search(r'const VERSION = "jaura-v(\d+)";', sw)
    assert m, "sw.js VERSION constant"
    ver = m.group(1)

    # the worker serves tokened subresources cache-first (never revalidating
    # an immutable URL against the network)
    assert "async function cachedVersioned(request)" in sw, \
        "sw.js must define cachedVersioned()"
    assert 'url.searchParams.get("v")' in sw, \
        "sw.js fetch handler must branch on the ?v= token"
    assert 'event.respondWith(cachedVersioned(req))' in sw, \
        "sw.js must serve tokened requests with cachedVersioned()"

    tokened = (
        f"/css/style.css?v={ver}",
        f"/js/store.js?v={ver}",
        f"/js/app.js?v={ver}",
        f"/images/brand/logo.jpg?v={ver}",
    )
    for path in tokened:
        response = client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"
        cache_control = response.headers.get("Cache-Control", "")
        assert "max-age=31536000" in cache_control and "immutable" in cache_control, \
            f"{path} serves Cache-Control={cache_control!r}; tokened assets " \
            "are immutable for the life of the token"
        assert "no-cache" not in cache_control

    untokened = ("/", "/shop.html", "/sw.js", "/css/style.css")
    for path in untokened:
        response = client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"
        cache_control = response.headers.get("Cache-Control", "")
        assert "no-cache" in cache_control, \
            f"{path} serves Cache-Control={cache_control!r}; every page, " \
            "sw.js and untokened asset links must revalidate"

    # only a fully numeric token unlocks immutability: a word token (?v=prod)
    # is not a build stamp and must stay revalidating.
    response = client.get("/css/style.css?v=prod")
    assert response.status_code == 200
    assert "no-cache" in response.headers.get("Cache-Control", ""), \
        "a non-numeric ?v= value is not a cache token and must stay no-cache"
