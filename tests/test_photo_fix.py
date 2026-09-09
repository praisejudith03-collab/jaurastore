"""Uploaded product photos must actually show up in the shop.

The incident: a photo the owner saved from her phone lives in the Supabase
bucket and reaches the browser as a same-origin link, /uploads/<key>. Three
pieces of the storefront then worked against it:

* ``thumbFor`` (js/store.js) invents a "<path>.400w.webp" <source> for every
  .jpg/.png/.webp src. Those companions only exist for committed repo assets
  (images/products/x.jpg -> images/products/x.400w.webp), so for an uploaded
  photo the <picture> selects a URL that 404s - and a <picture> does not fall
  back to its inner <img> once the chosen <source> fails. Permanent broken
  icon, even though the photo itself is fine.
* sw.js pipes every same-origin GET through staleWhileRevalidate, which
  respondWiths a synthetic empty 504 whenever fetch rejects. That kills the
  browser's own retry, so a photo that would have loaded a second later stays
  broken; the same path also hands back opaque cross-origin-redirect bodies
  for /uploads (the server 302s those to the bucket).
* the PDP's paintProduct catch branch repainted a stripped single-<img> block
  with no arrows and no thumbnails, and fallbackImg swapped the <img> src
  while a sibling <source> kept winning, so even the branded placeholder
  never appeared.

The tests below run the real storefront JS in node (no browser download):
js/store.js and js/app.js execute inside a vm context with a stub DOM, and
sw.js runs against a stub CacheStorage, so the shipped code paths are the
ones under test. The last two tests pin the guard the owner asked for:
replacing a product photo or pressing x must NEVER delete a bucket object -
only the order/receipt delete paths may, and they still do.

Run with:  python3 -m pytest tests/test_photo_fix.py -q
"""
import json
import os
import re
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

# ---------------------------------------------------------------------------
# Browser-free harness: a stub DOM/window/localStorage, then the real scripts.
# ---------------------------------------------------------------------------
_PRELUDE = r"""
const fs = require("fs");
const vm = require("vm");
const ROOT = process.cwd();

function mkEl(tag) {
  const cls = new Set();
  const el = {
    tagName: String(tag || "div").toUpperCase(),
    dataset: {}, children: [], style: {}, textContent: "", innerHTML: "", src: "",
    classList: {
      add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c),
      toggle: (c, on) => { if (on === undefined) { cls.has(c) ? cls.delete(c) : cls.add(c); } else if (on) cls.add(c); else cls.delete(c); },
    },
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    querySelector: () => null, querySelectorAll: () => [],
    setAttribute(k, v) { el["@" + k] = String(v); },
    getAttribute: (k) => (Object.prototype.hasOwnProperty.call(el, "@" + k) ? el["@" + k] : null),
    removeAttribute(k) { delete el["@" + k]; },
    appendChild(c) { el.children.push(c); return c; }, removeChild() {},
    closest: () => null, focus() {}, click() {}, scrollIntoView() {}, replaceWith() {},
  };
  return el;
}

const body = mkEl("body");
if (global.PAGE) body.dataset.page = global.PAGE;
const store = new Map();
const sandbox = {
  document: {
    body, documentElement: mkEl("html"), head: mkEl("head"), title: "", readyState: "complete",
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    querySelector: () => null, querySelectorAll: () => [],
    createElement: mkEl, createTextNode: (t) => ({ text: t }),
    createDocumentFragment: () => mkEl("fragment"),
  },
  localStorage: {
    getItem: (k) => (store.has(String(k)) ? store.get(String(k)) : null),
    setItem: (k, v) => store.set(String(k), String(v)),
    removeItem: (k) => store.delete(String(k)),
    clear: () => store.clear(),
  },
  console, URL, URLSearchParams, Response, Request, Headers, TextEncoder, TextDecoder,
  CustomEvent: class CustomEvent { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
  fetch: () => Promise.reject(new Error("offline")),
  navigator: { onLine: true, sendBeacon: () => true, userAgent: "node-harness" },
  location: { href: "https://jaurastore.com.ng/shop.html", origin: "https://jaurastore.com.ng", pathname: "/shop.html", search: "" },
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (cb) => setTimeout(() => cb(Date.now()), 0),
  addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
  matchMedia: () => ({ matches: false, addListener() {}, removeListener() {} }),
  scrollTo() {}, scrollY: 0,
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.globalThis = sandbox;
const ctx = vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(ROOT + "/js/store.js", "utf8") + "\n;globalThis.__JA = JA;", ctx, { filename: "js/store.js" });

global.emit = (o) => console.log("__RESULT__" + JSON.stringify(o));
global.sandbox = sandbox;
global.el = mkEl;
global.JA = sandbox.__JA;
global.runInCtx = (src, name) => vm.runInContext(src, ctx, { filename: name });
global.loadApp = () => runInCtx(fs.readFileSync(ROOT + "/js/app.js", "utf8"), "js/app.js");
"""

_SW_PRELUDE = r"""
const fs = require("fs");
const vm = require("vm");
const ROOT = process.cwd();
const ORIGIN = "https://jaurastore.com.ng";

global.emit = (o) => console.log("__RESULT__" + JSON.stringify(o));

global.loadSW = function loadSW(opts) {
  opts = opts || {};
  const src = fs.readFileSync(ROOT + "/sw.js", "utf8");
  const handlers = {};
  const put = [];
  const hits = new Map((opts.hits || []).map(([u, r]) => [u, r]));
  const cache = {
    async match(req) { return hits.get(typeof req === "string" ? req : req.url); },
    async put(req, res) { const k = typeof req === "string" ? req : req.url; hits.set(k, res); put.push(k); },
    async keys() { return [...hits.keys()].map((u) => ({ url: u })); },
    async delete(k) { hits.delete(typeof k === "string" ? k : k.url); },
    async addAll() {},
  };
  const sandbox = {
    self: {
      location: { origin: ORIGIN, href: ORIGIN + "/sw.js" },
      addEventListener: (type, fn) => { handlers[type] = fn; },
      skipWaiting: () => Promise.resolve(),
      clients: { claim: () => Promise.resolve() },
    },
    caches: { async open() { return cache; }, async keys() { return []; }, async delete() {} },
    console, URL, Response, Request, Headers, Promise, Map, Set, JSON, Error, setTimeout, clearTimeout,
    fetch: opts.fetch || (() => Promise.reject(new Error("offline"))),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(src, ctx, { filename: "sw.js" });
  return { handlers, cache, hits, put };
};

global.fetchEvent = function fetchEvent(url, init) {
  const request = Object.assign({ url, method: "GET", mode: "no-cors", destination: "" }, init || {});
  const ev = { request, responded: false, respondWith(p) { ev.responded = true; ev.value = p; }, waitUntil() {} };
  return ev;
};
"""


def _node(script, timeout=180):
    """Run the harness + a test body in node and return what it emitted."""
    if not NODE:
        raise AssertionError(
            "node is required by these tests (jsdom-free vm harness) but is not "
            "on PATH; install node 18+ rather than skipping the check"
        )
    proc = subprocess.run([NODE, "-"], input=script, cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(
            f"node harness exited {proc.returncode}\n"
            f"--- stderr ---\n{proc.stderr[-4000:]}\n--- stdout ---\n{proc.stdout[-2000:]}"
        )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("__RESULT__")]
    assert lines, f"harness emitted no result\nstdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
    return json.loads(lines[-1][len("__RESULT__"):])


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1 + 2 - thumbFor must not invent a .400w.webp <source> for uploaded photos
# ---------------------------------------------------------------------------
def test_uploads_and_absolute_urls_get_no_fabricated_400w_source():
    """/uploads/ and absolute URLs render as a plain <img>; repo paths keep theirs."""
    out = _node(_PRELUDE + r"""
const upload = "/uploads/products/2026/09/abc.jpg";
const cdn = "https://static.wixstatic.com/media/x.jpg";
const http = "http://example.com/y.png";
const repo = "images/products/x.jpg";
emit({
  upload: JA.mediaHTML(upload, { alt: "a" }),
  cdn: JA.mediaHTML(cdn, { alt: "b" }),
  http: JA.mediaHTML(http, { alt: "c" }),
  repo: JA.mediaHTML(repo, { alt: "d" }),
});
""")
    for key, url in (("upload", "/uploads/products/2026/09/abc.jpg"),
                     ("cdn", "https://static.wixstatic.com/media/x.jpg"),
                     ("http", "http://example.com/y.png")):
        html = out[key]
        assert "<picture" not in html, f"{url} must not be wrapped in a <picture>: {html}"
        assert "<source" not in html, f"{url} must not get a fabricated <source>: {html}"
        assert ".400w.webp" not in html, f"{url} has no .400w.webp companion: {html}"
        assert f'src="{url}"' in html, f"{url} must be the img src: {html}"
    # a committed repo asset still gets its hand-made webp companion
    assert '<source srcset="images/products/x.400w.webp"' in out["repo"], \
        f"repo-relative images keep their .400w.webp companion: {out['repo']}"


def test_product_gallery_of_uploaded_photos_renders_plain_imgs():
    """A product whose three photos are uploads shows all three, no broken sources."""
    out = _node(_PRELUDE + r"""
sandbox.document.body.dataset.page = "home";           // cardHTML plays slides on home
const p = {
  id: "jau-mtot3318", name: "Alarm clock", category: "home",
  priceNgn: 7500, priceCfa: 4500, stock: 3,
  image: "/uploads/products/2026/09/abc.jpg",
  images: ["/uploads/products/2026/09/b.jpg", "/uploads/products/2026/09/c.jpg"],
};
emit({ html: JA.cardHTML(p), gallery: JA.galleryOf(p) });
""")
    html = out["html"]
    assert len(out["gallery"]) == 3, f"all three photos belong in the gallery: {out['gallery']}"
    assert html.count("<img") == 3, f"three photos must render as three <img>: {html}"
    assert "<picture" not in html, f"uploaded photos must not be wrapped: {html}"
    assert "400w" not in html, f"no fabricated .400w.webp source: {html}"
    for src in ("/uploads/products/2026/09/abc.jpg",
                "/uploads/products/2026/09/b.jpg",
                "/uploads/products/2026/09/c.jpg"):
        assert f'src="{src}"' in html, f"{src} missing from the card: {html}"


# ---------------------------------------------------------------------------
# 3 + 4 - sw.js must never stand between a photo and the network
# ---------------------------------------------------------------------------
def test_service_worker_media_miss_falls_through_instead_of_504():
    """No cached copy + a failed fetch = let the browser retry (no respondWith)."""
    out = _node(_SW_PRELUDE + r"""
(async () => {
  const { handlers } = loadSW({});                        // empty cache, fetch always rejects
  const cases = [
    ["image destination", "https://jaurastore.com.ng/uploads/products/2026/09/abc.jpg", { destination: "image" }],
    ["uploads path", "https://jaurastore.com.ng/uploads/products/2026/09/abc.jpg", { destination: "" }],
    ["repo image", "https://jaurastore.com.ng/images/products/x.jpg", { destination: "image" }],
    ["video", "https://jaurastore.com.ng/uploads/products/2026/09/clip.mp4", { destination: "video" }],
  ];
  const res = {};
  for (const [name, url, init] of cases) {
    const ev = fetchEvent(url, init);
    await handlers.fetch(ev);
    let status = null;
    if (ev.responded) status = (await ev.value).status;
    res[name] = { responded: ev.responded, status };
  }
  // navigations and the catalogue keep their own strategies
  const nav = fetchEvent("https://jaurastore.com.ng/shop.html", { mode: "navigate" });
  await handlers.fetch(nav);
  const api = fetchEvent("https://jaurastore.com.ng/api/catalog", {});
  await handlers.fetch(api);
  emit({ media: res, nav: { responded: nav.responded, status: nav.responded ? (await nav.value).status : null },
         api: { responded: api.responded } });
})();
""")
    for name, got in out["media"].items():
        assert got["responded"] is False, \
            f"sw.js must NOT respondWith for {name} when it has no cached copy " \
            f"(it answered status {got['status']} instead of falling through to the browser)"
        assert got["status"] != 504, f"{name}: a synthetic 504 kills the photo"
    assert out["nav"]["responded"] is True, "navigations stay network-first"
    assert out["api"]["responded"] is True, "/api/catalog stays cached"


def test_service_worker_serves_cached_media_and_never_caches_a_broken_copy():
    """A cached photo is served; a media response is never cached by the worker."""
    out = _node(_SW_PRELUDE + r"""
(async () => {
  const url = "https://jaurastore.com.ng/uploads/products/2026/09/abc.jpg";
  const cached = new Response("cached-bytes", { status: 200, headers: { "Content-Type": "image/jpeg" } });
  const hit = loadSW({ hits: [[url, cached]] });
  const evHit = fetchEvent(url, { destination: "image" });
  await hit.handlers.fetch(evHit);
  const body = evHit.responded ? await (await evHit.value).text() : null;

  // a media request the worker does not own must not be written to the cache,
  // and a non-ok response must never be cached either
  const fresh = loadSW({ fetch: () => Promise.resolve(new Response("ok", { status: 200 })) });
  const evFresh = fetchEvent(url, { destination: "image" });
  await fresh.handlers.fetch(evFresh);
  const bad = loadSW({ fetch: () => Promise.resolve(new Response("nope", { status: 500 })) });
  const evBad = fetchEvent("https://jaurastore.com.ng/images/products/x.jpg", { destination: "image" });
  await bad.handlers.fetch(evBad);
  emit({
    hitResponded: evHit.responded, body,
    freshResponded: evFresh.responded, freshPut: fresh.put,
    badPut: bad.put,
  });
})();
""")
    assert out["hitResponded"] is True, "a cached photo must still be served from the cache"
    assert out["body"] == "cached-bytes", f"cached copy served unchanged: {out['body']!r}"
    assert out["freshResponded"] is False, "an uncached photo falls through to the browser"
    assert out["freshPut"] == [], f"the worker must not cache media it does not own: {out['freshPut']}"
    assert out["badPut"] == [], f"a non-ok response must never be cached: {out['badPut']}"


# ---------------------------------------------------------------------------
# 5 - the PDP catch branch keeps the whole gallery
# ---------------------------------------------------------------------------
def test_pdp_error_fallback_keeps_the_full_gallery_with_thumbs():
    """If paintProduct throws, the shop still shows every photo with thumbs + arrows."""
    out = _node(_PRELUDE + r"""
loadApp();
const p = {
  id: "jau-mtot3318", name: "Alarm clock", category: "home",
  priceNgn: 7500, priceCfa: 4500, stock: 3,
  image: "/uploads/products/2026/09/abc.jpg",
  images: ["/uploads/products/2026/09/b.jpg", "/uploads/products/2026/09/c.jpg"],
  placeholderImage: "images/products/_placeholder.jpg",
};
JA.product = () => p;
sandbox.location.search = "?id=jau-mtot3318";
sandbox.paintProduct = () => { throw new Error("boom from paintProduct"); };
const root = el("div");
sandbox.document.querySelector = (sel) => (sel === "[data-pdp]" ? root : null);
const errors = [];
const realError = console.error;
console.error = (...a) => errors.push(a.map(String).join(" "));
sandbox.renderProduct();
console.error = realError;
emit({ html: root.innerHTML, errors });
""")
    html = out["html"]
    assert "pdp-thumbs" in html, f"the fallback must keep the thumbnail strip: {html}"
    assert html.count('class="pdp-thumb') >= 3, f"one thumbnail per photo: {html}"
    assert "pdp-prev" in html and "pdp-next" in html, f"the arrows must survive: {html}"
    for src in ("/uploads/products/2026/09/abc.jpg",
                "/uploads/products/2026/09/b.jpg",
                "/uploads/products/2026/09/c.jpg"):
        assert src in html, f"{src} missing from the fallback gallery: {html}"
    assert "400w" not in html, f"no fabricated .400w.webp source: {html}"
    joined = " ".join(out["errors"])
    assert "jau-mtot3318" in joined, f"console.error must name the product: {joined!r}"
    assert "boom from paintProduct" in joined, f"console.error must keep the original throw: {joined!r}"


# ---------------------------------------------------------------------------
# 6 - fallbackImg: a sibling <source> must not outvote the placeholder
# ---------------------------------------------------------------------------
def test_fallback_img_drops_the_picture_source_before_swapping_placeholder():
    src = _read(os.path.join("js", "store.js"))
    assert "function fallbackImg" in src, "fallbackImg still exists in js/store.js"
    out = _node(_PRELUDE + r"""
const source = el("source");
source.setAttribute("srcset", "images/products/x.400w.webp");
let sourceRemoved = false;
source.remove = () => { sourceRemoved = true; };
const img = el("img");
img.setAttribute("src", "images/products/x.jpg");
img.setAttribute("data-ph", "images/products/_placeholder.jpg");
const picture = el("picture");
picture.children = [source, img];
picture.querySelectorAll = (sel) => (sel === "source" ? [source] : []);
const wrapper = el("div");
wrapper.children = [picture];
let unwrapped = false;
wrapper.replaceChild = (node, old) => { wrapper.children = [node]; unwrapped = (old === picture); };
source.parentNode = picture;
img.parentNode = picture;
picture.parentNode = wrapper;
sandbox.fallbackImg({ currentTarget: img });
emit({
  src: img.src,
  placeholderClass: img.classList.contains("is-placeholder"),
  sourceRemoved,
  srcset: source.getAttribute("srcset"),
  unwrapped,
  parentNodeIsImg: wrapper.children[0] === img,
});
""")
    assert out["src"] == "images/products/_placeholder.jpg", \
        f"the placeholder must become the img src: {out['src']!r}"
    assert out["placeholderClass"] is True, "the placeholder styling is applied"
    cleared = out["sourceRemoved"] or not out["srcset"]
    unwrapped = out["unwrapped"] and out["parentNodeIsImg"]
    assert cleared or unwrapped, \
        "the sibling <source> (or the whole <picture>) must be dropped first, " \
        f"otherwise it keeps winning: sourceRemoved={out['sourceRemoved']} " \
        f"srcset={out['srcset']!r} unwrapped={unwrapped}"


# ---------------------------------------------------------------------------
# 7 - the admin editor's media row tells the truth and points at the tiles
# ---------------------------------------------------------------------------
def test_admin_media_row_says_how_many_photos_and_points_at_the_tiles():
    admin = _read(os.path.join("js", "admin.js"))
    css = _read(os.path.join("css", "style.css"))
    assert "View All Media (" not in admin, \
        "the dead-end 'View All Media (N/20) >' button is gone"
    m = re.search(r"Photos: \$\{[^}]*\} of 20", admin)
    assert m, "the button must say how many of the 20 slots are used"
    assert re.search(r"#view-media[^\n]*\n", admin), "the #view-media button is still there"
    block = admin.split('e.target.closest("#view-media")', 1)
    assert len(block) == 2, "the #view-media click handler still exists"
    handler = block[1][:900]
    assert "scrollIntoView" in handler, "tapping it must scroll the media row into view"
    assert "is-flash" in handler, "tapping it must flash the media row"
    assert re.search(r"\.au-media-row\.is-flash", css), \
        "css/style.css must style the flash (.au-media-row.is-flash)"


# ---------------------------------------------------------------------------
# 8 + 9 - unlink-only: bucket objects die with a receipt, never with a photo
# ---------------------------------------------------------------------------
def _function_body(source, name):
    m = re.search(r"^def " + re.escape(name) + r"\(.*?(?=^def |^@|\Z)",
                  source, re.S | re.M)
    assert m, f"{name} not found"
    return m.group(0)


def test_replacing_or_removing_a_product_photo_never_deletes_bucket_objects():
    """Swapping a product photo, or pressing x, unlinks it - the file stays put."""
    api = _read("api.py")
    for fn in ("admin_product_upsert", "admin_product_delete", "admin_products_replace"):
        body = _function_body(api, fn)
        assert "delete_upload" not in body, \
            f"{fn} must never delete a bucket object - a replaced photo is only unlinked"
    assert "delete_upload" not in _read("catalog.py"), \
        "catalog.upsert/remove/replace_all must never delete a bucket object"
    admin = _read(os.path.join("js", "admin.js"))
    block = admin.split('e.target.closest("[data-del-img]")', 1)
    assert len(block) == 2, "the x (data-del-img) handler still exists in js/admin.js"
    handler = block[1][:600]
    assert "__editImages.splice" in handler, "x removes the photo from the editor's list"
    assert "api(" not in handler and "DELETE" not in handler, \
        f"x must not call a delete endpoint: {handler!r}"


def test_receipt_deletion_still_removes_the_bucket_object():
    """Deleting a receipt (or its order) still deletes the file - and a
    receipt whose Supabase RECORD failed to save removes its orphan object."""
    api = _read("api.py")
    assert api.count("storage.delete_upload(") == 4, \
        "four delete_upload call sites: orphan cleanup, production/local receipt deletes and the order"
    assert "delete_upload" in _function_body(api, "payment_proof"), \
        "a failed strict receipt write removes the orphan storage object"
    assert "delete_upload" in _function_body(api, "admin_payment_proof_delete"), \
        "deleting a payment proof still removes its uploaded file"
    assert "delete_upload" in _function_body(api, "admin_order_delete"), \
        "deleting an order still removes its receipts' files"
    storage = _read("storage.py")
    assert "def delete_upload(" in storage, "storage.delete_upload is still the deleter"
