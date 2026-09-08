# Publication audit — Supabase `products` (53 rows), 2026-09-08

> **Superseded (same day) by `publication-audit-2026-09-08-v2.md` /**
> **`publication-online-flags-2026-09-08-v2.sql`**, which derive the same
> 29 / 24 split from the codified publication policy (`publication.py`).
> Kept for the record; do not run the v1 SQL.

**Read-only.** Nothing in this document was executed against Supabase. The
companion file `docs/publication-online-flags-2026-09-08.sql` is a *proposal*
that must not be run until the ID list below is approved.

## Sources used (all read-only)

| Source | What it gave |
|---|---|
| Protected run #6 (`34241484557`) job log | `total_source_rows 53`, `approved_live 29`, `operator_offline ['wix-001','wix-012']`, `placeholder_only 3`, `fixtures 17`, `needs_review 0`, `proposed_updates 30`, `uploaded=30 rows_updated=30 verified=30` |
| `GET https://jaurastore.com.ng/api/catalog` (public, anonymous) | the 53 Supabase rows as served after the migration (`"source":"admin"`, snake_case columns), including each row's `image_url` and `online` |
| `migrate_images.py` @ `5a9971e` | the classifier (`classify_live_intent`, `FORCED_OFFLINE`, `_FIXTURE_ID_RE`) re-applied locally to those 53 rows |
| `images/products/` in this checkout | confirms every "live" photo exists in the repo |

The local re-classification reproduces the run's counts exactly
(29 / 2 / 3 / 2 / 17 / 0 = 53), so the IDs below are the IDs the approved
guard was counting.

## Exact IDs per bucket

### Approved live set — 29 (`online = true`)

```
wix-011  wix-030  wix-032  wix-033  wix-042  wix-053  wix-054  wix-070
wix-072  wix-079  wix-080  wix-088  wix-091  wix-117  wix-118  wix-163
wix-164  wix-189  wix-198  wix-199  wix-200  wix-201  wix-202  wix-222
wix-228  wix-229  wix-237  wix-244  wix-252
```

All 29 now carry a full `https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/uploads/products/<id>/…` `image_url` (written by run #6, `updated_at` 14:56:55Z–14:57:36Z).

### Operator-offline set — 2 (`online = false`)

| id | why | image_url today |
|---|---|---|
| `wix-001` | placeholder image + `stock_quantity = 0`; flagged online before it was ready | `images/products/_placeholder.jpg` (untouched by the run — correct) |
| `wix-012` | `priceNgn = 0` is not a retail price; price unknown, must not be guessed | `…/uploads/products/wix-012/3f817b09b3bb89f0-24-k-nicotinamide-toner-300-ml.jpg` (real photo, written by the run as approved) |

### Placeholder-only set — 3 (`online = false`)

```
wix-041   Apple manicure set
wix-055   Bioaqua Vitamin C Set
wix-197   Press on nail
```

`image_url` is the branded `images/products/_placeholder.jpg`; nothing to publish.

### No-image set — 2 (`online = false`)

```
jau-mtot3318   Alarm clock          image_url = /uploads/products/2026/09/bc54e8705f8913ef-1e195534.jpg
wix-002        100L storage bag     image_url = /uploads/products/2026/09/7f276ebd90a4e797-b65fa7a1.jpg
```

Both point at a **relative** `/uploads/...` key (admin-portal upload path),
not at a file in the repo and not at a complete HTTPS URL. The classifier
therefore cannot verify a photo for them. They are almost certainly real
products with real photos in the bucket — they need a human to confirm the
object exists and the URL renders, then they can be added to the live set in
a later, separate change.

### Fixture-excluded set — 17 (`online = false`)

```
jau-mirror-fail  jau-mirror-ok    jau-mirror-post
jau-stock-a      jau-stock-b      jau-stock-dcf    jau-stock-dec    jau-stock-del
jau-stock-dnp    jau-stock-dpn    jau-stock-em2    jau-stock-eml    jau-stock-idem
jau-stock-opr    jau-stock-opt    jau-stock-pay    jau-stock-rop
```

pytest fixtures (`Stock Test …`, `X`, `Y`, `Mirror Post`) that leaked into the
production table. The proposal below only sets them `online=false`; it does
**not** delete them. Deleting them is a separate decision.

### Needs-review set — 0

Empty. Every non-fixture row was decided by one of the rules above.

**Total: 29 + 2 + 3 + 2 + 17 + 0 = 53.** Non-approved = 24.

## Why all 53 rows are `online = true` right now

1. **Schema default.** `supabase_schema.sql` declares
   `online boolean default true` (create at line 48, repair at line 85, again
   at 681). Any row inserted without an explicit `online` — the Wix import,
   the admin-portal saves, the pytest fixtures — was born `true`.
2. **The application never writes `false` on read paths.** `catalog.py`
   normalises `online` as `product.get("online", True) is not False`, so a
   missing or `null` value is *also* treated as `true` and re-saved that way.
3. **The migration was designed not to touch it.** `migrate_images.py` writes
   only `image_url` + `updated_at` (`WRITABLE_COLUMNS`), and run #6's
   invariants confirm `writes_price_or_stock_columns=false`, no `online`
   write. The live-set decision was emitted as *review text* only
   (`applied_by_this_script: False`).
4. **Nobody has run the offline statement yet.** `MIGRATION_REPORT.md`
   ("The real risk this surfaced: `online` defaults to TRUE") called this out
   before the run. The SQL in the companion file is that statement, pinned to
   the exact 53-row Supabase source instead of the legacy seed.

So `live_products: 53` is the expected pre-publication state, not a
regression caused by the migration.

## How the storefront should filter `online` consistently on every phone

The public catalogue reaches a phone through three layers; each one must
agree, or one device shows a product another does not.

| Layer | Today | Required behaviour |
|---|---|---|
| **Server** `GET /api/catalog` (`api.py` → `catalog.merged(include_hidden=False)`) | drops `online is False` rows for anonymous callers; admins with a session and `?all=1` see everything | Keep this as the **single point of truth**: the filter is applied server-side, on the Supabase value, before JSON is built. A phone never receives an offline product, so it cannot render one. |
| **Client** `js/store.js` `products()` | `all.filter(p => p.online !== false)`, except on the admin page | Keep as a belt-and-braces guard for the bundled fallback (`js/products-data.js`), but it must never be the *only* filter — a client-side filter can be bypassed by a stale bundle. |
| **Service worker** `sw.js` (`VERSION = "jaura-v128"`, `networkFirst` for `/api/catalog`) | network first; falls back to the cached copy only when offline | This is why phones can disagree after a flip: a phone that is offline (or on a flaky connection that throws before the response) serves the **last successful** `/api/catalog`, which may predate the `online` change. |

**Consistency rules to apply when the UPDATE is approved:**

1. Filter on the server, on the canonical column, always:
   `online IS TRUE` (not `online != false`) so a `NULL` can never leak through
   as "online". A follow-up schema tightening —
   `alter table products alter column online set not null;` after the flags
   are set — makes this permanent. (Schema change, separate PR, not in the
   SQL below.)
2. Keep `Cache-Control: public, max-age=30` on `/api/catalog` short (it is) and
   make sure the `ETag` changes when `online` changes. Today the ETag hashes
   `meta()` = `{updatedAt, updatedBy, count}` where `updatedAt/updatedBy` come
   from the **local** `data/catalog.json` overrides and `count` is
   `merged(include_hidden=True)` — none of which move when only `online`
   flips in Supabase. A phone that revalidates will get **304 with the old
   list** for up to 30 s, and a CDN edge may hold it longer. The fix is to
   fold `max(updated_at)` and `count(*) filter (where online)` from the
   Supabase rows into the ETag input. (Code change, separate PR.)
3. Bump `sw.js` `VERSION` in the same deploy that ships (2), so every phone's
   old catalogue cache is dropped on next load instead of being served on a
   flaky network.
4. Never publish from the client: the admin page's "all products" view must
   stay behind the session check that already exists (`include_hidden` only
   when `authmod.current_admin()`).

With 1–3 in place, every device converges on the same 29 products within one
cache window (≤ 30 s online, or the next successful load if it was offline).

## What this audit did NOT do

- No workflow dispatched, no artifact re-downloaded, nothing uploaded.
- No write to Supabase; the SQL file is text only.
- No local file deleted; `data/`, `images/`, `js/products-data.js` untouched.
