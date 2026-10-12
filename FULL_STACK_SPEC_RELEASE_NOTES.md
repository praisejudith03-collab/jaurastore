# Full-stack specification release — category images, speed, Trash Bin, variant pricing

Companion document for the specification batch that lands **permanent
category image storage, storefront performance guards, the isolated Trash
Bin, variant-level pricing with supplier cost controls, and the single-run
supplier price backfill**. It records what changed, the exact contracts, and
what the owner has to do (Section 6).

---

## 1. Permanent category image storage & fallback protection

* Category covers were already persisted as **HTTPS URLs in the Supabase
  `categories.image_url` column** (uploads go to Storage in production, never
  to the ephemeral disk). This batch closes the last way a cover could still
  disappear: a transient `<img>` failure inside the admin desk used to be
  readable back into "Save categories".
* `js/admin.js` → `_catAssetHTML` now renders category assets with the shop's
  own `onerror="fallbackImg(event)"` handler, `loading="lazy"`, and a
  `data-cat-original` anchor.
* `js/admin.js` → `collectCats` resolves the saved URL in a fixed order:
  this session's fresh upload → the STORED url (`data-cat-stored` on the
  card) → a live non-placeholder `<img>` src → the document chip. A
  placeholder/branded-logo repaint is **refused** (`PLACEHOLDER_RE`), so a
  network hiccup can never overwrite the database value with
  `images/products/_placeholder.jpg`.
* Storefront category tiles (`catCover` + `mediaHTML`) keep their existing
  fallback chain (branded cover art, `fallbackImg`), untouched.

Verified by `tests/test_category_image_fallback.py`.

## 2. Storefront performance

The existing fast-pages stack (upload-time image optimisation, `.400w.webp`
companions, immutable `?v=` asset caching, gzip, read-through catalogue and
category caches, ETags, skeleton grids, lazy/eager media) is unchanged and
re-verified. This batch adds the cache-token discipline step every shipped
byte change requires:

* `js/store.js`, `js/app.js`, `js/admin.js`, `css/fonts.css`, `sw.js`, every
  page head and `tools/sync_favicon_head.py` move to **token `?v=203`**, with
  `tests/test_asset_cache_token.py` fingerprints refreshed via its own
  `--bump` helper.

## 3. Dedicated Trash Bin for deleted products

The Trash was already the default delete path (snapshot + restore + manual
purge, `?permanent=1` for the hard path). This batch hardens the tombstone:

* `supabase_store.delete_products_strict` now writes **both**
  `source='deleted'` **and** `is_deleted = true` on the products row, with a
  graceful fallback to the legacy marker on a table the migration has not
  reached yet.
* `supabase_store.upsert_products` (the live-save path) **clears both
  markers**, so Restore truly revives the row.
* `broadcast_posts.is_postable` also refuses `is_deleted`/`isDeleted` rows
  and `source='deleted'` — defence in depth behind the existing live-catalogue
  filter.
* New columns land through `supabase_schema.sql` (and
  `migrate_variant_pricing_and_carts.sql` for existing databases):
  `alter table products add column if not exists is_deleted boolean not null default false;`

Verified by `tests/test_trash_isolation_guards.py` (tombstone shape, legacy
fallback, revive-on-save, broadcast refusal end-to-end through the admin API).

## 4. Variant-level pricing & supplier cost controls

### 4.1 Admin editor (`js/admin.js`)

* **Per-option selling price**: every option value gets a `data-opt-price`
  number box ("Selling price per option"), saved into the existing
  `optionPrices` map. Typed figures survive the option-chip repaint (they are
  collected *before* the DOM is replaced, exactly like per-variant stock).
* **Per-option supplier cost**: `data-opt-cost` boxes ("Supplier cost per
  option") saved into the NEW `optionSupplierCost` map.
* **Dedicated product-level field**: `Supplier cost (Naira ₦)`
  (`name="supplierCost"`) sits under the price field with "shoppers never see
  this figure" wording. A cleared box the admin touched saves "not set"; an
  untouched box keeps the stored figure.
* **Base price optional**: the editor only demands the base PRICE (NAIRA ₦)
  when the options are not fully priced; a variant-priced product with an
  empty base box keeps its saved base (never wiped to ₦0). A live hint under
  the price field explains the optionality.

### 4.2 Persistence (`catalog.py`, `supabase_store.py`)

* `catalog.normalize` emits `supplierCost` (non-negative int or `None`,
  commas/spaces tolerated) and `optionSupplierCost` (label → int map, same
  cleaning rules as `optionPrices`), folding the aliases `supplier_cost`,
  `supplierPrice`, `option_supplier_cost(s)`, `variantSupplierCosts`,
  `optionCosts`.
* Both join `_CRITICAL_PRODUCT_COLUMNS` (a missing column **blocks** the save
  instead of silently dropping the data) and `_PRODUCT_PATCH_COLUMNS` (the
  fast edit path maps every alias).
* Schema: `alter table products add column if not exists "supplierCost"
  numeric;` and `..."optionSupplierCost" jsonb;` in `supabase_schema.sql`,
  `schema_sections/01_products.sql` (regenerated, still < 6 KB per section)
  and the variant migration file. `MIGRATION_COPY_PASTE.md` regenerated with
  `tools/make_copy_paste_doc.py`.

### 4.3 Public safety

* `api._FORBIDDEN_PUBLIC_KEYS` strips `supplierCost`/`optionSupplierCost`
  (and aliases) from every public row — costs are owner reference data.

### 4.4 Storefront rendering (`js/store.js`, `js/app.js`)

* `hasNgn` treats a variant-priced row as NGN-anchored, so it keeps exact
  Naira maths and the shopper's currency instead of being mis-read as a
  CFA-native ₦0 product.
* `priceOf` falls back to the **cheapest** option price when a row has no
  base price (sorting/filtering/legacy carts never see ₦0); the same fallback
  exists server-side in `api._server_unit_price`, so an order line that
  reached checkout without its variant string still totals correctly.
* Product cards keep rendering the exact range "₦2,500 – ₦3,200"
  (`priceRangeOf` / `priceHTML`, unchanged shape), now also for base-less
  rows; the PDP option switch uses `JA.displayCur(p)`.

Verified by `tests/test_variant_supplier_costs.py`.

## 5. Single-run supplier price sync & CSV importer

* NEW `scripts/backfill_variant_prices.py` — a **single-run** administrative
  backfill that reads each supplier page (product-level link and per-option
  links) through `security.safe_fetch` (the app's SSRF guard), extracts the
  first confident Naira-marked amount, and fills ONLY missing
  `supplierCost` / `optionSupplierCost` values. Dry run by default;
  `--apply` writes through `catalog.upsert`; `--limit`, `--id` and `--status`
  supported. No loops, no workers on Render. Selling prices are never
  touched.
* The native **Import Historical CSV** flow is unchanged (dashboard button →
  `/admin/accounting#import` modal → `/api/admin/accounting/import-historical`),
  and now pinned by `tests/test_admin_csv_importer_ui.py`: upload modal,
  dry-run-first staging report, per-row NGN/FCFA detection.
* The stock watchdog remains limited to In-Stock ⇄ Out-of-Stock mirroring
  (in-process, hourly; it never rewrites retail prices).

Verified by `tests/test_backfill_variant_prices.py` (parsing, plan, SSRF,
write-through, CLI limits) and `tests/test_admin_csv_importer_ui.py`.

## 6. Owner checklist

1. Run `supabase_schema.sql` once (Supabase SQL editor) — or just re-deploy:
   the repair block is add-only and idempotent, adding `"supplierCost"`,
   `"optionSupplierCost"` and `is_deleted` to `products`.
2. Optional: run `python scripts/backfill_variant_prices.py` once (dry run),
   review the plan, then `--apply` to fill missing supplier costs.
3. Hard-refresh an admin tab once so the `?v=203` bundle replaces `?v=202`.
