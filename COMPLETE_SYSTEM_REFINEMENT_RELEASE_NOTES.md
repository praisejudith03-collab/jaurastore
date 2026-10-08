# Complete system refinement — release notes and owner checklist

This is the companion document for the refinement that lands **Google Drive
sync, broadcast filtering, the accounting opening balance, supplier-cost
saving, discount deduction, currency conversion and the single Batch
Transportation Fee** on `main`. It records **what changed**, **the exact API
contracts**, **what was verified automatically**, and **the short list of
things the owner has to do** (Section 7).

---

## 1. Google Sheets / Drive sync

### 1.1 What the code now does

* `google_sheets.py` reads the workbook id in this precedence order:
  **Accounting desk setting → `GOOGLE_SHEET_ID` environment variable →
  `DEFAULT_REFERENCE_SPREADSHEET_ID`** (`google_sheets.py:41`, the owner's own
  workbook `1GnBgXl-VNoRzV-jiz4qCeb_BKzs31_Fu`). That default means a fresh
  deploy with no environment variable still writes to the right file.
* Order rows now carry the **discount** and the supplier fields (unit price,
  quantity, link) on the row's Notes, so the sheet shows the same numbers the
  desk shows. The app-created ledgers keep their own net-profit formula.
* **A single Batch Transportation Fee is allocated across that batch's sheet
  rows** (`accounting.allocate_transport`, whole units, the remainder on the
  first row) at write time only — the batch record itself keeps the ONE total.
  The shares always add back up to the fee exactly, so the sheet's columns and
  the app's batch total can never disagree.
* `GET /api/admin/accounting/google/verify` runs a real end-to-end check
  (`google_sheets.verify_sync`): OAuth client present → owner token valid
  (refreshing if needed) → both app ledgers readable → the reference workbook
  and its `NGN` / `FCFA` tabs readable. It **never raises and never writes**,
  so it is safe to press as often as you like.
* The Accounting desk has a **✓ Test Google sync** button
  (`data-action="verify-google"`) that calls that route and prints the step
  list, and the accounting payload now returns `googleSheetId` and
  `transportFeeHint` for the UI to display.

### 1.2 Environment variables (Render → Environment)

| Key | Value | Notes |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` | from Google Cloud Console | e.g. `1234-abc.apps.googleusercontent.com` |
| `GOOGLE_CLIENT_SECRET` | from Google Cloud Console | starts with `GOCSPX-` |
| `GOOGLE_REDIRECT_URI` | `https://<your-render-host>/api/admin/accounting/google/callback` | must match the OAuth client **exactly**; when unset the app builds it from `SITE_ORIGIN` |
| `GOOGLE_SHEET_ID` | `1GnBgXl-VNoRzV-jiz4qCeb_BKzs31_Fu` | the owner's reference workbook |
| `GOOGLE_SHEETS_TOKEN_KEY` | any long random string | encrypts the stored refresh token; falls back to `SECRET_KEY`. Set it so rotating `SECRET_KEY` does not disconnect Google |

No service-account e-mail is involved: the app signs in **as the owner** over
OAuth. `render.yaml` already declares all five keys (with `GOOGLE_REDIRECT_URI`
and `GOOGLE_SHEET_ID` filled in), `.env.example` and
`ENVIRONMENT_VARIABLES.md` list them too, and `GOOGLE_DRIVE_SYNC_SETUP.md` is
the longer walkthrough.

### 1.3 Getting the credentials from Google Cloud Console

1. Open <https://console.cloud.google.com/> and pick (or create) a project —
   e.g. `ITEMFLOW`.
2. **APIs & Services → Library** → search **Google Sheets API** → **Enable**.
   Repeat for **Google Drive API** (the app lists/creates files, so both are
   required).
3. **APIs & Services → OAuth consent screen** → User type **External** → fill
   the app name, support email and developer email → **Save and continue**
   through Scopes (nothing to add; the app requests them itself) and Test
   users. Add the Google account that owns the workbook under **Test users**
   while the app is in testing mode.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** →
   Application type **Web application**.
   * Name: `ITEMFLOW Render`.
   * **Authorised JavaScript origins**: `https://<your-render-host>`.
   * **Authorised redirect URIs**:
     `https://<your-render-host>/api/admin/accounting/google/callback`.
     Add the local one (`http://localhost:8080/...`) too if you test locally.
   * **Create** → copy the **Client ID** and **Client secret**.
5. In Render: **your service → Environment → Add Environment Variable** for
   each key in the table above → **Save changes** (Render redeploys).

### 1.4 Confirming the sync after the redeploy

1. Open `https://<your-render-host>/accounting.html` and sign in as admin.
2. Press **Connect Google Sheets** once and approve the Google account that
   owns the workbook. The consent screen warns about an unverified app while
   the OAuth client is in testing mode; click **Advanced → Go to ITEMFLOW**.
3. Press **✓ Test Google sync** (route `GET /api/admin/accounting/google/verify`).
   A healthy install prints a green step list: `oauth-client`,
   `owner-token` (with the connected e-mail), `NGN-ledger`, `FCFA-ledger`,
   `reference-sheet` (workbook title, and whether its NGN / FCFA tabs are
   there — a missing tab is created automatically on the next push).
   A failure names the exact step and what to fix (missing variables, no
   token, wrong workbook id, wrong account).
4. The same report is available as JSON for support — the button is just a
   pretty printer over it (admin session + CSRF).
5. Push a batch (**Push to Google Sheets**), then open the workbook — the
   `NGN` / `FCFA` tabs show one row per order, with the supplier fields and the
   discount of each order, and the transport column split from the batch fee.

---

## 2. Marketing Broadcast — no deleted or archived products

`/marketing` (the broadcast builder and its scheduled feed) now filters through
one shared predicate in `js/admin.js`:

* `productIsDeleted(p)` — true for `is_deleted` / `isDeleted` /
  `is_archived` / `archived` / `isArchived` / `trashed` / `in_trash`,
  a `deletedAt` (or `deleted_at`, `archivedAt`, …) timestamp, `deleted: true`,
  `active: false` / `enabled: false`, `source` `deleted` or `replaced`, and
  `status` `deleted` or `archived`. Server-side `catalog.is_deleted_product`
  matches the same markers.
* `productIsBroadcastable(p)` — not deleted **and** `online !== false`, so a
  merely hidden product stays out of a broadcast too.

Both pickers on the marketing page and `broadcastScheduledFeedFor()` (the pin
list used by the scheduled feed) apply it, so a deleted product can no longer
be selected *or* silently pinned into a broadcast. `POST`ing an id that is
deleted or missing is still refused server-side with
**400 "One or more selected products are unavailable."** — the client filter is
convenience, the server is the gate. `catalog.merged()` also drops deleted rows
even when called with `include_hidden=True`.

## 3. Accounting — Starting Profit / opening balance

A new **Starting Profit / Opening Balance** box sits at the top of the
Accounting desk (above the balance strip). Type the money the business had
before this ledger and press Save: it goes to
`PUT /api/admin/accounting/settings` as `startingProfit` and the balance
becomes

```
Balance = Starting Profit + Σ batch net profit − transfer fees − manual expenses − bank charges
```

New batches accumulate on top of it; editing the box re-renders the strip
immediately without a page reload.

## 4. Supplier costs, discounts, currency and transport

### 4.1 Blank-safe supplier fields

Supplier price and supplier link may be left **blank** — the order still
stages and simply contributes 0 until the owner types a value. Every edit
**auto-saves** (debounced 450 ms) through
`PATCH /api/admin/accounting/orders/<order-id>` and the row's figures
recalculate in the browser immediately, without reloading the page.

### 4.2 The arithmetic

```
Total Supplier Cost = Unit Supplier Price × Quantity   (Quantity defaults to the
                                                        order's item quantity
                                                        while the box is 0/blank)
Net Profit          = Selling Price − Applied Discounts
                      − Total Supplier Cost − Delivery / Transport Fee
```

* The applied discount is displayed and **shown as a deduction** (row and
  batch). A discount box that is **$0 / 0% is hidden** (the row offers a small
  `＋ discount` affordance instead); any non-zero discount is visible with its
  `N% off` badge. `discount` and `discountPercent` stay in sync both ways.
* **FCFA orders** convert the NGN supplier cost at the **active
  NGN → CFA rate from Store Settings** (`cfaRate`, fallback `0.44`). Typing a
  supplier price in the FCFA ledger reprices that order at the active rate;
  untouched history keeps the rate it was confirmed with, so changing Store
  Settings never rewrites past profit. The rate can still be pinned per order
  with `exchangeRate`.

### 4.3 Batch Transportation Fee

The batch dialog and the Sales batches table have ONE **Batch Transportation
Fee** box for the whole batch, saved with
`PUT /api/admin/accounting/batches/<batch-id>` `{ "batchTransportFee": 1800 }`
and logged as a single total cost.

* A number wins: the batch total uses it and the per-order transport fees are
  ignored in the batch total.
* Blank / null clears it back to `"per-order"` mode and the individual
  per-order transport fee inputs (still present on every row and in the push
  dialog) take over again.
* The response returns `{batch, balances}` so the strip refreshes with it.

---

## 5. Auto-save and route contracts

| Route | Body | Meaning |
| --- | --- | --- |
| `PATCH /api/admin/accounting/orders/<oid>` | any of `saleAmount`, `supplierCostNgn`, `supplierUnitPriceNgn`, `supplierQty`, `supplierLink`, `discount`, `discountPercent`, `deliveryExpense`, `notes`, `exchangeRate`, `applyActiveRate` | blank number ⇒ 0, invalid ⇒ 400. Returns `{ok, entry}` with recalculated figures |
| `PUT /api/admin/accounting/batches/<bid>` | `{batchTransportFee, name?}` | number ⇒ `"batch"` mode, blank/null ⇒ `"per-order"` |
| `PUT /api/admin/accounting/settings` | `{startingBalanceNgn, startingBalanceCfa, referenceSpreadsheetId}` — `startingProfitNgn/Cfa` and `openingBalanceNgn/Cfa` are accepted as aliases | opening balance and the reference workbook. The active NGN→CFA rate is **not** here: it is the Store Settings `cfaRate`, edited in **Admin → Settings** |
| `GET /api/admin/accounting` | — | adds `googleSheetId`, `transportFeeHint` |
| `GET /api/admin/accounting/google/verify` | — | `{ok, google, verification}`; never 500s, never writes |

---

## 6. Tests run before this was committed

| Suite | Result |
| --- | --- |
| `tests/test_accounting_refinement.py` (new, 22 tests) | **pass** |
| Full local gate `PYTHON=… bash tools/ci_check.sh` | **2087 collected, 2054 passed, 33 skipped, 0 failed** |

The new suite pins the whole contract: supplier 2 500 × 4 = **10 000**;
10 % of 45 000 CFA = **4 500**; opening 250 000 + 14 000 = **264 000**;
per-order 500 + 300 overridden by a batch fee of 1 800 and cleared back to
**800**; `discountsTotal` 5 000 with net **8 500**; blank supplier fields never
break a calculation; deleted/archived products never appear in a broadcast
payload or a scheduled feed; the batch fee's sheet shares sum back to the fee;
and a **Node VM run of the shipped `js/accounting.js`** (`entryFigures`,
`rowFigures`, `entryQuantity`, `stageTotals`).

`tests/_accounting_desk_dom_check.mjs` additionally drives the **real shipped
page** in jsdom (opening box, hidden-at-zero discount, live unit × qty
recalc, debounced PATCH merge, batch-fee fallback, settings PUT, verify
button). It is wrapped by
`test_the_accounting_desk_renders_and_autosaves_in_a_real_dom`, which
**skips** (never fails) where jsdom is not installed; run it locally with
`JA_JSDOM_DIR=/tmp/uidom node tests/_accounting_desk_dom_check.mjs`.

The 33 skips in the local gate are 32 Playwright browser-smoke tests plus the
jsdom DOM check: neither chromium nor jsdom is installed in the build sandbox
but CI installs chromium (so the browser suite runs there), and
`JA_REQUIRE_BROWSER=1` turns browser skips into failures if a browser run is
ever needed.

## 7. What the owner has to do

1. Add the five environment variables from §1.2 in Render (the workbook id
   and the redirect URI already carry their values in `render.yaml`) and let
   the service redeploy.
2. Create the OAuth client with the redirect URI from §1.3, then press
   **Connect Google Sheets** once in Admin → Accounting.
3. Press **✓ Test Google sync** and confirm the step list is green — that is
   the proof that direct Google Drive sync works after the redeploy.
4. Type the **Starting Profit / Opening Balance** at the top of the desk, and
   optionally the **Batch Transportation Fee** for each batch.
5. Nothing else: supplier fields save themselves, discounts show themselves,
   and deleted products no longer reach a broadcast.

## 8. Known limits

* Google's OAuth client in **testing** mode issues refresh tokens that expire
  after 7 days; publish the app (or add the owner account as a test user and
  reconnect) if the verify step ever reports an invalid token.
* The consent screen shows the "unverified app" warning until the app is
  verified; this does not affect functionality.
* Deleted products are filtered from broadcasts, not deleted from the
  catalogue history; restore is still `POST /admin/products/trash/<id>/restore`.
