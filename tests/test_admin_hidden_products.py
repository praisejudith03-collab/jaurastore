"""Hidden / Drafts desk: one place to restore a hidden product, plus counters.

Owner directive 2026-10-20: hidden and draft products need their own admin
filter so they can be un-hidden in one tap, and the catalogue counters must
always state the full picture ("289 Products: 283 Active, 6 Hidden") so no
item ever feels missing or untracked.

Run with: python3 -m pytest tests/test_admin_hidden_products.py -q
"""
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _admin():
    return open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()


def _css():
    return open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8").read()


_NODE_CHECK = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("js/admin.js", "utf8");
function grab(name) {
  const start = source.indexOf("function " + name + "(");
  if (start < 0) throw new Error("missing " + name);
  let depth = 0, at = source.indexOf("{", start);
  for (; at < source.length; at++) {
    if (source[at] === "{") depth++;
    else if (source[at] === "}" && --depth === 0) return source.slice(start, at + 1);
  }
  throw new Error("unterminated " + name);
}
const rows = [
  { id: "live-1", category: "bags", name: "Purse", online: true },
  { id: "live-2", category: "bags", name: "Tote" },
  { id: "hidden-1", category: "bags", name: "Draft bag", online: false },
  { id: "hidden-2", category: "beauty", name: "Draft cream", online: false },
];
const controls = { "prod-search": { value: "" }, "prod-cat": { value: "" } };
const sandbox = {
  JA: { products: () => rows, categories: () => [{ id: "bags" }, { id: "beauty" }] },
  document: { getElementById: (id) => controls[id] || null },
  selectedProductIds: new Set(),
  prodPage: 1, prodSearchQ: "", prodCatSel: "", dashCat: "",
  prodView: "all", PRODUCT_VIEWS: ["all", "live", "hidden"],
  paintDesk: () => {}, renderProdGrid: () => {}, bindProdGridEvents: () => {},
  URLSearchParams,
};
vm.createContext(sandbox);
vm.runInContext(["getFilteredProducts", "productsStateFromUrl", "applyProductsState"]
  .map(grab).join("\n"), sandbox);
function ids() { return Array.from(vm.runInContext("getFilteredProducts().map(p => p.id)", sandbox)); }
function check(c, m) { if (!c) throw new Error(m); }

check(JSON.stringify(ids()) === JSON.stringify(["live-1", "live-2", "hidden-1", "hidden-2"]),
  "the All view lists the whole catalogue");
vm.runInContext("prodView = 'hidden'", sandbox);
check(JSON.stringify(ids()) === JSON.stringify(["hidden-1", "hidden-2"]),
  "the Hidden / Drafts view collects exactly the hidden rows");
vm.runInContext("prodView = 'live'", sandbox);
check(JSON.stringify(ids()) === JSON.stringify(["live-1", "live-2"]),
  "the Live view is the complement, so the two add up to the whole catalogue");
check(ids().length + 2 === rows.length, "live + hidden must equal the total");

// The view is part of the captured return state, so hiding a product from the
// Hidden tab and saving comes back to the Hidden tab.
vm.runInContext("prodView = 'hidden'", sandbox);
const parsed = sandbox.productsStateFromUrl("/admin/products?view=hidden&page=2");
check(parsed.view === "hidden" && parsed.page === 2, "the view survives a return URL");
sandbox.applyProductsState(parsed);
check(sandbox.prodView === "hidden", "applyProductsState restores the view");
sandbox.applyProductsState({ category: "bags" });
check(sandbox.prodView === "all", "a link without a view falls back to All");
console.log("ADMIN HIDDEN PRODUCT CHECKS PASSED");
"""


def test_the_hidden_view_filters_and_survives_the_return_url():
    result = subprocess.run(["node", "-e", _NODE_CHECK], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"admin hidden VM failed:\n{result.stdout}\n{result.stderr}"
    assert "ADMIN HIDDEN PRODUCT CHECKS PASSED" in result.stdout


def test_the_desk_offers_all_live_and_hidden_tabs():
    js = _admin()
    assert 'data-prod-view="all"' in js or 'viewTab("all"' in js
    for view in ("live", "hidden"):
        assert f'viewTab("{view}"' in js
    assert "Hidden / Drafts" in js
    assert "PRODUCT_VIEWS = [\"all\", \"live\", \"hidden\"]" in js


def test_hidden_products_can_be_unhidden_in_one_tap():
    js = _admin()
    assert "data-unhide" in js
    unhide = js[js.index('document.querySelectorAll("#prod-grid [data-unhide]")'):]
    unhide = unhide[:unhide.index("document.querySelectorAll(\"#prod-grid [data-del]\")")]
    assert "online: true" in unhide
    assert "JA.success(" in unhide, "a restored product must confirm in green"


def test_the_counters_state_live_and_hidden_together():
    js = _admin()
    # "289 Products: 283 Active, 6 Hidden" - the header line the owner asked for.
    head = js[js.index('<p class="adx-count">'):]
    head = head[:head.index("</p>")]
    assert "id=\"prod-count-all\"" in head
    assert "id=\"prod-count-live\"" in head
    assert "id=\"prod-count-hidden\"" in head
    assert "Active" in head and "Hidden" in head
    # ...and the same numbers ride on the tabs and leave the old
    # "N of N products" line (which hid the hidden ones) behind.
    assert 'id="prod-tab-${id}"' in js
    assert 'setText("prod-tab-hidden", hiddenCount)' in js
    assert 'setText("prod-tab-live", liveCount)' in js
    assert "products${filteredNote}" not in js


def test_hiding_or_deleting_a_product_keeps_the_counters_honest():
    """The counter ids are repainted on every render, not only on first paint."""
    js = _admin()
    render = js[js.index("function renderProdGrid()"):js.index("function updateProductBulkUI()")]
    for ident in ("prod-count-all", "prod-count-live", "prod-count-hidden",
                  "prod-tab-all", "prod-tab-live", "prod-tab-hidden"):
        assert ident in render, ident
    assert "p.online !== false" in render


def test_the_view_tabs_are_styled_for_phone_and_desktop():
    css = _css()
    assert ".adx-viewtabs" in css
    assert ".adx-unhide" in css
    assert ".adx-viewtabs button.is-on" in css
