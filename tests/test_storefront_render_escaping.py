"""Functional escaping test: the REAL storefront render functions (executed
in a Node VM) must neutralise hostile product data.

Server-side `sec.clean()` strips markup before a row is stored, but it keeps
quotes - so a product name like `Purse " onmouseover="alert(1)` IS storable,
and the client-side escape() in cardHTML / mediaHTML is what protects the
attribute context. This test runs the actual js/store.js functions (grabbed
verbatim from the source, same harness style as the broadcast-picker VM
test) against hostile names, image URLs and placeholder paths, and asserts
the rendered HTML carries no injectable attribute or script tag.
"""
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VM_SCRIPT = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";
const src = readFileSync("js/store.js", "utf8");
const grab = (name) => {
  for (const prefix of ["async function ", "function "]) {
    const start = src.indexOf(prefix + name + "(");
    if (start < 0) continue;
    let depth = 0, i = src.indexOf("{", start);
    for (; i < src.length; i++) {
      if (src[i] === "{") depth++;
      else if (src[i] === "}") { depth--; if (!depth) break; }
    }
    return src.slice(start, i + 1);
  }
  throw new Error(name + " not found");
};
// ---- stubs for the store.js internals the render path leans on ----------
const stubs = `
  const KEYS = { wish: "jaura_wish", cats: "jaura_categories" };
  const DEFAULT_CATEGORIES = [{ id: "bags", name: "Bags" }];
  const read = (k, fallback) => fallback;
  const HEART_SVG = "<svg></svg>";
  function tx(s) { return s; }
  function categoryName(id) { return id; }
  function priceHTML() { return "<span class='price'>PRICE</span>"; }
  function reviewStats() { return { n: 0, avg: 0 }; }
`;
const sandbox = {
  Number, String, Math, JSON, Array, Object, Set, Map, Promise, console,
  encodeURIComponent,
  document: { body: { dataset: { page: "shop" } } },
};
sandbox.window = {};
vm.createContext(sandbox);
const pieces = [stubs];
for (const fn of ["wish", "isWished", "escape", "asset", "mediaKind",
                  "thumbFor", "galleryOf", "displayName", "publicProductSlug",
                  "productUrl", "mediaHTML", "cardHTML"]) {
  pieces.push(grab(fn));
}
vm.runInContext(pieces.join("\n"), sandbox);
const run = (code) => vm.runInContext(code, sandbox);
const assert = (cond, msg) => { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } };

const hostile = JSON.parse(String.raw`{
  "id": "jau-xss-1",
  "name": "Evil <img src=x onerror=alert(1)> \" ' Purse & Bag",
  "image": "images/evil.jpg\" onmouseover=\"alert(2)",
  "images": ["images/a.jpg\" alt=\"hax", "data:text/html,<script>alert(3)</script>"],
  "category": "bags", "priceNgn": 5000, "stock": 3, "badge": "new"
}`);

// ---- the shop card -------------------------------------------------------
run("__hostile = " + JSON.stringify(hostile));
const card2 = run("cardHTML(__hostile)");

assert(!/<img src=x onerror/.test(card2), "the hostile name's tag reached the DOM raw");
assert(card2.includes("&lt;img src=x"), "the hostile name is escaped as text");
assert(!card2.includes('" onmouseover="'), "an attribute could be injected from the image URL");
assert(!/" alt="hax/.test(card2), "an attribute could be injected from images[]");
assert(!/<script>/i.test(card2), "a raw <script> tag reached the DOM");
assert(!/data:text\/html,<script>/i.test(card2), "a data: URL with markup got through raw");
assert(card2.includes("Purse &amp; Bag"), "the ampersand is escaped");
// and the card is still a WORKING card, not an over-escaped husk
assert(card2.includes('href="product.html?slug='), "the product link still renders with a clean public slug");
assert(!card2.includes("product.html?id=jau-xss-1"), "public product URLs do not expose internal IDs");
assert(card2.includes("Evil"), "the product name still renders");

// ---- mediaHTML directly (alt / data-ph / src attribute contexts) --------
const media = run(`mediaHTML('x" onerror="alert(9)', {
  alt: 'a" onmouseover="alert(8)', ph: 'p" onload="alert(7)' })`);
assert(!/" onerror="alert\(9\)/.test(media), "src attribute injection");
assert(!/" onmouseover="alert\(8\)/.test(media), "alt attribute injection");
assert(!/" onload="alert\(7\)/.test(media), "data-ph attribute injection");
assert(media.includes("&quot;"), "the quotes are escaped");

// ---- sanity: the id is URL-encoded on the link ---------------------------
assert(card2.includes("product.html?slug="), "the readable product slug is encoded in the link");
console.log("ALL ESCAPING CHECKS PASSED");
"""


def test_storefront_render_neutralises_hostile_product_data():
    """The real cardHTML/mediaHTML escape hostile names, URLs and paths."""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", VM_SCRIPT],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (
        f"escaping VM failed:\n{result.stdout}\n{result.stderr}")
    assert "ALL ESCAPING CHECKS PASSED" in result.stdout


def test_server_side_clean_keeps_quotes_so_client_escaping_matters():
    """Documented threat model: sec.clean strips tags but keeps quotes, so a
    name carrying a quote-injection payload IS storable and the client-side
    escape is the actual defense (see the VM test above)."""
    import sys
    sys.path.insert(0, ROOT)
    import security as sec
    cleaned = sec.clean('Purse " onmouseover="alert(1)')
    assert '"' in cleaned and "<" not in cleaned
