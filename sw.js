/* J Aura Store - offline cache.
   Pages are network-first so a visitor with a connection always sees the
   newest store; when the connection drops, the last copy is served instead of
   an error. Saving is handled separately by js/net.js (outbox + retry). */
const VERSION = "jaura-v124";
const CORE = [
  "./",
  "./index.html",
  "./shop.html",
  "./product.html",
  "./cart.html",
  "./checkout.html",
  "./css/style.css?v=124",
  "./js/products-data.js?v=124",
  "./js/i18n.js?v=124",
  "./js/net.js?v=124",
  "./js/store.js?v=124",
  "./js/app.js?v=124",
  "./images/brand/logo.jpg?v=124",
];
const MAX_ASSETS = 140;

const OFFLINE_HTML = `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>You are offline · J Aura Store</title>
<style>
body{margin:0;font:16px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#3a332c;background:#fdfaf5;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:32px}
.box{max-width:440px;text-align:center}
h1{font:400 30px/1.2 Georgia,serif;margin:0 0 12px}
p{color:#7a7066;margin:0 0 18px}
a{display:inline-block;background:#3a332c;color:#fff;text-decoration:none;padding:12px 22px;border-radius:999px}
</style></head><body><div class="box">
<h1>You are offline</h1>
<p>J Aura Store cannot reach the internet right now. Anything you already saved is safe — it will send itself the moment you are back online.</p>
<p><a href="./index.html">Try again</a></p>
</div></body></html>`;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION)
      .then((c) => c.addAll(CORE).catch(() => null))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function trim(cache) {
  cache.keys().then((keys) => {
    if (keys.length <= MAX_ASSETS) return;
    return Promise.all(keys.slice(0, keys.length - MAX_ASSETS).map((k) => cache.delete(k)));
  });
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(VERSION);
  const hit = await cache.match(request);
  const network = fetch(request).then((res) => {
    if (res && res.ok) cache.put(request, res.clone()).then(() => trim(cache));
    return res;
  }).catch(() => null);
  if (hit) return hit;
  const res = await network;
  if (res) return res;
  return new Response("", { status: 504, statusText: "offline" });
}

async function networkFirst(request, fallbackHTML) {
  const cache = await caches.open(VERSION);
  try {
    const res = await fetch(request);
    if (res && res.ok) cache.put(request, res.clone()).then(() => trim(cache));
    return res;
  } catch (e) {
    const hit = await cache.match(request, { ignoreSearch: true });
    if (hit) return hit;
    if (fallbackHTML) {
      return new Response(OFFLINE_HTML, { headers: { "Content-Type": "text/html; charset=utf-8" } });
    }
    return new Response("", { status: 504, statusText: "offline" });
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;                       // writes go through the outbox
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;        // never cache third parties

  if (req.mode === "navigate") {
    event.respondWith(networkFirst(req, true));
    return;
  }
  if (url.pathname.indexOf("/api/") === 0) {
    // the catalogue is worth keeping so the shop still opens with no signal
    if (url.pathname.indexOf("/api/catalog") === 0) {
      event.respondWith(networkFirst(req, false));
    }
    return;
  }
  event.respondWith(staleWhileRevalidate(req));
});
