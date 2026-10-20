/* The real shop grid, in a real DOM, fed the real /api/catalog payload.
 *
 * tests/_store_sim.mjs proves the STORE keeps every product. This file proves
 * the GRID paints them: it boots the shipped shop.html (plus every js/*.js it
 * loads) in jsdom, stubs the three API answers with a payload the caller
 * controls, and then inspects the rendered cards, the pager, the category
 * pills and the photos.
 *
 * Why a DOM harness and not a unit test: the defect behind "I saved a product
 * and the shop does not show it" was in the paint path, not the data path -
 * renderShop() filtered the catalogue with a price range captured on the first
 * paint (before /api/site delivered the live CFA rate), so 259 products
 * collapsed to 1 card for a French visitor, while /api/catalog was serving all
 * of them. Nothing but actually rendering the page can catch that.
 *
 * Inputs (all optional; the pytest wrapper sets them):
 *   JA_GRID_CATALOG        path to the /api/catalog JSON body
 *   JA_GRID_CATEGORIES     path to the /api/categories JSON body
 *   JA_GRID_SITE           path to the /api/site JSON body (default: no rate)
 *   JA_GRID_LANG           "en" | "fr"            (default "en")
 *   JA_GRID_RATE           cfaRate handed to the page (default 0)
 *   JA_GRID_NEW_IDS        comma-separated ids that must land on page 1
 *   JA_GRID_UNKNOWN_CATEGORY  a category id that must still get a filter pill
 *
 * Exit codes: 0 all checks pass, 1 a check failed, 3 jsdom missing.
 *
 * Run directly:
 *   node tests/_storefront_grid_check.mjs
 */
import { readFileSync, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");
const require = createRequire(import.meta.url);

const candidates = [
  process.env.JA_JSDOM_DIR,
  join(ROOT, "node_modules"),
  join(process.cwd(), "node_modules"),
].filter(Boolean).map((dir) => (dir.endsWith("node_modules") ? dir : join(dir, "node_modules")));
let JSDOM = null;
let jsdomDir = null;
for (const dir of candidates) {
  try { ({ JSDOM } = require(join(dir, "jsdom"))); jsdomDir = dir; break; } catch (err) { /* next */ }
}
if (!JSDOM) {
  console.error("SKIP: jsdom is not installed (npm install jsdom, or set JA_JSDOM_DIR)");
  process.exit(3);
}

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
  ".webp": "image/webp", ".svg": "image/svg+xml", ".woff2": "font/woff2",
  ".ico": "image/x-icon",
};
const server = createServer((req, res) => {
  const path = decodeURIComponent((req.url || "/").split("?")[0]);
  const file = normalize(join(ROOT, path === "/" ? "index.html" : path));
  if (!file.startsWith(ROOT) || !existsSync(file) || !statSync(file).isFile()) {
    res.writeHead(404); res.end("not found"); return;
  }
  const ext = file.slice(file.lastIndexOf(".")).toLowerCase();
  res.writeHead(200, { "content-type": TYPES[ext] || "application/octet-stream" });
  res.end(readFileSync(file));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const BASE = "http://127.0.0.1:" + server.address().port + "/";

function readJson(path) {
  if (!path) return null;
  try { return JSON.parse(readFileSync(path, "utf8")); } catch (err) { return null; }
}
const CATALOG = readJson(process.env.JA_GRID_CATALOG) || { ok: true, products: [], meta: {} };
const CATEGORIES = readJson(process.env.JA_GRID_CATEGORIES) || { ok: true, categories: [] };
const SITE_ROW = readJson(process.env.JA_GRID_SITE) || {};
const LANG = (process.env.JA_GRID_LANG || "en").toLowerCase();
const RATE = Number(process.env.JA_GRID_RATE || 0) || 0;
const NEW_IDS = String(process.env.JA_GRID_NEW_IDS || "").split(",").map((s) => s.trim()).filter(Boolean);
const UNKNOWN_CATEGORY = String(process.env.JA_GRID_UNKNOWN_CATEGORY || "").trim();
const PRODUCTS = CATALOG.products || [];
const EXPECTED = PRODUCTS.length;

const SITE = {
  ok: true,
  site: {
    cfaRate: RATE,
    store_active: true,
    customer_care: { phone_primary: "+229 68 95 31 10", email: "jaurastore@gmail.com" },
    ...(SITE_ROW.site || SITE_ROW),
  },
};

function apiBody(url) {
  const u = String(url);
  if (u.includes("api/catalog")) return CATALOG;
  if (u.includes("api/categories")) return CATEGORIES;
  if (u.includes("api/site")) return SITE;
  if (u.includes("api/realtime-config")) return { ok: true, enabled: false };
  if (u.includes("api/most-viewed")) return { ok: true, items: [] };
  if (u.includes("api/config")) return { ok: true, csrf: "test", env: "production" };
  return { ok: true };
}

let failures = 0;
function check(name, ok, detail) {
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail !== undefined ? "  ->  " + detail : ""}`);
}

const dom = new JSDOM(readFileSync(join(ROOT, "shop.html"), "utf8"), {
  url: BASE + "shop.html",
  runScripts: "dangerously",
  resources: "usable",
  pretendToBeVisual: false,
  virtualConsole: (() => {
    const vc = new (require(join(jsdomDir, "jsdom")).VirtualConsole)();
    vc.on("jsdomError", (err) => console.error("jsdomError:", err && err.message));
    vc.on("error", (...args) => console.error("page error:", ...args));
    return vc;
  })(),
  beforeParse(window) {
    window.AbortSignal = globalThis.AbortSignal;
    window.IntersectionObserver = class {
      constructor(cb) { this.cb = cb; }
      observe() {} unobserve() {} disconnect() {}
    };
    window.matchMedia = () => ({
      matches: false, addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {},
    });
    window.scrollTo = () => {};
    window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
    window.fetch = (url) => {
      const payload = JSON.stringify(apiBody(url));
      return Promise.resolve({
        ok: true, status: 200,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve(JSON.parse(payload)),
        text: () => Promise.resolve(payload),
      });
    };
    Object.defineProperty(window.navigator, "language", {
      value: LANG === "fr" ? "fr-FR" : "en-NG", configurable: true,
    });
    Object.defineProperty(window.navigator, "languages", {
      value: LANG === "fr" ? ["fr-FR", "fr"] : ["en-NG", "en"], configurable: true,
    });
    window.localStorage.setItem("jaura.lang", LANG);
  },
});
const window = dom.window;
const doc = window.document;

/* ``const JA`` is a top-level lexical binding, so it is NOT a property of
 * window - page scripts see it, the harness has to evaluate in the page. */
const evalInPage = (expr) => {
  try { return window.eval(expr); } catch (err) { return undefined; }
};
for (let i = 0; i < 120 && !(evalInPage("typeof JA !== 'undefined' && JA.products().length")); i += 1) {
  await new Promise((r) => setTimeout(r, 25));
}
await new Promise((r) => setTimeout(r, 400));

const cards = () => Array.from(doc.querySelectorAll("[data-shop-grid] article.card"));
const productIds = () => cards().map((c) => {
  const a = c.querySelector("a.card-media");
  return a ? decodeURIComponent((a.getAttribute("href") || "").replace("/products/", "")) : "";
});

/* 1. the catalogue reached the page at all */
const held = Number(evalInPage("JA.products().length")) || 0;
check("the page holds every online product the API served", held === EXPECTED,
  `${held} of ${EXPECTED}`);

/* 2. the filter pipeline is not silently dropping rows.
 *    Before the fix this was 259 -> 1 in French/CFA (frozen price floor) and
 *    259 -> 257 in English (new products priced 0). */
const filterState = evalInPage("JSON.stringify(shopFilter)");
let filtered = -1;
try {
  filtered = Number(evalInPage("applyShopFilters(JA.products()).length"));
} catch (err) { filtered = -1; }
check("an untouched price range is not an active filter",
  JSON.parse(filterState || "{}").touched === false && filtered === EXPECTED,
  `${filterState} -> ${filtered}`);

/* 3. the first page really rendered a full grid, not an empty state */
const pageSize = Math.min(24, EXPECTED);
check("the grid renders a full first page", cards().length === pageSize,
  `${cards().length} card(s), expected ${pageSize}`);

/* 4. the count line matches what the shopper is looking at */
const countLine = (doc.querySelector("[data-shop-count]") || {}).textContent || "";
const countNumber = Number((countLine.match(/\d+/) || [0])[0]);
check("the count line matches the catalogue", countNumber === EXPECTED, countLine.trim());

/* 5. recently saved rows open on page 1 (this is the actual complaint) */
if (NEW_IDS.length) {
  const names = cards().map((c) => (c.querySelector("h3")?.textContent || "").trim());
  const wanted = NEW_IDS.map((id) => {
    const row = PRODUCTS.find((p) => p.id === id) || {};
    return String(LANG === "fr" ? (row.nameFr || row.name || id) : (row.name || id));
  });
  const missing = wanted.filter((name) => !names.includes(name));
  check("a product saved just now opens on page 1 (Newest)", missing.length === 0,
    missing.length ? `missing from page 1: ${missing.join(", ")}` : names.slice(0, 3).join(" | "));
}

/* 6. a new/unknown category is still reachable from a pill */
if (UNKNOWN_CATEGORY) {
  const pills = Array.from(doc.querySelectorAll("[data-filters] a.cat-pill"));
  const found = pills.some((a) => (a.getAttribute("href") || "").includes(encodeURIComponent(UNKNOWN_CATEGORY)));
  check("a product's own category always gets a filter pill", found,
    pills.map((a) => (a.getAttribute("href") || "").replace("shop.html?cat=", "")).join(", "));
}

/* 7. photos: lazy, sized, and WebP-with-fallback where the repo has a sibling */
const imgs = Array.from(doc.querySelectorAll("[data-shop-grid] article.card img"));
check("every card carries a photo element", imgs.length === pageSize,
  `${imgs.length} of ${pageSize}`);
const notLazy = imgs.filter((img) => img.getAttribute("loading") !== "lazy");
check("grid photos are lazy-loaded", notLazy.length === 0, `${notLazy.length} not lazy`);
const unsized = imgs.filter((img) => !img.getAttribute("width") || !img.getAttribute("height"));
check("grid photos reserve their box (no layout shift)", unsized.length === 0,
  `${unsized.length} unsized`);
const noFallback = imgs.filter((img) => !(img.getAttribute("data-ph") || "").includes("placeholder"));
check("grid photos keep the branded fallback", noFallback.length === 0,
  `${noFallback.length} without a fallback`);
const pictures = Array.from(doc.querySelectorAll("[data-shop-grid] picture"));
const badTypes = pictures.filter((pic) => {
  const src = pic.querySelector("source");
  return !src || src.getAttribute("type") !== "image/webp";
});
check("repo photos offer a WebP source", pictures.length > 0 && badTypes.length === 0,
  `${pictures.length} picture(s)`);

const fabricated = Array.from(doc.querySelectorAll("[data-shop-grid] picture img"))
  .filter((img) => String(img.getAttribute("src") || "").indexOf("/uploads/") === 0);
check("an uploaded photo never claims a WebP sibling that may not exist",
  fabricated.length === 0, `${fabricated.length} fabricated source(s)`);

/* 8. no card can print a zero price - an unknown price reads "on request" */
const priceTexts = Array.from(doc.querySelectorAll("[data-shop-grid] .price"))
  .map((el) => el.textContent.replace(/\s+/g, " ").trim());
const zeroPrices = priceTexts.filter((t) => /^(₦|CFA|\$)?\s*0([.,]00)?\s*(CFA|₦)?$/i.test(t));
check("no card prints a zero price", zeroPrices.length === 0, zeroPrices.join(" | "));

/* 8b. a row whose price is not entered yet says so - it is not "0" */
const unpricedOnPage = (() => {
  const slugs = new Set(productIds());
  return PRODUCTS.some((p) => {
    const price = Number(p.priceNgn || 0) + Number(p.priceCfa || 0);
    const slug = p.slug || p.id;
    return price <= 0 && Array.from(slugs).some((s) => String(s).includes(slug));
  });
})();
if (unpricedOnPage) {
  const pending = Array.from(doc.querySelectorAll("[data-shop-grid] .price-pending"));
  check("an unpriced row reads as on-request, not as a zero price", pending.length >= 1,
    `${pending.length} on-request card(s)`);
}

/* 9. in French with a live rate, prices are CFA (the rate reached the cards) */
if (LANG === "fr" && RATE > 0) {
  const cfa = priceTexts.filter((t) => /CFA/i.test(t) && !/^0/i.test(t));
  check("French prices convert at the live exchange rate", cfa.length >= Math.min(10, priceTexts.length),
    `${cfa.length} of ${priceTexts.length}`);
}

console.log(failures ? `\n${failures} storefront grid check(s) failed` : "\nSTOREFRONT GRID CHECKS PASSED");
server.close();
process.exit(failures ? 1 : 0);
