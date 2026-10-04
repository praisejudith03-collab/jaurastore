# Masterpiece overhaul — audit and regression report

Scope: the Python routes, the background workers, the SQL delete path and the
storefront/admin JavaScript. Every finding below is either fixed and pinned by
a test, or listed as deliberately unchanged with the reason. Nothing in this
file claims a change that is not in the tree.

Run the whole thing with:

```bash
python -m pytest tests/ -q          # ~1870 tests
node -e "1" >/dev/null && for f in js/*.js sw.js; do node --check "$f"; done
```

## 1. Deletions: the timeout, and what an honest fast answer looks like

| # | Finding | Severity | Action |
|---|---------|----------|--------|
| 1.1 | `DELETE /api/admin/products/<pid>` ran the whole Supabase half (atomic RPC, child cascade, Storage purge, abandoned-cart sweep) inside the request. A product with a gallery took seconds; the browser gave up with "Could not reach server" and the operator retried work that was already running. | critical | Fixed: the request writes the durable tombstone (one small write), hides the product locally, queues the heavy half on `task_queue`, and answers `200 {deleteMode: "queued", jobId}`. `?sync=1` keeps the old blocking, fail-closed contract. |
| 1.2 | A product that is tombstoned must stop selling *immediately*, not when the RPC finishes. | critical | Fixed: `catalog.hide_now()` writes the same local `deleted` list the synchronous path writes, so `merged()` stops serving the product on the next request in this process; the Supabase tombstone covers every other process and every redeploy. |
| 1.3 | The delete used to claim success only when the RPC confirmed the row; the async path must not weaken that into "queued and forgotten". | critical | Kept: a tombstone write that is not confirmed is still a `503` with "tombstone" in the error (nothing is queued). A queued job that exhausts its retries parks as `failed` with its reason, visible in the job panel, and the tombstone keeps the id out of the shop. |
| 1.4 | Receipt and discarded-upload deletes had the same shape (Storage round trip + row delete in the request). | high | Fixed for the production paths: queued (`receipt.delete`, `media.delete`) with `deleteMode: "queued"`; the local paths stay synchronous because they touch only SQLite/disk and are already fast. |
| 1.5 | Idempotency: a retried delete finds the row already gone; the RPC reports only ids that were there. | high | Fixed: `_task_product_delete` treats "nothing deleted **and** no error" as done (the RPC's documented contract) and retries when the report carries errors. A wrong "already gone" reading would have been a silent failure - found by `test_hard_delete_live_path.py` against a real PostgreSQL. |
| 1.6 | The portal turned a successful async delete into "Could not reach the server", because `js/store.js` treated any `{queued: true}` as an unreachable-server outbox reply. | high | Fixed: the outbox reply has no `deleteMode`; the server's async reply does. The two are now distinguished, and the toasts say "photos are purged in the background". |

Tests: `tests/test_async_deletions.py` (14), `tests/test_site_settings_supabase.py`
(async + `?sync=1` + tombstone-failure cases), `tests/test_hard_delete_live_path.py`
(real PostgreSQL: queued answer, worker completion, replay, no-migration
failure, retry from the panel), `tests/test_deleted_products_stay_deleted.py`
(unchanged, still green).

## 2. 512MB hardening and worker stability

| # | Finding | Severity | Action |
|---|---------|----------|--------|
| 2.1 | Batch jobs processed a whole page in one call (photo repair: 60 rows; broadcasts: every recipient in the request). | high | Fixed: `scheduler.CHUNK_SIZE` (5, hard-capped at 10) drives `_chunked()`, and `task_queue.BATCH_SIZE` (5, capped at 10) drives the queue. Both call `gc.collect()` after every chunk. |
| 2.2 | A crashed step aborted the rest of the maintenance tick only if it was wrapped; the queue had no such wrapper at all. | high | Fixed: the queue drains inside its own loop with a per-job boundary, and `tasks.sweep` plus the worker `ensure_alive()` step are isolated like every other `_step()`. |
| 2.3 | Retries hammered a broken dependency (the abandoned-cart retry used a bare `2 ** attempt` with no cap). | medium | Fixed: one `_backoff_seconds()` ladder (2s, 4s, 8s … capped at 60s) shared by the scheduler and the queue; a collection runs between attempts. |
| 2.4 | A single worker death paged the owner through the GitHub notifier (`scheduler.worker_died`), and the queue worker was not supervised at all. | medium | Fixed: the first restart is silent (logged + counted in `/healthz`), a repeat within the same boot is recorded with `notify=False`, and `ensure_alive()` now also restarts `jaurastore-tasks`. |
| 2.5 | Anything holding a page of rows across a tick (supplier watchdog, nightly sweep, stock guardrail) was left to the interpreter. | low | Fixed: explicit `gc.collect()` at the end of each of those steps. |
| 2.6 | Unbounded in-process state | high | Verified bounded: `task_queue._jobs` (ring of 120), `api._broadcast_streams` (2), `_order_image_cache` (60s TTL), `storage._signed_url_cache` (clears at 1024), `storage._object_exists_cache` (clears at 2048, 300s TTL), `supabase_store._read_cache` (fixed key set + TTL + deep copy), `observability._recent` (25). |

Tests: `tests/test_memory_hygiene.py` (17).

## 3. Ghost images and mobile speed

| # | Finding | Severity | Action |
|---|---------|----------|--------|
| 3.1 | A deleted or replaced photo kept rendering from a phone's cache. | high | Verified in place: `PUBLIC_MEDIA_CACHE_CONTROL = "public, max-age=300, must-revalidate"` is sent as the Storage object's `Cache-Control` for public folders, the `/uploads/<key>` route and its 302 both re-send it (proofs are `no-store`), and `sw.js` bounds a cached media hit at `MEDIA_MAX_AGE` (5 min): older copies are revalidated, a purge deletes the entry, and only an offline device is served the stale copy. |
| 3.2 | `loading="lazy"` on every photo render. | low | Verified: `mediaHTML`, `lineThumb`, `orderItemThumb` and the card templates all carry `loading="lazy" decoding="async"`. |
| 3.3 | Compact mobile PDP (title, price, note, buy button on one screen). | low | Verified unchanged: `tests/test_pdp_mobile_layout.py` still green; no PDP markup was touched by this change. |
| 3.5 | One in-division page cost: no in-process table may grow without a cap (a slow leak is what pushes a 512MB instance into an OOM kill, and the owner only sees "worker_died"). | high | Audited and pinned: task queue ring (120), signed-URL cache (clear at 1024), object-exists cache (clear at 2048, 300s TTL), Supabase read cache (deep-copied, TTL), order-image index (60s TTL), broadcast streams (2), `observability._recent` (25). `tests/test_memory_hygiene.py` (13) fails if a cap disappears. |
| 3.4 | WebP: catalogue photos ship `.400w.webp` companions (`tools/make_thumbs.py`, 183 in `images/products/`) and the storefront picks them with `<picture type="image/webp">`. **Uploaded** photos now get the same companion written into the bucket: `storage.save_image` queues a `thumb.build` job, and the worker builds the 400px WebP after the response, so an upload is never slower or fail-able because of it. Older objects are covered by `tools/backfill_thumbs.py` (dry run by default, `--apply` to write, proofs never touched, idempotent). | medium | Fixed (upload side) + tool added. The **markup** still does not advertise a companion for `/uploads/...` photos, and that is deliberate: a `<picture>` whose `<source>` 404s does not fall back to the `<img>` - it replaces the owner's photo with the placeholder ("PHOTO COMING SOON", the round-2 incident). Advertising it safely needs the server to confirm the companion exists per photo (a `thumb` field on the product row) or a redirect route that serves the original when the companion is missing; both are separate changes, and neither is safe to guess at from a phone. Until then the shipped bytes are already small (upload re-encode) and every photo has a companion ready. Pinned by `tests/test_photo_fix.py` (no fabricated source) and `tests/test_backfill_thumbs.py` (16). |

## 4. Inventory watchdog, order thumbnails, custom note

| # | Finding | Severity | Action |
|---|---------|----------|--------|
| 4.1 | Stock watchdog: a product at 0 keeps showing as available between nightly runs. | high | Verified in place: `catalog.stock_guardrail_sweep` runs on the five-minute tick, the storefront disables Add to Cart at 0 (`tests/test_storefront_stock_button.py`), and the admin save/re-stock path cannot pin a sold-out row to "in stock". |
| 4.2 | Order thumbnails: 56×56 in the admin order view, the fulfilment details and the customer email receipts. | medium | Verified in place: `orderItemThumb` (admin), `JA.lineThumb` (storefront receipt), `mailer._items_table` (`width="56" height="56"` with the attributes as well as CSS so a mail client that strips styles still sizes it). Order lines now also carry the resolved `image` (written at checkout, backfilled for older rows from the catalogue with a 60s cache), so the thumbnail is real for orders placed before the field existed. |
| 4.3 | Custom note chain: PDP → cart → checkout → admin records, kept as ONE line. | medium | Verified in place: `enableCustomNote`/prompt on the product, the note travels on the cart line and the order payload, is rendered as `CUSTOMER PROMPT` in the admin order row, and the PDP block is single-line (`tests/test_order_thumbnails.py`, `tests/test_pdp_mobile_layout.py`). |

## 5. Broadcast hub

| # | Finding | Severity | Action |
|---|---------|----------|--------|
| 5.1 | There was no `/admin/marketing/broadcast` - the address 404'd. | medium | Fixed: `app.py` serves the portal shell on `/admin`, `/admin/` and `/admin/<path:rest>`, and `js/admin.js` maps the path to the Marketing desk (`adminPathDesk`), so the hub opens directly. `?desk=`/`?tab=` still win. |
| 5.2 | No way to see who is on the list, split by source, with opt-outs excluded. | medium | Added `GET /api/admin/marketing/broadcast/audience`: total streamed from the same iterator the sender uses, split into order history / registered accounts / server-only copy, suppressed count, small sample, and the broadcast types the stored campaign can express. |
| 5.3 | No preview: the old composer sent as soon as it was submitted. | high | Added `POST …/broadcast/preview`, which renders the real email through `mailer.campaign_email_html` and sends nothing (`dryRun: true`, pinned by a spy test). |
| 5.4 | Sending happened inline in the request, recipient by recipient - a 500-address list held the request (and the worker) for minutes. | critical | Added `POST …/broadcast` (creates the campaign row as `queued`, enqueues `campaign.dispatch`, answers at once) and `GET …/<cid>` for progress, `POST …/<cid>/cancel` to stop at the next chunk. The dispatcher sends at most `BROADCAST_CHUNK × BROADCAST_PASSES_PER_JOB` addresses per pass, re-queues itself with a 1s delay while addresses remain, collects between chunks, and logs every address. |
| 5.5 | Resuming a broadcast after a restart could re-send to everyone (or never resume). | high | Fixed: `marketing_campaign_sends` (UNIQUE campaign_id+email) records every attempt, a fresh walk skips what is already logged, and an in-flight pass keeps its paging position in memory so it never re-walks the contact book. |
| 5.6 | A new campaign type would have been rejected by the database. | high | Guarded: `campaign_types.BROADCAST_KINDS` maps every button (new arrivals, promo, coupon, appreciation) onto one of the four CHECK-allowed discriminators, and a test asserts the mapping can never leave the allowed set. |
| 5.7 | The legacy `POST /admin/marketing/campaigns` is still referenced by the old card. | low | Kept working unchanged (inline send), so nothing regressed for the existing button. |

Tests: `tests/test_broadcast_hub.py` (18), plus the route inventory in
`tests/test_admin_features.py`.

## 6. JavaScript edges

| # | Finding | Severity | Action |
|---|---------|----------|--------|
| 6.1 | The job-progress poller could survive a repaint of the Marketing desk. | low | Fixed: `fillBroadcastHub` clears the previous interval before binding, and the poller clears itself when the campaign reaches a final state. |
| 6.2 | `setInterval` sites generally | low | Verified: every long-lived timer is either guarded by `document.hidden`/panel visibility or cleared before it is re-armed (`dashTimer`, `liveTimer`, `__jaCat`, `__jaBest`, `__jaSlides`). |
| 6.3 | Admin job panel needs to be usable on a phone (the owner's constraint). | low | The panel is a two-column card list that collapses to one column under 600px, with a plain Retry button per failed job. |

## 7. SQL

No SQL change is required by this work, and none was applied. The async delete
calls the exact same `public.hard_delete_products(text[])` RPC the inline path
called, so the ledger (`deleted_products`), the products trigger and the
`service_role` grant keep doing what they already do. The read-only probe
`select public.hard_delete_products(array[]::text[])` still returns `{}` and is
still not a write.

## 8. Things deliberately NOT changed

* **RLS on `public.products`** stays off: the storefront keeps an anon-key
  Realtime subscription for live stock, and RLS without a policy would silently
  kill those events.
* **Duplicate-index advisories** (`admin_reset_tokens`, `product_reviews`,
  `receipts`) were not acted on.
* **No SQL was applied** to any project.
* **Supplier batch size** stays at two links per tick: that throttle is
  politeness toward the supplier sites, not a memory guard.
* **Uploaded photos stay JPEG on the storefront** (see 3.4).
* **The old inline delete contract** is preserved behind `?sync=1` rather than
  deleted, because the migration runbook's manual verification and any
  integration that must block on the row being gone both rely on it.
