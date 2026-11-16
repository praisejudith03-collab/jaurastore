// Admin dashboard truncation simulation (Node VM, no browser dependency).
//
// Boots the REAL js/store.js + js/admin.js against a fake DOM and proves the
// dashboard cleanup the owner asked for on 2026-11-16:
//
//   * "Top viewed products" and "Visitor locations" show five rows, not the
//     whole list, so neither list can run the length of a phone screen;
//   * each truncated list carries a one-tap "Show all N" toggle that expands
//     and collapses it again;
//   * orders no longer take any space on the Dashboard — the "Latest orders"
//     table is gone from the panel and fillAnalytics() never writes one.
//
// Run with:  node tests/_dashboard_truncation_sim.mjs
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
function element(id) {
  if (!registry.has(id)) registry.set(id, mk("div"));
  return registry.get(id);
}

// The list toggles are buttons inside the rendered HTML; the fake
// document.querySelectorAll() turns them into clickable stubs so the harness
// can drive the real delegated handler.
const toggleStubs = new Map();
function togglesFrom(listIds) {
  const out = [];
  listIds.forEach((id) => {
    const html = String(element(id).innerHTML || "");
    const re = /data-dash-more="([^"]+)"/g;
    let m;
    while ((m = re.exec(html))) {
      const key = m[1];
      if (!toggleStubs.has(key)) toggleStubs.set(key, mk("button"));
      const button = toggleStubs.get(key);
      button.dataset.dashMore = key;
      out.push(button);
    }
  });
  return out;
}

const sandbox = {
  console, setTimeout, clearTimeout, setInterval, clearInterval, AbortSignal,
  Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map, Promise, RegExp,
  encodeURIComponent, decodeURIComponent, URL, URLSearchParams, FormData: class {},
  CustomEvent: class { constructor(t, i) { this.type = t; this.detail = (i || {}).detail; } },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { onLine: true, language: "en" },
  location: {
    href: "https://jaurastore.com.ng/admin", origin: "https://jaurastore.com.ng",
    protocol: "https:", host: "jaurastore.com.ng", pathname: "/admin",
    search: "",
  },
  history: { replaceState() {} },
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  fetch: (url) => {
    const u = String(url || "");
    if (u.includes("api/catalog")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, products: [], meta: {} }) });
    }
    if (u.includes("api/realtime-config")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ enabled: false }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, site: { cfaRate: 0.44 } }) });
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
  querySelectorAll: (sel) => (sel === "[data-dash-more]"
    ? togglesFrom(["an-products", "an-loc"]) : []),
  getElementById: (id) => element(id),
  createElement: (t) => mk(t),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.addEventListener = () => {};
sandbox.removeEventListener = () => {};
sandbox.dispatchEvent = () => true;
sandbox.I18N = { t: (k) => k, apply() {}, lang: () => "en" };
sandbox.JA_NET = { api: () => Promise.resolve({ ok: true, pending: [], stale: [], counts: {} }) };

vm.createContext(sandbox);
vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
vm.runInContext(adminSrc, sandbox, { filename: "js/admin.js" });

// ---------------------------------------------------- canned analytics answer
const products = Array.from({ length: 12 }, (_, i) => ({
  productId: `p-${i}`, name: `Top Product ${i}`, views: 500 - i * 10,
  carts: 3, purchases: i,
}));
const locations = Array.from({ length: 9 }, (_, i) => ({
  city: `City ${i}`, country: i % 2 ? "NG" : "BJ", sessions: 100 - i,
}));

vm.runInContext(`
  JA.adminAnalytics = () => Promise.resolve({
    ok: true,
    totals: { liveNow: 1, uniqueVisitors: 40, visits: 80, pageViews: 200 },
    conversion: { orders: 3, units: 5, revenueByCurrency: [{ currency: "NGN", value: 45000 }] },
    periods: { today: {}, week: {}, month: {} },
    live: [], activity: [], sales: [], series: [],
    topProducts: globalThis.__products,
    locations: globalThis.__locations,
    recentOrders: [{ id: "JA-SHOULD-NOT-RENDER", customer_name: "Ada", total: 5000, currency: "NGN", status: "confirmed" }],
  });
`, sandbox);
sandbox.__products = products;
sandbox.__locations = locations;

await vm.runInContext("fillAnalytics()", sandbox);

const productHTML = String(element("an-products").innerHTML || "");
const locHTML = String(element("an-loc").innerHTML || "");
const rows = (html) => (html.match(/<tr><td/g) || []).length;

check("top viewed products show five rows by default", rows(productHTML) === 5, `rows=${rows(productHTML)}`);
check("visitor locations show five rows by default", rows(locHTML) === 5, `rows=${rows(locHTML)}`);
check("the products list offers a 'show all' toggle",
  productHTML.includes('data-dash-more="products"') && productHTML.includes("Show all 12"),
  productHTML.slice(-220));
check("the locations list offers a 'show all' toggle",
  locHTML.includes('data-dash-more="locations"') && locHTML.includes("Show all 9"),
  locHTML.slice(-220));

// The dashboard panel itself must not carry an orders table any more.
const panelHTML = vm.runInContext("analyticsPanel()", sandbox);
check("the dashboard panel no longer renders 'Latest orders'",
  !panelHTML.includes("Latest orders") && !panelHTML.includes('id="an-orders"'),
  "orders belong on the Orders page");
check("the dashboard points orders at the Orders page instead",
  panelHTML.includes("adx-orders-moved") && panelHTML.includes("/admin/orders"));

// ------------------------------------------------ expanding and collapsing
const productsToggle = toggleStubs.get("products");
check("the products toggle has a click handler", typeof (productsToggle && productsToggle.onclick) === "function");
if (productsToggle && productsToggle.onclick) productsToggle.onclick();
await vm.runInContext("fillAnalytics()", sandbox);
const expandedProducts = String(element("an-products").innerHTML || "");
check("tapping the products toggle shows every product",
  rows(expandedProducts) === 12, `rows=${rows(expandedProducts)}`);
check("the expanded list offers a way back to five",
  expandedProducts.includes("Show top 5"), expandedProducts.slice(-160));

const productsToggleAgain = toggleStubs.get("products");
if (productsToggleAgain && productsToggleAgain.onclick) productsToggleAgain.onclick();
await vm.runInContext("fillAnalytics()", sandbox);
check("tapping again collapses the products list back to five",
  rows(String(element("an-products").innerHTML || "")) === 5);

const locToggle = toggleStubs.get("locations");
if (locToggle && locToggle.onclick) locToggle.onclick();
await vm.runInContext("fillAnalytics()", sandbox);
check("tapping the locations toggle shows every location",
  rows(String(element("an-loc").innerHTML || "")) === 9,
  `rows=${rows(String(element("an-loc").innerHTML || ""))}`);

// Orders are gone from the dashboard DOM entirely.
check("fillAnalytics never writes a Latest orders table",
  !String(element("an-orders").innerHTML || "").includes("JA-SHOULD-NOT-RENDER"),
  "the dashboard must not render orders");

const lateErrors = [];
process.on("unhandledRejection", (err) => lateErrors.push(String((err && err.message) || err)));
if (lateErrors.length) {
  console.error("unhandled rejection in the shipped code: " + lateErrors.join(" | "));
  process.exit(1);
}
console.log(failures
  ? `\n${failures} dashboard truncation check(s) FAILED`
  : "\nall dashboard truncation checks passed");
process.exit(failures ? 1 : 0);
