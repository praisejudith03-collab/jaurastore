# Storefront Visibility, FR/EN Boot & Free-Tier Memory — Release Notes (2026-10-20)

Directive: **new product catalog sync, bulk visibility, localization & render
memory recycling**. This release closes the four reports behind it and adds the
tests that make each one fail the build if it ever comes back.

---

## 1. A newly saved product now appears on the shop grid

### What was actually wrong

The API was innocent: every save was written, `/api/catalog` and
`/api/products` served the row immediately (verified end-to-end). The grid
dropped it in the browser:

1. **Frozen price bounds.** `renderShop()` derived the price-range sliders from
   the *first* catalogue it painted and then applied those bounds as a hard
   filter. On a French device the currency is locked to F CFA, and every
   Naira-anchored row converts to 0 until `/api/site` delivers the live rate —
   so the derived floor was a price most of the catalogue sat below.
   **Measured before the fix: 259 products → 1 rendered card.** The same bound
   hid a brand-new product with no price entered yet, while the admin order
   form (which does not use the grid filter) showed it everywhere.

2. **"Newest" sorted nothing.** The default sort kept the server's id order, so
   a row saved this morning landed on the last page and read as missing.

### The fix (all in `js/app.js`, display-only)

* `shopFilter.touched` — price bounds are a **display hint** until the shopper
  moves a slider; an untouched range never filters. Bounds are re-derived from
  the live catalogue on every paint, so a range painted before the exchange
  rate arrived cannot hide rows.
* `shopPrice()` — an unknown price is `0` and is **never** treated as "below
  the floor"; unpriced rows render as *price on request* ("Prix sur demande").
* Changing category or clearing the filter resets `touched`/`min`/`max`, so a
  range chosen for one category cannot hide the next one.
* "Newest" now sorts on `createdAt`/`created_at`/`updated_at` (`productStamp`),
  stable for the bundled seed rows that carry no date. A row saved now opens
  **page 1**; the homepage "Just in" rail uses the same order.
* Category pills (`shopCategoryIds()`) include any category id that a live
  product uses but the category table does not know, so every product is
  reachable from a filter (previously such a row existed only under
  "All products").
* `renderCards()` isolates a single malformed row (`console.error` names the
  product id) instead of blanking the whole grid.
* The count line is localized (`productCountLabel()`), and pager/most-viewed
  labels have real `aria-label` translations.

### Server-side visibility made measurable

* `/api/catalog` `meta` now carries `publishedCount` and `hiddenCount` beside
  the unchanged `count`; a public answer smaller than `count − hiddenCount` is
  a serializer bug and shows up in the numbers, not in a customer's complaint.
* `GET /api/admin/catalog/audit` (admin only) returns the whole pipeline:
  `tableRows → mergedRows → onlineRows → publishedRows`, plus `hidden`,
  `missingFromPublic` (must always be empty), `unexpectedInPublic`,
  `noCategory`, `noPrice`, `placeholderPhoto`. Add `?ids=1` for the published
  id list, `?limit=` to cap the lists.

### Photos

Grid cards keep `<picture><source type="image/webp"> <img …></picture>` with
`loading="lazy"`, `width/height` 400 (no layout shift), `decoding="async"` and
the branded placeholder as `onerror` fallback. Repository photos use the
committed `.400w.webp` sibling; **uploaded** photos never fabricate that
sibling (a missing WebP is a broken image, not a fallback) — they are served
as the original upload with the placeholder fallback, and the upload pipeline
still writes the real `.400w.webp` companion in the background.

---

## 2. 100% French — before *and* after the API answers

`tests/_french_pages_check.mjs` now renders **every storefront page twice**:

* **cold** — no backend answers (the first paint on a slow phone), and
* **after API boot** — `/api/site`, `/api/catalog` and `/api/categories`
  answered, so the page is judged on its settled, data-filled state.

That second pass found copy that only exists after boot. Fixed in this release:

| Where | Was | Now |
| --- | --- | --- |
| `order-complete.html` (no order on this device) | "We could not load this order on this device." | `ck.orderMissing` (EN/FR) |
| Checkout "order queued offline" note | two English sentences | `ck.queuedSaved` / `ck.queuedWaiting` (EN/FR) |
| "Order ID copied" toast | English | `toast.orderIdCopied` (EN/FR) |
| Grid pager buttons | `aria-label="Previous page"` | `pager.prevLabel` / `pager.nextLabel` (EN/FR) |
| Live-catalogue skeleton | "Loading live products and stock…" | `shop.loadingLive` (EN/FR) |

Both dictionaries stay key-identical (`tests/test_french_copy_complete.py`),
and the server still stamps `<html lang="fr">` before the first byte.

---

## 3. Free-tier memory recycling — buffers under 3 MB, `/healthz` under 50 ms

The instance is kept awake **on purpose**: UptimeRobot stays at **15 minutes**
(not shorter), and the app's own keep-alive still pings `/healthz` every five
minutes. That makes explicit recycling non-optional.

### `/healthz` is now a pure liveness probe

* No Supabase call, no SQLite query, no scheduler work on the request.
* Event-loop + SQLite checks are cached for one second; the Supabase verdict is
  refreshed by a background daemon at most every 10 s and read from memory.
* **Measured on gunicorn (`--workers 1 --threads 8`, production config):
  p50 1.19 ms, p95 1.63 ms, max 16 ms (cold first call), 0.08 ms server-side.**
* A single failed probe is reported as `degraded` and still answers 200 (a blip
  must not roll the instance). **Three consecutive failures (≈30 s) answer
  503**, which is what makes Render recycle a genuinely wedged process on its
  own — no "Restart Service" click.
* `GET /health` and `GET /api/health` keep the full synchronous report for
  humans and `tools/catalog_watchdog.py`.

### Request-scoped collection

`memory_manager` collects on `teardown_request` (after the response is built,
after every `after_request`, exactly once per request) — `gc.collect(1)` for an
ordinary request, throttled to one sweep every 2 s, and a full `gc.collect()`
after a multipart upload. Health probes, static assets and `404`s are exempt
from the accounting; `404`s still release their slot.

### Idle pool + cache reaping

Every five minutes the scheduler calls `memory_manager.idle_tick()`. When the
shop has had **no request and no Supabase access for 10 minutes**, it:

1. closes the shop's and the health probe's httpx connection pools
   (`supabase_store.recycle_clients`),
2. drops the serialized catalogue snapshot, the read cache and the French
   description cache (all rebuilt on the next request),
3. runs a full collection and `malloc_trim(0)` to return freed pages to the OS.

A busy shop is never disturbed: an in-flight request, recent request activity
or a recent background Supabase read all postpone the recycle. Tunable with
`SUPABASE_POOL_IDLE_SECONDS` (default 600) and `JAURA_GC_INTERVAL` (default 2).

### The 3 MB buffer ceiling

* The gzip handler no longer materialises a body it cannot compress cheaply:
  a declared length above **3 MB** is sent uncompressed, and a body that
  grows past 3 MB after reading is also sent as-is. No response is ever
  duplicated in memory beyond that ceiling.
* `/api/admin/memory` reports RSS, collector counters, last recycle and the
  configured limits; `POST /api/admin/memory/recycle?force=1` performs one on
  demand. Both are admin-only.

---

## 4. Verification

New/extended suites (all green):

| Suite | What it proves |
| --- | --- |
| `tests/test_catalog_visibility.py` | 1,205-row table served whole by `/api/products` + `/api/catalog` (no pagination, no cap); brand-new category/date/price never gate publication; only hidden/deleted rows are withheld; opt-in watchdog pages cover everything; audit endpoint traces every stage |
| `tests/test_storefront_grid_visibility.py` + `tests/_storefront_grid_check.mjs` | real `shop.html` in jsdom: 259/259 held, untouched range filters nothing, full pages, pagination covers every id once, new rows on page 1, unknown-category pill, lazy/sized/WebP photos, no zero prices, "prix sur demande", CFA conversion at the live rate |
| `tests/test_memory_recycling.py` | post-response collection (never before the body), throttling, full sweep on upload, idle-only pool reaping, busy-shop protection, scheduler tick wiring, admin controls, `<50 ms` `/healthz`, 3 MB ceiling |
| `tests/test_health_checks.py` | `/healthz` does no network I/O, answers <50 ms, degrades then 503s after a sustained outage; `/health` + `/api/health` still fail closed immediately |
| `tests/test_french_storefront_pages.py` (extended) | 17 pages × cold + after-API-boot, `lang=fr`, zero English nodes |
| `tests/_store_sim.mjs`, `tests/test_zero_cache_policy.py`, `tests/test_asset_cache_token.py` | store keeps every row; asset token bumped to `v217` and fingerprints refreshed |

Full suite: see the run recorded with this change (`pytest tests/ -q`).

## 5. Deploy notes

* No schema migration and no new required environment variable.
* Asset token `v216 → v217` (all HTML, `sw.js`, bundles and test constants), so
  a returning phone cannot keep the old bundle.
* Render still points `healthCheckPath` at `/healthz`; nothing in the dashboard
  needs changing, and UptimeRobot stays at 15 minutes.
* `GET /api/admin/catalog/audit` is the first stop for any future "saved but
  not in the shop" report: `missingFromPublic` empty means the API served it,
  and the client-side causes are then `noCategory` / `noPrice` /
  `placeholderPhoto` / `hidden` rather than a mystery.

---

## 6. Post-deploy verification (follow-up)

Local tests prove the code; two checks now prove the deployed service:

* `python3 tools/post_deploy_check.py` — read-only, no credentials. Verifies
  `/healthz` inside the 50 ms budget (≥ 80 % of samples), the catalogue's
  `publishedCount == len(products)` invariant, a non-zero live exchange rate,
  a French first paint with a single asset token, and that the catalogue audit
  is admin-only. Exit `0` pass, `1` a failed check, `2` nothing answered.
* `node tests/_toast_check.mjs` — loads the shipped `js/store.js` in jsdom and
  drives the real toast: one element, green `data-kind="success"`, the exact
  save sentence, reused for the next notice, red for errors, hidden on its
  timer.

See `POST_DEPLOY_VERIFICATION.md` for the check table, sample output and the
triage steps when `catalog` fails.
