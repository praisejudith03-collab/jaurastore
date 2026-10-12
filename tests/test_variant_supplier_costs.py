"""Variant-level pricing AND supplier cost controls, end to end.

Covers spec section 4: per-option selling prices and per-option supplier
costs entered in /admin, a dedicated product-level Supplier Cost field, the
base PRICE (NAIRA ₦) becoming optional when every variant carries its own
price, and storefront cards rendering the "₦2,500 – ₦3,200" range even for a
row with no base price. Supplier costs are owner reference data: they must
persist through the full save/mirror path yet never reach a public row.
"""
import json
import os
import subprocess
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import api  # noqa: E402
import app as appmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import supabase_store  # noqa: E402
from db import execute  # noqa: E402

sys_path_tests = os.path.join(ROOT, "tests")
if sys_path_tests not in os.sys.path:
    os.sys.path.insert(0, sys_path_tests)
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"
PID = "jau-variant-cost-1"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", "/tmp/jaura_test.db")
    import db as dbmod
    dbmod.init_db()
    execute("DELETE FROM rate_limits")
    monkeypatch.setattr(catalog_mod, "_prod_source", lambda: False)
    monkeypatch.setattr(catalog_mod, "_sync_repo_async", lambda: None)
    cat_file = tmp_path / "variant-cost-catalog.json"
    cat_file.write_text(json.dumps({"products": [], "deleted": []}))
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(cat_file))
    application = appmod.create_app()
    application.config.update(TESTING=True)
    with application.test_client() as c:
        r = c.post("/api/admin/login", json={"email": EMAIL, "password": PW})
        assert r.status_code == 200, r.data
        yield c


def _csrf(client):
    r = client.get("/api/admin/session")
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def _admin_js():
    return open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()


# ---------------------------------------------------------------- persistence
def test_normalize_keeps_supplier_costs_and_folds_aliases():
    clean = catalog_mod.normalize({
        "name": "Variant Cost Dress", "priceNgn": 9000,
        "supplierCost": "1,800",
        "supplierPrice": None,
        "optionSupplierCost": '{"Size: Small": 1200, "Size: Big": 2100}',
        "options": [{"title": "Size", "values": ["Small", "Big"]}],
    })
    assert clean["supplierCost"] == 1800
    assert clean["optionSupplierCost"] == {"Size: Small": 1200, "Size: Big": 2100}


def test_normalize_supplier_cost_garbage_is_not_set_not_zero():
    clean = catalog_mod.normalize({
        "name": "No cost yet",
        "supplierCost": "", "supplier_cost": 0, "supplierPrice": "free",
    })
    assert clean["supplierCost"] is None
    assert clean["optionSupplierCost"] == {}


def test_supplier_costs_survive_a_full_save_and_are_hidden_from_the_public(client):
    csrf = _csrf(client)
    r = client.post("/api/admin/products", headers={"X-CSRF-Token": csrf}, json={
        "product": {"id": PID, "name": "Costed Purse", "sku": "VC-1",
                    "category": "accessories", "priceNgn": 9000,
                    "supplierCost": 4300,
                    "options": [{"title": "Size", "values": ["Small", "Big"]}],
                    "optionPrices": {"Size: Small": 2500, "Size: Big": 3200},
                    "optionSupplierCost": {"Size: Small": 1800, "Size: Big": 2600},
                    "stock_quantity": 4, "online": True},
    })
    assert r.status_code == 200, r.data
    saved = catalog_mod.merged(include_hidden=True)
    row = next(p for p in saved if p["id"] == PID)
    assert row["supplierCost"] == 4300
    assert row["optionSupplierCost"] == {"Size: Small": 1800, "Size: Big": 2600}
    assert row["optionPrices"] == {"Size: Small": 2500, "Size: Big": 3200}

    public = client.get("/api/catalog").get_json()
    entry = next(p for p in public["products"] if p["id"] == PID)
    assert "supplierCost" not in entry and "supplier_cost" not in entry
    assert "optionSupplierCost" not in entry and "option_supplier_cost" not in entry
    # The shopper-facing override prices DO ride along (they drive the range).
    assert entry["optionPrices"] == {"Size: Small": 2500, "Size: Big": 3200}


def test_supabase_layer_persists_both_supplier_cost_columns():
    assert "supplierCost" in supabase_store._CRITICAL_PRODUCT_COLUMNS
    assert "optionSupplierCost" in supabase_store._CRITICAL_PRODUCT_COLUMNS
    for alias in ("supplier_cost", "supplierPrice", "supplier_price",
                  "option_supplier_cost", "optionSupplierCosts",
                  "variantSupplierCosts", "optionCosts"):
        assert alias in supabase_store._PRODUCT_PATCH_COLUMNS, alias
        assert supabase_store._PRODUCT_PATCH_COLUMNS[alias] <= (
            {"supplierCost"} | {"optionSupplierCost"})


def test_product_row_normalization_folds_cost_aliases():
    row = supabase_store._canonicalize_product({
        "id": "x", "supplier_cost": 2500,
        "option_supplier_costs": {"Colour: Pink": 1800},
    })
    assert row["supplierCost"] == 2500
    assert row["optionSupplierCost"] == {"Colour: Pink": 1800}


def test_schema_files_carry_the_new_columns():
    for path in ("supabase_schema.sql", os.path.join("schema_sections", "01_products.sql")):
        sql = open(os.path.join(ROOT, path), encoding="utf-8").read()
        assert 'add column if not exists "supplierCost" numeric' in sql, path
        assert 'add column if not exists "optionSupplierCost" jsonb' in sql, path
        assert "add column if not exists is_deleted boolean not null default false" in sql, path
    migration = open(os.path.join(ROOT, "migrate_variant_pricing_and_carts.sql"),
                     encoding="utf-8").read()
    assert '"supplierCost"' in migration and '"optionSupplierCost"' in migration


# ------------------------------------------------- base price optional (spec 4)
def test_variant_priced_product_saves_without_a_base_price(client):
    csrf = _csrf(client)
    r = client.post("/api/admin/products", headers={"X-CSRF-Token": csrf}, json={
        "product": {"id": PID, "name": "No Base Needed", "sku": "VC-2",
                    "category": "accessories", "priceNgn": 0,
                    "options": [{"title": "Size", "values": ["Small", "Big"]}],
                    "optionPrices": {"Size: Small": 2500, "Size: Big": 3200},
                    "stock_quantity": 6, "online": True},
    })
    assert r.status_code == 200, r.data
    row = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == PID)
    assert row["priceNgn"] == 0
    assert row["optionPrices"] == {"Size: Small": 2500, "Size: Big": 3200}
    # And the public row still serves the variant prices that draw the range.
    public = client.get("/api/catalog").get_json()
    entry = next(p for p in public["products"] if p["id"] == PID)
    assert entry["optionPrices"] == {"Size: Small": 2500, "Size: Big": 3200}


# --------------------------------------------------------------- admin editor
def test_admin_product_form_has_supplier_cost_and_variant_price_fields():
    src = _admin_js()
    start = src.index("function productForm(p = {})")
    end = src.index("async function handleProductSubmit", start)
    form = src[start:end]
    # Dedicated Supplier Cost display field on the product form (spec 4).
    assert "<label>Supplier cost (Naira ₦)</label>" in form
    assert 'name="supplierCost"' in form
    assert "shoppers never see" in src  # internal-only wording
    # Per-variant selling price + supplier cost inputs.
    assert 'data-opt-price=' in src
    assert 'data-opt-cost=' in src
    assert "Selling price per option" in src
    assert "Supplier cost per option" in src


def test_admin_base_price_is_optional_only_when_every_variant_is_priced():
    src = _admin_js()
    submit = src.split("async function handleProductSubmit(e, existing) {", 1)[1]
    submit = submit.split("\nasync function ", 1)[0]
    # The old unconditional gate is gone; the new one asks the variants.
    assert 'if (!(priceNgn > 0)) { JA.toast("Enter the Naira price."); return; }' not in submit
    assert "variantsAllPriced" in submit
    assert "Enter a price for every option, or the base Naira price." in submit
    # A cleared base never wipes the saved base price of a variant-priced row.
    assert "existing?.priceNgn" in submit


def test_admin_dirty_field_tracking_covers_the_new_fields():
    src = _admin_js()
    assert 'supplierCost: ["supplierCost"]' in src
    assert '["data-opt-price", ["optionPrices"]]' in src
    assert '"data-opt-cost"' in src and '"optionSupplierCost"' in src
    # The variant-chip repaint must re-collect typed prices/costs BEFORE it
    # replaces the DOM, or every figure the admin typed would be lost.
    chips = src.split("function refreshOptionChips()", 1)[1].split("\nfunction ", 1)[0]
    assert "currentOptionPrices()" in chips
    assert "currentOptionSupplierCosts()" in chips


def test_variant_chips_repaint_keeps_typed_prices_and_costs():
    """The real collectors + repaint merge, executed in a Node VM."""
    script = textwrap.dedent(r"""
        import { readFileSync } from "node:fs";
        import vm from "node:vm";
        const src = readFileSync("js/admin.js", "utf8");
        const names = ["activeOptionKeys", "keepActiveOptionMap",
                       "optionStockValues", "optionPricesHTML",
                       "currentOptionPrices", "optionSupplierCostsHTML",
                       "currentOptionSupplierCosts", "refreshPriceOptionality"];
        let code = "";
        for (const name of names) {
          const start = src.indexOf(`function ${name}(`);
          if (start < 0) { console.error("missing fn " + name); process.exit(1); }
          // Slice to the next top-level "function " marker (functions in this
          // file are separated by a blank line and a fresh "function").
          let end = src.indexOf("\nfunction ", start + 1);
          if (end < 0) end = src.length;
          code += src.slice(start, end) + "\n";
        }
        const rows = [];
        const makeRow = (attrs, html) => ({
          attrs, html,
          querySelector() { return null; },
          querySelectorAll() { return []; },
        });
        const inputs = [];
        const priceInput = (key, value) => {
          const el = { value, getAttribute: (a) => a === "data-opt-price" ? key : null,
                       closest() { return null; }, tagName: "INPUT" };
          inputs.push(el);
          return el;
        };
        const costInput = (key, value) => {
          const el = { value, getAttribute: (a) => a === "data-opt-cost" ? key : null,
                       closest() { return null; }, tagName: "INPUT" };
          inputs.push(el);
          return el;
        };
        const sandbox = {
          console, document: {
            querySelectorAll(sel) {
              if (sel === "[data-opt-price]") return inputs.filter((i) => i.getAttribute("data-opt-price"));
              if (sel === "[data-opt-cost]") return inputs.filter((i) => i.getAttribute("data-opt-cost"));
              return [];
            },
            getElementById() { return null; },
            querySelector() { return null; },
          },
          window: {},
          editingId: "x", JA: { escape: (s) => String(s) },
        };
        sandbox.window = sandbox; sandbox.globalThis = sandbox;
        vm.createContext(sandbox);
        vm.runInContext(code, sandbox, { filename: "admin-extract.js" });
        priceInput("Size: Small", "2500");
        priceInput("Size: Big", "3200");
        costInput("Size: Small", "1200");
        costInput("Size: Big", "");
        const prices = vm.runInContext("currentOptionPrices()", sandbox);
        const costs = vm.runInContext("currentOptionSupplierCosts()", sandbox);
        const html = vm.runInContext(
          `optionPricesHTML({ options: [{ title: "Size", values: ["Small", "Big"] }], optionPrices: { "Size: Small": 2500, "Size: Big": 3200 } })`,
          sandbox);
        if (prices["Size: Small"] !== 2500 || prices["Size: Big"] !== 3200) {
          console.error("prices: " + JSON.stringify(prices)); process.exit(1);
        }
        if (costs["Size: Small"] !== 1200 || "Size: Big" in costs) {
          console.error("costs: " + JSON.stringify(costs)); process.exit(1);
        }
        if (!html.includes('data-opt-price="Size: Small"') ||
            !html.includes("Selling price per option") ||
            !html.includes("Base price")) {
          console.error("optionPricesHTML: " + html.slice(0, 200)); process.exit(1);
        }
        const costHtml = vm.runInContext(
          `optionSupplierCostsHTML({ options: [{ title: "Size", values: ["Small"] }], optionSupplierCost: { "Size: Small": 1200 } })`,
          sandbox);
        if (!costHtml.includes('data-opt-cost="Size: Small"') ||
            !costHtml.includes("Supplier cost per option")) {
          console.error("optionSupplierCostsHTML: " + costHtml.slice(0, 200)); process.exit(1);
        }
        console.log("OK");
    """)
    proc = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                          text=True, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


# ------------------------------------------------------------- storefront (4)
def test_storefront_range_renders_for_a_variant_priced_row_without_a_base():
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
          localStorage: { getItem(k) { return k === "jaura_fx" ? JSON.stringify({ ngnToXof: 2.5, rate: 2.5 }) : null; }, setItem() {}, removeItem() {} },
          sessionStorage: { getItem() { return null; }, setItem() {} },
          navigator: { language: "en" },
          location: { href: "https://example.test/", origin: "https://example.test", protocol: "https:", host: "example.test", pathname: "/", search: "" },
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
          id: "no-base", priceNgn: 0, priceCfa: 0,
          options: [{ title: "Size", values: ["Small", "Big"] }],
          optionPrices: { "Size: Small": 2500, "Size: Big": 3200 },
        };
        const out = {
          range: JA.priceRangeOf(product, "NGN"),
          html: JA.priceHTML(product),
          from: JA.priceOf(product, "NGN"),
          cur: JA.displayCur(product, "NGN"),
          hasNgn: JA.hasNgn ? JA.hasNgn(product) : null,
          cheapestSort: JA.priceOf(product, "CFA"),
        };
        if (!out.range || out.range.min !== 2500 || out.range.max !== 3200) {
          console.error(JSON.stringify(out)); process.exit(1);
        }
        if (out.from !== 2500 || out.cur !== "NGN") {
          console.error(JSON.stringify(out)); process.exit(1);
        }
        if (!out.html.includes("₦2,500.00") || !out.html.includes("₦3,200.00")) {
          console.error("html: " + out.html); process.exit(1);
        }
        // Currency routing: a variant-priced row is NGN-anchored, so CFA is a
        // conversion of the exact Naira figure, not a zero-price product.
        if (out.cheapestSort <= 0) { console.error("CFA fallback zero"); process.exit(1); }
        console.log("OK");
    """)
    proc = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                          text=True, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


def test_pdp_variant_price_switch_uses_the_display_currency():
    app_js = open(os.path.join(ROOT, "js", "app.js"), encoding="utf-8").read()
    assert "JA.displayCur ? JA.displayCur(p)" in app_js
    # The old forced-CFA fallback for unpriced rows is gone.
    assert 'const cur = Number(p.priceNgn) > 0 ? JA.currency() : "CFA";' not in app_js
