/* Live DOM check for the accounting desk (js/accounting.js).

   Runs the shipped bundle inside jsdom against recorded API answers and
   asserts the operator-visible behaviour the pytest source pins describe:
   the Starting Profit / opening balance box, the supplier link + unit price x
   quantity autosave with instant net-profit recalculation, the discount field
   hidden at zero, the Batch Transportation Fee fallback, the settings PUT and
   the Google verify call.

   Needs jsdom (`npm install jsdom`); the pytest wrapper skips without it.
   Run directly:  node tests/_accounting_desk_dom_check.mjs
*/
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");
const require = createRequire(import.meta.url);

// jsdom is a local developer dependency (there is no package.json in this
// repo on purpose); resolve it from the usual places, or from JA_JSDOM_DIR.
const candidates = [
  process.env.JA_JSDOM_DIR,
  join(ROOT, "node_modules"),
  join(process.cwd(), "node_modules"),
].filter(Boolean).map((dir) => (dir.endsWith("node_modules") ? dir : join(dir, "node_modules")));
let JSDOM = null;
for (const dir of candidates) {
  try { ({ JSDOM } = require(join(dir, "jsdom"))); break; } catch (err) { /* try the next */ }
}
if (!JSDOM) {
  console.error("SKIP: jsdom is not installed (npm install jsdom, or set JA_JSDOM_DIR)");
  process.exit(3);
}
const html = readFileSync(`${ROOT}/accounting.html`, "utf8");
const script = readFileSync(`${ROOT}/js/accounting.js`, "utf8");

const dom = new JSDOM(html, { runScripts: "outside-only", url: "https://jaurastore.example/admin/accounting" ,
  pretendToBeVisual: true });
const { window } = dom;

const settings = { startingBalanceNgn: 250000, startingBalanceCfa: 40000,
  startingProfitNgn: 250000, referenceSpreadsheetId: "1GnBgXl-VNoRzV-jiz4qCeb_BKzs31_Fu" };
const entries = [
  { id: "ORD-1", date: "2026-10-05T09:00:00Z", currency: "NGN", customer: "Ada Ngo",
    itemsSummary: "3× Silk scarf", itemQuantity: 3, saleAmount: 50000, discount: 0,
    discountPercent: 0, supplierCostNgn: 0, supplierCostCfa: 0,
    supplierUnitPriceNgn: 0, supplierQty: 3, supplierLink: "", supplierCostInCurrency: 0,
    deliveryExpense: 0, netProfit: 50000, netCashProfit: 50000, exchangeRate: 0.44,
    notes: "", legacySnapshot: false, deleted: false, archived: false },
  { id: "ORD-2", date: "2026-10-05T10:00:00Z", currency: "NGN", customer: "Bola Ade",
    itemsSummary: "1× Handbag", itemQuantity: 1, saleAmount: 40000, discount: 4000,
    discountPercent: 10, supplierCostNgn: 12000, supplierCostCfa: 5280,
    supplierUnitPriceNgn: 12000, supplierQty: 1, supplierLink: "https://sup.example/handbag",
    supplierCostInCurrency: 12000, deliveryExpense: 500, netProfit: 24000,
    netCashProfit: 23500, exchangeRate: 0.44, notes: "", legacySnapshot: false,
    deleted: false, archived: false },
  { id: "ORD-3", date: "2026-10-05T11:00:00Z", currency: "CFA", customer: "Koffi",
    itemsSummary: "2× Parfum", itemQuantity: 2, saleAmount: 20000, discount: 0,
    discountPercent: 0, supplierCostNgn: 8000, supplierCostCfa: 3520,
    supplierUnitPriceNgn: 4000, supplierQty: 2, supplierLink: "", supplierCostInCurrency: 3520,
    deliveryExpense: 0, netProfit: 16480, netCashProfit: 16480, exchangeRate: 0.44,
    notes: "", legacySnapshot: false, deleted: false, archived: false },
];
const payload = {
  ok: true, entries, batches: [], archivedOrderIds: [], expenses: [],
  settings, balances: {
    NGN: { currency: "NGN", startingBalance: 250000, startingProfit: 250000, salesNetProfit: 0,
           manualExpenses: 0, bankCharges: 0, balance: 250000 },
    CFA: { currency: "CFA", startingBalance: 40000, startingProfit: 40000, salesNetProfit: 0,
           manualExpenses: 0, bankCharges: 0, balance: 40000 } },
  currentExchangeRate: 0.44, legacyRate: 0.44, googleSheetId: "1GnBgXl-VNoRzV-jiz4qCeb_BKzs31_Fu",
  google: { configured: true, connected: true, email: "owner@example.com",
            ledgers: { NGN: { id: "ngn", url: "https://sheets/ngn" },
                       CFA: { id: "cfa", url: "https://sheets/cfa" } } },
  totals: { stagedOrders: 3, archivedOrders: 0, legacySnapshots: 0 },
};

const calls = [];
window.fetch = async (path, options = {}) => {
  calls.push({ path: String(path), method: (options.method || "GET"), body: options.body ? JSON.parse(options.body) : null });
  if (String(path).includes("/api/admin/session")) {
    return { ok: true, status: 200, json: async () => ({ authenticated: true, email: "owner@example.com", csrf: "tok" }) };
  }
  return { ok: true, status: 200, json: async () => payload };
};
window.confirm = () => true;
window.prompt = () => "10%";
window.Intl = Intl;

window.eval(script);
await new Promise((r) => setTimeout(r, 60));

const root = window.document.getElementById("accounting-root");
const text = root.innerHTML;
const fail = (msg) => { console.error("FAIL: " + msg); process.exit(1); };
const ok = (cond, msg) => { if (!cond) fail(msg); };

ok(text.includes("STARTING PROFIT / OPENING BALANCE"), "opening balance section at the top");
ok(/name="startingBalanceNgn"[^>]*value="250000"/.test(text), "opening NGN input carries the saved figure");
ok(text.indexOf("STARTING PROFIT / OPENING BALANCE") < text.indexOf("aa-balance-strip"), "opening box is above the balance strip");
ok(text.includes("aa-batch-transport"), "batch transportation fee box");
ok(text.includes("blank = per-order fees"), "blank fallback hint");
ok(text.includes('data-stage-edit="supplierLink"'), "supplier link input");
ok(text.includes('data-stage-edit="supplierUnitPriceNgn"'), "unit supplier price input");
ok(text.includes('data-stage-edit="supplierQty"'), "quantity input");
ok(text.includes("＋ discount"), "zero-discount row shows the add button only");
const discountInputs = [...root.querySelectorAll('[data-stage-edit="discount"]')];
ok(discountInputs.length === 1, "only the discounted row renders a discount field");
ok(discountInputs[0].value === "4000", "the applied discount is displayed: " + discountInputs[0].value);
ok(discountInputs[0].closest(".aa-stage-row").dataset.id === "ORD-2", "on the right row");
ok(text.includes("10% off"), "the discount percentage is shown");
ok((text.match(/＋ discount/g) || []).length === 1, "the two undiscounted rows keep the clean add button");
ok(text.includes("Test Google sync"), "verify button");
ok(text.includes("Supplier link · unit ₦ × qty"), "supplier column header explains the formula");

// Live recalculation: type a unit price + qty, the row's net profit moves now.
const row = root.querySelector('.aa-stage-row[data-id="ORD-1"]');
ok(!!row, "the staged row rendered");
const unit = row.querySelector('[data-stage-edit="supplierUnitPriceNgn"]');
const qty = row.querySelector('[data-stage-edit="supplierQty"]');
unit.value = "2000"; qty.value = "3";
unit.dispatchEvent(new window.Event("input", { bubbles: true }));
qty.dispatchEvent(new window.Event("input", { bubbles: true }));
await new Promise((r) => setTimeout(r, 30));
const profitText = row.querySelector("[data-row-profit]").textContent;
ok(profitText.includes("44,000"), "net profit recalculated live (50,000 - 6,000): " + profitText);
const totalText = row.querySelector("[data-cost-total]").textContent;
ok(totalText.includes("6,000"), "unit × qty total shown live: " + totalText);

// The batch fee recalculates the section totals as one cost for the batch.
const batchFee = root.querySelector("[data-batch-transport]");
batchFee.value = "1500";
batchFee.dispatchEvent(new window.Event("change", { bubbles: true }));
await new Promise((r) => setTimeout(r, 30));
const summary = root.querySelector("[data-stage-summary]").textContent;
ok(summary.includes("transport") && summary.includes("1,500"), "batch transport replaces per-order sum: " + summary);

// Debounced autosave fires a PATCH with the typed unit price.
await new Promise((r) => setTimeout(r, 1100));
const patch = calls.find((c) => c.method === "PATCH");
ok(!!patch, "the edit auto-saved");
ok(patch.body.supplierUnitPriceNgn === 2000 && patch.body.supplierQty === 3,
   "the debounced PATCH merged both typed fields: " + JSON.stringify(patch.body));

// Saving the opening balance PUTs the settings route.
const openingForm = root.querySelector("[data-opening-form]");
openingForm.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
await new Promise((r) => setTimeout(r, 40));
const put = calls.find((c) => c.method === "PUT");
ok(!!put && put.path.includes("/api/admin/accounting/settings"), "opening balance saves via PUT settings");
ok(put.body.startingBalanceNgn === 250000, "PUT carries the opening figure: " + JSON.stringify(put.body));

// Test Google sync hits the verify route.
root.querySelector('[data-action="verify-google"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 40));
ok(calls.some((c) => c.path.includes("/api/admin/accounting/google/verify")), "verify route called");

console.log("ACCOUNTING DESK DOM CHECKS PASSED");
