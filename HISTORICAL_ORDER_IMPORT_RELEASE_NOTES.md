# Historical order import — release notes

Brings the old spreadsheet of past orders into the accounting desk without
hand-retyping anything.

## The bug this also fixes

The ledger gained a **Location / Destination** column (column L) in the
previous change, and the row builder was widened to emit twelve values. The
append, update and read ranges in `google_sheets.py` were still `A:K`, so on
every push the twelfth value was dropped on the floor and **Location always
arrived blank**.

| Function | Before | After |
| --- | --- | --- |
| `_append_rows_to` | `'Orders'!A:K` | `'Orders'!A:L` |
| `_update_row_range` | `A{row}:K{row}` | `A{row}:L{row}` |
| `_existing_order_rows_in` | `'Orders'!A:K` | `'Orders'!A:L` |

Pinned by `test_google_sheets_writes_all_twelve_columns`, which fails if any
11-column order range reappears.

## What the importer does

`import_historical_orders.py` reads the old sheet, works out each row's
currency, maps the columns onto the canonical 12-column ledger layout, fills
Location, converts supplier costs, and **stages** the rows.

**Currency separation.** Each row is routed by:

1. its currency cell (`NGN` / `CFA` / `FCFA` / `XOF`), then
2. any currency marker anywhere in the row (`₦45,000`, `62,000 FCFA`), then
3. `--default-currency`, with the row flagged `currency assumed` in the report.

Rows that fall through to (3) are the ones to glance at before pushing.

**Column mapping.** Headers are matched fuzzily — case, spacing, punctuation
and accents are ignored, and the longest alias wins, so `Order NO`,
`order_id` and `Invoice` all land on Order ID while `Net Profit` is not
swallowed by `Notes`. French headers are expected and supported (`Prix de
vente`, `Coût fournisseur`, `Livraison`, `Ville`, `Bénéfice`…).

**Location / Destination.** Filled from destination → country → city →
state/region → zone, and canonicalised (`NG` → `Nigeria`, `Bénin` →
`Benin Republic`). A location is **never invented**: when the source has
nothing, the cell stays blank and the row is reported.

**Supplier costs.** The snapshot invariant is that cost is stored in NGN and
the desk converts for display, so:

* a Naira figure is stored as-is;
* a CFA figure is converted back to NGN (`÷ rate`) and shown as the original
  FCFA on the CFA ledger.

Currency is read from the value first, then the header — a cell reading
`24,000 CFA` under a `Cost Price (NGN)` header is treated as CFA.

**Rate.** Historical rows have no captured rate, so they use
`accounting.LEGACY_RATE` (the same baseline the desk uses for pre-feature
orders), overridable with `--rate`. The *live* admin rate is deliberately not
used: repricing old orders with today's rate makes historical profit totals
drift.

## Safety

* **Dry run by default.** Nothing is written without `--confirm`.
* **Staging, not the live books.** Imported rows appear in the accounting
  desk's staging queue like a fresh confirmation, to be reviewed and pushed
  by hand.
* **No deletes**, ever. Existing rows are untouched and known order ids are
  skipped, so re-running changes nothing.
* Duplicate source ids are kept and suffixed (`ORD-1007-2`) rather than
  silently dropped; missing ids are generated (`HIST-00001`).

## Usage

```bash
# 1. see what would happen — always safe
python3 import_historical_orders.py --csv old-orders.csv

# 2. emit review CSVs in the exact ledger layout
python3 import_historical_orders.py --csv old-orders.csv --out-dir /tmp/ledgers

# 3. stage for real (needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY)
python3 import_historical_orders.py --csv old-orders.csv --confirm

# read the source workbook directly, where Google auth exists
python3 import_historical_orders.py --sheet-id 1GnBg… --tab "Orders" --confirm
```

Accepts `.csv`, `.tsv` and `.xlsx` (the first worksheet). Useful flags:
`--rate`, `--default-currency`, `--id-prefix`, `--limit`, `--out-dir`.

## Running it against the real workbook

The sandbox this was built in cannot reach Google, so the import has **not**
been run against the live sheet yet. To do it:

1. Easiest — `File → Download → Comma Separated Values` on the old workbook,
   drop it in the repo root, and run step 3 above.
2. Or run `--sheet-id 1GnBgXl-VNoRzV-jiz4qCeb_BKzs31_Fu` from an environment
   that has Google connected (that id is already
   `DEFAULT_REFERENCE_SPREADSHEET_ID`).

Then, in the accounting desk:

* check the **Location / Destination** column on both queues — any row still
  blank had no location in the source;
* check the converted FCFA supplier cost on the CFA queue;
* select the rows and push.

## Tests

`tests/test_historical_order_import.py` — 36 tests, all offline, no network
and no Google credentials required. Covers header mapping, currency
routing, money parsing (`₦45,000`, `35 000`, `12,50` decimal comma), both
conversion directions, location canonicalisation and the "never invent" rule,
item/date parsing, duplicate and generated ids, the exact 12-column output,
and end-to-end reads through the CSV reader.

Every built row is also fed through `accounting.entry_from_order`, proving an
imported order is indistinguishable from a live confirmation by the time it
reaches the desk.

Full gate: **2,180 tests — 0 failures, 32 skipped** (the skips are Playwright
chromium, which only runs in GitHub CI).
