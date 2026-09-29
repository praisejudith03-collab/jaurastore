// Admin social-link editor simulation (Node VM, no browser dependency).
//
// Boots the REAL js/store.js + js/admin.js and proves the Settings panel the
// shop owner actually uses:
//
//   * the four inputs (WhatsApp, Instagram, TikTok, Facebook) are rendered
//     by settingsForm() without throwing;
//   * socialPreview() swaps each logo from the LINK that was typed - a
//     Facebook address in the Instagram box previews the Facebook logo;
//   * a bare @handle is recognised and expanded, and a javascript: address
//     is reported as hidden rather than saved.
//
// Run with:  node tests/_admin_social_preview_sim.mjs
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

const storage = new Map();
const mk = (n) => ({
  nodeName: n, dataset: {}, style: {}, innerHTML: "", textContent: "",
  classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  setAttribute(k, v) { this[k] = String(v); },
  getAttribute(k) { return this[k] || ""; },
  removeAttribute(k) { delete this[k]; },
  appendChild() {}, addEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; },
});

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
  location: { href: "https://jaurastore.com.ng/admin.html", origin: "https://jaurastore.com.ng", protocol: "https:", host: "jaurastore.com.ng", pathname: "/admin.html", search: "" },
  history: { replaceState() {} },
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, site: {} }) }),
  alert() {}, confirm() { return true; }, prompt() { return ""; },
};
sandbox.document = {
  readyState: "loading",
  body: { dataset: { page: "admin" }, classList: { add() {}, remove() {}, toggle() {} }, addEventListener() {}, appendChild() {} },
  documentElement: { dataset: {}, classList: { add() {}, remove() {}, toggle() {} } },
  head: { querySelector: () => null, appendChild() {} },
  addEventListener() {}, dispatchEvent: () => true,
  querySelector: () => null, querySelectorAll: () => [],
  getElementById: () => null, createElement: () => mk("el"),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
// A real browser window exposes the event API; js/store.js registers window
// listeners (ja:store-status, focus, pageshow, ...) at load, so mirror it.
if (typeof sandbox.addEventListener !== "function") sandbox.addEventListener = () => {};
if (typeof sandbox.removeEventListener !== "function") sandbox.removeEventListener = () => {};
if (typeof sandbox.dispatchEvent !== "function") sandbox.dispatchEvent = () => true;
sandbox.I18N = { t: (k) => k, apply() {}, lang: () => "en" };
sandbox.JA_NET = { api: () => Promise.resolve({ ok: true }) };

vm.createContext(sandbox);
vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
vm.runInContext(adminSrc, sandbox, { filename: "js/admin.js" });

// ---------------------------------------------------- the settings markup
const html = vm.runInContext("settingsForm()", sandbox);
check("the settings panel renders one input per platform",
  ["social_whatsapp_url", "social_instagram_url", "social_tiktok_url", "social_facebook_url"]
    .every((n) => html.includes(`name="${n}"`)));
check("the panel is titled for the shop owner",
  html.includes("<summary>Social media links</summary>"));

// ------------------------------------------------- the live logo preview
const boxes = {};
const inputs = {
  social_whatsapp_url: { value: "" },
  social_instagram_url: { value: "https://www.facebook.com/jaurastore" },
  social_tiktok_url: { value: "@j_aura_store" },
  social_facebook_url: { value: "javascript:alert(1)" },
};
Object.keys(inputs).forEach((k) => { boxes[k] = mk("span"); });
sandbox.__form = {
  elements: { namedItem: (n) => inputs[n] || null },
  querySelector: (sel) => {
    const m = /data-social-preview="([^"]+)"/.exec(sel);
    return m ? boxes[m[1]] : null;
  },
};
const note = mk("p");
sandbox.document.getElementById = (id) => (id === "social-form-preview" ? note : null);
vm.runInContext("socialPreview(__form)", sandbox);

check("an empty box previews the platform it stands for",
  boxes.social_whatsapp_url["data-social"] === "whatsapp"
  && boxes.social_whatsapp_url.innerHTML.includes("<svg"));
check("a Facebook link in the Instagram box previews the Facebook logo",
  boxes.social_instagram_url["data-social"] === "facebook",
  boxes.social_instagram_url["data-social"]);
check("a bare @handle is recognised and expanded",
  boxes.social_tiktok_url["data-social"] === "tiktok"
  && note.textContent.includes("https://www.tiktok.com/@j_aura_store"),
  note.textContent);
check("a javascript: address is reported as hidden, never as a link",
  note.textContent.includes("facebook: hidden"), note.textContent);

console.log(failures ? `\n${failures} admin social preview check(s) FAILED` : "\nall admin social preview checks passed");
process.exit(failures ? 1 : 0);
