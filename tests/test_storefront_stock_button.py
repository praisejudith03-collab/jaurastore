"""The customer-facing half of the stock guardrail and the cart button.

`stockFor` is what every Add to cart / +1 tap consults. Two rules must hold on
the way in:

  * an explicit whole-product OFF wins over any leftover quantity, so a
    product the owner switched off can never be added;
  * the server's per-variant in/out map still decides each colour, and a
    variant it marks "out" reads as zero however the product-level number is
    spelled.

The tests run the real js/store.js in node with a stub DOM (no browser
download), the same way tests/test_photo_fix.py does.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

_PRELUDE = r"""
const fs = require("fs");
const vm = require("vm");
const ROOT = process.cwd();
process.on("unhandledRejection", () => {});
const store = new Map();
const sandbox = {
  document: { body: { dataset: {} }, documentElement: {}, head: {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    querySelector: () => null, querySelectorAll: () => [],
    createElement: () => ({ dataset: {}, style: {}, classList: { add() {}, remove() {} },
      addEventListener() {}, appendChild() {}, setAttribute() {}, getAttribute: () => null }),
    createTextNode: (t) => ({ text: t }) },
  localStorage: { getItem: (k) => (store.has(String(k)) ? store.get(String(k)) : null),
    setItem: (k, v) => store.set(String(k), String(v)), removeItem: (k) => store.delete(String(k)),
    clear: () => store.clear() },
  console, URL, URLSearchParams, Response, Request, Headers, TextEncoder, TextDecoder,
  AbortSignal, AbortController,
  CustomEvent: class { constructor(t, i) { this.type = t; this.detail = i && i.detail; } },
  fetch: () => Promise.reject(new Error("offline")),
  navigator: { onLine: true, sendBeacon: () => true, userAgent: "node-harness" },
  location: { href: "https://jaurastore.com.ng/shop.html", origin: "https://jaurastore.com.ng",
    pathname: "/shop.html", search: "" },
  setTimeout, clearTimeout, setInterval: () => 0, clearInterval,
  requestAnimationFrame: (cb) => setTimeout(() => cb(Date.now()), 0),
  addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
  matchMedia: () => ({ matches: false, addListener() {}, removeListener() {} }),
  scrollTo() {}, scrollY: 0,
};
sandbox.window = sandbox; sandbox.self = sandbox; sandbox.globalThis = sandbox;
const ctx = vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(ROOT + "/js/store.js", "utf8") + "\n;globalThis.__JA = JA;",
  ctx, { filename: "js/store.js" });
global.emit = (o) => process.stdout.write("__RESULT__" + JSON.stringify(o) + "\n",
  () => process.exit(0));
global.JA = sandbox.__JA;
global.sandbox = sandbox;
global.runInCtx = (src, name) => vm.runInContext(src, ctx, { filename: name });
global.loadApp = () => runInCtx(fs.readFileSync(ROOT + "/js/app.js", "utf8"), "js/app.js");
"""


def _node(script, timeout=180):
    assert NODE, "node is required by these tests but is not on PATH"
    proc = subprocess.run([NODE, "-"], input=script, cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout)
    assert proc.returncode == 0, (
        f"node harness exited {proc.returncode}\n--- stderr ---\n{proc.stderr[-3000:]}\n"
        f"--- stdout ---\n{proc.stdout[-1500:]}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("__RESULT__")]
    assert lines, f"harness emitted no result\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
    return json.loads(lines[-1][len("__RESULT__"):])


def test_an_explicit_off_product_is_never_sellable_even_with_units_left():
    """stock_status "out" + a leftover quantity still means zero available."""
    out = _node(_PRELUDE + r"""
const off = { id: "off-1", name: "Switched off", priceNgn: 5000, stock: 12,
              stock_status: "out" };
const inStock = { id: "in-1", name: "Live", priceNgn: 5000, stock: 3, stock_status: "in" };
emit({
  off: JA.stockFor(off, ""),
  offLeft: JA.stockLeft(off, ""),
  live: JA.stockFor(inStock, ""),
  zero: JA.stockFor({ id: "z", stock: 0, stock_status: "out" }, ""),
});
""")
    assert out["off"] == 0, "an explicit OFF must win over a positive quantity"
    assert out["offLeft"] == 0
    assert out["live"] > 0, "a live product must stay sellable"
    assert out["zero"] == 0


def test_a_variant_the_server_marks_out_reads_zero():
    """Through the real server -> client translation, not a hand-built row.

    A status-only legacy response stays supported; current server responses
    additionally provide exact per-variant quantities for stockFor.
    """
    out = _node(_PRELUDE + r"""
const raw = { id: "v-1", name: "Variant", price_ngn: 9000, priceNgn: 9000,
              stock_status: "in",
              option_stock_status: { Red: "in", Blue: "out" } };
const p = JA.normalizeServerProduct(raw);
emit({
  normalized: { stock: p.stock, status: p.optionStockStatus },
  red: JA.stockFor(p, "Red"),
  blue: JA.stockFor(p, "Blue"),
  redLeft: JA.stockLeft(p, "Red"),
  blueLeft: JA.stockLeft(p, "Blue"),
});
""")
    assert out["normalized"]["status"] == {"Red": 9999, "Blue": 0}, out["normalized"]
    assert out["red"] > 0
    assert out["blue"] == 0, "an out-of-stock variant must not be addable"
    assert out["blueLeft"] == 0


def test_unmapped_variants_do_not_borrow_the_product_total():
    """An absent allocation is zero, even if another option has units."""
    out = _node(_PRELUDE + r"""
const partial = JA.normalizeServerProduct({
  id: "partial-1", name: "Partial", priceNgn: 5000,
  stock_available: 2, option_stock_available: { Red: 2 },
  option_stock_status: { Red: "in" },
  options: [{ title: "Colour", type: "COLOR", values: ["Red", "Blue"] }],
});
const unassigned = JA.normalizeServerProduct({
  id: "unassigned-1", name: "Unassigned", priceNgn: 5000,
  stock_available: 0, stock_status: "out",
  options: [{ title: "Colour", type: "COLOR", values: ["Red", "Blue"] }],
});
emit({
  red: JA.stockFor(partial, "Colour: Red"),
  blue: JA.stockFor(partial, "Colour: Blue"),
  unassignedTotal: JA.stockFor(unassigned, ""),
  unassignedVariant: JA.stockFor(unassigned, "Colour: Red"),
});
""")
    assert out == {"red": 2, "blue": 0, "unassignedTotal": 0, "unassignedVariant": 0}


def test_a_whole_product_the_server_reports_out_is_unsellable_after_translation():
    out = _node(_PRELUDE + r"""
const p = JA.normalizeServerProduct({ id: "o-1", name: "Out", priceNgn: 4000,
                                      stock_status: "out" });
emit({ stock: p.stock, forZero: JA.stockFor(p, ""), left: JA.stockLeft(p, "") });
""")
    assert out["stock"] == 0 and out["forZero"] == 0 and out["left"] == 0


def test_the_pdp_fallback_disables_buy_for_a_sold_out_product():
    """The emergency PDP path paints its own markup: it must not offer Add.

    renderProduct's catch branch is exactly what a shopper sees when the full
    page failed, so a sold-out product must not be the one case where the
    button stays live.
    """
    out = _node(_PRELUDE + r"""
loadApp();
const sold = { id: "sold-1", name: "Gone", priceNgn: 5000, priceCfa: 3000, stock: 0,
               stock_status: "out", category: "home",
               image: "images/products/x.jpg", images: ["images/products/x.jpg"] };
JA.product = () => sold;
sandbox.location.search = "?id=sold-1";
sandbox.paintProduct = () => { throw new Error("boom"); };
const root = { innerHTML: "", querySelector: () => null,
  addEventListener() {}, dataset: {} };
sandbox.document.querySelector = (sel) => (sel === "[data-pdp]" ? root : null);
const realError = console.error; console.error = () => {};
sandbox.renderProduct();
console.error = realError;
emit({ html: root.innerHTML });
""")
    html = out["html"]
    assert "data-buy" in html, html
    assert 'data-buy disabled' in html or 'data-buy  disabled' in html, \
        f"a sold-out product's fallback buy button must be disabled: {html}"


def test_the_pdp_fallback_keeps_buy_live_for_a_sellable_product():
    out = _node(_PRELUDE + r"""
loadApp();
const live = { id: "live-1", name: "Live", priceNgn: 5000, priceCfa: 3000, stock: 4,
               stock_status: "in", category: "home",
               image: "images/products/x.jpg", images: ["images/products/x.jpg"] };
JA.product = () => live;
sandbox.location.search = "?id=live-1";
sandbox.paintProduct = () => { throw new Error("boom"); };
const root = { innerHTML: "", querySelector: () => null,
  addEventListener() {}, dataset: {} };
sandbox.document.querySelector = (sel) => (sel === "[data-pdp]" ? root : null);
const realError = console.error; console.error = () => {};
sandbox.renderProduct();
console.error = realError;
emit({ html: root.innerHTML });
""")
    html = out["html"]
    assert 'data-buy disabled' not in html, f"a live product must stay buyable: {html}"
    assert "data-buy" in html


def test_public_exact_stock_caps_the_product_selector_and_shows_inline_limit():
    out = _node(_PRELUDE + r"""
loadApp();
const el = (tag) => ({ tagName: tag, dataset: {}, classList: { add() {}, remove() {}, contains() { return false; } },
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  setAttribute() {}, getAttribute() { return null; }, appendChild() {}, focus() {} });
const p = JA.normalizeServerProduct({
  id: "exact-simple", name: "Exact Stock", priceNgn: 5000,
  stock_status: "in", stock_available: 5,
  images: ["images/products/test.jpg"],
});
sandbox.JA_SEED = [p];
const events = (node) => {
  node.handlers = {};
  node.addEventListener = (type, fn) => { node.handlers[type] = fn; };
  return node;
};
const qty = events(el("input")); qty.value = "1";
const minus = events(el("button")); minus.dataset.q = "-";
const plus = events(el("button")); plus.dataset.q = "+";
const stockLine = el("p");
const inlineError = el("p"); inlineError.hidden = true;
const buy = el("button");
const nodes = {
  "[data-qty]": qty, "[data-stock-line]": stockLine,
  "[data-qty-stock-error]": inlineError, "[data-buy]": buy,
};
const root = el("div");
root.querySelector = (selector) => nodes[selector] || null;
root.querySelectorAll = (selector) => selector === "[data-q]" ? [minus, plus] : [];
sandbox.paintProduct(root, p);
const initialMax = qty.max;
qty.value = "6";
qty.handlers.input();
emit({
  stock: JA.stockFor(p, ""), initialMax, max: qty.max, value: qty.value,
  message: inlineError.textContent, hidden: inlineError.hidden,
  buyDisabled: buy.disabled,
});
""")
    assert out["stock"] == 5
    assert out["initialMax"] == "5"
    assert out["max"] == "5" and out["value"] == "5"
    assert out["message"] == "Only 5 items remaining in stock"
    assert out["hidden"] is False
    assert out["buyDisabled"] is False


def test_variant_quantity_cap_and_zero_stock_disable_add_to_cart():
    out = _node(_PRELUDE + r"""
loadApp();
const el = (tag) => ({ tagName: tag, dataset: {}, classList: { add() {}, remove() {}, contains() { return false; } },
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  setAttribute() {}, getAttribute() { return null; }, appendChild() {}, focus() {} });
const p = JA.normalizeServerProduct({
  id: "exact-variants", name: "Variant Stock", priceNgn: 5000,
  stock_status: "in", stock_available: 2,
  option_stock_status: { Red: "in", Blue: "out" },
  option_stock_available: { Red: 2, Blue: 0 },
  options: [{ title: "Colour", type: "COLOR", values: ["Red", "Blue"] }],
  images: ["images/products/test.jpg"],
});
sandbox.JA_SEED = [p];
const tracked = (node) => {
  node.handlers = {};
  node.addEventListener = (type, fn) => { node.handlers[type] = fn; };
  return node;
};
const qty = tracked(el("input")); qty.value = "1";
const minus = tracked(el("button")); minus.dataset.q = "-";
const plus = tracked(el("button")); plus.dataset.q = "+";
const red = tracked(el("button")); red.dataset.opt = "0"; red.dataset.val = "Red";
const blue = tracked(el("button")); blue.dataset.opt = "0"; blue.dataset.val = "Blue";
const optionButtons = [red, blue];
const stockLine = el("p");
const inlineError = el("p"); inlineError.hidden = true;
const buy = el("button");
const nodes = {
  "[data-qty]": qty, "[data-stock-line]": stockLine,
  "[data-qty-stock-error]": inlineError, "[data-buy]": buy,
};
const root = el("div");
root.querySelector = (selector) => nodes[selector] || null;
root.querySelectorAll = (selector) => {
  if (selector === "[data-q]") return [minus, plus];
  if (selector === "[data-opt]") return optionButtons;
  if (selector === "[data-opt=\"0\"]") return optionButtons;
  return [];
};
sandbox.paintProduct(root, p);
red.handlers.click();
const redMax = qty.max;
blue.handlers.click();
emit({
  redStock: JA.stockFor(p, "Red"), redMax,
  blueStock: JA.stockFor(p, "Blue"), blueMax: qty.max,
  blueDisabled: qty.disabled, buyDisabled: buy.disabled, stockText: stockLine.textContent,
});
""")
    assert out["redStock"] == 2 and out["redMax"] == "2"
    assert out["blueStock"] == 0 and out["blueMax"] == "1"
    assert out["blueDisabled"] is True and out["buyDisabled"] is True
    assert out["stockText"] == "Out of Stock"


def test_product_save_timeout_queue_is_quiet_not_a_failure_toast():
    out = _node(_PRELUDE + r"""
const notices = [];
JA.toast = (message) => notices.push(String(message));
sandbox.JA_NET = {
  api: (_url, options) => Promise.resolve({ queued: true, options }),
};
JA.upsertProduct({ id: "quiet-queue", name: "Quiet queue", stock: 1 })
  .then((result) => emit({ result, notices }));
""")
    assert out["result"]["queued"] is True
    assert out["result"]["silent"] is True
    assert out["notices"] == []
    store = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    admin = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert "The change is queued and will retry" not in store
    assert "if (res && res.queued)" in admin
