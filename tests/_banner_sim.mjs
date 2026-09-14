// Moving-banner browser simulation (jsdom).
//
// Loads the REAL storefront page and its real scripts against a live server,
// then checks the thing the owner actually reported:
//
//   1. a banner saved in the Admin panel reaches the live header (no reload,
//      no cache in the way);
//   2. tapping FR swaps the header line to the French text, and EN swaps it
//      back;
//   3. an unwritten French line falls back to English rather than a blank bar;
//   4. the retired "Delivery window starts / ends" text is gone - the default
//      banner carries no dates, and no date input exists in the Admin panel;
//   5. nothing logs a console error, at desktop AND at a phone viewport.
//
// The sandbox cannot download a real Chromium (the CDN is network-blocked),
// so this uses jsdom, which executes the storefront's own JS. Run with the
// app up on :8080:
//
//   ADMIN_PW='...' node tests/_banner_sim.mjs
let JSDOM, VirtualConsole, requestInterceptor;
try {
  const m = await import("jsdom");
  JSDOM = m.JSDOM; VirtualConsole = m.VirtualConsole; requestInterceptor = m.requestInterceptor;
} catch (e) {
  console.error("jsdom is required but not installed. Run: npm install jsdom@30  (repo root)");
  process.exit(3);
}

const BASE = process.env.BASE || "http://127.0.0.1:8080";
const ADMIN_PW = process.env.ADMIN_PW || "";
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "jaurastore@gmail.com";
if (!ADMIN_PW) {
  console.error("ADMIN_PW is not set. Run as:  ADMIN_PW='<admin password>' node tests/_banner_sim.mjs");
  process.exit(3);
}

const results = [];
const errors = [];
function check(name, ok, detail = "") {
  results.push([!!ok, name]);
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
}

const vc = new VirtualConsole();
// jsdom has no media stack, so the hero video's play()/load() raise
// "Not implemented" - a property of the harness, not of the page.
const NOISE = /Not implemented: HTMLMediaElement|Could not parse CSS|Not implemented: navigation/i;
const note = (prefix, msg) => { if (!NOISE.test(msg)) errors.push(prefix + msg); };
vc.on("error", (...a) => note("console.error: ", a.map(String).join(" ")));
vc.on("jsdomError", (e) => note("jsdomError: ", String((e && e.message) || e)));

// ---- cookie + fetch shim ----------------------------------------------------
const cookies = new Map();
const jarHeader = () => [...cookies.entries()].map(([k, v]) => `${k}=${v}`).join("; ");
const realFetch = globalThis.fetch;
// The storefront fetches RELATIVE URLs ("api/site"), which a real browser
// resolves against the page. Node's fetch cannot, so resolve them here or
// every page-context call throws and nothing ever applies the site row.
function absolute(url, base) {
  try { return new URL(String(url), base || BASE).toString(); }
  catch (e) { return String(url); }
}
async function doFetch(url, opts = {}, base = BASE) {
  url = absolute(url, base);
  const headers = Object.assign({}, opts.headers || {});
  const ck = jarHeader();
  if (ck && !headers.Cookie) headers.Cookie = ck;
  const resp = await realFetch(url, Object.assign({}, opts, { headers }));
  const scs = typeof resp.headers.getSetCookie === "function"
    ? resp.headers.getSetCookie()
    : [resp.headers.get("set-cookie")].filter(Boolean);
  for (const line of scs) {
    const m = line.match(/^([^=;]+)=([^;]*)/);
    if (m) cookies.set(m[1].trim(), m[2].trim());
  }
  return resp;
}

function installShims(window, pageUrl) {
  window.fetch = (u, o) => doFetch(u, o, pageUrl);
  window.Request = globalThis.Request;
  window.Headers = globalThis.Headers;
  if (!window.requestAnimationFrame) window.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 16);
  if (!window.matchMedia) window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
  if (!window.scrollTo) window.scrollTo = () => {};
  for (const name of ["IntersectionObserver", "ResizeObserver"]) {
    if (!window[name]) {
      window[name] = class {
        constructor(cb) { this.cb = cb; }
        observe(t) { if (t && this.cb) this.cb([{ isIntersecting: true, target: t }]); return this; }
        unobserve() {} disconnect() {} takeRecords() { return []; }
      };
    }
  }
}

const localInterceptor = () => requestInterceptor((request) => {
  const u = String(request.url);
  if (u.startsWith(BASE) || u.startsWith("http://127.0.0.1") || u.startsWith("http://localhost")) return undefined;
  return new Response("", { status: 200, headers: { "Content-Type": "application/javascript" } });
});

async function loadPage(path, { width = 1280, height = 900 } = {}) {
  const html = await (await realFetch(BASE + path)).text();
  const dom = new JSDOM(html, {
    url: BASE + path,
    runScripts: "dangerously",
    resources: { interceptors: [localInterceptor()] },
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(window) {
      installShims(window, BASE + path);
      Object.defineProperty(window, "innerWidth", { value: width, writable: true });
      Object.defineProperty(window, "innerHeight", { value: height, writable: true });
    },
  });
  const { window } = dom;
  await new Promise((resolve) => {
    if (window.document.readyState === "complete") resolve();
    else window.addEventListener("load", resolve, { once: true });
    setTimeout(resolve, 6000);
  });
  await new Promise((r) => setTimeout(r, 900));   // let api/site land + repaint
  return window;
}

const bannerText = (w) =>
  (w.document.querySelector(".conv-track")?.textContent || "").replace(/\s+/g, " ").trim();

// ---- admin API --------------------------------------------------------------
let csrf = "";
async function login() {
  const r = await doFetch(BASE + "/api/admin/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: ADMIN_EMAIL, password: ADMIN_PW }),
  });
  const d = await r.json();
  csrf = d.csrf || "";
  return r.status === 200;
}
async function saveSite(payload) {
  const r = await doFetch(BASE + "/api/admin/site", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify(payload),
  });
  return { status: r.status, body: await r.json() };
}

async function main() {
  check("admin signs in", await login());

  // ---------------------------------------------------- 1. save -> storefront
  const EN = "Free delivery on every order this week";
  const FR = "Livraison offerte sur toute commande cette semaine";
  let res = await saveSite({ convBanner: EN, convBannerFr: FR, convBold: "ends Sunday" });
  check("the Admin banner save is accepted", res.status === 200 && res.body.ok === true,
        JSON.stringify(res.body).slice(0, 120));
  check("the save answers the stored row", res.body.site?.convBanner === EN,
        String(res.body.site?.convBanner));

  const w = await loadPage("/index.html");
  check("the saved banner renders in the live header", bannerText(w).includes(EN),
        bannerText(w).slice(0, 90));
  check("the bold highlight rides along", bannerText(w).includes("ends Sunday"));
  check("the English shopper does NOT see the French line", !bannerText(w).includes(FR));

  // ------------------------------------------------------ 2. the FR/EN toggle
  const frBtn = w.document.querySelector('[data-lang="fr"]');
  check("the header carries an FR toggle", !!frBtn);
  frBtn.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 600));
  check("switching to FR renders the French banner", bannerText(w).includes(FR),
        bannerText(w).slice(0, 90));
  check("the English line is replaced, not appended", !bannerText(w).includes(EN));

  const enBtn = w.document.querySelector('[data-lang="en"]');
  enBtn.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 600));
  check("switching back to EN restores the English banner",
        bannerText(w).includes(EN) && !bannerText(w).includes(FR),
        bannerText(w).slice(0, 90));

  // ------------------------------------- 3. FR falls back to EN when unwritten
  res = await saveSite({ convBanner: EN, _clear: ["convBannerFr"] });
  check("clearing the French line is accepted", res.status === 200 && res.body.ok === true);
  const w2 = await loadPage("/index.html?lang=fr");
  check("a French shopper falls back to the English line (never a blank bar)",
        bannerText(w2).includes(EN), bannerText(w2).slice(0, 90));

  // ----------------------------------------- 4. no delivery-window text left
  res = await saveSite({ _clear: ["convBanner", "convBannerFr", "convBold"] });
  check("clearing the banner entirely is accepted", res.status === 200 && res.body.ok === true);
  const w3 = await loadPage("/index.html");
  const fallback = bannerText(w3);
  check("the default banner still says something", fallback.length > 10, fallback.slice(0, 90));
  check("the default banner carries NO delivery-window dates",
        !/\d{4}-\d{2}-\d{2}/.test(fallback) && !/\bbetween\b/i.test(fallback),
        fallback.slice(0, 90));

  const w4 = await loadPage("/index.html?lang=fr");
  check("the default banner is translated for a French shopper",
        bannerText(w4).length > 10 && !/\bbetween\b/i.test(bannerText(w4)),
        bannerText(w4).slice(0, 90));

  // the retired date pickers are gone from the Admin panel markup
  const adminJs = await (await realFetch(BASE + "/js/admin.js")).text();
  check("the Admin panel has no delivery-window date inputs",
        !/name="bannerFrom"/.test(adminJs) && !/name="bannerTo"/.test(adminJs));
  check("the Admin panel still has the three banner inputs",
        /name="convBanner"/.test(adminJs) && /name="convBannerFr"/.test(adminJs)
        && /name="convBold"/.test(adminJs));
  check("the Delivery Fee / Shipping Note field is retained for checkout",
        /shipping_note|shippingNote/.test(adminJs));

  // ---------------------------------------------------- 5. phone viewport
  await saveSite({ convBanner: EN, convBannerFr: FR });
  const phone = await loadPage("/index.html", { width: 390, height: 844 });
  check("the banner renders on a 390px phone viewport", bannerText(phone).includes(EN),
        bannerText(phone).slice(0, 90));
  const bar = phone.document.querySelector(".conv-bar");
  check("the moving bar is present and non-empty on mobile",
        !!bar && bar.textContent.trim().length > 0);
  const phoneFr = phone.document.querySelector('[data-lang="fr"]');
  phoneFr.dispatchEvent(new phone.MouseEvent("click", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 600));
  check("the FR toggle works on mobile too", bannerText(phone).includes(FR),
        bannerText(phone).slice(0, 90));

  // the admin page itself must stay clean (it hides the bar via CSS)
  await loadPage("/admin.html");

  check("no console errors on any page or viewport", errors.length === 0,
        errors.slice(0, 6).join(" | "));

  const failed = results.filter(([ok]) => !ok);
  console.log(`\n=== ${results.length - failed.length}/${results.length} checks passed ===`);
  if (failed.length) {
    console.log("FAILED:\n" + failed.map(([, n]) => "  - " + n).join("\n"));
    process.exit(1);
  }
  // The loaded jsdom windows keep their timers (the card-play watchdog, the
  // presence ping) alive, so exit explicitly rather than hanging on a pass.
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
