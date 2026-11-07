/* J Aura Store — Meta pixel + Conversions API bridge.

   WHY THIS FILE EXISTS
   --------------------
   An ad blocker, an in-app browser (Instagram / Facebook / TikTok webviews) or
   a dropped 3G connection can delete a browser event before Meta ever sees it,
   and with it the attribution for a December campaign. So every standard
   event is reported TWICE:

     1. the pixel, with `fbq('track', name, params, {eventID: id})`
     2. the SAME event posted to /api/meta/event with the same `eventId`,
        which the server forwards to the Conversions API from its own IP,
        with the same hashed customer details.

   Meta collapses the two copies by `event_id`. That deduplication is the whole
   point: one add-to-cart, one conversion — counted once, whichever copy
   arrives first.

   DEDUPE IDS
   ----------
   * browsing events use a freshly minted id (shared by both copies);
   * Purchase uses `ja-purchase-<ORDER ID>`, which the SERVER also derives
     (meta_capi.purchase_event_id). Both sides compute the same string without
     talking to each other, so a purchase stays deduplicated even when the
     checkout response never made it back to the phone.

   Nothing here is required for the shop to work: with no pixel id configured
   the module is inert, and if JA_NET is missing every call is a no-op. */
window.JA_META = (function () {
  var pixelId = "";
  var ready = null;          // promise: pixel bootstrap finished (or skipped)
  var fired = {};            // per-page guard for ViewContent etc.
  var PURCHASE_PREFIX = "ja-purchase-";
  var SESSION_KEY = "jaura_meta_purchase_fired";

  function isAdminPage() {
    try { return (document.body && document.body.dataset.page) === "admin"; }
    catch (e) { return false; }
  }

  function newEventId(prefix) {
    return "ja-" + (prefix || "px") + "-" + Date.now().toString(36) + "-"
      + Math.random().toString(36).slice(2, 10);
  }

  /* The pixel base code. Loaded lazily (after the config answer) so a shop
     with no pixel configured never pays for a third-party script. */
  function boot() {
    if (ready) return ready;
    ready = new Promise(function (resolve) {
      if (!window.JA_NET || typeof window.JA_NET.config !== "function" || isAdminPage()) {
        return resolve("");
      }
      window.JA_NET.config().then(function (cfg) {
        pixelId = String((cfg && cfg.metaPixelId) || "").trim();
        if (!pixelId) return resolve("");
        /* eslint-disable */
        !function (f, b, e, v, n, t, s) {
          if (f.fbq) return; n = f.fbq = function () {
            n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments);
          };
          if (!f._fbq) f._fbq = n; n.push = n; n.loaded = !0; n.version = "2.0";
          n.queue = []; t = b.createElement(e); t.async = !0;
          t.src = v; s = b.getElementsByTagName(e)[0];
          s.parentNode.insertBefore(t, s);
        }(window, document, "script", "https://connect.facebook.net/en_US/fbevents.js");
        /* eslint-enable */
        try {
          window.fbq("init", pixelId);
          window.fbq("track", "PageView");
        } catch (e) { /* pixel blocked: the server copy still goes out */ }
        resolve(pixelId);
      }).catch(function () { resolve(""); });
    });
    return ready;
  }

  /* Fire the pixel half. Never throws: a blocked pixel must not stop the
     server half from being sent. */
  function pixel(name, params, eventId) {
    try {
      if (!window.fbq) return false;
      var opts = eventId ? { eventID: eventId } : undefined;
      if (opts) window.fbq("track", name, params || {}, opts);
      else window.fbq("track", name, params || {});
      return true;
    } catch (e) { return false; }
  }

  /* Post the server half. `queue: true` means a send that fails on a flaky
     mobile connection is retried from the durable outbox with the SAME event
     id, which keeps the pixel/CAPI pair deduplicated. */
  function send(name, params, eventId, extra) {
    if (!window.JA_NET || typeof window.JA_NET.api !== "function") return;
    var body = {
      name: name,
      eventId: eventId,
      eventSourceUrl: location.href,
      params: params || {},
    };
    if (extra && extra.email) body.email = extra.email;
    if (extra && extra.phone) body.phone = extra.phone;
    try {
      window.JA_NET.api("api/meta/event", {
        method: "POST",
        json: body,
        timeout: 8000,
        queue: true,
        keepalive: true,
        label: "Tracking",
      }).catch(function () { /* analytics must never surface an error */ });
    } catch (e) { /* ignore */ }
  }

  /* One standard event, both copies, one id. */
  function track(name, params, extra) {
    if (isAdminPage()) return "";
    var eventId = (extra && extra.eventId) || newEventId("px");
    boot();
    pixel(name, params, eventId);
    send(name, params, eventId, extra);
    return eventId;
  }

  function contents(productId, price, qty) {
    return [{
      id: String(productId || ""),
      quantity: Math.max(1, Math.round(Number(qty) || 1)),
      item_price: Number(price) || 0,
    }];
  }

  function viewContent(product, opts) {
    var o = opts || {};
    var id = String((product && (product.id || product.productId)) || "");
    if (!id) return "";
    if (fired["view:" + id]) return fired["view:" + id];
    var price = Number(o.price) || 0;
    var payload = {
      content_type: "product",
      content_ids: [id],
      contents: contents(id, price, 1),
      content_name: String((product && product.name) || o.name || ""),
      value: price,
      currency: o.currency || "NGN",
    };
    var eventId = track("ViewContent", payload, o);
    fired["view:" + id] = eventId;
    return eventId;
  }

  function addToCart(product, opts) {
    var o = opts || {};
    var id = String((product && (product.id || product.productId)) || o.id || "");
    if (!id) return "";
    var qty = Math.max(1, Math.round(Number(o.qty) || 1));
    var price = Number(o.price) || 0;
    var payload = {
      content_type: "product",
      content_ids: [id],
      contents: contents(id, price, qty),
      content_name: String((product && product.name) || o.name || ""),
      value: Math.round(price * qty * 100) / 100,
      currency: o.currency || "NGN",
      num_items: qty,
    };
    return track("AddToCart", payload, o);
  }

  function initiateCheckout(cart, opts) {
    var o = opts || {};
    var lines = Array.isArray(cart) ? cart : [];
    var payloadContents = [];
    var total = 0;
    var num = 0;
    lines.slice(0, 12).forEach(function (line) {
      var id = String((line && (line.id || line.productId)) || "");
      if (!id) return;
      var qty = Math.max(1, Math.round(Number(line.qty) || 1));
      var price = Number(o.priceOf ? o.priceOf(line) : (line.price || 0)) || 0;
      payloadContents.push({ id: id, quantity: qty, item_price: price });
      total += price * qty;
      num += qty;
    });
    if (!payloadContents.length) return "";
    return track("InitiateCheckout", {
      content_type: "product",
      content_ids: payloadContents.map(function (c) { return c.id; }),
      contents: payloadContents,
      value: Math.round(total * 100) / 100,
      currency: o.currency || "NGN",
      num_items: num,
    }, o);
  }

  /* Purchase — PIXEL ONLY, once per order per browser session.
     The server has already sent the authoritative copy from the order row it
     wrote. This call only has to hand Meta the SAME dedupe id so the browser
     copy (which carries cookies, IP and the user agent that actually bought)
     joins it instead of adding a second conversion. */
  function purchase(order, opts) {
    var o = opts || {};
    var id = String((order && order.id) || o.orderId || "").trim().toUpperCase();
    if (!id) return "";
    var eventId = String((order && order.metaEventId) || (PURCHASE_PREFIX + id));
    var seen = "";
    try { seen = sessionStorage.getItem(SESSION_KEY) || ""; } catch (e) {}
    if (seen === eventId) return eventId;      // already fired this session
    var currency = String((order && order.currency) || o.currency || "NGN");
    var lines = (order && (order.items || order.lines)) || [];
    var contentsList = [];
    var total = Number(order && order.total) || 0;
    lines.slice(0, 12).forEach(function (line) {
      var pid = String((line && (line.id || line.productId || line.name)) || "");
      if (!pid) return;
      contentsList.push({
        id: pid,
        quantity: Math.max(1, Math.round(Number(line.qty || line.quantity) || 1)),
        item_price: Number(line.price) || 0,
      });
    });
    var payload = {
      content_type: "product",
      value: total,
      currency: currency,
      num_items: contentsList.reduce(function (n, c) { return n + c.quantity; }, 0),
      order_id: id,
    };
    if (contentsList.length) {
      payload.content_ids = contentsList.map(function (c) { return c.id; });
      payload.contents = contentsList;
    }
    boot();
    pixel("Purchase", payload, eventId);
    try { sessionStorage.setItem(SESSION_KEY, eventId); } catch (e) {}
    return eventId;
  }

  function eventIdForOrder(id) { return PURCHASE_PREFIX + String(id || "").trim().toUpperCase(); }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { boot(); });
  } else {
    boot();
  }

  return {
    boot: boot,
    track: track,
    viewContent: viewContent,
    addToCart: addToCart,
    initiateCheckout: initiateCheckout,
    purchase: purchase,
    eventId: newEventId,
    eventIdForOrder: eventIdForOrder,
    pixelId: function () { return pixelId; },
  };
})();
