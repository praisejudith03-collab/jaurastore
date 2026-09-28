// Social-media link simulation (Node VM, no browser dependency).
//
// Boots the REAL js/store.js in a stubbed browser and exercises the owner's
// 2026-09-28 request end to end on the client side:
//
//   * the four admin-editable links (WhatsApp, Instagram, TikTok, Facebook)
//     reach the storefront footer from the live site row;
//   * the logo is chosen from the LINK, not from the box it was typed into
//     (a facebook.com address in the Instagram field renders the Facebook
//     logo) - the "automatic icon rendering" half of the request;
//   * handles, bare domains and phone numbers are normalised into real URLs;
//   * an empty box keeps the shipped default, and "OFF" removes the icon;
//   * javascript:/data: addresses are refused outright.
//
// Run with:  node tests/_social_links_sim.mjs
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

function makeEl(name) {
  return {
    nodeName: name,
    dataset: {},
    style: {},
    hidden: false,
    innerHTML: "",
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      toggle(c, on) { if (on === undefined ? !this._set.has(c) : on) this._set.add(c); else this._set.delete(c); },
      contains(c) { return this._set.has(c); },
    },
    setAttribute(k, v) { this[k] = String(v); },
    getAttribute(k) { return this[k] || ""; },
    removeAttribute(k) { delete this[k]; },
    appendChild() {},
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
}

const storage = new Map();
const listeners = new Map();
const socialBox = makeEl("div");

const sandbox = {
  console,
  setTimeout, clearTimeout, setInterval, clearInterval, AbortSignal,
  Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map, Promise,
  RegExp, encodeURIComponent, decodeURIComponent, URL, URLSearchParams,
  CustomEvent: class CustomEvent {
    constructor(type, init) { this.type = type; this.detail = (init || {}).detail; }
  },
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
    clear: () => storage.clear(),
  },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { onLine: true, language: "en" },
  location: { href: "https://jaurastore.com.ng/index.html", origin: "https://jaurastore.com.ng", protocol: "https:", host: "jaurastore.com.ng", pathname: "/index.html", search: "" },
  history: { replaceState() {} },
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, site: {} }) }),
};

sandbox.document = {
  readyState: "loading",
  body: { dataset: { page: "home" }, classList: { add() {}, remove() {}, toggle() {} } },
  documentElement: { dataset: {}, classList: { add() {}, remove() {}, toggle() {} } },
  head: { querySelector: () => null, appendChild() {} },
  addEventListener: (type, fn) => {
    if (!listeners.has(type)) listeners.set(type, []);
    listeners.get(type).push(fn);
  },
  dispatchEvent: (ev) => {
    (listeners.get(ev.type) || []).forEach((fn) => { try { fn(ev); } catch (e) { console.error(e); } });
    return true;
  },
  querySelector: () => null,
  querySelectorAll: (sel) => (String(sel) === "[data-social-links]" ? [socialBox] : []),
  getElementById: () => null,
  createElement: () => makeEl("el"),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.I18N = { t: (k) => k, apply() {}, lang: () => "en" };
sandbox.fallbackImg = () => {};

vm.createContext(sandbox);
vm.runInContext(storeSrc, sandbox, { filename: "js/store.js" });
const JA = vm.runInContext("JA", sandbox);

const byId = (links) => Object.fromEntries(links.map((l) => [l.id, l.href]));

// ---------------------------------------------------------------- defaults
let links = JA.socialLinks();
let map = byId(links);
check("an untouched shop still shows its TikTok account",
  /tiktok\.com\/@j_aura_store/.test(map.tiktok || ""), map.tiktok || "(none)");
check("an untouched shop still shows a working WhatsApp chat link",
  /^https:\/\/wa\.me\/\d{8,}/.test(map.whatsapp || ""), map.whatsapp || "(none)");
check("no Instagram / Facebook icon before the owner adds one",
  !map.instagram && !map.facebook, JSON.stringify(map));

// ------------------------------------------------- the owner saves 4 links
JA.applySiteConfig({
  social_whatsapp_url: "https://wa.me/2349161670236",
  social_instagram_url: "https://www.instagram.com/j_aura_store",
  social_tiktok_url: "https://www.tiktok.com/@j_aura_store",
  social_facebook_url: "https://www.facebook.com/jaurastore",
});
links = JA.socialLinks();
map = byId(links);
check("all four platforms render once the owner saves them",
  links.length === 4 && map.whatsapp && map.instagram && map.tiktok && map.facebook,
  JSON.stringify(map));
check("Facebook is a first-class platform (its own logo path ships)",
  /M13\.5 21v-8h2\.69/.test(JA.SOCIAL_ICONS.facebook || ""));
const html = JA.socialLinksHTML();
check("the footer row carries one anchor per platform with its logo",
  ["whatsapp", "instagram", "tiktok", "facebook"].every((id) =>
    html.includes(`data-social="${id}"`) && html.includes(`social-btn--${id}`)),
  html.slice(0, 200));
check("the Facebook logo is rendered next to the Facebook link",
  html.split('data-social="facebook"')[1].includes("M13.5 21v-8h2.69"));
check("every social link opens safely in a new tab",
  (html.match(/rel="noopener"/g) || []).length === 4
  && (html.match(/target="_blank"/g) || []).length === 4);

// ------------------------------------------- the logo follows the LINK
JA.applySiteConfig({
  social_whatsapp_url: "",
  social_instagram_url: "https://www.facebook.com/jaurastore",   // wrong box
  social_tiktok_url: "https://www.instagram.com/j_aura_store",   // wrong box
  social_facebook_url: "",
});
links = JA.socialLinks();
check("a Facebook address typed in the Instagram box shows the Facebook logo",
  links.some((l) => l.field === "instagram" && l.id === "facebook"),
  JSON.stringify(links.map((l) => [l.field, l.id])));
check("an Instagram address typed in the TikTok box shows the Instagram logo",
  links.some((l) => l.field === "tiktok" && l.id === "instagram"),
  JSON.stringify(links.map((l) => [l.field, l.id])));
check("socialNetwork() reads the platform straight off the address",
  JA.socialNetwork("https://fb.me/jaura") === "facebook"
  && JA.socialNetwork("https://vm.tiktok.com/ZS99/") === "tiktok"
  && JA.socialNetwork("https://instagr.am/jaura") === "instagram"
  && JA.socialNetwork("https://chat.whatsapp.com/ABC") === "whatsapp");

// --------------------------------------------- what an owner actually types
check("a bare @handle becomes a real Instagram address",
  JA.normalizeSocialUrl("@j_aura_store", "instagram") === "https://www.instagram.com/j_aura_store",
  JA.normalizeSocialUrl("@j_aura_store", "instagram"));
check("a bare @handle becomes a real TikTok address",
  JA.normalizeSocialUrl("j_aura_store", "tiktok") === "https://www.tiktok.com/@j_aura_store",
  JA.normalizeSocialUrl("j_aura_store", "tiktok"));
check("a bare domain gets https://",
  JA.normalizeSocialUrl("facebook.com/jaurastore", "facebook") === "https://facebook.com/jaurastore");
check("a phone number becomes a wa.me chat link",
  JA.normalizeSocialUrl("+234 916 167 0236", "whatsapp") === "https://wa.me/2349161670236",
  JA.normalizeSocialUrl("+234 916 167 0236", "whatsapp"));
check("dangerous schemes are refused",
  JA.normalizeSocialUrl("javascript:alert(1)", "facebook") === ""
  && JA.normalizeSocialUrl("data:text/html,x", "instagram") === "");

// ------------------------------------------------------------ OFF removes it
JA.applySiteConfig({
  social_whatsapp_url: "OFF",
  social_instagram_url: "",
  social_tiktok_url: "off",
  social_facebook_url: "https://www.facebook.com/jaurastore",
});
links = JA.socialLinks();
map = byId(links);
check("typing OFF removes that icon from the storefront completely",
  !map.whatsapp && !map.tiktok && !!map.facebook, JSON.stringify(map));

// ------------------------------------------- the live row repaints the row
JA.applySiteConfig({
  social_whatsapp_url: "https://wa.me/22968953110",
  social_instagram_url: "https://www.instagram.com/j_aura_store",
  social_tiktok_url: "https://www.tiktok.com/@j_aura_store",
  social_facebook_url: "https://www.facebook.com/jaurastore",
});
check("applySiteConfig repaints every [data-social-links] row in place",
  socialBox.innerHTML.includes('data-social="facebook"')
  && socialBox.innerHTML.includes('data-social="instagram"')
  && socialBox.innerHTML.includes("wa.me/22968953110"),
  socialBox.innerHTML.slice(0, 160));

console.log(failures ? `\n${failures} social link check(s) FAILED` : "\nall social link checks passed");
process.exit(failures ? 1 : 0);
