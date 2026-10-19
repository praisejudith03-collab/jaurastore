// Locale selection / defaulting (owner request 2026-09-27, revised
// 2026-10-19) — Node, no browser.
//
// The rule under test (owner directive 2026-10-19, FINAL):
//
//   * THE DEVICE DECIDES, ON EVERY ENTRY. navigator.languages /
//     navigator.language reporting fr, fr-FR, fr-BJ, fr-CI… gives the French
//     storefront and F CFA prices; en / en-NG / en-US, or any unknown language,
//     gives English. List order is the browser's own, so ["en-NG","fr"] is
//     English and ["fr-FR","en"] is French. "without manual toggling" is
//     literal: THERE IS NO LANGUAGE SWITCH IN THE STOREFRONT — no header
//     control, no menu control, no floating pill — and these checks pin that.
//   * The one override is ?lang=fr|en on the link the visitor arrived on
//     (WhatsApp broadcasts, support links, QA). It is remembered for that
//     BROWSING SESSION (sessionStorage + a session cookie, which is also what
//     app.py reads for the first paint) so following a French link does not
//     flip back to English on the next page — and it dies with the tab, so a
//     new entry always re-consults the device.
//   * localStorage is RETIRED as a language store: it is never read, never
//     written, and migrateStaleLocale() CLEARS any value left under the key
//     from the era when a stored tap used to outrank the device forever. That
//     purge is what unsticks a visitor who would otherwise have no way out.
//   * Geography and IP are never consulted.
//   * A stale/garbage value under the session key (anything that isn't exactly
//     "en" or "fr") is purged so it can never leak French onto a fresh
//     English visitor.
//   * French lock: JA.currency() is "CFA" whenever the interface is French,
//     JA.setCurrency("NGN") cannot undo it, the floating currency pill is
//     hidden, and paintCheckoutTotals() surfaces the FCFA payment gateway
//     while hiding the Naira pay-card. The price is the live NGN->CFA rate from
//     the DB, with the stored per-product F CFA figure as the fallback.
//   * English default: JA.currency() is "NGN" on first load, the floating
//     pill's [data-cur] buttons recalculate prices without a reload (the tap
//     dispatches ja:currency, no navigation), and the checkout surfaces the
//     Naira gateway.
//   * The header carries NO language/currency switches, the slide-out menu
//     has no more static toggles, and the only floating controls are currency
//     (English mode) and the WhatsApp bubble.
//
// This harness boots the REAL js/i18n.js, js/store.js and js/app.js in a
// stubbed browser, exactly like tests/_homepage_currency_dom_sim.mjs.
//
// Run directly:   node tests/_auto_locale_sim.mjs   (exit 0 = all pass)
// Or via pytest:  python3 -m pytest tests/test_auto_locale_rules.py -q
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const i18nSrc = readFileSync(path.join(root, "js", "i18n.js"), "utf8");
const storeSrc = readFileSync(path.join(root, "js", "store.js"), "utf8");
const appSrc = readFileSync(path.join(root, "js", "app.js"), "utf8");

let failures = 0;
function check(name, ok, detail = "") {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
  if (!ok) failures++;
}

// ---------------------------------------------------------- i18n detection
function detect({ navTags = [], search = "", stored = null, staleStored = null } = {}) {
  // `stored`  = the value in sessionStorage  (the only thing still honoured)
  // `staleStored` = the value in localStorage (the RETIRED key: junk, or the
  //                 leftover of an explicit tap from before the pill went away)
  const session = new Map();
  const local = new Map();
  if (stored != null) session.set("jaura_lang", stored);
  if (staleStored != null) local.set("jaura_lang", staleStored);
  const sandbox = {
    console, URLSearchParams,
    navigator: { languages: navTags, language: navTags[0] || "", onLine: true },
    location: { search, href: "https://jaurastore.com.ng/" },
    document: { documentElement: { lang: "" }, cookie: "", dispatchEvent() {} },
    localStorage: {
      getItem: (k) => (local.has(k) ? local.get(k) : null),
      setItem: (k, v) => local.set(k, String(v)),
      removeItem: (k) => local.delete(k),
    },
    sessionStorage: {
      getItem: (k) => (session.has(k) ? session.get(k) : null),
      setItem: (k, v) => session.set(k, String(v)),
      removeItem: (k) => session.delete(k),
    },
    CustomEvent: class { constructor(t) { this.type = t; } },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(i18nSrc, sandbox, { filename: "js/i18n.js" });
  return { I18N: sandbox.I18N, session, local, storage: session };
}

// ------------------------------------------- 1. every visit follows the device
check('fresh visitor, no device signal -> English (the authored language)',
  detect({}).I18N.lang() === "en");
check('fresh visitor with a FRENCH browser locale -> French, without asking',
  detect({ navTags: ["fr-FR", "fr", "en"] }).I18N.lang() === "fr");
check('fresh visitor with fr-BJ device -> French',
  detect({ navTags: ["fr-BJ"] }).I18N.lang() === "fr");
check('fresh visitor with fr-CI device -> French',
  detect({ navTags: ["fr-CI", "fr"] }).I18N.lang() === "fr");
check('fresh visitor with an ENGLISH-first list -> English (browser order wins)',
  detect({ navTags: ["en-NG", "en", "fr"] }).I18N.lang() === "en");
check('fresh visitor with fr listed AFTER en -> English (the shop speaks en-NG)',
  detect({ navTags: ["en-US", "fr-FR"] }).I18N.lang() === "en");
check('fresh visitor, empty navigator languages -> English',
  detect({ navTags: [] }).I18N.lang() === "en");
check('any other browser language -> English default',
  detect({ navTags: ["es-ES", "de-DE"] }).I18N.lang() === "en");
check('the device locale is an observation, never persisted to either store',
  (() => {
    const { session, local } = detect({ navTags: ["fr-FR"] });
    return !session.has("jaura_lang") && !local.has("jaura_lang");
  })());
check('so nothing is latched: the NEXT entry re-consults the device',
  (() => {
    const first = detect({ navTags: ["fr-FR"] });
    first.I18N.lang();                       // French visit, nothing written
    return detect({ navTags: ["en-NG"] }).I18N.lang() === "en";
  })());

// ---------------------------------------------- 2. explicit selection sticks// ---------------------------------------------- 2. explicit selection sticks
check('?lang=fr is an explicit choice -> French',
  detect({ navTags: ["en-US"], search: "?lang=fr" }).I18N.lang() === "fr");
check('?lang=en is an explicit choice -> English, even on a French device',
  detect({ navTags: ["fr-FR"], search: "?lang=en" }).I18N.lang() === "en");
{
  const { I18N, session, local } = detect({ search: "?lang=fr" });
  check('the ?lang=fr link is remembered for the REST OF THE SESSION',
    I18N.lang() === "fr" && session.get("jaura_lang") === "fr");
  check('…and is never promoted to permanent storage — a retired key stays retired',
    !local.has("jaura_lang"));
}
check('a session-started French choice carries onto an English-tagged device',
  detect({ navTags: ["en-US", "en"], stored: "fr" }).I18N.lang() === "fr");
check('a session-started English choice carries onto a French-tagged device',
  detect({ navTags: ["fr-FR"], stored: "en" }).I18N.lang() === "en");
check('but a NEW entry (empty session) on that French device is French again',
  detect({ navTags: ["fr-FR"], stored: null }).I18N.lang() === "fr");

// -------------------------------------------- 3. stale storage is neutralised
check('a stale/garbage session value cannot force French',
  detect({ navTags: ["en-NG"], stored: "fr-old" }).I18N.lang() === "en");
check('a stale/garbage session value is actually removed from storage',
  (() => { const { session } = detect({ stored: "xx" }); return !session.has("jaura_lang"); })());
check('a LEGACY localStorage value can NEVER outvote the device — and is purged',
  (() => {
    const en = detect({ navTags: ["en-NG"], staleStored: "fr" });
    const frDevice = detect({ navTags: ["fr-FR"], staleStored: "en" });
    return en.I18N.lang() === "en" && !en.local.has("jaura_lang")
      && frDevice.I18N.lang() === "fr" && !frDevice.local.has("jaura_lang");
  })());
check('an empty-string stored value is not a choice — the device decides instead',
  detect({ navTags: ["fr-FR"], staleStored: "" }).I18N.lang() === "fr"
    && detect({ navTags: ["en-NG"], staleStored: "" }).I18N.lang() === "en");
check('an empty-string stored value is purged, not honoured',
  (() => { const { storage } = detect({ staleStored: "" }); return !storage.has("jaura_lang"); })());

const frI18n = detect({ stored: "fr" }).I18N;
check('home.kicker greeting is the polite English one',
  detect({}).I18N.t("home.kicker") === "Welcome. Ready to shop?");
check('home.kicker greeting translates for an explicitly French shopper',
  frI18n.t("home.kicker") === "Bienvenue. Prêt à faire vos achats ?",
  frI18n.t("home.kicker"));
check('the headline stays verbatim',
  detect({}).I18N.t("home.heroLine") === "Experience effortless elegance and curated essentials");
check('"Most viewed" heading is English by default',
  detect({}).I18N.t("home.mostViewed") === "Most viewed right now");
check('"Most viewed" heading translates for an explicit French choice',
  frI18n.t("home.mostViewed") === "Les plus consultés en ce moment");

// ------------------------------------------------- full storefront sandbox
const PRODUCTS = Array.from({ length: 4 }, (_, i) => ({
  id: `jau-00${i + 1}`, sku: `JAU00${i + 1}`, slug: `product-${i + 1}`,
  name: `Product ${i + 1}`, category: "household", priceNgn: 1000 * (i + 1),
  priceCfa: Math.round(1000 * (i + 1) * 0.44), image: "images/products/_placeholder.jpg",
  stock: 5, online: true,
}));

function makeEl(tag) {
  const el = {
    tagName: (tag || "div").toUpperCase(),
    style: {}, dataset: {}, children: [], listeners: {},
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
    setAttribute(k, v) { el.attributes[k] = String(v); if (k.startsWith("data-")) {
      const key = k.slice(5).replace(/-([a-z])/g, (m, c) => c.toUpperCase());
      el.dataset[key] = String(v);
    } },
    getAttribute(k) { return el.attributes[k] ?? null; },
    removeAttribute() {},
    addEventListener(type, fn) { (el.listeners[type] = el.listeners[type] || []).push(fn); },
    removeEventListener() {},
    dispatchEvent(ev) { (el.listeners[ev.type] || []).forEach((fn) => fn(ev)); return true; },
    appendChild(c) { el.children.push(c); c.parentNode = el; return c; },
    remove() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    matches() { return false; },
    closest() { return null; },
    focus() {}, click() { el.dispatchEvent({ type: "click", target: el, preventDefault() {}, stopPropagation() {} }); },
  };
  el.attributes = {};
  return el;
}

function makeStoreSandbox({ navTags = ["en-NG", "en"], search = "", stored = null, legacyLang = null } = {}) {
  // navTags defaults to an English device: the ordinary Lagos/Abuja visitor.
  // `stored` is the SESSION choice (what a ?lang= link leaves behind);
  // `legacyLang` is a value left in localStorage by the retired permanent
  // store, which must now be purged rather than honoured.
  const storage = new Map();
  const session = new Map();
  if (stored != null) session.set("jaura_lang", stored);
  if (legacyLang != null) storage.set("jaura_lang", legacyLang);
  const listeners = new Map();
  const sandbox = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval, AbortSignal,
    Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map, Promise,
    encodeURIComponent, decodeURIComponent, URL, URLSearchParams,
    CustomEvent: class CustomEvent {
      constructor(type, init) { this.type = type; this.detail = (init || {}).detail; }
    },
    localStorage: {
      // The RETIRED language store. Reading it here would be a bug, so the
      // sandbox is deliberately permissive: a legacy value may be seeded in
      // (seedLegacyLang) and the checks assert it is purged, never honoured.
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: (k) => storage.delete(k),
      clear: () => storage.clear(),
    },
    // sessionStorage holds both the once-per-tab welcome flag ("1") and the
    // session language choice, which is the only "stored" thing i18n honours.
    sessionStorage: {
      getItem: (k) => (session.has(k) ? session.get(k) : (k === "jaura_welcome_seen" ? "1" : null)),
      setItem: (k, v) => session.set(k, String(v)),
      removeItem: (k) => session.delete(k),
    },
    navigator: { onLine: true, languages: navTags, language: navTags[0] || "en" },
    location: {
      href: "https://jaurastore.com.ng/index.html", origin: "https://jaurastore.com.ng",
      protocol: "https:", host: "jaurastore.com.ng", pathname: "/index.html", search,
    },
    history: { replaceState() {} },
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    addEventListener() {}, removeEventListener() {},
    fetch: (url) => {
      const u = String(url);
      if (u.includes("api/catalog")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, products: PRODUCTS, meta: { count: PRODUCTS.length } }) });
      }
      if (u.includes("api/categories")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, categories: [] }) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, site: {} }) });
    },
  };

  // Fake DOM. querySelectorAll/ querySelector are selector-aware only where
  // the chrome wiring needs it; the header/footer land in innerHTML strings
  // we assert on directly.
  const headerSlot = makeEl("div");
  const footerSlot = makeEl("div");
  const pill = makeEl("div");
  pill.setAttribute("data-cur-float", "");
  const curButtons = ["NGN", "CFA"].map((cur) => {
    const b = makeEl("button");
    b.setAttribute("data-cur", cur);
    return b;
  });
  // NO language control is stubbed, because none may exist: the storefront has
  // nothing that writes jaura_lang except a ?lang= link. querySelectorAll
  // below therefore answers [] for [data-lang] on purpose, and the check that
  // the rendered chrome never asks for it is in the English block.
  const elements = { headerSlot, footerSlot, pill, curButtons };

  const doc = {
    readyState: "loading",
    body: makeEl("body"),
    documentElement: { dataset: {}, lang: "", classList: makeEl("div").classList },
    head: { querySelector: () => null, appendChild() {} },
    cookie: "",
    addEventListener: (type, fn) => {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    dispatchEvent: (ev) => {
      (listeners.get(ev.type) || []).forEach((fn) => { try { fn(ev); } catch (e) { console.error(e); } });
      return true;
    },
    getElementById: (id) => {
      if (id === "site-header") return headerSlot;
      if (id === "site-footer") return footerSlot;
      return null;
    },
    querySelector: () => null,
    querySelectorAll: (sel) => {
      if (sel.startsWith("body >")) return [];                 // nothing parked yet
      if (sel === "[data-cur]") return curButtons;
      if (sel === "[data-cur-float]") return [pill];
      return [];
    },
    createElement: (tag) => makeEl(tag),
    createTreeWalker: () => ({ nextNode: () => null }),
  };
  doc.body.dataset.page = "home";
  sandbox.document = doc;
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.fallbackImg = () => {};
  sandbox.listeners = listeners;
  sandbox.__els = elements;
  sandbox.__session = session;   // the live sessionStorage map (the language lives here)
  sandbox.__local = storage;     // the live localStorage map (retired for language)
  return sandbox;
}

async function bootShop(opts) {
  const sandbox = makeStoreSandbox(opts);
  vm.createContext(sandbox);
  vm.runInContext(i18nSrc, sandbox, { filename: "js/i18n.js" });
  vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
  await vm.runInContext("JA.ready", sandbox);
  // Mount the chrome exactly like app.js boot() does.
  vm.runInContext("JA.mountChrome()", sandbox);
  const JA = vm.runInContext("JA", sandbox);
  return { sandbox, JA };
}

// ---------------------------------------------------------- ENGLISH shop
// An English device and no stored choice: the ordinary Lagos/Abuja visitor.
{
  const { sandbox, JA } = await bootShop({ navTags: ["en-NG", "en"] });
  const els = sandbox.__els;
  check("an English device opens the English storefront", sandbox.I18N.lang() === "en");
  check("FCFA is NOT locked for English shoppers", JA.currencyLocked() === false);
  check("English default prices open in ₦ Naira", JA.currency() === "NGN");

  const header = els.headerSlot.innerHTML;
  const footer = els.footerSlot.innerHTML;
  check("header: menu slot comes before the logo (menu left)", header.indexOf("header-slot--left") < header.indexOf('<a class="logo"'));
  check("header: logo comes before the controls (logo centre)", header.indexOf('<a class="logo"') < header.indexOf("header-slot nav-right"));
  check("header: hamburger lives in the left slot", header.includes("data-open-menu") && header.indexOf("data-open-menu") < header.indexOf('<a class="logo"'));
  check("header: search + cart live in the right slot", header.includes("data-open-search") && header.includes("data-cart-icon"));
  check("header: no EN|FR switch", !header.includes("data-lang"));
  check("header: no ₦|F CFA switch", !header.includes('.currency-switch') && !header.includes('class="lang-switch"'));
  check("menu: the static bottom toggles are gone", !header.includes("au-menu-tools"));
  check("ticker: the moving banner is still mounted", header.includes("conv-bar") && header.includes("conv-track"));
  check("pill: mounted from the footer with both currencies",
    footer.includes("data-cur-float") && footer.includes('data-cur="NGN"') && footer.includes('data-cur="CFA"'));
  check("pill: mounts stacked above the WhatsApp bubble",
    footer.indexOf("data-cur-float") < footer.indexOf('class="wa-float"'));
  check("pill: visible on the English storefront", els.pill.hidden === false);
  check("WhatsApp bubble unchanged", footer.includes('class="wa-float"'));

  // Tap FCFA on the floating pill: prices recalculate without a reload.
  let currencyEvents = 0;
  const myListeners = sandbox.listeners.get("ja:currency") || [];
  currencyEvents = myListeners.length; // store.js itself registered one
  els.curButtons[1].click();
  check("tapping FCFA on the pill flips the shop (no reload)",
    JA.currency() === "CFA" && sandbox.location.href.endsWith("index.html"));
  check("the tap fired the repaint event",
    (sandbox.listeners.get("ja:currency") || []).length >= currencyEvents);
  // The actual recalculation: the SAME product re-prices in the new currency.
  // This sandbox has no live FX rate (toCfa() answers 0 rather than inventing
  // one), which is exactly the case where the storefront used to paint
  // "0 F CFA" — so the expectation is the conversion when there is one and the
  // catalogue's own F CFA figure (rounded up to the 50 step the shop prices on)
  // when there is not.
  const before = JA.priceOf(PRODUCTS[0], "NGN");
  const dbCfa = Math.ceil(PRODUCTS[0].priceCfa / 50) * 50;
  check("the sandbox really has no live rate (so the fallback below is being tested)",
    JA.toCfa(before) === 0);
  check("prices recalculate in F CFA after the tap",
    JA.priceOf(PRODUCTS[0]) === (JA.toCfa(before) || dbCfa),
    `NGN ${before} -> CFA ${JA.priceOf(PRODUCTS[0])}`);
  check("with no live rate a CFA price is the database figure, never 0",
    JA.priceOf(PRODUCTS[0]) === dbCfa && JA.priceOf(PRODUCTS[0]) > 0,
    `got ${JA.priceOf(PRODUCTS[0])}, catalogue ${PRODUCTS[0].priceCfa}`);
  check("money formats put the amount before the CFA suffix", JA.money(1000) === "1,000 CFA", JA.money(1000));
  els.curButtons[0].click();
  check("tapping ₦ flips back to Naira", JA.currency() === "NGN" && JA.priceOf(PRODUCTS[0]) === before);
  check("Naira formatting is restored", JA.money(1000).startsWith("₦"), JA.money(1000));

  // ---- no language control anywhere (owner directive 2026-10-19, revised) ----
  // The live rate must reach the screen, not just storage: on a slow phone the
  // grid paints before /api/site answers, and a francophone shopper's every
  // price is a conversion of it (owner rule: French = F CFA at the live rate).
  check("a live rate landing after the first paint re-prices what is on screen",
    (() => {
      const stale = JA.priceOf(PRODUCTS[0], "CFA");          // no rate yet -> DB figure
      let rerendered = 0, refetch = 0;
      sandbox.document.addEventListener("ja:rerender", () => { rerendered += 1; });
      sandbox.document.addEventListener("ja:currency", () => { refetch += 1; });
      JA.applySiteConfig({ cfaRate: 0.5 });
      return JA.toCfa(1000) === 500                          // the rate is live now
        && JA.priceOf(PRODUCTS[0], "CFA") !== stale          // and the price moved
        && rerendered === 1                                  // via an in-memory repaint
        && refetch === 0;                                    // not a full catalogue refetch
    })(),
    `priceOf -> ${JA.priceOf(PRODUCTS[0], "CFA")}, toCfa(1000) -> ${JA.toCfa(1000)}`);

  check("no language control is mounted in the footer",
    !footer.includes("data-lang") && !footer.includes("lang-float"));
  check("no language control is mounted anywhere in the chrome",
    !header.includes("data-lang") && !footer.includes("lang-switch")
      && !header.includes("lang-switch"));
  check("and the currency pill is still there, untouched, above the bubble",
    footer.includes("data-cur-float") && footer.indexOf("data-cur-float") < footer.indexOf('class="wa-float"'));
  check("the English shop can still switch CURRENCY (that control stays)",
    JA.currency() === "NGN" && JA.currencyLocked() === false);
}

// ---------------------------------------------- FRENCH DEVICE, first visit
// The requirement itself: a shopper whose phone speaks French lands in the
// French shop with live F CFA prices having tapped nothing — and with NOTHING
// to tap, because the storefront carries no language control. The way out is
// the device's own language setting, which is why the decision is never latched.
{
  const { sandbox, JA } = await bootShop({ navTags: ["fr-BJ", "fr"] });
  const els = sandbox.__els;
  check("device-only visitor gets the French storefront without any toggle",
    sandbox.I18N.lang() === "fr");
  check("French device: nothing was written to storage (a device is not a choice)",
    sandbox.localStorage.getItem("jaura_lang") === null);
  check("French device: FCFA is locked, so prices read in F CFA",
    JA.currencyLocked() === true && JA.currency() === "CFA");
  check("French device: the F CFA price comes from the active catalogue record, not 0",
    JA.priceOf(PRODUCTS[0]) === Math.ceil(PRODUCTS[0].priceCfa / 50) * 50
      && JA.priceOf(PRODUCTS[0]) > 0,
    `got ${JA.priceOf(PRODUCTS[0])}, catalogue ${PRODUCTS[0].priceCfa}`);
  check("French device: every product in the grid carries a price, so no card reads 0",
    PRODUCTS.every((p) => JA.priceOf(p) > 0),
    PRODUCTS.map((p) => JA.priceOf(p)).join("/"));
  check("French device: <html lang> is set for the first paint",
    sandbox.document.documentElement.lang === "fr",
    sandbox.document.documentElement.lang);
  const footer = els.footerSlot.innerHTML;
  check("French device: the currency pill is hidden (FCFA is locked in)",
    /hidden/.test(footer.slice(footer.indexOf("data-cur-float") - 90, footer.indexOf("data-cur-float") + 40)));
  check("French device: no language control appears even here",
    !footer.includes("data-lang") && !footer.includes("lang-float"));
  check("French device: leaving French means the device setting, not a tap — "
        + "and the shop follows immediately, because nothing was written",
    sandbox.localStorage.getItem("jaura_lang") === null
      && detect({ navTags: ["en-NG"] }).I18N.lang() === "en");
  check("French device: a LEGACY permanent choice is purged, never honoured",
    (() => {
      const stuck = makeStoreSandbox({ navTags: ["en-NG"], legacyLang: "fr" });
      vm.createContext(stuck);
      vm.runInContext(i18nSrc, stuck, { filename: "js/i18n.js" });
      return stuck.I18N.lang() === "en" && stuck.localStorage.getItem("jaura_lang") === null;
    })());
  const { sandbox: back } = await bootShop({ navTags: ["fr-BJ", "fr"], stored: "en" });
  check("French device: and the shop stays English on the next visit",
    back.I18N.lang() === "en");
}

// ---------------------------------------------------------- FRENCH shop
// French because of an explicit SESSION choice (a `?lang=fr` support link) —
// the device tags stay English on purpose: within a session an asked-for
// language outranks the device, which is what keeps a French link from flipping
// back to English on the next page.
{
  const { sandbox, JA } = await bootShop({ navTags: ["en-US", "en"], stored: "fr" });
  const els = sandbox.__els;
  check("explicit session choice -> French interface, even on an English device", sandbox.I18N.lang() === "fr");
  check("FCFA IS locked once French is explicitly active", JA.currencyLocked() === true);
  check("French prices open in F CFA", JA.currency() === "CFA");
  check("setCurrency cannot undo the French FCFA lock",
    (JA.setCurrency("NGN"), JA.currency() === "CFA"));
  const footer = els.footerSlot.innerHTML;
  check("pill: rendered but hidden in French mode",
    footer.includes("data-cur-float") && /\bhidden/.test(footer.slice(footer.indexOf("data-cur-float") - 90, footer.indexOf("data-cur-float") + 40)));
  check("pill: hidden property synced by refreshChrome", els.pill.hidden === true);
  check("body carries the ja-fr class backstop", els && sandbox.document.body.classList.contains("ja-fr"));

  // ---- checkout: all methods stay available; the selected one paints its own details ----
  vm.runInContext(appSrc, sandbox, { filename: "js/app.js" });
  const group = { selected: "naira" };
  const radio = (value) => ({
    value,
    get checked() { return group.selected === value; },
    set checked(on) { if (on) group.selected = value; },
  });
  const radios = {
    naira: radio("naira"),
    benin: radio("benin_cfa"),
    togo: radio("togo_cfa"),
  };
  const cards = Object.values(radios).map((r) =>
    Object.assign(makeEl("label"), { querySelector: () => r }));
  const boxes = {
    "[data-bank-ngn]": { hidden: false },
    "[data-bank-benin]": { hidden: true },
    "[data-bank-togo]": { hidden: true },
    "[data-ck-min-cfa]": { hidden: false },
  };
  const form = {
    dataset: {},
    querySelector: (sel) => sel === "[name=paymentMethod]:checked"
      ? Object.values(radios).find((r) => r.checked) || null : null,
    querySelectorAll: (sel) => sel === ".pay-card" ? cards : [],
  };
  const docQS = sandbox.document.querySelector;
  sandbox.document.querySelector = (sel) => boxes[sel] || docQS(sel);
  radios.benin.checked = true; // French storefront's default is Benin CFA.
  vm.runInContext("paintCheckoutTotals(form)", Object.assign(sandbox, { form }));
  check("French checkout defaults to the Benin CFA payment method", radios.benin.checked === true);
  check("all three payment-method cards remain available", cards.length === 3 && cards.every((card) => !card.hidden));
  check("Benin selection shows Benin details only", !boxes["[data-bank-benin]"].hidden
    && boxes["[data-bank-ngn]"].hidden && boxes["[data-bank-togo]"].hidden);
  check("Benin CFA selection derives CFA order currency",
    vm.runInContext("checkoutCurrency(form)", sandbox) === "CFA");
  radios.togo.checked = true;
  vm.runInContext("paintCheckoutTotals(form)", sandbox);
  check("Togo selection shows its separate detail sheet", !boxes["[data-bank-togo]"].hidden
    && boxes["[data-bank-benin]"].hidden);
  check("Togo notice copy is exact and present in checkout HTML",
    /Moov Money Togo may charge a fee for cross-border transfers\./.test(
      readFileSync(path.join(root, "checkout.html"), "utf8")));
  radios.naira.checked = true;
  vm.runInContext("paintCheckoutTotals(form)", sandbox);
  check("Naira selection shows the Naira sheet and derives NGN",
    !boxes["[data-bank-ngn]"].hidden
      && vm.runInContext("checkoutCurrency(form)", sandbox) === "NGN");
  sandbox.document.querySelector = docQS;
}

// ------------------------- navigating on / starting a new session
// A ?lang= link must survive the NEXT PAGE of the same visit (sessionStorage)
// and must NOT survive into a new visit, where the device rules again — in both
// directions. This is the whole point of the session-scoped store: a support
// link keeps working, and nobody is ever latched with no control to undo it.
{
  const { sandbox: s1 } = await bootShop({ navTags: ["en-US"], search: "?lang=fr" });
  const forSession = s1.sessionStorage.getItem("jaura_lang");
  check("?lang=fr is remembered for the session", forSession === "fr");
  check("?lang=fr is NOT promoted to permanent storage",
    s1.localStorage.getItem("jaura_lang") === null);

  // Navigating to another page of the same visit: no query string, same session.
  const { sandbox: s2, JA: JA2 } = await bootShop({ navTags: ["en-US"], stored: forSession });
  check("the next page of that visit is still French", s2.I18N.lang() === "fr");
  check("the next page of that visit is still priced in F CFA", JA2.currency() === "CFA");

  const { sandbox: s3 } = await bootShop({ navTags: ["fr-FR"], search: "?lang=en" });
  const forSessionEn = s3.sessionStorage.getItem("jaura_lang");
  const { sandbox: s4, JA: JA4 } = await bootShop({ navTags: ["fr-FR"], stored: forSessionEn });
  check("a ?lang=en link on a French device holds English for that visit", s4.I18N.lang() === "en");
  check("…and stays on ₦ Naira", JA4.currency() === "NGN");

  // A NEW visit (fresh session, nothing stored): the device decides again.
  const { sandbox: s5 } = await bootShop({ navTags: ["fr-FR"] });
  check("a NEW visit re-consults the device, ignoring the last session entirely",
    s5.I18N.lang() === "fr");
  const { sandbox: s6 } = await bootShop({ navTags: ["en-NG"] });
  check("a NEW visit on an English device is English, whatever the last one was",
    s6.I18N.lang() === "en");
}

// -------------------------- delivery-zone dropdown + "Most viewed" locale
// Both must render in whichever language is EXPLICITLY active — English by
// default, French only when explicitly chosen — never a mix.
async function localeSurfaceCheck(stored, label, navTags = ["en-NG", "en"]) {
  const { sandbox, JA } = await bootShop({ navTags, stored });
  vm.runInContext(appSrc, sandbox, { filename: "js/app.js" });

  // 1. Checkout delivery-zone dropdown groups.
  const zones = [
    { name: "Lekki", currency: "NGN", fare_min: 3000, fare_max: 6000 },
    { name: "Cotonou", currency: "CFA", fare_min: 1000, fare_max: 3000 },
    { name: "Store pickup", currency: "NGN", kind: "pickup" },
  ];
  const groups = vm.runInContext("zoneGroups", sandbox)(zones);
  const naira = groups.find((g) => g.id === "ngn");
  const cfa = groups.find((g) => g.id === "cfa");
  const pickup = groups.find((g) => g.id === "pickup");
  if (sandbox.I18N.lang() === "fr") {
    check(`[${label}] delivery-zone Naira group heading is French`, naira.label === "Nigéria (₦ Naira)", naira.label);
    check(`[${label}] delivery-zone F CFA group heading is French`, cfa.label === "Bénin & Togo (CFA)", cfa.label);
    check(`[${label}] delivery-zone pickup group heading is French`, pickup.label === "Retrait / point relais", pickup.label);
  } else {
    check(`[${label}] delivery-zone Naira group heading is English`, naira.label === "Nigeria (₦ Naira)", naira.label);
    check(`[${label}] delivery-zone F CFA group heading is English`, cfa.label === "Benin & Togo (CFA)", cfa.label);
    check(`[${label}] delivery-zone pickup group heading is English`, pickup.label === "Pickup / collection", pickup.label);
  }

  // 2. "Most viewed right now" heading + product-card buttons.
  const host = { dataset: {}, innerHTML: "" };
  const items = [
    { productId: PRODUCTS[0].id }, { productId: PRODUCTS[1].id },
    { productId: PRODUCTS[2].id }, { productId: PRODUCTS[3].id },
  ];
  sandbox.__mvHost = host;
  vm.runInContext("paintMostViewed(__mvHost, __mvItems)",
    Object.assign(sandbox, { __mvItems: items }));
  if (sandbox.I18N.lang() === "fr") {
    check(`[${label}] "Most viewed" heading is French`, host.innerHTML.includes("Les plus consultés en ce moment"));
    check(`[${label}] "Most viewed" add-to-cart button is French`, host.innerHTML.includes(">Ajouter au panier<"));
    check(`[${label}] "Most viewed" does not leak the English heading`, !host.innerHTML.includes("Most viewed right now"));
  } else {
    check(`[${label}] "Most viewed" heading is English`, host.innerHTML.includes("Most viewed right now"));
    check(`[${label}] "Most viewed" add-to-cart button is English`, host.innerHTML.includes(">Add to cart<"));
    check(`[${label}] "Most viewed" does not leak the French heading`, !host.innerHTML.includes("Les plus consultés"));
  }
}
await localeSurfaceCheck(null, "fresh visitor on an English device");
await localeSurfaceCheck("fr", "explicit French choice");
// …and a French DEVICE with no choice yet gets the same French surfaces,
// which is the 2026-10-19 requirement in the DOM rather than in i18n alone.
await localeSurfaceCheck(null, "French device, first visit", ["fr-CI", "fr"]);

// ------------------------------------------------ static checkout cleanup
{
  const appText = appSrc;
  check("the Benin/Togo currency popup helper is gone", !appText.includes("promptCurrencyForBeninTogo"));
  check('no more "delivery detected. Choose your currency" dialog', !appText.includes("delivery detected. Choose your currency"));
  check("no confirm() currency dialog left at checkout", !/confirm\(\s*msg\s*\)/.test(appText));
}

console.log(failures ? `\n${failures} auto-locale check(s) FAILED` : "\nall auto-locale checks passed");
process.exit(failures ? 1 : 0);
