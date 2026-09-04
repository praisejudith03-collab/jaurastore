/* jsdom simulation of the growth-suite frontend:
   1. checkout promo box applies a server-validated code and shows the discount
   2. the order-done screen paints the referral share block
   3. the product page review form is email-gated and server-backed
   4. checkout.html?recover=TOKEN restores the saved cart
   5. admin marketing tab functions exist and render the expected controls
   Run:  node tests/_growth_sim.mjs   (dev server must be on :8080) */
import { JSDOM } from "jsdom";
import fs from "node:fs";

const BASE = "http://127.0.0.1:8080/";
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "jaurastore@gmail.com";
// The harness signs in to the real shop, so the admin password is never
// written in this file - pass it in:  ADMIN_PW='...' node tests/_growth_sim.mjs
const ADMIN_PW = process.env.ADMIN_PW || "";
if (!ADMIN_PW) {
  console.error("ADMIN_PW is not set. Run this as:  ADMIN_PW='<admin password>' node tests/_growth_sim.mjs");
  process.exit(3);
}
let passed = 0, failed = 0;
const ok = (cond, name) => {
  if (cond) { passed += 1; console.log("PASS", name); }
  else { failed += 1; console.log("FAIL", name); }
};
process.on("uncaughtException", (e) => { console.log("uncaught:", e && e.message); });
const watchdog = setTimeout(() => { console.log(`\nWATCHDOG: ${passed} passed, ${failed} failed (stalled)`); process.exit(1); }, 90000);

// ---- fixture seeding -------------------------------------------------------
// Two of the features under test (a coupon and a saved cart) live in the
// SQLite database, which is gitignored and must never be committed - so a
// freshly cloned checkout has no rows and these checks fail for a reason that
// has nothing to do with the storefront. Seed both here through the app's own
// HTTP API, the same way an admin and a shopper would, before driving the UI.
// Idempotent: safe to re-run against a database that already has them, and it
// never touches a row that is not a sim fixture.
const jar = new Map();
function absorb(resp) {
  const raw = typeof resp.headers.getSetCookie === "function" ? resp.headers.getSetCookie() : [];
  for (const line of raw) {
    const m = /^([^=;]+)=([^;]*)/.exec(line);
    if (m) jar.set(m[1].trim(), m[2].trim());
  }
}
async function api(path, { json, ...opts } = {}) {
  const headers = { ...(opts.headers || {}) };
  const cookie = [...jar].map(([k, v]) => `${k}=${v}`).join("; ");
  if (cookie) headers.Cookie = cookie;
  if (json) headers["Content-Type"] = "application/json";
  const resp = await fetch(BASE + path, { ...opts, headers, body: json ? JSON.stringify(json) : opts.body });
  absorb(resp);
  return resp;
}
async function csrf() {
  const r = await api("api/csrf");
  const d = await r.json().catch(() => ({}));
  return d.token || "";
}

async function seedFixtures() {
  // 1. the saved cart the "recover my cart" link restores
  let tok = await csrf();
  let r = await api("api/cart/abandon", {
    method: "POST",
    json: { _csrf: tok, token: "CT-SIMRECOVER", email: "sim@example.com",
            currency: "NGN", items: [{ id: "wix-001", name: "Sim bag", qty: 2, color: "" }] },
  });
  let body = await r.json().catch(() => ({}));
  if (!body.ok || body.skipped) {
    console.log("WARN could not seed the abandoned cart:", JSON.stringify(body), r.status);
  }

  // 2. the 15% coupon the promo box validates.
  // Ask the public endpoint first: when the coupon is already good there is
  // nothing to create, and we avoid spending one of the six admin logins the
  // server allows per five minutes (so the suite can be re-run freely).
  const probe = await api("api/promo/check", { method: "POST", json: { code: "TEST-SIM" } });
  const probeBody = await probe.json().catch(() => ({}));
  if (probe.ok && probeBody.ok && Number(probeBody.percent) === 15) return;

  const login = await api("api/admin/login", {
    method: "POST", json: { email: ADMIN_EMAIL, password: ADMIN_PW },
  });
  const loginBody = await login.json().catch(() => ({}));
  if (!login.ok) {
    console.log("WARN admin login failed - cannot seed the coupon:", JSON.stringify(loginBody), login.status);
    return;
  }
  tok = loginBody.csrf || (await csrf());
  r = await api("api/admin/coupons", {
    method: "POST", json: { _csrf: tok, code: "TEST-SIM", percent: 15, note: "sim fixture" },
  });
  body = await r.json().catch(() => ({}));
  if (!r.ok) {
    // already present (or stale): force it back to an active 15% coupon
    tok = await csrf();
    const p = await api("api/admin/coupons/TEST-SIM", {
      method: "PATCH", json: { _csrf: tok, percent: 15, active: true, expiresAt: "", maxUses: null },
    });
    const pb = await p.json().catch(() => ({}));
    if (!p.ok) console.log("WARN could not seed the coupon:", JSON.stringify(body), JSON.stringify(pb));
  }
  await api("api/admin/logout", { method: "POST" });   // never leave a session behind
}
await seedFixtures();

async function page(path, { beforeScripts, settle = 1400 } = {}) {
  console.log("· loading", path);
  const html = await (await fetch(BASE + path)).text();
  const dom = new JSDOM(html, {
    url: BASE + path,
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const w = dom.window;
  w.matchMedia = w.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
  w.scrollTo = () => {};
  w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.HTMLMediaElement.prototype.play = () => Promise.resolve();
  w.fetch = (u, opts) => {
    const url = String(u).startsWith("http") ? String(u) : BASE + String(u).replace(/^\//, "");
    return fetch(url, opts);
  };
  w.navigator.clipboard = { writeText: () => Promise.resolve() };
  if (beforeScripts) beforeScripts(w);
  // Concatenate the page's classic scripts into ONE eval so top-level
  // const/let bindings (e.g. store.js `const JA`) stay visible across files,
  // exactly as sequential <script> tags behave in a real browser.
  const srcs = [...dom.window.document.querySelectorAll("script[src]")].map((s) => s.src);
  const chunks = [];
  for (const src of srcs) {
    const rel = new URL(src).pathname.replace(/^\//, "").split("?")[0];
    if (!fs.existsSync(rel)) continue;
    chunks.push(fs.readFileSync(rel, "utf8"));
  }
  try { w.eval(chunks.join("\n;\n")); }
  catch (e) { console.log("script error:", e.message); }
  w.document.dispatchEvent(new w.window.Event("DOMContentLoaded", { bubbles: true }));
  w.dispatchEvent(new w.window.Event("load"));
  await new Promise((r) => setTimeout(r, settle));
  return { dom, w };
}

// ---- 1. checkout promo box ----
{
  const { w } = await page("checkout.html", {
    beforeScripts: (w) => {
      w.localStorage.setItem("jaura_cart", JSON.stringify([{ id: "wix-001", qty: 1, color: "" }]));
      w.localStorage.setItem("jaura_currency", "NGN");
    },
  });
  const d = w.document;
  ok(d.querySelector("[data-ck-promo-input]") && d.querySelector("[data-ck-promo-apply]"), "checkout: promo input + apply button present");
  const totBefore = d.querySelector("[data-ck-total]")?.textContent || "";
  d.querySelector("[data-ck-promo-input]").value = "test-sim";
  d.querySelector("[data-ck-promo-apply]").click();
  await new Promise((r) => setTimeout(r, 1800));
  const discRow = d.querySelector("[data-ck-disc-row]");
  const msg = d.querySelector("[data-ck-promo-msg]");
  ok(discRow && !discRow.hidden, "checkout: discount row appears after a valid code");
  ok((d.querySelector("[data-ck-disc]")?.textContent || "").includes("15%"), "checkout: discount shows the 15% coupon");
  ok(msg && !msg.hidden && msg.classList.contains("is-good"), "checkout: success message shown");
  const totAfter = d.querySelector("[data-ck-total]")?.textContent || "";
  ok(totBefore !== totAfter, "checkout: total drops after the discount");
  // a junk code clears it again
  d.querySelector("[data-ck-promo-input]").value = "JUNK-999";
  d.querySelector("[data-ck-promo-apply]").click();
  await new Promise((r) => setTimeout(r, 1500));
  ok(msg.classList.contains("is-bad"), "checkout: invalid code rejected with a message");
  ok(d.querySelector("[data-ck-disc-row]").hidden, "checkout: discount row hides for an invalid code");
}

// ---- 2. referral share block on the order-done screen ----
{
  const { w } = await page("checkout.html", {
    beforeScripts: (w) => {
      w.localStorage.setItem("jaura_cart", JSON.stringify([{ id: "wix-001", qty: 1, color: "" }]));
      w.localStorage.setItem("ja_referral_last", JSON.stringify({ orderId: "JA-SIM1", code: "JA-TESTCD" }));
    },
  });
  w.eval(`showOrderDone({ id: "JA-SIM1", at: new Date().toISOString(), status: "pending",
    currency: "NGN", total: 25000, items: [{ qty: 1, name: "Bag", price: 25000 }],
    customer: { name: "Sim", city: "Lagos" } });`);
  await new Promise((r) => setTimeout(r, 300));
  const d = w.document;
  ok(!!d.querySelector(".referral-card"), "order done: referral card painted from the stash");
  ok((d.querySelector(".referral-code")?.textContent || "") === "JA-TESTCD", "order done: shows the minted code");
  ok(!!d.querySelector("[data-ref-share]") && !!d.querySelector("[data-ref-copy]"), "order done: share + copy buttons present");
  const wa = d.querySelector("[data-ref-wa]");
  ok(wa && wa.href.includes("wa.me") && wa.href.includes("JA-TESTCD"), "order done: WhatsApp share link carries the code");
  // and the late-arriving event path also works on a fresh screen
  w.localStorage.removeItem("ja_referral_last");
  w.eval(`showOrderDone({ id: "JA-SIM2", at: new Date().toISOString(), status: "pending",
    currency: "NGN", total: 25000, items: [], customer: {} });`);
  w.eval(`document.dispatchEvent(new CustomEvent("ja:referral", { detail: { orderId: "JA-SIM2", code: "JA-LATECD" } }));`);
  await new Promise((r) => setTimeout(r, 200));
  ok((d.querySelector(".referral-code")?.textContent || "") === "JA-LATECD", "order done: referral card also paints when the code arrives late");
}

// ---- 3. product page reviews ----
{
  const { w } = await page("product.html?id=wix-001", { settle: 2000 });
  const d = w.document;
  const form = d.querySelector("[data-rev-form]");
  ok(!!form, "product: review form present");
  ok(!!form?.querySelector("[name=email]"), "product: review form asks for the order email");
  ok(!!d.querySelector(".rev-gate-note"), "product: verified-buyers note shown");
}

// ---- 4. cart recovery link ----
{
  const { w } = await page("checkout.html?recover=CT-SIMRECOVER", { settle: 2200 });
  const cart = JSON.parse(w.localStorage.getItem("jaura_cart") || "[]");
  ok(cart.length === 1 && cart[0].id === "wix-001" && cart[0].qty === 2, "recover: saved cart restored into localStorage");
  ok(w.localStorage.getItem("ja_cart_token") === "CT-SIMRECOVER", "recover: token kept so the order closes the record");
}

// ---- 5. admin marketing tab wiring ----
{
  const { w } = await page("admin.html", { settle: 1500 });
  ok(w.eval("typeof marketingPanel === 'function' && typeof fillMarketing === 'function'"), "admin: marketing tab functions defined");
  const html = w.eval("marketingPanel()");
  ok(html.includes("mk-settings-card") && html.includes("mk-coupons-card")
     && html.includes("mk-referrals-card") && html.includes("mk-abandoned-card")
     && html.includes("mk-backup-now"), "admin: marketing panel renders all five cards");
  const src = fs.readFileSync("js/admin.js", "utf8");
  ok(src.includes('marketing: "Marketing"') && src.includes('navBtn("marketing")')
     && src.includes('if (tab === "marketing") fillMarketing()'), "admin: Marketing tab fully wired into the desk");
  ok(!src.includes("delivered"), "admin: no delivered status anywhere");
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
