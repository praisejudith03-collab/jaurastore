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
function makeSandbox(servedProducts, opts = {}) {
  const storage = new Map();
  const localStorage = {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
    clear: () => storage.clear(),
  };
  const fetched = [];
  const sandbox = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval, AbortSignal,
    Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map, Promise,
    fetch: (url) => {
      fetched.push(String(url));
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ ok: true, products: servedProducts, meta: { count: servedProducts.length } }),
      });
    },
    localStorage,
    sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    document: { body: { dataset: { page: opts.adminPage ? "admin" : "" } }, addEventListener: () => {}, createElement: () => ({ style: {}, setAttribute: () => {}, appendChild: () => {} }) },
    navigator: { onLine: true, language: "en" },
    location: { href: "https://jaurastore.com.ng/shop.html", origin: "https://jaurastore.com.ng", protocol: "https:", host: "jaurastore.com.ng", pathname: "/shop.html", search: "" },
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
  if (opts.phrases) {
    // The real French vocabulary (js/store.js displayOptionValue reads
    // window.I18N_OPTION_VALUES from it), so a colour assertion below proves
    // the shipped table, not a copy of it.
    vm.runInContext(
      readFileSync(path.join(root, "js", "i18n-phrases.js"), "utf8"),
      sandbox, { filename: "js/i18n-phrases.js" });
  }
  const JA = vm.runInContext("JA", sandbox);
  return { JA, storage, fetched, sandbox };
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

// ------------------------------- 5. the admin portal loads the FULL catalogue
{
  // The admin answer: every saved row incl. offline ones, with stock numbers.
  const withStock = (p) => ({ ...p, stock: 24, stock_quantity: 24 });
  const served = [...WIX.map((p) => withStock(online(p))),
                  ...[
                    ["jau-stock-a", "Stock Test jau-stock-a"], ["jau-mtot3318", "Tote bag"],
                  ].map(([id, name]) => ({ ...offlineFixture(id, name), stock: 3, stock_quantity: 3 }))];
  const { JA, fetched } = makeSandbox(served, { adminPage: true });
  await JA.reloadCatalog();
  const products = JA.products();               // admin page: no online filter
  check("admin portal requests the full catalogue (?all=1)",
    fetched.some((u) => String(u).includes("api/catalog?all=1")),
    `fetches: ${JSON.stringify(fetched)}`);
  check("admin portal sees every saved row, hidden ones included",
    products.length === served.length &&
    products.some((p) => p.id === "jau-mtot3318" && p.online === false),
    `JA.products() returned ${products.length} of ${served.length}`);
  check("admin portal keeps the stock numbers (public answer strips them)",
    products.every((p) => Number(p.stock) > 0 || p.online === false));
  // and the storefront still asks for the public list
  const { JA: JAShop, fetched: shopFetches } = makeSandbox(served);
  await JAShop.reloadCatalog();
  check("the storefront still requests the plain public catalogue",
    shopFetches.some((u) => String(u).includes("api/catalog")) &&
    !shopFetches.some((u) => String(u).includes("all=1")));
}

// ------------------- 6. the PUBLIC catalogue's stock shape reaches the UI
{
  // The public /api/catalog answer carries NO numeric stock - that is a
  // business secret. It ships stock_status ("in"/"out") plus, for a product
  // sold per variant, an option_stock map. The storefront decides "sold out"
  // from a NUMBER, so a public row used to read as stock 0 and the WHOLE shop
  // rendered "Out of stock" with Add-to-cart dead. normalizeServerProduct
  // translates the public shape on the way in.
  const publicRow = (id, name, extra) => ({
    id, sku: `PUB-${id}`, slug: id, name, category: "household",
    priceNgn: 5000, priceCfa: 2200, image: "images/products/_placeholder.jpg",
    online: true, ...extra,
  });
  const served = [
    publicRow("jau-pub-in", "In stock piece", { stock_status: "in" }),
    publicRow("jau-pub-out", "Sold out piece", { stock_status: "out" }),
    publicRow("jau-pub-opt", "Per-variant piece", {
      stock_status: "in", option_stock: { Red: 2, Blue: 0, Green: 5 },
    }),
    publicRow("jau-pub-none", "No status at all", {}),
  ];
  const { JA } = makeSandbox(served);
  await JA.reloadCatalog();
  const get = (id) => JA.products().find((p) => p.id === id);

  const inRow = get("jau-pub-in");
  check("a public \"in\" row becomes sellable stock",
    inRow && Number(inRow.stock) > 0, `stock = ${inRow && inRow.stock}`);
  check("an \"in\" product does NOT render the out-of-stock pill",
    !JA.cardHTML(inRow).includes("is-oos"));

  const outRow = get("jau-pub-out");
  check("a public \"out\" row becomes stock 0",
    outRow && Number(outRow.stock) === 0, `stock = ${outRow && outRow.stock}`);
  check("an \"out\" product DOES render the out-of-stock pill",
    JA.cardHTML(outRow).includes("is-oos"));

  const optRow = get("jau-pub-opt");
  check("option_stock becomes optionStock and the total is its sum",
    optRow && Number(optRow.stock) === 7 && optRow.optionStock
      && optRow.optionStock.Red === 2 && optRow.optionStock.Blue === 0,
    `stock = ${optRow && optRow.stock}`);
  check("stockFor answers per variant from the translated map",
    JA.stockFor(optRow, "Red") === 2
    && JA.stockFor(optRow, "Blue") === 0
    && JA.stockFor(optRow, "Green") === 5);
  check("a per-variant product with any stock left is not sold out",
    !JA.cardHTML(optRow).includes("is-oos"));

  check("a row with no stock_status at all stays sellable",
    Number(get("jau-pub-none").stock) > 0);

  // an ADMIN row (real number) must survive untouched
  const kept = JA.normalizeServerProduct({ id: "x", stock: 4, stock_status: "out" });
  check("a row that already carries a numeric stock is returned unchanged",
    kept.stock === 4);
  // a fully-zero option map reads as sold out
  const zero = JA.normalizeServerProduct({
    id: "z", stock_status: "in", option_stock: { S: 0, M: 0 } });
  check("a product whose every variant is 0 is sold out",
    zero.stock === 0);
}

// ------------------------- 8. the French storefront actually reads French
{
  // Every assertion below runs against the REAL js/store.js and the REAL
  // js/i18n-phrases.js vocabulary loaded into the same sandbox.
  const served = WIX.map(online);
  const { JA, sandbox } = makeSandbox(served, { phrases: true });
  await JA.reloadCatalog();

  const empty = JA.CATEGORIES.filter((c) => !String(c.nameFr || "").trim()).map((c) => c.id);
  check("every default category carries a French name", empty.length === 0,
    empty.length ? "missing nameFr: " + empty.join(", ") : "all " + JA.CATEGORIES.length + " filled");

  // English first: with no I18N loaded at all the shop must answer English.
  const p = JA.products().find((x) => x.id === "wix-001");
  check("before switching, the catalogue answers English",
    !JA.inFrench() && JA.displayName(p) === p.name, JA.displayName(p));

  // The switch itself: same row, same catalogue, French answer.
  sandbox.I18N = { lang: () => "fr" };
  check("a French shopper reads the French product name",
    JA.inFrench() && JA.displayName(p) === "10000 mah batterie externe", JA.displayName(p));
  check("a French shopper reads the French category name",
    JA.categoryName("beauty") === "Beauté & soins" && JA.categoryName("shoes") === "Chaussures",
    JA.categoryName("beauty") + " / " + JA.categoryName("shoes"));
  check("a product with no French name falls back to English, never blank",
    JA.displayName({ ...p, nameFr: "" }) === p.name);

  // Server rows may answer with either spelling of the French columns.
  const snake = JA.normalizeServerProduct({
    id: "jau-snake", name: "Snake Row", name_fr: "Ligne serpent",
    description_fr: "Décrite en français", stock: 4 });
  check("name_fr / description_fr fold onto nameFr / descriptionFr",
    snake.nameFr === "Ligne serpent" && snake.descriptionFr === "Décrite en français",
    snake.nameFr + " / " + snake.descriptionFr);
  check("camelCase French wins over a stale snake_case column",
    JA.normalizeServerProduct({ id: "jau-both", stock: 1,
      nameFr: "Gagnant", name_fr: "Perdant" }).nameFr === "Gagnant");
  check("a row with no French at all is returned unchanged (same object)",
    (() => { const row = { id: "jau-none", stock: 1 };
             return JA.normalizeServerProduct(row) === row; })());

  const bilingual = { description: "English copy", descriptionFr: "Texte en français" };
  check("displayDescription answers the French copy in French",
    JA.displayDescription(bilingual) === "Texte en français");
  sandbox.I18N = { lang: () => "en" };
  check("displayDescription answers the English copy in English",
    JA.displayDescription(bilingual) === "English copy");
  sandbox.I18N = { lang: () => "fr" };
  check("a missing French description falls back to English, not to blank",
    JA.displayDescription({ description: "Only English" }) === "Only English");

  // Option VALUES: the label translates, the identity does not.
  const opt = { title: "Colour", type: "DROP_DOWN", values: ["Black", "Light blue"] };
  check("a French shopper reads the colour in French",
    JA.displayOptionValue(opt, "Black") === "Noir"
    && JA.displayOptionValue(opt, "Light blue") === "Bleu clair",
    JA.displayOptionValue(opt, "Black") + " / " + JA.displayOptionValue(opt, "Light blue"));
  check("any capitalisation of a known colour translates",
    JA.displayOptionValue(opt, "black") === "Noir");
  check("displayOptionRaw keeps the untranslated variant identity",
    JA.displayOptionRaw("Black") === "Black" && JA.displayOptionRaw(null) === "");
  check("a hex swatch has no word to translate",
    JA.displayOptionValue({ type: "COLOR" }, "#800080") === "");
  check("an unknown option value is shown exactly as typed",
    JA.displayOptionValue(opt, "Chartreuse") === "Chartreuse");
  check("a per-product French value list wins over shared vocabulary",
    JA.displayOptionValue({ title: "Colour", values: ["Black"],
      valuesFr: ["Noir profond"] }, "Black") === "Noir profond");

  // The regression that would actually cost money: if the French label ever
  // reached the stock lookup, optionStock (keyed by the RAW value) would miss
  // and the variant would sell from the product total instead.
  const stocked = { id: "jau-fr-stock", name: "Stocked", nameFr: "En stock",
                    options: [opt], stock: 9, optionStock: { Black: 3, "Light blue": 0 } };
  check("stock is found by the raw value, not by the French label",
    JA.stockFor(stocked, "Black") === 3 && JA.stockFor(stocked, "Noir") === 9,
    "Black=" + JA.stockFor(stocked, "Black") + " Noir=" + JA.stockFor(stocked, "Noir"));
  check("a sold-out variant is still sold out in French",
    JA.stockFor(stocked, "Light blue") === 0);
}

// ---------------- 9. the owner's moving banner follows EN/FR, stock aliases stay in sync
{
  const served = WIX.map(online);
  const { JA, sandbox, storage } = makeSandbox(served, { phrases: true });
  await JA.reloadCatalog();

  // The banner ships as English + French fields on the site row; the
  // storefront picks per language and falls back to English.
  JA.setBanner("Back to school sale", "ends Sunday", "Soldes de rentrée");
  sandbox.I18N = { lang: () => "en" };
  const enBanner = JA.convBannerHTML();
  check("the moving banner answers English in English",
    enBanner.includes("Back to school sale") && !enBanner.includes("Soldes de rentrée"),
    enBanner.slice(0, 80));
  sandbox.I18N = { lang: () => "fr" };
  const frBanner = JA.convBannerHTML();
  check("the moving banner answers French in French",
    frBanner.includes("Soldes de rentrée") && !frBanner.includes("Back to school sale"),
    frBanner.slice(0, 80));
  check("the bold highlight rides along in both languages",
    enBanner.includes("ends Sunday") && frBanner.includes("ends Sunday"));
  JA.setBanner("English only banner", "", "");
  check("a French shopper falls back to the English banner when French is unwritten",
    JA.convBannerHTML().includes("English only banner"));

  // upsertProduct keeps the two stock aliases in lock-step (the server
  // prefers stock_quantity), so a caller setting only one never ships the
  // other as a stale value.
  await JA.upsertProduct({ id: "jau-sim-stock", name: "Sim stock", priceNgn: 0, stock: 5 });
  const custom = JSON.parse(storage.get("jaura_custom_products") || "[]");
  const row = custom.find((p) => String(p.id) === "jau-sim-stock");
  check("upsertProduct mirrors stock onto stock_quantity",
    !!row && Number(row.stock) === 5 && Number(row.stock_quantity) === 5,
    row ? JSON.stringify({ stock: row.stock, stock_quantity: row.stock_quantity }) : "missing");
  await JA.upsertProduct({ id: "jau-sim-stock2", name: "Sim stock 2", priceNgn: 0, stock_quantity: 9 });
  const custom2 = JSON.parse(storage.get("jaura_custom_products") || "[]");
  const row2 = custom2.find((p) => String(p.id) === "jau-sim-stock2");
  check("upsertProduct mirrors stock_quantity onto stock",
    !!row2 && Number(row2.stock) === 9 && Number(row2.stock_quantity) === 9,
    row2 ? JSON.stringify({ stock: row2.stock, stock_quantity: row2.stock_quantity }) : "missing");
}

{
  const { JA, storage, sandbox } = makeSandbox(WIX.map(online));
  await JA.ready;
  check("Household leads default categories", JA.categories()[0].id === "household");
  storage.set("jaura_categories", JSON.stringify([
    {id: "beauty", name: "Beauty", order: 2},
    {id: "household", name: "Household & Kitchen", order: 0},
    {id: "shoes", name: "Shoes", order: 2},
    {id: "perfume", name: "Perfume"},
  ]));
  check("category ordering preserves every category",
    JA.categories().map(c => c.id).join(",") === "household,perfume,beauty,shoes");
  const count = JA.products().length;
  sandbox.fetch = async () => { throw new Error("offline"); };
  let failed = false;
  try { await JA.reloadCatalog(); } catch { failed = true; }
  check("manual reload reports failure and preserves catalogue", failed && JA.products().length === count);
  sandbox.fetch = async () => ({ok: true, json: async () => ({products: []})});
  check("manual reload accepts an empty live catalogue", await JA.reloadCatalog() === 0);
}

console.log(failures ? `\n${failures} storefront check(s) FAILED` : "\nall storefront checks passed");
process.exit(failures ? 1 : 0);
