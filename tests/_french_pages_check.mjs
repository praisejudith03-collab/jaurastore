/* Every storefront page, in French, in a real DOM: zero English left behind.
 *
 * The owner's directive (2026-10-19) is absolute: a French device must never
 * read raw English. The data-i18n dictionary covers the markup, the phrase
 * table covers copy that JavaScript builds, and the server fills nameFr /
 * descriptionFr for catalogue rows. Nothing exercised all three together per
 * page - so a policy paragraph the phrase table missed, or a default the
 * footer paints from the site row, could stay English and every existing test
 * still passed. Three such holes were found this way on 2026-10-20:
 *
 *   1. privacy.html  "Only the partners needed to fulfil your order ..."
 *   2. shipping.html "Once your order is confirmed you will receive an
 *                     order ID. Message us on WhatsApp ..."  (the table only
 *                     carried an older, differently wrapped sentence)
 *   3. the customer-care block, whose English defaults were painted into
 *      every page footer and the contact page
 *
 * How it works: a local static server feeds the real shipped HTML + JS to
 * jsdom, the device language is French, and every rendered text node is
 * scanned for English function words ("the", "with", "your", ...). A node is
 * only reported when it holds TWO OR MORE of them, so a brand ("Jaura Store"),
 * a product name, a city or an amount never trips it - a sentence does.
 *
 * Exit codes: 0 clean, 1 English found, 3 jsdom missing (the pytest wrapper
 * skips locally and FAILS in CI, which installs jsdom).
 *
 * Run directly:  node tests/_french_pages_check.mjs
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
for (const dir of candidates) {
  try { ({ JSDOM } = require(join(dir, "jsdom"))); break; } catch (err) { /* next */ }
}
if (!JSDOM) {
  console.error("SKIP: jsdom is not installed (npm install jsdom, or set JA_JSDOM_DIR)");
  process.exit(3);
}

/* The pages a shopper can land on. admin.html / accounting.html are the
 * owner's tools and intentionally stay English. */
const PAGES = [
  "index.html", "shop.html", "product.html", "cart.html", "checkout.html",
  "categories.html", "wishlist.html", "account.html", "order-complete.html",
  "faq.html", "about.html", "contact.html", "delivery.html", "shipping.html",
  "returns.html", "privacy.html", "terms.html", "404.html",
];

/* Words that only appear in an English SENTENCE. Each is a function word with
 * a different French form, so a French sentence cannot contain two of them. */
const ENGLISH = new Set([
  "the", "and", "your", "you", "are", "for", "that", "this", "from", "have",
  "not", "will", "can", "when", "what", "how", "our", "its", "was", "were",
  "has", "but", "any", "been", "they", "their", "there", "which", "who",
  "these", "those", "then", "than", "into", "over", "after", "before",
  "also", "only", "more", "most", "some", "such", "other", "just", "like",
  "should", "would", "could", "must", "may", "about", "above", "under",
  "between", "through", "during", "against", "because", "while", "with",
  "we", "is", "of", "to", "in", "on", "by", "as", "at", "or", "if", "it",
]);

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
  ".webp": "image/webp", ".svg": "image/svg+xml", ".woff2": "font/woff2",
  ".ico": "image/x-icon",
};

/* `node tests/_french_pages_check.mjs privacy.html` checks just those pages;
 * the pytest wrapper always runs the full list. */
const ONLY = process.argv.slice(2).filter((a) => a.endsWith(".html"));
const TARGETS = ONLY.length ? ONLY : PAGES;

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

function englishNodes(window) {
  const doc = window.document;
  const walker = doc.createTreeWalker(doc.body, 4 /* SHOW_TEXT */, null, false);
  const hits = [];
  let node;
  while ((node = walker.nextNode())) {
    const parent = node.parentNode;
    if (!parent || !parent.nodeName) continue;
    if (/^(SCRIPT|STYLE|NOSCRIPT|TEXTAREA|CODE|PRE)$/.test(parent.nodeName)) continue;
    if (parent.closest && parent.closest("[data-no-i18n]")) continue;
    const text = String(node.nodeValue || "").replace(/\s+/g, " ").trim();
    if (text.length < 12) continue;
    const words = (text.toLowerCase().match(/[a-zà-ÿ'’-]+/g) || []);
    const en = words.filter((w) => ENGLISH.has(w)).length;
    // Two function words in one node is a sentence, not a brand or a price.
    if (en >= 2) hits.push({ text, en });
  }
  return hits;
}

/* ------------------------------------------------------------------ live API
 * The cold pass above is the first paint on a slow phone: nothing answers, so
 * the page shows its skeleton and its default copy. The SECOND pass is what
 * the shopper sees once /api/site and /api/catalog have answered - and that is
 * where dynamic copy is built (grid counts, order panels, footer rows painted
 * from the site row). A string that only ever appears after boot is exactly as
 * English as one baked into the HTML, so both passes are required.
 */
const LIVE_PRODUCTS = [
  {
    id: "wix-001", sku: "JAU001", slug: "10000-mah-power-bank",
    name: "10000 mah power bank", nameFr: "Batterie externe 10 000 mAh",
    category: "gadgets", priceNgn: 10000, priceCfa: 0,
    image: "images/products/10000-mah-power-bank.jpg",
    images: ["images/products/10000-mah-power-bank.jpg"],
    description: "Fast charging power bank", descriptionFr: "Batterie externe à charge rapide",
    stock_available: 5, stock_status: "in", online: true, badge: "", featured: true,
    colors: ["Black"], options: [{ title: "Colour", values: ["Black"] }],
  },
  {
    id: "wix-012", sku: "JAU012", slug: "24-k-nicotinamide-toner-300-ml",
    name: "24 k nicotinamide toner 300 ml", nameFr: "Tonique à la niacinamide 24 k 300 ml",
    category: "beauty", priceNgn: 0, priceCfa: 3500,
    image: "images/products/24-k-nicotinamide-toner-300-ml.jpg",
    images: ["images/products/24-k-nicotinamide-toner-300-ml.jpg"],
    description: "Brightening toner", descriptionFr: "Tonique éclaircissant",
    stock_available: 2, stock_status: "in", online: true, badge: "",
  },
  {
    /* Saved a moment ago: no price yet, a category the table does not know. */
    id: "jau-live-new", sku: "JAUNEW", slug: "nouveaute",
    name: "Mystery gift box", nameFr: "Boîte cadeau mystère",
    category: "brandnew-cat", priceNgn: 0, priceCfa: 0,
    image: "images/products/_placeholder.jpg", images: [],
    description: "Surprise box", descriptionFr: "Boîte surprise",
    stock_available: 0, stock_status: "out", online: true, badge: "new",
  },
];
const LIVE_CATALOG = {
  ok: true, products: LIVE_PRODUCTS,
  meta: { count: 3, publishedCount: 3, hiddenCount: 0 },
  homepageFeatured: {},
};
const LIVE_CATEGORIES = {
  ok: true,
  categories: [
    { id: "gadgets", name: "Gadgets / Electronics", nameFr: "Gadgets / Électronique", image: "images/categories/gadgets.jpg", hidden: false },
    { id: "beauty", name: "Beauty", nameFr: "Beauté & soins", image: "images/categories/beauty.jpg", hidden: false },
  ],
};
const LIVE_SITE = {
  ok: true,
  site: {
    cfaRate: 0.44, store_active: true, referralEnabled: false, promosEnabled: false,
    bulkDiscountTiers: [], minimumOrderEnabled: false, minOrderCfa: 0, minOrderNgn: 0,
    customer_care: {
      phone_primary: "+229 68 95 31 10", phone_secondary: "+234 916 167 0236",
      email: "jaurastore@gmail.com",
      business_contact_info: "Cotonou, Bénin",
      support_hours: "Lundi – samedi, 9h – 18h",
      support_details: "Notre équipe est disponible pour vos commandes.",
    },
  },
};

function liveApiBody(url) {
  const u = String(url);
  if (u.includes("api/catalog")) return LIVE_CATALOG;
  if (u.includes("api/categories")) return LIVE_CATEGORIES;
  if (u.includes("api/site")) return LIVE_SITE;
  if (u.includes("api/realtime-config")) return { ok: true, enabled: false };
  if (u.includes("api/most-viewed")) return { ok: true, items: [{ productId: "wix-001" }] };
  if (u.includes("api/config")) return { ok: true, csrf: "x", env: "production" };
  return { ok: true };
}

async function checkPage(page, live) {
  const dom = new JSDOM(readFileSync(join(ROOT, page), "utf8"), {
    url: BASE + page,
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: false,
    beforeParse(window) {
      if (!live) {
        // No backend in the cold harness: /api/site and /api/catalog never
        // answer, which is exactly the first visit a slow phone sees.
        window.fetch = () => new Promise(() => {});
      } else {
        window.AbortSignal = globalThis.AbortSignal;
        window.IntersectionObserver = class {
          constructor(cb) { this.cb = cb; }
          observe() {} unobserve() {} disconnect() {}
        };
        window.fetch = (url) => {
          const payload = JSON.stringify(liveApiBody(url));
          return Promise.resolve({
            ok: true,
            status: 200,
            headers: { get: () => "application/json" },
            json: () => Promise.resolve(JSON.parse(payload)),
            text: () => Promise.resolve(payload),
          });
        };
      }
      window.matchMedia = () => ({
        matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
      });
      window.scrollTo = () => {};
      window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
      Object.defineProperty(window.navigator, "language", { value: "fr-FR", configurable: true });
      Object.defineProperty(window.navigator, "languages", { value: ["fr-FR", "fr"], configurable: true });
      window.localStorage.setItem("jaura.lang", "fr");
    },
  });
  const window = dom.window;
  // Give the deferred scripts their turn, then drive the switch explicitly so
  // the check measures the FINAL French rendering, not a race.
  for (let i = 0; i < 80 && !window.I18N; i += 1) {
    await new Promise((r) => setTimeout(r, 25));
  }
  if (live) {
    // Wait for the catalogue/site round-trips the page really makes before it
    // is judged: the boot path paints in stages and the last stage is built
    // from the API answer.
    for (let i = 0; i < 120 && !(window.JA_SEED && window.JA_SEED.length); i += 1) {
      await new Promise((r) => setTimeout(r, 25));
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  // The boot path itself must have chosen French (store.js/app.js call
  // I18N.apply() on every page). If it did not, the page is broken for a
  // French phone no matter what a later manual apply() would produce - so
  // this is checked BEFORE the settle loop that re-applies the engine.
  const bootLang = window.document.documentElement.lang;
  // Boot paints in stages (mountChrome, then the catalogue paint, then the
  // late checkout/cart panels), and a slow runner can still be mid-paint when
  // the first scan runs. Re-apply the engine and re-scan until the page stops
  // changing, so a page is judged on its settled state - the same thing a
  // shopper sees once the spinner is gone - never on a half-painted one.
  let hits = [];
  let stable = 0;
  for (let i = 0; i < 8 && stable < 2; i += 1) {
    try {
      window.I18N.setLang("fr");
      window.I18N.apply();
    } catch (err) { /* boot paints fr anyway */ }
    await new Promise((r) => setTimeout(r, 180));
    const next = englishNodes(window);
    stable = next.length === hits.length && next.every((h, k) => h.text === hits[k].text)
      ? stable + 1 : 0;
    hits = next;
  }
  const lang = window.document.documentElement.lang;
  const bad = bootLang !== "fr" || lang !== "fr" || hits.length > 0;
  dom.window.close();
  return { bad, lang, bootLang, hits };
}

let failures = 0;
for (const page of TARGETS) {
  for (const live of [false, true]) {
    const { bad, lang, bootLang, hits } = await checkPage(page, live);
    const label = page + (live ? " (after API boot)" : " (cold)");
    if (bad) failures += 1;
    console.log((bad ? "FAIL  " : "PASS  ") + label + "  lang=" + lang
      + (bootLang !== "fr" ? "  BOOT LANG=" + bootLang : "")
      + (hits.length ? "  " + hits.length + " English node(s):" : ""));
    hits.slice(0, 4).forEach((h) => console.log("        [" + h.en + "] " + h.text.slice(0, 150)));
  }
}

server.close();
if (failures) {
  console.error(`\n${failures} page render(s) still show English for a French device`);
  process.exit(1);
}
console.log("\nFRENCH PAGES CHECKS PASSED");
process.exit(0);
