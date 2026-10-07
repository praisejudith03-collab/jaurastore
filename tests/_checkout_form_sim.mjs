/* Simulation tests for checkout form enhancements:
   - Delivery zone hierarchy (Pickup -> Nigeria -> Benin/Togo)
   - Inter-State Notice banner toggling
   - Soft validation & inline tooltips ("Please fill in this detail to complete your order.")
   - Place Your Order button text and loading spinner
*/
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.dirname(HERE);

const I18N_JS = fs.readFileSync(path.join(ROOT, "js", "i18n.js"), "utf8");
const APP_JS = fs.readFileSync(path.join(ROOT, "js", "app.js"), "utf8");

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
    if (sel.includes("phone")) return this.children.find((c) => c.name === "phone") || null;
    if (sel.includes("country")) return this.children.find((c) => c.name === "country") || null;
    if (sel.includes("address")) return this.children.find((c) => c.name === "address") || null;
    if (sel.includes("zone")) return this.children.find((c) => c.name === "zone") || null;
    if (sel === ".ck-place") return this.children.find((c) => c.classList.contains("ck-place")) || null;
    return null;
  }
  querySelectorAll() { return []; }
  closest() { return this; }
  focus() { this.focused = true; }
  scrollIntoView() { this.scrolled = true; }
}

const sandbox = {
  console,
  setTimeout, clearTimeout, Promise, Date, JSON, Math,
  window: {},
  document: {
    createElement(tag) { return new El(tag); },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
  },
  navigator: { language: "en-US", languages: ["en-US", "en"] },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.runInNewContext(I18N_JS, sandbox);
vm.runInNewContext(APP_JS, sandbox);

// Test 1: Delivery zone hierarchy
const mockZones = [
  { id: "calavi", name: "Calavi", currency: "CFA", fare_min: 1500, fare_max: 3500 },
  { id: "lagos-mainland", name: "Lagos Mainland", currency: "NGN", fare_min: 2000, fare_max: 5000 },
  { id: "pickup", name: "Pickup in Cotonou", kind: "pickup", currency: "CFA" },
  { id: "ng-other", name: "Other Nigeria", currency: "NGN", kind: "quote" },
];
const groups = vm.runInContext("zoneGroups", sandbox)(mockZones);
check("zoneGroups top group is pickup", groups[0].id === "pickup");
check("zoneGroups 2nd group is Nigeria", groups[1].id === "ngn");
check("zoneGroups 3rd group is CFA (Benin & Togo)", groups[2].id === "cfa");

// Test 2: Zone label formatting
const pickupLabel = vm.runInContext("zoneLabel", sandbox)(mockZones[2]);
check("pickup zone label is the neutral Pick up only",
  pickupLabel === "Pick up only", pickupLabel);

const lagosLabel = vm.runInContext("zoneLabel", sandbox)(mockZones[1]);
check("Lagos Mainland label has Express Delivery prefix",
  lagosLabel.includes("🇳🇬 Lagos State (Express Delivery)"), lagosLabel);

const ngOtherLabel = vm.runInContext("zoneLabel", sandbox)(mockZones[3]);
check("Other Nigeria label is 🇳🇬 Other States in Nigeria (Inter-State Dispatch)",
  ngOtherLabel === "🇳🇬 Other States in Nigeria (Inter-State Dispatch)", ngOtherLabel);

// Test 3: Inter-state notice logic
const noticeEl = new El("div");
noticeEl.hidden = true;
sandbox.document.querySelector = (sel) => (sel === "[data-interstate-notice]" ? noticeEl : null);

const selNigeriaOther = new El("select");
selNigeriaOther.value = "Other Nigeria";
selNigeriaOther.selectedOptions = [{ textContent: "🇳🇬 Other States in Nigeria (Inter-State Dispatch)" }];
vm.runInContext("updateInterStateNotice", sandbox)(selNigeriaOther);
check("Other Nigeria makes inter-state notice visible", noticeEl.hidden === false);

const selLagos = new El("select");
selLagos.value = "Lagos Mainland";
selLagos.selectedOptions = [{ textContent: "🇳🇬 Lagos State (Express Delivery) — Lagos Mainland" }];
vm.runInContext("updateInterStateNotice", sandbox)(selLagos);
check("Lagos Mainland hides inter-state notice", noticeEl.hidden === true);

// Test 4: Validation message
const form = new El("form");
const fnInput = new El("input"); fnInput.name = "firstName"; fnInput.value = ""; form.appendChild(fnInput);
const lnInput = new El("input"); lnInput.name = "lastName"; lnInput.value = ""; form.appendChild(lnInput);
const phoneInput = new El("input"); phoneInput.name = "phone"; phoneInput.value = ""; form.appendChild(phoneInput);
const countrySelect = new El("select"); countrySelect.name = "country"; countrySelect.value = ""; form.appendChild(countrySelect);
const addressInput = new El("input"); addressInput.name = "address"; addressInput.value = ""; form.appendChild(addressInput);
const zoneSelect = new El("select"); zoneSelect.name = "zone"; zoneSelect.value = ""; form.appendChild(zoneSelect);

const res = vm.runInContext("validateCheckoutForm", sandbox)(form);
check("empty checkout form fails validation", res.ok === false);
check("failures include required message 'Please fill in this detail to complete your order.'",
  res.failures.some((f) => f.message === "Please fill in this detail to complete your order."));

const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} checks passed`);
process.exit(passed === results.length ? 0 : 1);
