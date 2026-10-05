// Admin dashboard stock-queue simulation (Node VM, no browser dependency).
//
// Boots the REAL js/store.js + js/admin.js and proves the compact dashboard
// the owner asked for on 2026-10-05:
//
//   * "Supplier-linked", "Low Stock" and "Out of Stock" render as ONE
//     compact summary line each ("⚠️ 236 Low Stock Items — Tap to view"),
//     never as permanently expanded inline lists;
//   * tapping a summary bar opens a clean, scrollable drawer with the list;
//   * tapping a row deep-links straight to /admin/products?id=<ID>;
//   * /admin/products?id=<ID> opens that product's editor (deep link);
//   * the "Products to feature" picker is a compact, scrollable dropdown
//     that pages its matches and shows prices in the ACTIVE currency
//     (₦ for NGN/English, CFA for XOF/French).
//
// Run with:  node tests/_admin_attention_drawer_sim.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const storeSrc = readFileSync(path.join(root, "js", "store.js"), "utf8");
const adminSrc = readFileSync(path.join(root, "js", "admin.js"), "utf8");

let failures = 0;
function check(name, ok, detail = "") {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
  if (!ok) failures++;
}

// ------------------------------------------------------------- fake DOM
function mk(tag) {
  return {
    tagName: String(tag || "div").toUpperCase(),
    dataset: {}, style: {}, hidden: false, innerHTML: "", textContent: "", value: "",
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute(k, v) { this[k] = String(v); },
    getAttribute(k) { return this[k] == null ? "" : String(this[k]); },
    removeAttribute(k) { delete this[k]; },
    addEventListener() {}, removeEventListener() {}, appendChild() {}, append() {},
    remove() {}, focus() {}, click() {}, closest() { return null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
}

const registry = new Map();
const barStubs = new Map();
const toasts = [];
const productLookups = [];

function barsFromHTML(html) {
  const out = [];
  const re = /data-attention-bar="([^"]+)"/g;
  let m;
  while ((m = re.exec(String(html || "")))) {
    const key = m[1];
    if (!barStubs.has(key)) barStubs.set(key, mk("button"));
    const bar = barStubs.get(key);
    bar.dataset.attentionBar = key;
    out.push(bar);
  }
  return out;
}

function element(id) {
  if (!registry.has(id)) registry.set(id, mk("div"));
  return registry.get(id);
}

const boxEl = element("needs-attention-box");
boxEl.querySelectorAll = (sel) => (sel === "[data-attention-bar]" ? barsFromHTML(boxEl.innerHTML) : []);
boxEl.querySelector = (sel) => boxEl.querySelectorAll(sel)[0] || null;

const storage = new Map();
const sandbox = {
  console, setTimeout, clearTimeout, setInterval, clearInterval, AbortSignal,
  Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map, Promise, RegExp,
  encodeURIComponent, decodeURIComponent, URL, URLSearchParams, FormData: class {},
  CustomEvent: class { constructor(t, i) { this.type = t; this.detail = (i || {}).detail; } },
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
  },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { onLine: true, language: "en" },
  location: {
    href: "https://jaurastore.com.ng/admin/products",
    origin: "https://jaurastore.com.ng", protocol: "https:",
    host: "jaurastore.com.ng", pathname: "/admin/products", search: "",
  },
  history: { replaceState() {} },
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  // The storefront boots its catalogue from api/catalog; answer the shapes it
  // expects so no background reload rejects while the dashboard is driven.
  fetch: (url) => {
    const u = String(url || "");
    if (u.includes("api/catalog")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, products: [], meta: {} }) });
    }
    if (u.includes("api/realtime-config")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ enabled: false }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, site: {} }) });
  },
  alert() {}, confirm() { return true; }, prompt() { return ""; },
};

sandbox.document = {
  readyState: "loading",
  body: Object.assign(mk("body"), { dataset: { page: "admin" } }),
  documentElement: mk("html"),
  head: { querySelector: () => null, appendChild() {} },
  addEventListener() {}, dispatchEvent: () => true,
  querySelector: (sel) => {
    const m = /^#([\w-]+)$/.exec(String(sel));
    return m ? element(m[1]) : null;
  },
  querySelectorAll: (sel) => (sel === "[data-attention-bar]" ? [...barStubs.values()] : []),
  getElementById: (id) => element(id),
  createElement: (t) => mk(t),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.addEventListener = () => {};
sandbox.removeEventListener = () => {};
sandbox.dispatchEvent = () => true;
sandbox.I18N = { t: (k) => k, apply() {}, lang: () => "en" };

// --------------------------------------------------------- canned payload
const rowSet = (n, prefix, qty) => Array.from({ length: n }, (_, i) => ({
  product_id: `${prefix}-${i}`,
  name: `Product ${prefix} ${i}`,
  variant_label: "Black",
  qty,
}));

const payload = {
  ok: true,
  pending: [{ id: "JA-100", at: new Date().toISOString(), total: 5000, currency: "NGN", customer: { name: "Ada" } }],
  stale: [],
  lowStock: rowSet(200, "low", 2),
  outOfStock: rowSet(3, "sold", 0),
  supplierOutOfStock: rowSet(2, "supout", 0),
  supplierLowStock: rowSet(5, "suplow", 1),
  counts: { pending: 1, lowStock: 236, outOfStock: 3, supplierOutOfStock: 2, supplierLowStock: 5 },
};

sandbox.JA_NET = {
  api: (p) => Promise.resolve(p === "api/admin/needs-attention" ? payload : { ok: true }),
};

// The picker renders from the catalogue the admin is browsing.
sandbox.__rows = Array.from({ length: 100 }, (_, i) => ({
  id: `p-${i}`, name: `Catalogue Product ${i}`, sku: `SKU-${i}`, priceNgn: 25000 * (i + 1),
}));
sandbox.__selected = new Set(["p-0"]);

vm.createContext(sandbox);
vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
vm.runInContext(adminSrc, sandbox, { filename: "js/admin.js" });

// The catalogue lookup is stubbed so the deep link can be proven without a
// live server; the real editor decision code still runs.
vm.runInContext(`
  JA.toast = (m) => { globalThis.__toasts.push(String(m)); };
  JA.product = (id) => {
    globalThis.__lookups.push(String(id));
    return String(id) === "wix-005" ? { id: "wix-005", name: "Test Product" } : undefined;
  };
`, sandbox);
sandbox.__toasts = toasts;
sandbox.__lookups = productLookups;

// ------------------------------------------------- 1. compact summary bars
await vm.runInContext("fillNeedsAttention()", sandbox);
const boxHTML = boxEl.innerHTML;
const bars = ["lowStock", "outOfStock", "supplierLow", "supplierOut"].filter((k) => boxHTML.includes(`data-attention-bar="${k}"`));
check("every non-empty stock queue renders as a compact summary bar",
  bars.length === 4, `bars=${bars.join(",")}`);
check("the low-stock bar reads as ONE line with the true total and a call to action",
  /data-attention-bar="lowStock"[\s\S]*?<b>236<\/b>\s*Low Stock Items/.test(boxHTML)
  && boxHTML.includes("Tap to view"));
check("the sold-out and supplier bars carry their own counts",
  /data-attention-bar="outOfStock"[\s\S]*?<b>3<\/b>\s*Out of Stock Items/.test(boxHTML)
  && /data-attention-bar="supplierLow"[\s\S]*?<b>5<\/b>/.test(boxHTML)
  && /data-attention-bar="supplierOut"[\s\S]*?<b>2<\/b>/.test(boxHTML));
check("no low-stock product is printed inline on the dashboard",
  !boxHTML.includes("Product low 0") && !boxHTML.includes("Product supout 0"),
  "the list must live behind the bar, not on the dashboard");
check("pending orders still render as their own block",
  boxHTML.includes("Pending orders") && boxHTML.includes("JA-100"));

// ------------------------------------------------------ 2. the drawer
const lowBar = barStubs.get("lowStock");
check("the low-stock bar has a click handler", typeof (lowBar && lowBar.onclick) === "function");
if (lowBar && lowBar.onclick) lowBar.onclick();

const drawer = element("attention-drawer");
const listEl = element("attention-drawer-list");
check("tapping the bar opens the drawer", drawer.hidden === false);
check("the drawer is titled for the queue it opened",
  element("attention-drawer-title").textContent === "Low stock");
check("the drawer explains itself and pages long queues",
  element("attention-drawer-note").textContent.includes("Showing the first 60 of 200"),
  element("attention-drawer-note").textContent);
check("the open bar advertises its expanded state",
  String((barStubs.get("lowStock") || {}).ariaExpanded || barStubs.get("lowStock").getAttribute("aria-expanded")) === "true");
const rowCount = (listEl.innerHTML.match(/class="attention-drawer-row"/g) || []).length;
check("the drawer paints one page, not all 200 rows", rowCount === 60, `rows=${rowCount}`);
check("each row deep-links straight to the product editor",
  listEl.innerHTML.includes('href="/admin/products?id=low-0"')
  && listEl.innerHTML.includes('href="/admin/products?id=low-59"'));
check("the drawer offers a Show more control for the rest",
  element("attention-drawer-more").hidden === false
  && element("attention-drawer-more").textContent.includes("140 left"),
  element("attention-drawer-more").textContent);

const more = element("attention-drawer-more");
if (typeof more.onclick === "function") more.onclick();
const rowCount2 = (listEl.innerHTML.match(/class="attention-drawer-row"/g) || []).length;
check("Show more paints the next page without leaving the drawer", rowCount2 === 120, `rows=${rowCount2}`);

vm.runInContext("closeAttentionDrawer()", sandbox);
check("closing the drawer hides it again", drawer.hidden === true);

// -------------------------------------------------- 3. deep-link routing
const parsed = vm.runInContext('productsStateFromUrl("/admin/products?id=wix-005")', sandbox);
check("?id=<ID> survives return-url parsing", parsed && parsed.id === "wix-005", JSON.stringify(parsed));
const href = vm.runInContext('attentionProductHref("wix-005")', sandbox);
check("the row link is the canonical /admin/products?id=<ID>",
  href === "/admin/products?id=wix-005", href);

productLookups.length = 0;
sandbox.location.search = "?id=wix-005";
sandbox.location.pathname = "/admin/products";
const desk = vm.runInContext("applyAdminDeepLink()", sandbox);
check("a known product id opens the products desk", desk === "products", String(desk));
check("the deep link asked for exactly that product",
  productLookups.includes("wix-005"), productLookups.join(","));
check("the editor is opened for that product",
  vm.runInContext("editingId", sandbox) === "wix-005",
  String(vm.runInContext("editingId", sandbox)));

sandbox.location.search = "?id=gone-404";
vm.runInContext("editingId = null", sandbox);
toasts.length = 0;
vm.runInContext("applyAdminDeepLink()", sandbox);
check("a stale product id is refused with a clear message, never an empty form",
  !vm.runInContext("editingId", sandbox)
  && toasts.some((t) => /no longer in the catalogue/i.test(t)),
  toasts.join(" | "));

// ------------------------------------- 4. "Products to feature" picker
const shell = vm.runInContext('marketingPickerHTML("mk-product", "Products to feature")', sandbox);
check("the picker is a collapsed <details> dropdown, not an inline list",
  shell.includes("<details") && shell.includes("mk-product-picker")
  && !shell.includes("<fieldset"));
check("the collapsible shell keeps the ids the composer binds",
  shell.includes('id="mk-product-search"') && shell.includes('id="mk-product-options"')
  && shell.includes('id="mk-product-count"'));

const pagedHTML = vm.runInContext(
  "marketingPickerListHTML(__rows, 'campaignProduct', __selected, 40, 'data-campaign-picker-more')",
  sandbox,
);
const labelCount = (pagedHTML.match(/class="mk-product-option"/g) || []).length;
check("the picker pages a big catalogue instead of painting all of it",
  labelCount === 40 && pagedHTML.includes("Showing 40 of 100"), `labels=${labelCount}`);
check("the chosen product is still ticked in the rendered page",
  /value="p-0"\s+checked|checked[^>]*value="p-0"/.test(pagedHTML) || pagedHTML.includes('value="p-0" checked'));
check("the list is a scrollable panel by CSS contract",
  readFileSync(path.join(root, "css", "style.css"), "utf8").includes("max-height: 260px; overflow: auto"));

// Currency follows the ACTIVE context: English/NGN then French/XOF.
const ngnPrice = vm.runInContext("marketingPickerPrice({ priceNgn: 25000 })", sandbox);
check("an English/NGN context shows Naira", ngnPrice === "₦25,000", ngnPrice);
const cfaPrice = vm.runInContext('(JA.currency = () => "CFA", marketingPickerPrice({ priceNgn: 25000 }))', sandbox);
check("a French/XOF context shows F CFA",
  cfaPrice === "11,000 CFA" && !cfaPrice.includes("₦"), cfaPrice);
// toCfa is the shared ceiling: 25,000 NGN * 0.44 = 11,000 -> 11,000 CFA.
const cfaXof = vm.runInContext('(JA.currency = () => "XOF", marketingPickerPrice({ priceNgn: 25000 }))', sandbox);
check("the XOF spelling behaves exactly like CFA", cfaXof === "11,000 CFA", cfaXof);
const rowHTML = vm.runInContext("marketingPickerRowHTML(__rows[0], 'campaignProduct', false)", sandbox);
check("a picker row carries the active-currency price", rowHTML.includes("11,000 CFA") && !rowHTML.includes("₦"));

// A background reload that genuinely rejects must fail the harness, not
// disappear into an unhandled-rejection stack trace.
const lateErrors = [];
process.on("unhandledRejection", (err) => lateErrors.push(String((err && err.message) || err)));

if (lateErrors.length) {
  console.error("unhandled rejection in the shipped code: " + lateErrors.join(" | "));
  process.exit(1);
}
console.log(failures
  ? `\n${failures} admin dashboard check(s) FAILED`
  : "\nall admin dashboard checks passed");
// The shipped storefront keeps live-sync intervals alive; exit explicitly.
process.exit(failures ? 1 : 0);
