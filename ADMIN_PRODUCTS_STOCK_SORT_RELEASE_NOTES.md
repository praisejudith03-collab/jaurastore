# Admin Products: Out of Stock section + Newest/Oldest sort

Owner request, 2026-11-23: catalogue housekeeping on a phone must be fast.

## 1. Out of Stock, in one place

The Products desk now carries an **Out of Stock** quick filter next to the
All / Live / Hidden tabs, with a count badge (`Out of Stock 4`):

* It collects **both** kinds of stockout:
  * a single item whose own quantity has run to 0;
  * a product that sells options where **every** option is at 0, even if the
    cached product-level total still reads 24. This is the same rule the
    storefront's Buy button already uses, so the desk and the shop can never
    disagree (`productStockUnits()` / `productIsOutOfStock()`).
* The count is **catalogue-wide** - hidden rows included - so it is the
  owner's real restock to-do list, and it also appears in the counter line
  under the filters (`4 Out of stock`).
* It **composes** with everything else: `Out of Stock + Hidden` (sold-out
  drafts), `Out of Stock + Bags` (sold-out bags), `Out of Stock + search`.
* Tapping the chip again switches it off; the chip reads muted when nothing is
  out of stock.
* A variant product's card pill now follows the same rule, so a piece whose
  options are all gone reads "Out of stock" on its card even when its stored
  total is stale.

## 2. Newest / Oldest sort

A sort dropdown sits with the search and category filters:

* **Newest to Oldest** (default) and **Oldest to Newest**.
* It never disturbs the other filters or the ticked products - a re-sort only
  re-orders what is already on screen and returns to page one.
* Both the sort and the stock filter are part of the captured list position, so
  opening a product from the restock list and saving returns to *that* list
  (also via `?stock=out&sort=oldest` in the address bar).

### Where the date comes from

`created_at` wins (the date the piece was **added**), then `updated_at` (older
rows that predate the column), then the order the id encodes - the shipped
legacy ids, and `jau-<base36>`, which *is* a timestamp for pieces added in the
desk. A malformed date can never scramble the list (non-numeric stamps are
ignored, ties keep catalogue order).

`products.created_at` is new, additive and **database-owned**:

* `migrations/20261123_products_created_at.sql` (also in `supabase_schema.sql`,
  `schema_sections/01_products.sql` and the boot-time `auto_migrate.py`).
* Added **without** a default so existing rows keep NULL (they sort by
  `updated_at`), then `set default now()` so every row inserted from now on is
  stamped. **No UPDATE runs**, so the catalogue write-lock is untouched.
* `supabase_store._upsert_products_resilient()` strips `created_at` from every
  product write: a save echoes the whole row back, and a stale browser copy
  must never be able to move a product's "added" date. It also means a database
  that has not run the migration yet can never refuse a save over the column.

## 3. Mobile

* The Out of Stock chip takes its own full-width row (thumb-sized tap target).
* Search takes a full row, then the category and date selects share one - four
  stacked controls were pushing the first card off a phone screen.
* Red `.adx-oos-chip` / `.adx-oos-link` styling, muted when fully stocked.

## 4. Tests

* `tests/test_admin_products_stock_sort.py` (new, 9 tests): variant-level
  stockouts, the catalogue-wide badge count, the filter composing with tab /
  category / search, newest + oldest ordering (including created_at beating a
  later `updated_at` and the id fallback), the sort dropdown keeping the other
  filters and the ticked rows, the return-URL round trip, the wiring of both
  controls, the mobile CSS hooks, and the schema/migration/write-path guards
  for `created_at`.
* The two existing products-desk VM harnesses now load the new helpers.
* Full suite: **2705 passed, 41 skipped** (`python3 -m pytest tests -q`).
* `tools/asset_version.py --write` bumped the shared token for the changed
  `js/admin.js` and `css/style.css`.
