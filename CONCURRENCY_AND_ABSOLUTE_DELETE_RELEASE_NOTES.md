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

### 1a. Per-field merge — the follow-up, so neither admin loses work

Row-level last-write-wins still reverts a *field* another admin changed. The
editor now ships the row as it was when it was **opened**, and the server
does a three-way merge on the fields it understands:

| | admin changed it | admin left it alone |
| --- | --- | --- |
| **someone else changed it too** | the admin wins | **the newer value is kept** |
| **nobody else touched it** | the admin wins | identical either way |

So a rename on the phone no longer reverts a price set on the laptop, and the
save banner says what it preserved: *"Kept the newer value of priceNgn, which
you had not changed."* Nothing is ever refused — this is a merge, not a guard.

**The subtlety that makes it correct.** Comparing the edited row against the
opened row cannot tell *"the admin never opened this box"* from *"the admin
deliberately set it back to the value it already had"*. Both look identical,
and "put the price back to 5,000" is an ordinary thing to do. Guessing wrong
silently discards a real edit — far worse than the revert being fixed. The
editor therefore reports **which controls were actually touched**, and that
report decides. Without it (an older client, an API call) the base comparison
is a best-effort fallback.

**A second subtlety, found while verifying.** The row the editor snapshots is
the *public* catalogue projection, which deliberately omits `stock`. A key
that is **absent** is not "unchanged", it is *unknown* — and reading unknown
as unchanged made the merge overwrite the admin's own stock with the stored
value while announcing it had "preserved a newer change". **No evidence means
no preservation**: a field the base never carried keeps the admin's value.

**Deliberately narrow.** Only fields in `MERGEABLE_FIELDS` are merged;
everything else keeps plain last-write-wins. Per-variant maps
(`optionStock`, `optionPrices`, …) are merged per key, so one variant's
quantity cannot wipe another's, and a variant added since the editor opened is
never dropped. Callers that send no base copy — API integrations, CSV imports,
the supplier watchdog — are completely unaffected.

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

### The SQL is now executed by the test suite, and it had two real bugs

`pgserver` (already a test dependency) ships a real PostgreSQL, so
`tests/test_hard_delete_sql.py` runs the migration through `psql` — the same
way it gets pasted into the Supabase SQL editor — and checks the state it
leaves behind. Reading the file would not have found either of these.

**1. `grant execute … to service_role` aborted the entire script.** A bare
`GRANT` to a role that does not exist is an error, and the error rolled back
everything above it. The revokes were already guarded; the grant was not. It
worked on Supabase, which is the only place anyone was going to run it, and
would have failed on staging or on any future database. The guard now checks
`pg_roles` first.

**2. A function that deletes the catalogue was executable by anyone.**
`CREATE FUNCTION` grants `EXECUTE` to `PUBLIC`, and on Supabase the `public`
schema is the one PostgREST exposes. As written, a visitor who could reach
the shop's API could POST a list of product ids and empty the shop — a
`SECURITY DEFINER` function with no `REVOKE`. It is now revoked from `PUBLIC`,
`anon` and `authenticated` (those roles only exist on Supabase, so their
absence is not an error) and granted to `service_role`, which is server-side
only.

**Also changed, for a failure mode the first two would have hidden:** one
`product_reviews` row pointing at a product deleted years ago would have
failed the foreign-key validation and rolled back the whole file — so *none*
of the cascades would have been applied. The foreign key is now added
`NOT VALID` first (which checks no existing rows, and means the table is
never left with no foreign key at all), the old non-cascading constraint is
dropped, and only then is it validated. A table that still cannot take it is
left **exactly** as it was, with a `NOTICE` saying so, and the other tables
keep their cascade.

Worth being clear about what the SQL does and does not buy: **the app already
deletes correctly without it.** `_purge_product_children()` sweeps all seven
child tables unconditionally, whether or not the RPC succeeded, and the
end-to-end delete test passes on a database where the migration has never
been run. The migration makes the cascade a property of the schema, so a
future table or a future code path cannot quietly orphan rows. Applying it is
an improvement, not a prerequisite.

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

`python3 -m pytest tests/` — **1681 passed, 0 skipped** locally, plus 8
Playwright browser tests that need a real browser and run in CI. New:
`tests/test_absolute_delete_and_ghost_guard.py` (12),
`tests/test_product_field_merge.py` (28), `tests/test_hard_delete_sql.py`
(10, against a real PostgreSQL).

Two live harnesses run against a real gunicorn server:

- `tests/_admin_editor_verify.mjs` — **52/52**. Loads the real `admin.html` and
  scripts in jsdom (the browser CDN is network-blocked in this sandbox), signs
  in over HTTP, and drives the actual UI: filter to a category, click a
  product card, edit the name, submit the form, and measure what the admin
  sees. Also runs a two-device stale-save race, a two-admin different-field
  merge, and deletes a product then re-reads the catalogue six times.
- `tests/_ghost_restore_verify.py` — **21/21**. Creates a product, hard-deletes
  it, then forces the stale copy back into the catalogue so the only thing that
  can stop the re-create is the guard — and runs the tick, the nightly sweep
  and repeated passes afterwards.

Measured on the verification server: **save round trip 76–116 ms** (budget:
2 s), concurrent saves 25–60 ms, no `409`, no "changed by someone else", editor
closed automatically, returned to the source category (`filter=gadgets`), the
other admin's price surviving our rename, a deliberate re-price honoured, and
the deleted product absent after every refresh.

> jsdom executes the shipped JavaScript but is not a real browser engine, and
> the verification server runs the local (non-Supabase) storage backend. The
> Supabase path is covered by unit tests with PostgREST doubles plus
> `hard_delete_products.sql`, which is **not yet applied to the live
> database** — run it once in the Supabase SQL editor to switch production over
> to the single-statement cascade.

## Files

| File | What changed |
| --- | --- |
| `api.py` | 409 guard replaced by last-write-wins + `product.save_overwrite` receipt; per-field merge wired into the same funnel |
| `product_merge.py` | **new** — the three-way merge, with the touched-field report |
| `js/store.js` | 409 rollback branch removed; token/base/touched-list are request-only |
| `js/admin.js` | banner after redirect; filter boxes restored; category never blanked; base snapshot + touched-field tracking |
| `js/net.js` | failed jobs dropped so the pill closes; "Retrying" vs "Syncing" |
| `supabase_store.py` | SQL cascade with an identical fallback; all named child tables swept |
| `supplier_watchdog.py` | deleted-id re-check immediately before every write |
| `hard_delete_products.sql` | **new** — real `ON DELETE CASCADE` + the delete function |
| `tests/test_absolute_delete_and_ghost_guard.py` | **new** — 12 tests |
| `tests/test_hard_delete_sql.py` | **new** — 10 tests that execute the migration |
| `tests/test_product_field_merge.py` | **new** — 28 tests |
| asset cache token | bumped 178 → 179 (shipped JS changed) |
