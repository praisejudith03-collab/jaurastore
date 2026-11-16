# Sales Timeframes, Inline Cost Fix, Sourcing Batches & Dashboard Cleanup

Release date: 2026-11-16

Existing Google Sheets sync and order deletion are untouched: the ledger
workbook target, the push route, the CSV importer and every delete/restore
path behave exactly as before. This release adds reporting windows, an inline
cost editor, and a new sourcing-batch worksheet beside the existing ledger.

## 1. Sales tab — timeframe filters and the missing-cost quick fix

**Calendar timeframe switch.** The Confirmed sales section now switches between
**Today**, **This week**, **This month**, **This year** and **All time**.
Weeks start on Monday, months on the 1st and years on 1 January (UTC), and the
window is resolved on the server (`GET /api/admin/sales?period=…`) so it can
never drift with the browser clock:

* Revenue, Net profit, Orders, Units and Average order value all describe the
  same window;
* the top-products table and the CSV export follow the selection too;
* explicit From / To dates still narrow any window, and `?days=30` /
  `?days=all` bookmarks from before this release keep working unchanged.

**Missing cost prices are now fixable in place.** The warning
(“N orders in this period have no cost price recorded…”) is a button. It opens
the exact flagged orders on the Sales tab, each with:

* one supplier-cost box per product line (the line ids the accounting editor
  already validates), entered as an NGN line total;
* a delivery-expense box for the same order;
* a Save button that writes through the existing accounting order PATCH, so the
  net-profit card and the accounting desk agree immediately.

Orders that already sit in a pushed (immutable) Google Sheets batch are shown
read-only and labelled as locked, instead of offering a save the server would
refuse. The API returns at most 60 flagged orders per window (`missingCostOrders`)
and says so when the list is truncated.

## 2. Accounting desk — sourcing / pre-order batches

A new **Buying trips** section (Admin → More → Accounting) organises the desk
around the sourcing trips themselves — “Cotonou Batch 1”, “Nov Pre-Orders”:

* create a named batch per ledger (NGN or FCFA);
* rename it, and delete an old batch (its orders stay on the desk untouched);
* log itemised costs with a type — **Goods bought**, **Sourcing expense** or
  **Transport / waybill** — each with a label, quantity and amount, and delete a
  wrongly logged line;
* tick confirmed orders from a multi-select checklist to assign them to the
  batch (an NGN batch can only hold NGN orders; a stale id is refused, never
  silently dropped);
* type/edit the supplier cost of any product line **inside the batch**, plus the
  order's delivery expense — saved onto the order with the same PATCH the desk
  uses;
* automatic totals on every change:
  **Total spent** (goods + sourcing + transport logged in the batch),
  **Total revenue** (the assigned orders' selling amounts minus their
  discounts) and **Net profit** (revenue − spent), plus a per-type breakdown.

Assigned orders that have already been pushed to Google Sheets keep counting
toward the batch's revenue, because the trip really did sell them. The batches
are mutable worksheets stored in the same `growth_settings` key/value row
pattern as the expense logger and mirrored to Supabase
(`accounting_sourcing_batches_json`); the immutable pushed-batch archive is
untouched.

Routes: `POST /api/admin/accounting/sourcing`,
`PATCH|DELETE /api/admin/accounting/sourcing/<bid>`,
`POST /api/admin/accounting/sourcing/<bid>/costs`,
`DELETE /api/admin/accounting/sourcing/<bid>/costs/<cid>`.

## 3. Dashboard cleanup

* **Top viewed products** and **Visitor locations** show five rows by default,
  with a one-tap “Show all N” toggle (and “Show top 5” to collapse again), so
  neither list can run the length of a phone screen.
* The **Latest orders** table is gone from the Dashboard: orders live on the
  Orders page, and the dashboard links there instead. The compact “Needs
  attention” queue (pending orders / low stock) is unchanged — it is the action
  summary, not a second order list.

## 4. Tests

* `tests/test_sales_timeframes_and_sourcing.py` — calendar windows (pinned to a
  Wednesday so Monday/1 January boundaries are unambiguous), the inline cost
  fix end-to-end (list → PATCH → net profit), locked pushed orders, sourcing
  batch creation, itemised costs, multi-select assignment, currency rejection,
  rename/delete and the spent/revenue/profit arithmetic.
* `tests/_sales_timeframes_quickfix_sim.mjs` — boots the shipped `admin.js` in a
  Node VM: timeframe buttons, the server window each one requests, the four
  cards, opening the warning, and the per-line PATCH payload a typed cost sends.
* `tests/_dashboard_truncation_sim.mjs` — boots the shipped `admin.js` and
  proves five-row truncation, expanding/collapsing, and that no orders table is
  rendered on the Dashboard.
* `tests/_accounting_desk_dom_check.mjs` — extended for the sourcing section:
  the three totals, the checklist, the cost form and the delete route.
* `tests/test_admin_features.py` — the new admin routes added to the gated-route
  audit.

Run: `python3 -m pytest tests/test_sales_timeframes_and_sourcing.py -q` and the
full suite `python3 -m pytest -q`.

## Deploy notes

Asset token bumped `v243 → v244` (`tools/asset_version.py --write`) so returning
phones pick up the new CSS/JS immediately. No database migration is required:
sourcing batches are one new JSON row, created on first save.
