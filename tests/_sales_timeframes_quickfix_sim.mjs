// Sales tab simulation (Node VM, no browser dependency).
//
// Boots the REAL js/store.js + js/admin.js and proves the Sales upgrade the
// owner asked for on 2026-11-16:
//
//   * the tab switches between Today / This week / This month / This year and
//     asks the server for that calendar window (`?period=…`), not a day count;
//   * revenue, net profit, orders and units all come from that same window;
//   * the "orders missing a cost price" warning is a control that opens those
//     exact orders, with a supplier-cost box per product line and a delivery
//     box, and saving sends the accounting order PATCH.
//
// Run with:  node tests/_sales_timeframes_quickfix_sim.mjs
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
    checked: false, disabled: false,
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

// Timeframe buttons live inside the panel markup; expose them as clickable
// stubs so the real handler bound by fillSales() can be driven.
const rangeStubs = new Map();
function rangeButtons() {
  ["today", "week", "month", "year", "all"].forEach((key) => {
    if (!rangeStubs.has(key)) rangeStubs.set(key, mk("button"));
    rangeStubs.get(key).dataset.salesRange = key;
  });
  return [...rangeStubs.values()];
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
    protocol: "https:", host: "jaurastore.com.ng", pathname: "/admin", search: "",
  },
  history: { replaceState() {} },
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  alert() {}, confirm() { return true; }, prompt() { return ""; },
};

const salesPayload = {
  ok: true,
  orders: 3,
  units: 5,
  revenue: 60000,
  revenueByCurrency: [{ currency: "NGN", orders: 3, value: 60000 }],
  averageOrderCurrency: "NGN",
  averageByCurrency: [{ currency: "NGN", orders: 3, average: 20000 }],
  averageOrderValue: 20000,
  pendingCount: 1,
  topProducts: [{ id: "p-1", name: "Silk scarf", units: 3, orders: 2 }],
  profitByCurrency: [{ currency: "NGN", orders: 2, revenue: 45000, supplierCost: 12000,
                       delivery: 1000, discount: 0, netProfit: 32000, missingCost: 1 }],
  netProfit: 32000,
  ordersMissingCost: 2,
  costComplete: false,
  period: "month",
  periodLabel: "This month",
  from: "2026-11-01",
  to: "2026-11-18",
  missingCostOrders: [
    { id: "JA-FIX-A", customer: "Ada Ngo", date: "2026-11-10", currency: "NGN",
      saleAmount: 20000, discount: 0, supplierCostNgn: 0, deliveryExpenseNgn: 0,
      itemsSummary: "2× Silk scarf, 1× Hair clip",
      lineItems: [
        { lineId: "line-01", name: "Silk scarf", variant: "Ivory", qty: 2, saleAmount: 12000, supplierCostNgn: 0 },
        { lineId: "line-02", name: "Hair clip", variant: "", qty: 1, saleAmount: 8000, supplierCostNgn: 0 },
      ] },
    { id: "JA-FIX-B", customer: "Bola Ade", date: "2026-11-11", currency: "NGN",
      saleAmount: 40000, discount: 0, supplierCostNgn: 0, deliveryExpenseNgn: 0,
      itemsSummary: "1× Handbag",
      lineItems: [
        { lineId: "line-01", name: "Handbag", variant: "", qty: 1, saleAmount: 40000, supplierCostNgn: 0 },
      ] },
  ],
};

const requests = [];
sandbox.fetch = (url) => {
  const u = String(url || "");
  requests.push(u);
  if (u.includes("api/catalog")) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, products: [], meta: {} }) });
  }
  if (u.includes("api/realtime-config")) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ enabled: false }) });
  }
  if (u.includes("api/admin/sales/insights")) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve({
      ok: true, summary: { currency: "NGN", revenue: 0, supplierCosts: 0, transport: 0, netProfit: 0 },
      batches: [], balances: {}, periodStart: "", periodEnd: "" }) });
  }
  if (u.includes("api/admin/sales")) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(salesPayload) });
  }
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, site: { cfaRate: 0.44 } }) });
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
  querySelectorAll: (sel) => (String(sel) === "[data-sales-range]" ? rangeButtons() : []),
  getElementById: (id) => element(id),
  createElement: (t) => mk(t),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.addEventListener = () => {};
sandbox.removeEventListener = () => {};
sandbox.dispatchEvent = () => true;
sandbox.I18N = { t: (k) => k, apply() {}, lang: () => "en" };

const apiCalls = [];
sandbox.JA_NET = {
  api: (p, opts) => {
    apiCalls.push({ path: String(p), opts: opts || {} });
    return Promise.resolve({ ok: true, entry: { id: "JA-FIX-A" } });
  },
  onStatus() {}, flush: () => Promise.resolve(0),
};

vm.createContext(sandbox);
vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
vm.runInContext(adminSrc, sandbox, { filename: "js/admin.js" });

// ------------------------------------------------------------ 1. the panel
const panel = vm.runInContext("salesPanel()", sandbox);
check("the Sales tab ships Today / This week / This month / This year",
  ["today:Today", "week:This week", "month:This month", "year:This year"]
    .every((pair) => {
      const [key, label] = pair.split(":");
      return panel.includes(`data-sales-range="${key}"`) && panel.includes(`>${label}</button>`);
    }),
  panel.slice(0, 420));
check("all-time stays available for the full history",
  panel.includes('data-sales-range="all"') && panel.includes(">All time</button>"));
check("the missing-cost editor container is in the panel",
  panel.includes('id="sales-cost-fix"'));

// ------------------------------------------------------- 2. the first paint
await vm.runInContext("fillSales()", sandbox);
const kpis = String(element("sales-kpis").innerHTML || "");
check("revenue, net profit, orders and units are all cards",
  kpis.includes("Confirmed revenue") && kpis.includes("Net profit")
  && kpis.includes("Orders") && kpis.includes("Units"),
  kpis);
check("the cards name the calendar window, not a day count",
  kpis.includes("This month"), kpis);
check("the first request is the This month window",
  requests.some((u) => u.includes("period=month")), requests.join(" | "));

const note = String(element("sales-cost-note").innerHTML || "");
check("the missing-cost warning is clickable",
  note.includes("sales-cost-fix-toggle") && note.includes("Add their supplier cost here"), note);
check("the warning still explains the effect on net profit",
  note.includes("2 orders") && note.includes("no cost price"), note);

// ------------------------------------------------- 3. opening the editor
vm.runInContext("salesCostFixOpen = true", sandbox);
await vm.runInContext("fillSales()", sandbox);
const fix = element("sales-cost-fix");
check("opening the warning reveals the editor", fix.hidden === false);
const fixHTML = String(fix.innerHTML || "");
check("both flagged orders are listed by id",
  fixHTML.includes("JA-FIX-A") && fixHTML.includes("JA-FIX-B"));
check("each product line gets its own supplier-cost box",
  (fixHTML.match(/data-cost-line="/g) || []).length === 3,
  `boxes=${(fixHTML.match(/data-cost-line="/g) || []).length}`);
check("the line ids the accounting editor validates are carried through",
  fixHTML.includes('data-line-id="line-01"') && fixHTML.includes('data-line-id="line-02"'));
check("the delivery expense has its own box", fixHTML.includes("data-cost-delivery"));

// ------------------------------------------------ 4. saving a typed cost
// Wire the boxes inside the rendered card the way the browser would.
const lineInputs = [
  Object.assign(mk("input"), { value: "8000", dataset: { costLine: "0", lineId: "line-01" } }),
  Object.assign(mk("input"), { value: "4000", dataset: { costLine: "1", lineId: "line-02" } }),
];
const deliveryInput = Object.assign(mk("input"), { value: "1500" });
const saveButton = Object.assign(mk("button"), { dataset: { costSave: "JA-FIX-A" } });
const feedback = mk("span");
fix.querySelectorAll = (sel) => {
  if (sel === "[data-cost-line]") return sel ? lineInputs : [];
  return [];
};
fix.querySelector = (sel) => {
  if (sel === "[data-cost-delivery]") return deliveryInput;
  if (sel === "[data-cost-save]") return saveButton;
  if (sel === "[data-cost-feedback]") return feedback;
  return null;
};
sandbox.document.querySelectorAll = (sel) => {
  if (String(sel) === "[data-sales-range]") return rangeButtons();
  if (String(sel) === "[data-cost-order]") {
    fix.dataset.costOrder = "JA-FIX-A";
    return [fix];
  }
  if (String(sel) === "[data-cost-feedback]") return [feedback];
  return [];
};

await vm.runInContext('saveSalesOrderCost("JA-FIX-A")', sandbox);
const patch = apiCalls.find((call) => /accounting\/orders\/JA-FIX-A/.test(call.path));
check("saving a cost calls the accounting order PATCH", Boolean(patch), JSON.stringify(apiCalls));
if (patch) {
  const body = patch.opts.json || {};
  check("the typed supplier costs are sent per product line",
    JSON.stringify(body.lineItems) === JSON.stringify([
      { lineId: "line-01", supplierCostNgn: 8000 },
      { lineId: "line-02", supplierCostNgn: 4000 },
    ]), JSON.stringify(body.lineItems));
  check("the delivery expense is saved with it", body.deliveryExpense === 1500,
    String(body.deliveryExpense));
}

// ------------------------------------------------- 5. switching timeframe
const yearButton = rangeStubs.get("year");
check("the timeframe buttons have click handlers", typeof (yearButton && yearButton.onclick) === "function");
requests.length = 0;
if (yearButton && yearButton.onclick) yearButton.onclick();
await new Promise((r) => setTimeout(r, 30));
check("clicking This year asks the server for the year window",
  requests.some((u) => u.includes("period=year")), requests.join(" | "));
check("the CSV export follows the same window",
  String(element("sales-csv").href).includes("period=year"), String(element("sales-csv").href));

const allButton = rangeStubs.get("all");
requests.length = 0;
if (allButton && allButton.onclick) allButton.onclick();
await new Promise((r) => setTimeout(r, 30));
check("All time keeps the original every-order contract",
  requests.some((u) => u.includes("days=all")) && !requests.some((u) => u.includes("period=all")),
  requests.join(" | "));

const lateErrors = [];
process.on("unhandledRejection", (err) => lateErrors.push(String((err && err.message) || err)));
if (lateErrors.length) {
  console.error("unhandled rejection in the shipped code: " + lateErrors.join(" | "));
  process.exit(1);
}
console.log(failures
  ? `\n${failures} Sales tab check(s) FAILED`
  : "\nall Sales tab checks passed");
process.exit(failures ? 1 : 0);
