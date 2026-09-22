// F CFA ceiling simulation (Node, no browser needed).
//
// Boots the REAL js/store.js in a stubbed browser sandbox and proves the
// browser-side pricing pipeline applies the same ceiling the server applies
// in currency.py (round_cfa / to_cfa), catalog.py (_clean_cfa_prices) and
// api.py (_checkout_items):
//
//   1. roundCfa / toCfa: every F CFA amount lands on a clean 50 step and is
//      never rounded below the exact conversion (924 -> 950, 2,112 -> 2,150,
//      11,440 -> 11,450 - the figures reported on the live shop);
//   2. priceOf / compareOf: a served product's CFA price and "was" price are
//      ceiled, and a product priced in CFA only keeps that exact figure;
//   3. bulkUnit: a discounted CFA unit is ceiled - 2,150 at -10% is 1,935 in
//      the raw maths but 1,950 on the tag, the identical figure the server
//      bills - while Naira keeps ordinary Math.round and is never ceiled.
//
// Run directly:   node tests/_cfa_rounding_sim.mjs      (exit 0 = all pass)
// Or via pytest:  python3 -m pytest tests/test_cfa_rounding.py -q
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const storeSrc = readFileSync(path.join(root, "js", "store.js"), "utf8");

let failures = 0;
function check(name, ok, detail = "") {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
  if (!ok) failures++;
}

// ------------------------------------------------------------- browser sandbox
function makeSandbox() {
  const storage = new Map();
  const localStorage = {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
    clear: () => storage.clear(),
  };
  const sandbox = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval, AbortSignal,
    Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map, Promise,
    fetch: () => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ ok: true, products: [], meta: { count: 0 } }),
    }),
    localStorage,
    sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    document: { body: { dataset: { page: "" } }, addEventListener: () => {},
                createElement: () => ({ style: {}, setAttribute: () => {}, appendChild: () => {} }) },
    navigator: { onLine: true, language: "en" },
    location: { href: "https://jaurastore.com.ng/shop.html", origin: "https://jaurastore.com.ng",
                protocol: "https:", host: "jaurastore.com.ng", pathname: "/shop.html", search: "" },
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
  return vm.runInContext("JA", sandbox);
}

const JA = makeSandbox();
await JA.ready.catch(() => {});

// ------------------------------------------------- 1. the ceiling itself
check("roundCfa ceilings to the next 50 (the reported cases)",
  JA.roundCfa(924) === 950 &&
  JA.roundCfa(2112) === 2150 &&
  JA.roundCfa(11440) === 11450 &&
  JA.roundCfa(1935) === 1950,
  `got ${JA.roundCfa(924)}, ${JA.roundCfa(2112)}, ${JA.roundCfa(11440)}, ${JA.roundCfa(1935)}`);
check("roundCfa keeps clean steps and clamps bad input",
  JA.roundCfa(50) === 50 && JA.roundCfa(4400) === 4400 &&
  JA.roundCfa(0) === 0 && JA.roundCfa(-5) === 0 && JA.roundCfa("x") === 0);
check("toCfa derives at the house rate and ceilings",
  JA.toCfa(2100) === 950 && JA.toCfa(4800) === 2150 && JA.toCfa(26000) === 11450,
  `got ${JA.toCfa(2100)}, ${JA.toCfa(4800)}, ${JA.toCfa(26000)}`);

{
  let clean = true, neverBelow = true;
  for (let n = 1; n <= 5000; n += 7) {
    const c = JA.toCfa(n);
    if (c % 50 !== 0) clean = false;
    if (c + 1e-9 < n * 0.44) neverBelow = false;
  }
  check("every converted amount is a clean 50 step", clean);
  check("a conversion never rounds below the exact figure", neverBelow);
}

// ------------------------------------- 2. served prices are ceiled at read time
const cfaOnly = { id: "sim-cfa-only", name: "Sim CFA Piece", priceCfa: 2112,
                  stock: 5, online: true };
check("a CFA-only product's price is ceiled (2,112 -> 2,150)",
  JA.priceOf(cfaOnly, "CFA") === 2150, `got ${JA.priceOf(cfaOnly, "CFA")}`);
check("a CFA-only product's compare-at price is ceiled too (11,440 -> 11,450)",
  JA.compareOf({ ...cfaOnly, compareCfa: 11440 }, "CFA") === 11450,
  `got ${JA.compareOf({ ...cfaOnly, compareCfa: 11440 }, "CFA")}`);
check("an independently priced CFA figure is kept exactly as priced",
  JA.priceOf({ id: "sim-2", name: "Sim 2", priceCfa: 15000 }, "CFA") === 15000,
  `got ${JA.priceOf({ id: "sim-2", name: "Sim 2", priceCfa: 15000 }, "CFA")}`);

// ----------------------------------------------- 3. bulkUnit: the money path
const derived = { id: "sim-bulk", name: "Sim Bulk Piece", priceNgn: 4800,
                  stock: 50, online: true, bulkQty: 2, bulkPercent: 10 };
check("a discounted CFA unit is ceiled (2,150 @ -10% -> 1,950)",
  JA.bulkUnit(derived, 3, "CFA") === 1950, `got ${JA.bulkUnit(derived, 3, "CFA")}`);
check("at or below the threshold there is no discount to round",
  JA.bulkUnit(derived, 2, "CFA") === 2150 && JA.bulkUnit(derived, 1, "CFA") === 2150);
check("Naira keeps ordinary Math.round (4,800 @ -10% -> 4,320)",
  JA.bulkUnit(derived, 3, "NGN") === 4320, `got ${JA.bulkUnit(derived, 3, "NGN")}`);
check("an odd Naira unit is Math.round-ed, never ceiled (1,013 @ -10% -> 912)",
  JA.bulkUnit({ ...derived, priceNgn: 1013 }, 3, "NGN") === 912,
  `got ${JA.bulkUnit({ ...derived, priceNgn: 1013 }, 3, "NGN")}`);

const cfaBulk = { id: "sim-cfa-bulk", name: "Sim CFA Bulk", priceCfa: 2150,
                  stock: 9, online: true, bulkQty: 2, bulkPercent: 10 };
check("a CFA-only bulk product ceils the same way (2,150 @ -10% -> 1,950)",
  JA.bulkUnit(cfaBulk, 3, "CFA") === 1950, `got ${JA.bulkUnit(cfaBulk, 3, "CFA")}`);

{
  let sweepOk = true;
  for (const pct of [5, 7, 10, 15, 20, 33]) {
    const unit = JA.bulkUnit({ ...derived, bulkPercent: pct }, 3, "CFA");
    if (unit % 50 !== 0 || unit + 1e-9 < 2150 * (100 - pct) / 100) sweepOk = false;
  }
  check("every bulk percentage lands on a clean CFA step, never below the maths",
    sweepOk);
}

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nAll F CFA ceiling checks passed");
