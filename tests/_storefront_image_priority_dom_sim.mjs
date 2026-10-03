// Storefront image priority (Node VM, no browser dependency).
//
// Boots the real js/store.js and js/app.js, renders the real homepage
// "shop by category" row into a stub DOM node, and checks the attributes that
// decide which photos a phone downloads first:
//
//   * the tiles visible before any scrolling are eager + high priority;
//   * everything below them is lazy, and NOT marked fetchpriority="low" -
//     marking every off-screen photo low also held back the ones a
//     thumb-scroll reaches next;
//   * a media image that is neither the hero nor a category tile keeps its
//     lazy loading and the <picture> WebP source.
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

const CATEGORIES = Array.from({ length: 7 }, (_, i) => ({
  id: `cat-${i}`,
  name: `Category ${i}`,
  image: "images/products/_placeholder.jpg",
  hidden: false,
  order: i,
}));
const PRODUCTS = [
  { id: "p1", sku: "P1", slug: "one", name: "One", category: "cat-0", priceNgn: 1000, image: "images/products/_placeholder.jpg", stock: 4, online: true },
];

function makeEl(name) {
  return {
    nodeName: name,
    dataset: {},
    style: {},
    hidden: false,
    innerHTML: "",
    scrollWidth: 0,
    clientWidth: 0,
    scrollLeft: 0,
    classList: { _set: new Set(), add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); }, toggle(c, on) { if (on === undefined ? !this._set.has(c) : on) this._set.add(c); else this._set.delete(c); }, contains(c) { return this._set.has(c); } },
    setAttribute(k, v) { this[k] = String(v); },
    getAttribute(k) { return this[k] || ""; },
    appendChild() {},
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    scrollTo() {},
  };
}

const catsEl = makeEl("section");
const storage = new Map();
const listeners = new Map();

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
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, products: PRODUCTS, meta: { count: 1 } }) });
    }
    if (u.includes("api/categories")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, categories: CATEGORIES }) });
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
  // Only the category row is mounted: renderHome() must cope with the rest of
  // the homepage being absent, which it already does.
  querySelector: (sel) => (sel === "[data-home-cats]" ? catsEl : null),
  querySelectorAll: () => [],
  getElementById: () => null,
  createElement: () => makeEl("el"),
};
sandbox.window = sandbox;
if (typeof sandbox.addEventListener !== "function") sandbox.addEventListener = () => {};
if (typeof sandbox.removeEventListener !== "function") sandbox.removeEventListener = () => {};
if (typeof sandbox.dispatchEvent !== "function") sandbox.dispatchEvent = () => true;
sandbox.globalThis = sandbox;
sandbox.I18N = { t: (k) => k, apply() {}, lang: () => "en" };
sandbox.fallbackImg = () => {};

vm.createContext(sandbox);
vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
vm.runInContext(appSrc, sandbox, { filename: "js/app.js" });
const JA = vm.runInContext("JA", sandbox);
await JA.ready;

// ---------------------------------------------------- mediaHTML itself
const lazyMedia = JA.mediaHTML("images/products/_placeholder.jpg", { alt: "x" });
check("a below-the-fold photo is still lazy", /loading="lazy"/.test(lazyMedia));
check("a below-the-fold photo is NOT marked low priority",
  !/fetchpriority="low"/i.test(lazyMedia), lazyMedia.slice(0, 160));
check("a below-the-fold photo is not promoted to high",
  !/fetchpriority="high"/i.test(lazyMedia), lazyMedia.slice(0, 160));
check("the WebP <picture> source is kept",
  /<picture><source srcset="images\/products\/_placeholder\.400w\.webp"/.test(lazyMedia),
  lazyMedia.slice(0, 200));

const heroMedia = JA.mediaHTML("images/products/_placeholder.jpg", { alt: "x", eager: true });
check("a hero photo is eager and high priority",
  /loading="eager"/.test(heroMedia) && /fetchpriority="high"/.test(heroMedia), heroMedia.slice(0, 160));

// ---------------------------------------------------- the homepage row
vm.runInContext("renderHome()", sandbox);
const catNames = vm.runInContext(
  "JA.categories().map((c) => JA.categoryName(c.id))", sandbox);
const tiles = String(catsEl.innerHTML).split('<a class="home-cat"').slice(1);
check("the homepage rendered every category tile", tiles.length === catNames.length,
  `${tiles.length} tile(s) for ${catNames.length} categories`);

const EAGER_TILES = 4;
const eager = tiles.filter((t) => /loading="eager"/.test(t) && /fetchpriority="high"/.test(t));
const lazy = tiles.filter((t) => /loading="lazy"/.test(t));
check("the tiles visible before scrolling load eagerly", eager.length === EAGER_TILES,
  `${eager.length} eager`);
check("the first tile is one of them", /fetchpriority="high"/.test(tiles[0]));
check("the rest of the row stays lazy", lazy.length === Math.max(0, catNames.length - EAGER_TILES),
  `${lazy.length} lazy`);
check("no tile is marked low priority", !/fetchpriority="low"/i.test(catsEl.innerHTML));
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[ch]));
check("every tile still carries its cover and name",
  tiles.every((t, i) => /<img /.test(t) && t.includes(escapeHtml(catNames[i]))));

console.log(failures ? `\n${failures} storefront image priority check(s) FAILED` : "\nall storefront image priority checks passed");
process.exit(failures ? 1 : 0);
