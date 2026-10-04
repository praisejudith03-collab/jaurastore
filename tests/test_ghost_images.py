"""Ghost images: a deleted photo must stop showing on a customer's phone.

The report: the owner deleted (or replaced) a product photo and customers who
had already opened the shop kept seeing the old picture for days. Two caches
were responsible, and neither had any notion of age:

  * the service worker handed back ANY cached media copy, however old, with no
    attempt to ask the network whether the photo still exists;
  * the /uploads/<key> route answered with no Cache-Control at all (the local
    branch) or let a redirect be cached heuristically (the production branch),
    so the browser was free to keep the old bytes indefinitely.

The fix is bounded caching, not no caching: a copy younger than five minutes
is served instantly (that is what keeps a grid quick on a 4G phone), an older
one must be confirmed, and a copy the server no longer has is dropped.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
NODE = shutil.which("node")

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
  const calls = [];
  const hits = new Map((opts.hits || []).map(([u, r]) => [u, r]));
  const cache = {
    async match(req) { const k = typeof req === "string" ? req : req.url;
                       return hits.get(k) || null; },
    async put(req, res) { const k = typeof req === "string" ? req : req.url; hits.set(k, res); },
    async keys() { return [...hits.keys()].map((u) => ({ url: u })); },
    async delete(k) { hits.delete(typeof k === "string" ? k : k.url); },
    async addAll() {},
  };
  const sandbox = {
    self: { location: { origin: ORIGIN, href: ORIGIN + "/sw.js" },
      addEventListener: (type, fn) => { handlers[type] = fn; },
      skipWaiting: () => Promise.resolve(),
      clients: { claim: () => Promise.resolve() } },
    caches: { async open() { return cache; }, async keys() { return []; }, async delete() {} },
    console, URL, Response, Request, Headers, Promise, Map, Set, JSON, Error,
    setTimeout, clearTimeout, Date,
    fetch: (req, init) => { calls.push(typeof req === "string" ? req : (req && req.url) || "");
      return (opts.fetch || (() => Promise.reject(new Error("offline"))))(req, init); },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(src, ctx, { filename: "sw.js" });
  return { handlers, cache, hits, calls };
};

global.loadSW = global.loadSW;
global.imageResponse = (body, ageSeconds) => new Response(body, { status: 200,
  headers: { "Content-Type": "image/jpeg",
             "Date": new Date(Date.now() - (ageSeconds || 0) * 1000).toUTCString() } });

global.fetchEvent = function fetchEvent(url, init) {
  const request = Object.assign({ url, method: "GET", mode: "no-cors", destination: "image" }, init || {});
  const ev = { request, responded: false,
               respondWith(p) { ev.responded = true; ev.value = p; }, waitUntil() {} };
  return ev;
};
"""


def _node(script, timeout=180):
    assert NODE, "node is required by these tests but is not on PATH"
    proc = subprocess.run([NODE, "-"], input=script, cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout)
    assert proc.returncode == 0, (
        f"node harness exited {proc.returncode}\n--- stderr ---\n{proc.stderr[-3000:]}\n"
        f"--- stdout ---\n{proc.stdout[-1500:]}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("__RESULT__")]
    assert lines, proc.stdout[-1500:]
    return json.loads(lines[-1][len("__RESULT__"):])


# --------------------------------------------------------------- the worker
def test_a_fresh_media_copy_is_served_without_hitting_the_network():
    out = _node(_SW_PRELUDE + r"""
(async () => {
  const url = "https://jaurastore.com.ng/uploads/products/2026/10/a.jpg";
  const sw = loadSW({ hits: [[url, imageResponse("fresh", 30)]],
                      fetch: () => Promise.resolve(imageResponse("network", 0)) });
  const ev = fetchEvent(url);
  await sw.handlers.fetch(ev);
  emit({ responded: ev.responded, body: ev.responded ? await (await ev.value).text() : null,
         fetches: sw.calls.length });
})();
""")
    assert out["body"] == "fresh"
    assert out["fetches"] == 0, "a five-minute-old copy must not cost a request"


def test_a_stale_media_copy_is_confirmed_before_it_is_served():
    out = _node(_SW_PRELUDE + r"""
(async () => {
  const url = "https://jaurastore.com.ng/uploads/products/2026/10/a.jpg";
  const sw = loadSW({ hits: [[url, imageResponse("old", 3600)]],
                      fetch: () => Promise.resolve(imageResponse("new", 0)) });
  const ev = fetchEvent(url);
  await sw.handlers.fetch(ev);
  emit({ responded: ev.responded,
         body: ev.responded ? await (await ev.value).text() : null,
         fetches: sw.calls.length });
})();
""")
    assert out["fetches"] == 1, "an hour-old copy must be checked before it is served"
    assert out["body"] == "new", "the customer must see the picture the shop has now"


def test_a_deleted_photo_is_dropped_instead_of_ghosting():
    """The exact complaint: the network says 404, the cached copy disappeared."""
    out = _node(_SW_PRELUDE + r"""
(async () => {
  const url = "https://jaurastore.com.ng/uploads/products/2026/10/gone.jpg";
  const sw = loadSW({ hits: [[url, imageResponse("ghost", 86400)]],
                      fetch: () => Promise.resolve(new Response("", { status: 404 })) });
  const ev = fetchEvent(url);
  await sw.handlers.fetch(ev);
  emit({ responded: ev.responded,
         body: ev.responded ? await (await ev.value).text() : null,
         stillCached: sw.hits.has(url) });
})();
""")
    assert out["stillCached"] is False, "the dead copy must be evicted from the cache"
    if out["responded"]:
        assert out["body"] != "ghost", "a deleted photo must never be served again"


def test_an_offline_phone_still_gets_the_last_copy_it_had():
    out = _node(_SW_PRELUDE + r"""
(async () => {
  const url = "https://jaurastore.com.ng/uploads/products/2026/10/a.jpg";
  const sw = loadSW({ hits: [[url, imageResponse("offline-copy", 86400)]],
                      fetch: () => Promise.reject(new Error("no signal")) });
  const ev = fetchEvent(url);
  await sw.handlers.fetch(ev);
  emit({ responded: ev.responded,
         body: ev.responded ? await (await ev.value).text() : null });
})();
""")
    assert out["body"] == "offline-copy", "a stale photo beats a hole with no signal"


def test_a_media_request_with_no_cached_copy_still_falls_through():
    out = _node(_SW_PRELUDE + r"""
(async () => {
  const sw = loadSW({ fetch: () => Promise.reject(new Error("offline")) });
  const ev = fetchEvent("https://jaurastore.com.ng/uploads/products/2026/10/new.jpg");
  await sw.handlers.fetch(ev);
  emit({ responded: ev.responded });
})();
""")
    assert out["responded"] is False, \
        "the browser's own fetch (and its retry) must handle a cache miss"


# ------------------------------------------------------------- the route
def test_the_uploads_route_bounds_the_photo_cache_and_never_caches_proofs():
    """A deleted photo must stop showing; a browsing session must stay quick."""
    import app as app_mod

    client = app_mod.app.test_client()
    resp = client.get("/uploads/products/2026/10/missing.jpg")
    assert resp.status_code == 404          # nothing stored: still a clean 404

    # The header policy is asserted from the route source as well, because the
    # testing branch only runs when the file exists on disk.
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    route = src[src.index('@app.route("/uploads/<path:p>")'):]
    route = route[:route.index("@app.route", 10)]
    assert "public, max-age=300, must-revalidate" in route, \
        "photos need a SHORT, revalidating cache - a year-long 'immutable' is " \
        "exactly how a deleted photo kept showing"
    # BOTH branches (the local file and the production redirect) carry the
    # pair, so a proof is never cached and a photo is never pinned forever.
    assert route.count('"no-store, no-cache, must-revalidate" if is_proof') == 2, \
        "payment proofs are private evidence and must never be cached"
    assert route.count('else "public, max-age=300, must-revalidate"') == 2, \
        "the redirect branch needs the bounded header too"
    assert route.count("Cache-Control") >= 2, \
        "both the file branch and the production redirect need the header"
    assert "is_proof" in route and "current_admin" in route, \
        "the proof restriction must survive the cache change"


def test_a_payment_proof_is_still_admin_only(tmp_path, monkeypatch):
    """The cache work must not have loosened who may read a receipt."""
    import app as app_mod
    import storage
    monkeypatch.setenv("FLASK_ENV", "testing")
    root = tmp_path / "uploads"
    (root / "proofs").mkdir(parents=True)
    (root / "proofs" / "receipt.jpg").write_bytes(b"\xff\xd8\xff\xdbprivate")

    monkeypatch.setattr(storage, "local_root", lambda: str(root), raising=False)
    monkeypatch.setattr(storage.Config, "UPLOAD_DIR", str(root), raising=False)
    client = app_mod.app.test_client()
    assert client.get("/uploads/proofs/receipt.jpg").status_code == 404, \
        "a payment receipt must never be public"


def test_a_real_upload_still_serves_with_the_bounded_header(tmp_path, monkeypatch):
    import app as app_mod
    import storage
    monkeypatch.setenv("FLASK_ENV", "testing")

    root = tmp_path / "uploads"
    (root / "products").mkdir(parents=True)
    f = root / "products" / "x.jpg"
    f.write_bytes(b"\xff\xd8\xff\xdbjpeg-bytes")
    monkeypatch.setattr(storage, "local_root", lambda: str(root), raising=False)
    monkeypatch.setattr(storage.Config, "UPLOAD_DIR", str(root), raising=False)

    client = app_mod.app.test_client()
    resp = client.get("/uploads/products/x.jpg")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "public, max-age=300, must-revalidate"


def test_the_service_worker_no_longer_serves_any_cached_media_copy_forever():
    src = open(os.path.join(ROOT, "sw.js"), encoding="utf-8").read()
    assert "MEDIA_MAX_AGE" in src and "freshEnough" in src
    body = src[src.index("async function cachedMedia"):]
    body = body[:body.index("\n}") + 2]
    assert "fetch(request)" in body, \
        "a stale copy must be revalidated against the network"
    assert "cache.delete(request)" in body, \
        "a copy the server no longer has must be evicted, not served"
    assert "return hit" in body, "offline must still fall back to the last copy"


def test_no_upload_path_hands_out_a_year_long_cache_any_more():
    """S3 used to pin every object for 31536000s, proofs included."""
    src = open(os.path.join(ROOT, "storage.py"), encoding="utf-8").read()
    assert "max-age=31536000" not in src, \
        "an uploaded photo must not be pinned for a year - that is the ghost"
    assert "PUBLIC_MEDIA_CACHE_CONTROL" in src
    # the S3 write only sends the header when it has a bounded policy
    assert 'extra["CacheControl"] = cache_control' in src


def test_the_stored_photo_policy_and_the_route_agree():
    import storage
    assert storage.PUBLIC_MEDIA_CACHE_CONTROL == "public, max-age=300, must-revalidate"
    app_src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert storage.PUBLIC_MEDIA_CACHE_CONTROL in app_src
