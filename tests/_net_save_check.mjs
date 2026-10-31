// Admin-save transport regression (Node, no browser needed).
//
// Boots the REAL js/net.js in a stubbed browser and proves the three
// transport rules an admin save depends on:
//
//   1. a caller may ask for MORE than five seconds. The old
//      Math.min(5000, ...) cap aborted every slow save, queued it and
//      re-sent it from scratch - the "saved in the portal, never in the
//      shop" report. A 5.6 s response under `timeout: 45000` must simply
//      succeed.
//   2. a server that ANSWERED and refused (5xx) must hand its reason back to
//      the caller, so the editor can print "the products table is missing the
//      column 'x'" instead of a silent queue entry.
//   3. a dropped connection (no server answer) stays quiet: the durable
//      outbox retries it and a scary toast would be wrong.
//
// Run directly:   node tests/_net_save_check.mjs      (exit 0 = all pass)
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const netSrc = readFileSync(path.join(here, "..", "js", "net.js"), "utf8");

let failures = 0;
function check(name, ok, detail = "") {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
  if (!ok) failures++;
}

function makeElement() {
  const classes = new Set();
  return {
    id: "", type: "", className: "", innerHTML: "", style: {}, parentNode: null,
    dataset: {},
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      toggle: (c, on) => (on === undefined ? (classes.has(c) ? classes.delete(c) : classes.add(c)) : (on ? classes.add(c) : classes.delete(c))),
      contains: (c) => classes.has(c),
    },
    addEventListener() {}, setAttribute() {}, removeAttribute() {},
    appendChild() {}, getAttribute() { return null; },
  };
}

function makeSandbox(handler) {
  const storage = new Map();
  const toasts = [];
  const localStorage = {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
    clear: () => storage.clear(),
  };
  const document = {
    readyState: "complete",
    hidden: false,
    head: makeElement(),
    body: Object.assign(makeElement(), { dataset: { page: "admin" } }),
    addEventListener() {}, dispatchEvent() {},
    querySelectorAll: () => [], querySelector: () => null,
    getElementById: () => null,
    createElement: () => makeElement(),
  };
  const sandbox = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map,
    Promise, Error, AbortController, AbortSignal, FormData,
    indexedDB: undefined,
    localStorage, sessionStorage: localStorage, document,
    navigator: { onLine: true },
    location: { href: "https://jaurastore.com.ng/admin.html" },
    fetch: handler,
    JA: { toast: (m) => toasts.push(String(m)) },
  };
  sandbox.window = sandbox;
  sandbox.window.addEventListener = () => {};
  sandbox.window.isSecureContext = false;
  vm.createContext(sandbox);
  vm.runInContext(netSrc, sandbox);
  return { sandbox, toasts };
}

function jsonResponse(status, body, delayMs = 0) {
  const send = () => ({
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  });
  return delayMs
    ? new Promise((resolve) => setTimeout(() => resolve(send()), delayMs))
    : Promise.resolve(send());
}

const CONFIG = { ok: true, status: 200, json: () => Promise.resolve({ csrf: "tok" }), text: () => Promise.resolve("{}") };

// 1. a slow save is honoured, not cut off at five seconds
{
  const started = Date.now();
  const { sandbox } = makeSandbox((url) => {
    if (String(url).indexOf("/api/config") >= 0) return Promise.resolve(CONFIG);
    return jsonResponse(200, { ok: true, product: { id: "jau-slow" }, live: true }, 5600);
  });
  const out = await sandbox.JA_NET.api("api/admin/products", {
    method: "POST", json: { product: { id: "jau-slow" } }, queue: true,
    label: "Product", timeout: 45000,
  });
  const elapsed = Date.now() - started;
  check("a 5.6 s save with timeout:45000 succeeds instead of being queued",
        !!(out && out.ok === true && out.product && out.product.id === "jau-slow"),
        JSON.stringify(out) + " after " + elapsed + " ms");
}

// 2. a server refusal keeps its reason for the editor to print
{
  const detail = "the products table is missing the column 'supplierCost'; run supabase_schema.sql";
  const { sandbox } = makeSandbox((url) => {
    if (String(url).indexOf("/api/config") >= 0) return Promise.resolve(CONFIG);
    return jsonResponse(503, { ok: false, error: "The product could not be saved to Supabase. No changes were made.", detail });
  });
  const out = await sandbox.JA_NET.api("api/admin/products", {
    method: "POST", json: { product: { id: "jau-refused" } }, queue: true,
    label: "Product", timeout: 45000,
  });
  check("a 503 comes back as queued WITH the server's detail",
        !!(out && out.queued === true && out.status === 503 && String(out.error).indexOf("supplierCost") >= 0),
        JSON.stringify(out));
}

// 3. a dropped connection stays quiet (the outbox retries it)
{
  const { sandbox } = makeSandbox((url) => {
    if (String(url).indexOf("/api/config") >= 0) return Promise.resolve(CONFIG);
    return Promise.reject(new TypeError("Failed to fetch"));
  });
  const out = await sandbox.JA_NET.api("api/admin/products", {
    method: "POST", json: { product: { id: "jau-offline" } }, queue: true,
    label: "Product", timeout: 45000,
  });
  check("a dropped connection is queued without a fake server message",
        !!(out && out.queued === true && !out.error),
        JSON.stringify(out));
}

if (failures) {
  console.error(`\n${failures} admin-save transport check(s) FAILED`);
  process.exit(1);
}
console.log("\nADMIN SAVE TRANSPORT CHECKS PASSED");
// js/net.js arms a 45 s outbox flush interval and a status pill; the check is
// finished, so leave without waiting for them.
process.exit(0);
