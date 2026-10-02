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
        try:
            browser = pw.chromium.launch(
                executable_path=os.environ.get("CHROMIUM_EXECUTABLE"),
                args=["--no-sandbox", "--disable-dev-shm-usage"])
        except Exception as exc:
            pytest.skip(f"Playwright chromium not available: {exc}")
        context = browser.new_context(viewport={"width": 390, "height": 844},
                                      is_mobile=True, has_touch=True, service_workers="block")
        context.add_init_script("sessionStorage.setItem('jaura_welcome_seen', '1')")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        yield page
        browser.close()
        assert not errors, errors


def open_admin_tab(page, tab):
    """Click an admin section through the pinned bottom dock (2026-09-12).

    The five primary sections sit in the dock itself; Categories, Delivery,
    Settings and Account live behind its "More" sheet, so open the sheet
    first when the tab is not visible yet.
    """
    btn = page.locator(f'[data-tab="{tab}"]:visible').first
    if btn.count() == 0:
        page.locator("[data-admin-more]").click()
    page.locator(f'[data-tab="{tab}"]:visible').first.click()


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
    # The button is only usable on a deployed production/staging instance
    # (repo_sync.repo_sync_blocked_reason always refuses under pytest, since
    # tests must never touch the git repository). Simulate the deployed case:
    # this test exercises the BUTTON (it calls regenerate and reports the
    # real result honestly), not the gate itself.
    monkeypatch.setattr(repo_sync, "repo_sync_blocked_reason", lambda: None)
    calls = []

    def sync(**kwargs):
        calls.append(kwargs)
        return True, {"committed": True, "pushed": True, "branch": "test"}

    monkeypatch.setattr(repo_sync, "regenerate", sync)
    response = mobile.request.post(live_shop + "/api/admin/login",
                                   data={"email": "jaurastore@gmail.com", "password": PW})
    assert response.ok, response.text()
    mobile.goto(live_shop + "/admin.html")
    open_admin_tab(mobile, "account")
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
def test_header_controls_do_not_overlap(mobile, live_shop, width):
    """Option A (owner request 2026-09-27): menu left, logo centred, search +
    cart right - at every width the three slots stay on one row, never
    overlap, and never leave the viewport."""
    mobile.set_viewport_size({"width": width, "height": 844})
    mobile.goto(live_shop + "/shop.html")
    menu = mobile.locator("#site-header .header-slot--left [data-open-menu]")
    expect(menu).to_be_visible()
    logo = mobile.locator("#site-header .logo")
    expect(logo).to_be_visible()
    right = mobile.locator("#site-header .header-slot.nav-right")
    expect(right).to_be_visible()
    m, a, b = menu.bounding_box(), logo.bounding_box(), right.bounding_box()
    # one row: the vertical centres agree within 2px
    assert abs((m["y"] + m["height"] / 2) - (a["y"] + a["height"] / 2)) <= 2
    assert abs((a["y"] + a["height"] / 2) - (b["y"] + b["height"] / 2)) <= 2
    # menu left of logo, logo left of the controls - no overlaps
    assert m["x"] + m["width"] <= a["x"] + 1
    assert a["x"] + a["width"] <= b["x"] + 1
    for control in mobile.locator("#site-header .header-slot.nav-right button, "
                                  "#site-header .header-slot.nav-right a").all():
        box = control.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width
    # the old header dropdowns never come back
    assert mobile.locator("#site-header .lang-switch").count() == 0
    assert mobile.locator("#site-header .currency-switch").count() == 0


def test_desktop_logo_is_centered_on_one_row_and_currency_pill_floats(mobile, live_shop):
    """The Option A header (owner request 2026-09-27, supersedes the
    2026-09-11 left-logo lock): the logo sits at the horizontal CENTRE of
    the header row on desktop, on the SAME horizontal line as the
    menu/search/cart controls, the header carries NO language or currency
    dropdowns, and the floating currency pill sits pinned above the
    WhatsApp bubble."""
    width = 1440
    mobile.set_viewport_size({"width": width, "height": 900})
    mobile.goto(live_shop + "/")
    logo = mobile.locator("#site-header .logo img")
    expect(logo).to_be_visible()
    box = logo.bounding_box()
    # The logo's horizontal centre must be the header row's horizontal
    # centre (within 3px), and its vertical centre must match the
    # controls' - i.e. one row.
    row_box = mobile.evaluate(
        "(() => { const r = document.querySelector('#site-header .header .header-inner')"
        ".getBoundingClientRect(); return { left: r.left, width: r.width }; })()")
    logo_mid_x = box["x"] + box["width"] / 2
    row_mid_x = row_box["left"] + row_box["width"] / 2
    assert abs(logo_mid_x - row_mid_x) <= 3, (
        f"header logo centre {logo_mid_x} != header row centre {row_mid_x} "
        "- the Option A centred-logo lock regressed")
    right_box = mobile.locator("#site-header .header .nav-right").bounding_box()
    logo_mid = box["y"] + box["height"] / 2
    right_mid = right_box["y"] + right_box["height"] / 2
    assert abs(logo_mid - right_mid) <= 2, (
        f"logo mid {logo_mid} != controls mid {right_mid} "
        "- the header stopped being a single row")
    # No language/currency dropdowns in the header; the currency lives in the
    # floating pill (English storefront), white with a nude border.
    assert mobile.locator("#site-header .header .currency-switch").count() == 0
    assert mobile.locator("#site-header .header .lang-switch").count() == 0
    pill = mobile.locator(".cur-float")
    expect(pill).to_be_visible()
    pbox = pill.bounding_box()
    assert width - (pbox["x"] + pbox["width"]) <= 24, (
        f"the pill must hug the right edge (right:20px), box {pbox}")
    bottom_gap = 900 - (pbox["y"] + pbox["height"])
    # Owner request 2026-09-28: the pill sits clear ABOVE the WhatsApp
    # bubble (bubble bottom:20px + 58px body = a 78px top edge, so the pill
    # rests at 106px). The real non-overlap check is asserted below against
    # the measured bubble box, not against this figure.
    assert 100 <= bottom_gap <= 118, (
        f"the pill must float ~106px above the bottom (stacked clear over "
        f"WhatsApp), gap {bottom_gap}")
    pill_bg = pill.evaluate("el => getComputedStyle(el).backgroundColor")
    assert pill_bg.replace(" ", "") in ("rgb(255,255,255)",), pill_bg
    on_btn = mobile.locator('.cur-float button[data-cur="NGN"]')
    on_bg = on_btn.evaluate("el => getComputedStyle(el).backgroundColor")
    # Owner retheme 2026-09-28: the active currency is rich espresso brown,
    # NOT the retired vibrant purple. (#33251A = rgb(51, 37, 26))
    assert on_bg.replace(" ", "") == "rgb(51,37,26)", (
        f"the active currency must be #33251A, got {on_bg}")
    wa = mobile.locator(".wa-float")
    wbox = wa.bounding_box()
    assert 900 - (wbox["y"] + wbox["height"]) <= 25, (
        f"WhatsApp must hug bottom:20px, box {wbox}")
    # The rule the owner actually asked for: the two floating controls are
    # measured in a real browser and must not touch.
    assert pbox["y"] + pbox["height"] <= wbox["y"] - 10, (
        "the currency pill must sit clearly ABOVE the WhatsApp bubble "
        f"(pill {pbox}, WhatsApp {wbox})")
    # And the two golden butterflies still fly, exactly as the owner left them.
    assert mobile.locator("#site-header .header-flies .hfly").count() == 2
    mobile.locator('#site-header [data-open-search]').click()
    expect(mobile.locator('[data-search-input]')).to_be_visible()
    expect(mobile.locator('[data-search]')).to_have_count(1)


@pytest.mark.parametrize("path", ["/", "/shop.html", "/product.html"])
def test_the_floating_pill_never_covers_whatsapp_on_a_phone(mobile, live_shop, path):
    """The defect the owner photographed: on a PHONE homepage the currency
    pill was painted on top of the WhatsApp bubble (the dock-less homepage
    CSS rule out-ranked the <=640px rule, so the pill dropped to the desktop
    homepage offset while the bubble kept its phone one).

    Measured in a real browser at 390x844 on the homepage AND on the product
    grid pages: the two boxes must not intersect, the pill must sit above the
    bubble, and both must be clickable (nothing on top of either)."""
    mobile.set_viewport_size({"width": 390, "height": 844})
    mobile.goto(live_shop + path)
    pill = mobile.locator(".cur-float")
    wa = mobile.locator(".wa-float")
    expect(pill).to_be_visible()
    expect(wa).to_be_visible()
    pbox = pill.bounding_box()
    wbox = wa.bounding_box()
    assert pbox and wbox
    # No intersection at all, and the pill is the one on top.
    overlap_x = min(pbox["x"] + pbox["width"], wbox["x"] + wbox["width"]) - max(pbox["x"], wbox["x"])
    overlap_y = min(pbox["y"] + pbox["height"], wbox["y"] + wbox["height"]) - max(pbox["y"], wbox["y"])
    assert not (overlap_x > 0 and overlap_y > 0), (
        f"{path}: the currency pill overlaps the WhatsApp bubble "
        f"(pill {pbox}, WhatsApp {wbox})")
    assert pbox["y"] + pbox["height"] <= wbox["y"] - 10, (
        f"{path}: the pill must sit clearly above the bubble "
        f"(pill bottom {pbox['y'] + pbox['height']}, bubble top {wbox['y']})")
    # Both controls are genuinely reachable: the element at each centre point
    # is the control itself (or its own child), not something covering it.
    for name, box, selector in (("pill", pbox, ".cur-float"), ("WhatsApp", wbox, ".wa-float")):
        hit = mobile.evaluate(
            "([x, y, sel]) => { const el = document.elementFromPoint(x, y);"
            " return !!(el && el.closest(sel)); }",
            [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, selector])
        assert hit, f"{path}: the {name} control is covered by something else"


def test_automatic_language_and_currency_logic(mobile, live_shop):
    """The 2026-09-27 rule: the device language decides everything.

    English (Chromium's default en-US here): interface English, prices open
    in Naira FIRST, the floating pill is shown, and a tap on FCFA recalculates
    every price on screen without a reload. French (?lang=fr, the explicit
    form of what a French phone detects): the whole interface turns French,
    the storefront currency locks to CFA, the pill disappears, and checkout
    defaults to Benin CFA while keeping all three payment methods available.
    """
    # -- English default: NGN first, pill visible, tap FCFA recalculates.
    mobile.set_viewport_size({"width": 390, "height": 844})
    mobile.goto(live_shop + "/shop.html")
    assert mobile.evaluate("I18N.lang()") == "en"
    assert mobile.evaluate("JA.currency()") == "NGN"
    expect(mobile.locator(".cur-float")).to_be_visible()
    first_price = mobile.locator(".price").first
    expect(first_price).to_be_visible()
    expect(first_price).to_contain_text("₦")
    mobile.locator('.cur-float button[data-cur="CFA"]').click()
    mobile.wait_for_timeout(600)
    assert mobile.evaluate("JA.currency()") == "CFA"
    expect(mobile.locator(".price").first).to_contain_text("CFA")  # recalculated without reload
    assert mobile.locator(".price").first.evaluate(
        "el => el.isConnected"), "prices repainted in place, no navigation"
    # -- French: greeting + interface in French, FCFA locked, pill hidden.
    mobile.goto(live_shop + "/?lang=fr")
    assert mobile.evaluate("I18N.lang()") == "fr"
    assert mobile.evaluate("JA.currency()") == "CFA"
    assert mobile.evaluate("JA.currencyLocked()") is True
    pill = mobile.locator(".cur-float")
    assert pill.count() == 1 and pill.evaluate("el => el.hidden"), (
        "the floating currency pill stays hidden in French mode")
    expect(mobile.locator('.home-hero-static [data-i18n="home.kicker"]')).to_have_text(
        "Bienvenue. Prêt à faire vos achats ?")
    expect(mobile.locator(".price").first).to_contain_text("CFA")
    # setCurrency cannot talk a French storefront out of FCFA
    mobile.evaluate("JA.setCurrency('NGN')")
    assert mobile.evaluate("JA.currency()") == "CFA"
    # -- Checkout presents all three payment methods; the selected method
    # derives the order currency, rather than currency hiding a payment option.
    mobile.goto(live_shop + "/shop.html?lang=fr")
    # The storefront deliberately shows nothing until the authoritative
    # /api/catalog answer lands (store.js boot clears window.JA_SEED and awaits
    # loadSeed), so JA.products() is empty for a beat right after a navigation.
    # Wait for the live catalogue before adding to the cart, otherwise
    # JA.products()[0] is undefined and .id throws. NOTE: JA is a top-level
    # `const` (a global lexical binding, NOT a property of window), so the
    # condition must reference the bare `JA`, never `window.JA`.
    mobile.wait_for_function(
        "() => { try { return typeof JA !== 'undefined' && Array.isArray(JA.products())"
        " && JA.products().length > 0; } catch (e) { return false; } }")
    added = mobile.evaluate(
        "() => { const p = JA.products().find(p => JA.stockFor(p, '') > 0) || JA.products()[0];"
        " return JA.addToCart(p.id); }")
    assert added is True, "the server-validated cart addition should succeed"
    assert mobile.evaluate("JA.cartCount()") > 0, "the test item must be in the cart"
    mobile.goto(live_shop + "/checkout.html?lang=fr")
    expect(mobile.locator("[data-checkout]")).to_be_visible()
    expect(mobile.locator('[name=paymentMethod][value="benin_cfa"]')).to_be_checked()
    expect(mobile.locator("[data-bank-benin]")).to_be_visible()
    expect(mobile.locator("[data-bank-ngn]")).to_be_hidden()
    expect(mobile.locator("[data-bank-togo]")).to_be_hidden()
    for method in ("naira", "benin_cfa", "togo_cfa"):
        expect(mobile.locator(f'[name=paymentMethod][value="{method}"]')).to_be_visible()

    # English checkout defaults to Naira even after the French page used CFA;
    # each of the three methods remains selectable in both languages.
    mobile.goto(live_shop + "/shop.html?lang=en")
    mobile.evaluate("JA.setCurrency('NGN')")
    assert mobile.evaluate("JA.currency()") == "NGN"
    mobile.goto(live_shop + "/checkout.html?lang=en")
    expect(mobile.locator("[data-checkout]")).to_be_visible()
    assert mobile.locator('[name=paymentMethod][value="naira"]').is_checked(), (
        "English checkout defaults to the Naira method")
    expect(mobile.locator("[data-bank-ngn]")).to_be_visible()
    expect(mobile.locator("[data-bank-benin]")).to_be_hidden()
    expect(mobile.locator("[data-bank-togo]")).to_be_hidden()
    for method in ("naira", "benin_cfa", "togo_cfa"):
        expect(mobile.locator(f'[name=paymentMethod][value="{method}"]')).to_be_visible()


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
    open_admin_tab(mobile, "categories")
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
    open_admin_tab(mobile, "categories")
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
    # Language follows the device since 2026-09-27 (the header EN|FR buttons
    # are gone); I18N.setLang is the in-session override, and the ?lang= URL
    # parameter carries a language onto the next page load.
    mobile.evaluate("I18N.setLang('fr')")
    mobile.wait_for_timeout(500)
    expect(mobile.locator('[data-shop-grid]')).to_contain_text('Parfum de démonstration')
    expect(mobile.locator('[data-shop-title]').last).to_have_text('Parfum')
    mobile.goto(live_shop + '/categories.html?lang=fr')
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
    # Language is auto-detected from the device since 2026-09-27; ?lang= is
    # the explicit override the header buttons used to provide.
    mobile.goto(live_shop + '/faq.html?lang=' + language)
    assert mobile.evaluate("I18N.lang()") == language
    assert not re.search(r'admin[\s_-]*portal|portail\s+admin', mobile.locator('body').inner_text(), re.I)
