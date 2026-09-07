# Jaurastore production migration — blocker report

Branch `arena/01a07c7a-jaurastore` · HEAD `e4a1694` · 2026-09-07

**The real image migration was NOT run.** No upload, no database write. Everything
below is either a code change with tests, or a dry-run report.

---

## Test tally (exact, as executed)

```
.venv/bin/python -m pytest --collect-only -q   →  606 tests collected
.venv/bin/python -m pytest tests/ -q -ra       →  606 passed
                                                  0 failed, 0 errors, 0 skipped
                                                  in 56.40s
```

> 564 → 606: +14 live-product-policy tests, +9 non-positive-price tests,
> +19 schema inventory tests. See the addendum at the bottom for what changed
> since the first version of this report.

There are no skips and therefore no skip reasons. The suite was green on two
consecutive full runs.

Per-module, summing to exactly 564:

| module | tests | | module | tests |
|---|---|---|---|---|
| test_api.py | 121 | | test_photo_fix.py | 9 |
| test_static_exposure.py | 81 | | test_push_catalog.py | 9 |
| test_admin_features.py | 45 | | test_wix_free.py | 9 |
| test_delivery_zones.py | 33 | | test_site_settings_supabase.py | 14 |
| test_migrate_images.py | 31 | | test_legacy_id_alias.py | 14 |
| test_storage_supabase.py | 29 | | test_save_visibility.py | 13 |
| test_admin_reset_tokens.py | 18 | | test_sitemap.py | 13 |
| test_image_migration_workflow.py | 19 | | test_stock_confirm.py | 13 |
| test_payment_settings.py | 15 | | test_recaptcha.py | 12 |
| test_admin_password_persistence.py | 10 | | test_catalog_mirror.py | 9 |
| test_dedupe_scope.py | 10 | | test_admin_route_gating.py | 7 |
| test_growth_persistence.py | 6 | | test_uploaded_photos.py | 7 |
| test_stock_sales_perf.py | 5 | | test_sync_health.py | 5 |
| test_brand_icons.py | 3 | | test_supabase_schema.py | 3 |
| test_backfill_rows.py | 1 | | | |

---

## Blocker status

### 1. Test isolation — CLOSED
`pytest` can no longer mutate tracked files. Root cause was
`test_admin_product_delete_ok_when_supabase_confirms` setting
`Config.ENV="production"`, which lifted `catalog._sync_repo_async`'s only guard
and fired a real `repo_sync.regenerate(commit=True, push=True)` in a daemon
thread. Two layers now: a pytest guard in `catalog.py`, and an autouse
`_tracked_repo_is_read_only` fixture in `conftest.py` that fails the test if a
tracked file's digest changes.

**Extended this session.** Two more leaks were found and fixed:
- The admin category editor writes to `CATEGORIES_PATH`, resolved by `api.py` at
  **import** time. Nothing redirected it, so saving a category from a test
  rewrote tracked `data/categories.json` — it wiped all **11** real categories
  down to one. `conftest.py` now points it at a scratch copy seeded from the
  real file, and `data/categories.json` is in `TRACKED_DATA_FILES`.
- Every test module called `os.environ.setdefault("SITE_CONFIG_PATH", <its own
  file>)`, so whichever module was **collected first** won for the whole
  session; the file also outlives the process, so a stale `bannerFrom` leaked
  across runs. `conftest.py` now pins one path and clears it at session start.

Verified clean: `git status --porcelain data/ js/products-data.js images/` is
empty; 258 seed products and 11 categories intact.

### 2. Wix UI classes removed — CLOSED
Your grep, run verbatim:

```
$ grep -RInE 'wix-|Wix' css/ js/ templates/ app.py api.py --exclude='*.bak'
js/products-data.js:1:window.JA_SEED = [{"id":"wix-001", ...
js/store.js:316:    // Canonical id / slug first, then the legacyId alias - so an old wix-*
api.py:615:    # wix-* id still prices and stock-checks against the right row.
```

All three are permitted by your brief: the 258 legacy `wix-*` product ids in
migration data, and two `legacyId` rationale comments. Zero UI class names,
zero customer-facing branding.

Your pattern is case-sensitive and **misses** camelCase and ALL-CAPS. Running it
case-insensitively (`grep -RInE 'wix' -i`) surfaced three real leftovers, all
now removed:
- `css/style.css` — 14 provenance banner comments, one of them
  `/* ADMIN — WIX-STYLE REDESIGN (v107) */`, which neither `wix-` nor `Wix`
  matches.
- `css/style.css` — `@keyframes wixMediaFlash` → `auMediaFlash`. A hyphen-based
  rename can never reach a camelCase CSS identifier.
- `js/admin.js` — a customer-facing note reading "exactly like Wix".
- `js/i18n.js` — a dead `search.wix` key (EN + FR), referenced nowhere.

283 `wix-*` UI classes were renamed to `au-*` (style.css 208, admin.js 52,
store.js 22, shop.html 1, e2e.py 3, test_photo_fix.py 2). `tests/test_wix_free.py`
(9 tests) enforces this case-insensitively, including the comments.

### 3. Hardcoded payment details removed — CLOSED
The 12 payment values are `site_settings` columns, served by `GET /api/site`
and edited in the Admin Portal. Grep for `23474678931`, `"UBA"`, the holder
names, `01 52 01 99 30`, `+229 01 68 95 31 10` → none found in any shipped file.

**Completed this session.** The shipping note was the one field still travelling
under its legacy alias. It is now canonical end to end (`name="shipping_note"`,
payload key `shipping_note`, repainted from `site.shipping_note`).

This exposed a real bug: `shipping_note` was **not** in `SITE_KEYS`, so an admin
form posting the canonical column name had it **silently dropped**. Also, the
testing-mode site write returned the raw dict while production returns the
`site_settings` row — so a canonical-field-name bug passed in tests and failed
in production. Both fixed. `tests/test_payment_settings.py` (15 tests) pins all
22 canonical fields against the real submit handler and round-trips them.

⚠️ **Operator action required:** with the fallbacks removed, `/api/site`
returns **503** without Supabase and `checkout.html` serves empty placeholders.
Populate the 12 payment columns in the Admin Portal immediately after deploying
the schema change.

### 4. Real `legacyId` compatibility — CLOSED
Resolution added in four places: `catalog.product_index()`/`resolve_product_id()`,
`api._checkout_items()` + `api._order_stock_moves()`, `supabase_store.product_by_id()`,
`js/store.js product()`. The canonical id always wins a `legacyId` collision.
No production row renamed or deleted. `tests/test_legacy_id_alias.py` (14 tests).

The original bug was in `api._order_stock_moves()`, which keyed products by `id`
only and forwarded the raw legacy pid — so `apply_stock_delta()` silently
no-oped and a confirmed order never decremented stock.

### 5. Checkout security — CLOSED, with a design decision recorded
Already in place: server-side unit prices (browser prices ignored), recomputed
totals, quantity validation, duplicate-line aggregation, `stock_quantity`
enforcement, oversell prevention, atomic single decrement, idempotent
confirm/retry, coupons and referral codes validated server-side. Customers see
only In Stock / Out of Stock.

**New this session — server-side delivery fees.** The fare list was hardcoded as
`<option>` text in `checkout.html`, `POST /api/orders` accepted **any** free-text
zone, and the server could not say what a zone cost. Now:
- A `delivery_zones` table (SQLite mirror + Postgres DDL, seeded with the 10
  zones the shop has always shown), editable in **Admin Portal → Settings →
  Delivery zones and fares**, served to the storefront by `GET /api/site`.
- `delivery.py` is the single authority. A fare is a **range**, never one number
  — transport varies with weight and the exact figure is agreed with the
  customer after payment. `kind` is `delivery` | `pickup` | `quote`.
- Checkout resolves the zone, stores the canonical name, and records the
  computed fare on the order. Unknown zones are refused, which subsumes the old
  pickup regex.

**Deliberate non-decision:** a zone/currency mismatch (naira checkout to
Cotonou) is **flagged, not rejected**. Currency and zone are chosen
independently, so blocking would turn away a sale the shop has always taken.
The fare is left unpublished and the existing Benin/Togo minimum still applies.

Two real bugs found building this:
- Reading the zone table per checkout called `init_db()`, and sqlite3's
  `executescript()` **implicitly commits** any transaction in flight — a
  half-finished order could be committed early. This made 3 unrelated referral
  tests fail with `duplicate=True` in full-suite order only. `zones()` is now
  cached and invalidated on write.
- `_row_to_zone` used `row.get()`, which `sqlite3.Row` does not have — it worked
  against Supabase and crashed on the local path.

`tests/test_delivery_zones.py` (33 tests).

### 6. Admin password persistence — CLOSED
Durable `admin_users` table storing only a werkzeug hash. `set_password` always
applies the local hash first so the previous password dies even when Supabase is
unreachable, then writes the durable copy and records a failure in
`password_durable_error()`, surfaced as `durable: false` plus a restart warning.
`verify_login` falls back to the durable hash and repairs with an **upsert**
(after a redeploy the row does not exist, so an `UPDATE` matches nothing).
Nothing relies on SQLite-only state, local disk, localStorage, server memory,
`SECRET_KEY`, or `ADMIN_BOOTSTRAP_PASSWORD`.

**New this session — reset tokens.** Render runs `Config.SUPABASE_ENABLED`, so
the SQLite branch of `auth.verify_otp` is not what protects the live portal;
`supabase_settings.verify_reset_token` is. `tests/test_admin_reset_tokens.py`
(18 tests) drives the **real** function against a fake PostgREST client and
proves: expired codes rejected and left unconsumed, codes single-use via
`consumed_at`, `attempts` a durable column capping the 6th try **even with the
correct code**, a new code resetting the lockout and invalidating the previous
one, email scoping, case-insensitive matching, fail-closed when Supabase is
unreachable, and `reset_token_recent` throttling.

Two things surfaced: the fake had to return a fresh query builder per `.table()`
call or filter state leaked between queries; and `created_at` comes from the DDL
default `now()`, which `_token_row` never sends — a test reading it as blank was
silently asserting the wrong thing.

### 7. Admin features — CLOSED
45 tests in `tests/test_admin_features.py` drive all **46** admin routes as a
real logged-in admin: products CRUD, stock, low stock, categories, site
settings, delivery zones, coupons, growth settings, referrals, orders
(list/confirm/decline/reopen/csv/delete), payment proofs, seven reporting
endpoints, backup, four image upload routes, video upload, session probe,
password change, OTP, logout, sync status.

The route inventory is pinned against the live `url_map`, so a route added or
removed without updating the list fails the suite. A separate test asserts every
endpoint `js/admin.js` calls actually exists, catching a button wired to a
typo'd path.

**Two real defects found:**
1. `POST /api/admin/coupons` passed `percent` through `sec.clean_int`, which
   **clamps** to its bounds. An admin who typed 99% silently got a 90% coupon
   and a success toast — a discount that was never what they asked for, while
   the error message claimed the range was enforced. Now validated and rejected.
2. `PUT /api/admin/products` replaces the **entire** catalogue, not one product.
   The single-product update goes through `POST`.

`tests/test_admin_route_gating.py` (7 tests) additionally walks the live
`url_map` for every `/api/admin*` rule and asserts an anonymous caller is
rejected. It found exactly one anonymous route, `GET /api/admin/session`
(`api.py:1179`), answering `{ok, authenticated:false, email:null, csrf}` — the
deliberate login probe, now an explicit commented exemption with a test pinning
its exact key set. Receipt GET/DELETE are `@require_admin`, DELETE needs CSRF
and removes both the row and the stored file, and no public storefront response
carries `payment_proofs`, `proofs/`, `/object/sign/` or `file_url`.

### 8. `migrate_images.py` validated — CLOSED
`py_compile` clean, `--help` clean, dry-run exit 0.

**New this session — an explicit `existing_https_urls` section.**
`images_already_uploaded` counts objects this run found in the bucket; that is
not the number a reviewer needs. The report now carries, for every examined row:

```json
"existing_https_urls": {
  "count": 0,
  "already_public_supabase_uploads": 0, "other_https_host": 0,
  "insecure_http": 0, "unresolved_template": 0,
  "relative_or_other": 13, "blank": 262,
  "host_breakdown": {}, "samples": []
}
```

Only the `uploads` bucket counts as already-migrated — a URL in `receipts` is
classified `other_https_host`, never as ours. `insecure_http` and
`unresolved_template` are now hard blockers on a non-dry run, since both mean a
broken image is live.

Two real bugs found: `classify_image_url` checked `startswith("https://")`
before the template markers, so `https://<SUPABASE_URL>/storage/...` was filed
as a real host and a broken row read as healthy; and `plan_products` resolved
the local file before checking `image_url`, so an already-migrated row whose
file was gone was reported as "missing image" — breaking idempotency.

### 9. Credential security — CLOSED via a protected manual workflow
Re-verified in this environment: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and
`SUPABASE_BUCKET` are all **unset**, there is no `.env` file, `render.yaml`
holds secret references only, and `gh secret list` returns **HTTP 403**. The
migration therefore cannot be run here and was not faked.

`.github/workflows/image-migration.yml` is the sanctioned path. Manual
(`workflow_dispatch`) only; `dry_run` is a required choice defaulting to `true`;
`environment: image-migration` so it waits on required reviewers; a real apply
needs **both** `dry_run=false` **and** `confirm_apply=APPLY`; the plan's
`safe_to_apply` must be true or the apply exits 1; a dry run always executes and
its JSON report is uploaded as an artifact; the service-role key is
`::add-mask::`ed before any step runs the script and the credential step reports
only the URL host and the key **length**; `contents: read`; a concurrency group
with `cancel-in-progress: false`; `SUPABASE_BUCKET` pinned to `uploads`; the
apply passes `--http-check`, which HEADs the saved public URL; and local images
are never deleted.

19 tests in `tests/test_image_migration_workflow.py` pin all of this by parsing
the YAML. `ci.yml` now installs `pyyaml`, otherwise those tests silently skip.

**You must configure once:** Settings → Environments → `image-migration` →
required reviewers; and add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` as
repository secrets.

### 10. Real image migration — NOT RUN (blocked)
Cannot proceed: no credentials in this environment. The dry-run report is ready
for review and the protected workflow is ready to execute it.

### 11. Final tally
See the top of this document: **564 collected / 564 passed / 0 failed /
0 errors / 0 skipped**.

---

## Dry-run report (`migration-dry-run.json`, exit 0, zero writes)

```
data source                : local catalogue (data/seed.json + data/catalog.json)
products examined/matched/skipped : 275 / 182 / 93
images discovered/missing/to upload: 275 / 5 / 182
existing HTTPS URLs        : 0  (uploads 0 | other host 0 | insecure 0 | template 0)
  blank / relative         : 262 / 13
duplicate rows             : 0
blank ids                  : 0
missing prices / stock     : 0 / 0
stock value source         : {'stock_quantity': 18, 'stock (legacy alias)': 257}
legacy wix-* ids           : 258
proposed DB updates        : 182   (UPDATE only, never INSERT)
proposed storage paths     : 182
categories                 : 11 / 11 images
blockers                   : none
environment blockers       : SUPABASE_URL not set; credentials not configured
safe_to_apply              : False
```

Storage key format: `products/<slug(id)>/<sha256[:16]>-<slug(stem)>.<ext>` in
bucket `uploads`. Only `image_url` and `updated_at` are writable; price, stock,
name, category and `online` are never touched.

### Live-product decision (`live_intent` in the report)
The migration must not silently publish all 258 seed products. Recorded facts:

```
local rows considered                    : 275
local seed rows                          : 258
seed rows with an explicit `online` flag : 1   (wix-001, via its override)
seed rows with a real committed photo    : 182
seed rows on the placeholder only        : 76
rows explicitly marked offline           : 0
catalog overrides                        : 18  (17 pytest fixtures + 1 real)
deleted ids                              : 16
```

The script decides nothing and inserts nothing. The schema defaults `online` to
true, so importing all 258 would publish all 258 — including the 76 that have
only a placeholder photo. **You must decide which are intended to be live before
applying.**

---

## Post-migration verification SQL

The brief's third query selects `price_ngn, price_cfa`; those columns are dead.
The canonical names are quoted `"priceNgn"` / `"priceCfa"`.

```sql
-- 1. Live rows still pointing somewhere other than the public bucket
select id, name, "image_url"
from products
where "online" = true
  and ("image_url" is null
       or "image_url" !~ '^https://[^/]+\.supabase\.co/storage/v1/object/public/uploads/');

-- 2. Must return ZERO rows for live products
select id, name, "image_url"
from products
where "online" = true
  and ("image_url" like 'http://%');
```

---

## Two conflicts with your instructions, stated plainly

1. **"Keep the PR open, do not merge it."** PR #43 is already
   `MERGED` (`mergedAt 2026-09-07T12:47:59Z`,
   https://github.com/praisejudith03-collab/jaurastore/pull/43). This cannot be
   undone from here. All work since is on `arena/01a07c7a-jaurastore` and is
   **not** merged.
2. **The brief's baseline test tallies were wrong.** The real baseline at
   `a2fa387` was 363 collected / 363 passed. The figures 365/363/2 and 486 in
   the brief do not correspond to this repository.

## Commits on this branch

```
e4a1694 Blocker 7: exercise all 46 admin routes, and fix two defects found
ec033e8 Blocker 5: delivery zones and fares are server-authoritative and admin-editable
b0aa5b5 Blocker 9: protected manual workflow for the image migration
574edf6 Blocker 8: the report states Existing HTTPS URLs explicitly
b1c9ddf Blocker 6: prove reset codes expire, are single-use, and rate-limit
297c8cb Blocker 2+3: last Wix references gone; Admin settings fully canonical
250bb1e Admin route gating audit and an explicit live-product decision record
fa32ff0 Admin password survives a Render restart via a durable admin_users row
7f86982 Test isolation, au-* rename, Supabase-owned payment details, legacyId aliases
585759a Add migrate_images.py with an offline-safe dry-run and 19 safety tests
```

## What still needs you

1. Add the two Supabase secrets and a required reviewer on the
   `image-migration` environment, then run the workflow in dry-run and review
   the artifact.
2. Decide which of the 258 seed products are intended to be live (76 have only a
   placeholder photo).
3. Populate the 12 payment columns in the Admin Portal right after deploying the
   schema change — `/api/site` 503s until Supabase is configured.
4. Apply `supabase_schema.sql` (it is idempotent) to create `admin_users`,
   `admin_reset_tokens` and `delivery_zones`.

---

# Addendum — second pass (HEAD `010dc35` → current)

## The sandbox was re-cloned; the branch pointer was restored, not the work

On resuming, `git log` showed only the base commit `a2fa387` and all prior work
appeared as uncommitted changes. Diagnosis: the sandbox had been **freshly
cloned** (shallow, two shallow points) and `arena/01a07c7a-jaurastore` was
recreated from `main`. The `.venv` was gone too, since it is excluded from
snapshots — so the first "suite" run in this pass never executed at all.

Recovery, with no destructive command:
- `git ls-remote` showed the remote branch still at `010dc35`; `git fetch` made
  the object available locally.
- `git diff 010dc35` proved the 24 tracked files were **byte-identical** to that
  commit; only the 14 new files appeared as "deleted" because they were untracked.
- `git update-ref` moved the branch pointer, then `git reset --mixed` realigned
  the index. Neither touches working-tree files — digests before and after are
  identical, verified by `diff`.

`.venv` was rebuilt (Flask 3.0.3, Pillow 12.3.0, pytest, pyyaml) and the suite
re-run for real.

## `data/catalog.json` holds 51 entries, not 18

The brief states 18. The committed file has **51**: **34 real `wix-*`
overrides** plus **17 pytest fixtures** (`jau-stock-*`, `jau-mirror-*`,
`jau-unit-*`, `jau-sync-*`, `jau-opt-*`). The 33 extra `wix-*` rows came from
harness "Sync catalogue state" commits after the earlier count was taken. This
matters because the fixtures must never reach production.

## Live-product policy implemented and documented per id

Your recommended policy is now computed by `migrate_images.py`
(`classify_live_intent` / `live_set_report`) and emitted as a `live_set` section
listing **every id in each bucket**, not just a count:

```
policy: real committed photo + priceNgn>0 + priceCfa>0 + stock>0 -> online=true
        placeholder-only or unverifiable -> online=false, reported for review

live                     : 181
placeholder_only         :  76
no_image                 :   0
needs_review             :   1
test_fixtures_excluded   :  17
                           ---
                           275   (every local row classified)
```

`applied_by_this_script` is **false**. The script writes only `image_url` and
`updated_at`, so it never changes `online`.

### Two findings that need your decision

1. **`wix-001` contradicts its own flag.** It is the only local row carrying an
   explicit `online: true`, yet it points at `images/products/_placeholder.jpg`
   and has `stock_quantity: 0` (while `stock` says 24). Under the policy it must
   **not** be live. The report lists this under
   `existing_online_flags_that_conflict_with_the_policy` rather than letting it
   pass silently.
2. **`wix-012` has `priceNgn: 0`.** It has a real photo, so it is inside the 182
   to upload, but it is excluded from the live set as `needs_review`. A 0 price
   on a live row would put a free product on the storefront.

### "0 missing prices" was misleading

`_price_missing` only tested `is None`, so `priceNgn: 0` counted as present —
the plan said **0 missing prices** while `wix-012` was in fact unpriceable.
Rather than conflate "absent" with "free", a separate `non_positive_prices`
field now reports 0-or-negative values distinctly, and it appears in the printed
summary. It is deliberately **not** a hard blocker: a 0 price should not prevent
an image-only migration, and the live set already captures it.

## Schema: two required tables were missing

`coupon_uses` and `product_reviews` were absent from `supabase_schema.sql`. Both
are now defined, and the full 13-table inventory plus the 15 required `products`
columns are pinned by tests.

- `coupon_uses` carries `unique (code, order_id)`, so a retried order cannot
  count the same redemption twice.
- `product_reviews` mirrors the SQLite shape with `unique (product_id, email)`
  and `check (stars between 1 and 5)`.

**Honest limitation — the DDL exists but the app does not use these two tables
yet.** No code writes to `coupon_uses`. Reviews still go to SQLite and are
mirrored to Supabase as **one JSON blob in a single `growth_settings` row**
(`supabase_store.save_product_reviews`), which is durable but not queryable and
is last-write-wins, so two concurrent reviews can lose one. Wiring both to the
new tables is real remaining work, not a done item.

## Verification run this pass

```
git status --short                              → clean
pytest --collect-only -q                        → 606 collected
pytest tests/ -q -ra                            → 606 passed, 0 failed/errors/skipped
sha256 before vs after the suite                → identical for catalog.json,
                                                  categories.json, seed.json,
                                                  wix_products.json
git diff -- data/catalog.json                   → empty
git diff -- data/categories.json                → empty
py_compile migrate_images.py                    → OK
migrate_images.py --help                        → OK
migrate_images.py --dry-run                     → exit 0, zero uploads, zero writes
```

Row counts intact: `seed.json` 258 · `wix_products.json` 258 ·
`catalog.json` 51 · `categories.json` 11.

## Dry-run report after these changes

```
products examined / matched / skipped : 275 / 182 / 93
images discovered / missing / upload  : 275 / 5 / 182
existing HTTPS URLs                   : 0   (262 blank, 13 relative)
non-positive prices                   : 1   (wix-012, priceNgn=0)
missing prices / missing stock        : 0 / 0
duplicate rows / blank ids            : 0 / 0
legacy wix-* id references            : 258
proposed DB updates / storage paths   : 182 / 182
plan blockers                         : none
environment blockers                  : SUPABASE_URL unset; credentials absent
safe_to_apply                         : False
safety: no deletes, no inserts, no renames, no price/stock writes,
        writable_columns = [image_url, updated_at], no credentials in report
```

**The real migration has still not been run.** It cannot be from here: no
credentials, and `gh secret list` returns HTTP 403.
