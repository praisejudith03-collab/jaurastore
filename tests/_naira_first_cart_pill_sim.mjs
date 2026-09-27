// "Naira first" + the cart-drawer collision fix (owner request 2026-09-27).
//
// Boots the REAL js/i18n.js + js/store.js in a stubbed browser, once per
// simulated PAGE LOAD, sharing the storages the way a real phone does
// (localStorage survives everything, sessionStorage survives the visit).
//
// The rules under test:
//
//   1. English opens in ₦ Naira on EVERY fresh visit - even when an earlier
//      visit (or the French storefront, which forces CFA) left "CFA" behind
//      in localStorage. That stale value used to paint English shoppers'
//      prices in F CFA before they had asked for anything.
//   2. A manual tap on the compact pill is honoured for the rest of the
//      visit: page to page, product to checkout, the shop stays in F CFA.
//   3. A French load still locks FCFA, still cannot be talked out of it, and
//      never counts as the shopper's manual choice.
//   4. Opening the slide-out bag pushes the floating currency pill and the
//      WhatsApp bubble behind it (class + aria-hidden + inert), so neither
//      can sit on top of "View bag" / "Checkout"; closing it restores them.
//
// Run directly:   node tests/_naira_first_cart_pill_sim.mjs
// Or via pytest:  python3 -m pytest tests/test_naira_first_and_cart_pill.py -q
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const i18nSrc = readFileSync(path.join(root, "js", "i18n.js"), "utf8");
const storeSrc = readFileSync(path.join(root, "js", "store.js"), "utf8");

let failures = 0;
function check(name, ok, detail = "") {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
  if (!ok) failures++;
}

function storage(map) {
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    clear: () => map.clear(),
  };
}

function makeEl(tag) {
  const el = {
    tagName: (tag || "div").toUpperCase(),
    style: {}, dataset: {}, children: [], listeners: {}, attributes: {},
    innerHTML: "", textContent: "", className: "", hidden: false, value: "",
    classList: {
      _set: new Set(),
      add(...c) { c.forEach((x) => this._set.add(x)); },
      remove(...c) { c.forEach((x) => this._set.delete(x)); },
      toggle(c, force) {
        const on = force === undefined ? !this._set.has(c) : !!force;
        if (on) this._set.add(c); else this._set.delete(c);
        return on;
      },
      contains(c) { return this._set.has(c); },
    },
    setAttribute(k, v) { el.attributes[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(el.attributes, k) ? el.attributes[k] : null; },
    removeAttribute(k) { delete el.attributes[k]; },
    addEventListener(type, fn) { (el.listeners[type] = el.listeners[type] || []).push(fn); },
    removeEventListener() {},
    dispatchEvent(ev) { (el.listeners[ev.type] || []).forEach((fn) => fn(ev)); return true; },
    appendChild(c) { el.children.push(c); return c; },
    remove() {}, focus() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    matches: () => false,
    closest: () => null,
    click() { el.dispatchEvent({ type: "click", target: el, preventDefault() {}, stopPropagation() {} }); },
  };
  return el;
}

/** One page load. `local` / `session` are Maps shared between loads. */
function loadPage({ local, session, lang = "en", search = "" }) {
  const listeners = new Map();
  const pill = makeEl("div");
  const wa = makeEl("a");
  const mini = makeEl("aside");
  const mask = makeEl("div");
  const curButtons = ["NGN", "CFA"].map((cur) => {
    const b = makeEl("button");
    b.dataset.cur = cur;
    b.setAttribute("data-cur", cur);
    return b;
  });

  const sandbox = {
    console, setTimeout, clearTimeout, setInterval, clearInterval, AbortSignal,
    Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map, Promise, RegExp, Error,
    encodeURIComponent, decodeURIComponent, URL, URLSearchParams,
    CustomEvent: class CustomEvent {
      constructor(type, init) { this.type = type; this.detail = (init || {}).detail; }
    },
    localStorage: storage(local),
    sessionStorage: storage(session),
    navigator: {
      onLine: true,
      languages: lang === "fr" ? ["fr-BJ", "fr"] : ["en-NG", "en"],
      language: lang === "fr" ? "fr-BJ" : "en-NG",
    },
    location: {
      href: "https://jaurastore.com.ng/shop.html" + search,
      origin: "https://jaurastore.com.ng", protocol: "https:",
      host: "jaurastore.com.ng", pathname: "/shop.html", search,
    },
    history: { replaceState() {} },
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    addEventListener() {}, removeEventListener() {},
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, products: [], categories: [], site: {} }) }),
  };

  const doc = {
    readyState: "complete",
    body: makeEl("body"),
    documentElement: { dataset: {}, lang: "", classList: makeEl("html").classList },
    head: { querySelector: () => null, appendChild() {} },
    addEventListener: (type, fn) => {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    dispatchEvent: (ev) => {
      (listeners.get(ev.type) || []).forEach((fn) => { try { fn(ev); } catch (e) { console.error(e); } });
      return true;
    },
    getElementById: () => null,
    querySelector: (sel) => {
      if (sel === "[data-mini]") return mini;
      if (sel === "[data-mini-mask]") return mask;
      return null;
    },
    querySelectorAll: (sel) => {
      if (sel === "[data-cur]") return curButtons;
      if (sel === "[data-cur-float]") return [pill];
      if (sel === ".cur-float, .wa-float") return [pill, wa];
      return [];
    },
    createElement: (tag) => makeEl(tag),
    createTreeWalker: () => ({ nextNode: () => null }),
  };
  doc.body.dataset.page = "shop";
  sandbox.document = doc;
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.fallbackImg = () => {};

  vm.createContext(sandbox);
  vm.runInContext(i18nSrc, sandbox, { filename: "js/i18n.js" });
  vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
  const JA = vm.runInContext("JA", sandbox);
  return { sandbox, JA, els: { pill, wa, mini, mask, curButtons }, doc };
}

// ------------------------------------------------- 1. the shopper's journey
const local = new Map();
const session = new Map();

// --- visit 1, page 1: nothing stored yet.
{
  const { JA } = loadPage({ local, session });
  check("first English load opens in ₦ Naira", JA.currency() === "NGN", JA.currency());
  JA.setCurrency("CFA");                               // the shopper taps F CFA
  check("tapping F CFA switches the shop at once", JA.currency() === "CFA", JA.currency());
}
// --- visit 1, page 2: a real navigation (new JS context, same storages).
{
  const { JA } = loadPage({ local, session });
  check("the manual F CFA choice survives navigation inside the visit",
    JA.currency() === "CFA", JA.currency());
  JA.setCurrency("NGN");
  check("tapping ₦ switches back", JA.currency() === "NGN", JA.currency());
  JA.setCurrency("CFA");                               // leave CFA behind again
}
// --- visit 2: same phone, new session (tab closed and reopened).
{
  const fresh = new Map();
  check("localStorage still remembers the last currency",
    local.get("jaura_currency") === "CFA", String(local.get("jaura_currency")));
  const { JA } = loadPage({ local, session: fresh });
  check("a NEW English visit opens strictly in ₦ Naira", JA.currency() === "NGN", JA.currency());
  check("prices are formatted in Naira", JA.money(1000).startsWith("₦"), JA.money(1000));
  check("the pill is still offered (nothing is locked)", JA.currencyLocked() === false);
}

// ------------------------------------------------- 2. the French lock is safe
{
  const frSession = new Map();
  const { JA } = loadPage({ local, session: frSession, lang: "fr" });
  check("a French device still locks F CFA", JA.currencyLocked() === true && JA.currency() === "CFA");
  JA.setCurrency("NGN");
  check("setCurrency cannot talk French out of F CFA", JA.currency() === "CFA", JA.currency());
  check("the French lock is not recorded as a manual choice",
    !frSession.has("jaura_currency_manual"), JSON.stringify([...frSession]));
  // Same tab, shopper switches the interface to English (?lang=en).
  const en = loadPage({ local, session: frSession, lang: "fr", search: "?lang=en" });
  check("switching that session to English opens in ₦ Naira",
    en.JA.currency() === "NGN", en.JA.currency());
}

// ------------------------------------- 3. the bag never hides its own buttons
{
  const { JA, els, doc } = loadPage({ local, session: new Map() });
  check("the pill starts on top of the page", !els.pill.classList.contains("is-behind-cart"));
  JA.openMini();
  check("opening the bag marks the body", doc.body.classList.contains("mini-open"));
  check("the currency pill is pushed behind the cart pane",
    els.pill.classList.contains("is-behind-cart"));
  check("the WhatsApp bubble is pushed behind it too",
    els.wa.classList.contains("is-behind-cart"));
  check("the pill is hidden from screen readers while the bag is open",
    els.pill.getAttribute("aria-hidden") === "true");
  check("the bubble is hidden from screen readers too",
    els.wa.getAttribute("aria-hidden") === "true");
  JA.closeMini();
  check("closing the bag brings the pill back",
    !els.pill.classList.contains("is-behind-cart") && els.pill.getAttribute("aria-hidden") === null);
  check("closing the bag brings the WhatsApp bubble back",
    !els.wa.classList.contains("is-behind-cart") && els.wa.getAttribute("aria-hidden") === null);
  check("the body class is cleared", !doc.body.classList.contains("mini-open"));
}

console.log(failures ? `\n${failures} Naira-first / cart-pill check(s) FAILED`
                     : "\nall Naira-first and cart-pill checks passed");
process.exit(failures ? 1 : 0);
