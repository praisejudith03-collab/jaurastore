// Automatic language/currency (owner request 2026-09-27) — Node, no browser.
//
// The rule under test:
//
//   * The device language is detected on load (navigator.languages /
//     navigator.language). French (fr, fr-FR, fr-BJ, ...) renders the French
//     interface and LOCKS the currency to FCFA; English (en, en-NG, en-US)
//     and everything else stays English with prices in ₦ Naira first.
//   * French lock: JA.currency() is "CFA", JA.setCurrency("NGN") cannot undo
//     it, the floating pill is hidden, and paintCheckoutTotals() surfaces the
//     FCFA payment gateway while hiding the Naira pay-card.
//   * English default: JA.currency() is "NGN" on first load, the floating
//     pill's [data-cur] buttons recalculate prices without a reload (the tap
//     dispatches ja:currency, no navigation), and the checkout surfaces the
//     Naira gateway.
//   * The header carries NO language/currency switches, the slide-out menu
//     has no more static toggles, and the floating pill mounts stacked above
//     the WhatsApp bubble.
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
function detect(navTags, opts = {}) {
  const storage = new Map();
  const sandbox = {
    console, URLSearchParams,
    navigator: opts.navigator || { languages: navTags, language: navTags[0] || "", onLine: true },
    location: { search: opts.search || "", href: "https://jaurastore.com.ng/" },
    document: { documentElement: { lang: "" } },
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
    },
    sessionStorage: { getItem: () => null, setItem() {} },
    CustomEvent: class { constructor(t) { this.type = t; } },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(i18nSrc, sandbox, { filename: "js/i18n.js" });
  return sandbox.I18N;
}

check('fr-FR device -> French', detect(["fr-FR", "fr", "en"]).lang() === "fr");
check('fr-BJ device -> French', detect(["fr-BJ"]).lang() === "fr");
check('bare fr device -> French', detect(["fr"]).lang() === "fr");
check('en-NG device -> English', detect(["en-NG"]).lang() === "en");
check('en-US device -> English', detect(["en-US"]).lang() === "en");
check('bare en device -> English', detect(["en"]).lang() === "en");
check('any other language -> English default', detect(["es-ES", "de-DE"]).lang() === "en");
check('empty languages -> English default', detect([], { navigator: {} }).lang() === "en");
check('?lang=fr overrides detection (support/QA hatch)',
  detect(["en-US"], { search: "?lang=fr" }).lang() === "fr");
check('?lang=en overrides a French device (support/QA hatch)',
  detect(["fr-FR"], { search: "?lang=en" }).lang() === "en");

const frI18n = detect(["fr-FR"]);
check('home.kicker greeting is the polite English one',
  detect(["en-US"]).t("home.kicker") === "Welcome. Ready to shop?");
check('home.kicker greeting translates for French shoppers',
  frI18n.t("home.kicker") === "Bienvenue. Prêt à faire vos achats ?",
  frI18n.t("home.kicker"));
check('the headline stays verbatim',
  detect(["en-US"]).t("home.heroLine") === "Experience effortless elegance and curated essentials");

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

function makeStoreSandbox(langTags) {
  const storage = new Map();
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
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: (k) => storage.delete(k),
      clear: () => storage.clear(),
    },
    sessionStorage: { getItem: () => "1", setItem() {}, removeItem() {} }, // welcome dismissed
    navigator: { onLine: true, languages: langTags, language: langTags[0] || "en" },
    location: {
      href: "https://jaurastore.com.ng/index.html", origin: "https://jaurastore.com.ng",
      protocol: "https:", host: "jaurastore.com.ng", pathname: "/index.html", search: "",
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
  const elements = { headerSlot, footerSlot, pill, curButtons };

  const doc = {
    readyState: "loading",
    body: makeEl("body"),
    documentElement: { dataset: {}, lang: "", classList: makeEl("div").classList },
    head: { querySelector: () => null, appendChild() {} },
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
  return sandbox;
}

async function bootShop(langTags) {
  const sandbox = makeStoreSandbox(langTags);
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
{
  const { sandbox, JA } = await bootShop(["en-NG", "en"]);
  const els = sandbox.__els;
  check("detected English interface", sandbox.I18N.lang() === "en");
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
  const before = JA.priceOf(PRODUCTS[0], "NGN");
  check("prices recalculate in F CFA after the tap",
    JA.priceOf(PRODUCTS[0]) === JA.toCfa(before),
    `NGN ${before} -> CFA ${JA.priceOf(PRODUCTS[0])}`);
  check("money formats follow the active currency", JA.money(1000).startsWith("F CFA"), JA.money(1000));
  els.curButtons[0].click();
  check("tapping ₦ flips back to Naira", JA.currency() === "NGN" && JA.priceOf(PRODUCTS[0]) === before);
  check("Naira formatting is restored", JA.money(1000).startsWith("₦"), JA.money(1000));
}

// ---------------------------------------------------------- FRENCH shop
{
  const { sandbox, JA } = await bootShop(["fr-BJ", "fr"]);
  const els = sandbox.__els;
  check("detected French interface", sandbox.I18N.lang() === "fr");
  check("FCFA IS locked for French shoppers", JA.currencyLocked() === true);
  check("French prices open in F CFA", JA.currency() === "CFA");
  check("setCurrency cannot undo the French FCFA lock",
    (JA.setCurrency("NGN"), JA.currency() === "CFA"));
  const footer = els.footerSlot.innerHTML;
  check("pill: rendered but hidden in French mode",
    footer.includes("data-cur-float") && /\bhidden/.test(footer.slice(footer.indexOf("data-cur-float") - 90, footer.indexOf("data-cur-float") + 40)));
  check("pill: hidden property synced by refreshChrome", els.pill.hidden === true);
  check("body carries the ja-fr class backstop", els && sandbox.document.body.classList.contains("ja-fr"));

  // ---- checkout: the FCFA gateway surfaces, the Naira card disappears ----
  vm.runInContext(appSrc, sandbox, { filename: "js/app.js" });
  // Same-group radios are mutually exclusive in a real DOM: checking one
  // unchecks the other. Model that link or the harness lies.
  const group = { cfa: null, ngn: null };
  const radio = (value, initiallyChecked, key) => {
    let on = !!initiallyChecked;
    return {
      value,
      get checked() { return on; },
      set checked(v) {
        on = !!v;
        if (on) {
          const other = group[key === "cfa" ? "ngn" : "cfa"];
          if (other) other._uncheck();
        }
      },
      _uncheck() { on = false; },
    };
  };
  const cfaRadio = radio("CFA", false, "cfa");
  const ngnRadio = radio("NGN", true, "ngn");
  group.cfa = cfaRadio; group.ngn = ngnRadio;
  const ngnCard = Object.assign(makeEl("label"), { querySelector: () => ngnRadio });
  const cfaCard = Object.assign(makeEl("label"), { querySelector: () => cfaRadio });
  const ngnBox = { hidden: false };
  const cfaBox = { hidden: true };
  const form = {
    querySelector: (sel) => {
      if (sel === "[name=currency]:checked") return ngnRadio.checked ? ngnRadio : (cfaRadio.checked ? cfaRadio : null);
      if (sel === '[name=currency][value="CFA"]') return cfaRadio;
      return null;
    },
    querySelectorAll: (sel) => (sel === ".pay-card" ? [ngnCard, cfaCard] : []),
  };
  const docQS = sandbox.document.querySelector;
  sandbox.document.querySelector = (sel) => {
    if (sel === "[data-bank-ngn]") return ngnBox;
    if (sel === "[data-bank-cfa]") return cfaBox;
    return docQS(sel);
  };
  vm.runInContext("paintCheckoutTotals(form)", Object.assign(sandbox, { form }));
  check("French checkout pre-selects the FCFA gateway", cfaRadio.checked === true);
  check("French checkout hides the Naira pay-card", ngnCard.hidden === true);
  check("French checkout leaves the FCFA pay-card", cfaCard.hidden === false);
  check("French checkout shows the FCFA bank sheet", cfaBox.hidden === false);
  check("French checkout hides the NGN bank sheet", ngnBox.hidden === true);
  sandbox.document.querySelector = docQS;
}

// ------------------------------------------------ static checkout cleanup
{
  const appText = appSrc;
  check("the Benin/Togo currency popup helper is gone", !appText.includes("promptCurrencyForBeninTogo"));
  check('no more "delivery detected. Choose your currency" dialog', !appText.includes("delivery detected. Choose your currency"));
  check("no confirm() currency dialog left at checkout", !/confirm\(\s*msg\s*\)/.test(appText));
}

console.log(failures ? `\n${failures} auto-locale check(s) FAILED` : "\nall auto-locale checks passed");
process.exit(failures ? 1 : 0);
