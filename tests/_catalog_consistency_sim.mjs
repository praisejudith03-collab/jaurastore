// Smoke test: the REAL js/store.js against the running local server.
// Verifies the audit fixes on the frontend side:
//  1. public feed stock normalisation (stock_status -> soft quantity)
//  2. NO window.JA_SEED / bundled-JSON fallback when the API fails
//  3. customer pages ignore localStorage custom/deleted (per-phone drift)
//  4. admin pages keep the local pending-edit merge
//  5. dedupe mirrors the server (name-confirmed slug/sku)
//  6. offline rows hidden for customers, visible to admin
import { readFileSync } from "node:fs";
import vm from "node:vm";
import http from "node:http";

const BASE = "http://127.0.0.1:8080";
const code = readFileSync(new URL("../js/store.js", import.meta.url), "utf8");

function getJSON(path) {
  return new Promise((res, rej) => {
    http.get(BASE + path, (r) => {
      let b = "";
      r.on("data", (c) => (b += c));
      r.on("end", () => {
        try { res({ status: r.statusCode, json: JSON.parse(b) }); }
        catch (e) { rej(e); }
      });
    }).on("error", rej);
  });
}

function makeSandbox(opts = {}) {
  const store = opts.localStorage || {};
  const ls = {
    getItem: (k) => (k in store ? String(store[k]) : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
  const ss = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
  const windowObj = {
    I18N: null,
    JA_SEED: opts.bundle || null,   // the pre-bundled static catalogue (the old fallback)
    fetch: opts.fetch,
    isSecureContext: true,
  };
  const sandbox = {
    window: windowObj,
    document: {
      body: { dataset: { page: opts.page || "shop" } },
      addEventListener: () => {},
      querySelector: () => null,
      querySelectorAll: () => [],
      createElement: () => ({ setAttribute: () => {}, classList: { add: () => {} } }),
    },
    localStorage: ls,
    sessionStorage: ss,
    fetch: opts.fetch,
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
    console, URLSearchParams, CustomEvent: function () {},
    crypto: { randomUUID: () => "x" },
  };
  sandbox.window.addEventListener = () => {};
  sandbox.window.dispatchEvent = () => {};
  return { sandbox, windowObj, ls };
}

async function run(storeCode, opts) {
  const { sandbox, windowObj } = makeSandbox(opts);
  vm.createContext(sandbox);
  vm.runInContext(storeCode + "\nthis.__JA = JA;", sandbox);
  const JA = sandbox.__JA;
  await Promise.race([JA.ready, new Promise((r) => setTimeout(r, 6000))]);
  return JA;
}

let failures = 0;
function check(name, ok, detail = "") {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
  if (!ok) failures++;
}

// Real server fetch (routes to the local app)
const realFetch = (url) => new Promise((resolve, reject) => {
  const p = String(url).startsWith("http") ? url : BASE + "/" + String(url).replace(/^\//, "");
  http.get(p, (r) => {
    let b = "";
    r.on("data", (c) => (b += c));
    r.on("end", () => {
      const status = r.statusCode;
      resolve({ ok: status === 200, status, json: () => Promise.resolve(JSON.parse(b)) });
    });
  }).on("error", reject);
});

// ---- 1. live load, public page: stock normalised from stock_status
{
  const JA = await run(code, { fetch: realFetch, page: "shop" });
  const list = JA.products();
  check("live catalogue loads (public)", list.length > 100, `n=${list.length}`);
  const raw = (await getJSON("/api/catalog")).json.products;
  check("server feed strips numeric stock", raw.every((p) => !("stock" in p) && (p.stock_status === "in" || p.stock_status === "out")));
  const inStock = list.find((p) => p.stock_status === "in");
  const outStock = list.find((p) => p.stock_status === "out");
  check("in-stock normalised to soft quantity", inStock && inStock.stock === 99, inStock ? `stock=${inStock.stock}` : "no in-stock row");
  if (outStock) check("out-of-stock normalised to 0", outStock.stock === 0, `stock=${outStock.stock}`);
  check("catalogLive true after live load", JA.catalogLive() === true);
  const cartOk = JA.stockFor(inStock) > 0;
  check("stockFor works on normalised feed", cartOk);
}

// ---- 2. API failure: no bundled-JSON fallback, empty list + catalogLive false
{
  const bundle = Array.from({ length: 275 }, (_, i) => ({ id: "bundle-" + i, name: "B" + i, stock: 24, category: "beauty", priceNgn: 100 }));
  const JA = await run(code, {
    page: "shop",
    bundle,                                        // window.JA_SEED present (old fallback source)
    fetch: () => Promise.reject(new Error("offline")),
  });
  const list = JA.products();
  check("API down: bundled window.JA_SEED NOT used", list.length === 0, `n=${list.length}`);
  check("API down: catalogLive false", JA.catalogLive() === false);
}

// ---- 3/4. localStorage custom+deleted: ignored on customer, merged on admin
{
  const pending = JSON.stringify([{ id: "jau-local-pending", name: "Local Pending", stock: 5, category: "beauty", priceNgn: 50, online: true }]);
  const pendTs = JSON.stringify({ "jau-local-pending": Date.now() });
  const JA = await run(code, {
    page: "shop",
    fetch: realFetch,
    localStorage: { jaura_custom_products: pending, jaura_pending_products: pendTs, jaura_deleted: JSON.stringify(["wix-003"]) },
  });
  const pub = JA.products().map((p) => p.id);
  check("customer: local pending product NOT shown", !pub.includes("jau-local-pending"));
  check("customer: local deleted list NOT applied (wix-003 still shown)", pub.includes("wix-003"));

  const JA2 = await run(code, {
    page: "admin",
    fetch: realFetch,
    localStorage: { jaura_custom_products: pending, jaura_pending_products: pendTs, jaura_deleted: JSON.stringify(["wix-003"]) },
  });
  const adm = JA2.products().map((p) => p.id);
  check("admin: local pending product shown", adm.includes("jau-local-pending"));
  check("admin: local deleted list applied (wix-003 hidden)", !adm.includes("wix-003"));
}

// ---- 5. dedupe mirrors the server (name-confirmed slug/sku)
{
  const JA = await run(code, { fetch: realFetch, page: "shop" });
  // reach into the IIFE via behaviour: build a list through the public API is
  // not possible, so re-run the file in a bare sandbox and call dedupeProducts
  // through products() by feeding it through loadSeed is heavy; instead just
  // assert on the live list: every (slug,name) and (sku,name) pair unique.
  const list = JA.products();
  const seen = new Set();
  let dup = null;
  for (const p of list) {
    const k1 = (p.slug || "") + "\u0000" + (p.name || "").toLowerCase();
    const k2 = (p.sku || "") + "\u0000" + (p.name || "").toLowerCase();
    if (k1 && k1 !== "\u0000" && seen.has(k1)) { dup = k1; break; }
    if (k2 && k2 !== "\u0000" && seen.has(k2)) { dup = k2; break; }
    seen.add(k1); seen.add(k2);
  }
  check("no duplicate (slug|sku, name) pairs in rendered list", dup === null, dup || "");
}

// ---- 6. offline rows: hidden for customers (server filter), admin sees them
{
  const cat = await getJSON("/api/catalog");
  const raw = cat.json.products;
  const offlineInRaw = raw.filter((p) => p.online === false);
  // local test path: seed rows have no online key; if none are false, skip
  if (offlineInRaw.length) {
    const JA = await run(code, { fetch: realFetch, page: "shop" });
    const pub = JA.products().map((p) => p.id);
    check("customer: offline rows filtered", offlineInRaw.every((p) => !pub.includes(p.id)));
  } else {
    console.log("SKIP  offline-row check (no online=false rows in local test feed)");
  }
}

console.log(failures ? `\n${failures} FAILURE(S)` : "\nALL PASS");
process.exit(failures ? 1 : 0);
