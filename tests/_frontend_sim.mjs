// Frontend browser-simulation harness (jsdom).
// Loads the real storefront page + scripts (runScripts: "dangerously" and
// resources: "usable"), routes fetch to the live server, then drives the real
// cart / checkout / analytics logic and captures console errors + exceptions.
//
// WHY: the sandbox cannot download a real Chromium/Playwright browser (the
// browser CDN is network-blocked), so this uses jsdom - a pure-JS DOM that
// actually EXECUTES the storefront's own JS in the same global scope a browser
// would, and talks to the live Flask server over HTTP. jsdom is a dev-only
// dependency, installed with:  npm install jsdom
// Run with the app up on :8080:
//   ADMIN_PW='...' node tests/_frontend_sim.mjs
let JSDOM, VirtualConsole, ResourceLoader;
try {
  const m = await import("jsdom");
  JSDOM = m.JSDOM; VirtualConsole = m.VirtualConsole; ResourceLoader = m.ResourceLoader;
} catch (e) {
  console.error("jsdom is required but not installed. Run: npm install jsdom  (repo root)");
  process.exit(3);
}

const BASE = process.env.BASE || "http://127.0.0.1:8080";
const ADMIN_PW = process.env.ADMIN_PW || "Jaura@Admin#2026x";
const results = [];
const errors = [];

function check(name, ok, detail = "") {
  results.push([!!ok, name]);
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
}

const vc = new VirtualConsole();
vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ")));
vc.on("jsdomError", (e) => errors.push("jsdomError: " + (e && e.message || e)));
vc.on("warn", (...a) => {
  const m = a.map(String).join(" ");
  if (!/Deprecat|ExperimentalWarning|Canvas|not implemented/i.test(m)) errors.push("console.warn: " + m);
});

// ---- cookie + fetch shim ----------------------------------------------------
const cookies = new Map();
function jarHeader() {
  return [...cookies.entries()].map(([k, v]) => `${k}=${v}`).join("; ");
}
const realFetch = globalThis.fetch;
async function doFetch(url, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const ck = jarHeader();
  if (ck && !headers.Cookie) headers.Cookie = ck;
  const resp = await realFetch(url, Object.assign({}, opts, { headers }));
  const sc = resp.headers.get("set-cookie");
  if (sc) {
    const parts = sc.split(/,\s*(?=[^;,\s]+=[^;,\s]+)/);
    for (const line of Array.isArray(sc) ? sc : parts) {
      const m = line.match(/^([^=]+)=([^;]*)/);
      if (m) cookies.set(m[1], m[2]);
    }
  }
  return resp;
}

function installShims(window) {
  window.fetch = doFetch;
  window.Request = globalThis.Request;
  window.Headers = globalThis.Headers;
  if (!window.requestAnimationFrame) window.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 16);
  if (!window.matchMedia) window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
  if (!window.scrollTo) window.scrollTo = () => {};
  if (!window.HTMLElement) { /* jsdom provides it */ }
  if (!window.IntersectionObserver) {
    window.IntersectionObserver = class {
      constructor(cb) { this.cb = cb; }
      observe(t) { if (t && this.cb) this.cb([{ isIntersecting: true, target: t }]); return this; }
      unobserve() {}
      disconnect() {}
      takeRecords() { return []; }
    };
  }
  if (!window.ResizeObserver) {
    window.ResizeObserver = class {
      constructor(cb) { this.cb = cb; }
      observe(t) { if (t && this.cb) this.cb([{ target: t }]); return this; }
      unobserve() {}
      disconnect() {}
    };
  }
  // net.js uses IndexedDB; jsdom lacks it. net.js has a localStorage fallback,
  // but guard the open() call too.
}

// Load only same-origin scripts; ignore external fonts/CDNs (blocked in sandbox)
// so they never abort page/script loading.
class LocalResourceLoader extends ResourceLoader {
  fetch(url, options) {
    const u = String(url);
    if (u.startsWith(BASE) || u.startsWith("http://127.0.0.1") || u.startsWith("http://localhost")) {
      return super.fetch(u, options);
    }
    // Return empty for external resources (fonts, analytics, etc.) - they are
    // not part of the under-test logic and are network-blocked here.
    return Promise.resolve(Buffer.from(""));
  }
}

async function main() {
  // ---- homepage / shop: load real page + scripts ---------------------------
  const shopHtml = await (await realFetch(BASE + "/shop.html")).text();
  const dom = new JSDOM(shopHtml, {
    url: BASE + "/shop.html",
    runScripts: "dangerously",
    resources: new LocalResourceLoader(),
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(window) { installShims(window); },
  });
  const { window } = dom;
  await new Promise((resolve) => {
    if (window.document.readyState === "complete") resolve();
    else window.addEventListener("load", resolve, { once: true });
    setTimeout(resolve, 6000); // safety
  });

  const JA = window.JA || (typeof window.eval === "function" ? window.eval("JA") : undefined);
  check("all frontend scripts loaded without error", errors.length === 0,
        errors.length ? errors.slice(0, 6).join(" | ") : "");
  check("JA storefront API exposed", !!JA && typeof JA.addToCart === "function");

  const prods = window.JA_SEED || [];
  check("product catalogue loaded (258)", Array.isArray(prods) && prods.length >= 200, prods.length);

  if (!JA) { finish(); return; }

  const qtyOf = (id) => JA.cart().filter((i) => i.id === id).reduce((n, i) => n + (Number(i.qty) || 0), 0);

  // ---- Add to cart + cart state -------------------------------------------
  const first = prods.find((p) => (p.stock || 0) > 0) || prods[0];
  JA.addToCart(first.id, 2, "");
  JA.addToCart(first.id, 1, "");
  const cart = JA.cart();
  check("add to cart stores item", cart.some((i) => i.id === first.id), JSON.stringify(cart));
  check("cart quantity accumulates", qtyOf(first.id) === 3, "qty=" + qtyOf(first.id));
  check("cart count reflects adds", JA.cartCount() >= 3, "count=" + JA.cartCount());

  JA.setCurrency("CFA");
  const total = JA.cartTotal("CFA");
  check("cart total computes (CFA)", typeof total === "number" && total > 0, "total=" + total);

  JA.setQty(first.id, "", 5);
  check("setQty updates quantity", qtyOf(first.id) === 5, "qty=" + qtyOf(first.id));

  // ---- Cart page renders ---------------------------------------------------
  const cartHtml = await (await realFetch(BASE + "/cart.html")).text();
  check("cart page serves", /cart/i.test(cartHtml) && cartHtml.length > 200, "len=" + cartHtml.length);

  // ---- Checkout: pickup removal + receipt accept ---------------------------
  const ckHtml = await (await realFetch(BASE + "/checkout.html")).text();
  const dom2 = new JSDOM(ckHtml, {
    url: BASE + "/checkout.html", runScripts: "dangerously", resources: new LocalResourceLoader(),
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(window) { installShims(window); },
  });
  const w2 = dom2.window;
  await new Promise((res) => { w2.addEventListener("load", res, { once: true }); setTimeout(res, 6000); });

  // app.js removes pickup options at bind time; ensure we read AFTER scripts run
  let zoneText = w2.document.querySelector("[data-delivery-zones]")?.textContent || "";
  // Fall back to raw if list re-rendered on cart
  if (!zoneText) zoneText = w2.document.querySelector(".fare-list")?.textContent || "";
  check("'Pick up' is NOT in the delivery zone menu", !/pick\s*-?\s*up/i.test(zoneText),
        zoneText.replace(/\s+/g, " ").trim().slice(0, 90));
  check("delivery includes Cotonou option", /Cotonou/i.test(zoneText), "zoneText=" + zoneText.replace(/\s+/g, " ").slice(0, 60));
  check("receipt upload accepts PDF/JPG/PNG",
        /application\/pdf/.test(ckHtml) && /image\/jpeg/.test(ckHtml) && /image\/png/.test(ckHtml));

  // ---- Real backend order (PDF receipt) ------------------------------------
  const cfg = await (await doFetch(BASE + "/api/config")).json();
  const csrfTok = (cfg && cfg.csrf) || "";
  const order = {
    id: "JA-TEST" + Date.now().toString().slice(-6),
    customer: { name: "Grace Mensah", email: "grace@example.com", phone: "+229 97 00 11 22",
                city: "Cotonou", zone: "Cotonou", address: "12 Rue des Palmiers", country: "Benin" },
    items: [{ id: first.id, name: first.name, qty: 1, price: first.priceCfa }],
    currency: "CFA", total: first.priceCfa,
  };
  const boundary = "----Jaura" + Math.random().toString(16).slice(2);
  const pdf = Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF");
  const head = Buffer.from(
    `--${boundary}\r\nContent-Disposition: form-data; name="order"\r\n\r\n` +
    JSON.stringify(order) + "\r\n" +
    `--${boundary}\r\nContent-Disposition: form-data; name="proof"; filename="receipt.pdf"\r\n` +
    "Content-Type: application/pdf\r\n\r\n");
  const tail = Buffer.from(`\r\n--${boundary}--\r\n`);
  const bodyBuf = Buffer.concat([head, pdf, tail]);
  const ordRes = await doFetch(BASE + "/api/orders", {
    method: "POST",
    headers: { "Content-Type": `multipart/form-data; boundary=${boundary}`, "X-CSRF-Token": csrfTok },
    body: bodyBuf,
  });
  const ord = await ordRes.json().catch(() => ({}));
  check("checkout order places OK (PDF receipt)", ordRes.status === 200 && ord.ok === true,
        "status=" + ordRes.status + " proof=" + ord.proofUrl);

  // pickup / self-collect rejected server-side
  for (const z of ["Pick Up", "Self collect", "pick up"]) {
    const b2 = Object.assign({}, order, { id: "JA-PICK" + z.replace(/\W/g, "").slice(0, 4),
      customer: Object.assign({}, order.customer, { zone: z }) });
    const rr = await doFetch(BASE + "/api/orders", { method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfTok }, body: JSON.stringify(b2) });
    check(`server rejects '${z}' as a delivery zone`, rr.status === 400, "status=" + rr.status);
  }

  // ---- Analytics: real-time visitor + report --------------------------------
  await doFetch(BASE + "/api/track", { method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfTok },
    body: JSON.stringify({ events: [{ type: "pageview", page: "/" }] }) });
  const loginRes = await doFetch(BASE + "/api/admin/login", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "jaurastore@gmail.com", password: ADMIN_PW }) });
  const login = await loginRes.json().catch(() => ({}));
  check("admin login with shared password", loginRes.status === 200 && login.ok === true, "status=" + loginRes.status);
  const csrf = login.csrf || "";
  const liveRes = await doFetch(BASE + "/api/admin/live", { headers: { "X-CSRF-Token": csrf } });
  const live = await liveRes.json().catch(() => ({}));
  check("live visitor tracking returns data", liveRes.status === 200 && Array.isArray(live.visitors), "visitors=" + JSON.stringify(live.visitors));
  const anRes = await doFetch(BASE + "/api/admin/analytics?days=7", { headers: { "X-CSRF-Token": csrf } });
  const an = await anRes.json().catch(() => ({}));
  check("analytics report has revenue + conversion", anRes.status === 200 && an.conversion && typeof an.conversion.revenue === "number",
        "revenue=" + (an.conversion && an.conversion.revenue) + " orders=" + (an.conversion && an.conversion.orders));
  const mvRes = await doFetch(BASE + "/api/admin/most-viewed?limit=5", { headers: { "X-CSRF-Token": csrf } });
  check("most-viewed products endpoint OK", mvRes.status === 200);

  // ---- Admin portal: masked password + change ------------------------------
  const admHtml = await (await realFetch(BASE + "/admin.html")).text();
  const admDom = new JSDOM(admHtml, {
    url: BASE + "/admin.html", runScripts: "dangerously", resources: new LocalResourceLoader(),
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(window) { installShims(window); },
  });
  const aw = admDom.window;
  await new Promise((res) => { aw.addEventListener("load", res, { once: true }); setTimeout(res, 6000); });
  await new Promise((res) => setTimeout(res, 800)); // let async login paint run
  const pwInput = aw.document.querySelector("#login-form input[name=password], #admin-root input[type=password]");
  check("admin login password input is masked (type=password)", !!pwInput && pwInput.type === "password",
        pwInput ? "type=" + pwInput.type : "no password input found");
  check("admin portal served (no 500)", admHtml.length > 500 && /admin/i.test(admHtml), "len=" + admHtml.length);

  finish();
}

function finish() {
  const fails = results.filter(([ok]) => !ok);
  console.log(`\n=== ${results.length - fails.length}/${results.length} checks passed ===`);
  if (errors.length) console.log("\nCONSOLE ERRORS:\n" + errors.slice(0, 20).join("\n"));
  process.exit((fails.length || errors.length) ? 1 : 0);
}

main().catch((e) => { console.error("FATAL " + e.stack); process.exit(2); });
