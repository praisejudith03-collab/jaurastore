"""Functional regressions for exact admin product-category filtering."""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
  { id: "bags-exact", category: "bags", name: "Purse", nameFr: "", sku: "B1" },
  { id: "bags-substring", category: "bags-accessories", name: "Tote", nameFr: "", sku: "B2" },
  { id: "beauty-near", category: "beauty-bags", name: "Oil", nameFr: "", sku: "B3" },
  { id: "beauty-exact", category: "beauty", name: "Cream", nameFr: "", sku: "B4" },
];
const controls = {
  "prod-search": { value: "" },
  "prod-cat": { value: "bags" },
};
const sandbox = {
  JA: { products: () => rows, categories: () => [{ id: "bags" }, { id: "beauty" }] },
  document: { getElementById: (id) => controls[id] || null },
  selectedProductIds: new Set(["stale-selection"]),
  prodPage: 9, prodSearchQ: "", prodCatSel: "beauty", dashCat: "beauty",
  paintDesk: (tab) => { sandbox.lastPainted = tab; },
  renderProdGrid: () => {}, bindProdGridEvents: () => {},
};
vm.createContext(sandbox);
vm.runInContext(["getFilteredProducts", "applyProductFilter", "applyProductsState"]
  .map(grab).join("\n"), sandbox);
function ids() { return Array.from(vm.runInContext("getFilteredProducts().map(p => p.id)", sandbox)); }
function check(condition, message) { if (!condition) throw new Error(message); }

// A stale dashboard pin cannot override a new explicit dropdown selection.
vm.runInContext("applyProductFilter({ target: { id: 'prod-cat' } })", sandbox);
check(sandbox.dashCat === "", "category dropdown change must clear stale pinned selection");
check(sandbox.prodCatSel === "bags", "the selected category must remain visible");
check(sandbox.lastPainted === "products", "category change must rebuild the heading/count");
check(JSON.stringify(ids()) === JSON.stringify(["bags-exact"]),
      "bags must not include category ids that merely contain 'bags'");

// Choosing All categories clears both filters and restores the full list.
controls["prod-cat"].value = "";
vm.runInContext("applyProductFilter({ target: { id: 'prod-cat' } })", sandbox);
check(sandbox.dashCat === "" && sandbox.prodCatSel === "", "All categories clears both states");
check(ids().length === rows.length, "All categories must show all assigned categories");

// Opening a category pins an exact ID; a deleted category in a saved/deep
// link is discarded instead of leaving the desk in a permanently empty state.
vm.runInContext("dashCat = 'beauty'; prodCatSel = 'beauty'", sandbox);
check(JSON.stringify(ids()) === JSON.stringify(["beauty-exact"]),
      "opening Beauty must use exact category equality");
vm.runInContext("applyProductsState({ category: 'deleted-category' })", sandbox);
check(sandbox.dashCat === "" && sandbox.prodCatSel === "",
      "stale category state must be cleared");
console.log("ADMIN CATEGORY FILTER CHECKS PASSED");
"""


def test_admin_category_filter_is_exact_and_stale_state_is_cleared():
    result = subprocess.run(
        ["node", "-e", _NODE_CHECK], cwd=ROOT,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"admin category VM failed:\n{result.stdout}\n{result.stderr}"
    assert "ADMIN CATEGORY FILTER CHECKS PASSED" in result.stdout
