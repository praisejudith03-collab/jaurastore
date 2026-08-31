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

  var token = "";
  var tokenAt = 0;
  var inflight = null;
  var listeners = [];
  var jobs = [];          // in-memory mirror of the outbox
  var dbReady = null;

  var online = typeof navigator === "undefined" ? true : navigator.onLine !== false;
  var flushing = false;
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
  function csrf(force) {
    if (token && !force && Date.now() - tokenAt < 20 * 60 * 1000) return Promise.resolve(token);
    if (inflight && !force) return inflight;
  inflight = fetch("https://jaurastore.com.ng/api/config", { credentials: "include" })

      .then(function (r) { return r.json(); })
      .then(function (d) {
        token = (d && d.csrf) || "";
        tokenAt = Date.now();
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
    return csrf().then(function (tok) {
      var ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
      var timer = ctl ? setTimeout(function () { ctl.abort(); }, job.timeout || 25000) : null;
      return fetch(job.url, {
        method: job.method,
        headers: headersFor(job, tok),
        body: buildBody(job),
        credentials: "same-origin",
        signal: ctl ? ctl.signal : undefined,
        cache: "no-store",
        keepalive: !!job.keepalive,
      }).then(function (r) {
        if (timer) clearTimeout(timer);
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
      }, function (e) {
        if (timer) clearTimeout(timer);
        var err = e instanceof Error ? e : new Error("network");
        err.retryable = true;
        throw err;
      });
    });
  }

  function enqueue(job) {
    if (jobs.length >= MAX_QUEUE) return Promise.resolve({ queued: true, full: true });
    var rec = {
      id: job.id || newId(),
      url: job.url, method: job.method, bodyKind: job.bodyKind || "none",
      body: job.body || null, blob: job.blob || null, field: job.field || "file",
      filename: job.filename || "", extra: job.extra || null,
      label: job.label || "", tries: 0, nextAt: 0, createdAt: Date.now(),
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

  function flush() {
    if (flushing) return Promise.resolve(jobs.length);
    if (!navigator.onLine && typeof navigator !== "undefined" && navigator.onLine === false) return Promise.resolve(jobs.length);
    flushing = true;
    var now = Date.now();
    var ready = jobs.filter(function (j) { return !j.nextAt || j.nextAt <= now; });
    var chain = Promise.resolve();
    ready.forEach(function (rec) {
      chain = chain.then(function () {
        if (navigator.onLine === false) return null;
        return send(rec).then(function (data) {
          drop(rec);
          if (rec.label && window.JA && JA.toast) JA.toast(rec.label + " saved.");
          if (rec.onDone) { try { rec.onDone(data); } catch (e) {} }
          return data;
        }, function (err) {
          rec.tries = (rec.tries || 0) + 1;
          if (!err.retryable || rec.tries >= MAX_ATTEMPTS) {
            if (!err.retryable) { drop(rec); return null; }
            rec.dead = true;
            if (window.JA && JA.toast) JA.toast(rec.label + " could not be saved. Check your connection.");
            return idbPut(rec);
          }
          rec.nextAt = Date.now() + Math.min(300000, Math.pow(2, rec.tries) * 5000);
          return idbPut(rec);
        });
      });
    });
    return chain.then(function () {
      flushing = false;
      lastFlushAt = Date.now();
      emit();
      return jobs.length;
    }, function () { flushing = false; emit(); return jobs.length; });
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
      timeout: opts.timeout || 25000,
      keepalive: !!opts.keepalive,
      onDone: opts.onDone || null,
    };
    // GETs are never queued - a cached read is fine to lose.
    if (job.method === "GET" || !opts.queue) return send(job);
    if (navigator.onLine === false) return enqueue(job);
    return send(job).catch(function (err) {
      if (err.retryable) return enqueue(job);
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
      jobs = merged.filter(function (j) { return !j.dead; });
      emit();
      if (jobs.length) setTimeout(flush, 1500);
      return jobs.length;
    });
  }

  // ------------------------------------------------------- status indicator
  function paintPill() {
    var el = document.getElementById("ja-sync-pill");
    if (!jobs.length) {
      if (el) el.remove();
      return;
    }
    if (!el) {
      el = document.createElement("button");
      el.id = "ja-sync-pill";
      el.type = "button";
      el.className = "sync-pill";
      document.body.appendChild(el);
      el.addEventListener("click", function () { JA.toast("Sending…"); flush(); });
    }
    var offline = navigator.onLine === false;
    el.className = "sync-pill" + (offline ? " is-offline" : "");
    el.innerHTML = '<span class="sync-dot"></span>' +
      (offline ? "Offline · " + jobs.length + " waiting" : "Syncing " + jobs.length + " change" + (jobs.length === 1 ? "" : "s"));
  }

  window.addEventListener("online", function () { online = true; emit(); setTimeout(flush, 400); });
  window.addEventListener("offline", function () { online = false; emit(); });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && Date.now() - lastFlushAt > 20000) flush();
  });
  setInterval(function () { if (jobs.length && navigator.onLine !== false) flush(); }, 45000);

  return {
    api: api, csrf: csrf, flush: flush, boot: boot,
    pending: pending, onStatus: onStatus, isOnline: isOnline,
    _jobs: function () { return jobs.slice(); },
  };
})();
document.addEventListener("DOMContentLoaded", function () { JA_NET.boot(); });

/* Offline caching: the shell, the catalogue and the pages you visited stay
   available with no signal. Enabled only in a secure context (https or
   localhost) so development over plain http is never half-cached. */
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("sw.js").catch(function () { /* optional */ });
  });
}
