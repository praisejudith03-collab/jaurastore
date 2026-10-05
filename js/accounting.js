/* Jaura Store dual-currency accounting desk. */
(() => {
  "use strict";

  const root = document.getElementById("accounting-root");
  if (!root) return;

  const PAGE_SIZE = 100;
  const state = {
    csrf: "",
    email: "",
    entries: [],
    batches: [],
    currency: "NGN",
    period: "month",
    from: "",
    to: "",
    search: "",
    includeDeleted: false,
    selected: new Set(),
    limit: PAGE_SIZE,
    currentExchangeRate: 0.44,
    legacyRate: 0.44,
    busy: false,
  };

  const html = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const asNumber = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  };
  const fmt = (value, currency = state.currency) => {
    const code = currency === "CFA" ? "XOF" : "NGN";
    try {
      return new Intl.NumberFormat("en", {
        style: "currency", currency: code, maximumFractionDigits: 0,
      }).format(asNumber(value));
    } catch (_) {
      return `${currency} ${Math.round(asNumber(value)).toLocaleString("en")}`;
    }
  };
  const fmtRate = (value) => asNumber(value).toLocaleString("en", {
    minimumFractionDigits: 2, maximumFractionDigits: 4,
  });
  const dateOnly = (value) => String(value || "").slice(0, 10);
  const dateLabel = (value) => {
    const raw = dateOnly(value);
    if (!raw) return "—";
    const date = new Date(`${raw}T00:00:00`);
    return Number.isNaN(date.getTime()) ? html(raw) : date.toLocaleDateString("en", {
      year: "numeric", month: "short", day: "numeric",
    });
  };
  const dateTimeLabel = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? html(value) : date.toLocaleString("en", {
      year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  };
  const ymd = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

  async function request(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (state.csrf && options.method && options.method !== "GET") headers["X-CSRF-Token"] = state.csrf;
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers,
    });
    let data = {};
    try { data = await response.json(); } catch (_) { /* non-JSON error */ }
    if (!response.ok || data.ok === false) {
      const error = new Error(data.error || `Request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function toast(message, kind = "ok") {
    let node = document.getElementById("ac-toast");
    if (!node) {
      node = document.createElement("div");
      node.id = "ac-toast";
      node.className = "ac-toast";
      node.setAttribute("role", "status");
      document.body.appendChild(node);
    }
    node.textContent = message;
    node.dataset.kind = kind;
    node.classList.add("is-visible");
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(() => node.classList.remove("is-visible"), 3200);
  }

  function showLogin(message = "") {
    root.innerHTML = `
      <section class="ac-login-shell">
        <a class="ac-login-brand" href="/admin" aria-label="Back to the dashboard">JAURA <span>STORE</span></a>
        <div class="ac-login-card">
          <div class="ac-eyebrow">PRIVATE ADMIN TOOL</div>
          <h1>Accounting desk</h1>
          <p>Sign in with your administrator credentials to view the NGN and CFA ledgers.</p>
          ${message ? `<div class="ac-alert" role="alert">${html(message)}</div>` : ""}
          <form data-form="login" class="ac-login-form">
            <label>Admin email <span>(only needed if prompted)</span>
              <input type="email" name="email" autocomplete="username" placeholder="name@example.com" value="${html(state.email)}" />
            </label>
            <label>Password
              <input type="password" name="password" autocomplete="current-password" required autofocus />
            </label>
            <button class="ac-primary" type="submit">Sign in to accounting</button>
          </form>
          <a class="ac-back-link" href="/admin">← Return to the dashboard</a>
        </div>
        <p class="ac-login-foot">Protected by Jaura Store Admin authentication.</p>
      </section>`;
  }

  function initializeRange() {
    const today = new Date();
    const todayText = ymd(today);
    if (state.period === "all") {
      state.from = "";
      state.to = "";
    } else if (state.period === "month") {
      state.from = ymd(new Date(today.getFullYear(), today.getMonth(), 1));
      state.to = todayText;
    } else if (state.period === "year") {
      state.from = `${today.getFullYear()}-01-01`;
      state.to = todayText;
    } else if (state.period === "prior-current") {
      state.from = `${today.getFullYear() - 1}-01-01`;
      state.to = todayText;
    }
  }

  function currentRows() {
    const query = state.search.trim().toLocaleLowerCase();
    return state.entries.filter((entry) => {
      if (entry.currency !== state.currency) return false;
      if (entry.deleted && !state.includeDeleted) return false;
      const day = dateOnly(entry.date);
      if (state.from && day && day < state.from) return false;
      if (state.to && day && day > state.to) return false;
      if (query) {
        const haystack = [entry.id, entry.customer, entry.itemsSummary, entry.notes]
          .join(" ").toLocaleLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    }).sort((a, b) => String(b.date || "").localeCompare(String(a.date || ""))
      || String(b.id || "").localeCompare(String(a.id || "")));
  }

  function entryTotals(rows) {
    return rows.reduce((totals, entry) => {
      if (entry.deleted) return totals;
      totals.count += 1;
      totals.revenue += asNumber(entry.saleAmount);
      totals.supplierNgn += asNumber(entry.supplierCostNgn);
      totals.supplierCurrency += asNumber(entry.supplierCostInCurrency);
      totals.transport += asNumber(entry.deliveryExpense);
      totals.profit += asNumber(entry.netProfit);
      totals.cash += asNumber(entry.netCashProfit);
      return totals;
    }, { count: 0, revenue: 0, supplierNgn: 0, supplierCurrency: 0, transport: 0, profit: 0, cash: 0 });
  }

  function totalsCards(rows) {
    const totals = entryTotals(rows);
    const cards = [
      ["Customer revenue", fmt(totals.revenue)],
      ["Supplier payable · NGN", fmt(totals.supplierNgn, "NGN")],
      [state.currency === "CFA" ? "Supplier cost · CFA" : "Supplier cost · NGN", fmt(totals.supplierCurrency)],
      ["Delivery / transport", fmt(totals.transport)],
      ["Net profit · before delivery", fmt(totals.profit)],
      ["Net cash profit · after delivery", fmt(totals.cash)],
    ];
    return `<section class="ac-kpis" aria-label="Filtered ledger totals">${cards.map(([label, value], index) => `
      <article class="ac-kpi${index === 5 ? " ac-kpi-highlight" : ""}">
        <span>${html(label)}</span><strong>${html(value)}</strong>
        ${index === 0 ? `<small>${totals.count.toLocaleString("en")} confirmed orders</small>` : ""}
      </article>`).join("")}</section>`;
  }

  function trendPanel(rows) {
    const monthly = new Map();
    const yearly = new Map();
    rows.filter((entry) => !entry.deleted).forEach((entry) => {
      const day = dateOnly(entry.date);
      if (day.length < 7) return;
      const month = day.slice(0, 7);
      const year = day.slice(0, 4);
      monthly.set(month, (monthly.get(month) || 0) + asNumber(entry.netProfit));
      yearly.set(year, (yearly.get(year) || 0) + asNumber(entry.netProfit));
    });
    const allMonths = [...monthly.keys()].sort();
    const shownMonths = allMonths.slice(-18);
    const maxAbs = Math.max(1, ...shownMonths.map((month) => Math.abs(monthly.get(month) || 0)));
    const bars = shownMonths.map((month) => {
      const value = monthly.get(month) || 0;
      const height = Math.max(4, Math.round(Math.abs(value) / maxAbs * 100));
      const date = new Date(`${month}-01T00:00:00`);
      const label = Number.isNaN(date.getTime()) ? month : date.toLocaleDateString("en", { month: "short", year: "2-digit" });
      return `<div class="ac-chart-col" title="${html(label)} · ${html(fmt(value))}">
        <span class="ac-chart-value">${html(compact(value))}</span>
        <div class="ac-chart-track"><i class="ac-chart-bar${value < 0 ? " is-negative" : ""}" style="height:${height}%"></i></div>
        <span class="ac-chart-label">${html(label)}</span>
      </div>`;
    }).join("");
    const annual = [...yearly.entries()].sort((a, b) => b[0].localeCompare(a[0])).slice(0, 8);
    return `<section class="ac-trend-grid">
      <article class="ac-panel ac-chart-panel">
        <div class="ac-panel-head"><div><span class="ac-eyebrow">LEDGER PERFORMANCE</span><h2>Monthly net profit</h2></div><span class="ac-muted">${html(state.currency)} · filtered dates</span></div>
        ${bars ? `<div class="ac-chart">${bars}</div>` : `<div class="ac-empty-chart">No dated orders match this period yet.</div>`}
        <p class="ac-footnote">Net profit is sale price minus supplier cost. Delivery expense is shown separately and deducted in net cash profit.</p>
      </article>
      <article class="ac-panel ac-year-panel">
        <div class="ac-panel-head"><div><span class="ac-eyebrow">LONG-RANGE VIEW</span><h2>Yearly trend</h2></div></div>
        ${annual.length ? `<div class="ac-year-list">${annual.map(([year, total]) => `<div><span>${html(year)}</span><strong>${html(fmt(total))}</strong></div>`).join("")}</div>` : `<p class="ac-empty-chart">Year totals appear as orders are recorded.</p>`}
      </article>
    </section>`;
  }

  function compact(value) {
    const abs = Math.abs(asNumber(value));
    if (abs >= 1000000) return `${value < 0 ? "−" : ""}${(abs / 1000000).toFixed(1)}m`;
    if (abs >= 1000) return `${value < 0 ? "−" : ""}${(abs / 1000).toFixed(0)}k`;
    return Math.round(asNumber(value)).toLocaleString("en");
  }

  function statusChip(entry) {
    if (entry.deleted) return `<span class="ac-status is-deleted">Removed</span>`;
    if (entry.archived) return `<span class="ac-status is-archived">Archived</span>`;
    if (entry.legacySnapshot) return `<span class="ac-status is-legacy">Legacy estimate</span>`;
    return `<span class="ac-status is-live">Confirmed</span>`;
  }

  function editableAmount(entry, field, value, label) {
    const disabled = entry.deleted || entry.archived ? " disabled" : "";
    return `<label class="ac-cell-edit"><span class="sr-only">${html(label)} for ${html(entry.id)}</span>
      <input type="number" inputmode="numeric" min="0" step="1" max="1000000000000"
        class="ac-edit-input" data-order="${html(entry.id)}" data-field="${html(field)}"
        value="${html(Math.round(asNumber(value)))}"${disabled} /></label>`;
  }

  function noteInput(entry) {
    const disabled = entry.deleted || entry.archived ? " disabled" : "";
    return `<input type="text" maxlength="500" class="ac-note-input ac-edit-input"
      data-order="${html(entry.id)}" data-field="notes" value="${html(entry.notes || "")}"
      aria-label="Notes for ${html(entry.id)}" placeholder="Add note…"${disabled} />`;
  }

  function actionButtons(entry) {
    if (entry.archived) return `<span class="ac-muted">Batch locked</span>`;
    if (entry.deleted) return `<button class="ac-action ac-restore" type="button" data-action="restore" data-order="${html(entry.id)}">Restore</button>`;
    return `<button class="ac-action ac-delete" type="button" data-action="delete" data-order="${html(entry.id)}" aria-label="Remove ${html(entry.id)} from accounting">Remove</button>`;
  }

  function tablePanel(rows) {
    const shown = rows.slice(0, state.limit);
    const visibleSelectable = shown.filter((entry) => !entry.deleted && !entry.archived);
    const selectedVisible = visibleSelectable.filter((entry) => state.selected.has(entry.id)).length;
    const allVisibleSelected = visibleSelectable.length > 0 && selectedVisible === visibleSelectable.length;
    const cfa = state.currency === "CFA";
    const rowsHtml = shown.map((entry) => {
      const blocked = entry.deleted || entry.archived;
      return `<tr class="${entry.deleted ? "is-row-deleted" : ""}${entry.archived ? " is-row-archived" : ""}">
        <td class="ac-select-cell"><input type="checkbox" data-select="${html(entry.id)}" aria-label="Select order ${html(entry.id)}"${state.selected.has(entry.id) ? " checked" : ""}${blocked ? " disabled" : ""} /></td>
        <td class="ac-order-cell"><strong>${html(entry.id)}</strong><span>${html(entry.customer || "Customer")}</span><small>${html(entry.itemsSummary || "")}</small></td>
        <td class="ac-date-cell"><strong>${html(dateLabel(entry.date))}</strong><small>${html(dateTimeLabel(entry.date))}</small></td>
        <td>${editableAmount(entry, "saleAmount", entry.saleAmount, cfa ? "CFA sale price" : "NGN sale price")}</td>
        <td>${editableAmount(entry, "supplierCostNgn", entry.supplierCostNgn, "Supplier cost in NGN")}</td>
        ${cfa ? `<td class="ac-computed-cell"><strong>${html(fmt(entry.supplierCostCfa, "CFA"))}</strong><small>NGN × ${html(fmtRate(entry.exchangeRate))}</small></td>` : ""}
        <td>${editableAmount(entry, "deliveryExpense", entry.deliveryExpense, cfa ? "Delivery expense in CFA" : "Delivery expense in NGN")}</td>
        <td class="ac-profit-cell"><strong>${html(fmt(entry.netProfit))}</strong><small>cash ${html(fmt(entry.netCashProfit))}</small></td>
        <td class="ac-rate-cell"><strong>${html(fmtRate(entry.exchangeRate))}</strong></td>
        <td>${statusChip(entry)}</td>
        <td>${noteInput(entry)}</td>
        <td>${actionButtons(entry)}</td>
      </tr>`;
    }).join("");
    const currencySupplierNote = cfa
      ? "CFA supplier cost is calculated from supplier spend in NGN × the order’s locked confirmation rate."
      : "Supplier payable stays in NGN; CFA conversions are only shown in the separate CFA ledger.";
    return `<section class="ac-panel ac-table-panel">
      <div class="ac-table-heading">
        <div><span class="ac-eyebrow">${html(state.currency)} LEDGER</span><h2>Confirmed orders</h2><p class="ac-muted">Edit a cell and leave it to save. Rates are fixed at confirmation.</p></div>
        <div class="ac-table-actions">
          <button class="ac-secondary" type="button" data-action="select-visible"${visibleSelectable.length ? "" : " disabled"}>${allVisibleSelected ? "Clear visible" : "Select visible"}</button>
          <button class="ac-secondary" type="button" data-action="export-filtered"${rows.length ? "" : " disabled"}>Export filtered CSV</button>
        </div>
      </div>
      <p class="ac-table-note">${html(currencySupplierNote)} CSV exports are Excel-ready.</p>
      <div class="ac-table-wrap"><table class="ac-table">
        <thead><tr>
          <th><input type="checkbox" data-select-visible aria-label="Select all visible confirmed orders"${allVisibleSelected ? " checked" : ""}${visibleSelectable.length ? "" : " disabled"} /></th>
          <th>Order / customer</th><th>Confirmed</th><th>${cfa ? "Sale · CFA" : "Sale · NGN"}</th>
          <th>Supplier cost · NGN</th>${cfa ? "<th>Supplier cost · CFA</th>" : ""}
          <th>Delivery · ${cfa ? "CFA" : "NGN"}</th><th>Profit / cash</th><th>Locked rate</th><th>Record</th><th>Notes</th><th>Action</th>
        </tr></thead>
        <tbody>${rowsHtml || `<tr><td colspan="${cfa ? 12 : 11}" class="ac-empty-row">No confirmed ${html(state.currency)} orders match these filters.</td></tr>`}</tbody>
      </table></div>
      <div class="ac-table-footer"><span>Showing ${Math.min(state.limit, rows.length).toLocaleString("en")} of ${rows.length.toLocaleString("en")} matching orders</span>
        ${rows.length > state.limit ? `<button class="ac-secondary" type="button" data-action="load-more">Load 100 more</button>` : ""}
      </div>
    </section>`;
  }

  function selectedEntries() {
    const selected = state.selected;
    return state.entries.filter((entry) => selected.has(entry.id) && entry.currency === state.currency && !entry.deleted && !entry.archived);
  }

  function drawerPanel() {
    const entries = selectedEntries();
    if (!entries.length) return "";
    const totals = entries.reduce((sum, entry) => {
      sum.revenue += asNumber(entry.saleAmount);
      sum.supplierNgn += asNumber(entry.supplierCostNgn);
      sum.supplierCurrency += asNumber(entry.supplierCostInCurrency);
      sum.transport += asNumber(entry.deliveryExpense);
      sum.net += asNumber(entry.netCashProfit);
      return sum;
    }, { revenue: 0, supplierNgn: 0, supplierCurrency: 0, transport: 0, net: 0 });
    return `<div class="ac-drawer-backdrop" data-action="close-drawer"></div>
      <aside class="ac-batch-drawer" role="dialog" aria-modal="true" aria-label="Review delivery batch" tabindex="-1">
        <div class="ac-drawer-head"><div><span class="ac-eyebrow">DELIVERY BATCH</span><h2>Selected orders <span>${entries.length}</span></h2></div>
          <button type="button" class="ac-close" data-action="close-drawer" aria-label="Close batch summary">×</button>
        </div>
        <p class="ac-muted">${html(state.currency)} ledger · this immutable snapshot can be exported later from Archived batches.</p>
        ${entries.length > 500 ? `<p class="ac-batch-limit-note">Delivery batches are limited to 500 orders. Remove ${entries.length - 500} selection${entries.length - 500 === 1 ? "" : "s"} to continue.</p>` : ""}
        <div class="ac-drawer-totals">
          <div><span>Customer revenue</span><strong>${html(fmt(totals.revenue))}</strong></div>
          <div><span>Supplier payable · exact NGN</span><strong>${html(fmt(totals.supplierNgn, "NGN"))}</strong></div>
          <div><span>Supplier cost · ${html(state.currency)}</span><strong>${html(fmt(totals.supplierCurrency))}</strong></div>
          <div><span>Delivery / transport</span><strong>− ${html(fmt(totals.transport))}</strong></div>
          <div class="ac-drawer-net"><span>Net cash profit to bank</span><strong>${html(fmt(totals.net))}</strong></div>
        </div>
        <label class="ac-batch-name-label">Batch name <span>optional</span>
          <input type="text" maxlength="100" data-batch-name placeholder="e.g. Lagos delivery · 12 Oct" />
        </label>
        <div class="ac-drawer-actions">
          <button type="button" class="ac-secondary" data-action="export-selected">Export selected CSV</button>
          <button type="button" class="ac-primary" data-action="archive-selected"${entries.length > 500 ? " disabled" : ""}>Save &amp; archive batch</button>
          <button type="button" class="ac-text-button" data-action="close-drawer">Keep editing orders</button>
        </div>
      </aside>`;
  }

  function batchMarkup(batch) {
    const currency = batch.currency === "CFA" ? "CFA" : "NGN";
    const totals = batch.totals || {};
    const rows = Array.isArray(batch.orders) ? batch.orders : [];
    return `<article class="ac-batch-card">
      <div class="ac-batch-summary">
        <div><span class="ac-status is-archived">${html(currency)} · ARCHIVED</span><h3>${html(batch.name || "Delivery batch")}</h3><p>${html(batch.id || "")} · ${html(dateTimeLabel(batch.createdAt))}</p></div>
        <div class="ac-batch-numbers"><strong>${html(fmt(totals.customerRevenue, currency))}</strong><span>${Number(totals.orderCount || rows.length).toLocaleString("en")} orders · payable ${html(fmt(totals.supplierPayableNgn, "NGN"))}</span></div>
        <div class="ac-batch-controls"><button class="ac-secondary" type="button" data-action="export-batch" data-batch="${html(batch.id)}">Export CSV</button>
          <button class="ac-text-button" type="button" data-action="toggle-batch" data-batch="${html(batch.id)}">View snapshot</button></div>
      </div>
      <div class="ac-batch-details" id="batch-${html(batch.id)}" hidden>
        <div class="ac-batch-stat-grid">
          <div><span>Revenue</span><strong>${html(fmt(totals.customerRevenue, currency))}</strong></div>
          <div><span>Supplier payable · NGN</span><strong>${html(fmt(totals.supplierPayableNgn, "NGN"))}</strong></div>
          <div><span>Supplier cost · ${html(currency)}</span><strong>${html(fmt(totals.supplierCostInCurrency, currency))}</strong></div>
          <div><span>Delivery expense</span><strong>${html(fmt(totals.transportExpense, currency))}</strong></div>
          <div><span>Net cash profit</span><strong>${html(fmt(totals.netCashProfit, currency))}</strong></div>
        </div>
        <div class="ac-table-wrap"><table class="ac-table ac-snapshot-table"><thead><tr><th>Order</th><th>Customer</th><th>Revenue</th><th>Supplier · NGN</th><th>Supplier · ${html(currency)}</th><th>Delivery</th><th>Net profit</th><th>Rate</th></tr></thead>
          <tbody>${rows.map((entry) => `<tr><td>${html(entry.id)}</td><td>${html(entry.customer)}</td><td>${html(fmt(entry.saleAmount, currency))}</td><td>${html(fmt(entry.supplierCostNgn, "NGN"))}</td><td>${html(fmt(entry.supplierCostInCurrency, currency))}</td><td>${html(fmt(entry.deliveryExpense, currency))}</td><td>${html(fmt(entry.netProfit, currency))}</td><td>${html(fmtRate(entry.exchangeRate))}</td></tr>`).join("")}</tbody>
        </table></div>
      </div>
    </article>`;
  }

  function archivesPanel() {
    const batches = [...state.batches].sort((a, b) => String(b.createdAt || "").localeCompare(String(a.createdAt || "")));
    return `<section class="ac-panel ac-archives" id="ac-archives">
      <div class="ac-panel-head"><div><span class="ac-eyebrow">DELIVERY HISTORY</span><h2>Archived batches</h2><p class="ac-muted">Saved batch figures are immutable snapshots of their selected orders.</p></div><span class="ac-archive-count">${batches.length} ${batches.length === 1 ? "batch" : "batches"}</span></div>
      ${batches.length ? `<div class="ac-batch-list">${batches.map(batchMarkup).join("")}</div>` : `<div class="ac-empty-archives"><strong>No batches archived yet</strong><span>Select confirmed orders and use “Save &amp; archive batch” to keep a delivery snapshot.</span></div>`}
    </section>`;
  }

  function viewsMarkup() {
    const rows = currentRows();
    const activeCount = state.entries.filter((entry) => entry.currency === state.currency && !entry.deleted).length;
    const legacyCount = state.entries.filter((entry) => entry.currency === state.currency && !entry.deleted && entry.legacySnapshot).length;
    return `
      <div class="ac-rate-banner">
        <div class="ac-rate-symbol">↔</div><div><strong>Today’s configured Admin rate: 1 NGN = ${html(fmtRate(state.currentExchangeRate))} CFA</strong><span>Every confirmed order keeps its own locked rate; updating today’s setting never changes history.</span></div>
      </div>
      ${legacyCount ? `<div class="ac-legacy-banner"><strong>${legacyCount.toLocaleString("en")} pre-accounting ${legacyCount === 1 ? "order uses" : "orders use"} a fixed ${html(fmtRate(state.legacyRate))} legacy estimate.</strong> No confirmation-time exchange rate was stored for those orders; this estimate will not drift with today’s rate.</div>` : ""}
      ${totalsCards(rows)}
      ${trendPanel(rows)}
      ${tablePanel(rows)}
      ${archivesPanel()}
      ${drawerPanel()}`;
  }

  function render() {
    if (!state.selected.size) document.body.classList.remove("ac-drawer-open");
    const rowsCount = state.entries.filter((entry) => entry.currency === state.currency && !entry.deleted).length;
    root.innerHTML = `
      <div class="ac-app">
        <header class="ac-topbar">
          <a href="/admin" class="ac-brand"><span class="ac-brand-mark">J</span><span><strong>JAURA STORE</strong><small>ADMIN FINANCE</small></span></a>
          <nav class="ac-top-actions" aria-label="Accounting actions">
            <a class="ac-secondary ac-top-link" href="/admin">← Admin dashboard</a>
            <span class="ac-signed-in">${html(state.email || "Admin")}</span>
            <button class="ac-text-button" type="button" data-action="refresh">Refresh</button>
            <button class="ac-text-button" type="button" data-action="logout">Sign out</button>
          </nav>
        </header>
        <main class="ac-main">
          <section class="ac-hero">
            <div><span class="ac-eyebrow">PRIVATE FINANCE DESK</span><h1>Accounting spreadsheet</h1><p>Confirmed sales, supplier payables and delivery cash—kept in separate NGN and CFA ledgers.</p></div>
            <div class="ac-hero-count"><strong>${rowsCount.toLocaleString("en")}</strong><span>active ${html(state.currency)} orders</span></div>
          </section>
          <section class="ac-toolbar">
            <div class="ac-currency-tabs" role="tablist" aria-label="Choose ledger currency">
              <button role="tab" aria-selected="${state.currency === "NGN"}" class="${state.currency === "NGN" ? "is-active" : ""}" data-action="currency" data-currency="NGN"><span class="ac-currency-code">₦</span><span><strong>Naira ledger</strong><small>NGN orders &amp; payables</small></span></button>
              <button role="tab" aria-selected="${state.currency === "CFA"}" class="${state.currency === "CFA" ? "is-active" : ""}" data-action="currency" data-currency="CFA"><span class="ac-currency-code">C</span><span><strong>CFA ledger</strong><small>XOF sales &amp; converted cost</small></span></button>
            </div>
            <div class="ac-filter-controls">
              <label>Period<select data-filter="period">
                <option value="month"${state.period === "month" ? " selected" : ""}>This month</option>
                <option value="year"${state.period === "year" ? " selected" : ""}>This year</option>
                <option value="prior-current"${state.period === "prior-current" ? " selected" : ""}>Last year to this year</option>
                <option value="all"${state.period === "all" ? " selected" : ""}>All time</option>
                <option value="custom"${state.period === "custom" ? " selected" : ""}>Custom dates</option>
              </select></label>
              <label>From<input type="date" data-filter="from" value="${html(state.from)}"${state.period === "all" ? " disabled" : ""} /></label>
              <label>To<input type="date" data-filter="to" value="${html(state.to)}"${state.period === "all" ? " disabled" : ""} /></label>
              <label class="ac-search">Search<input type="search" data-filter="search" value="${html(state.search)}" placeholder="Order, customer, note…" /></label>
              <label class="ac-deleted-toggle"><input type="checkbox" data-filter="deleted"${state.includeDeleted ? " checked" : ""} /> Show removed</label>
            </div>
          </section>
          <div class="ac-selection-bar"><span>${state.selected.size ? `<strong>${state.selected.size}</strong> eligible order${state.selected.size === 1 ? "" : "s"} selected` : "Tip: select orders from one currency ledger to prepare a delivery batch."}</span>
            <button type="button" class="ac-primary" data-action="open-drawer"${state.selected.size ? "" : " disabled"}>Review delivery batch${state.selected.size ? ` (${state.selected.size})` : ""}</button>
          </div>
          <div id="ac-views">${viewsMarkup()}</div>
          <footer class="ac-footer">Jaura Store Accounting · Private finance tool · Amounts are whole NGN / CFA units</footer>
        </main>
      </div>`;
  }

  async function loadLedger() {
    const query = state.includeDeleted ? "?includeDeleted=true" : "";
    const data = await request(`/api/admin/accounting${query}`);
    state.entries = Array.isArray(data.entries) ? data.entries : [];
    state.batches = Array.isArray(data.batches) ? data.batches : [];
    state.currentExchangeRate = asNumber(data.currentExchangeRate) || 0.44;
    state.legacyRate = asNumber(data.legacyRate) || 0.44;
    state.selected = new Set([...state.selected].filter((id) => {
      const entry = state.entries.find((row) => row.id === id);
      return entry && !entry.deleted && !entry.archived && entry.currency === state.currency;
    }));
    render();
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
      initializeRange();
      await loadLedger();
    } catch (error) {
      showLogin(error.message || "The accounting page could not connect. Please try again.");
    }
  }

  function csvCell(value) {
    let text = String(value == null ? "" : value);
    // Avoid spreadsheet formula injection for order notes and customer names.
    if (/^[\s]*[=+@\-]/.test(text)) text = `'${text}`;
    return `"${text.replace(/"/g, '""')}"`;
  }

  function csvDocument(entries, currency, batch = null) {
    const headings = ["Order ID", "Confirmed at", "Customer", "Items", "Currency", "Sale amount", "Supplier payable NGN", "Supplier cost in ledger currency", "Delivery / transport", "Net profit before delivery", "Net cash profit after delivery", "Locked NGN to CFA rate", "Legacy estimate", "Notes"];
    const rows = entries.map((entry) => [
      entry.id, entry.date, entry.customer, entry.itemsSummary, currency, entry.saleAmount,
      entry.supplierCostNgn, entry.supplierCostInCurrency, entry.deliveryExpense,
      entry.netProfit, entry.netCashProfit, entry.exchangeRate,
      entry.legacySnapshot ? "yes" : "no", entry.notes,
    ]);
    const lines = [headings, ...rows].map((line) => line.map(csvCell).join(",")).join("\r\n");
    const blob = new Blob(["\uFEFF", lines], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    const base = batch ? safeFileName(batch.name || batch.id) : `jaura-${currency.toLowerCase()}-accounting`;
    link.href = URL.createObjectURL(blob);
    link.download = `${base}-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  function safeFileName(value) {
    return String(value || "delivery-batch").toLowerCase().normalize("NFKD")
      .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 50) || "delivery-batch";
  }

  async function updateEntry(input) {
    const id = input.dataset.order;
    const field = input.dataset.field;
    if (!id || !field) return;
    const body = {};
    if (field === "notes") body.notes = input.value;
    else {
      if (input.value === "" || Number(input.value) < 0 || !Number.isFinite(Number(input.value))) {
        toast("Enter a non-negative whole amount.", "error");
        input.focus();
        return;
      }
      body[field] = Math.round(Number(input.value));
    }
    input.disabled = true;
    try {
      const data = await request(`/api/admin/accounting/orders/${encodeURIComponent(id)}`, {
        method: "PATCH", body: JSON.stringify(body),
      });
      const index = state.entries.findIndex((entry) => entry.id === id);
      if (index >= 0 && data.entry) state.entries[index] = data.entry;
      toast("Accounting entry saved.");
      render();
    } catch (error) {
      toast(error.message || "Could not save this entry.", "error");
      input.disabled = false;
      try { await loadLedger(); } catch (_) { /* retain the visible edit error */ }
    }
  }

  async function removeEntry(id) {
    const entry = state.entries.find((row) => row.id === id);
    if (!entry || entry.archived) return;
    if (!window.confirm(`Remove order ${id} from the active accounting ledger? The confirmed order itself will remain intact.`)) return;
    try {
      await request(`/api/admin/accounting/orders/${encodeURIComponent(id)}`, { method: "DELETE" });
      state.selected.delete(id);
      toast("Accounting row removed. You can restore it with “Show removed”.");
      await loadLedger();
    } catch (error) {
      toast(error.message || "Could not remove this row.", "error");
    }
  }

  async function restoreEntry(id) {
    try {
      const data = await request(`/api/admin/accounting/orders/${encodeURIComponent(id)}/restore`, {
        method: "POST", body: "{}",
      });
      const index = state.entries.findIndex((entry) => entry.id === id);
      if (index >= 0 && data.entry) state.entries[index] = data.entry;
      toast("Accounting row restored.");
      render();
    } catch (error) {
      toast(error.message || "Could not restore this row.", "error");
    }
  }

  async function archiveSelection() {
    const entries = selectedEntries();
    if (!entries.length) return;
    if (entries.length > 500) {
      toast("A delivery batch can contain at most 500 orders.", "error");
      return;
    }
    const nameInput = root.querySelector("[data-batch-name]");
    const name = nameInput ? nameInput.value.trim() : "";
    const button = root.querySelector('[data-action="archive-selected"]');
    if (button) button.disabled = true;
    try {
      const data = await request("/api/admin/accounting/batches", {
        method: "POST",
        body: JSON.stringify({ orderIds: entries.map((entry) => entry.id), currency: state.currency, name }),
      });
      state.selected.clear();
      await loadLedger();
      toast(`Delivery batch ${data.batch && data.batch.id ? data.batch.id : "saved"} archived.`);
    } catch (error) {
      if (button) button.disabled = false;
      toast(error.message || "Could not archive this delivery batch.", "error");
    }
  }

  async function logout() {
    try {
      const data = await request("/api/admin/logout", { method: "POST", body: "{}" });
      state.csrf = data.csrf || "";
    } catch (_) { /* the local view is discarded either way */ }
    state.entries = [];
    state.batches = [];
    state.selected.clear();
    state.email = "";
    showLogin("You have signed out of the accounting desk.");
  }

  async function submitLogin(form) {
    const values = new FormData(form);
    const email = String(values.get("email") || "").trim();
    const password = String(values.get("password") || "");
    const button = form.querySelector("button[type=submit]");
    if (button) { button.disabled = true; button.textContent = "Signing in…"; }
    state.email = email;
    try {
      const data = await request("/api/admin/login", {
        method: "POST", body: JSON.stringify({ email, password }),
      });
      state.csrf = data.csrf || "";
      state.email = data.email || email;
      initializeRange();
      await loadLedger();
    } catch (error) {
      showLogin(error.message || "Sign in failed.");
      const emailInput = root.querySelector('input[name="email"]');
      if (emailInput) emailInput.value = email;
    }
  }

  function toggleBatch(id) {
    const panel = document.getElementById(`batch-${CSS.escape(id)}`);
    if (!panel) return;
    panel.hidden = !panel.hidden;
    const button = root.querySelector(`[data-action="toggle-batch"][data-batch="${CSS.escape(id)}"]`);
    if (button) button.textContent = panel.hidden ? "View snapshot" : "Hide snapshot";
  }

  function handleClick(event) {
    const button = event.target.closest("[data-action]");
    if (!button || !root.contains(button)) return;
    const action = button.dataset.action;
    if (action === "currency") {
      if (state.currency !== button.dataset.currency) {
        state.currency = button.dataset.currency;
        state.selected.clear();
        state.limit = PAGE_SIZE;
        render();
      }
    } else if (action === "refresh") {
      button.disabled = true;
      loadLedger().then(() => toast("Ledger refreshed.")).catch((error) => toast(error.message, "error"));
    } else if (action === "logout") {
      logout();
    } else if (action === "open-drawer") {
      if (state.selected.size) {
        const drawer = root.querySelector(".ac-batch-drawer");
        if (drawer) drawer.focus();
        document.body.classList.add("ac-drawer-open");
      }
    } else if (action === "close-drawer") {
      document.body.classList.remove("ac-drawer-open");
    } else if (action === "select-visible") {
      const rows = currentRows().slice(0, state.limit).filter((entry) => !entry.deleted && !entry.archived);
      const allSelected = rows.length && rows.every((entry) => state.selected.has(entry.id));
      rows.forEach((entry) => allSelected ? state.selected.delete(entry.id) : state.selected.add(entry.id));
      render();
    } else if (action === "load-more") {
      state.limit += PAGE_SIZE;
      render();
    } else if (action === "export-filtered") {
      const rows = currentRows().filter((entry) => !entry.deleted);
      if (rows.length) csvDocument(rows, state.currency);
    } else if (action === "export-selected") {
      const rows = selectedEntries();
      if (rows.length) csvDocument(rows, state.currency);
    } else if (action === "archive-selected") {
      archiveSelection();
    } else if (action === "delete") {
      removeEntry(button.dataset.order);
    } else if (action === "restore") {
      restoreEntry(button.dataset.order);
    } else if (action === "toggle-batch") {
      toggleBatch(button.dataset.batch);
    } else if (action === "export-batch") {
      const batch = state.batches.find((item) => item.id === button.dataset.batch);
      if (batch && Array.isArray(batch.orders)) csvDocument(batch.orders, batch.currency, batch);
    }
  }

  root.addEventListener("click", handleClick);
  root.addEventListener("submit", (event) => {
    const form = event.target.closest('[data-form="login"]');
    if (!form) return;
    event.preventDefault();
    submitLogin(form);
  });
  root.addEventListener("change", (event) => {
    const target = event.target;
    if (target.matches("[data-select]")) {
      const id = target.dataset.select;
      if (target.checked) state.selected.add(id); else state.selected.delete(id);
      render();
      return;
    }
    if (target.matches("[data-select-visible]")) {
      const rows = currentRows().slice(0, state.limit).filter((entry) => !entry.deleted && !entry.archived);
      rows.forEach((entry) => target.checked ? state.selected.add(entry.id) : state.selected.delete(entry.id));
      render();
      return;
    }
    if (target.matches("[data-filter='period']")) {
      state.period = target.value;
      if (state.period !== "custom") initializeRange();
      state.limit = PAGE_SIZE;
      render();
      return;
    }
    if (target.matches("[data-filter='from']")) {
      state.from = target.value;
      state.period = "custom";
      state.limit = PAGE_SIZE;
      render();
      return;
    }
    if (target.matches("[data-filter='to']")) {
      state.to = target.value;
      state.period = "custom";
      state.limit = PAGE_SIZE;
      render();
      return;
    }
    if (target.matches("[data-filter='deleted']")) {
      state.includeDeleted = target.checked;
      state.limit = PAGE_SIZE;
      loadLedger().catch((error) => toast(error.message, "error"));
      return;
    }
    if (target.matches(".ac-edit-input")) updateEntry(target);
  });
  root.addEventListener("input", (event) => {
    const target = event.target;
    if (target.matches("[data-filter='search']")) {
      state.search = target.value;
      state.limit = PAGE_SIZE;
      const views = root.querySelector("#ac-views");
      if (views) views.innerHTML = viewsMarkup();
      return;
    }
  });

  initializeRange();
  start();
})();
