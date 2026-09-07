# Jaurastore production migration — blocker report

Branch `arena/01a07c7a-jaurastore` · HEAD `e4a1694` · 2026-09-07

**The real image migration was NOT run.** No upload, no database write. Everything
below is either a code change with tests, or a dry-run report.

---

## Test tally (exact, as executed)

```
.venv/bin/python -m pytest --collect-only -q   →  640 tests collected
.venv/bin/python -m pytest tests/ -q -ra       →  640 passed
                                                  0 failed, 0 errors, 0 skipped
                                                  in 61.87s / 61.45s (two runs)
```

> 564 → 606 → 626 → 640: +14 live-product-policy tests, +9 non-positive-price
> tests, +19 schema inventory tests, +18 coupon/review data-model tests, +3
> review-persistence tests retargeted at the real table, then +10
> schema-verification tests and +4 for the review column contract. See the
> addendum at the bottom for what changed since the first version of this report.

There are no skips and therefore no skip reasons. The suite was green on two
consecutive full runs.

Per-module, from `pytest --collect-only -q`, summing to exactly 640:

| module | tests | | module | tests |
|---|---|---|---|---|
| test_api.py | 121 | | test_recaptcha.py | 12 |
| test_static_exposure.py | 81 | | test_verify_schema.py | 10 |
| test_migrate_images.py | 54 | | test_dedupe_scope.py | 10 |
| test_admin_features.py | 45 | | test_admin_password_persistence.py | 10 |
| test_delivery_zones.py | 33 | | test_wix_free.py | 9 |
| test_storage_supabase.py | 29 | | test_push_catalog.py | 9 |
| test_supabase_schema.py | 25 | | test_photo_fix.py | 9 |
| test_image_migration_workflow.py | 19 | | test_catalog_mirror.py | 9 |
| test_coupon_uses_and_reviews.py | 19 | | test_growth_persistence.py | 8 |
| test_admin_reset_tokens.py | 18 | | test_uploaded_photos.py | 7 |
| test_payment_settings.py | 15 | | test_admin_route_gating.py | 7 |
| test_site_settings_supabase.py | 14 | | test_sync_health.py | 5 |
| test_legacy_id_alias.py | 14 | | test_stock_sales_perf.py | 5 |
| test_stock_confirm.py | 13 | | test_brand_icons.py | 3 |
| test_sitemap.py | 13 | | test_backfill_rows.py | 1 |
| test_save_visibility.py | 13 | |  |  |

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
pytest --collect-only -q                        → 626 collected
pytest tests/ -q -ra  (run twice)               → 626 passed both times,
                                                  0 failed/errors/skipped
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
non-positive prices                   : 3   (wix-012 priceNgn=0, plus the
                                                 test fixtures jau-mirror-fail
                                                 and jau-mirror-ok, priceCfa=0)
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

---

# Pass 3 — operator decisions, the coupon/review data model, and a real bug

## 1. Operator decisions: `wix-001` and `wix-012` go offline

Both products are now recorded as explicit operator decisions in
`migrate_images.py`:

```python
FORCED_OFFLINE = {
    "wix-001": "placeholder image and stock_quantity=0; the online=true flag "
               "was wrong - the storefront must not show it",
    "wix-012": "priceNgn=0 is not a valid retail price; the price is unknown, "
               "not free",
}
```

`classify_live_intent()` consults `FORCED_OFFLINE` **first**, so an operator
decision always wins over the generic classification, and the report names the
reason `operator_offline` rather than burying it in `placeholder_only` or
`needs_review`.

**Neither row is touched.** No delete, no rename, no price change, no id
change. The script still writes only `image_url` and `updated_at`; the offline
decision is emitted as `apply_sql` **review text** for a human to run, with
`applied_by_this_script: False`.

## 2. The approved live count is **181**, not 180

This is measured from the report, not derived from an expectation:

```
total_source_rows                275
rows_with_a_valid_image          181
rows_with_a_valid_price          256
rows_with_valid_stock            256
approved_live_count              181

counts: approved_live 181 · operator_offline 2 · placeholder_only 75
        no_image 0 · needs_review 0 · test_fixtures_excluded 17   (sums 275)

operator_offline_ids  ['wix-001', 'wix-012']
first approved ids    wix-003, wix-004, wix-005, wix-008, wix-009, wix-010
```

`wix-001` and `wix-012` were **already excluded from the 181** by the existing
policy — `wix-001` as placeholder-only, `wix-012` as needs-review on
`priceNgn=0`. Recording the operator decisions moved them into a
distinguishable bucket; it did not remove them from a set they were never in.
The arithmetic is a bucket relabel, not 181 − 2 = 179.

## 3. `coupon_uses` — and a bug the tests caught

`growth.record_code_use()` now writes the redemption **first** and lets
`unique(code, order_id)` decide whether it is new:

```python
cur = execute("INSERT OR IGNORE INTO coupon_uses "
              "(code, email, order_id, percent, used_at) VALUES (?,?,?,?,?)", ...)
if not cur or cur.rowcount != 1:      # replay — do not count again
    report["duplicate"] = True
    return report
execute("UPDATE coupons SET uses=uses+1 WHERE code=?", (code,))
```

The counter increment is **gated on a row actually being written**, so a
confirm/retry loop, a double-tapped button or a replayed webhook cannot
inflate `coupons.uses`. A call with no order id is refused outright — there is
nothing to be idempotent against.

Supabase is mirrored through `supabase_store.mirror_coupon_use()` with
`on_conflict="code,order_id"`, so the durable side is idempotent too.

**Bug found and fixed.** The first version of this code read `c["percent"]`,
but the enclosing `SELECT` never fetched that column. SQLite raised
`IndexError`, the order flow swallowed it, and the redemption was silently
never recorded — the discount still applied, so nothing looked wrong. It was
only visible because a test asserted on the log rather than on the counter:

```
"order_return": {"discount": 2565, "id": "JA-GRCP01", ...}   ← discount applied
"coupon":       {"code": "SALE-10", "uses": 0, ...}          ← counter unmoved
"uses":         []                                           ← nothing logged
```

`percent` is now in the `SELECT`. This is why the DDL was never the test.

New admin route: `GET /api/admin/coupon-uses?code=…` — answers "which order
used this code", which the counter never could.

## 4. `product_reviews` — the table is now the source of truth

Reviews used to be mirrored into Supabase as **one JSON blob** in
`growth_settings`. That was last-write-wins: the whole table was re-serialised
on every review, so two reviews arriving together lost one.

Now:

| Operation | Path |
|---|---|
| create | `POST /api/reviews` → SQLite + `save_product_reviews_table()` upsert on `(product_id, email)` |
| list | `GET /api/reviews/<pid>` → Supabase table when configured, else SQLite; `source` says which |
| update | same upsert — one row per `(product_id, email)`, so a resubmit edits |
| moderate | `PATCH /api/admin/reviews` → sets `hidden`, keeps the row |
| delete | `DELETE /api/admin/reviews` → `(product_id, email)`, the unique key |
| admin list | `GET /api/admin/reviews` → includes hidden rows |

Moderation needed a column, so `hidden` was added to both schemas, with a
guarded `ALTER` for databases that already have the table
(`REVIEW_COLUMNS` in `db.py:migrate()`, and a `do $$ … $$` block in
`supabase_schema.sql`). A hidden review is kept — it is a real customer's
verified purchase — but excluded from the storefront and from the average.

Updates and deletes are addressed by `(product_id, email)`, the unique key, so
neither can touch a different customer's review. Tests assert exactly that.

**The old blob is not deleted.** `migrate_product_reviews_from_blob()` is
dry-run by default, copies into the table, then reads back per product and
reports `verified`. If `verified < usable` it says so and leaves the blob in
place; `blob_deleted` is always `False`. The boot restore in `app.py` prefers
the table and falls back to the blob with a warning naming the migration.

## 5. Schema audit (re-verified, not assumed)

13/13 required tables present in `supabase_schema.sql`, and all 15 required
`products` columns — note that only the camelCase ones are double-quoted, so an
audit that matches only `"col"` reports 5/15 and is wrong:

```
site_settings · products · categories · orders · receipts · admin_users ·
admin_reset_tokens · coupons · coupon_uses · referral_codes · referral_uses ·
delivery_zones · product_reviews                                  13/13

id · legacyId · name · category · priceNgn · priceCfa · compareNgn ·
compareCfa · image_url · images · stock_quantity · description ·
featured · online · updated_at                                    15/15

coupon_uses      unique (code, order_id)                present
product_reviews  unique (product_id, email)             present
product_reviews  hidden                                 present
```

## 6. Test-isolation fix worth recording

`/tmp/jaura_test.db` outlives the process. Two tests silently depended on that
in the wrong direction: a leftover `JA-GRCP01` order made the checkout return
`duplicate=True` and skip the coupon bookkeeping, and a leftover `JA-REV1`
tripped `orders.id`'s unique constraint. Both now delete their own rows first.
A test that passes only on a cold database is not testing anything.

## Verification run this pass

```
pytest tests/ -q -ra  (pass 1)                  → 626 passed
pytest tests/ -q -ra  (pass 2)                  → 626 passed
git status --short after each                   → only the intended source edits
git diff -- data/catalog.json                   → empty
git diff -- data/categories.json                → empty
git diff -- data/seed.json                      → empty
git diff -- data/wix_products.json              → empty
sha256 before vs after                          → identical, all four files
py_compile migrate_images.py                    → OK
migrate_images.py --help                        → OK
migrate_images.py --dry-run                     → exit 0, zero uploads, zero writes
```

New in this pass: `tests/test_coupon_uses_and_reviews.py` (18 tests — coupon
success, retry, two-orders, cross-code replay, no-order-id, expiry, max-uses,
invalid, admin log; review create, list, product filtering, update, delete,
delete-requires-admin, moderation, moderation scoping, unknown-review 404,
purchase gate), plus 3 review-persistence tests rewritten to target the table.

## Dry-run report after these changes

```
products examined / matched / skipped : 275 / 182 / 93
images discovered / missing / upload  : 275 / 5 / 182
approved live products                : 181
operator-offline decisions            : 2   (wix-001, wix-012)
placeholder-only                      : 75
test fixtures excluded                : 17
existing HTTPS URLs                   : 0   (262 blank, 13 relative)
non-positive prices                   : 3   (wix-012 priceNgn=0, plus the
                                                 test fixtures jau-mirror-fail
                                                 and jau-mirror-ok, priceCfa=0)
missing prices / missing stock        : 0 / 0
duplicate rows / blank ids            : 0 / 0
legacy wix-* id references            : 258
proposed DB updates / storage paths   : 182 / 182
plan blockers                         : none
environment blockers                  : SUPABASE_URL unset; credentials absent
safe_to_apply                         : False
applied_by_this_script                : False
safety: no deletes, no inserts, no renames, no price/stock writes,
        writable_columns = [image_url, updated_at], no credentials in report
```

The single occurrence of the string `SUPABASE_SERVICE_ROLE_KEY` in the report
is the **variable name** inside a note explaining that it is unset. No key
value appears anywhere in it.

**The real migration has still not been run.** No credentials are present, and
`dry_run=false` has not been executed.

---

# Pass 4 — the review column contract, the migration pre-flight, and a blocked secret

## 1. `product_reviews` did not match the agreed column contract

The spec is `product_id, email, name, rating, title, body, hidden, created_at,
updated_at`. What I had shipped in pass 3 was `stars, note, at` with no
`title` and no `updated_at` — five of the nine required names were wrong or
absent. The schema test passed because it asserted the names I had chosen, not
the names that were agreed.

Now, in both `supabase_schema.sql` and `db.py`:

```
id · product_id · order_id · email · name · rating · title · body ·
hidden · created_at · updated_at                    unique (product_id, email)
rating: integer not null default 5 check (rating between 1 and 5)
```

`order_id` is kept as an addition to the spec — it is what ties a review to the
verified purchase.

**A column rename moves data, it does not drop it.** Both schemas carry a
guarded upgrade path so an existing database is migrated rather than rebuilt:

```sql
do $$ begin
  if exists (select 1 from information_schema.columns
             where table_name = 'product_reviews' and column_name = 'stars') then
    alter table product_reviews rename column stars to rating;
  end if;
  ... note → body, at → created_at
end $$;
```

SQLite gets the same treatment in `db.py:migrate()` via `REVIEW_RENAMES`, which
`app.py` already calls right after `init_db()`. Verified on a scratch database
that the rename preserves the row, preserves the value, and leaves the
`unique(product_id, email)` constraint intact. SQLite refuses
`ADD COLUMN … DEFAULT (datetime('now'))`, so `updated_at` is nullable there and
every write path sets it explicitly.

**Legacy compatibility is kept on both edges.** Reading accepts
`stars`/`note`/`at` and maps them onto the new names, so restoring the old
`growth_settings` blob cannot lose a review. The API accepts `rating` or
`stars`, `body` or `note`, and returns the new names *plus* the old ones as
deprecated aliases, because a customer's cached `js/app.js` outlives a deploy.
One subtlety: `d.get("rating", d.get("stars"))` does **not** fall back when
`rating` is present but `null`, so the lookup is null-aware.

**On the test that forbade this.** `test_repair_block_only_adds_columns` banned
`drop|truncate|delete|rename` outright. Rather than weaken it, I narrowed it to
the three verbs that are genuinely destructive and added
`test_the_only_renames_are_the_guarded_review_column_moves`, which pins the
exact three renames and asserts each is wrapped in an `information_schema`
existence check. A fourth rename still fails the suite.

## 2. `verify_schema.py` — the pre-flight the migration was missing

The migration writes `products.image_url` and Storage objects, so the schema
must be applied first. `verify_schema.py` probes all 13 tables via PostgREST,
requesting exactly the columns the application uses — a missing table or column
makes PostgREST error, which is the signal. It needs no `information_schema`
access and no `psql` connection, only the two secrets the workflow already has,
and it prints table names, column names and counts, never the key.

`tests/test_verify_schema.py` (10 tests) drives `check_live()` against a fake
PostgREST client, so the check is proven to fail rather than assumed to. The
most important one reproduces this pass's own bug: a `product_reviews` table
still holding `stars/note/at` **fails** the check.

```
python3 verify_schema.py --dry-run
  required tables          : 13
  OK  coupon_uses: unique (code, order_id)
  OK  product_reviews: unique (product_id, email)
  network probe            : skipped (--dry-run)
  ok                       : True
```

Against real Supabase it exits 1 until the schema is applied; no credentials
here, so the live probe has not been run.

## 3. The workflow now gates on the approved classification

`.github/workflows/image-migration.yml` grew a schema pre-flight, a live-set
summary, an approved-classification gate, and a `migration-result.json`
artifact. The gate re-checks the reviewed decision on every apply and stops for
a human if the data has moved:

```
expected {total_source_rows: 275, approved_live_count: 181,
          placeholder_only: 75, test_fixtures_excluded: 17, needs_review: 0}
and {wix-001, wix-012} ⊆ operator_offline_ids
and live_ids ∩ {wix-001, wix-012} = ∅
```

Every workflow step's embedded Python was executed locally against the real
`migration-dry-run.json` rather than read:

| trial | exit |
|---|---|
| approved decision (275/181/75/17/0) | 0 |
| approved count drifts to 180 | 1 |
| placeholder count drifts to 76 | 1 |
| `needs_review` becomes 1 | 1 |
| `wix-001` dropped from `operator_offline` | 1 |
| `wix-012` leaks into the live set | 1 |

`migration-result.json` was built from a synthetic apply report and contains
`uploaded=182 reused=0 rows_updated=182 verified=182`, all seven destructive
invariants `False`, `credentials_in_report: False`, and no secret material. Its
field names were taken from the report's actual keys (`execution.products.{uploaded,
reused, rows_updated, verified, failed, verified_urls, not_verified}`) — an
earlier draft invented `images_uploaded` and `http_failures`, which do not
exist and would have produced a report of confident `null`s.

Requirements audit: **20/20** — `workflow_dispatch` only, no `push` trigger,
`dry_run` a required choice of `true`/`false`, bucket pinned to `uploads`, no
`SUPABASE_PRIVATE_BUCKET`, no `receipts`, `environment: image-migration`,
`permissions: contents:read, actions:read`, key `::add-mask::`ed with only its
length printed, three artifacts uploaded, apply needs `dry_run=false` **and**
`confirm_apply=APPLY` **and** `safe_to_apply`, apply passes `--http-check`,
`cancel-in-progress: false`. A leak scan finds no JWT-shaped token, no
`sb_secret`, and only two `secrets.*` references.

One false positive worth recording: my own check for the string `receipts`
matched the comment `# never 'receipts'` on the `SUPABASE_BUCKET` line. Stripped
of comments, neither `receipts` nor `SUPABASE_PRIVATE_BUCKET` appears.

## 4. **Blocked:** the protected secrets cannot be added from here

```
$ gh secret list
failed to get secrets: HTTP 403: Resource not accessible by integration
```

The Actions secret API is not reachable with this sandbox's token, so I can
neither read nor set `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`. **The
repository owner must add them** from GitHub → Settings → Secrets and variables
→ Actions, and create the `image-migration` environment with at least one
required reviewer under Settings → Environments. I am not asking for the key
here and it must not be pasted into chat.

Until that is done the migration cannot run, `safe_to_apply` stays `false`, and
no claim of production migration is made.

## Verification run this pass

```
pytest --collect-only -q                        → 640 collected
pytest tests/ -q -ra  (pass 1)                  → 640 passed in 61.87s
pytest tests/ -q -ra  (pass 2)                  → 640 passed in 61.45s
git status --short                              → only the intended source edits
git diff -- data/catalog.json                   → empty
git diff -- data/categories.json                → empty
git diff -- data/seed.json                      → empty
git diff -- data/wix_products.json              → empty
sha256 before vs after both passes              → identical, all four files
py_compile migrate_images.py verify_schema.py   → OK
migrate_images.py --help                        → OK
migrate_images.py --dry-run                     → exit 0, zero uploads, zero writes
node --check js/app.js js/admin.js              → OK
```

## Approved live-product decision (unchanged, re-verified)

```
approved_live_count        181
operator_offline_ids       wix-001, wix-012
placeholder_only            75
test_fixtures_excluded      17
needs_review                 0
total_source_rows          275
```

`wix-001` and `wix-012` are not subtracted a second time: they were already
outside the 181. Neither row is deleted, renamed, or repriced.

## Still open — cannot be done from this sandbox

1. Add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`; create the
   `image-migration` environment with a required reviewer.
2. Apply `supabase_schema.sql` in the Supabase SQL editor, then run
   `python3 verify_schema.py` and confirm `ok: True`.
3. Run the workflow with `dry_run=true` and review the `dry-run-report` and
   `schema-verification` artifacts.
4. Only then `dry_run=false` with `confirm_apply=APPLY`.
5. Migrate the legacy review blob with `POST /api/admin/reviews/migrate`
   (dry-run first) and verify row counts and samples before touching it. The
   blob has not been deleted.
6. Post-migration SQL, the admin/persistence checklist, and the visual pass.
