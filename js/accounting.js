/* Minimal dual-currency accounting desk. */
(() => {
  "use strict";

  const root = document.getElementById("accounting-root");
  if (!root) return;

  const state = {
    csrf: "",
    email: "",
    currency: "NGN",
    period: "month",
    batch: "",
    batches: [],
    google: { configured: false, connected: false, ledgers: {} },
    summary: null,
    error: "",
    loading: false,
    requestSeq: 0,
  };

  const html = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);

  function money(value, currency = state.currency) {
    const number = Number(value || 0);
    const safe = Number.isFinite(number) ? number : 0;
    const amount = new Intl.NumberFormat("en", { maximumFractionDigits: 0 })
      .format(Math.abs(safe));
    const negative = safe < 0 ? "−" : "";
    return currency === "CFA" ? `${negative}FCFA ${amount}` : `${negative}₦${amount}`;
  }

  async function request(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    if (state.csrf && options.method && options.method !== "GET") headers["X-CSRF-Token"] = state.csrf;
    const response = await fetch(path, {
      credentials: "same-origin", cache: "no-store", ...options, headers,
    });
    let data = {};
    try { data = await response.json(); } catch (_) { /* display a safe error below */ }
    if (!response.ok || data.ok === false) {
      const error = new Error(data.error || `Request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function toast(message, kind = "ok") {
    let node = document.getElementById("aa-toast");
    if (!node) {
      node = document.createElement("div");
      node.id = "aa-toast";
      node.className = "aa-toast";
      node.setAttribute("role", "status");
      document.body.appendChild(node);
    }
    node.textContent = message;
    node.dataset.kind = kind;
    node.classList.add("is-visible");
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(() => node.classList.remove("is-visible"), 3600);
  }

  function showLogin(message = "") {
    root.innerHTML = `
      <section class="aa-login-shell">
        <a class="aa-wordmark" href="/admin">JAURA <span>STORE</span></a>
        <div class="aa-login-card">
          <span class="aa-eyebrow">PRIVATE ADMIN TOOL</span>
          <h1>Accounting</h1>
          <p>Sign in to view the store's NGN and FCFA ledgers.</p>
          ${message ? `<p class="aa-error" role="alert">${html(message)}</p>` : ""}
          <form data-login>
            <label>Email <span>optional when only one admin is configured</span>
              <input type="email" name="email" autocomplete="username" value="${html(state.email)}" />
            </label>
            <label>Password
              <input type="password" name="password" autocomplete="current-password" required autofocus />
            </label>
            <button class="aa-button aa-button-primary" type="submit">Sign in</button>
          </form>
          <a class="aa-back-link" href="/admin">← Back to admin</a>
        </div>
        <p class="aa-login-foot">Protected by Jaura Store Admin authentication.</p>
      </section>`;
  }

  function googleActions() {
    const google = state.google || {};
    const ledgers = google.ledgers || {};
    if (google.connected) {
      return `
        <a class="aa-button aa-button-sheet" href="${html((ledgers.NGN || {}).url || "#")}" target="_blank" rel="noopener noreferrer">↗ Open NGN Google Sheet</a>
        <a class="aa-button aa-button-sheet" href="${html((ledgers.CFA || {}).url || "#")}" target="_blank" rel="noopener noreferrer">↗ Open FCFA Google Sheet</a>
        ${state.error && google.configured ? `<a class="aa-button aa-button-primary" href="/api/admin/accounting/google/connect">Reconnect Google Sheets</a>` : ""}
        <span class="aa-drive-status is-connected"><i aria-hidden="true"></i> Drive connected · ${html(google.email || "Google account")}</span>`;
    }
    const connect = google.configured
      ? `<a class="aa-button aa-button-primary" href="/api/admin/accounting/google/connect">Connect Google Sheets</a>`
      : `<span class="aa-setup-hint">Google Sheets setup is required on the server.</span>`;
    return `
      <button class="aa-button aa-button-sheet" type="button" disabled>↗ Open NGN Google Sheet</button>
      <button class="aa-button aa-button-sheet" type="button" disabled>↗ Open FCFA Google Sheet</button>
      ${connect}
      <span class="aa-drive-status"><i aria-hidden="true"></i> ${html(google.message || "Google Drive is not connected")}</span>`;
  }

  function periodLabel() {
    return state.period === "week" ? "Weekly totals"
      : state.period === "year" ? "Yearly totals" : "Monthly totals";
  }

  function batchOptions() {
    const options = state.batches.map((batch) =>
      `<option value="${html(batch)}"${state.batch === batch ? " selected" : ""}>${html(batch)}</option>`
    ).join("");
    return `<option value=""${state.batch ? "" : " selected"}>All batches</option>${options}`;
  }

  function totalsCards() {
    const summary = state.summary;
    const cards = [
      ["Total Revenue", summary ? summary.revenue : null, "Revenue from confirmed orders"],
      ["Supplier Costs", summary ? summary.supplierCosts : null, "Order costs + unlinked purchases"],
      ["Transport", summary ? summary.transport : null, "Delivery and transportation"],
      ["Net Profit", summary ? summary.netProfit : null, "Revenue less costs and transport"],
    ];
    return `<section class="aa-cards" aria-label="Accounting totals">${cards.map(([label, value, note], index) => `
      <article class="aa-card${index === 3 ? " is-profit" : ""}">
        <span class="aa-card-label">${html(label)}</span>
        <strong>${value == null ? "—" : html(money(value))}</strong>
        <small>${html(note)}</small>
      </article>`).join("")}</section>`;
  }

  function render() {
    const google = state.google || {};
    const currencyName = state.currency === "NGN" ? "Naira" : "FCFA";
    const summary = state.summary;
    const coverage = summary && summary.periodStart && summary.periodEnd
      ? `${summary.periodStart} – ${summary.periodEnd}` : "";
    const connectedMessage = google.connected && google.syncingExistingOrders
      ? `<p class="aa-sync-note" role="status">Existing confirmed orders are syncing into your ledgers.</p>` : "";
    const readError = state.error
      ? `<p class="aa-error aa-sheet-error" role="alert">${html(state.error)}${google.connected ? " You can still open the Google Sheet directly." : ""}</p>`
      : "";
    root.innerHTML = `
      <div class="aa-app">
        <header class="aa-topbar">
          <a href="/admin" class="aa-wordmark">JAURA <span>STORE</span><small>ACCOUNTING</small></a>
          <nav class="aa-top-actions" aria-label="Admin actions">
            <span class="aa-signed-in">${html(state.email || "Admin")}</span>
            <a class="aa-back-button" href="/admin">← Admin</a>
            <button class="aa-link-button" type="button" data-action="refresh"${state.loading ? " disabled" : ""}>Refresh</button>
            <button class="aa-link-button" type="button" data-action="logout">Sign out</button>
          </nav>
        </header>
        <main class="aa-main">
          <section class="aa-heading">
            <div>
              <span class="aa-eyebrow">A CLEAR VIEW OF YOUR CASH</span>
              <h1>Accounting</h1>
              <p>Confirmed sales, supplier costs and transport, kept in simple NGN and FCFA ledgers.</p>
            </div>
            <div class="aa-current-ledger"><span>Ledger</span><strong>${html(currencyName)}</strong></div>
          </section>

          <section class="aa-sheet-strip" aria-label="Google Sheets ledgers">
            <div class="aa-sheet-copy"><strong>Your ledgers</strong><span>Saved in the store owner's Google Drive</span></div>
            <div class="aa-sheet-actions">${googleActions()}</div>
          </section>
          ${connectedMessage}
          ${readError}

          <section class="aa-filter-panel" aria-label="Accounting filters">
            <div class="aa-currency-switch" role="group" aria-label="Currency">
              <button type="button" data-currency="NGN" class="${state.currency === "NGN" ? "is-active" : ""}" aria-pressed="${state.currency === "NGN"}">₦ <span>NGN</span></button>
              <button type="button" data-currency="CFA" class="${state.currency === "CFA" ? "is-active" : ""}" aria-pressed="${state.currency === "CFA"}">C <span>FCFA</span></button>
            </div>
            <div class="aa-filter-divider" aria-hidden="true"></div>
            <div class="aa-period-switch" role="group" aria-label="Time period">
              <span class="aa-filter-label">Period</span>
              ${[["week", "Weekly"], ["month", "Monthly"], ["year", "Yearly"]].map(([period, label]) => `
                <button type="button" data-period="${period}" class="${state.period === period ? "is-active" : ""}" aria-pressed="${state.period === period}">${label}</button>`).join("")}
            </div>
            <label class="aa-batch-filter">Selected batch
              <select data-batch>${batchOptions()}</select>
            </label>
          </section>

          <section class="aa-results-heading">
            <div><h2>${html(periodLabel())}</h2><span>${html(coverage || (state.loading ? "Updating totals…" : "Current period"))}</span></div>
            <span class="aa-order-count">${summary ? `${Number(summary.orderCount || 0).toLocaleString("en")} confirmed orders` : ""}</span>
          </section>
          ${totalsCards()}
          <p class="aa-sheet-hint">Enter supplier purchases, transportation and per-order costs directly in Google Sheets. Net Profit updates automatically.</p>
          <footer class="aa-footer">Jaura Store · Private accounting desk · Amounts shown in whole ${html(state.currency)} units</footer>
        </main>
      </div>`;
  }

  function oauthNotice() {
    const params = new URLSearchParams(window.location.search);
    const result = params.get("google");
    if (!result) return;
    const messages = {
      connected: "Google Drive connected. Your two ledgers are ready; existing orders are syncing.",
      "not-configured": "Google Sheets has not been configured on the server yet.",
      login: "Sign in to the admin account, then connect Google Drive.",
      "state-error": "The Google sign-in expired or could not be verified. Please try again.",
      cancelled: "Google Drive was not connected.",
      failed: "Google could not finish the connection. Check the OAuth setup and retry.",
    };
    if (messages[result]) toast(messages[result], result === "connected" ? "ok" : "error");
    window.history.replaceState({}, "", window.location.pathname);
  }

  async function loadSummary() {
    const requestId = ++state.requestSeq;
    state.loading = true;
    state.error = "";
    render();
    const params = new URLSearchParams({
      currency: state.currency,
      period: state.period,
      batch: state.batch,
    });
    try {
      const data = await request(`/api/admin/accounting/summary?${params.toString()}`);
      if (requestId !== state.requestSeq) return;
      state.google = data.google || state.google;
      state.summary = data.summary || null;
      state.error = data.error || "";
      state.batches = Array.isArray((data.summary || {}).batches) ? data.summary.batches : state.batches;
      if (state.batch && !state.batches.some((value) => value === state.batch)) {
        state.batch = "";
        if (data.summary) {
          const retry = new URLSearchParams({ currency: state.currency, period: state.period });
          const refreshed = await request(`/api/admin/accounting/summary?${retry.toString()}`);
          if (requestId !== state.requestSeq) return;
          state.google = refreshed.google || state.google;
          state.summary = refreshed.summary || null;
          state.error = refreshed.error || "";
          state.batches = Array.isArray((refreshed.summary || {}).batches) ? refreshed.summary.batches : [];
        }
      }
    } catch (error) {
      if (requestId !== state.requestSeq) return;
      if (error.status === 401) {
        state.summary = null;
        state.google = { configured: false, connected: false, ledgers: {} };
        state.email = "";
        showLogin("Your admin session has expired. Please sign in again.");
        return;
      }
      state.error = error.message || "The accounting totals could not be loaded.";
      state.summary = null;
    } finally {
      if (requestId === state.requestSeq) state.loading = false;
    }
    if (requestId === state.requestSeq) render();
  }

  async function start() {
    try {
      const session = await request("/api/admin/session");
      state.csrf = session.csrf || "";
      state.email = session.email || "";
      if (!session.authenticated) {
        showLogin();
        return;
      }
      oauthNotice();
      await loadSummary();
    } catch (error) {
      showLogin(error.message || "The accounting desk could not connect. Please try again.");
    }
  }

  async function login(form) {
    const values = new FormData(form);
    const email = String(values.get("email") || "").trim();
    const password = String(values.get("password") || "");
    const button = form.querySelector("button[type=submit]");
    if (button) { button.disabled = true; button.textContent = "Signing in…"; }
    try {
      const data = await request("/api/admin/login", {
        method: "POST", body: JSON.stringify({ email, password }),
      });
      state.csrf = data.csrf || "";
      state.email = data.email || email;
      oauthNotice();
      await loadSummary();
    } catch (error) {
      state.email = email;
      showLogin(error.message || "Sign in failed.");
    }
  }

  async function logout() {
    try {
      await request("/api/admin/logout", { method: "POST", body: "{}" });
    } catch (_) { /* discard the local view even if sign-out could not sync */ }
    state.summary = null;
    state.email = "";
    showLogin("You have signed out of the accounting desk.");
  }

  root.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-login]");
    if (!form) return;
    event.preventDefault();
    login(form);
  });

  root.addEventListener("click", (event) => {
    const currency = event.target.closest("[data-currency]");
    if (currency) {
      const next = currency.dataset.currency;
      if (next && next !== state.currency) {
        state.currency = next;
        loadSummary();
      }
      return;
    }
    const period = event.target.closest("[data-period]");
    if (period) {
      const next = period.dataset.period;
      if (next && next !== state.period) {
        state.period = next;
        loadSummary();
      }
      return;
    }
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "refresh") loadSummary().then(() => toast("Totals refreshed.")).catch((error) => toast(error.message, "error"));
    else if (action === "logout") logout();
  });

  root.addEventListener("change", (event) => {
    if (!event.target.matches("[data-batch]")) return;
    state.batch = event.target.value || "";
    loadSummary();
  });

  start();
})();
