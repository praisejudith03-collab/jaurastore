"""Real Chromium/mobile smoke tests. No live shop writes or GitHub pushes."""
import os
import threading

import pytest
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

import app as appmod
from config import Config
from db import execute
from _pw import PW


@pytest.fixture()
def live_shop(monkeypatch, tmp_path):
    import db
    import api
    import catalog
    monkeypatch.setattr(catalog, "CATALOG_FILE", str(tmp_path / "catalog.json"))
    from pathlib import Path
    # The older suite keeps thread-local SQLite connections and mutable category
    # files. Give the browser server its own database and canonical categories.
    monkeypatch.setattr(Config, "DB_PATH", str(tmp_path / "browser.db"))
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(db, "_local", threading.local())
    cats = tmp_path / "categories.json"
    cats.write_bytes((Path(__file__).resolve().parents[1] / "data/categories.json").read_bytes())
    monkeypatch.setattr(api, "CATEGORIES_FILE", str(cats))
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", PW)
    app = appmod.create_app()
    app.config.update(TESTING=True)
    execute("DELETE FROM rate_limits")
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()


@pytest.fixture()
def mobile():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_EXECUTABLE"),
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 390, "height": 844},
                                      is_mobile=True, has_touch=True, service_workers="block")
        context.add_init_script("sessionStorage.setItem('jaura_welcome_seen', '1')")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        yield page
        browser.close()
        assert not errors, errors


@pytest.mark.parametrize("path", ["/", "/shop.html", "/categories.html", "/faq.html",
                                  "/cart.html", "/checkout.html", "/product.html"])
def test_mobile_headers_and_shop(mobile, live_shop, path):
    mobile.goto(live_shop + path)
    logo = mobile.locator("#site-header .logo img")
    expect(logo).to_be_visible()
    assert logo.evaluate("img => img.complete && img.naturalWidth > 0")
    search = mobile.locator("#site-header [data-open-search]")
    expect(search).to_be_visible()
    search.click()
    expect(mobile.locator("[data-search-input]")).to_be_visible()
    if path == "/shop.html":
        expect(mobile.locator("[data-shop-grid] > *").first).to_be_visible()
        assert mobile.locator("[data-shop-grid] > *").count() > 0
    if path == "/categories.html":
        expect(mobile.locator("[data-cat-list] a").first).to_have_attribute("href", "shop.html?cat=household")


def test_advanced_actions(mobile, live_shop, monkeypatch):
    import repo_sync
    monkeypatch.setattr(Config, "GITHUB_TOKEN", "test-configured-not-a-real-token")
    calls = []

    def sync(**kwargs):
        calls.append(kwargs)
        return True, {"committed": True, "pushed": True, "branch": "test"}

    monkeypatch.setattr(repo_sync, "regenerate", sync)
    response = mobile.request.post(live_shop + "/api/admin/login",
                                   data={"email": "jaurastore@gmail.com", "password": PW})
    assert response.ok, response.text()
    mobile.goto(live_shop + "/admin.html")
    mobile.locator('[data-tab="account"]:visible').first.click()
    mobile.get_by_text("Advanced settings", exact=True).click()
    expect(mobile.locator("#sync-github")).to_be_visible()
    status = mobile.locator("#sync-status")
    mobile.locator("#retry-sync").click()
    expect(status).to_contain_text("Retry complete")
    mobile.locator("#reload-cat").click()
    expect(status).to_contain_text("Catalogue reloaded from the server:")
    expect(mobile.locator("#reload-cat")).to_be_enabled()
    mobile.locator("#sync-github").click()
    expect(status).to_contain_text("Synced and pushed to GitHub (test)")
    assert calls == [{"commit": True, "push": True}]
    # Status refresh must not erase the actual result.
    expect(mobile.locator("#sync-github")).to_be_enabled()

    monkeypatch.setattr(repo_sync, "regenerate", lambda **kw: (False, {"error": "GitHub unavailable"}))
    mobile.locator("#sync-github").click()
    expect(status).to_contain_text("Failed: GitHub unavailable")
    expect(mobile.locator("#sync-github")).to_be_enabled()

    mobile.route("**/api/catalog?all=1", lambda route: route.fulfill(status=503, json={"error": "offline"}))
    mobile.locator("#reload-cat").click()
    expect(status).to_contain_text("Failed:")
    expect(mobile.locator("#reload-cat")).to_be_enabled()

    # A stranded product fails visibly, rather than resolving as 'all synced'.
    mobile.evaluate("localStorage.setItem('jaura_custom_products', JSON.stringify([{id:'test-stranded', name:'Test', priceNgn:100}]))")
    mobile.route("**/api/admin/products", lambda route: route.fulfill(status=401, json={"error": "Session expired"}))
    mobile.locator("#retry-sync").click()
    expect(status).to_contain_text("Failed:")
    expect(mobile.locator("#retry-sync")).to_be_enabled()

    # A queued mutation must be forced through its retry backoff and awaited.
    mobile.unroute("**/api/admin/products")
    mobile.route("**/api/admin/products", lambda route: route.fulfill(status=503, json={"error": "Offline"}))
    mobile.locator("#retry-sync").click()
    expect(status).to_contain_text("Failed:")
    assert mobile.evaluate("JA_NET.pending()") > 0
    mobile.unroute("**/api/admin/products")
    mobile.route("**/api/admin/products", lambda route: route.fulfill(json={
        "ok": True, "product": {"id": "test-stranded", "name": "Test", "priceNgn": 100}}))
    mobile.locator("#retry-sync").click()
    expect(status).to_contain_text("Retry complete")
    assert mobile.evaluate("JA.syncPending()") == 0


@pytest.mark.parametrize("width", [320, 360, 390, 680, 1440])
@pytest.mark.parametrize("language", ["en", "fr"])
def test_header_controls_do_not_overlap(mobile, live_shop, width, language):
    mobile.set_viewport_size({"width": width, "height": 844})
    mobile.goto(live_shop + "/shop.html")
    logo = mobile.locator("#site-header .logo")
    expect(logo).to_be_visible()
    mobile.locator(f'#site-header .nav-right [data-lang="{language}"]').click()
    a = logo.bounding_box()
    b = mobile.locator("#site-header .nav-right").bounding_box()
    assert a["x"] + a["width"] <= b["x"] + 1 or a["y"] + a["height"] <= b["y"] + 1
    for control in mobile.locator("#site-header .nav-right button").all():
        box = control.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width


def test_owner_category_creation_product_and_reordering(mobile, live_shop):
    response = mobile.request.post(live_shop + '/api/admin/login',
                                   data={'email':'jaurastore@gmail.com', 'password':PW})
    assert response.ok
    token = response.json()['csrf']
    baseline = [
        {'id':'household', 'name':'Household & Kitchen', 'nameFr':'Maison & cuisine'},
        {'id':'beauty', 'name':'Beauty', 'nameFr':'Beauté'},
    ]
    assert mobile.request.put(live_shop + '/api/admin/categories', data={'categories':baseline},
                              headers={'X-CSRF-Token':token}).ok
    mobile.goto(live_shop + '/admin.html')
    mobile.locator('[data-tab="categories"]:visible').first.click()
    mobile.locator('#new-cat-name').fill('Perfume')
    mobile.locator('#new-cat-fr').fill('Parfum')
    mobile.locator('#add-cat').click()
    perfume = mobile.locator('[data-cat-id="perfume"]')
    expect(perfume).to_be_visible()
    from pathlib import Path
    with mobile.expect_response(lambda r: '/api/admin/categories' in r.url and r.request.method == 'PUT') as asset_saved:
        perfume.locator('[data-cat-img]').set_input_files(Path(__file__).resolve().parents[1] / 'images/products/_placeholder.jpg')
    assert asset_saved.value.ok
    for _ in range(2):
        with mobile.expect_response(lambda r: '/api/admin/categories' in r.url and r.request.method == 'PUT') as saved:
            perfume.locator('[data-cat-move="-1"]').click()
        assert saved.value.ok
    expect(mobile.locator('#cat-list > article').first).to_have_attribute('data-cat-id', 'perfume')
    mobile.reload()
    mobile.locator('[data-tab="categories"]:visible').first.click()
    expect(mobile.locator('#cat-list > article').first).to_have_attribute('data-cat-id', 'perfume')
    # Reordered controls still allow editing; live nodes and input IDs survive.
    expect(mobile.locator('[data-cat-id="perfume"] input[name^="cat-fr-"]')).to_have_value('Parfum')
    mobile.locator('[data-tab="products"]:visible').first.click()
    mobile.locator('#add-product').click()
    mobile.locator('#prod-form [name="name"]').fill('Perfume browser sample')
    mobile.locator('#prod-form [name="nameFr"]').fill('Parfum de démonstration')
    mobile.locator('#prod-form [name="priceNgn"]').fill('4000')
    mobile.locator('#prod-form [name="category"]').select_option('perfume')
    from pathlib import Path
    with mobile.expect_response(lambda r: '/api/admin/uploads/image' in r.url) as uploaded:
        mobile.locator('#more-media').set_input_files(Path(__file__).resolve().parents[1] / 'images/products/_placeholder.jpg')
    assert uploaded.value.ok
    with mobile.expect_response(lambda r: '/api/admin/products' in r.url and r.request.method == 'POST') as saved:
        mobile.locator('#prod-form button[type="submit"]').click()
    assert saved.value.ok, saved.value.text()
    mobile.context.clear_cookies()
    mobile.goto(live_shop + '/shop.html?cat=perfume')
    expect(mobile.locator('[data-shop-grid]')).to_contain_text('Perfume browser sample')
    mobile.locator('#site-header .nav-right [data-lang="fr"]').click()
    expect(mobile.locator('[data-shop-grid]')).to_contain_text('Parfum de démonstration')
    expect(mobile.locator('[data-shop-title]').last).to_have_text('Parfum')
    mobile.goto(live_shop + '/categories.html')
    expect(mobile.locator('[data-cat-list] a').first).to_have_attribute('href', 'shop.html?cat=perfume')
    expect(mobile.locator('[data-cat-list] a').first).to_contain_text('Parfum')
    # A new Flask app has no browser-local category/product state.
    with appmod.create_app().test_client() as fresh:
        assert fresh.get('/api/categories').json['categories'][0]['id'] == 'perfume'
        products = fresh.get('/api/catalog').json['products']
    assert any(p['category'] == 'perfume' and p['nameFr'] == 'Parfum de démonstration' for p in products)


@pytest.mark.parametrize('language', ['en', 'fr'])
def test_faq_wording_in_browser(mobile, live_shop, language):
    import re
    mobile.goto(live_shop + '/faq.html')
    mobile.locator(f'#site-header .nav-right [data-lang="{language}"]').click()
    assert not re.search(r'admin[\s_-]*portal|portail\s+admin', mobile.locator('body').inner_text(), re.I)
