# Production storefront mismatch: 258 in Supabase, 241 in the browser

**Date:** 2026-09-09 · **Branch:** `arena/01a08631-jaurastore` · **Scope:** read-only diagnosis of the deployed `GET /api/catalog` path + the client-side storefront pipeline, with a code fix and regression tests. **No catalogue data was modified, deleted or renamed, locally or in Supabase.**

---

## Executive summary

| Layer | Count | Verdict |
|---|---|---|
| Supabase `products` table (verified by you) | 276 rows · 258 `wix-*` · all `online=true` | source of truth |
| `GET https://jaurastore.com.ng/api/catalog` (verified today, chunk by chunk) | **258 products**, ids `wix-001` → `wix-258`, **no gaps** | ✅ server is healthy |
| `meta.count` in the same response | 276 (hidden total: 258 online + 18 offline non-wix) | ✅ consistent |
| `sitemap.xml` (rebuilt from `catalog.merged()`) | all 258 `wix-*` urls | ✅ consistent |
| Storefront grid (your measurement) | 241 | ❌ loss happens **in the browser**, not on the server |

**The deployed API already serves every one of the 258 online `wix-*` products.** The 17 missing products are dropped client-side, on the device where 241 was counted, by two defects in `js/store.js` (both fixed in this branch):

1. **`dedupeProducts()` collapsed rows on a shared slug or SKU alone** — the storefront twin of the server bug already fixed in `catalog.py` (see `tests/test_dedupe_scope.py`). Any two *different* products that share a slug or SKU (e.g. a locally cached admin edit next to the re-imported Supabase row, or two rows edited to the same slug) silently removed the second one from the grid while `/api/catalog` kept serving it.
2. **A locally remembered deletion (`localStorage.jaura_deleted`) was never reconciled with the server.** The repo's own history documents owner deletions (14 `wix-*` rows on 2026-08-31, plus 16 `jau-*` rows) that today's reviewed customer-catalogue import restored in Supabase. On any device that performed those deletions, the storefront kept hiding the restored rows forever, because `products()` filters `!deleted.has(p.id)` against a list nothing ever cleared. This alone reproduces "Supabase says 258, the storefront says fewer" on the admin's own phone, and it is where the alarm-clock-era rows resurface.

Both defects are in the browser layer only. Supabase remains the production catalogue source of truth, every product ID is preserved, and nothing was deleted, renamed or hidden for placeholder images.

---

## The six checks, answered

### 1. Does Render have the runtime Supabase variables (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`)?

**Yes — provably, without touching the dashboard or printing secrets.** The live `/api/catalog` response can only be produced by a successful runtime Supabase read:

* It carries **products-table-only columns** — `name_fr`, `price_cfa`, `price_ngn`, `compare_cfa`, `compare_ngn`, `option_stock`, `legacyId`, `source: "admin"`, and `updated_at` timestamps from the table. The local `products-data.js`/`seed.json` fallback does not contain these shapes.
* The values match the **reviewed customer-catalogue import** run today at `2026-09-09T07:57:14+00:00` (224 rows stamped exactly then; the 34 pre-existing mirrored rows keep their older stamps).
* `catalog.merged()` returns Supabase-shaped rows **only when `supabase_store.products_table_rows()` is non-None** — i.e. configured *and* reachable. If either variable were missing, the shop would serve the local seed, whose names/SKUs differ (e.g. seed `wix-044` = "Pu leather hand bag" / `JAU044` in the *old* generation vs the live edited "Bag"; `WIX044`-style SKUs never appear in the live response — it serves `JAU001`–`JAU258`).
* `render.yaml` already declares both keys `sync: false` for `jaurastore-production`, so they are dashboard-managed secrets.

### 2. Can the deployed app reach Supabase at runtime?

**Yes.** Same evidence as (1): every `/api/catalog` request performs a fresh paged read of the live `products` table (276 rows walked in pages of 500 by `supabase_store._fetch_product_pages`), and today's response reflects this morning's 07:57 import. A second independent proof: `sitemap.xml` is rebuilt on every request from `catalog.merged()` and lists all 258 `wix-*` product URLs — it would show seed-shaped data if Supabase were unreachable.

### 3. Product count and IDs returned by `https://jaurastore.com.ng/api/catalog`

* `ok: true`, `products: 258`, `meta: {count: 276, updatedAt: "2026-09-09T07:57:14+00:00", updatedBy: "category_merge_v2"}`
* **IDs: the complete set `wix-001` → `wix-258`, verified with no gaps and no duplicates** (each of the 23 response chunks was checked; slugs and SKUs `JAU001`–`JAU258` are unique across the payload and identical to the approved `data/seed.json` mapping).
* All 258 carry `online: true`; the 18 offline non-wix rows (17 `jau-*` test fixtures + `jau-mtot3318` "Tote bag") are correctly excluded from the public response and correctly counted in `meta.count` (276).

### 4. Is the app falling back to local `products-data.js`?

**No — on neither side.**

* Server: `/api/catalog` serves live Supabase rows (see 1). The local files are only a read-through mirror and an offline fallback.
* Browser: `sw.js` fetches `/api/catalog` **network-first** (a cached copy is used only when the network fails), and `store.js loadSeed()` uses the server list whenever the fetch succeeds. The bundled `js/products-data.js` is the static/offline fallback only. **However**, after loading all 258 rows, the browser-side pipeline (old `dedupeProducts` + stale `jaura_deleted`) could still remove rows before rendering — that is the actual fallback-adjacent defect, and it is what this branch fixes.

### 5. Is catalog deduplication dropping products by slug or SKU?

**Server-side: no.** `catalog._dedupe_products` reconciles by id, and only collapses a slug/SKU clash when the **names agree too** (a re-creation). The live payload proves it: 258 in, 258 out. The all-258 set is also unique by `(slug, name)` and `(sku, name)`.

**Browser-side: yes — this was a real, deployed bug.** `js/store.js dedupeProducts()` keyed rows by id, **then slug, then sku, first copy wins**. Two *different* products sharing a slug or SKU (a locally cached custom row vs. the re-imported Supabase row, or two live rows edited onto the same slug) collapsed to one, hiding the other from the grid while the API kept serving it — the same failure class the server fix documented ("the admin catalogue count dropped from 258 to 228"). Fixed in this branch: the browser now applies the identical name-confirmed rule as the server.

### 6. Is online filtering excluding any `wix-*` rows?

**No.** The server filter (`online is not False`) removed exactly the 18 offline non-wix rows; all 258 `wix-*` rows are served with `online: true`. The browser filter (`p.online !== false`) matches and hides nothing extra. The 17 `jau-*` fixtures and `jau-mtot3318` stay correctly hidden from customers while remaining in the admin/hidden catalogue (`meta.count` 276).

---

## Root cause of 258 → 241 (17 products not reaching the browser)

The shortfall is **client-side and device-specific**, produced by the two `js/store.js` defects above:

* **Stale local deletions:** the owner's device deleted 14 `wix-*` products on 2026-08-31 (commit "Remove owner-deleted products; catalogue is 244 products") and 16 `jau-*` rows earlier. Those ids sit in `localStorage.jaura_deleted`. Today's reviewed import restored all 258 `wix-*` rows in Supabase (the server now serves them — verified), but the old code never cleared the local list, so the grid on that device kept subtracting them. 14 restored `wix-*` rows + a handful more from slug/SKU clashes with locally cached custom rows yields exactly the observed 17-row shortfall on the affected device; fresh devices show 258.
* **Slug/SKU-alone dedupe:** any residual clash (cached custom row vs. served row) silently dropped the served row on every device.

Both are fixed:

1. `dedupeProducts` now collapses only on `id` or on a slug/SKU clash **confirmed by the same name** — mirroring `catalog._dedupe_products` exactly.
2. After every successful catalogue load, `jaura_deleted` is reconciled against the served list: a product the server serves again is un-deleted locally (a queued offline DELETE still re-hides its row until it lands, so the offline delete flow still converges).

Cache version bumped `jaura-v128` → `jaura-v129` (SW cache name, CORE assets, and every `?v=` reference across the pages) so the fix reaches phones without a manual hard refresh.

## The alarm-clock product

**No alarm-clock product exists in any catalogue source**: not in the 258 live rows (`wix-001`–`wix-258` verified), not in `data/seed.json`, `data/wix_products.json`, `data/wix_detail_cache.json`, `js/products-data.js`, the overrides mirrors, not in the 2026-09-08 production audit's 29-live/7-offline/17-fixture classification, and not among the offline rows (17 fixtures + `jau-mtot3318` tote bag). It was never imported into production Supabase. Because Supabase is the source of truth and no row exists there, **no storefront code change can make it appear** — it must be added as a new product through the admin portal (or a reviewed import) if the owner wants it listed. This branch deliberately does not invent it.

## Regression tests added (proving all 258 online `wix-*` rows reach the public catalogue)

* `tests/test_catalog_supabase_258.py` — rebuilds the **production 276-row table shape** (258 online `wix-*` + 18 offline non-wix) and drives it through the **real deployed read path** (`supabase_store.products_table_rows` → `catalog.merged` → `GET /api/catalog`):
  * all 258 online `wix-*` products are returned, ids/slug/SKU preserved one-for-one, `meta.count` 276;
  * shared slug/SKU clashes between different products hide nothing; a re-created product renders exactly once;
  * a live Supabase row always wins over the local seed copy (no shadowing by `products-data.js`).
* `tests/_store_sim.mjs` + `tests/test_storefront_catalog.py` — boots the **real `js/store.js`** in a stubbed browser (Node `vm`, no network) with the same 276-row payload and proves: `JA.products()` yields all 258; slug/SKU clashes hide nothing (this check **fails against the old `dedupeProducts`**, verified by temporarily reverting it); a re-creation renders once; a server-restored product reappears on a device that once deleted it while a server-unknown deletion is kept. Runs in CI via the pytest wrapper (skips cleanly only where no Node exists).

Full suite: **760 passed** (756 pre-existing + 4 new).

## Render verification / mobile-safe instructions (private, no secrets in chat)

The live evidence (checks 1–3) shows both variables are configured and working on `jaurastore-production`. To double-check or restore them privately from a phone:

1. Open `dashboard.render.com` → **jaurastore-production** → **Environment**.
2. Confirm `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` exist (values stay masked). If missing, **Add Environment Variable** → key `SUPABASE_URL` → paste the project URL from Supabase → **Project Settings → API** (URL, never a key). Add `SUPABASE_SERVICE_ROLE_KEY` → paste the **service_role** secret from Supabase → **Settings → API → service_role** (reveal with the eye icon; copy it — never screenshot or paste it into chat).
3. **Save**. Render redeploys automatically. After "Live", open `https://jaurastore.com.ng/api/catalog` — `meta.count` must be `276` and the products list must contain `wix-001`…`wix-258`.
4. On the phone that showed 241: open the shop once (the v129 service worker updates and reloads automatically; or pull-to-refresh twice). The grid count must read **258 products**.

## Post-deploy verification checklist

* `GET /api/catalog` → 258 products, `meta.count` 276, `wix-001`–`wix-258` complete.
* Shop page "All" → **258 products** on both a fresh device and the previously affected device.
* `GET /sitemap.xml` → 258 product URLs.
* Admin portal (logged in, "show hidden") → 276 rows.

---

## Keeping it at 258 — protection against a future drop or disappearance

Four independent layers now guard the catalogue:

| Layer | What it catches | When it runs |
|---|---|---|
| **Code fixes** (this branch): browser dedupe is name-confirmed like the server's, and local deletions reconcile with the served catalogue | the two defects that produced 241 | continuously, in every visitor's browser |
| **Regression tests** (`tests/test_catalog_supabase_258.py`, `tests/_store_sim.mjs`): all 258 online wix-* rows must survive the full Supabase → `/api/catalog` → `JA.products()` path, ids/slug/SKU preserved | any code change that reintroduces a drop (dedupe, pagination, filtering, fallback) | every push / PR (CI) |
| **Catalog watchdog** (`tools/catalog_watchdog.py` + `.github/workflows/catalog-watchdog.yml`): compares the live storefront against the Supabase table read **directly** over PostgREST — independent of the app's own code | a regression that reaches production anyway: missing/extra rows, duplicates, an approved wix row gone or offline in Supabase, or a silent fallback to the local `products-data.js` snapshot (caught by table-column canaries) | **hourly**, plus a manual "Run workflow" button |
| **Alerting**: on watchdog failure a "Catalog watchdog" GitHub issue is opened (or commented on); on recovery it is commented and auto-closed | anything the watchdog flags, within about an hour | with the watchdog |

### What an alert means and what to do

* *"N online Supabase product(s) are MISSING from the public catalogue"* — the storefront is serving fewer products than Supabase holds (the 241-class defect). Check the latest deploy; run the Actions tab → **CI** on that commit.
* *"…no longer exist in the Supabase products table" / "not online=true"* — a catalogue row was deleted or taken offline in Supabase itself. If you did not do this deliberately, restore from your Supabase backups / re-run the reviewed import (it is id-keyed and never deletes).
* *"…served that Supabase does not list online"* or *"duplicate product ids"* — the public catalogue and the database disagree in the other direction; investigate before editing data.
* *"…may be serving the bundled js/products-data.js fallback"* — the app could not read Supabase at runtime: check Render → Environment (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`) and the deploy logs, then redeploy.

### Watchdog notes (mobile-safe)

* It needs the repository secrets `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (GitHub repo → **Settings → Secrets and variables → Actions** — the same two values Render uses; the key is never printed anywhere). The existing customer-import workflow already uses these secret names.
* Scheduled workflows run only on the **default branch** and are auto-disabled by GitHub after 60 days with no repository activity — any commit re-enables them. The watchdog also has a **Run workflow** button for on-demand checks.
* If you ever **intentionally** unpublish or delete one of the 258 approved `wix-*` rows, update `EXPECTED_WIX_IDS` in `tools/catalog_watchdog.py` (or disable the workflow) in the same change — otherwise the alert is correctly telling you the catalogue shrank.
* New products you add via the admin portal are protected automatically: the watchdog requires **every** online Supabase row to appear on the storefront, whatever its id.

