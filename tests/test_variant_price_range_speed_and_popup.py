"""Full-stack checks for variant pricing, fast catalogue reads and popup ON/OFF.

These tests exercise the public API plus the exact browser helpers shipped in
js/store.js.  They intentionally use the real Flask app and a small Node VM
rather than duplicating pricing logic in Python.
"""
import json
import os
import subprocess
import textwrap
from unittest.mock import patch

import api
import app as appmod
import catalog as catalog_mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _node(script):
    return subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                          text=True, capture_output=True, timeout=30)


def test_variant_range_helper_and_exact_variant_stock_contract():
    """The real storefront helper returns a min/max range and exact stock."""
    script = textwrap.dedent(r"""
        import { readFileSync } from "node:fs";
        import vm from "node:vm";
        const source = readFileSync("js/store.js", "utf8");
        const listeners = new Map();
        const noopClassList = { add() {}, remove() {}, toggle() {} };
        const sandbox = {
          console, setTimeout, clearTimeout, clearInterval,
          setInterval() { return 0; },
          AbortSignal, Date, Math, JSON, Number, String, Array, Object,
          Boolean, Set, Map, Promise,
          localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
          sessionStorage: { getItem() { return null; }, setItem() {} },
          navigator: { language: "en" },
          location: { href: "https://example.test/product.html", origin: "https://example.test", protocol: "https:", host: "example.test", pathname: "/product.html", search: "" },
          CustomEvent: class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
          requestAnimationFrame(fn) { return setTimeout(fn, 0); },
          fetch() { return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, products: [], meta: {} }) }); },
          addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
        };
        sandbox.document = {
          body: { dataset: { page: "shop" }, classList: noopClassList },
          documentElement: { dataset: {} },
          querySelector() { return null; }, querySelectorAll() { return []; },
          createElement() { return { style: {}, dataset: {}, setAttribute() {}, appendChild() {} }; },
          head: { appendChild() {} },
          addEventListener(type, fn) { listeners.set(type, fn); },
          dispatchEvent(ev) { const fn = listeners.get(ev.type); if (fn) fn(ev); return true; },
        };
        sandbox.window = sandbox; sandbox.globalThis = sandbox;
        vm.createContext(sandbox);
        vm.runInContext(source, sandbox, { filename: "js/store.js" });
        const JA = vm.runInContext("JA", sandbox);
        const product = {
          id: "variant-demo", priceNgn: 2000, stock: 9,
          options: [{ title: "Colour", values: ["Red", "Blue"] }],
          optionPrices: { "Colour: Red": 1800, "Colour: Blue": 2500 },
          optionStockStatus: { Red: 0, Blue: 9999 },
        };
        const range = JA.priceRangeOf(product, "NGN");
        const html = JA.priceHTML(product);
        const result = {
          range,
          html,
          red: JA.priceOf(product, "NGN", "Colour: Red"),
          blue: JA.priceOf(product, "NGN", "Colour: Blue"),
          redStock: JA.stockFor(product, "Red"),
          blueStock: JA.stockFor(product, "Blue"),
        };
        if (!range || range.min !== 1800 || range.max !== 2500 ||
            result.red !== 1800 || result.blue !== 2500 ||
            result.redStock !== 0 || result.blueStock <= 0 ||
            !html.includes("₦1,800.00") || !html.includes("₦2,500.00") ||
            !html.includes("₦1,800") || !html.includes("₦2,500") || !html.includes("data-price-state=\"range\"")) {
          console.error(JSON.stringify(result)); process.exit(1);
        }
        // The plain-text range label is exactly the formatted requirement:
        // "₦1,800.00 – ₦2,500.00".
        const label = JA.moneyRange(range, "NGN");
        if (label !== "₦1,800.00 – ₦2,500.00") {
          console.error("moneyRange: " + label); process.exit(1);
        }
    """)
    proc = _node(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_pdp_selection_markup_switches_price_and_announces_live_stock():
    app_js = open(os.path.join(ROOT, "js", "app.js"), encoding="utf-8").read()
    css = open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8").read()
    assert 'data-stock-line role="status" aria-live="polite"' in app_js
    assert 'priceEl.dataset.priceState = "exact"' in app_js
    assert 'stockLine.textContent = "In Stock"' in app_js
    assert 'stockLine.textContent = "Out of Stock"' in app_js
    # The final mobile overrides must win over the older compact-card layers.
    assert '.pdp .price,' in css and 'font-weight: 800 !important' in css
    assert '.just-in-grid .price,' in css and 'font-size: 12px !important' in css


def test_catalog_and_category_read_through_caches_eliminate_repeat_queries(monkeypatch):
    """Server-side response snapshots make repeated page transitions cheap."""
    app = appmod.create_app()
    app.config.update(TESTING=True)
    monkeypatch.setattr(api, "_cache_enabled", lambda: True)
    api._invalidate_catalog_cache()
    api._invalidate_category_cache()

    original_merged = catalog_mod.merged
    with patch.object(catalog_mod, "merged", wraps=original_merged) as merged:
        with app.test_client() as client:
            first = client.get("/api/catalog")
            calls_after_first = merged.call_count
            second = client.get("/api/catalog")
        assert first.status_code == second.status_code == 200
        # Building one snapshot may resolve homepage feature fallbacks too;
        # the second response must add zero database/catalogue reads.
        assert calls_after_first >= 1
        assert merged.call_count == calls_after_first
        assert "no-cache" in first.headers["Cache-Control"]
        assert first.headers["ETag"] == second.headers["ETag"]

    original_categories = api._categories_data_uncached
    with patch.object(api, "_categories_data_uncached", wraps=original_categories) as categories:
        with app.test_client() as client:
            first = client.get("/api/categories")
            second = client.get("/api/categories")
        assert first.status_code == second.status_code == 200
        assert categories.call_count == 1
        assert "max-age=60" in first.headers["Cache-Control"]

    api._invalidate_catalog_cache()
    api._invalidate_category_cache()


def test_popup_active_toggle_persists_and_storefront_guard_uses_it(tmp_path, monkeypatch):
    """Admin OFF is a database/API value and stops modal rendering client-side."""
    site_file = tmp_path / "site.json"
    monkeypatch.setenv("SITE_CONFIG_PATH", str(site_file))
    # api reads this environment in its testing helper on each request.
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as client:
        login = client.post("/api/admin/login", json={
            "email": "jaurastore@gmail.com", "password": os.environ["ADMIN_BOOTSTRAP_PASSWORD"],
        })
        assert login.status_code == 200
        token = login.get_json()["csrf"]
        saved = client.post("/api/admin/site", headers={"X-CSRF-Token": token},
                            json={"popup_banner_active": False, "welcome_enabled": "0"})
        assert saved.status_code == 200, saved.data
        assert saved.get_json()["site"]["popup_banner_active"] is False
        served = client.get("/api/site").get_json()["site"]
        assert served["popup_banner_active"] is False

    row = json.loads(site_file.read_text(encoding="utf-8"))
    assert row["popup_banner_active"] is False
    store_js = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert "popup_banner_active" in store_js
    assert '!welcomeEnabled()) return' in store_js
    assert 'patch.popup_banner_active = !!(enabled && enabled.checked)' in admin_js
    assert 'role="switch"' in admin_js


def test_product_save_returns_admin_to_their_captured_list_state():
    """"Save Product" sends each admin back to THEIR exact source page.

    The list position (category, search, page, scroll) is captured in
    sessionStorage when an editor is opened - the browser scopes that store
    PER TAB, so several admins working simultaneously each return to their
    own list instead of sharing one global view.
    """
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    submit = admin_js.split("async function handleProductSubmit(e, existing) {", 1)[1].split("\nlet prodPage", 1)[0]
    # A successful save leaves the editor and restores the captured position.
    assert "restoreProductsReturn();" in submit
    assert "editingId = String((res && res.data" not in submit
    # The capture is per-tab, not a shared cookie/localStorage slot.
    assert 'sessionStorage.setItem(PRODUCTS_RETURN_KEY' in admin_js
    assert 'sessionStorage.getItem(PRODUCTS_RETURN_KEY)' in admin_js
    assert "localStorage" not in admin_js.split("PRODUCTS_RETURN_KEY =")[1].split("function productsStateFromUrl")[0]
    # Opening an editor captures the current list URL state.
    assert "rememberProductsReturn();" in admin_js
    assert "editingId = b.dataset.edit;" in admin_js
    assert 'editingId = "new";' in admin_js
    # The canonical state URL matches /admin/products?category=...&page=...
    assert 'return "/admin/products" + (qs ? "?" + qs : "");' in admin_js
    # A deep-linked return_url (or its loose un-encoded spelling) reopens the
    # exact list position after a reload.
    assert "applyAdminDeepLink" in admin_js
    assert 'search.get("return_url")' in admin_js
    # Cancel and in-editor delete return to the same captured position.
    assert '$("#cancel-edit")?.addEventListener("click", () => { restoreProductsReturn(); });' in admin_js
