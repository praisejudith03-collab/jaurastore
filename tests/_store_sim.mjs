// Frontend storefront regression (Node, no browser needed).
//
// Boots the REAL js/store.js in a stubbed browser sandbox, points its
// fetch("api/catalog") at a simulated server payload and proves the
// browser-side catalogue pipeline keeps every online product the server
// serves:
//
//   1. all 258 online wix-* products reach JA.products() (the shop grid);
//   2. a shared slug or sku between DIFFERENT products hides neither
//      (dedupeProducts used to collapse on slug/sku alone - the storefront
//      twin of the server bug in tests/test_dedupe_scope.py - which is how
//      "Supabase says 258, the storefront shows 241" happened);
//   3. a re-created product (different id, same name + slug + sku) still
//      renders exactly once;
//   4. a locally remembered deletion for a product the SERVER serves again
//      is forgotten (a restored Supabase row must not stay hidden on a
//      device that once deleted it), while a deletion for a row the server
//      does not serve is kept.
//
// Run directly:   node tests/_store_sim.mjs      (exit 0 = all pass)
// Or via pytest:  python3 -m pytest tests/test_storefront_catalog.py -q
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const storeSrc = readFileSync(path.join(root, "js", "store.js"), "utf8");
const seedRows = JSON.parse(readFileSync(path.join(root, "data", "seed.json"), "utf8"));
const WIX = seedRows.filter((p) => String(p.id || "").startsWith("wix-"));
if (WIX.length !== 258) {
  console.error(`FAIL  the approved customer catalogue must hold 258 wix-* rows, found ${WIX.length}`);
  process.exit(1);
}

let failures = 0;
function check(name, ok, detail = "") {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
  if (!ok) failures++;
}

// ------------------------------------------------------------- browser sandbox
function makeSandbox(servedProducts) {
  const storage = new Map();
  const localStorage = {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
    clear: () => storage.clear(),
  };
  const sandbox = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map, Promise,
    fetch: () => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ ok: true, products: servedProducts, meta: { count: servedProducts.length } }),
    }),
    localStorage,
    sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    document: { body: { dataset: { page: "" } }, addEventListener: () => {}, createElement: () => ({ style: {}, setAttribute: () => {}, appendChild: () => {} }) },
    navigator: { onLine: true, language: "en" },
    location: { href: "https://jaurastore.com.ng/shop.html", origin: "https://jaurastore.com.ng", protocol: "https:", host: "jaurastore.com.ng", pathname: "/shop.html", search: "" },
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
  const JA = vm.runInContext("JA", sandbox);
  return { JA, storage };
}

const online = (p) => ({ ...p, online: true });
const offlineFixture = (id, name) => ({
  id, sku: `FIXTURE-${id}`, slug: id, name, category: "household",
  priceNgn: 1000, priceCfa: 440, image: "images/products/_placeholder.jpg",
  stock: 3, online: false,
});

// ------------------------------------------------- 1. all 258 reach the storefront
{
  const served = [
    ...WIX.map(online),
    ...[
      ["jau-mirror-fail", "X"], ["jau-mirror-ok", "Y"], ["jau-mirror-post", "Mirror Post"],
      ["jau-stock-a", "Stock Test jau-stock-a"], ["jau-stock-b", "Stock Test jau-stock-b"],
      ["jau-stock-dcf", "Stock Test jau-stock-dcf"], ["jau-stock-dec", "Stock Test jau-stock-dec"],
      ["jau-stock-del", "Stock Test jau-stock-del"], ["jau-stock-dnp", "Stock Test jau-stock-dnp"],
      ["jau-stock-dpn", "Stock Test jau-stock-dpn"], ["jau-stock-em2", "Stock Test jau-stock-em2"],
      ["jau-stock-eml", "Stock Test jau-stock-eml"], ["jau-stock-idem", "Stock Test jau-stock-idem"],
      ["jau-stock-opr", "Stock Test jau-stock-opr"], ["jau-stock-opt", "Stock Test jau-stock-opt"],
      ["jau-stock-pay", "Stock Test jau-stock-pay"], ["jau-stock-rop", "Stock Test jau-stock-rop"],
      ["jau-mtot3318", "Tote bag"],
    ].map(([id, name]) => offlineFixture(id, name)),
  ]; // the production shape: 276 served, 258 online
  const { JA } = makeSandbox(served);
  await JA.reloadCatalog();
  const products = JA.products();
  check("all 258 online wix-* products reach the storefront", products.length === 258,
    `JA.products() returned ${products.length}`);
  const ids = new Set(products.map((p) => String(p.id)));
  const expected = new Set(WIX.map((p) => String(p.id)));
  check("every product id preserved on the way to the grid",
    ids.size === expected.size && [...expected].every((id) => ids.has(id)));
  check("offline rows stay hidden on the storefront",
    !ids.has("jau-stock-a") && !ids.has("jau-mtot3318"));
}

// ------------------------------------- 2. slug / sku clashes hide no distinct product
{
  const served = WIX.map(online);
  const byId = Object.fromEntries(served.map((p) => [p.id, p]));
  byId["wix-002"].slug = byId["wix-001"].slug;   // same slug, DIFFERENT names
  byId["wix-004"].sku = byId["wix-003"].sku;     // same sku, DIFFERENT names
  const { JA } = makeSandbox(served);
  await JA.reloadCatalog();
  const ids = new Set(JA.products().map((p) => String(p.id)));
  check("a shared slug never hides a different product",
    ids.has("wix-001") && ids.has("wix-002"), [...ids].length + " products rendered");
  check("a shared sku never hides a different product",
    ids.has("wix-003") && ids.has("wix-004"));
  check("the storefront still shows all 258", ids.size === 258, `got ${ids.size}`);
}

// --------------------------------------- 3. a re-created product renders exactly once
{
  const served = WIX.map(online);
  const reborn = { ...served.find((p) => p.id === "wix-005"),
                   id: "wix-005-recreated" };      // same name + slug + sku, fresh id
  served.push(reborn);
  const { JA } = makeSandbox(served);
  await JA.reloadCatalog();
  const ids = new Set(JA.products().map((p) => String(p.id)));
  check("a re-created product renders exactly once",
    ids.has("wix-005") && !ids.has("wix-005-recreated") && ids.size === 258,
    `got ${ids.size}`);
}

// --------------------- 4. locally remembered deletions reconcile with the server
{
  const served = WIX.map(online);
  const { JA, storage } = makeSandbox(served);
  // this device once deleted wix-005 and jau-gone-forever; the server has
  // since restored wix-005 (the reviewed catalogue import), but never had
  // jau-gone-forever
  storage.set("jaura_deleted", JSON.stringify(["wix-005", "jau-gone-forever"]));
  await JA.reloadCatalog();
  const ids = new Set(JA.products().map((p) => String(p.id)));
  check("a server-restored product is visible again on a device that once deleted it",
    ids.has("wix-005"));
  const remembered = JSON.parse(storage.get("jaura_deleted") || "[]");
  check("the stale local delete was forgotten, the server-unknown one kept",
    remembered.length === 1 && remembered[0] === "jau-gone-forever",
    `jaura_deleted = ${JSON.stringify(remembered)}`);
}

console.log(failures ? `\n${failures} storefront check(s) FAILED` : "\nall storefront checks passed");
process.exit(failures ? 1 : 0);
