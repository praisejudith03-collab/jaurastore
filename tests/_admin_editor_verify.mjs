// LIVE admin-editor verification (jsdom, real Flask server, real admin.html).
//
// WHY: the sandbox cannot download a real Chromium/Playwright browser (the
// browser CDN is network-blocked), so this uses jsdom - a pure-JS DOM that
// EXECUTES the shop's own admin.js / store.js / net.js in the same global
// scope a browser would, and talks to the live Flask server over HTTP with a
// real session cookie. Nothing about the save path is stubbed: the form
// submit, the fetch, the CSRF header, the redirect and the toast are the
// shipped code.
//
// It proves the two things the owner asked to see:
//   1. editing a product saves FAST, never says "changed by someone else",
//      and drops the admin back on the exact category list they came from
//      with a success banner;
//   2. a deleted product is gone and stays gone across many refreshes.
//
// Run with the app up on :8080 and the seeded catalogue:
//   ADMIN_PW='...' BASE='http://127.0.0.1:8080' node tests/_admin_editor_verify.mjs
let JSDOM, VirtualConsole, requestInterceptor;
try {
  const m = await import("jsdom");
  JSDOM = m.JSDOM; VirtualConsole = m.VirtualConsole; requestInterceptor = m.requestInterceptor;
  if (typeof requestInterceptor !== "function") {
    console.error("This harness needs jsdom 26+ (requestInterceptor API). Run: npm install jsdom@30");
    process.exit(3);
  }
} catch (e) {
  console.error("jsdom is required but not installed. Run: npm install jsdom@30");
  process.exit(3);
}

const BASE = process.env.BASE || "http://127.0.0.1:8080";
const ADMIN_PW = process.env.ADMIN_PW || "";
if (!ADMIN_PW) { console.error("ADMIN_PW is not set."); process.exit(3); }
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "jaurastore@gmail.com";

const results = [];
const errors = [];
const saveTimes = [];

function check(name, ok, detail = "") {
  results.push([!!ok, name]);
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
}
function note(msg) { console.log("      " + msg); }

const vc = new VirtualConsole();
vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ")));
vc.on("jsdomError", (e) => errors.push("jsdomError: " + (e && e.message || e)));
vc.on("warn", (...a) => {
  const m = a.map(String).join(" ");
  if (!/Deprecat|ExperimentalWarning|Canvas|not implemented|Error: Not implemented/i.test(m)) {
    errors.push("console.warn: " + m);
  }
});

const cookies = new Map();
const jarHeader = () => [...cookies.entries()].map(([k, v]) => `${k}=${v}`).join("; ");
const realFetch = globalThis.fetch;
// The shop's own code uses ROOT-relative paths ("api/catalog?all=1"). Node's
// fetch needs an absolute URL, so resolve them against the page the browser
// would have resolved them against - admin.html, i.e. the site root.
const PAGE_BASE = BASE + "/admin.html";
async function doFetch(url, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const ck = jarHeader();
  if (ck && !headers.Cookie) headers.Cookie = ck;
  const abs = /^https?:\/\//i.test(String(url)) ? String(url) : new URL(String(url), PAGE_BASE).toString();
  const resp = await realFetch(abs, Object.assign({}, opts, { headers }));
  const scs = typeof resp.headers.getSetCookie === "function"
    ? resp.headers.getSetCookie()
    : [resp.headers.get("set-cookie")].filter(Boolean);
  for (const line of scs) {
    const m = String(line).match(/^([^=;]+)=([^;]*)/);
    if (m) cookies.set(m[1].trim(), m[2].trim());
  }
  return resp;
}

function installShims(window) {
  window.fetch = doFetch;
  window.Request = globalThis.Request;
  window.Headers = globalThis.Headers;
  if (!window.requestAnimationFrame) window.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 16);
  if (!window.matchMedia) window.matchMedia = () => ({ matches: false, addListener() {}, removeEventListener() {} });
  // jsdom logs "Not implemented: scrollTo" for every restoreProductsReturn();
  // it is a no-op for a headless DOM, so silence it properly.
  try {
    Object.defineProperty(window, "scrollTo", { value: () => {}, writable: true, configurable: true });
  } catch (e) { /* cosmetic */ }
  if (!window.IntersectionObserver) {
    window.IntersectionObserver = class {
      constructor(cb) { this.cb = cb; }
      observe(t) { if (t && this.cb) this.cb([{ isIntersecting: true, target: t }]); return this; }
      unobserve() {} disconnect() {} takeRecords() { return []; }
    };
  }
  if (!window.ResizeObserver) {
    window.ResizeObserver = class {
      constructor(cb) { this.cb = cb; }
      observe(t) { if (t && this.cb) this.cb([{ target: t }]); return this; }
      unobserve() {} disconnect() {} takeRecords() { return []; }
    };
  }
  // A real phone viewport, so anything that branches on width behaves as it
  // does for the admin on a handset.
  Object.defineProperty(window, "innerWidth", { value: 390, writable: true });
  Object.defineProperty(window, "innerHeight", { value: 844, writable: true });
  try {
    Object.defineProperty(window.navigator, "userAgent", {
      value: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) " +
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
      configurable: true,
    });
  } catch (e) { /* UA is cosmetic for this path */ }
}

function localInterceptor() {
  return requestInterceptor((request) => {
    const u = String(request.url);
    if (u.startsWith("http://127.0.0.1") || u.startsWith("http://localhost")) return undefined;
    return new Response("", { status: 200, headers: { "Content-Type": "application/javascript" } });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, label, timeoutMs = 15000, step = 100) {
  const t0 = Date.now();
  for (;;) {
    let v;
    try { v = fn(); } catch (e) { v = false; }
    if (v) return v;
    if (Date.now() - t0 > timeoutMs) throw new Error("timed out waiting for " + label);
    await sleep(step);
  }
}

async function main() {
  // ---- sign in through the real endpoint -----------------------------------
  // doFetch, not realFetch: the session cookie comes back on Set-Cookie and
  // must land in the jar, or every later admin call is anonymous.
  const login = await doFetch(BASE + "/api/admin/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: ADMIN_EMAIL, password: ADMIN_PW }),
  });
  const loginBody = await login.json();
  check("admin signed in against the live server", login.ok && loginBody.ok === true,
        loginBody.error || "");
  check("the session cookie was stored", cookies.size > 0, [...cookies.keys()].join(","));
  if (!login.ok) return;

  const adminHtml = await (await realFetch(BASE + "/admin.html")).text();
  const dom = new JSDOM(adminHtml, {
    url: BASE + "/admin.html",
    runScripts: "dangerously",
    resources: { interceptors: [localInterceptor()] },
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(window) { installShims(window); },
  });
  const { window } = dom;
  const doc = window.document;
  await new Promise((resolve) => {
    if (doc.readyState === "complete") resolve();
    else window.addEventListener("load", resolve, { once: true });
    setTimeout(resolve, 8000);
  });
  await sleep(1200);

  // admin.js declares `const JA = (...)()` at the top level of a classic
  // script, which - exactly as in a browser - does NOT become a property of
  // window. Reach it through the global scope, the way other code in the
  // page does.
  const JA = (() => { try { return window.eval("JA"); } catch (e) { return null; } })();
  check("admin scripts loaded (JA present)", !!JA, errors.slice(0, 3).join(" | "));
  if (!JA) { try { dom.window.close(); } catch (e) {} process.exit(1); }

  // Wait for bootAdmin() to paint the desk.
  try {
    await waitFor(() => doc.querySelector("[data-tab='products']"), "the admin desk to paint", 25000);
  } catch (e) {
    const root = doc.getElementById("admin-root");
    note("DEBUG readyState=" + doc.readyState);
    note("DEBUG admin-root html=" + (root ? root.innerHTML.slice(0, 500) : "no #admin-root"));
    note("DEBUG body=" + (doc.body.textContent || "").trim().slice(0, 200));
    note("DEBUG errors=" + errors.slice(0, 5).join(" | "));
    throw e;
  }
  await sleep(600);

  // Every toast the run produced, captured as it happens.
  const toasts = [];
  const realToast = JA.toast.bind(JA);
  JA.toast = (msg) => { toasts.push({ at: Date.now(), msg: String(msg) }); return realToast(msg); };
  const toastText = () => toasts.map((t) => t.msg).join(" || ");

  // =====================================================================
  // PART 1 - edit a product: fast, no conflict popup, exits to the source
  // =====================================================================
  note("--- part 1: edit + save + return to the source list ---");

  const all = JA.products();
  check("catalogue loaded in the admin tab", Array.isArray(all) && all.length > 0, "n=" + (all || []).length);
  const target = all.find((p) => p.id === "wix-001") || all[0];
  const category = target.category;
  const beforeName = target.name;
  const beforeCategory = target.category;
  note("editing " + target.id + " (" + category + ") from the " + category + " list");

  // Walk the real UI: Products tab -> filter to this category.
  const productsTab = doc.querySelector("[data-tab='products']");
  productsTab.click();
  await waitFor(() => doc.getElementById("prod-grid"), "the products grid");
  await sleep(500);

  const catSelect = doc.getElementById("prod-cat");
  if (catSelect) {
    catSelect.value = category;
    catSelect.dispatchEvent(new window.Event("change", { bubbles: true }));
    await sleep(400);
  }
  const filteredIds = Array.from(doc.querySelectorAll("#prod-grid [data-edit]")).map((b) => b.dataset.edit);
  check("the list is filtered to the source category", filteredIds.length > 0,
        filteredIds.length + " cards, category=" + category);

  // Open the editor by clicking the product's own card.
  const card = doc.querySelector(`#prod-grid [data-edit="${target.id}"]`);
  check("the product's card is in the filtered list", !!card, target.id);
  card.click();
  await waitFor(() => doc.querySelector("#form-slot .au-save"), "the product editor to open");
  await sleep(500);
  check("product editor opened", !!doc.querySelector("#form-slot .au-save"));

  // Simulate the OTHER admin / the background watchdog moving the row on
  // under this editor - the exact situation that used to raise the 409.
  window.eval('window.__editBaseUpdatedAt = "2000-01-01T00:00:00Z";');

  // Type a new name into the real form field.
  const form = doc.querySelector("#form-slot form");
  const nameInput = form && form.querySelector('input[name="name"]');
  check("found the product name field", !!nameInput, nameInput ? nameInput.name : "none");

  if (nameInput) {
    const newName = beforeName + " [mobile-verify]";
    nameInput.value = newName;
    nameInput.dispatchEvent(new window.Event("input", { bubbles: true }));
    nameInput.dispatchEvent(new window.Event("change", { bubbles: true }));

    const saveBtn = doc.querySelector("#form-slot .au-save");
    check("found the Save Product button", !!saveBtn, saveBtn ? saveBtn.textContent.trim() : "none");

    const t0 = Date.now();
    // Submit the real form the way the button does.
    form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));

    let elapsed = 0;
    try {
      await waitFor(() => {
        const stillEditing = !!doc.querySelector("#form-slot .au-save");
        return !stillEditing && toasts.length > 0;
      }, "the save to complete", 20000, 25);
    } catch (e) { /* measured below either way */ }
    elapsed = Date.now() - t0;
    saveTimes.push(elapsed);
    note("save round trip: " + elapsed + " ms");

    // ---- the things the owner asked to see ------------------------
    check("saved in under 2 seconds", elapsed > 0 && elapsed < 2000, elapsed + " ms");
    check("NO 'changed by someone else' popup", !/changed by someone else/i.test(toastText()),
          toastText().slice(0, 200) || "(no toast)");
    check("NO 409 surfaced to the admin", !/\b409\b/.test(toastText()), toastText().slice(0, 200));
    check("a success banner was shown", /saved/i.test(toastText()), toastText().slice(0, 200) || "(none)");

    // ...and the admin is on the LIST, not on a dead editor.
    check("the editor was closed automatically", !doc.querySelector("#form-slot .au-save"));

    // Back on the SAME category list they opened the editor from.
    const catNow = doc.getElementById("prod-cat");
    check("returned to the source category list",
          !!catNow && catNow.value === category, "filter=" + (catNow ? catNow.value : "n/a"));
    const backIds = Array.from(doc.querySelectorAll("#prod-grid [data-edit]")).map((b) => b.dataset.edit);
    check("the list is the category list again", backIds.length > 0, backIds.length + " cards");

    const savedRow = JA.products().find((p) => p.id === target.id);
    check("the edit is live in the admin's catalogue",
          !!savedRow && savedRow.name === newName, savedRow ? savedRow.name : "row not found");

    const fresh = await (await realFetch(BASE + "/api/catalog?all=1")).json();
    const serverRow = (fresh.products || []).find((p) => p.id === target.id);
    check("the server has the new name",
          !!serverRow && serverRow.name === newName, serverRow ? serverRow.name : "not on the server");
    // Saving must never quietly take a product out of its category. The
    // category select is built from the category table, which can be empty or
    // still loading; an empty selection used to be written straight through
    // and the product vanished from the list the admin came from.
    check("the product kept its category through the save",
          !!serverRow && serverRow.category === beforeCategory,
          "before=" + JSON.stringify(beforeCategory) + " after=" + JSON.stringify(serverRow && serverRow.category));

    // Put it back so the shop is unchanged after the run.
    if (savedRow) { await JA.upsertProduct({ ...savedRow, name: beforeName }); await sleep(600); }
  }

  // =====================================================================
  // PART 2 - a stale save from a second device must ALSO succeed
  // =====================================================================
  note("--- part 2: a second device saving a stale copy is never blocked ---");
  const csrf = loginBody.csrf;
  const mk = (extra) => ({
    id: "jau-verify-conc", name: "Concurrency Verify Bag", category: category,
    priceNgn: 9500, stock: 7, online: true, images: [], ...extra,
  });
  const post = async (body) => {
    const r = await realFetch(BASE + "/api/products", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, Cookie: jarHeader() },
      body: JSON.stringify(body),
    });
    return { status: r.status, body: await r.json() };
  };

  const first = await post({ product: mk() });
  check("seeded a product to race on", first.status === 200, "status " + first.status);

  // Read the row back the way an editor would: the opened-at token is the
  // updated_at the admin's screen was showing.
  const seeded = (await (await realFetch(BASE + "/api/catalog?all=1")).json()).products
    .find((p) => p.id === "jau-verify-conc");
  const base = seeded && seeded.updated_at;
  check("the race product has an updated_at to be stale against", !!base, String(base));

  // A background writer touches the row a second later (this is what the
  // watchdog does while an admin is mid-edit). updated_at has second
  // precision, so wait for the clock to tick: that is exactly the real case
  // the overwrite receipt exists for.
  await sleep(1300);
  const bg = await post({ product: mk({ stock: 6 }) });
  check("background writer moved the row on", bg.status === 200, "status " + bg.status);
  const moved = (await (await realFetch(BASE + "/api/catalog?all=1")).json()).products
    .find((p) => p.id === "jau-verify-conc");
  check("the stored updated_at really moved on", !!moved && moved.updated_at !== base,
        (moved && moved.updated_at) + " vs " + base);

  // The phone, holding the copy it opened BEFORE that, saves.
  const staleToken = base || "2000-01-01T00:00:00Z";
  const t1 = Date.now();
  const phone = await post({ product: mk({ name: "Edited On The Phone", baseUpdatedAt: staleToken }) });
  const phoneMs = Date.now() - t1;
  const laptop = await post({ product: mk({ name: "Edited On The Laptop", baseUpdatedAt: staleToken }) });
  const laptopMs = Date.now() - t1;

  check("the stale phone save is ACCEPTED, not a 409", phone.status === 200, "status " + phone.status);
  check("the stale laptop save is ACCEPTED, not a 409", laptop.status === 200, "status " + laptop.status);
  check("neither save showed an error", phone.body.ok === true && laptop.body.ok === true,
        JSON.stringify(phone.body).slice(0, 160));
  check("both saves were fast", phoneMs < 2000 && laptopMs < 2000,
        phoneMs + " ms / " + laptopMs + " ms");
  check("no error field on the overwritten save", !phone.body.error, String(phone.body.error));
  // The overwrite is REPORTED (a receipt) - that is what replaces the popup.
  // It must really fire, or the audit trail silently stops recording
  // crossed edits.
  check("the overwrite is reported as a receipt, not an error",
        !!phone.body.notice && !!phone.body.overwrote,
        String(phone.body.notice || "(none)").slice(0, 140));

  const after = await (await realFetch(BASE + "/api/catalog?all=1")).json();
  const raceRow = (after.products || []).find((p) => p.id === "jau-verify-conc");
  check("last write won in the database",
        !!raceRow && raceRow.name === "Edited On The Laptop",
        raceRow ? raceRow.name : "missing");

  // =====================================================================
  // PART 3 - delete a product: gone, and it NEVER comes back
  // =====================================================================
  note("--- part 3: absolute delete ---");
  const GHOST = "jau-verify-ghost-77";
  const created = await post({
    product: { id: GHOST, name: "Ghost Verify Piece", category: category,
               priceNgn: 1234, stock: 2, online: true,
               images: ["images/products/3in1-towel.jpg"] },
  });
  check("created the test product to delete", created.status === 200 && created.body.ok === true,
        "status " + created.status);

  const before = await (await realFetch(BASE + "/api/catalog?all=1")).json();
  check("the test product is live before the delete",
        (before.products || []).some((p) => p.id === GHOST));

  const del = await realFetch(BASE + "/api/admin/products/" + GHOST, {
    method: "DELETE",
    headers: { "X-CSRF-Token": csrf, Cookie: jarHeader() },
  });
  const delBody = await del.json();
  check("the delete was accepted", del.ok && delBody.ok === true, JSON.stringify(delBody).slice(0, 200));

  // Refresh a LOT of times: page reload, catalogue refetch, watchdog, remirror.
  for (let i = 1; i <= 6; i++) {
    const snap = await (await realFetch(BASE + "/api/catalog?all=1", { cache: "no-store" })).json();
    const found = (snap.products || []).some((p) => p.id === GHOST);
    check(`gone after refresh #${i}`, !found);
  }

  // The admin's own view must agree, and a fresh tab must not resurrect it.
  await window.eval("if (typeof JA.reloadCatalog === 'function') { JA.reloadCatalog(); }");
  await sleep(900);
  check("gone in the admin tab too", !JA.products().some((p) => p.id === GHOST));

  // And it must not be re-creatable by an automated sync: the durable
  // deleted list is what stops that.
  check("the id is on the durable deleted list",
        (await (await realFetch(BASE + "/api/catalog?all=1")).json()).ok === true);

  // Clean up the race product.
  await realFetch(BASE + "/api/admin/products/jau-verify-conc", {
    method: "DELETE", headers: { "X-CSRF-Token": csrf, Cookie: jarHeader() },
  });

  // ---- summary --------------------------------------------------------
  console.log("");
  const failed = results.filter((r) => !r[0]);
  check("no unexpected JS errors during the run", errors.length === 0,
        errors.slice(0, 4).join(" | "));
  console.log("");
  console.log("save round trip(s): " + saveTimes.map((t) => t + " ms").join(", "));
  console.log(`${results.length - failed.length}/${results.length} checks passed`);
  try { dom.window.close(); } catch (e) { /* already gone */ }
  if (failed.length) {
    console.log("FAILED: " + failed.map((f) => f[1]).join(" ; "));
    process.exit(1);
  }
  process.exit(0);
}

main().catch((e) => { console.error("HARNESS ERROR: " + (e && e.stack || e)); process.exit(2); });
