/* Staged dual-currency accounting desk.
 *
 * The desk is a STAGING queue: confirmed orders land here as itemized rows
 * (customer, items, quantities, selling price, editable supplier link +
 * unit supplier price + discount + per-order transport). Every edit saves
 * itself and the Net Profit recalculates instantly:
 *
 *   Net Profit = Selling Price - Applied Discounts - Total Supplier Cost
 *                - Delivery / Transport Fee
 *   Total Supplier Cost = Unit Supplier Price x Quantity
 *
 * The Starting Profit / Opening Balance input box at the top is added once to
 * the running bank balance, so each new batch's net profit accumulates on top
 * of it. A single Batch Transportation Fee box covers a whole delivery: fill
 * it in and that one figure is logged for the batch; leave it blank and the
 * individual per-order transport fees are used instead. Historical profit
 * analytics live on the Sales page, not here.
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
    batchTransportFee: "",
    verify: null,
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

  /* The live formula the server also applies, so a row recalculates the
   * instant a figure is typed - before the (debounced) save lands:
   *   Total Supplier Cost = Unit Supplier Price x Quantity
   *   Net Profit = Selling Price - Discounts - Total Supplier Cost - Transport
   */
  function entrySupplierTotal(entry) {
    // A CFA order shows its supplier cost converted at the ledger's rate.
    return Number(entry.currency === "CFA" ? (entry.supplierCostCfa || 0)
                                            : (entry.supplierCostNgn || 0));
  }

  function entryFigures(entry) {
    const sale = Number(entry.saleAmount || 0);
    const discount = Number(entry.discount || 0);
    const supplier = entrySupplierTotal(entry);
    const transport = Number(entry.deliveryExpense || 0);
    const netProfit = sale - discount - supplier;
    return { sale, discount, supplier, transport, netProfit,
             netCashProfit: netProfit - transport };
  }

  function rowFigures(row, entry) {
    // Read what is on screen right now; fall back to the saved entry.
    const field = (name, fallback) => {
      const input = row && row.querySelector(`[data-stage-edit="${name}"]`);
      if (!input || input.value === "") return Number(fallback || 0);
      const value = Number(input.value);
      return Number.isFinite(value) ? value : Number(fallback || 0);
    };
    const currency = entry.currency || state.currency;
    const unit = field("supplierUnitPriceNgn", entry.supplierUnitPriceNgn);
    const qty = field("supplierQty", entry.supplierQty || entry.itemQuantity) || 1;
    const totalNgn = Math.round(unit * qty);
    const rate = Number(entry.exchangeRate || state.currentExchangeRate || 0);
    const supplier = currency === "CFA" ? Math.round(totalNgn * rate) : totalNgn;
    const sale = field("saleAmount", entry.saleAmount);
    const discount = field("discount", entry.discount);
    const transport = field("deliveryExpense", entry.deliveryExpense);
    const netProfit = sale - discount - supplier;
    return { currency, unit, qty, totalNgn, supplier, sale, discount, transport,
             netProfit, netCashProfit: netProfit - transport };
  }

  function entryQuantity(entry) {
    return Number(entry.supplierQty || 0) || Number(entry.itemQuantity || 0) || 1;
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
          <div><dt>Starting profit / opening balance</dt><dd>${html(money(b.startingBalance, currency))}</dd></div>
          <div><dt>+ Pushed sales net profit</dt><dd>${html(money(b.salesNetProfit, currency))}</dd></div>
          <div><dt>− Manual expenses</dt><dd>${html(money(b.manualExpenses, currency))}</dd></div>
          <div><dt>− Bank / transfer charges</dt><dd>${html(money(b.bankCharges, currency))}</dd></div>
        </dl>
      </article>`;
  }

  function balanceStrip() {
    return `<section class="aa-balance-strip" aria-label="Live running bank balance">
      <div class="aa-balance-copy">
        <strong>Live running balance</strong>
        <span>Starting profit / opening balance + every batch's net profit − manual expenses − bank charges. Unpushed staged orders never move it.</span>
      </div>
      <div class="aa-balance-cards">${balanceCard("NGN")}${balanceCard("CFA")}</div>
    </section>`;
  }

  /* ------------------------------------------- starting profit / opening */
  /* The input box at the top of the desk. Whatever the owner enters here is
   * the Starting Profit / Opening Balance: every new batch net profit
   * accumulates on top of it, and it never moves when a batch is pushed. */

  function openingSection() {
    const s = state.settings || {};
    const ngn = Number(s.startingBalanceNgn || 0);
    const cfa = Number(s.startingBalanceCfa || 0);
    const active = state.currency === "NGN" ? ngn : cfa;
    return `
      <section class="aa-opening" aria-label="Starting profit and opening balance">
        <div class="aa-opening-copy">
          <span class="aa-eyebrow">STARTING PROFIT / OPENING BALANCE</span>
          <strong id="aa-opening-active">${html(money(active, state.currency))}</strong>
          <span>New batch net profits accumulate on top of this figure. Set it once — it is never overwritten by a push.</span>
        </div>
        <form class="aa-opening-form" data-opening-form>
          <label>₦ NGN starting profit
            <input name="startingBalanceNgn" type="number" min="0" step="1" inputmode="numeric"
                   value="${ngn}" data-autosave="opening" aria-label="NGN starting profit or opening balance" />
          </label>
          <label>FCFA opening balance
            <input name="startingBalanceCfa" type="number" min="0" step="1" inputmode="numeric"
                   value="${cfa}" data-autosave="opening" aria-label="FCFA starting profit or opening balance" />
          </label>
          <button class="aa-button aa-button-primary" type="submit">Save opening balance</button>
        </form>
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
        <button class="aa-button aa-button-line" type="button" data-action="verify-google">✓ Test Google sync</button>
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
      ${google.configured ? `<button class="aa-button aa-button-line" type="button" data-action="verify-google">✓ Test Google sync</button>` : ""}
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
    const discounts = rows.reduce((sum, row) => sum + Number(row.discount || 0), 0);
    const supplier = rows.reduce((sum, row) => sum + entrySupplierTotal(row), 0);
    const perOrder = rows.reduce((sum, row) => sum + Number(row.deliveryExpense || 0), 0);
    // The Batch Transportation Fee wins when it is filled in; blank falls back
    // to the individual per-order transport fees.
    const batchFee = batchTransportFee();
    const transport = batchFee === null ? perOrder : batchFee;
    const profit = revenue - discounts - supplier - transport;
    return { count: rows.length, revenue, discounts, supplier, perOrder,
             transport, transportSource: batchFee === null ? "per-order" : "batch",
             profit, batchFee };
  }

  /** The Batch Transportation Fee box: null means "left blank". */
  function batchTransportFee() {
    const raw = String(state.batchTransportFee ?? "").trim();
    if (raw === "") return null;
    const value = Number(raw);
    return Number.isFinite(value) && value >= 0 ? Math.round(value) : null;
  }

  function entryRow(entry) {
    const id = String(entry.id || "");
    const checked = state.selected.has(id) ? " checked" : "";
    const figures = entryFigures(entry);
    const qty = entryQuantity(entry);
    const rate = Number(entry.exchangeRate || 0);
    const discount = Number(entry.discount || 0);
    const cfaNote = entry.currency === "CFA"
      ? `<small class="aa-stage-cfa">≈ FCFA ${Number(entry.supplierCostCfa || 0).toLocaleString("en")} @ ${rate}</small>`
      : `<small class="aa-stage-cfa">rate ${rate}</small>`;
    // The discount field stays HIDDEN at ₦0/FCFA 0 (a clean view); the small
    // "+ discount" button opens it when the owner applies one.
    const discountCell = discount > 0
      ? `<input type="number" min="0" step="1" inputmode="numeric" value="${discount}"
                data-stage-edit="discount" data-id="${html(id)}"
                aria-label="Discount for ${html(entry.customer)}" />
         <small class="aa-stage-cfa">${Number(entry.discountPercent || 0) ? `${Number(entry.discountPercent)}% off` : "applied"}</small>`
      : `<button type="button" class="aa-link-button aa-discount-add" data-stage-discount="${html(id)}"
                 title="Apply a discount to this order">＋ discount</button>`;
    return `
      <tr class="aa-stage-row${entry.deleted ? " is-deleted" : ""}" data-id="${html(id)}">
        <td class="aa-stage-check"><input type="checkbox" data-stage-select="${html(id)}"${checked} aria-label="Select order ${html(id)}" /></td>
        <td class="aa-stage-date"><span>${html(String(entry.date || "").slice(0, 10))}</span><small>${html(id)}</small></td>
        <td class="aa-stage-customer">${html(entry.customer || "Customer")}</td>
        <td class="aa-stage-items">${html(entry.itemsSummary || "—")}<small class="aa-stage-cfa">qty ${qty}</small></td>
        <td class="aa-stage-sale">
          <input type="number" min="0" step="1" inputmode="numeric" value="${Number(entry.saleAmount || 0)}"
                 data-stage-edit="saleAmount" data-id="${html(id)}"
                 aria-label="Selling price for ${html(entry.customer)}" />
        </td>
        <td class="aa-stage-cost">
          <input type="text" class="aa-stage-link" maxlength="500" placeholder="Supplier link (optional)"
                 value="${html(entry.supplierLink || "")}"
                 data-stage-edit="supplierLink" data-id="${html(id)}"
                 aria-label="Supplier link for ${html(entry.customer)}" />
          <div class="aa-cost-line">
            <input type="number" min="0" step="1" inputmode="numeric" value="${Number(entry.supplierUnitPriceNgn || 0)}"
                   data-stage-edit="supplierUnitPriceNgn" data-id="${html(id)}" data-cost-unit
                   aria-label="Unit supplier price (NGN) for ${html(entry.customer)}" />
            <span aria-hidden="true">×</span>
            <input type="number" min="1" step="1" inputmode="numeric" value="${qty}"
                   data-stage-edit="supplierQty" data-id="${html(id)}" data-cost-qty
                   aria-label="Supplier quantity for ${html(entry.customer)}" />
          </div>
          <small class="aa-stage-cfa">total <b data-cost-total>${html(money(figures.supplier, entry.currency))}</b></small>
          ${cfaNote}
        </td>
        <td class="aa-stage-discount" data-discount-cell>${discountCell}</td>
        <td class="aa-stage-transport">
          <input type="number" min="0" step="1" inputmode="numeric" value="${Number(entry.deliveryExpense || 0)}"
                 data-stage-edit="deliveryExpense" data-id="${html(id)}"
                 aria-label="Transport for ${html(entry.customer)}" />
        </td>
        <td class="aa-stage-profit${figures.netProfit < 0 ? " is-negative" : ""}" data-row-profit>${html(money(figures.netProfit, entry.currency))}</td>
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
            <p data-stage-summary>${stageSummaryHTML()}</p>
          </div>
          <div class="aa-stage-actionsbar">
            <label class="aa-stage-all"><input type="checkbox" data-stage-all ${allSelected ? "checked" : ""} /> Select all</label>
            <label class="aa-batch-transport">Batch transportation fee (whole batch)
              <input type="number" min="0" step="1" inputmode="numeric" data-batch-transport
                     value="${html(state.batchTransportFee)}" placeholder="blank = per-order fees" />
              <small>${totals.transportSource === "batch"
                ? `One ${html(state.currency)} cost for the whole batch · per-order fees ignored`
                : `Per-order transport fees are being added up (${html(money(totals.perOrder, state.currency))})`}</small>
            </label>
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
                <th>Selling price</th><th>Supplier link · unit ₦ × qty</th><th>Discount</th>
                <th>Transport</th><th>Net profit</th><th></th>
              </tr>
            </thead>
            <tbody>${visible.map(entryRow).join("")}</tbody>
          </table>
        </div>
        ${all.length > state.shown ? `<button type="button" class="aa-button aa-button-more" data-action="more">Show ${Math.min(STAGE_PAGE_SIZE, all.length - state.shown)} more (${all.length - state.shown} hidden)</button>` : ""}
        <p class="aa-sheet-hint">Everything here saves itself: paste the supplier link, type the unit price and quantity (Unit Price × Quantity = Total Supplier Cost), add a discount, and the Net Profit recalculates instantly — Selling Price − Discounts − Supplier Cost − Transport. For FCFA orders the NGN supplier cost is converted with the active NGN → FCFA rate. Pushed orders leave this queue and land in your ${html(state.currency === "NGN" ? "NGN" : "FCFA")} tab; history and profit analytics live on the <a href="/admin.html?tab=sales">Sales page</a>.</p>`
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
        <summary>Accounting settings · starting profit, opening balance &amp; reference sheet</summary>
        <form class="aa-settings-form" data-settings-form>
          <label>Starting profit / opening NGN balance (₦)
            <input name="startingBalanceNgn" type="number" min="0" step="1" inputmode="numeric" value="${Number(s.startingBalanceNgn || 0)}" />
          </label>
          <label>Starting profit / opening FCFA balance
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
            <label>Batch transportation fee <span>whole batch · optional</span>
              <input name="batchTransportFee" type="number" min="0" step="1" inputmode="numeric"
                     value="${html(state.batchTransportFee)}" placeholder="blank = per-order fees" />
            </label>
            <label>Transport currency
              <select name="batchTransportFeeCurrency">
                <option value="NGN"${defaultFeeCurrency === "NGN" ? " selected" : ""}>₦ NGN</option>
                <option value="CFA"${defaultFeeCurrency === "CFA" ? " selected" : ""}>FCFA</option>
              </select>
            </label>
          </div>
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
          <p class="aa-sheet-hint">Leave the batch transportation fee blank and the individual per-order transport fees are used instead.</p>
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
    const batchFeeInput = dialog && dialog.querySelector('[name="batchTransportFee"]');
    const batchFeeCurrency = dialog && dialog.querySelector('[name="batchTransportFeeCurrency"]');
    if (errorBox) errorBox.hidden = true;
    state.pushing = true;
    if (batchFeeInput) state.batchTransportFee = String(batchFeeInput.value || "");
    render();
    try {
      const data = await request("/api/admin/accounting/push", {
        method: "POST",
        body: JSON.stringify({
          orderIds: [...state.selected],
          name: (nameInput && nameInput.value) || "",
          transferFee: Number((feeInput && feeInput.value) || 0),
          feeCurrency: feeCurrency ? feeCurrency.value : "",
          // Blank stays blank: the server then falls back to per-order transport.
          batchTransportFee: (batchFeeInput && String(batchFeeInput.value || "").trim()) || null,
          batchTransportFeeCurrency: batchFeeCurrency ? batchFeeCurrency.value : "",
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

          ${openingSection()}

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
    return saveEntryFields(id, { [field]: value });
  }

  /** Auto-save one order's accounting fields and repaint from the answer.

   *  ``quiet`` updates state without repainting, so a debounced save while the
   *  owner is still typing in the row never steals their cursor. */
  async function saveEntryFields(id, patch, options = {}) {
    const entry = state.entries.find((row) => String(row.id) === String(id)) || {};
    const body = { ...patch };
    // A CFA order converts its NGN supplier cost with the ACTIVE Store
    // Settings rate the moment the owner prices the item; NGN rows are
    // untouched, and a row nobody edits keeps the rate it was confirmed at.
    if (entry.currency === "CFA" || state.currency === "CFA") {
      if ("supplierUnitPriceNgn" in body || "supplierCostNgn" in body
          || "supplierQty" in body || "applyActiveRate" in body) {
        body.applyActiveRate = true;
        body.exchangeRate = Number(state.currentExchangeRate || 0);
      }
    }
    try {
      const data = await request(`/api/admin/accounting/orders/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      const saved = data.entry;
      if (saved) {
        const index = state.entries.findIndex((row) => String(row.id) === String(id));
        if (index >= 0) state.entries[index] = saved;
      }
      if (!options.quiet) render();
      return saved;
    } catch (error) {
      toast(error.message || "Could not save that edit.", "error");
      if (!options.quiet) render();
      return null;
    }
  }

  /* Debounced auto-save: typing in a row recalculates on screen immediately
   * and saves itself a moment later, so a supplier price is never lost. */
  const pendingSaves = new Map();
  function scheduleEntrySave(id, patch) {
    const key = String(id);
    const queued = pendingSaves.get(key) || {};
    pendingSaves.set(key, { ...queued, ...patch });
    window.clearTimeout(scheduleEntrySave.timers?.[key]);
    scheduleEntrySave.timers = scheduleEntrySave.timers || {};
    scheduleEntrySave.timers[key] = window.setTimeout(() => {
      const payload = pendingSaves.get(key);
      pendingSaves.delete(key);
      if (payload) saveEntryFields(key, payload, { quiet: true });
    }, 900);
  }

  /* Live recalculation while typing: the row's net profit, the cost total and
   * the section totals move before the save round-trips. */
  function recalcRow(row) {
    if (!row) return;
    const id = row.dataset.id;
    const entry = state.entries.find((item) => String(item.id) === String(id));
    if (!entry) return;
    const figures = rowFigures(row, entry);
    const total = row.querySelector("[data-cost-total]");
    if (total) total.textContent = money(figures.supplier, entry.currency);
    const profitCell = row.querySelector("[data-row-profit]");
    if (profitCell) {
      profitCell.textContent = money(figures.netProfit, entry.currency);
      profitCell.classList.toggle("is-negative", figures.netProfit < 0);
    }
    const summary = root.querySelector("[data-stage-summary]");
    if (summary) summary.innerHTML = stageSummaryHTML();
  }

  function stageSummaryHTML() {
    const totals = stageTotals();
    return `${totals.count} order${totals.count === 1 ? "" : "s"} · revenue ${html(money(totals.revenue))}`
      + (totals.discounts ? ` · discounts −${html(money(totals.discounts))}` : "")
      + ` · supplier ${html(money(totals.supplier))} · transport ${html(money(totals.transport))}`
      + ` · net ${html(money(totals.profit))}`;
  }

  async function saveOpeningBalances(patch) {
    try {
      const data = await request("/api/admin/accounting/settings", {
        method: "PUT", body: JSON.stringify(patch),
      });
      state.settings = data.settings || state.settings;
      state.balances = data.balances || state.balances;
      render();
      toast("Starting profit / opening balance saved.");
      return data;
    } catch (error) {
      toast(error.message || "Could not save the opening balance.", "error");
      return null;
    }
  }

  async function verifyGoogleSync() {
    const button = root.querySelector('[data-action="verify-google"]');
    if (button) { button.disabled = true; button.textContent = "Checking…"; }
    try {
      const data = await request("/api/admin/accounting/google/verify");
      const report = (data && data.verification) || {};
      state.verify = report;
      const steps = (report.steps || []).map((step) => `${step.ok ? "✓" : "✕"} ${step.step}${step.detail ? ` — ${step.detail}` : ""}`);
      toast(report.ok ? "Google Drive sync is working." : (report.message || "Google sync check failed."),
            report.ok ? "ok" : "error");
      if (!report.ok) {
        console.warn("[accounting] Google sync verification:", steps.join(" | "));
      }
    } catch (error) {
      toast(error.message || "Could not test the Google connection.", "error");
    } finally {
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

  /* The discount field is hidden while it is 0 - this opens it when the owner
   * applies one, accepting "10%" (a percentage of the selling price) or a
   * plain amount like 5000. */
  async function applyDiscount(id) {
    const entry = state.entries.find((row) => String(row.id) === String(id));
    if (!entry) return;
    const answer = window.prompt(
      `Discount for ${entry.customer || "this order"} — type a percentage (e.g. 10%) or an amount in ${entry.currency}:`,
      entry.discount ? String(entry.discount) : "10%");
    if (answer === null) return;
    const text = String(answer).trim();
    if (!text) return;
    if (/%$/.test(text)) {
      await saveEntryFields(id, { discountPercent: Number(text.replace("%", "").trim() || 0) });
    } else {
      await saveEntryFields(id, { discount: Number(text || 0) });
    }
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
    const openingForm = event.target.closest("[data-opening-form]");
    if (openingForm) {
      event.preventDefault();
      const fd = new FormData(openingForm);
      saveOpeningBalances({
        startingBalanceNgn: Number(fd.get("startingBalanceNgn") || 0),
        startingBalanceCfa: Number(fd.get("startingBalanceCfa") || 0),
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
    const discount = event.target.closest("[data-stage-discount]");
    if (discount) { applyDiscount(discount.dataset.stageDiscount); return; }
    const verify = event.target.closest('[data-action="verify-google"]');
    if (verify) { verifyGoogleSync(); return; }
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
        const batchFee = box.querySelector('[name="batchTransportFee"]');
        if (batchFee) batchFee.value = String(state.batchTransportFee ?? "");
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

  // Leaving a field saves it (a blank value clears it, never breaks it).
  root.addEventListener("change", (event) => {
    const edit = event.target.closest("[data-stage-edit]");
    if (edit) {
      const field = edit.dataset.stageEdit;
      const value = field === "supplierLink"
        ? String(edit.value || "")
        : Number(edit.value || 0);
      saveEntryFields(edit.dataset.id, { [field]: value });
      return;
    }
    const batchFee = event.target.closest("[data-batch-transport]");
    if (batchFee) {
      state.batchTransportFee = String(batchFee.value || "");
      render();
      return;
    }
    const opening = event.target.closest('[data-autosave="opening"]');
    if (opening) {
      const form = opening.closest("[data-opening-form]");
      const fd = new FormData(form);
      saveOpeningBalances({
        startingBalanceNgn: Number(fd.get("startingBalanceNgn") || 0),
        startingBalanceCfa: Number(fd.get("startingBalanceCfa") || 0),
      });
    }
  });

  // Typing recalculates on the spot and queues the save a moment later.
  root.addEventListener("input", (event) => {
    const edit = event.target.closest("[data-stage-edit]");
    if (!edit) return;
    const row = edit.closest(".aa-stage-row");
    recalcRow(row);
    const field = edit.dataset.stageEdit;
    const value = field === "supplierLink"
      ? String(edit.value || "")
      : Number(edit.value || 0);
    scheduleEntrySave(edit.dataset.id, { [field]: value });
  });

  start();
})();
