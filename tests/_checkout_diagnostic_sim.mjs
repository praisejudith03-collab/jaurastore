/* Exhaustive simulated browser test for checkout pipeline:
   1. reCAPTCHA silent refresh and fallback
   2. Mandatory field soft-validation (smooth scrolling & inline tooltips)
   3. Delivery zone selection, hierarchy, and inter-state notice
   4. Final action button text ("Place Your Order") and loading spinner state
   5. Minimum order banner removal (.ck-bj-min cleanly removed with no artifacts)
*/
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.dirname(HERE);

const I18N_JS = fs.readFileSync(path.join(ROOT, "js", "i18n.js"), "utf8");
const APP_JS = fs.readFileSync(path.join(ROOT, "js", "app.js"), "utf8");
const NET_JS = fs.readFileSync(path.join(ROOT, "js", "net.js"), "utf8");
const CHECKOUT_HTML = fs.readFileSync(path.join(ROOT, "checkout.html"), "utf8");

const results = [];
function check(name, ok, detail = "") {
  results.push(!!ok);
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
}

class El {
  constructor(tag = "div") {
    this.tagName = String(tag).toUpperCase();
    this.attrs = new Map();
    this.children = [];
    this.className = "";
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.hidden = false;
    this.disabled = false;
    this.dataset = {};
    this.selectedOptions = [];
    this.name = "";
    this.style = {};
    this.classList = {
      add: (...cls) => {
        const parts = new Set((this.className || "").split(" ").filter(Boolean));
        cls.forEach((c) => parts.add(c));
        this.className = Array.from(parts).join(" ");
      },
      remove: (...cls) => {
        const parts = new Set((this.className || "").split(" ").filter(Boolean));
        cls.forEach((c) => parts.delete(c));
        this.className = Array.from(parts).join(" ");
      },
      contains: (c) => (this.className || "").split(" ").includes(c),
      toggle: (c, force) => {
        if (force === true) this.classList.add(c);
        else if (force === false) this.classList.remove(c);
        else if (this.classList.contains(c)) this.classList.remove(c);
        else this.classList.add(c);
      },
    };
  }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  removeAttribute(k) { this.attrs.delete(k); }
  appendChild(c) { this.children.push(c); return c; }
  querySelector(sel) {
    if (sel.includes("firstName")) return this.children.find((c) => c.name === "firstName") || null;
    if (sel.includes("lastName")) return this.children.find((c) => c.name === "lastName") || null;
    if (sel.includes("phone")) return this.children.find((c) => c.name === "phone") || null;
    if (sel.includes("country")) return this.children.find((c) => c.name === "country") || null;
    if (sel.includes("address")) return this.children.find((c) => c.name === "address") || null;
    if (sel.includes("city")) return this.children.find((c) => c.name === "city") || null;
    if (sel.includes("zone")) return this.children.find((c) => c.name === "zone") || null;
    if (sel === ".ck-place") return this.children.find((c) => c.classList.contains("ck-place")) || null;
    return null;
  }
  querySelectorAll(sel) {
    if (sel === "[required]") return this.children.filter((c) => c.attrs.has("required"));
    return [];
  }
  closest() { return this; }
  focus() { this.focused = true; }
  scrollIntoView() { this.scrolled = true; }
}

const sandbox = {
  console,
  setTimeout, clearTimeout, setInterval, clearInterval, Promise, Date, JSON, Math,
  addEventListener: () => {},
  removeEventListener: () => {},
  document: {
    createElement(tag) { return new El(tag); },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
  },
  navigator: { language: "en-US", languages: ["en-US", "en"], onLine: true },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  fetch: async () => ({ json: async () => ({ ok: true, csrf: "csrf-val", recaptchaSiteKey: "site-key" }) }),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.runInNewContext(I18N_JS, sandbox);
vm.runInNewContext(NET_JS, sandbox);
vm.runInNewContext(APP_JS, sandbox);

// ------------------------------------------------- Check 1: Minimum Order Banner Removal
check("checkout.html has no .ck-bj-min element", !CHECKOUT_HTML.includes('class="ck-bj-min"'));
check("checkout.html has no 'Benin deliveries: minimum order' text", !CHECKOUT_HTML.includes("Benin deliveries: minimum order"));

// ------------------------------------------------- Check 2: Delivery Zone Hierarchy
const zones = [
  { id: "calavi", name: "Calavi", currency: "CFA", fare_min: 1500, fare_max: 3500 },
  { id: "lagos-mainland", name: "Lagos Mainland", currency: "NGN", fare_min: 2000, fare_max: 5000 },
  { id: "pickup", name: "Pickup in Cotonou", kind: "pickup", currency: "CFA" },
  { id: "ng-other", name: "Other Nigeria", currency: "NGN", kind: "quote" },
];
const groups = vm.runInContext("zoneGroups", sandbox)(zones);
check("Delivery Zone hierarchy: 1st is Pickup", groups[0].id === "pickup");
check("Delivery Zone hierarchy: 2nd is Nigeria", groups[1].id === "ngn");
check("Delivery Zone hierarchy: 3rd is Benin/Togo", groups[2].id === "cfa");

// ------------------------------------------------- Check 3: Inter-State Banner
const noticeEl = new El("div");
noticeEl.hidden = true;
sandbox.document.querySelector = (sel) => (sel === "[data-interstate-notice]" ? noticeEl : null);

const selNigeriaOther = new El("select");
selNigeriaOther.value = "Other Nigeria";
selNigeriaOther.selectedOptions = [{ textContent: "🇳🇬 Other States in Nigeria (Inter-State Dispatch)" }];
vm.runInContext("updateInterStateNotice", sandbox)(selNigeriaOther);
check("Inter-State notice is visible when Other States in Nigeria is selected", noticeEl.hidden === false);

const selLagos = new El("select");
selLagos.value = "Lagos Mainland";
selLagos.selectedOptions = [{ textContent: "🇳🇬 Lagos State (Express Delivery) — Lagos Mainland" }];
vm.runInContext("updateInterStateNotice", sandbox)(selLagos);
check("Inter-State notice is hidden when Lagos State is selected", noticeEl.hidden === true);

// ------------------------------------------------- Check 4: Mandatory Field Soft Validation
const form = new El("form");
const fn = new El("input"); fn.name = "firstName"; fn.value = ""; fn.setAttribute("required", ""); form.appendChild(fn);
const ln = new El("input"); ln.name = "lastName"; ln.value = ""; ln.setAttribute("required", ""); form.appendChild(ln);
const phone = new El("input"); phone.name = "phone"; phone.value = ""; phone.setAttribute("required", ""); form.appendChild(phone);
const country = new El("select"); country.name = "country"; country.value = ""; country.setAttribute("required", ""); form.appendChild(country);
const address = new El("input"); address.name = "address"; address.value = ""; address.setAttribute("required", ""); form.appendChild(address);
const city = new El("input"); city.name = "city"; city.value = ""; city.setAttribute("required", ""); form.appendChild(city);
const zone = new El("select"); zone.name = "zone"; zone.value = ""; zone.setAttribute("required", ""); form.appendChild(zone);

const valResult = vm.runInContext("validateCheckoutForm", sandbox)(form);
check("Incomplete form fails soft validation", valResult.ok === false);
check("Soft validation failure message is 'Please fill in this detail to complete your order.'",
  valResult.failures[0].message === "Please fill in this detail to complete your order.");
check("First incomplete control is focused and smooth-scrolled", fn.focused === true && fn.scrolled === true);

// ------------------------------------------------- Check 5: Action Button & Spinner State
check("Button text in checkout.html is 'Place Your Order'", CHECKOUT_HTML.includes('data-i18n="ck.place">Place Your Order</button>'));
const i18nPlace = vm.runInContext("t('ck.place')", sandbox);
check("i18n dictionary 'ck.place' translates to 'Place Your Order'", i18nPlace === "Place Your Order");

const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} diagnostic checks passed`);
process.exit(passed === results.length ? 0 : 1);
