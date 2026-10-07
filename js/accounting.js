/* Staged dual-currency accounting desk.
 *
 * The desk is a STAGING queue: confirmed orders land here as itemized rows
 * (customer, items, quantities, selling price, editable NGN supplier cost).
 * The owner logs manual expenses (bulk stock, payouts, bank fees), watches the
 * live running bank balance, then selects orders and pushes them to Google
 * Sheets - NGN rows to the NGN tab, FCFA rows to the FCFA tab - at which point
 * they are archived into a named batch and cleared off the queue. Historical
 * profit analytics live on the Sales page, not here.
 */
(() => {
  "use strict";

  const root = document.getElementById("accounting-root");
  if (!root) return;

  const STAGE_PAGE_SIZE = 40;

  const state = {
    csrf: "",
    email: "",
    currency: "NGN",
    entries: [],
    expenses: [],
    settings: { startingBalanceNgn: 0, startingBalanceCfa: 0, referenceSpreadsheetId: "" },
    balances: { NGN: null, CFA: null },
    batches: [],
    google: { configured: false, connected: false, ledgers: {} },
    currentExchangeRate: 0.44,
    error: "",
    loading: false,
    requestSeq: 0,
    selected: new Set(),
    shown: STAGE_PAGE_SIZE,
    pushing: false,
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
    toast.timer = window.setTimeout(() => node.classList.remove("is-visible"), 4200);
  }

  function showLogin(message = "") {
    root.innerHTML = `
      <section class="aa-login-shell">
        <a class="aa-wordmark" href="/admin">JAURA <span>STORE</span></a>
        <div class="aa-login-card">
          <span class="aa-eyebrow">PRIVATE ADMIN TOOL</span>
          <h1>Accounting</h1>
          <p>Sign in to stage confirmed orders and push them to your ledgers.</p>
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

  /* ------------------------------------------------------------- balances */

  function balanceCard(currency) {
    const b = (state.balances || {})[currency] || {};
    const symbol = currency === "CFA" ? "FCFA" : "₦";
    return `
      <article class="aa-balance${Number(b.balance || 0) < 0 ? " is-negative" : ""}">
        <span class="aa-balance-label">${symbol} <span>${currency === "CFA" ? "FCFA" : "NGN"}</span> bank balance</span>
        <strong>${html(money(b.balance, currency))}</strong>
        <dl>
          <div><dt>Starting balance</dt><dd>${html(money(b.startingBalance, currency))}</dd></div>
          <div><dt>+ Pushed sales profit</dt><dd>${html(money(b.salesNetProfit, currency))}</dd></div>
          <div><dt>− Manual expenses</dt><dd>${html(money(b.manualExpenses, currency))}</dd></div>
          <div><dt>− Bank / transfer charges</dt><dd>${html(money(b.bankCharges, currency))}</dd></div>
        </dl>
      </article>`;
  }

  function balanceStrip() {
    return `<section class="aa-balance-strip" aria-label="Live running bank balance">
      <div class="aa-balance-copy">
        <strong>Live running balance</strong>
        <span>Starting balance + pushed sales profit − manual expenses − bank charges. Unpushed staged orders never move it.</span>
      </div>
      <div class="aa-balance-cards">${balanceCard("NGN")}${balanceCard("CFA")}</div>
    </section>`;
  }

  /* ------------------------------------------------------------ google bar */

  function googleActions() {
    const google = state.google || {};
    const ledgers = google.ledgers || {};
    const reference = referenceSheetUrl();
    if (google.connected) {
      return `
        ${reference ? `<a class="aa-button aa-button-sheet" href="${html(reference)}" target="_blank" rel="noopener noreferrer">↗ Open reference sheet</a>` : ""}
        <a class="aa-button aa-button-sheet" href="${html((ledgers.NGN || {}).url || "#")}" target="_blank" rel="noopener noreferrer">↗ NGN ledger</a>
        <a class="aa-button aa-button-sheet" href="${html((ledgers.CFA || {}).url || "#")}" target="_blank" rel="noopener noreferrer">↗ FCFA ledger</a>
        ${state.error && google.configured ? `<a class="aa-button aa-button-primary" href="/api/admin/accounting/google/connect">Reconnect Google Sheets</a>` : ""}
        <span class="aa-drive-status is-connected"><i aria-hidden="true"></i> Drive connected · ${html(google.email || "Google account")}</span>`;
    }
    const connect = google.configured
      ? `<a class="aa-button aa-button-primary" href="/api/admin/accounting/google/connect">Connect Google Sheets</a>`
      : `<span class="aa-setup-hint">Google Sheets setup is required on the server.</span>`;
    return `
      <button class="aa-button aa-button-sheet" type="button" disabled>↗ Reference sheet</button>
      <button class="aa-button aa-button-sheet" type="button" disabled>↗ NGN ledger</button>
      <button class="aa-button aa-button-sheet" type="button" disabled>↗ FCFA ledger</button>
      ${connect}
      <span class="aa-drive-status"><i aria-hidden="true"></i> ${html(google.message || "Google Drive is not connected")}</span>`;
  }

  function referenceSheetUrl() {
    const id = String((state.settings || {}).referenceSpreadsheetId || "").trim();
    return id ? `https://docs.google.com/spreadsheets/d/${encodeURIComponent(id)}/edit` : "";
  }

  /* --------------------------------------------------------- staging queue */

  function stagedEntries() {
    return state.entries.filter((entry) => entry.currency === state.currency);
  }

  function stageTotals() {
    const rows = stagedEntries();
    const revenue = rows.reduce((sum, row) => sum + Number(row.saleAmount || 0), 0);
    const supplier = rows.reduce((sum, row) => sum + Number(row.supplierCostInCurrency || 0), 0);
    const transport = rows.reduce((sum, row) => sum + Number(row.deliveryExpense || 0), 0);
    return { count: rows.length, revenue, supplier, transport, profit: revenue - supplier - transport };
  }

  function entryRow(entry) {
    const id = String(entry.id || "");
    const checked = state.selected.has(id) ? " checked" : "";
    const cfaNote = entry.currency === "CFA"
      ? `<small class="aa-stage-cfa">≈ FCFA ${Number(entry.supplierCostCfa || 0).toLocaleString("en")} @ ${Number(entry.exchangeRate || 0)}</small>`
      : `<small class="aa-stage-cfa">rate ${Number(entry.exchangeRate || 0)}</small>`;
    const profit = Number(entry.netCashProfit || 0);
    return `
      <tr class="aa-stage-row${entry.deleted ? " is-deleted" : ""}" data-id="${html(id)}">
        <td class="aa-stage-check"><input type="checkbox" data-stage-select="${html(id)}"${checked} aria-label="Select order ${html(id)}" /></td>
        <td class="aa-stage-date"><span>${html(String(entry.date || "").slice(0, 10))}</span><small>${html(id)}</small></td>
        <td class="aa-stage-customer">${html(entry.customer || "Customer")}</td>
        <td class="aa-stage-items">${html(entry.itemsSummary || "—")}</td>
        <td class="aa-stage-sale">${html(money(entry.saleAmount, entry.currency))}</td>
        <td class="aa-stage-cost">
          <input type="number" min="0" step="1" inputmode="numeric" value="${Number(entry.supplierCostNgn || 0)}"
                 data-stage-edit="supplierCostNgn" data-id="${html(id)}"
                 aria-label="NGN supplier cost for ${html(entry.customer)}" />
          ${cfaNote}
        </td>
        <td class="aa-stage-transport">
          <input type="number" min="0" step="1" inputmode="numeric" value="${Number(entry.deliveryExpense || 0)}"
                 data-stage-edit="deliveryExpense" data-id="${html(id)}"
                 aria-label="Transport for ${html(entry.customer)}" />
        </td>
        <td class="aa-stage-profit${profit < 0 ? " is-negative" : ""}">${html(money(profit, entry.currency))}</td>
        <td class="aa-stage-actions">
          <button type="button" class="aa-link-button" data-stage-notes="${html(id)}" title="Edit notes">📝</button>
          <button type="button" class="aa-link-button aa-danger" data-stage-remove="${html(id)}" title="Remove from accounting">✕</button>
        </td>
      </tr>`;
  }

  function stageSection() {
    const all = stagedEntries();
    const visible = all.slice(0, state.shown);
    const totals = stageTotals();
    const allSelected = all.length > 0 && all.every((entry) => state.selected.has(String(entry.id)));
    return `
      <section class="aa-stage" aria-label="Staged confirmed orders">
        <header class="aa-stage-head">
          <div>
            <span class="aa-eyebrow">CONFIRMED ORDERS WAITING TO BE PUSHED</span>
            <h2>Staging queue · ${html(state.currency === "NGN" ? "Naira" : "FCFA")}</h2>
            <p>${totals.count} order${totals.count === 1 ? "" : "s"} · revenue ${html(money(totals.revenue))} · supplier ${html(money(totals.supplier))} · transport ${html(money(totals.transport))} · net ${html(money(totals.profit))}</p>
          </div>
          <div class="aa-stage-actionsbar">
            <label class="aa-stage-all"><input type="checkbox" data-stage-all ${allSelected ? "checked" : ""} /> Select all</label>
            <button class="aa-button aa-button-primary" type="button" data-action="push" ${state.selected.size ? "" : "disabled"}${state.pushing ? " disabled" : ""}>
              ${state.pushing ? "Pushing…" : `⬆ Push ${state.selected.size || ""} to Google Sheet`}
            </button>
          </div>
        </header>
        ${all.length ? `
        <div class="aa-table-wrap">
          <table class="aa-stage-table">
            <thead>
              <tr>
                <th></th><th>Date / Order</th><th>Customer</th><th>Items &amp; quantities</th>
                <th>Selling price</th><th>Supplier cost (₦)</th><th>Transport</th><th>Net profit</th><th></th>
              </tr>
            </thead>
            <tbody>${visible.map(entryRow).join("")}</tbody>
          </table>
        </div>
        ${all.length > state.shown ? `<button type="button" class="aa-button aa-button-more" data-action="more">Show ${Math.min(STAGE_PAGE_SIZE, all.length - state.shown)} more (${all.length - state.shown} hidden)</button>` : ""}
        <p class="aa-sheet-hint">Supplier costs are editable right here — type the cost of custom or unlinked items and it saves instantly. Pushed orders leave this queue and land in your ${html(state.currency === "NGN" ? "NGN" : "FCFA")} tab; history and profit analytics live on the <a href="/admin.html?tab=sales">Sales page</a>.</p>`
        : `<p class="aa-stage-empty">The ${html(state.currency === "NGN" ? "naira" : "FCFA")} queue is clean — every confirmed order has been pushed. New confirmations will appear here.</p>`}
      </section>`;
  }

  /* ------------------------------------------------------ expense logger */

  function expenseSection() {
    const rows = (state.expenses || []).slice(0, 12);
    return `
      <section class="aa-expenses" aria-label="Manual purchase and payout logger">
        <header class="aa-stage-head">
          <div>
            <span class="aa-eyebrow">MANUAL PURCHASE / PAYOUT LOGGER</span>
            <h2>Expenses &amp; charges</h2>
            <p>Record bulk stock bought ahead of sales (Ankara, perfumes…), owner payouts, and bank transfer / withdrawal charges (e.g. the ₦100 fee per transaction).</p>
          </div>
        </header>
        <form class="aa-expense-form" data-expense-form>
          <label>Type
            <select name="kind">
              <option value="purchase">Stock purchase</option>
              <option value="payout">Payout / withdrawal</option>
              <option value="fee">Bank transfer fee</option>
            </select>
          </label>
          <label>Currency
            <select name="currency">
              <option value="NGN">₦ NGN</option>
              <option value="CFA">FCFA</option>
            </select>
          </label>
          <label>Amount<input name="amount" type="number" min="1" step="1" inputmode="numeric" required placeholder="0" /></label>
          <label class="aa-expense-note">Note<input name="note" maxlength="120" placeholder="e.g. 12 yards of Ankara" /></label>
          <button class="aa-button aa-button-primary" type="submit">Log expense</button>
        </form>
        ${rows.length ? `
        <div class="aa-table-wrap">
          <table class="aa-stage-table aa-expense-table">
            <thead><tr><th>Date</th><th>Type</th><th>Currency</th><th>Amount</th><th>Note</th><th></th></tr></thead>
            <tbody>${rows.map((row) => `
              <tr>
                <td>${html(String(row.at || "").slice(0, 16).replace("T", " "))}</td>
                <td><span class="aa-expense-kind is-${html(row.kind)}">${html(kindLabel(row.kind))}</span></td>
                <td>${html(row.currency)}</td>
                <td>${html(money(row.amount, row.currency))}</td>
                <td class="aa-expense-notecell">${html(row.note || "—")}</td>
                <td><button type="button" class="aa-link-button aa-danger" data-expense-del="${html(row.id)}" title="Delete this record">✕</button></td>
              </tr>`).join("")}</tbody>
          </table>
        </div>` : `<p class="aa-stage-empty">No manual expenses recorded yet.</p>`}
      </section>`;
  }

  function kindLabel(kind) {
    return { purchase: "Stock purchase", payout: "Payout", fee: "Bank fee" }[kind] || kind;
  }

  /* ------------------------------------------------------------- settings */

  function settingsSection() {
    const s = state.settings || {};
    return `
      <details class="aa-settings">
        <summary>Accounting settings · starting balances &amp; reference sheet</summary>
        <form class="aa-settings-form" data-settings-form>
          <label>Starting NGN bank balance (₦)
            <input name="startingBalanceNgn" type="number" min="0" step="1" inputmode="numeric" value="${Number(s.startingBalanceNgn || 0)}" />
          </label>
          <label>Starting FCFA bank balance
            <input name="startingBalanceCfa" type="number" min="0" step="1" inputmode="numeric" value="${Number(s.startingBalanceCfa || 0)}" />
          </label>
          <label class="aa-settings-ref">Reference spreadsheet (ITEMFLOW) URL or ID — NGN / FCFA tabs
            <input name="referenceSpreadsheetId" maxlength="200" value="${html(s.referenceSpreadsheetId || "")}" placeholder="https://docs.google.com/spreadsheets/d/…" />
            <small>Leave as the default to route pushes to its NGN and FCFA tabs. Clear it to push to the app-created ledgers instead.</small>
          </label>
          <button class="aa-button aa-button-primary" type="submit">Save settings</button>
        </form>
        <p class="aa-sheet-hint">Active NGN → FCFA exchange rate: <strong>${Number(state.currentExchangeRate || 0)}</strong> — manage it in <a href="/admin.html?tab=settings">Admin → Settings</a> (Store Settings, next to the bank details). Each confirmed order locks in the rate that was active when it was confirmed, so history never reprices.</p>
      </details>`;
  }

  /* ------------------------------------------------------------- pushing */

  function pushDialog() {
    const count = state.selected.size;
    const currencies = [...new Set(stagedEntries()
      .filter((entry) => state.selected.has(String(entry.id)))
      .map((entry) => entry.currency))];
    const defaultFeeCurrency = currencies.length === 1 ? currencies[0] : "NGN";
    return `
      <div class="aa-push-dialog" data-push-dialog hidden>
        <div class="aa-push-card" role="dialog" aria-modal="true" aria-label="Push to Google Sheet">
          <h3>Push ${count} order${count === 1 ? "" : "s"} to Google Sheet</h3>
          <p>NGN rows are routed to the <strong>NGN</strong> tab and FCFA rows to the <strong>FCFA</strong> tab${referenceSheetUrl() ? " of your reference sheet" : " of your ledgers"}. Pushed orders are archived into the batch below and cleared off this queue.</p>
          <label>Batch / delivery name
            <input name="name" maxlength="100" placeholder="${html(state.currency === "NGN" ? "NGN" : "FCFA")} delivery · ${new Date().toISOString().slice(0, 10)}" />
          </label>
          <div class="aa-push-row">
            <label>Bank transfer / withdrawal fee
              <input name="transferFee" type="number" min="0" step="1" inputmode="numeric" value="0" />
            </label>
            <label>Fee currency
              <select name="feeCurrency">
                <option value="NGN"${defaultFeeCurrency === "NGN" ? " selected" : ""}>₦ NGN</option>
                <option value="CFA"${defaultFeeCurrency === "CFA" ? " selected" : ""}>FCFA</option>
              </select>
            </label>
          </div>
          <p class="aa-push-error" data-push-error hidden></p>
          <div class="aa-push-buttons">
            <button type="button" class="aa-button" data-push-cancel>Cancel</button>
            <button type="button" class="aa-button aa-button-primary" data-push-confirm>Push to Google Sheet</button>
          </div>
        </div>
      </div>`;
  }

  async function confirmPush() {
    const dialog = root.querySelector("[data-push-dialog]");
    const errorBox = dialog && dialog.querySelector("[data-push-error]");
    const nameInput = dialog && dialog.querySelector('[name="name"]');
    const feeInput = dialog && dialog.querySelector('[name="transferFee"]');
    const feeCurrency = dialog && dialog.querySelector('[name="feeCurrency"]');
    if (errorBox) errorBox.hidden = true;
    state.pushing = true;
    render();
    try {
      const data = await request("/api/admin/accounting/push", {
        method: "POST",
        body: JSON.stringify({
          orderIds: [...state.selected],
          name: (nameInput && nameInput.value) || "",
          transferFee: Number((feeInput && feeInput.value) || 0),
          feeCurrency: feeCurrency ? feeCurrency.value : "",
        }),
      });
      state.selected.clear();
      toast(`Pushed ${data.pushed.length} order${data.pushed.length === 1 ? "" : "s"} — cleared off the queue.`, "ok");
      await loadAll();
    } catch (error) {
      if (errorBox) {
        errorBox.textContent = error.message || "The push did not complete.";
        errorBox.hidden = false;
      }
      toast(error.message || "The push did not complete.", "error");
    } finally {
      state.pushing = false;
      const box = root.querySelector("[data-push-dialog]");
      if (box) box.hidden = true;
      render();
    }
  }

  /* --------------------------------------------------------------- render */

  function render() {
    const google = state.google || {};
    const currencyName = state.currency === "NGN" ? "Naira" : "FCFA";
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
              <span class="aa-eyebrow">A CLEAN VIEW OF YOUR CASH</span>
              <h1>Accounting</h1>
              <p>Confirmed orders stage here with their items, quantities and editable costs. Push them to Google Sheets in named batches — the queue clears the moment they land, so nothing is ever counted twice.</p>
            </div>
            <div class="aa-current-ledger"><span>Ledger</span><strong>${html(currencyName)}</strong></div>
          </section>

          <section class="aa-filter-panel" aria-label="Accounting filters">
            <div class="aa-currency-switch" role="group" aria-label="Currency">
              <button type="button" data-currency="NGN" class="${state.currency === "NGN" ? "is-active" : ""}" aria-pressed="${state.currency === "NGN"}">₦ <span>NGN</span></button>
              <button type="button" data-currency="CFA" class="${state.currency === "CFA" ? "is-active" : ""}" aria-pressed="${state.currency === "CFA"}">C <span>FCFA</span></button>
            </div>
            <div class="aa-filter-divider" aria-hidden="true"></div>
            <span class="aa-rate-pill" title="Active NGN → FCFA rate from Store Settings">1 ₦ = ${Number(state.currentExchangeRate || 0)} FCFA</span>
          </section>

          ${balanceStrip()}

          <section class="aa-sheet-strip" aria-label="Google Sheets ledgers">
            <div class="aa-sheet-copy"><strong>Your ledgers</strong><span>Saved in the store owner's Google Drive</span></div>
            <div class="aa-sheet-actions">${googleActions()}</div>
          </section>
          ${connectedMessage}
          ${readError}

          ${stageSection()}
          ${expenseSection()}
          ${settingsSection()}
          ${pushDialog()}
          <footer class="aa-footer">Jaura Store · Private accounting desk · Amounts shown in whole ${html(state.currency)} units</footer>
        </main>
      </div>`;
  }

  function oauthNotice() {
    const params = new URLSearchParams(window.location.search);
    const result = params.get("google");
    if (!result) return;
    const messages = {
      connected: "Google Drive connected. Your ledgers are ready; existing orders are syncing.",
      "not-configured": "Google Sheets has not been configured on the server yet.",
      login: "Sign in to the admin account, then connect Google Drive.",
      "state-error": "The Google sign-in expired or could not be verified. Please try again.",
      cancelled: "Google Drive was not connected.",
      failed: "Google could not finish the connection. Check the OAuth setup and retry.",
    };
    if (messages[result]) toast(messages[result], result === "connected" ? "ok" : "error");
    window.history.replaceState({}, "", window.location.pathname);
  }

  async function loadAll() {
    const requestId = ++state.requestSeq;
    state.loading = true;
    state.error = "";
    render();
    try {
      const data = await request("/api/admin/accounting");
      if (requestId !== state.requestSeq) return;
      state.entries = Array.isArray(data.entries) ? data.entries : [];
      state.expenses = Array.isArray(data.expenses) ? data.expenses : [];
      state.settings = data.settings || state.settings;
      state.balances = data.balances || { NGN: null, CFA: null };
      state.google = data.google || state.google;
      state.currentExchangeRate = Number(data.currentExchangeRate || state.currentExchangeRate);
      // Selections that no longer match a staged order are dropped quietly.
      const staged = new Set(state.entries.map((entry) => String(entry.id)));
      [...state.selected].forEach((id) => { if (!staged.has(id)) state.selected.delete(id); });
    } catch (error) {
      if (requestId !== state.requestSeq) return;
      if (error.status === 401) {
        state.email = "";
        state.google = { configured: false, connected: false, ledgers: {} };
        showLogin("Your admin session has expired. Please sign in again.");
        return;
      }
      state.error = error.message || "The accounting queue could not be loaded.";
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
      await loadAll();
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
      await loadAll();
    } catch (error) {
      state.email = email;
      showLogin(error.message || "Sign in failed.");
    }
  }

  async function logout() {
    try {
      await request("/api/admin/logout", { method: "POST", body: "{}" });
    } catch (_) { /* discard the local view even if sign-out could not sync */ }
    state.email = "";
    showLogin("You have signed out of the accounting desk.");
  }

  async function saveEntryField(id, field, value) {
    try {
      const data = await request(`/api/admin/accounting/orders/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ [field]: value }),
      });
      const entry = data.entry;
      if (entry) {
        const index = state.entries.findIndex((row) => String(row.id) === String(id));
        if (index >= 0) state.entries[index] = entry;
      }
      render();
    } catch (error) {
      toast(error.message || "Could not save that edit.", "error");
      render();
    }
  }

  async function editNotes(id) {
    const entry = state.entries.find((row) => String(row.id) === String(id));
    const current = (entry && entry.notes) || "";
    const next = window.prompt("Notes for this order (shown in the sheet):", current);
    if (next === null) return;
    await saveEntryField(id, "notes", next);
  }

  async function removeEntry(id) {
    if (!window.confirm("Remove this order from the accounting queue? The order record itself is kept; it just stops being staged.")) return;
    try {
      await request(`/api/admin/accounting/orders/${encodeURIComponent(id)}`, { method: "DELETE" });
      state.entries = state.entries.filter((row) => String(row.id) !== String(id));
      state.selected.delete(String(id));
      toast("Removed from the accounting queue.");
      render();
    } catch (error) {
      toast(error.message || "Could not remove that record.", "error");
    }
  }

  /* ------------------------------------------------------------- events */

  root.addEventListener("submit", (event) => {
    const loginForm = event.target.closest("[data-login]");
    if (loginForm) {
      event.preventDefault();
      login(loginForm);
      return;
    }
    const expenseForm = event.target.closest("[data-expense-form]");
    if (expenseForm) {
      event.preventDefault();
      const fd = new FormData(expenseForm);
      const button = expenseForm.querySelector("button[type=submit]");
      if (button) button.disabled = true;
      request("/api/admin/accounting/expenses", {
        method: "POST",
        body: JSON.stringify({
          kind: fd.get("kind"),
          currency: fd.get("currency"),
          amount: Number(fd.get("amount") || 0),
          note: String(fd.get("note") || ""),
        }),
      }).then((data) => {
        state.expenses = Array.isArray(data.expenses) ? data.expenses : state.expenses;
        state.balances = data.balances || state.balances;
        toast("Expense logged.");
        render();
      }).catch((error) => {
        toast(error.message || "Could not log that expense.", "error");
        if (button) button.disabled = false;
      });
      return;
    }
    const settingsForm = event.target.closest("[data-settings-form]");
    if (settingsForm) {
      event.preventDefault();
      const fd = new FormData(settingsForm);
      const reference = String(fd.get("referenceSpreadsheetId") || "").trim();
      request("/api/admin/accounting/settings", {
        method: "PUT",
        body: JSON.stringify({
          startingBalanceNgn: Number(fd.get("startingBalanceNgn") || 0),
          startingBalanceCfa: Number(fd.get("startingBalanceCfa") || 0),
          referenceSpreadsheetId: extractSpreadsheetId(reference),
        }),
      }).then((data) => {
        state.settings = data.settings || state.settings;
        state.balances = data.balances || state.balances;
        toast("Accounting settings saved.");
        render();
      }).catch((error) => {
        toast(error.message || "Could not save the settings.", "error");
      });
    }
  });

  function extractSpreadsheetId(value) {
    const text = String(value || "").trim();
    const match = text.match(/\/spreadsheets\/d\/([a-zA-Z0-9_-]+)/);
    return match ? match[1] : text;
  }

  root.addEventListener("click", (event) => {
    const currency = event.target.closest("[data-currency]");
    if (currency) {
      const next = currency.dataset.currency;
      if (next && next !== state.currency) {
        state.currency = next;
        state.shown = STAGE_PAGE_SIZE;
        render();
      }
      return;
    }
    const select = event.target.closest("[data-stage-select]");
    if (select) {
      const id = select.dataset.stageSelect;
      if (select.checked) state.selected.add(id); else state.selected.delete(id);
      render();
      return;
    }
    const selectAll = event.target.closest("[data-stage-all]");
    if (selectAll) {
      const staged = stagedEntries();
      if (selectAll.checked) staged.forEach((entry) => state.selected.add(String(entry.id)));
      else staged.forEach((entry) => state.selected.delete(String(entry.id)));
      render();
      return;
    }
    const notes = event.target.closest("[data-stage-notes]");
    if (notes) { editNotes(notes.dataset.stageNotes); return; }
    const remove = event.target.closest("[data-stage-remove]");
    if (remove) { removeEntry(remove.dataset.stageRemove); return; }
    const expenseDel = event.target.closest("[data-expense-del]");
    if (expenseDel) {
      const id = expenseDel.dataset.expenseDel;
      request(`/api/admin/accounting/expenses/${encodeURIComponent(id)}`, { method: "DELETE" })
        .then((data) => {
          state.expenses = Array.isArray(data.expenses) ? data.expenses : state.expenses;
          state.balances = data.balances || state.balances;
          toast("Expense record deleted.");
          render();
        })
        .catch((error) => toast(error.message || "Could not delete that record.", "error"));
      return;
    }
    const pushConfirm = event.target.closest("[data-push-confirm]");
    if (pushConfirm) { confirmPush(); return; }
    const pushCancel = event.target.closest("[data-push-cancel]");
    if (pushCancel) {
      const box = root.querySelector("[data-push-dialog]");
      if (box) box.hidden = true;
      return;
    }
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "push") {
      if (!state.selected.size) return;
      const box = root.querySelector("[data-push-dialog]");
      if (box) {
        box.hidden = false;
        const errorBox = box.querySelector("[data-push-error]");
        if (errorBox) errorBox.hidden = true;
        const name = box.querySelector('[name="name"]');
        if (name) name.focus();
      }
    } else if (action === "more") {
      state.shown += STAGE_PAGE_SIZE;
      render();
    } else if (action === "refresh") {
      loadAll().then(() => toast("Queue refreshed.")).catch((error) => toast(error.message, "error"));
    } else if (action === "logout") {
      logout();
    }
  });

  // Inline cost edits save the moment the owner leaves the field.
  root.addEventListener("change", (event) => {
    const edit = event.target.closest("[data-stage-edit]");
    if (edit) {
      saveEntryField(edit.dataset.id, edit.dataset.stageEdit, Number(edit.value || 0));
    }
  });

  start();
})();
