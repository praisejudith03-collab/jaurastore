/* J Aura Store - network layer.
   - Adds the CSRF token to every mutating call.
   - Retries with backoff, and if the device goes offline it stores the call in
     an outbox (IndexedDB) and pushes it the moment the connection returns.
   Nothing a shopper or an admin saves is lost because Wi-Fi blinked. */
window.JA_NET = (function () {
  var CSRF_KEY = "jaura_csrf";
  var DB_NAME = "jaura_outbox";
  var STORE = "jobs";
  var MAX_ATTEMPTS = 8;
  var MAX_QUEUE = 300;
  // The floating sync pill is feedback, not a permanent queue monitor. Keep
  // it transient: it slides away 1.4s after it appears and has a hard 5s cap.
  // Durable offline jobs remain in IndexedDB/localStorage and the admin's
  // Connection & sync panel; they must never pin a badge on screen forever.
  var PILL_AUTO_DISMISS_MS = 1400;
  var PILL_HARD_TIMEOUT_MS = 5000;
  var PILL_TRANSITION_MS = 260;

  var token = "";
  var tokenAt = 0;
  var inflight = null;
  var listeners = [];
  var jobs = [];          // in-memory mirror of the outbox
  var dbReady = null;

  var online = typeof navigator === "undefined" ? true : navigator.onLine !== false;
  var flushing = false;
  var flushPromise = null;
  var lastFlushAt = 0;

  function emit() {
    var n = jobs.length;
    listeners.forEach(function (fn) { try { fn({ pending: n, online: online }); } catch (e) {} });
    try { paintPill(); } catch (e) {}
  }
  function onStatus(fn) { if (typeof fn === "function") listeners.push(fn); fn && fn({ pending: jobs.length, online: online }); return fn; }

  // ------------------------------------------------------------ IndexedDB
  function openDB() {
    if (dbReady) return dbReady;
    dbReady = new Promise(function (resolve) {
      var req;
      try { req = indexedDB.open(DB_NAME, 1); } catch (e) { return resolve(null); }
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: "id" });
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { resolve(null); };
    });
    return dbReady;
  }
  /* IndexedDB is not always there (Safari private mode, some in-app
     browsers). Without a fallback a queued save lived only in memory and was
     lost the moment the page was refreshed, so mirror the outbox in
     localStorage too. File uploads are too big for it - those stay in
     memory and are covered by IndexedDB. */
  var LS_KEY = "jaura_outbox_ls";
  function lsAll() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || "[]") || []; }
    catch (e) { return []; }
  }
  function lsSave(rows) {
    try { localStorage.setItem(LS_KEY, JSON.stringify(rows)); return true; }
    catch (e) { return false; }
  }
  function lsPut(rec) {
    if (rec.blob) return false;                     // files do not fit
    var rows = lsAll().filter(function (r) { return r.id !== rec.id; });
    rows.push(rec);
    return lsSave(rows);
  }
  function lsDelete(id) { lsSave(lsAll().filter(function (r) { return r.id !== id; })); }

  function idbPut(job) {
    return openDB().then(function (db) {
      if (!db) return false;
      return new Promise(function (resolve) {
        try {
          var tx = db.transaction(STORE, "readwrite");
          tx.objectStore(STORE).put(job);
          tx.oncomplete = function () { resolve(true); };
          tx.onerror = function () { resolve(false); };
        } catch (e) { resolve(false); }
      });
    }).catch(function () { return false; });
  }
  function idbDelete(id) {
    return openDB().then(function (db) {
      if (!db) return false;
      return new Promise(function (resolve) {
        try {
          var tx = db.transaction(STORE, "readwrite");
          tx.objectStore(STORE).delete(id);
          tx.oncomplete = function () { resolve(true); };
          tx.onerror = function () { resolve(false); };
        } catch (e) { resolve(false); }
      });
    }).catch(function () { return false; });
  }
  function idbAll() {
    return openDB().then(function (db) {
      if (!db) return [];
      return new Promise(function (resolve) {
        try {
          var tx = db.transaction(STORE, "readonly");
          var req = tx.objectStore(STORE).getAll();
          req.onsuccess = function () { resolve(req.result || []); };
          req.onerror = function () { resolve([]); };
        } catch (e) { resolve([]); }
      });
    }).catch(function () { return []; });
  }

  // ------------------------------------------------------------ CSRF token
  var recaptchaKey = "";
  var recaptchaLoad = null;
  function csrf(force) {
    if (token && !force && Date.now() - tokenAt < 20 * 60 * 1000) return Promise.resolve(token);
    if (inflight && !force) return inflight;
    // absolute path: a relative "api/config" resolves against the current
    // directory, so any page served from a sub-path lost the CSRF token and
    // with it the reCAPTCHA site key — the widget stayed empty.
    var opts = { credentials: "same-origin", cache: "no-store" };
    if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
      // CSRF bootstrap is part of the request budget too. Leaving this at
      // 30s let a frozen config request hold every admin write before the
      // actual API call (and its own timeout) ever started.
      opts.signal = AbortSignal.timeout(5000);
    }
    inflight = fetch("/api/config", opts)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        token = (d && d.csrf) || "";
        tokenAt = Date.now();
        if (d && d.recaptchaSiteKey) recaptchaKey = String(d.recaptchaSiteKey);
        try { sessionStorage.setItem(CSRF_KEY, token); } catch (e) {}
        return token;
      })
      .catch(function () {
        try { token = sessionStorage.getItem(CSRF_KEY) || ""; } catch (e) { token = ""; }
        return token;
      })
      .then(function (t) { inflight = null; return t; });
    return inflight;
  }

  // ------------------------------------------- Google reCAPTCHA v2 (invisible)
  // The site key comes from api/config (set RECAPTCHA_SITE_KEY on the
  // server) and MUST be a reCAPTCHA v2 INVISIBLE key - a v3 key here is what
  // makes the widget say "Invalid key type". The widget is rendered INVISIBLE
  // (size: "invisible") into a hidden anchor, so there is no box
  // on the page and the shopper never sees it; grecaptcha.execute() mints the
  // token on submit. When no key is configured nothing loads, no widget is
  // rendered and every call resolves to "" - the shop works exactly as before.
  var recaptchaWidgets = [];
  function siteKey() {
    if (recaptchaKey) return Promise.resolve(recaptchaKey);
    return csrf().then(function () { return recaptchaKey; }).catch(function () { return ""; });
  }
  function loadRecaptcha() {
    if (recaptchaLoad) return recaptchaLoad;
    recaptchaLoad = new Promise(function (resolve) {
      if (window.grecaptcha && window.grecaptcha.render) return resolve(true);
      var s = document.createElement("script");
      s.src = "https://www.google.com/recaptcha/api.js?render=explicit";
      s.async = true;
      s.defer = true;
      s.onload = function () { resolve(!!window.grecaptcha); };
      s.onerror = function () { resolve(false); };
      document.head.appendChild(s);
      setTimeout(function () { resolve(!!(window.grecaptcha && window.grecaptcha.render)); }, 4000);
    });
    return recaptchaLoad;
  }
  // Render the invisible widget into every [data-recaptcha-widget] anchor.
  // No visible box: the anchor is hidden in CSS (.ck-recaptcha-anchor), the
  // shopper never sees reCAPTCHA, but the widget can still mint a token.
  function renderWidgets(key) {
    var boxes = document.querySelectorAll("[data-recaptcha-widget]");
    if (!boxes.length) return Promise.resolve(false);
    return loadRecaptcha().then(function (ok) {
      if (!ok || !window.grecaptcha || !window.grecaptcha.render) return false;
      return new Promise(function (resolve) {
        var draw = function () {
          var rendered = 0;
          Array.prototype.forEach.call(boxes, function (el) {
            if (el.getAttribute("data-recaptcha-id")) { rendered++; return; }
            try {
              var id = window.grecaptcha.render(el, { sitekey: key, size: "invisible" });
              el.setAttribute("data-recaptcha-id", String(id));
              recaptchaWidgets.push(id);
              rendered++;
            } catch (e) {}
          });
          resolve(rendered > 0);
        };
        try { window.grecaptcha.ready(draw); } catch (e) { draw(); }
      });
    });
  }
  function widgetIds() {
    var out = [];
    document.querySelectorAll("[data-recaptcha-id]").forEach(function (el) {
      var v = el.getAttribute("data-recaptcha-id");
      if (v !== null && v !== "") out.push(Number(v));
    });
    return out;
  }
  // The invisible widget's response token. Never blocks a sale: when no key
  // is configured (or the widget never loaded) this is "" and the server
  // decides (RECAPTCHA_REQUIRED is off by default).
  function recaptcha(action) {
    return siteKey().then(function (key) {
      if (!key) return "";
      return renderWidgets(key).then(function () {
        var ids = widgetIds();
        if (!ids.length) return "";
        var id = ids[0];
        try { window.grecaptcha.reset(id); } catch (e) {}
        return new Promise(function (resolve) {
          var settled = false;
          var finish = function (tok) {
            if (!settled) { settled = true; resolve(tok || ""); }
          };
          // Invisible v2: execute() returns a PROMISE that resolves with the
          // fresh token.
          try {
            var run = window.grecaptcha.execute(id);
            if (run && typeof run.then === "function") {
              run.then(function (tok) { finish(tok || ""); }, function () { finish(""); });
            }
          } catch (e) {
            finish("");
          }
          // Polling probe as fallback with a short graceful timeout
          var probes = 0;
          var poll = function () {
            var tok = "";
            try { tok = window.grecaptcha.getResponse(id) || ""; } catch (e) {}
            if (tok) return finish(tok);
            if (probes < 4) { probes += 1; setTimeout(poll, probes === 1 ? 250 : 350); }
            else finish("");
          };
          setTimeout(poll, 250);
        });
      });
    }).catch(function () { return ""; });
  }
  // A v2 token is single-use: clear the tick once it has been spent so the
  // next order gets a fresh one.
  function resetRecaptcha() {
    widgetIds().forEach(function (id) {
      try { window.grecaptcha.reset(id); } catch (e) {}
    });
  }
  // Show the reCAPTCHA legal note and arm the invisible widget on pages
  // that carry one.
  // Contract for tests: when no key is configured nothing renders and checkout is never blocked.
  // The two exact strings below are asserted by tests/test_recaptcha.py
  function mountRecaptcha() {
    return siteKey().then(function (key) {
      var notes = document.querySelectorAll("[data-recaptcha-note]");
      notes.forEach(function (el) { el.hidden = false; el.style.display = "block"; });
      if (!key) return false;
      return renderWidgets(key);
    }).catch(function () {
      return false;
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { mountRecaptcha(); });
  } else {
    mountRecaptcha();
  }

  // ------------------------------------------------------------- outbox
  function newId() { return "j" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }

  function buildBody(job) {
    if (job.bodyKind === "blob") {
      var fd = new FormData();
      (job.extra || []).forEach(function (p) { fd.append(p[0], p[1]); });
      fd.append(job.field || "file", job.blob, job.filename || "upload.jpg");
      return fd;
    }
    if (job.bodyKind === "json") return job.body;
    return null;
  }
  function headersFor(job, tok) {
    var h = { "X-Requested-With": "XMLHttpRequest" };
    if (job.method !== "GET" && tok) h["X-CSRF-Token"] = tok;
    if (job.bodyKind === "json") h["Content-Type"] = "application/json";
    return h;
  }

  function send(job) {
    var abortMs = job.timeout || (job.bodyKind === "blob" ? 300000 : 5000);
    var deadlineAt = Date.now() + abortMs;
    var ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = null;
    var timedOut = new Promise(function (_resolve, reject) {
      timer = setTimeout(function () {
        try { if (ctl) ctl.abort(); } catch (e) {}
        var err = new Error("Request timed out after " + Math.ceil(abortMs / 1000) + " seconds.");
        err.name = "TimeoutError";
        err.status = 0;
        err.retryable = true;
        err.timeout = true;
        reject(err);
      }, abortMs);
    });

    // One deadline covers CSRF bootstrap, reCAPTCHA, response headers AND the
    // response body. Previously the 25s timer started only at fetch(), and
    // cleared as soon as headers arrived; a hanging config/token/body request
    // could therefore leave the outbox badge saying "Syncing" indefinitely.
    var attempt = csrf().then(function (tok) {
      // A fresh reCAPTCHA token per attempt: a queued retry must never reuse
      // the expired token from the original submit.
      var rcp = job.recaptcha ? recaptcha(job.recaptcha) : Promise.resolve("");
      return rcp.then(function (rct) {
        if (Date.now() >= deadlineAt) {
          var expired = new Error("Request timed out after " + Math.ceil(abortMs / 1000) + " seconds.");
          expired.name = "TimeoutError"; expired.status = 0;
          expired.retryable = true; expired.timeout = true;
          throw expired;
        }
        var hdrs = headersFor(job, tok);
        if (rct) hdrs["X-Recaptcha-Token"] = rct;
        if (rct) { try { resetRecaptcha(); } catch (e) {} }
        return fetch(job.url, {
          method: job.method,
          headers: hdrs,
          body: buildBody(job),
          credentials: "include",
          signal: ctl ? ctl.signal : undefined,
          cache: "no-store",
          keepalive: !!job.keepalive,
        });
      });
    }).then(function (r) {
      var bad = r.status >= 500 || r.status === 429 || r.status === 0;
      return r.text().then(function (t) {
        var data = null;
        try { data = JSON.parse(t); } catch (e) { data = null; }
        if (!r.ok) {
          var err = new Error((data && data.error) || ("HTTP " + r.status));
          err.status = r.status; err.retryable = bad; err.data = data;
          throw err;
        }
        return data || {};
      });
    }).catch(function (e) {
      var err = e instanceof Error ? e : new Error("network");
      if (ctl && ctl.signal.aborted && Date.now() >= deadlineAt) {
        err.timeout = true;
        err.name = "TimeoutError";
        err.message = "Request timed out after " + Math.ceil(abortMs / 1000) + " seconds.";
      }
      if (err.retryable !== false) err.retryable = true;
      throw err;
    });

    return Promise.race([attempt, timedOut]).finally(function () {
      if (timer) clearTimeout(timer);
    });
  }

  function enqueue(job, quietIndicator) {
    if (jobs.length >= MAX_QUEUE) {
      var evictAt = -1;
      for (var i = 0; i < jobs.length; i++) {
        if (jobs[i].dead) { evictAt = i; break; }
      }
      if (evictAt < 0) evictAt = 0;
      drop(jobs[evictAt]);
    }
    var rec = {
      id: job.id || newId(),
      url: job.url, method: job.method, bodyKind: job.bodyKind || "none",
      body: job.body || null, blob: job.blob || null, field: job.field || "file",
      filename: job.filename || "", extra: job.extra || null,
      recaptcha: job.recaptcha || "",
      label: job.label || "", timeout: job.timeout || 0,
      keepalive: !!job.keepalive,
      tries: 0, nextAt: 0, createdAt: Date.now(),
      badgeHidden: !!quietIndicator, notifiedFailure: false,
    };
    return idbPut(rec).then(function (stored) {
      jobs.push(rec);
      var persisted = !!stored || lsPut(rec);
      rec.memoryOnly = !persisted;
      emit();
      return { queued: true, id: rec.id, persisted: persisted };
    });
  }

  function drop(rec) {
    jobs = jobs.filter(function (j) { return j.id !== rec.id; });
    idbDelete(rec.id);
    lsDelete(rec.id);
    emit();
  }

  /** Drop every queued job that matches ``predicate`` (job -> truthy).
   *
   *  The delete flow uses this to purge a product's queued saves before the
   *  DELETE is enqueued: a stale save retried after the delete would land
   *  later and resurrect the product (every save clears the delete
   *  tombstone by design). Returns how many jobs were discarded. */
  function discard(predicate) {
    var doomed = jobs.filter(function (j) {
      try { return !!predicate(j); } catch (e) { return false; }
    });
    doomed.forEach(drop);
    return doomed.length;
  }


  function flush(force) {
    if (flushing) return flushPromise.then(function () { return force ? flush(true) : jobs.length; });
    if (!navigator.onLine && typeof navigator !== "undefined" && navigator.onLine === false) return Promise.resolve(jobs.length);
    flushing = true;
    var now = Date.now();
    var ready = jobs.filter(function (j) {
      if (force) return true;
      if (j.dead) return false;
      return !j.nextAt || j.nextAt <= now;
    });
    var chain = Promise.resolve();
    ready.forEach(function (rec) {
      chain = chain.then(function () {
        if (navigator.onLine === false) return null;
        if (force) {
          rec.dead = false;
          rec.badgeHidden = false;
          rec.notifiedFailure = false;
        }
        return send(rec).then(function (data) {
          drop(rec);
          if (rec.label && window.JA && JA.toast) JA.toast(rec.label + " saved.");
          if (rec.onDone) { try { rec.onDone(data); } catch (e) {} }
          return data;
        }, function (err) {
          rec.tries = (rec.tries || 0) + 1;
          rec.nextAt = Date.now() + Math.min(300000, Math.pow(2, rec.tries) * 5000);
          if (!err.retryable || rec.tries >= MAX_ATTEMPTS) {
            // A permanently failed change is dropped here on purpose. It used
            // to stay in the outbox forever and pin "Syncing 1 change" on
            // screen. Tell the admin once, then remove the failed job.
            var label = rec.label || "Change";
            rec.dead = true;
            if (window.JA && JA.toast) {
              JA.toast(label + " could not be saved. " +
                ((err && (err.message || (err.data && err.data.error))) || "Check your connection."));
            }
            drop(rec);
            return;
          }
          // Keep the exact request durably queued, but don't leave a floating
          // status widget for a background retry. A short standard toast tells
          // the owner it is pending; the Connection & sync panel still shows
          // the durable queue count, and Retry now remains available there.
          rec.badgeHidden = true;
          if (!rec.notifiedFailure && window.JA && JA.toast) {
            rec.notifiedFailure = true;
            var retryLabel = rec.label || "Change";
            JA.toast(err && err.timeout
              ? retryLabel + " is taking too long. It is safely queued to retry."
              : retryLabel + " could not sync. It is safely queued to retry.");
          }
          return idbPut(rec).then(function () { lsPut(rec); });
        });
      });
    });
    flushPromise = chain.then(function () {
      flushing = false;
      lastFlushAt = Date.now();
      emit();
      return jobs.length;
    }, function () { flushing = false; emit(); return jobs.length; });
    return flushPromise;
  }

  /** Public request helper. */
  function api(path, opts) {
    opts = opts || {};
    var job = {
      url: path,
      method: (opts.method || "GET").toUpperCase(),
      bodyKind: opts.json ? "json" : (opts.blob ? "blob" : "none"),
      body: opts.json ? JSON.stringify(opts.json) : null,
      blob: opts.blob || null,
      field: opts.field || "file",
      filename: opts.filename || "",
      extra: opts.extra || null,
      label: opts.label || "",
      timeout: opts.blob
        ? (opts.timeout || 300000)
        : Math.min(5000, Math.max(1, Number(opts.timeout) || 5000)),
      keepalive: !!opts.keepalive,
      onDone: opts.onDone || null,
    };
    // The forms Google reCAPTCHA v3 protects: checkout + payment receipt.
    if (job.method === "POST") {
      if (/api\/orders(\?|$)/.test(path)) job.recaptcha = "checkout";
      else if (/api\/payment-proof(\?|$)/.test(path)) job.recaptcha = "receipt";
    }
    if (opts.recaptcha) job.recaptcha = opts.recaptcha;
    // GETs are never queued - a cached read is fine to lose.
    if (job.method === "GET" || !opts.queue) return send(job);
    if (navigator.onLine === false) return enqueue(job);
    return send(job).catch(function (err) {
      // A failed foreground request is queued for data safety, but it is no
      // longer an active sync. Hide its floating pill and let the calling
      // action show the ordinary temporary toast (Product/Delete/etc.).
      if (err.retryable) return enqueue(job, true);
      throw err;
    });
  }

  function pending() { return jobs.length; }
  function isOnline() { return !(navigator.onLine === false); }

  function boot() {
    return idbAll().then(function (rows) {
      var merged = (rows || []).slice();
      var seen = {};
      merged.forEach(function (j) { seen[j.id] = 1; });
      lsAll().forEach(function (j) { if (!seen[j.id]) merged.push(j); });
      jobs = merged;
      emit();
      if (jobs.length) setTimeout(function () { flush(false); }, 1500);
      return jobs.length;
    });
  }

  // ------------------------------------------------------- status indicator
  function clearPillTimers(el) {
    if (!el) return;
    clearTimeout(el._jaPillAutoTimer);
    clearTimeout(el._jaPillHardTimer);
    clearTimeout(el._jaPillRemoveTimer);
    el._jaPillAutoTimer = el._jaPillHardTimer = el._jaPillRemoveTimer = null;
  }

  function dismissPill(el, suppressDisplayedJobs) {
    if (!el) return;
    if (suppressDisplayedJobs) {
      var displayed = new Set(el._jaPillJobIds || []);
      jobs.forEach(function (job) {
        if (!displayed.has(job.id)) return;
        job.badgeHidden = true;
        idbPut(job);
        lsPut(job);
      });
    }
    clearTimeout(el._jaPillAutoTimer);
    clearTimeout(el._jaPillHardTimer);
    el._jaPillAutoTimer = el._jaPillHardTimer = null;
    el.classList.add("is-dismissing");
    clearTimeout(el._jaPillRemoveTimer);
    el._jaPillRemoveTimer = setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, PILL_TRANSITION_MS);
  }

  function paintPill() {
    var el = document.getElementById("ja-sync-pill");
    var visible = jobs.filter(function (j) { return !j.badgeHidden; });
    if (!visible.length) {
      // Success, a failed/slow background retry, or an empty outbox: slide the
      // transient widget away instead of leaving a fixed element in the UI.
      if (el) dismissPill(el, false);
      return;
    }

    // Dead jobs are dropped on failure. Count retry-backoff jobs separately:
    // they are waiting, not actively syncing.
    var now = Date.now();
    var waiting = 0;
    visible.forEach(function (j) { if (j.dead || (j.nextAt && j.nextAt > now)) waiting++; });
    var live = visible.length - waiting;
    if (!el) {
      el = document.createElement("button");
      el.id = "ja-sync-pill";
      el.type = "button";
      el.className = "sync-pill";
      document.body.appendChild(el);
      el.addEventListener("click", function () { JA.toast("Sending…"); flush(true); });
    } else if (el.classList.contains("is-dismissing")) {
      // A genuinely new queued action arrived during the slide-out.
      clearPillTimers(el);
      el.classList.remove("is-dismissing");
    }

    el._jaPillJobIds = visible.map(function (j) { return j.id; });
    var offline = navigator.onLine === false;
    el.className = "sync-pill" + (offline ? " is-offline" : "");
    var many = function (n) { return n + " change" + (n === 1 ? "" : "s"); };
    var label;
    if (offline) {
      label = "Offline · " + many(visible.length) + " waiting";
    } else if (live === 0) {
      var dueIn = 0;
      visible.forEach(function (j) { if (j.nextAt && j.nextAt > now) dueIn = Math.max(dueIn, j.nextAt - now); });
      label = "Retrying " + many(waiting) + (dueIn ? " in " + Math.max(1, Math.round(dueIn / 1000)) + "s" : "");
    } else {
      label = "Syncing " + many(live) + (waiting ? " · " + waiting + " waiting" : "");
    }
    el.innerHTML = '<span class="sync-dot"></span>' + label;

    // Brief entrance feedback, then slide fully off-screen. Even if a browser
    // timer fires late, the hard cap suppresses these jobs so later status
    // polls cannot recreate a stuck badge.
    clearTimeout(el._jaPillAutoTimer);
    el._jaPillAutoTimer = setTimeout(function () { dismissPill(el, true); }, PILL_AUTO_DISMISS_MS);
    if (!el._jaPillHardTimer) {
      el._jaPillHardTimer = setTimeout(function () { dismissPill(el, true); }, PILL_HARD_TIMEOUT_MS);
    }
  }

  window.addEventListener("online", function () { online = true; emit(); setTimeout(flush, 400); });
  window.addEventListener("offline", function () { online = false; emit(); });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && Date.now() - lastFlushAt > 20000) flush();
  });
  setInterval(function () { if (jobs.length && navigator.onLine !== false) flush(); }, 45000);

  return {
    api: api, csrf: csrf, flush: flush, boot: boot, recaptcha: recaptcha,
    mountRecaptcha: mountRecaptcha, resetRecaptcha: resetRecaptcha,
    pending: pending, onStatus: onStatus, isOnline: isOnline, discard: discard,
    _jobs: function () { return jobs.slice(); },
  };
})();
document.addEventListener("DOMContentLoaded", function () { JA_NET.boot(); });

/* Offline caching: the shell, the catalogue and the pages you visited stay
   available with no signal. Enabled only in a secure context (https or
   localhost) so development over plain http is never half-cached. */
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("sw.js", { updateViaCache: "none" }).then(function (reg) {
      if (!reg) return;

      /* Auto cache-bust: when a new worker turns up, reload ONCE so the
         visitor gets the new files without pressing Ctrl/Cmd + Shift + R.
         The reload is rate-limited (at most one per minute) so a broken
         deploy can never trap a phone in a refresh loop. */
      reg.addEventListener("updatefound", function () {
        var worker = reg.installing;
        if (!worker) return;
        worker.addEventListener("statechange", function () {
          if (worker.state !== "installed") return;
          if (!navigator.serviceWorker.controller) return;   // first visit
          var now = Date.now();
          try {
            var last = Number(sessionStorage.getItem("jaura-sw-reload-at") || 0);
            if (last && now - last < 60000) return;
            sessionStorage.setItem("jaura-sw-reload-at", String(now));
          } catch (e) { /* private mode: still reload, just unguarded below */ }
          window.location.reload();
        });
      });

      /* Look for a newer worker when the tab comes back, and every half
         hour. With updateViaCache:"none" this always asks the network, so a
         shipped change reaches an open tab on its own. */
      if (reg.update) {
        document.addEventListener("visibilitychange", function () {
          if (!document.hidden) reg.update().catch(function () {});
        });
        setInterval(function () { reg.update().catch(function () {}); }, 30 * 60 * 1000);
      }
    }).catch(function () { /* optional */ });
  });
}
