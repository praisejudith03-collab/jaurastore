"""Behavioral checks for the simplified product editor's rendered controls."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_NODE_CHECK = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("js/admin.js", "utf8");
function section(startMark, endMark) {
  const start = source.indexOf(startMark);
  const end = source.indexOf(endMark, start);
  if (start < 0 || end < 0) throw new Error("missing editor section: " + startMark);
  return source.slice(start, end);
}
const editorHelpers = section("function editorOptions(p) {", "function bindMedia()")
  + "\n" + section("function currentOptionStock() {", "function bindCfaPreview()")
  + "\n" + section("function productForm(p = {}) {", "async function handleProductSubmit(e, existing)");
const escape = (value) => String(value ?? "").replace(/[&<>\"']/g, ch =>
  ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;", "'":"&#39;"}[ch]));
const sandbox = {
  JA: { categories: () => [{ id: "bags", name: "Bags" }], CATEGORIES: [], escape },
  window: {}, dashCat: "bags", prodCatSel: "", editingId: "p1",
  productImages: p => p.images || (p.image ? [p.image] : []),
  mediaStripHTML: () => "<div>media</div>",
};
vm.createContext(sandbox);
vm.runInContext(editorHelpers, sandbox);
const product = {
  id: "p1", name: "Test bag", description: "Description", category: "bags",
  priceNgn: 10000, stock: 3, image: "https://img.example/a.jpg", sku: "JAU-TEST-1",
  images: ["https://img.example/a.jpg"],
  options: [{ title: "Colour", values: ["Red", "Blue"] }],
  optionStock: { Red: 3 },
  optionSupplierSku: {
    "Colour: Red": "https://supplier.example/red",
    "Colour: Blue": "https://supplier.example/blue",
  },
  optionSku: { "Colour: Red": "SKU-RED-1" },
};
const html = sandbox.productForm(product);
const names = [...html.matchAll(/\bname="([^"]+)"/g)].map(match => match[1]);
const required = ["id", "name", "sku", "description", "category", "priceNgn", "supplierSku",
  "stock", "enableCustomNote", "customNotePrompt", "opt-title-0", "opt-vals-0"];
for (const name of required) {
  if (!names.includes(name)) throw new Error("missing form control " + name);
}
for (const removed of ["nameFr", "descriptionFr", "dimensions", "compareNgn", "bulkQty",
  "bulkPercent", "featured", "badge", "online", "stockStatus",
  "Customer Name", "Customer Stars", "Customer Review"]) {
  if (html.includes(removed)) throw new Error("removed control still rendered: " + removed);
}
if (!html.includes('data-opt-stock="Red"') || !html.includes('value="3"'))
  throw new Error("saved option stock did not render");
if (!html.includes('data-opt-stock="Blue"') || !html.includes('value="0"'))
  throw new Error("unassigned option stock must render as zero");
if (!html.includes('data-opt-supplier="Colour: Red"') || !html.includes("https://supplier.example/red")
    || !html.includes('data-opt-supplier="Colour: Blue"') || !html.includes("https://supplier.example/blue"))
  throw new Error("independent option supplier URLs did not render");
if (!html.includes('name="sku"') || !html.includes('value="JAU-TEST-1"'))
  throw new Error("the product-level SKU field did not render its saved value");
if (!html.includes('name="sku"') || !html.includes("JAU-TEST-1"))
  throw new Error("product SKU missing");
// The per-variant SKU is a text field keyed like the stock/supplier maps, and
// it must not be confused with the supplier URL field above it.
if (!html.includes('data-opt-sku="Colour: Red"') || !html.includes('value="SKU-RED-1"'))
  throw new Error("per-variant SKU did not render its saved value");
if (!html.includes('data-opt-sku="Colour: Blue"'))
  throw new Error("every active variant needs its own SKU field");
if (!html.includes("rounded up to the nearest 50") || !html.includes("Custom order note")
    || !html.includes('type="checkbox" name="enableCustomNote"'))
  throw new Error("price display or arbitrary custom-note controls are missing");
if (!html.includes("<textarea name=\"customNotePrompt\"") || !html.includes('id="note-preview"'))
  throw new Error("the custom-note prompt must be a readable, previewed field");
if (/\b(?:undefined|NaN)\b/.test(html)) throw new Error("form contains an unresolved value");
const normalizedStock = sandbox.optionStockValues(product.options,
  { "Colour: Red": 4, Blue: "", unused: 99 });
if (JSON.stringify(normalizedStock) !== JSON.stringify({ Red: 4, Blue: 0 }))
  throw new Error("legacy/composite or blank stock did not normalize to active values");
console.log("ADMIN PRODUCT EDITOR CHECKS PASSED");
"""


def test_product_editor_renders_clean_fields_zero_stock_and_independent_supplier_links():
    result = subprocess.run(
        ["node", "-e", _NODE_CHECK], cwd=ROOT,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"admin editor VM failed:\n{result.stdout}\n{result.stderr}"
    assert "ADMIN PRODUCT EDITOR CHECKS PASSED" in result.stdout
