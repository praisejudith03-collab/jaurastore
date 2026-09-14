// Click-driven Admin Moving Banner browser simulation (jsdom).
//
// Drives the REAL Admin portal UI in a browser context:
//   1. Signs in via the real login form.
//   2. Opens Settings.
//   3. Types into the real banner text inputs (English, French, Bold).
//   4. CLICKS THE REAL "Save banner" BUTTON.
//   5. Verifies that the client-side helper siteFieldPatch() resolves cleanly
//      without throwing ReferenceError: Can't find variable: siteFieldPatch.
//   6. Verifies that the save persists and displays on the live storefront in
//      both English and French.
//   7. Verifies clearing French falls back to English, and clearing all
//      restores the default banner.
//
// Run with the app up on :8080:
//   ADMIN_PW='...' node tests/_admin_banner_click_sim.mjs

let JSDOM, VirtualConsole, requestInterceptor;
try {
  const m = await import("jsdom");
  JSDOM = m.JSDOM; VirtualConsole = m.VirtualConsole; requestInterceptor = m.requestInterceptor;
} catch (e) {
  console.error("jsdom is required but not installed. Run: npm install --no-save jsdom@30");
  process.exit(3);
}

const BASE = process.env.BASE || "http://127.0.0.1:8080";
const ADMIN_PW = process.env.ADMIN_PW || "";
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "jaurastore@gmail.com";
if (!ADMIN_PW) {
  console.error("ADMIN_PW is not set. Run as: ADMIN_PW='<admin password>' node tests/_admin_banner_click_sim.mjs");
  process.exit(3);
}

const results = [];
const errors = [];
function check(name, ok, detail = "") {
  results.push([!!ok, name]);
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
}

const vc = new VirtualConsole();
const NOISE = /Not implemented: HTMLMediaElement|Could not parse CSS|Not implemented: navigation/i;
const note = (prefix, msg) => { if (!NOISE.test(msg)) errors.push(prefix + msg); };
vc.on("error", (...a) => note("console.error: ", a.map(String).join(" ")));
vc.on("jsdomError", (e) => note("jsdomError: ", String((e && e.message) || e)));

// ---- cookie + fetch shim ----------------------------------------------------
const cookies = new Map();
const jarHeader = () => [...cookies.entries()].map(([k, v]) => `${k}=${v}`).join("; ");
const realFetch = globalThis.fetch;

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
  await new Promise((r) => setTimeout(r, 600));
  return window;
}

const bannerText = (w) =>
  (w.document.querySelector(".conv-track")?.textContent || "").replace(/\s+/g, " ").trim();

async function main() {
  // 1. Load admin portal and sign in through the UI
  const adminWin = await loadPage("/admin.html");
  const loginForm = adminWin.document.querySelector("#login-form");
  const pwInput = adminWin.document.querySelector('input[name="password"]');
  const emailInput = adminWin.document.querySelector('input[name="email"]');
  if (emailInput) emailInput.value = ADMIN_EMAIL;
  if (pwInput) pwInput.value = ADMIN_PW;

  const loginBtn = adminWin.document.querySelector("#login-btn");
  if (loginBtn) {
    loginBtn.click();
  } else if (loginForm) {
    loginForm.dispatchEvent(new adminWin.Event("submit", { bubbles: true, cancelable: true }));
  }
  await new Promise((r) => setTimeout(r, 1000));

  const deskHeader = adminWin.document.querySelector(".adx-head h1");
  check("admin signs into portal via UI", !!deskHeader);

  // 2. Open Settings tab
  const settingsBtn = adminWin.document.querySelector('[data-tab="settings"]');
  check("admin opens Settings panel", !!settingsBtn);
  if (settingsBtn) {
    settingsBtn.click();
    await new Promise((r) => setTimeout(r, 800));
  }

  // 3. Banner form rendered with inputs
  const bannerForm = adminWin.document.querySelector("#banner-form");
  const convInput = adminWin.document.querySelector("#conv-banner");
  const convFrInput = adminWin.document.querySelector("#conv-banner-fr");
  const convBoldInput = adminWin.document.querySelector("#conv-bold");
  check("banner form and inputs are rendered", !!bannerForm && !!convInput && !!convFrInput && !!convBoldInput);

  // 4. Type English banner text
  const EN = "Grand opening sale: up to 40% off";
  convInput.value = EN;
  convInput.dispatchEvent(new adminWin.Event("input", { bubbles: true }));
  check("types English banner text", convInput.value === EN);

  // 5. Type French banner text
  const FR = "Grande ouverture : jusqu'à -40%";
  convFrInput.value = FR;
  convFrInput.dispatchEvent(new adminWin.Event("input", { bubbles: true }));
  check("types French banner text", convFrInput.value === FR);

  // 6. Type bold highlight
  const BOLD = "shop now";
  convBoldInput.value = BOLD;
  convBoldInput.dispatchEvent(new adminWin.Event("input", { bubbles: true }));
  check("types bold highlight text", convBoldInput.value === BOLD);

  // 7. Click the real "Save banner" submit button
  const saveBtn = bannerForm ? bannerForm.querySelector("button") : null;
  check("Save banner button is present", !!saveBtn);
  if (saveBtn) {
    saveBtn.click();
  } else if (bannerForm) {
    bannerForm.dispatchEvent(new adminWin.Event("submit", { bubbles: true, cancelable: true }));
  }
  await new Promise((r) => setTimeout(r, 1200));

  // 8. Verify no error message displayed (siteFieldPatch resolved cleanly)
  const errBox = adminWin.document.querySelector("#banner-form-error");
  const errMsg = (errBox && !errBox.hidden) ? errBox.textContent.trim() : "";
  check("clicking Save banner succeeds without ReferenceError",
        !errMsg.includes("siteFieldPatch") && !errMsg.includes("Can't find variable") && errMsg === "",
        errMsg ? `Error showed: ${errMsg}` : "No error shown");

  // 9. Verify server confirmed the stored row
  const siteResp = await doFetch(BASE + "/api/site");
  const siteData = siteResp.ok ? await siteResp.json() : {};
  check("the save is persisted and confirmed by the server",
        siteData.site?.convBanner === EN && siteData.site?.convBannerFr === FR && siteData.site?.convBold === BOLD,
        JSON.stringify(siteData.site || {}));

  // 10. Live storefront renders the English banner
  const storeWin = await loadPage("/index.html");
  check("the saved banner renders in the live storefront header",
        bannerText(storeWin).includes(EN),
        bannerText(storeWin).slice(0, 90));

  // 11. Live storefront renders the bold highlight
  check("the bold highlight renders in the storefront header",
        bannerText(storeWin).includes(BOLD),
        bannerText(storeWin).slice(0, 90));

  // 12. Tapping FR toggle renders the French banner
  const frBtn = storeWin.document.querySelector('[data-lang="fr"]');
  if (frBtn) {
    frBtn.dispatchEvent(new storeWin.MouseEvent("click", { bubbles: true, cancelable: true }));
    await new Promise((r) => setTimeout(r, 600));
  }
  check("switching storefront to FR renders the French banner text",
        bannerText(storeWin).includes(FR),
        bannerText(storeWin).slice(0, 90));

  // 13. Clear French text in Admin UI and click Save banner
  convFrInput.value = "";
  convFrInput.dispatchEvent(new adminWin.Event("input", { bubbles: true }));
  if (saveBtn) {
    saveBtn.click();
  } else if (bannerForm) {
    bannerForm.dispatchEvent(new adminWin.Event("submit", { bubbles: true, cancelable: true }));
  }
  await new Promise((r) => setTimeout(r, 1200));

  const storeWinFr = await loadPage("/index.html?lang=fr");
  check("French shopper falls back to English when French text is cleared",
        bannerText(storeWinFr).includes(EN),
        bannerText(storeWinFr).slice(0, 90));

  // 14. Clear all banner inputs and click Save banner
  convInput.value = "";
  convFrInput.value = "";
  convBoldInput.value = "";
  convInput.dispatchEvent(new adminWin.Event("input", { bubbles: true }));
  convFrInput.dispatchEvent(new adminWin.Event("input", { bubbles: true }));
  convBoldInput.dispatchEvent(new adminWin.Event("input", { bubbles: true }));
  if (saveBtn) {
    saveBtn.click();
  } else if (bannerForm) {
    bannerForm.dispatchEvent(new adminWin.Event("submit", { bubbles: true, cancelable: true }));
  }
  await new Promise((r) => setTimeout(r, 1200));

  const storeWinDefault = await loadPage("/index.html");
  const defaultText = bannerText(storeWinDefault);
  check("clearing all banner inputs restores default banner without errors",
        defaultText.length > 10 && !defaultText.includes(EN),
        defaultText.slice(0, 90));

  // 15. Check no uncaught console errors
  check("zero console errors throughout the entire admin click flow",
        errors.length === 0,
        errors.slice(0, 6).join(" | "));

  const failed = results.filter(([ok]) => !ok);
  console.log(`\n=== ${results.length - failed.length}/${results.length} checks passed ===`);
  if (failed.length) {
    console.log("FAILED:\n" + failed.map(([, n]) => "  - " + n).join("\n"));
    process.exit(1);
  }
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
