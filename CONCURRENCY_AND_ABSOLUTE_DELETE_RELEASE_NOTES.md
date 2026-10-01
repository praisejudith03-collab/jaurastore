# Product editing, concurrency, absolute delete and the 2:00 AM audit

An emergency pass over the five reports: saves rejected with "This product was
changed by someone else", the editor not letting go after a save, a hanging
"Syncing 1 change" pill, deleted products reappearing, and the nightly
supplier watchdog.

Everything below was verified against a running server with the real
`admin.html` / `js/admin.js` / `js/store.js` / `js/net.js` and the real
`supplier_watchdog` / `scheduler` — see "How this was verified".

---

## 1. Saves are last-write-wins. The 409 is gone.

**What was wrong.** A previous pass added a freshness guard: the editor
shipped the row's `updated_at` as it was when the editor was *opened*, and the
server answered `409 This product was changed by someone else while you were
editing` whenever the stored row had moved on.

It mis-fired constantly, because `updated_at` moves for reasons that have
nothing to do with a human editing anything:

- the 5-minute supplier watchdog writing a stock number,
- a cache re-hydration or a repo mirror,
- **a second tab that merely opened the same product**,
- an API integration.

So a perfectly good save was refused, and the only way out the popup offered
was "close the editor and re-apply everything by hand". Multiple admins on
phones and laptops were locked out of their own shop.

**What it is now.** Every save through `_product_save_response` is
unconditional; the newest complete copy wins, from any device, for any number
of admins. There is no longer a version key whose movement can block a save,
which is also why autosaves, watchdog runs and background mirrors can no
longer interfere with an open editor — structurally, not by convention.

The opened-at token is still sent, but only as a **receipt**: when a save
replaced a newer row it is written to the audit trail as
`product.save_overwrite` (who, which row, what it replaced) and returned as
`overwrote` + `notice`, which the editor appends to the success banner. A
crossed edit is traceable; it is never a popup.

> **The trade-off, stated plainly.** Last-write-wins means a save is
> row-level, not field-level. If admin A changes a price and admin B changes a
> photo from an editor opened earlier, B's save carries the whole row and A's
> price change is reverted. That is inherent to what was asked for — the
> alternative is a merge that decides per field which admin "owns" it. The
> audit entry above is what makes it recoverable rather than silent.

## 2. Saving exits to the source list

Already largely in place, with two gaps closed:

- The success banner is now raised **after** the redirect, so it lands on the
  list the admin was returned to rather than on an editor that is gone.
- `restoreProductsReturn()` now re-fills the category box and the search box.
  The grid was filtered but the controls read as unfiltered, so the first tap
  on either one moved the products somewhere the admin did not ask for.

### A third bug found while verifying this: saving could erase a product's category

The editor's category `<select>` is built from the category table. When that
table is empty or has not loaded yet, the select rendered with **zero
options**, the form submitted an empty value, and the save wrote
`category: ""` — quietly taking the product out of its category and out of
every category-filtered list the owner had set up. Reproduced live against a
server with an empty category table.

Fixed twice over: the form always offers the product's own category as an
option, and `handleProductSubmit` never writes an empty category over a row
that already has one.

## 3. The "Syncing 1 change" pill

The offline outbox flagged a permanently failed job `dead` and **kept it**.
Nothing ever removed it, and the pill counted it for the rest of the session —
long after the admin had moved on.

- A permanently failed job is now **dropped** once the admin has been told
  once, in a toast that names the reason. The queue returns to empty and the
  indicator closes immediately.
- A job sleeping out its retry backoff now reads **"Retrying N changes in Xs"**
  rather than "Syncing" — a job waiting is not a job sending, and saying
  "Syncing" made the pill look frozen.

## 4. Absolute delete: SQL cascade + the ghost-restore race

**Schema (new file `hard_delete_products.sql`).** The delete used to be a list
of per-table calls in Python, so any table added later was simply left behind.
The cascade is now a property of the schema:

- `product_variants`, `product_prices`, `product_options` are created if
  absent (some deployments normalise options into their own tables; this shop
  keeps them as JSON columns on `products`);
- every per-product table gets `ON DELETE CASCADE` to `products(id)`;
- `hard_delete_products(text[])` is one statement that returns **only the ids
  that were really there**, so the app can never answer "deleted" for a row
  that survived.

`supabase_store.hard_delete_products()` calls that function when it exists and
falls back to the identical table-by-table delete when it does not. The
durable tombstone stays single-sourced in
`growth_settings.deleted_product_ids_json` — two lists would eventually
disagree, and the disagreement is exactly how a product comes back.

**The real cause of ghost restores.** Candidate selection already skipped
deleted ids, but selection and write are *minutes* apart on a real catalogue —
the watchdog fetches a supplier page in between. Deleting a product inside
that window made the watchdog the **last writer**: `catalog.upsert()`
re-created the row *and* cleared the durable tombstone on the way (that is
what un-hides a re-created product), silently undoing the owner's deletion.

`supplier_watchdog._save_synced()` now re-checks the durable deleted list
immediately before the write — the only place the window can actually be
closed — and reports `product_deleted_during_sync` instead of writing. The
`permanently-removed` answer is also checked before the "did it save?" test, so
it is no longer misreported as a failed supplier sync.

## 5. The 2:00 AM supplier watchdog

**Confirmed present and correct.** Render's free tier has no cron, so the
in-process loop *is* the cron: each 5-minute tick asks whether the nightly
hour has passed and the deep pass has not run today. Default 02:00 in the
owner's timezone (UTC+1), tunable via `SUPPLIER_WATCHDOG_NIGHTLY_HOUR` /
`SUPPLIER_WATCHDOG_NIGHTLY_TZ_OFFSET` / `SUPPLIER_WATCHDOG_NIGHTLY=0`.

The pass runs the full supplier sweep and then the storage sweeper, which
purges orphaned and duplicate upload media through the same protected plan as
the admin's Storage cleanup card (live references are never candidates, a
failed reference scan aborts the sweep rather than treating unreadable
references as orphans, and recent uploads are protected by a grace window).
`_health` reports `nightlyLastRun` and `nightlySchedule`.

## How this was verified

`python3 -m pytest tests/` — **1643 passed, 21 skipped**. New:
`tests/test_absolute_delete_and_ghost_guard.py` (12 tests).

Two live harnesses run against a real gunicorn server:

- `tests/_admin_editor_verify.mjs` — **42/42**. Loads the real `admin.html` and
  scripts in jsdom (the browser CDN is network-blocked in this sandbox), signs
  in over HTTP, and drives the actual UI: filter to a category, click a
  product card, edit the name, submit the form, and measure what the admin
  sees. Also runs a two-device stale-save race, and deletes a product then
  re-reads the catalogue six times.
- `tests/_ghost_restore_verify.py` — **20/20**. Creates a product, hard-deletes
  it, then forces the stale copy back into the catalogue so the only thing that
  can stop the re-create is the guard — and runs the tick, the nightly sweep
  and repeated passes afterwards.

Measured on the verification server: **save round trip 76–90 ms** (budget:
2 s), concurrent saves 25–60 ms, no `409`, no "changed by someone else", editor
closed automatically, returned to the source category (`filter=gadgets`), the
deleted product absent after every refresh.

> jsdom executes the shipped JavaScript but is not a real browser engine, and
> the verification server runs the local (non-Supabase) storage backend. The
> Supabase path is covered by unit tests with PostgREST doubles plus
> `hard_delete_products.sql`, which is **not yet applied to the live
> database** — run it once in the Supabase SQL editor to switch production over
> to the single-statement cascade.

## Files

| File | What changed |
| --- | --- |
| `api.py` | 409 guard replaced by last-write-wins + `product.save_overwrite` receipt |
| `js/store.js` | 409 rollback branch removed; token is a receipt |
| `js/admin.js` | banner after redirect; filter boxes restored; category never blanked |
| `js/net.js` | failed jobs dropped so the pill closes; "Retrying" vs "Syncing" |
| `supabase_store.py` | SQL cascade with an identical fallback; all named child tables swept |
| `supplier_watchdog.py` | deleted-id re-check immediately before every write |
| `hard_delete_products.sql` | **new** — real `ON DELETE CASCADE` + the delete function |
| `tests/test_absolute_delete_and_ghost_guard.py` | **new** — 12 tests |
| asset cache token | bumped 178 → 179 (shipped JS changed) |
