// Currency switch must never leave a short, stale product grid (Node, no browser).
//
// The bug: tapping ₦ / F CFA repainted the shop from whatever the DEVICE was
// holding. Two independent things could make that a short list, and both were
// reported as "27 products in Naira, 17 in CFA":
//
//   A. js/store.js kept the last catalogue in localStorage
//      (jaura_catalog_cache) as a paint accelerator. A box written from a
//      partial answer - a page opened mid-deploy, a phone that loaded the
//      shop on a flaky connection, rows published since - was reused as-is,
//      because a currency switch only fired "ja:rerender".
//
//   B. js/app.js kept the shop's price-filter bounds in the ACTIVE currency.
//      1 ₦ is 0.44 CFA, so a Naira floor is roughly double the same product's
//      CFA price: after ₦ -> CFA every product under the old floor was
//      silently filtered out of the grid.
//
// This harness boots the REAL js/store.js in a stubbed browser and asserts:
//
//   1. setCurrency() drops the persisted catalogue box;
//   2. it refetches the catalogue, and the request is forced past every cache
//      (cache: "reload" + Cache-Control: no-cache + a one-shot ?_fresh token);
//   3. a stale 17-row cache is REPLACED by the server's full 27-row answer,
//      in both directions (₦ -> CFA and CFA -> ₦);
//   4. the repaint events fire, so the grid is redrawn with the full list;
//   5. a currency switch that happens while the network is down leaves the
//      catalogue that is already on screen intact (never a blank shop).
//
// It also checks the js/app.js half statically: shopFilter carries its
// currency and renderShop() resets the bounds when that currency changes.
//
// Run directly:   node tests/_currency_refresh_sim.mjs   (exit 0 = all pass)
// Or via pytest:  python3 -m pytest tests/test_currency_refresh.py -q
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const storeSrc = readFileSync(path.join(root, "js", "store.js"), "utf8");
const appSrc = readFileSync(path.join(root, "js", "app.js"), "utf8");
const swSrc = readFileSync(path.join(root, "sw.js"), "utf8");

let failures = 0;
function check(name, ok, detail = "") {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
  if (!ok) failures++;
}

const CACHE_KEY = "jaura_catalog_cache";

// The shop the server really serves: 27 active, published products.
const FULL = Array.from({ length: 27 }, (_, i) => ({
  id: `jau-${String(i + 1).padStart(3, "0")}`,
  sku: `JAU${String(i + 1).padStart(3, "0")}`,
  slug: `product-${i + 1}`,
  name: `Product ${i + 1}`,
  category: "household",
  priceNgn: 1000 * (i + 1),
  priceCfa: Math.round(1000 * (i + 1) * 0.44),
  image: "images/products/_placeholder.jpg",
  stock: 5,
  online: true,
}));
// What a device could be holding: the first 17 of them.
const STALE = FULL.slice(0, 17);

function makeSandbox(served, opts = {}) {
  const storage = new Map();
  const localStorage = {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
    clear: () => storage.clear(),
  };
  const fetches = [];
  const listeners = new Map();
  const sandbox = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval, AbortSignal,
    Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map, Promise,
    CustomEvent: class CustomEvent {
      constructor(type, init) { this.type = type; this.detail = (init || {}).detail; }
    },
    fetch: (url, init) => {
      fetches.push({ url: String(url), init: init || {} });
      if (opts.offline) return Promise.reject(new Error("offline"));
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          ok: true, products: served(), meta: { count: served().length },
        }),
      });
    },
    localStorage,
    sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    navigator: { onLine: !opts.offline, language: "en" },
    location: {
      href: "https://jaurastore.com.ng/shop.html", origin: "https://jaurastore.com.ng",
      protocol: "https:", host: "jaurastore.com.ng", pathname: "/shop.html", search: "",
    },
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
  };
  sandbox.document = {
    body: { dataset: { page: "shop" }, classList: { add() {}, remove() {}, toggle() {} } },
    documentElement: { dataset: {} },
    addEventListener: (type, fn) => {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    dispatchEvent: (ev) => {
      (listeners.get(ev.type) || []).forEach((fn) => { try { fn(ev); } catch (e) {} });
      return true;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => ({ style: {}, dataset: {}, setAttribute() {}, appendChild() {} }),
    head: { querySelector: () => null, appendChild() {} },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
  const JA = vm.runInContext("JA", sandbox);
  // Count the repaints the page would perform.
  const painted = { rerender: 0, catalog: 0 };
  sandbox.document.addEventListener("ja:rerender", () => { painted.rerender++; });
  sandbox.document.addEventListener("ja:catalog", () => { painted.catalog++; });
  return { JA, storage, fetches, painted, sandbox };
}

// Let the store's promise chain (loadSeed -> refreshCatalog) settle.
const settle = () => new Promise((r) => setTimeout(r, 30));

// --------------------------------------------- 1. the stale box is dropped
{
  const { JA, storage } = makeSandbox(() => FULL);
  storage.set(CACHE_KEY, JSON.stringify({ at: Date.now(), view: "public", products: STALE }));
  check("precondition: the device holds a 17-product cache",
    JSON.parse(storage.get(CACHE_KEY)).products.length === 17);
  JA.setCurrency("CFA");
  await settle();
  const box = storage.get(CACHE_KEY);
  const rows = box ? JSON.parse(box).products.length : 0;
  check("the currency switch replaces the stale cache with the full catalogue",
    rows === 27, `cache now holds ${rows} products`);
}

// ------------------------------- 2. the refetch is forced past every cache
{
  const { JA, fetches } = makeSandbox(() => FULL);
  await JA.ready;
  fetches.length = 0;
  JA.setCurrency("CFA");
  await settle();
  const hit = fetches.find((f) => f.url.includes("api/catalog"));
  check("the currency switch refetches the catalogue", !!hit,
    `fetches: ${JSON.stringify(fetches.map((f) => f.url))}`);
  check("the refetch bypasses the HTTP cache",
    !!hit && hit.init.cache === "reload", hit ? String(hit.init.cache) : "no request");
  check("the refetch sends Cache-Control: no-cache",
    !!hit && (hit.init.headers || {})["Cache-Control"] === "no-cache",
    hit ? JSON.stringify(hit.init.headers) : "no request");
  check("the refetch carries a one-shot token so no proxy can answer it",
    !!hit && /[?&]_fresh=\d+/.test(hit.url), hit ? hit.url : "no request");
}

// ------------------------- 3. both directions recover the full 27 products
for (const [from, to] of [["NGN", "CFA"], ["CFA", "NGN"]]) {
  const { JA, storage } = makeSandbox(() => FULL);
  storage.set("jaura_currency", from);
  storage.set(CACHE_KEY, JSON.stringify({ at: Date.now(), view: "public", products: STALE }));
  JA.setCurrency(to);
  await settle();
  check(`${from} -> ${to} shows the full catalogue, not the cached 17`,
    JA.products().length === 27, `JA.products() returned ${JA.products().length}`);
  check(`${from} -> ${to} actually switched the currency`, JA.currency() === to);
}

// ------------------------------------------ 4. the page is told to repaint
{
  const { JA, storage, painted } = makeSandbox(() => FULL);
  storage.set(CACHE_KEY, JSON.stringify({ at: Date.now(), view: "public", products: STALE }));
  await JA.ready;
  painted.rerender = 0;
  JA.setCurrency("CFA");
  check("prices flip immediately (a repaint before any network wait)",
    painted.rerender >= 1, `${painted.rerender} repaints`);
  await settle();
  check("the page repaints again once the fresh catalogue lands",
    painted.rerender >= 2, `${painted.rerender} repaints`);
}

// --------------------------------- 5. offline: never blank out a live shop
{
  const { JA, storage, sandbox } = makeSandbox(() => FULL);
  storage.set(CACHE_KEY, JSON.stringify({ at: Date.now(), view: "public", products: FULL }));
  await JA.ready;
  const before = JA.products().length;
  sandbox.fetch = () => Promise.reject(new Error("offline"));
  JA.setCurrency("CFA");
  await settle();
  check("a currency switch with no signal keeps the products already loaded",
    JA.products().length === before && before === 27,
    `${before} before, ${JA.products().length} after`);
}

// ------------------------------------------- 6. the js/app.js filter half
{
  check("shopFilter records the currency its price bounds were captured in",
    /const shopFilter = \{[^}]*\bcur:\s*""/.test(appSrc));
  check("a helper clears bounds captured in another currency",
    /function resetShopFilterForCurrency\(\)/.test(appSrc));
  const parts = appSrc.split("\nfunction renderShop() {");
  const body = parts.length > 1 ? parts[1].slice(0, 600) : "";
  check("renderShop() resets the bounds before it filters anything",
    body.includes("resetShopFilterForCurrency()"),
    body.slice(0, 160).replace(/\n/g, " ") || "renderShop() not found");
  const resetFn = appSrc.split("function resetShopFilterForCurrency() {")[1].slice(0, 400);
  check("the reset really drops min/max and re-derives them",
    /shopFilter\.min = 0/.test(resetFn) && /shopFilter\.max = 0/.test(resetFn)
      && /shopFilter\.inited = false/.test(resetFn));
}

// ------------------------------------- 7. the service worker steps aside
{
  const branch = swSrc.split('indexOf("/api/") === 0')[1].slice(0, 700);
  check("the service worker never answers a forced catalogue refresh",
    branch.includes('searchParams.has("_fresh")'),
    branch.slice(0, 200).replace(/\n/g, " "));
}

console.log(failures ? `\n${failures} currency check(s) FAILED`
                     : "\nall currency refresh checks passed");
process.exit(failures ? 1 : 0);
