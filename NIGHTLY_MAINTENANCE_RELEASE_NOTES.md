# Nightly 2 AM maintenance · supplier price watch · ghost-product guard

Release notes for the emergency background-watchdog audit of **2026-10-01**
(PR #128, commits `79200df` → `ba289e7`). It records what the audit found,
what changed, **what was verified automatically and live**, and the short
checklist to run on production after deploy.

---

## 1. What the audit found

| # | Question asked | Answer before this release |
| --- | --- | --- |
| 1 | Does the supplier sync run at 2:00 AM? | **No schedule existed.** The watchdog ran 8 products every 5 minutes around the clock — no nightly deep pass at all. |
| 2 | Are supplier **prices** monitored? | No. Stock only. |
| 3 | Can a deleted product come back? | Yes in principle — nothing told the watchdog a product had been hard-deleted. |
| 4 | Is orphaned media cleaned automatically? | No — only via the admin Storage-cleanup card or the CLI tool. |
| 5 | Does saving in /admin return you to your place? | Already worked (shipped earlier in the PR); now proven by a live test. |
| 6 | Are caches invalidated on save/delete? | Already worked; now proven by a live test. |

## 2. What changed

### 2A — The 2:00 AM nightly pass

Once per day, after 2:00 AM in the owner's timezone (UTC+1 default), the
in-process scheduler runs a deep pass:

* every supplier-linked product is checked **exactly once** — no day-time
  batching, no minimum re-check interval. (The first implementation of this
  looped forever re-checking the same batch; the live test caught it and it
  was rewritten before shipping.)
* the **storage sweeper** then purges orphaned/duplicate upload media through
  the exact same protected plan as the admin card: files a live product,
  order, receipt or the site still references are never touched; uploads
  younger than two days are protected; a failed reference scan aborts the
  sweep rather than guessing.
* `/healthz` reports the schedule and last run under `background.nightly`.

Knobs and defaults (all optional — nothing needs configuring):
`SUPPLIER_WATCHDOG_NIGHTLY=1`, `SUPPLIER_WATCHDOG_NIGHTLY_HOUR=2`,
`SUPPLIER_WATCHDOG_NIGHTLY_TZ_OFFSET=1`, `SUPPLIER_WATCHDOG_NIGHTLY_MAX=400`,
`SUPPLIER_WATCHDOG_MIN_INTERVAL=3600` (day-time recheck wait),
`STORAGE_SWEEPER_NIGHTLY=1`. Documented in `ENVIRONMENT_VARIABLES.md`.

### 2B — Supplier price watch

Supplier pages are now parsed for **prices** (JSON-LD offers, embedded product
JSON, `data-price` attributes). The last price seen per product/variant is
remembered; a **rise** writes a `supplier_price_increased` warning to the
admin's supplier-warning queue (drops are reported too). The shop's own retail
price is **never** rewritten by a supplier page — the supplier's price is a
cost signal; repricing stays the owner's decision.

### 2C — Ghost-product guard

`catalog.deleted_product_ids()` unions the local deleted-list with the durable
Supabase tombstones. Every automated pass (day-time ticks and the nightly
sweep) skips those ids, so **no automated sync can re-create a hard-deleted
product**. An outage of the durable read degrades to the local list, never to
"nothing is deleted". Re-creating a product from the admin deliberately clears
its tombstone.

## 3. What was verified

**Automated:** full suite **1641 passed / 0 failed**, including 23 new tests
covering the nightly schedule, the exactly-once sweep, the price watch, the
deleted-id guard, and the sweeper's protections. GitHub Actions: tests pass.

**Live (four sandbox instances — preview, testing, a real supplier fixture
page, and a production-env scheduler instance), 26/26 checks:**

* nightly schedule configured (`02:00 (UTC-17)` test offset) and **executed**
  at boot; `/healthz` reported `nightlyLastRun`;
* watchdog synced stock **9 → 5** from the live supplier page, then mirrored a
  supplier sell-out → **0**;
* supplier price rise **30,000 → 45,000** flagged as a warning; the shop's
  price was not rewritten;
* the automated sweeper purged a 30-day-old orphan and **kept** a file a live
  product referenced;
* a supplier-linked product was hard-deleted → gone from the catalogue, its
  file purged, and it **never reappeared** across later sync passes;
* editor save from *beauty, page 2* returned to **the same category and page**
  with the "Live on the store now" toast; the edit persisted;
* every save and delete moved the public catalogue ETag immediately.

**Could not be run in the sandbox:** a genuine 2 AM wall-clock wait (verified
instead with a shifted timezone offset), and Supabase Storage deletion (the
sandbox runs `UPLOAD_MODE=local`; the Supabase path is exercised by unit
tests and the CLI tool's dry-run).

## 4. Post-deploy checklist (production)

1. After Render redeploys, open `https://jaurastore.com.ng/healthz` —
   `background.backgroundAlive` is `true` and
   `background.nightly.schedule` reads **`02:00 (UTC+1)`**.
2. Leave the service up past 2 AM WAT; the next morning `nightlyLastRun`
   shows a timestamp and `supplier.checked` has grown past one batch.
3. Admin Portal → **Settings → Advanced settings → Storage cleanup** still
   opens its report (the nightly sweeper shares this code path).
4. Edit any product from a filtered category page → Save → you land back on
   the same category/page with the toast.
5. Delete a test product → it disappears from the storefront at once, and it
   is still gone the next morning (the nightly pass ran meanwhile).
6. When a supplier raises a price, a `supplier_price_increased` warning
   appears in the admin's supplier warnings — and the shop price is unchanged
   until you edit it yourself.
