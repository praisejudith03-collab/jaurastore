/* The admin's save toast, in a real DOM: created, coloured, replaced, gone.
 *
 * The owner's directive says a confirmed save must be an unmistakable green
 * confirmation, and the existing tests only grep the source for that intent
 * (js/admin.js calls JA.success, css/style.css has the success rule). This
 * check loads the shipped js/store.js into jsdom and drives the real toast:
 *
 *   1. JA.success("...") creates ONE .toast with data-kind="success",
 *      class is-success (the green rule) and class show;
 *   2. a neutral notice reuses the same element and clears the kind, so a
 *      stale green cannot advertise a message that was not a save;
 *   3. an error toast is data-kind="error" / is-error and is never green;
 *   4. the toast hides itself again after its 2.2 s timer.
 *
 * Exit codes: 0 all checks pass, 1 a check failed, 3 jsdom missing.
 *
 * Run directly:  node tests/_toast_check.mjs
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
let jsdomDir = null;
for (const dir of candidates) {
  try { ({ JSDOM } = require(join(dir, "jsdom"))); jsdomDir = dir; break; } catch (err) { /* next */ }
}
if (!JSDOM) {
  console.error("SKIP: jsdom is not installed (npm install jsdom, or set JA_JSDOM_DIR)");
  process.exit(3);
}

const dom = new JSDOM(
  '<!doctype html><html lang="en"><head></head><body data-page="admin"></body></html>',
  {
    url: "http://127.0.0.1/admin.html",
    runScripts: "outside-only",
    pretendToBeVisual: true,
    virtualConsole: (() => {
      const vc = new (require(join(jsdomDir, "jsdom")).VirtualConsole)();
      vc.on("jsdomError", (err) => console.error("jsdomError:", err && err.message));
      return vc;
    })(),
    beforeParse(window) {
      // The store bundle may probe these at load; none of them may reach the
      // network, so every request simply never answers.
      window.fetch = () => new Promise(() => {});
      window.matchMedia = () => ({
        matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
      });
      window.scrollTo = () => {};
      window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
      window.IntersectionObserver = class {
        constructor(cb) { this.cb = cb; }
        observe() {} unobserve() {} disconnect() {}
      };
      window.localStorage.setItem("jaura.lang", "en");
    },
  },
);
const window = dom.window;
const doc = window.document;

try {
  // ``const JA`` is a lexical declaration, and a lexical binding created by an
  // indirect eval stays inside that eval. Appending the export to the SAME
  // source puts JA on the window, which is what the rest of this check drives.
  window.eval(readFileSync(join(ROOT, "js", "store.js"), "utf8") + "\n;window.JA = JA;");
} catch (err) {
  console.error("could not load js/store.js:", err && err.stack || err);
  process.exit(1);
}
if (!window.JA || typeof window.JA.success !== "function") {
  console.error("js/store.js loaded without exporting JA");
  process.exit(1);
}
let failures = 0;
function check(name, ok, detail) {
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail !== undefined ? "  ->  " + detail : ""}`);
}

/* 1. a confirmed save is green */
window.JA.success("Saved successfully · live on the shop");
const nodes = doc.querySelectorAll(".toast");
const toast = nodes[0] || null;
check("a confirmed save creates exactly one toast", nodes.length === 1, `${nodes.length} toast node(s)`);
check("the success toast is marked as a success",
  !!toast && toast.getAttribute("data-kind") === "success",
  toast ? toast.getAttribute("data-kind") : "missing");
check("the success toast carries the green class",
  !!toast && toast.classList.contains("is-success") && !toast.classList.contains("is-error"),
  toast ? toast.className : "missing");
check("the success toast is visible",
  !!toast && toast.classList.contains("show"), toast ? toast.className : "missing");
check("the success toast shows the exact sentence the admin expects",
  !!toast && toast.textContent === "Saved successfully · live on the shop",
  toast ? toast.textContent : "missing");

/* 2. a neutral notice reuses that element and drops the green */
window.JA.toast("Heads up");
const afterNeutral = doc.querySelectorAll(".toast");
check("a storefront notice reuses the single toast element",
  afterNeutral.length === 1 && afterNeutral[0] === toast, `${afterNeutral.length} toast node(s)`);
check("a neutral notice is not painted as a success",
  !!toast && !toast.hasAttribute("data-kind") && !toast.classList.contains("is-success")
    && !toast.classList.contains("is-error"),
  toast ? `${toast.getAttribute("data-kind")} ${toast.className}` : "missing");
check("the neutral notice replaces the previous text",
  !!toast && toast.textContent === "Heads up", toast ? toast.textContent : "missing");

/* 3. an error is red, never green */
window.JA.toast("Could not save the product. No changes are live.", { kind: "error" });
check("an error toast is marked as an error",
  !!toast && toast.getAttribute("data-kind") === "error" && toast.classList.contains("is-error")
    && !toast.classList.contains("is-success"),
  toast ? `${toast.getAttribute("data-kind")} ${toast.className}` : "missing");

/* 4. the stylesheet really has the success rule the toast relies on */
const css = readFileSync(join(ROOT, "css", "style.css"), "utf8");
check("css/style.css styles the success variant",
  /\.toast\[data-kind="success"\]/.test(css), "rule present");
check("css/style.css styles the error variant",
  /\.toast\[data-kind="error"\]/.test(css), "rule present");

/* 5. the toast hides itself again (2.2 s timer) */
window.JA.success("Saved successfully · live on the shop");
await new Promise((resolve) => setTimeout(resolve, 2500));
check("the toast hides itself when its timer expires",
  !!toast && !toast.classList.contains("show"), toast ? toast.className : "missing");

console.log(failures ? `\n${failures} toast check(s) failed` : "\nADMIN TOAST CHECKS PASSED");
window.close();
process.exit(failures ? 1 : 0);
