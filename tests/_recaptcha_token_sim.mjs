/* Token-timing simulation for the invisible reCAPTCHA widget.

   Drives the REAL js/net.js - the same file the browser loads - inside a DOM
   stub, against a fake Google whose minting latency we control.

   WHY: grecaptcha.execute() returns a PROMISE. The old code passed it a
   callback (not part of the v2 API, so it never fired) and leaned on a single
   300 ms getResponse() probe. Minting is a round trip to Google - 0.5-2 s on a
   phone - so most real orders were sent with no X-Recaptcha-Token at all and
   the server-side gate silently did nothing.

   Run:  node tests/_recaptcha_token_sim.mjs        (node only, no browser)
   Point it at another build with:  NET_JS=/tmp/net.js node tests/_recaptcha_token_sim.mjs
*/
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.dirname(HERE);
const NET_JS = process.env.NET_JS || path.join(ROOT, "js", "net.js");
const SRC = fs.readFileSync(NET_JS, "utf8");

const results = [];
function check(name, ok, detail = "") {
  results.push(!!ok);
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
}

// ------------------------------------------------------------------ DOM stub
class El {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this.attrs = new Map();
    this.children = [];
    this.className = "";
    this.type = "";
  }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  hasAttribute(k) { return this.attrs.has(k); }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener() {}
  remove() {}
  get innerHTML() { return ""; }
  set innerHTML(_v) {}
}

function makeSandbox({ siteKey, latency, mode, anchors, notes = 1 }) {
  const state = { scripts: [], renders: 0, executes: 0, token: null, listeners: {} };
  const anchorEls = [];
  for (let i = 0; i < anchors; i += 1) anchorEls.push(new El("div"));
  const noteEls = [];
  for (let i = 0; i < notes; i += 1) noteEls.push(new El("p"));

  const google = {
    ready(fn) { if (typeof fn === "function") fn(); },
    render(el) {
      state.renders += 1;
      const id = state.renders - 1;
      el.setAttribute("data-recaptcha-id", String(id));
      return id;
    },
    execute() {
      state.executes += 1;
      const tok = "tok-" + state.executes;
      if (mode === "throw") throw new Error("grecaptcha not ready");
      if (mode === "legacy") { state.token = tok; return undefined; }
      if (mode === "silent") return new Promise(() => {});   // never settles
      // real invisible v2: a promise that carries the token after the round trip
      return new Promise((resolve) => setTimeout(() => { state.token = tok; resolve(tok); }, latency));
    },
    getResponse() { return state.token || ""; },
    reset() { state.token = null; },
  };

  const doc = {
    readyState: "complete",
    hidden: false,
    head: new El("head"),
    body: new El("body"),
    createElement(tag) {
      const el = new El(tag);
      if (tag === "script") {
        Object.defineProperty(el, "src", {
          set(v) { state.scripts.push(v); setTimeout(() => el.onload && el.onload(), 0); },
          get() { return ""; },
        });
      }
      return el;
    },
    querySelectorAll(sel) {
      if (sel === "[data-recaptcha-widget]") return anchorEls;
      if (sel === "[data-recaptcha-note]") return noteEls;
      if (sel === "[data-recaptcha-id]") return anchorEls.filter((e) => e.hasAttribute("data-recaptcha-id"));
      return [];
    },
    querySelector() { return null; },
    getElementById() { return null; },
    addEventListener(name) { state.listeners[name] = (state.listeners[name] || 0) + 1; },
  };

  const sandbox = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval, Promise, Date, JSON, Math,
    document: doc,
    navigator: { onLine: true },
    sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    grecaptcha: google,
    fetch: async () => ({ json: async () => ({ ok: true, csrf: "csrf-token", recaptchaSiteKey: siteKey }) }),
    isSecureContext: false,
    addEventListener(name) { state.listeners[name] = (state.listeners[name] || 0) + 1; },
  };
  sandbox.window = sandbox;              // in a browser, window IS the global
  sandbox.globalThis = sandbox;
  vm.runInNewContext(SRC, sandbox, { filename: NET_JS });
  return { JA_NET: sandbox.JA_NET, google, state };
}

// ------------------------------------------------------------------- the run
// 1-3: a slow phone. Minting takes 1.5 s - far past the old 300 ms probe.
{
  const { JA_NET, state } = makeSandbox({ siteKey: "site-key", latency: 1500, mode: "promise", anchors: 1 });
  const t0 = Date.now();
  const tok = await JA_NET.recaptcha("checkout");
  const ms = Date.now() - t0;
  check("slow Google (1.5 s): a token still reaches the request", !!tok, `"${tok}"`);
  check("slow Google: the token is the one Google minted", tok === "tok-1", `${tok} after ${ms} ms`);
  check("slow Google: waiting is bounded, not open-ended", ms < 2500, `${ms} ms`);
}

// 4: a fast network still works, and minting happens exactly once per order.
{
  const { JA_NET, state } = makeSandbox({ siteKey: "site-key", latency: 20, mode: "promise", anchors: 1 });
  const tok = await JA_NET.recaptcha("checkout");
  check("fast Google (20 ms): token minted", tok === "tok-1", `"${tok}"`);
  check("execute() is called once per order", state.executes === 1, `${state.executes} call(s)`);
}

// 5: two orders get two different tokens - a v2 token is single-use.
{
  const { JA_NET } = makeSandbox({ siteKey: "site-key", latency: 900, mode: "promise", anchors: 1 });
  const first = await JA_NET.recaptcha("checkout");
  await JA_NET.resetRecaptcha();
  const second = await JA_NET.recaptcha("checkout");
  check("a second order mints a fresh token", !!first && !!second && first !== second, `${first} / ${second}`);
}

// 6-7: builds that give nothing back from execute() must still work.
{
  const { JA_NET } = makeSandbox({ siteKey: "site-key", latency: 0, mode: "legacy", anchors: 1 });
  const tok = await JA_NET.recaptcha("checkout");
  check("legacy execute() (no promise): polling still collects the token", tok === "tok-1", `"${tok}"`);
}
{
  const { JA_NET } = makeSandbox({ siteKey: "site-key", latency: 0, mode: "throw", anchors: 1 });
  let state = "unsettled";
  JA_NET.recaptcha("checkout").then((t) => { state = "resolved:" + t; },
                                    () => { state = "rejected"; });
  await new Promise((r) => setTimeout(r, 2600));
  check("execute() throwing: settles instead of rejecting the order",
        state !== "unsettled" && state !== "rejected", state);
}

// 9: Google that never answers must not hold the order hostage.
{
  const { JA_NET } = makeSandbox({ siteKey: "site-key", latency: 0, mode: "silent", anchors: 1 });
  const t0 = Date.now();
  const tok = await JA_NET.recaptcha("checkout");
  const ms = Date.now() - t0;
  check("silent Google: resolves empty inside the ~1.9 s cap",
        tok === "" && ms >= 1500 && ms < 2600, `"${tok}" after ${ms} ms`);
}
// 10-11: with no key, or no anchor on the page, nothing is loaded or called.
{
  const { JA_NET, state } = makeSandbox({ siteKey: "", latency: 20, mode: "promise", anchors: 1 });
  const tok = await JA_NET.recaptcha("checkout");
  check("no site key: empty token, Google never loaded", tok === "" && state.scripts.length === 0,
        `"${tok}", ${state.scripts.length} script(s)`);
}
{
  const { JA_NET, state } = makeSandbox({ siteKey: "site-key", latency: 20, mode: "promise", anchors: 0 });
  const tok = await JA_NET.recaptcha("checkout");
  check("no widget anchor: empty token, execute() never called", tok === "" && state.executes === 0,
        `"${tok}", ${state.executes} call(s)`);
}

const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} checks passed`);
process.exit(passed === results.length ? 0 : 1);
