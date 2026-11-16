/* Live DOM check for the accounting desk (js/accounting.js).

   Runs the shipped bundle inside jsdom against recorded API answers and
   asserts the operator-visible behaviour the pytest source pins describe:
   the Starting Profit / opening balance box, per-product supplier link/cost
   autosave with instant net-profit recalculation, the discount field hidden at
   zero, the Batch Transportation Fee fallback, the settings PUT and the
   Google verify call.

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
  startingProfitNgn: 250000, referenceSpreadsheetId: "1qT_YYqPt4pfTL7URxbePZnmFLLVljaRbBhh0qt_5AzE" };
const entries = [
  { id: "ORD-1", date: "2026-10-05T09:00:00Z", currency: "NGN", customer: "Ada Ngo",
    itemsSummary: "2× Silk scarf, 1× Hair clip", itemQuantity: 3, saleAmount: 50000, discount: 0,
    discountPercent: 0, supplierCostNgn: 0, supplierCostCfa: 0,
    supplierUnitPriceNgn: 0, supplierQty: 3, supplierLink: "", supplierCostInCurrency: 0,
    deliveryExpense: 0, deliveryExpenseNgn: 0, deliveryExpenseCfa: 0,
    netProfit: 50000, netCashProfit: 50000, exchangeRate: 0.44,
    lineItems: [
      { lineId: "line-01", name: "Silk scarf", qty: 2, saleAmount: 30000,
        supplierCostNgn: 0, supplierLink: "" },
      { lineId: "line-02", name: "Hair clip", qty: 1, saleAmount: 20000,
        supplierCostNgn: 0, supplierLink: "" },
    ],
    notes: "", legacySnapshot: false, deleted: false, archived: false },
  { id: "ORD-2", date: "2026-10-05T10:00:00Z", currency: "NGN", customer: "Bola Ade",
    itemsSummary: "1× Handbag", itemQuantity: 1, saleAmount: 40000, discount: 4000,
    discountPercent: 10, supplierCostNgn: 12000, supplierCostCfa: 27273,
    supplierUnitPriceNgn: 12000, supplierQty: 1, supplierLink: "https://sup.example/handbag",
    supplierCostInCurrency: 12000, deliveryExpense: 500, deliveryExpenseNgn: 500,
    deliveryExpenseCfa: 1136, netProfit: 23500, netCashProfit: 23500, exchangeRate: 0.44,
    lineItems: [{ lineId: "line-01", name: "Handbag", qty: 1, saleAmount: 40000,
      supplierCostNgn: 12000, supplierLink: "https://sup.example/handbag" }],
    notes: "", legacySnapshot: false, deleted: false, archived: false },
  { id: "ORD-3", date: "2026-10-05T11:00:00Z", currency: "CFA", customer: "Koffi",
    itemsSummary: "2× Parfum", itemQuantity: 2, saleAmount: 20000, discount: 0,
    discountPercent: 0, supplierCostNgn: 8000, supplierCostCfa: 18182,
    supplierUnitPriceNgn: 4000, supplierQty: 2, supplierLink: "", supplierCostInCurrency: 18182,
    deliveryExpense: 0, deliveryExpenseNgn: 0, deliveryExpenseCfa: 0,
    netProfit: 1818, netCashProfit: 1818, exchangeRate: 0.44,
    lineItems: [{ lineId: "line-01", name: "Parfum", qty: 2, saleAmount: 20000,
      supplierCostNgn: 8000, supplierLink: "" }],
    notes: "", legacySnapshot: false, deleted: false, archived: false },
];
// One sourcing / pre-order batch shape exactly as /api/admin/accounting
// returns it (owner request 2026-11-16).
const sourcingBatches = [
  { id: "SRC-AAAA1111", name: "Cotonou Batch 1", currency: "NGN",
    notes: "", createdAt: "2026-11-01T08:00:00Z", createdBy: "owner@example.com",
    orderIds: ["ORD-1", "ORD-2"],
    costs: [
      { id: "SRCC-0001", kind: "goods", label: "12 Ankara wrappers", qty: 12, amount: 12000, at: "2026-11-01T08:10:00Z" },
      { id: "SRCC-0002", kind: "transport", label: "Cotonou waybill", qty: 1, amount: 3000, at: "2026-11-01T08:20:00Z" },
    ],
    orders: [
      { id: "ORD-1", currency: "NGN", saleAmount: 50000, discount: 0,
        supplierCostInCurrency: 0, deliveryExpense: 0,
        profitOverrideActive: false, archived: false },
      { id: "ORD-2", currency: "NGN", saleAmount: 40000, discount: 4000,
        supplierCostInCurrency: 12000, deliveryExpense: 500,
        profitOverrideActive: false, archived: false },
    ],
    totals: { currency: "NGN", orderCount: 2, totalSpent: 15000, revenue: 86000, netProfit: 71000 } },
];

const payload = {
  ok: true, entries, batches: [], archivedOrderIds: [], expenses: [],
  sourcingBatches, sourcingSummary: [{ currency: "NGN", batchCount: 1,
    totalSpent: 15000, revenue: 86000, netProfit: 71000 }],
  settings, balances: {
    NGN: { currency: "NGN", startingBalance: 250000, startingProfit: 250000, salesNetProfit: 0,
           manualExpenses: 0, bankCharges: 0, balance: 250000 },
    CFA: { currency: "CFA", startingBalance: 40000, startingProfit: 40000, salesNetProfit: 0,
           manualExpenses: 0, bankCharges: 0, balance: 40000 } },
  currentExchangeRate: 0.44, legacyRate: 0.44, googleSheetId: "",
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
ok(text.includes('data-stage-edit="supplierLink"'), "per-item supplier link input");
ok(text.includes('data-stage-edit="supplierCostNgn"'), "per-item supplier cost input");
ok(text.includes('data-stage-edit="saleAmount"'), "per-item selling price input");
ok(text.includes("＋ discount"), "zero-discount row shows the add button only");
const discountInputs = [...root.querySelectorAll('[data-stage-edit="discount"]')];
ok(discountInputs.length === 1, "only the discounted row renders a discount field");
ok(discountInputs[0].value === "4000", "the applied discount is displayed: " + discountInputs[0].value);
ok(discountInputs[0].closest(".aa-stage-row").dataset.id === "ORD-2", "on the right row");
ok(text.includes("10% off"), "the discount percentage is shown");
ok((text.match(/＋ discount/g) || []).length === 1, "the two undiscounted rows keep the clean add button");
ok(text.includes("Test Google sync"), "verify button");
ok(text.includes('href="/api/admin/accounting/google/ledger/NGN"'), "NGN button resolves the fixed workbook tab");
ok(text.includes('href="/api/admin/accounting/google/ledger/CFA"'), "CFA button resolves the fixed workbook tab");
ok(text.includes("1qT_YYqPt4pfTL7URxbePZnmFLLVljaRbBhh0qt_5AzE"), "fixed workbook ID is shown to the operator");
ok(text.includes("Supplier URL &amp; cost · line total"), "supplier column header explains itemized costs");

// Live recalculation: type a line supplier cost, the row's net profit moves now.
const row = root.querySelector('.aa-stage-row[data-id="ORD-1"]');
ok(!!row, "the staged row rendered");
ok(root.querySelectorAll('.aa-stage-row[data-id="ORD-1"]').length === 2,
   "a multi-product order renders one staging row for each product");
const cost = row.querySelector('[data-stage-edit="supplierCostNgn"][data-line-index="0"]');
cost.value = "6000";
cost.dispatchEvent(new window.Event("input", { bubbles: true }));
await new Promise((r) => setTimeout(r, 30));
const profitText = row.querySelector("[data-row-profit]").textContent;
ok(profitText.includes("44,000"), "net profit recalculated live (50,000 - 6,000): " + profitText);
const totalText = row.querySelector("[data-cost-total]").textContent;
ok(totalText.includes("6,000"), "per-line supplier cost total shown live: " + totalText);

// The batch fee recalculates the section totals as one cost for the batch.
const batchFee = root.querySelector("[data-batch-transport]");
batchFee.value = "1500";
batchFee.dispatchEvent(new window.Event("change", { bubbles: true }));
await new Promise((r) => setTimeout(r, 30));
const summary = root.querySelector("[data-stage-summary]").textContent;
ok(summary.includes("transport") && summary.includes("1,500"), "batch transport replaces per-order sum: " + summary);

// Debounced autosave fires a PATCH with the typed per-item supplier amount.
await new Promise((r) => setTimeout(r, 1100));
const patch = calls.find((c) => c.method === "PATCH");
ok(!!patch, "the edit auto-saved");
ok(patch.body.lineItems[0].supplierCostNgn === 6000,
   "the debounced PATCH preserves the per-item cost: " + JSON.stringify(patch.body));

// A manual net-profit override is immediate, signed, and saved separately.
// Previous autosaves re-render the table, so use the live row rather than a
// detached node captured before the batch-fee change.
const overrideRow = root.querySelector('.aa-stage-row[data-id="ORD-1"]');
const override = overrideRow.querySelector('[data-stage-edit="profitOverride"]');
override.value = "-1000";
override.dispatchEvent(new window.Event("input", { bubbles: true }));
await new Promise((r) => setTimeout(r, 30));
ok(overrideRow.querySelector("[data-row-profit]").textContent.includes("−₦1,000"),
   "manual override replaces the calculated net profit immediately");
await new Promise((r) => setTimeout(r, 1000));
const overridePatch = calls.filter((c) => c.method === "PATCH").at(-1);
ok(overridePatch.body.profitOverride === -1000,
   "the signed profit override is persisted by the PATCH");

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

// ------------------------------------------ sourcing / pre-order batches
const sourcingText = root.innerHTML;
ok(sourcingText.includes("SOURCING / PRE-ORDER BATCHES"), "sourcing batches section is on the desk");
ok(sourcingText.includes("Cotonou Batch 1"), "the batch is listed by name");
ok(sourcingText.includes("Total spent") && sourcingText.includes("Total revenue") && sourcingText.includes("Net profit"),
   "the three batch totals are labelled");
ok(/Total spent<\/span><b>₦15,000<\/b>/.test(sourcingText), "total spent sums the logged goods + waybill");
ok(/Total revenue<\/span><b>₦86,000<\/b>/.test(sourcingText), "total revenue comes from the assigned orders");
ok(/Net profit<\/span><b>₦71,000<\/b>/.test(sourcingText), "net profit = revenue - total spent");

const toggle = root.querySelector("[data-sourcing-toggle]");
ok(!!toggle, "the batch has an open/close control");
toggle.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 40));
const openText = root.innerHTML;
ok(openText.includes('data-sourcing-cost-form="SRC-AAAA1111"'), "an open batch exposes the itemised cost form");
ok(openText.includes("12 Ankara wrappers") && openText.includes("Cotonou waybill"), "logged cost lines are listed");
ok(openText.includes("Goods bought") && openText.includes("Transport / waybill"), "cost types are labelled");
ok(openText.includes('data-sourcing-order="ORD-1"') && openText.includes('data-sourcing-order="ORD-2"'),
   "the multi-select checklist lists the confirmed orders");
ok(root.querySelectorAll('[data-sourcing-order][checked]').length === 2,
   "the orders already in the batch are ticked");
ok(openText.includes('data-sourcing-line="ORD-1"'), "supplier cost can be typed per item inside the batch");

// Ticking the checklist assigns the order to the batch.
const cfaBox = root.querySelector('[data-sourcing-order="ORD-3"]');
const untick = root.querySelector('[data-sourcing-order="ORD-2"]');
if (untick) {
  untick.checked = false;
  untick.dispatchEvent(new window.Event("change", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 40));
  const patchCall = calls.filter((c) => c.method === "PATCH" && c.path.includes("/sourcing/")).at(-1);
  ok(!!patchCall, "the checklist saves its selection");
  ok(JSON.stringify(patchCall.body.orderIds) === JSON.stringify(["ORD-1"]),
     "only the ticked orders are sent: " + JSON.stringify(patchCall && patchCall.body));
}

// Logging a cost line posts to the batch's cost route.
const costForm = root.querySelector('[data-sourcing-cost-form="SRC-AAAA1111"]');
ok(!!costForm, "the cost form is bound");
if (costForm) {
  costForm.querySelector('[name="kind"]').value = "transport";
  costForm.querySelector('[name="label"]').value = "Second waybill";
  costForm.querySelector('[name="qty"]').value = "1";
  costForm.querySelector('[name="amount"]').value = "2500";
  costForm.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 40));
  const post = calls.filter((c) => c.method === "POST" && c.path.includes("/sourcing/") && c.path.endsWith("/costs")).at(-1);
  ok(!!post, "adding a cost line calls the cost route");
  ok(post.body.kind === "transport" && post.body.amount === 2500,
     "the cost type and amount are sent: " + JSON.stringify(post && post.body));
}

// Deleting a batch asks first, then deletes only that batch.
const del = root.querySelector("[data-sourcing-delete]");
ok(!!del, "an old batch can be deleted");
if (del) {
  del.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 40));
  ok(calls.some((c) => c.method === "DELETE" && c.path.includes("/sourcing/SRC-AAAA1111")),
     "deleting a batch calls its DELETE route");
}

console.log("ACCOUNTING DESK DOM CHECKS PASSED");
