/* Rate copy in a real DOM (jsdom): never a stale number, never a raw token.
 *
 * The footer line and the FAQ answers quote the exchange rate, and that rate
 * is admin-controlled. Static copy gets one thing wrong: the page paints
 * BEFORE GET /api/site answers, so on a first visit (empty cache) there is
 * no rate at all yet.
 *
 * Boots the shipped js/i18n.js in jsdom and proves:
 *   1. with no rate yet the copy shows a pending marker - not a hardcoded
 *      number and not the literal "{rate}" token;
 *   2. when the live row lands (ja:site) the copy re-renders with the real
 *      rate, in English ("0.45") and French ("0,45");
 *   3. changing the admin rate changes the copy - it is live, not frozen.
 *
 * Exit 3 when jsdom is missing, so the pytest wrapper can skip (or FAIL in
 * CI, which installs it).
 * Run directly:  node tests/_rate_copy_dom_check.mjs
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");
const require = createRequire(import.meta.url);

const candidates = [
  process.env.JA_JSDOM_DIR,
  join(ROOT, "node_modules"),
  join(process.cwd(), "node_modules"),
].filter(Boolean).map((dir) => (dir.endsWith("node_modules") ? dir : join(dir, "node_modules")));
let JSDOM = null;
for (const dir of candidates) {
  try { ({ JSDOM } = require(join(dir, "jsdom"))); break; } catch (err) { /* next */ }
}
if (!JSDOM) {
  console.error("SKIP: jsdom is not installed (npm install jsdom, or set JA_JSDOM_DIR)");
  process.exit(3);
}

const i18nSrc = readFileSync(join(ROOT, "js", "i18n.js"), "utf8");

let failures = 0;
function check(name, ok, detail = "") {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
  if (!ok) failures++;
}

function page() {
  const dom = new JSDOM(
    `<!doctype html><html lang="en"><body>
       <p id="pay" data-i18n="footer.pay"></p>
       <p id="faq3" data-i18n="faq.a3"></p>
     </body></html>`,
    { runScripts: "outside-only", url: "https://jaurastore.example/faq.html" });
  const { window } = dom;
  // The storefront's live site row; empty until GET /api/site answers.
  window.eval("window.JA = { getSiteConfig: () => ({}) };");
  window.eval(i18nSrc);
  return dom;
}

const text = (dom, id) => dom.window.document.getElementById(id).textContent;

// ------------------------------------------- 1. no rate yet: a pending marker
{
  const dom = page();
  dom.window.eval("I18N.apply()");
  const pay = text(dom, "pay");
  const faq = text(dom, "faq3");
  check("a raw {rate} token never reaches the shopper",
    !pay.includes("{rate}") && !faq.includes("{rate}"), pay);
  check("no invented number is shown before the rate is known",
    !/\d\.\d+/.test(pay), pay);
  check("a pending marker stands in until the live rate arrives",
    pay.includes("\u2026"), pay);
}

// ------------------------------------- 2. the live rate lands: copy re-renders
{
  const dom = page();
  const { window } = dom;
  window.eval("I18N.apply()");
  window.eval(`
    window.JA = { getSiteConfig: () => ({ cfaRate: 0.45 }) };
    document.dispatchEvent(new CustomEvent('ja:site', { detail: { cfaRate: 0.45 } }));
  `);
  const pay = text(dom, "pay");
  const faq = text(dom, "faq3");
  check("the live rate is rendered as soon as the site row lands",
    pay.includes("0.45") && faq.includes("0.45"), pay);
  check("the pending marker and token are both gone afterwards",
    !pay.includes("\u2026") && !pay.includes("{rate}"), pay);
}

// ------------------------------------------------- 3. a rate change is picked up
{
  const dom = page();
  const { window } = dom;
  window.eval(`
    window.JA = { getSiteConfig: () => ({ cfaRate: 0.44 }) };
    I18N.apply();
  `);
  check("the admin rate is what the copy shows",
    text(dom, "pay").includes("0.44"), text(dom, "pay"));
  window.eval(`
    window.JA = { getSiteConfig: () => ({ cfaRate: 1.5 }) };
    document.dispatchEvent(new CustomEvent('ja:site', { detail: { cfaRate: 1.5 } }));
  `);
  const pay = text(dom, "pay");
  check("changing the admin rate changes the copy - it is live, not frozen",
    pay.includes("1.5") && !pay.includes("0.44"), pay);
}

// ---------------------------------------------------- 4. French decimal comma
{
  const dom = page();
  const { window } = dom;
  window.eval(`
    I18N.setLang('fr');
    window.JA = { getSiteConfig: () => ({ cfaRate: 0.45 }) };
    I18N.apply();
  `);
  const pay = text(dom, "pay");
  check("French copy uses a comma, not a dot",
    pay.includes("0,45") && !pay.includes("0.45"), pay);
}

if (failures) {
  console.error(`\n${failures} rate copy check(s) FAILED`);
  process.exit(1);
}
console.log("\nRATE COPY DOM CHECKS PASSED");
process.exit(0);
