// DOM-level homepage currency simulation (Node VM, no browser dependency).
// Boots the real js/store.js and js/app.js, renders the real homepage
// "More from the house" and "Most viewed right now" components into stub DOM
// nodes, then switches currency. Both sections must update immediately from ₦
// to clean round F CFA amounts without a page reload.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const storeSrc = readFileSync(path.join(root, "js", "store.js"), "utf8");
const appSrc = readFileSync(path.join(root, "js", "app.js"), "utf8");

let failures = 0;
function check(name, ok, detail = "") {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
  if (!ok) failures++;
}

const PRODUCTS = [
  { id: "p-house", sku: "H1", slug: "house", name: "House Bowl", category: "household", priceNgn: 1000, priceCfa: 440, image: "images/products/_placeholder.jpg", stock: 5, online: true },
  { id: "p-bag", sku: "B1", slug: "bag", name: "Soft Bag", category: "bags", priceNgn: 2000, priceCfa: 880, image: "images/products/_placeholder.jpg", stock: 5, online: true },
  { id: "p-beauty", sku: "S1", slug: "serum", name: "Glow Serum", category: "beauty", priceNgn: 3000, priceCfa: 1320, image: "images/products/_placeholder.jpg", stock: 5, online: true },
  { id: "p-perfume", sku: "P1", slug: "mist", name: "Fresh Mist", category: "perfume", priceNgn: 4000, priceCfa: 1760, image: "images/products/_placeholder.jpg", stock: 0, online: true },
];
const FEATURED = { maxTotal: 12, categories: { household: ["p-house"], beauty: ["p-beauty"] } };
const CATEGORIES = [
  { id: "household", name: "Household & Kitchen", hidden: false, order: 0 },
  { id: "beauty", name: "Beauty", hidden: false, order: 1 },
  { id: "bags", name: "Bags", hidden: false, order: 2 },
  { id: "perfume", name: "Perfume", hidden: false, order: 3 },
];

function makeEl(name) {
  return {
    nodeName: name,
    dataset: {},
    style: {},
    hidden: false,
    innerHTML: "",
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      toggle(c, on) { if (on === undefined ? !this._set.has(c) : on) this._set.add(c); else this._set.delete(c); },
      contains(c) { return this._set.has(c); },
    },
    setAttribute(k, v) { this[k] = String(v); },
    getAttribute(k) { return this[k] || ""; },
    appendChild() {},
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    scrollTo() {},
  };
}

const storage = new Map();
const listeners = new Map();
const newEl = makeEl("div");
const mostViewedEl = makeEl("section");
let catalogueFetches = 0;

const sandbox = {
  console,
  setTimeout, clearTimeout, setInterval, clearInterval, AbortSignal,
  Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map, Promise,
  encodeURIComponent, decodeURIComponent, URL, URLSearchParams,
  CustomEvent: class CustomEvent {
    constructor(type, init) { this.type = type; this.detail = (init || {}).detail; }
  },
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
    clear: () => storage.clear(),
  },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { onLine: true, language: "en" },
  location: { href: "https://jaurastore.com.ng/index.html", origin: "https://jaurastore.com.ng", protocol: "https:", host: "jaurastore.com.ng", pathname: "/index.html", search: "" },
  history: { replaceState() {} },
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  fetch: (url) => {
    const u = String(url);
    if (u.includes("api/catalog")) {
      catalogueFetches += 1;
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, products: PRODUCTS, meta: { count: PRODUCTS.length, homepageFeatured: FEATURED }, homepageFeatured: FEATURED }) });
    }
    if (u.includes("api/categories")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, categories: CATEGORIES }) });
    }
    if (u.includes("api/most-viewed")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, items: PRODUCTS.map((p, i) => ({ productId: p.id, views: 99 - i, carts: i })) }) });
    }
    if (u.includes("api/site")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, site: {} }) });
    }
    if (u.includes("api/homepage-featured")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, featured: FEATURED, groups: [] }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
  },
};

sandbox.document = {
  readyState: "loading",
  body: { dataset: { page: "home" }, classList: { add() {}, remove() {}, toggle() {} } },
  documentElement: { dataset: {}, classList: { add() {}, remove() {}, toggle() {} } },
  head: { querySelector: () => null, appendChild() {} },
  addEventListener: (type, fn) => {
    if (!listeners.has(type)) listeners.set(type, []);
    listeners.get(type).push(fn);
  },
  dispatchEvent: (ev) => {
    (listeners.get(ev.type) || []).forEach((fn) => { try { fn(ev); } catch (e) { console.error(e); } });
    return true;
  },
  querySelector: (sel) => {
    if (sel === "[data-new]") return newEl;
    if (sel === "[data-most-viewed]") return mostViewedEl;
    return null;
  },
  querySelectorAll: () => [],
  getElementById: () => null,
  createElement: () => makeEl("el"),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.I18N = { t: (k) => ({ "card.add": "Add to cart", "card.oos": "Out of stock" }[k] || k), apply() {}, lang: () => "en" };
sandbox.fallbackImg = () => {};

vm.createContext(sandbox);
vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
vm.runInContext(appSrc, sandbox, { filename: "js/app.js" });
const JA = vm.runInContext("JA", sandbox);
await JA.ready;

// Bind the same redraw contract app boot uses, without mounting the whole page chrome.
vm.runInContext(`
  document.addEventListener("ja:rerender", () => { renderHome(); renderMostViewed(); });
  document.addEventListener("ja:catalog", () => { renderHome(); renderMostViewed(); });
  renderHome();
`, sandbox);
await vm.runInContext("renderMostViewed()", sandbox);

check("More from the house rendered grouped category headings", /home-featured-cat/.test(newEl.innerHTML) && /Household/.test(newEl.innerHTML) && /Beauty/.test(newEl.innerHTML));
check("Most viewed rendered product buttons", /data-add=/.test(mostViewedEl.innerHTML) && /Add to cart/.test(mostViewedEl.innerHTML) && /Out of stock/.test(mostViewedEl.innerHTML));
check("Most viewed hides raw view/cart counts", !/\b\d+\s+views?\b|\bin carts\b/.test(mostViewedEl.innerHTML));
check("initial homepage prices are Naira", /₦1,000/.test(newEl.innerHTML) && /₦1,000/.test(mostViewedEl.innerHTML));

catalogueFetches = 0;
JA.setCurrency("CFA");

check("currency switched synchronously", JA.currency() === "CFA");
check("More from the house updates to rounded F CFA immediately", /F CFA\s*450/.test(newEl.innerHTML), newEl.innerHTML.replace(/\s+/g, " ").slice(0, 220));
check("Most viewed updates to rounded F CFA immediately", /F CFA\s*450/.test(mostViewedEl.innerHTML), mostViewedEl.innerHTML.replace(/\s+/g, " ").slice(0, 220));
check("no page reload is needed for the DOM price update", sandbox.location.href.endsWith("index.html"));
check("the immediate DOM update happened before the forced catalogue refresh finished", catalogueFetches >= 1, `${catalogueFetches} refresh fetch(es) scheduled`);

console.log(failures ? `\n${failures} homepage currency DOM check(s) FAILED` : "\nall homepage currency DOM checks passed");
process.exit(failures ? 1 : 0);
