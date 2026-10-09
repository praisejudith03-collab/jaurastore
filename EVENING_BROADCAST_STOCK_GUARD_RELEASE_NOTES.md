# Evening broadcast, historical import engine & inventory watchdog

Three production features built into the codebase.

---

## 1. In-stock broadcast post generator

**`/admin → Marketing → Generate evening broadcast`**, backed by
`POST /api/admin/broadcast/evening-post` (admin + CSRF).

One click builds the evening Telegram / WhatsApp post from **live database
rows**, never from what a browser happens to have cached:

- grouped by category, products sorted within each group;
- only **verified in-stock** options/colours, each with its remaining count;
- prices in **Naira and F CFA**, converted with the storefront's own
  `currency.to_cfa`, so a post can never disagree with the product page;
- a **direct deep link** under every line;
- a plain-text variant alongside the markdown one (some channels strip
  formatting — the plain version has no `*`/`_` markers at all).

**One correction to the requested URL.** You asked for
`https://jaurastore.com.ng/product/<slug>`. The storefront actually serves
`/products/<slug>` (plural) — `app.py:760`, and the sitemap and Open Graph
tags both use the plural form. Singular `/product/<slug>` does not resolve, so
broadcasting it would send customers to a 404. The generator uses the plural
route, and a test asserts it. Say the word if you'd rather I add a singular
redirect instead.

**Nothing is sent and nothing is written.** The endpoint returns copy; you
read it, then hit *Copy to clipboard* (with a select-the-text fallback for
non-HTTPS or denied clipboard permission).

The report under the button shows products, in-stock options, categories, the
rate used, and how many were skipped as out-of-stock or hidden — so you can
see the filter working rather than wondering where a product went.

---

## 2. Historical CSV importer engine

`import_historical_orders.py` (built earlier) is now wired into the app at
`POST /api/admin/accounting/import-historical` (admin + CSRF).

- Accepts an **uploaded `.csv` / `.tsv` / `.xlsx`** file, or raw CSV text.
- **Stages — never writes to the ledgers.** Rows land in the staging queue,
  where the accounting desk shows them like any fresh confirmation, to be
  reviewed and pushed by hand.
- Header normalisation, legacy mapping, destination locations and item unit
  costs all as before.
- **Conversion rate** now defaults to the **active** admin rate
  (`accounting.current_exchange_rate`), as you asked. `--legacy-rate` (or
  `"legacyRate": true`) pins `accounting.LEGACY_RATE` instead — worth
  considering for old orders, because repricing them with whatever the rate is
  later makes historical profit totals drift. `--rate` overrides both.
- **Dry run by default.** Without `confirm` nothing is written; the response
  carries per-currency counts, the column mapping, warnings and a preview.
- Uploads over 8 MB are refused rather than parsed and half-imported.

> **One thing did not exist as specified.** You asked for ingestion into a
> `staging_orders` table. There is no such table in this schema — the
> staging queue *is* the `orders` table: a confirmed order whose accounting
> snapshot has not been pushed yet. That is what the accounting desk reads and
> what the push button archives, so importing there makes rows visible
> immediately. Creating a separate `staging_orders` table would need the desk
> re-wired to read it too, and until then imported rows would be invisible.
> Happy to add it as a genuine holding area if you want one — just say so.

---

## 3. Inventory watchdog & out-of-stock guard

The catalog watchdog used to compare Supabase rows against the public
catalogue and alert on **missing** products. It never looked at quantity, so a
colour that sold out at 9am was still being posted at 8pm.

`tools/catalog_watchdog.py` now also:

- measures **per-SKU and per-variant stock** every run (`stock_snapshot`);
- diffs against the previous run and reports **In-Stock → Out-of-Stock**
  transitions (and restocks) as `STOCK out: <name> [<colour>] <sku>`;
- asks for `slug`, `stock_quantity` and `"optionStock"` in its query (the
  camelCase column is quoted — Postgres folds it otherwise);
- persists the reading to `growth_settings` under
  `watchdog_stock_state_json`, the same key/value map the app reads, so
  yesterday's measurement survives the GitHub Actions runner being wiped.

**Broadcast integrity guard.** The generator reads that blocklist before
building a post (`broadcast_posts.watchdog_out_of_stock`). A variant the
watchdog watched hit zero is excluded **even when the row in front of it still
claims stock** — so a stale tab, a cached CDN copy or a lagging mirror can
never resurrect a sold-out colour. The skip is reported as
`out of stock (watchdog)` rather than `out of stock`, so you can tell which
guard fired.

The guard **fails open to the database**: if the watchdog has never run or
Supabase is unreachable, the blocklist is empty and the post falls back to
reading stock directly, which is still the truthful source. It never blocks
everything on a read error.

---

## Files

| File | Change |
| --- | --- |
| `broadcast_posts.py` | new — in-stock post generation + OOS guard |
| `tools/catalog_watchdog.py` | stock snapshot, diff, durable persistence |
| `api.py` | two admin endpoints |
| `js/admin.js` | evening broadcast card + copy button |
| `css/style.css` | styles for the new card |
| `import_historical_orders.py` | active-rate default, `--legacy-rate` |

## Tests

70 new tests, all offline:

- `tests/test_evening_broadcast.py` (28) — availability, watchdog override,
  dual-currency prices, option overrides, deep links, grouping, plain variant,
  skip reporting, junk input, no hardcoded rate.
- `tests/test_watchdog_stock_guard.py` (24) — option parsing incl. legacy JSON
  strings, snapshots, the blocklist, transitions (out/restock/new/unchanged),
  disk round-trip, and an end-to-end check that what the watchdog measures is
  what the post excludes.
- `tests/test_evening_broadcast_endpoint.py` (18) — auth + CSRF on both
  endpoints, in-stock-only output, watchdog deference, file and XLSX upload,
  size refusal, dry-run-never-writes.
