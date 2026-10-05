// Cart inventory-bound simulation (Node VM, no browser dependency).
//
// Boots the REAL js/store.js and drives the real cart functions to pin the
// Shopify-style stock contract the owner asked for on 2026-10-05:
//
//   * stock = N permits cart selection up to N (exactly N is accepted);
//   * selecting N + 1 is refused with "Only N items remaining in stock";
//   * the cap accounts for units already in the cart;
//   * a server 409 (another shopper got there first) is surfaced with the
//     server's own message and does not mutate the cart.
//
// Run with:  node tests/_stock_bounds_cart_sim.mjs
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

// The shop's own toast() paints into a .toast element; the harness keeps
// every painted message so the shopper-facing wording can be asserted.
const paintedToasts = [];
function mk(tag) {
  const el = {
    tagName: String(tag || "div").toUpperCase(),
    dataset: {}, style: {}, hidden: false, innerHTML: "", value: "",
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute(k, v) { this[k] = String(v); },
    getAttribute(k) { return this[k] == null ? "" : String(this[k]); },
    removeAttribute(k) { delete this[k]; },
    addEventListener() {}, removeEventListener() {}, appendChild() {}, append() {},
    remove() {}, focus() {}, click() {}, closest() { return null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
  Object.defineProperty(el, "textContent", {
    get() { return el._text == null ? "" : el._text; },
    set(v) { el._text = String(v); if (el.className === "toast") paintedToasts.push(String(v)); },
  });
  return el;
}

const storage = new Map();
const apiCalls = [];
let toastEl = null;
// The server answer the cart probe receives; tests rewrite it per case.
let serverReply = { ok: true, items: [] };
let serverError = null;

const catalogue = [
  { id: "p-five", name: "Five Left", priceNgn: 1000, stock: 5, stock_quantity: 5, online: true, category: "beauty" },
  { id: "p-none", name: "Sold Out", priceNgn: 1000, stock: 0, stock_quantity: 0, online: true, category: "beauty" },
  { id: "p-ten", name: "Ten Left", priceNgn: 1000, stock: 10, stock_quantity: 10, online: true, category: "beauty" },
];

const sandbox = {
  console, setTimeout, clearTimeout, setInterval, clearInterval, AbortSignal,
  Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map, Promise, RegExp,
  encodeURIComponent, decodeURIComponent, URL, URLSearchParams, FormData: class {},
  CustomEvent: class { constructor(t, i) { this.type = t; this.detail = (i || {}).detail; } },
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
  },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { onLine: true, language: "en" },
  location: { href: "https://jaurastore.com.ng/", origin: "https://jaurastore.com.ng", protocol: "https:", host: "jaurastore.com.ng", pathname: "/", search: "" },
  history: { replaceState() {} },
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  // The storefront boots its catalogue from api/catalog; serve the same rows
  // a live shop would so the cart validates against a real shelf count.
  fetch: (url) => {
    const u = String(url || "");
    if (u.includes("api/catalog")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, products: catalogue, meta: {}, categories: [] }) });
    }
    if (u.includes("api/realtime-config")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ enabled: false }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
  },
  alert() {}, confirm() { return true; }, prompt() { return ""; },
  JA_SEED: catalogue,
};
sandbox.document = {
  readyState: "loading",
  body: Object.assign(mk("body"), { dataset: { page: "shop" } }),
  documentElement: mk("html"),
  head: { querySelector: () => null, appendChild() {} },
  addEventListener() {}, dispatchEvent: () => true,
  querySelector: (sel) => {
    if (sel === ".toast") {
      if (!toastEl) { toastEl = mk("div"); toastEl.className = "toast"; }
      return toastEl;
    }
    return null;
  },
  querySelectorAll: () => [],
  getElementById: () => null,
  createElement: (t) => mk(t),
};
sandbox.document.body.appendChild = (el) => {
  if (el && el.className === "toast" && !paintedToasts.includes(el.textContent)) paintedToasts.push(el.textContent);
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.addEventListener = () => {};
sandbox.removeEventListener = () => {};
sandbox.dispatchEvent = () => true;
sandbox.I18N = { t: (k) => k, apply() {}, lang: () => "en" };
// The cart's pre-flight probe talks to the server through JA_NET. It either
// confirms the cart or rejects with the server's own stock error.
sandbox.JA_NET = {
  api: (p, opts) => {
    apiCalls.push({ path: p, opts });
    if (serverError) {
      // The shape net.js rejects with: the HTTP status, the server's message
      // and the parsed body all travel together.
      const err = new Error(serverError.error);
      err.status = serverError.status || 409;
      err.data = serverError;
      return Promise.reject(err);
    }
    return Promise.resolve(serverReply);
  },
  csrf: () => Promise.resolve("csrf"),
  pending: () => 0,
};

vm.createContext(sandbox);
vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
const errors = [];
process.on("unhandledRejection", (err) => errors.push(String((err && err.message) || err)));

const call = (expr) => vm.runInContext(expr, sandbox);
// First paint waits for the authoritative catalogue, exactly like the browser.
await vm.runInContext("JA.ready", sandbox).catch(() => {});
const toastText = () => paintedToasts[paintedToasts.length - 1] || "";
const clearToasts = () => { paintedToasts.length = 0; };

// ------------------------------------------- 1. the shelf count is the cap
check("stockFor reads the saved shelf count", call('JA.stockFor(JA.product("p-five"), "")') === 5);
check("a sold-out product reports zero", call('JA.stockFor(JA.product("p-none"), "")') === 0);

clearToasts();
const exact = await call('JA.addToCart("p-five", 5)');
check("stock = 5 permits exactly 5", exact === true && call('JA.cartQtyFor("p-five")') === 5,
  `ok=${exact} qty=${call('JA.cartQtyFor("p-five")')}`);

clearToasts();
const over = await call('JA.addToCart("p-five", 1)');
check("one more than the shelf is refused", over === false);
check("the shopper is told exactly what is left",
  toastText() === "Only 0 items remaining in stock", toastText());
check("the cart was not changed by the refusal", call('JA.cartQtyFor("p-five")') === 5);
check("a cart sitting exactly on the cap is not flagged",
  call('JA.stockProblems().length') === 0);
// A cart written by an older session (or another tab) that holds more than
// the shelf can still be detected and reported.
storage.set("jaura_cart", JSON.stringify([{ id: "p-five", qty: 6, color: "" }]));
check("a cart holding more than the shelf is reported",
  call('JA.stockProblems().length') === 1,
  JSON.stringify(call('JA.stockProblems()')));
check("the over-filled line is told what is really available",
  call('JA.stockProblems()[0].available') === 5 && call('JA.stockProblemLine()') === "Only 5 items remaining in stock",
  call('JA.stockProblemLine()'));
storage.delete("jaura_cart");

// ------------------------------------- 2. N + 1 from an empty cart
vm.runInContext('JA.clearCart()', sandbox);
vm.runInContext('localStorage.removeItem("jaura_cart")', sandbox);
clearToasts();
const oneOver = await call('JA.addToCart("p-ten", 11)');
check("an 11th unit on a 10-unit shelf is refused", oneOver === false);
check("the refusal names the exact remaining count",
  toastText() === "Only 10 items remaining in stock", toastText());
check("nothing was added", call('JA.cartQtyFor("p-ten")') === 0);

const tenOk = await call('JA.addToCart("p-ten", 10)');
check("exactly 10 units are accepted", tenOk === true && call('JA.cartQtyFor("p-ten")') === 10);

// ------------------------------------- 3. setQty enforces the same ceiling
clearToasts();
const grew = await call('JA.setQty("p-ten", "", 11)');
check("raising a line above the shelf count is refused", grew === false);
check("the line keeps its previous quantity", call('JA.cartQtyFor("p-ten")') === 10);
check("the message names the cap", toastText() === "Only 10 items remaining in stock", toastText());
const shrankOk = await call('JA.setQty("p-ten", "", 3)');
check("lowering a line is always allowed", shrankOk === true && call('JA.cartQtyFor("p-ten")') === 3);
const backUp = await call('JA.setQty("p-ten", "", 10)');
check("coming back up to the cap is allowed", backUp === true && call('JA.cartQtyFor("p-ten")') === 10);

// ------------------------------------- 4. a server 409 wins and is surfaced
vm.runInContext('JA.clearCart()', sandbox);
vm.runInContext('localStorage.removeItem("jaura_cart")', sandbox);
clearToasts();
serverError = { ok: false, code: "insufficient_stock", error: "Only 2 items remaining in stock" };
const raced = await call('JA.addToCart("p-five", 3)');
check("a server-side shortage refuses the add", raced === false);
check("the server's own message reaches the shopper",
  toastText() === "Only 2 items remaining in stock", toastText());
check("no cart line survives the refusal", call('JA.cartQtyFor("p-five")') === 0);
serverError = null;

// ------------------------------------- 5. sold out, and the message wording
clearToasts();
const soldOut = await call('JA.addToCart("p-none", 1)');
check("a sold-out product cannot be added", soldOut === false);
check("sold out says so plainly", toastText() === "Out of Stock", toastText());

const src = storeSrc;
check("the refusal string is the literal the server also uses",
  src.includes("`Only ${remaining} items remaining in stock`")
  && src.includes('return "Out of Stock";'));

if (errors.length) {
  console.error("unhandled rejection in the shipped code: " + errors.join(" | "));
  process.exit(1);
}
console.log(failures ? `\n${failures} stock-bound check(s) FAILED` : "\nall stock-bound checks passed");
process.exit(failures ? 1 : 0);
