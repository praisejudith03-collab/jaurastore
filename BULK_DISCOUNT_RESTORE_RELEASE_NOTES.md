# Restore per-product bulk discount inputs (2026-11-07)

**What the owner asked for:** bring the **Bulk Quantity Threshold** (`bulkQty`)
and **Bulk Discount Percentage** (`bulkPercent`) input fields back into the
Admin Product Editor, make sure what is typed is actually saved, and confirm
the discount fires at checkout and is visible on the product page and cart.

## The short version

Only the **inputs** were ever missing. The backend never stopped supporting the
feature:

| layer | state before this change |
| --- | --- |
| `products."bulkQty"` / `products."bulkPercent"` columns | present in `schema_sections/01_products.sql`, `supabase_schema.sql`, `auto_migrate.py` |
| `catalog.normalize` / `catalog.bulk_discount_for` | present — cleans the pair and prices orders |
| `supabase_store` column list + alias map | present — writes and reads the columns |
| `store.bulkPercentFor` (storefront pricing) | present — per-product offer beats the shop-wide tiers |
| cart / checkout / receipt `bulk-tag` | present — the applied % is labelled |
| **Admin editor input boxes** | **REMOVED on 2026-10-05** |

So for just over a year the shop could *read* a per-product discount that
nothing in the app could *write* except a direct database edit. This restores
the inputs and the save wiring.

## What changed

### 1. `js/admin.js` — the editor inputs come back

* `productForm()` renders a **Bulk volume discount** section directly under
  **Stock quantity** (the Inventory / Pricing part of the form), with:
  * `name="bulkQty"` — Bulk Quantity Threshold (units), `min="2"`
  * `name="bulkPercent"` — Bulk Discount Percentage (%), `min="1" max="90"`
  * a one-sentence read-back under the boxes (`bulkPreview`) naming the **first
    qualifying quantity**, because the rule is *more than* `bulkQty`: a
    threshold of 10 means **11 units**, and owners reasonably read "10+" as 10.
* `handleProductSubmit()` reads both boxes with `num()` and ships
  `bulkQty` / `bulkPercent` on **every** save — as an explicit `null` when a
  box is empty. Previously the payload carried only the row's old numbers, so a
  threshold the owner deleted came straight back on the next save.
* `bindBulkPreview()` repaints the read-back sentence live as the owner types.
* `EDIT_FIELD_MAP` records the two fields as edited, for the audit trail.

Empty boxes render as `""`, never `0` or `NaN` — a literal `0` reads as a real
threshold and can never fire.

### 2. `js/app.js` + `js/i18n.js` + `css/style.css` — the product-page prompt

The PDP savings prompt is back as **one line** under the quantity stepper (not
the information box that was purged on 2026-10-05):

* `pdpBulkText(p, qty)` decides what it says and is repainted from
  `updateStockUI`, so it counts the shopper up to the threshold as the quantity
  changes — *"Add 3 more to save 15% at checkout"* → *"Bulk discount unlocked —
  15% off at checkout"*.
* It mirrors the server's two different comparisons exactly:
  * a product's own offer fires at **more than** `bulkQty` (exclusive);
  * a shop-wide tier fires at `minQuantity` **or more** (inclusive).
* When promotions are switched off, `JA.bulkDiscountTiers()` answers `[]` and
  the line stays hidden.
* New strings `pdp.bulkNext` / `pdp.bulkReady` in **both** the English and
  French dictionaries (the build fails if French is incomplete).

### 3. Asset token 242 → 243

`tools/asset_version.py --write` was run because four shipped assets changed
(`js/admin.js`, `js/app.js`, `js/i18n.js`, `css/style.css`). The versioned URLs
are cached `immutable`, so without the bump every returning phone would have
kept the old build for a year.

## Tests

Five existing tests **asserted the removal** and had to be re-pointed at the
restored behaviour (each now encodes the new contract rather than the old one):

| test | was | now |
| --- | --- | --- |
| `test_bulk_discount.py::test_simplified_product_editor_hides_legacy_per_product_discount_fields` | boxes absent | renamed to `test_product_editor_exposes_per_product_bulk_discount_fields`; asserts the boxes render **and** that `num("bulkQty")` / `num("bulkPercent")` are read into the payload |
| `test_admin_editor_cleanup.py` | `bulkQty`/`bulkPercent` in the "removed control" list | in the approved set; plus new checks that an unset offer renders empty and a saved offer round-trips into the boxes |
| `test_pr98_feature_updates.py::test_product_editor_excludes_redundant_discount_and_merchandising_fields` | `bulkQty`/`bulkPercent` in the removed tuple | removed from the tuple; asserts the inputs are genuinely rendered |
| `test_stock_toggle_persistence.py::test_clean_editor_has_only_the_approved_product_controls` | exact `names` set without the pair | pair added to the approved set and to the required labels |
| `test_enterprise_hardening.py` (2 tests) | `pdp-bulk` banned from `app.js` and from the rendered PDP | the *box* stays banned; the one-line prompt is asserted present, and a new `test_pdp_bulk_prompt_counts_to_the_right_threshold` pins the exclusive-vs-inclusive arithmetic |

New coverage added:

* `test_typed_bulk_values_survive_an_ordinary_edit_elsewhere` — a rename keeps
  the offer, a threshold change lands, and clearing both boxes switches it off.
* `test_pdp_bulk_prompt_counts_to_the_right_threshold` — 10 units against
  `bulkQty: 10` earns **no** discount; 11 does; a half-filled pair falls back to
  the shop-wide tiers instead of quoting a percentage that can never fire.

**Suite:** `pytest tests/ -q` → **2695 passed, 32 skipped**. Every skip is
`Playwright chromium not available` — the sandbox this was written in can only
reach npm/pypi/GitHub hosts, so the browser binary could not be downloaded.
Those tests run normally in GitHub Actions, which has no such restriction.

## Verifying persistence

`python3 tools/verify_bulk_discount.py` walks the real admin write path and the
real checkout pricing path and prints a PASS/FAIL per step:

1. admin save writes `bulkQty=10` / `bulkPercent=15`
2. the **public** catalogue the browser reads carries the offer
3. order exactly 10 units → **full price** (the threshold is exclusive)
4. order 11 units → **15% off every unit**, `bulkDiscount` on the response
5. the stored order payload and the admin order view both carry the 15%
6. an unrelated edit (a rename) does **not** drop the offer
7. clearing both boxes switches it off

All seven pass.

Supabase itself was **not** touched: this sandbox has no Supabase credentials.
No migration is required — `bulkQty` and `bulkPercent` are already in
`schema_sections/01_products.sql` and `supabase_schema.sql`, are in
`auto_migrate.py`'s additive column list (so a live table missing them heals on
the next boot), and are in `supabase_store.py`'s column list and alias map.
After the Render deploy completes, the live check is one row: open any product
in Admin → Products, type a threshold and a percentage, Save, reload, and
confirm both boxes still hold the numbers.

## Behaviour, in one table

| situation | what happens |
| --- | --- |
| product has `bulkQty` + `bulkPercent`, customer orders **more than** `bulkQty` | that percentage comes off every unit of that product |
| same product, customer orders `bulkQty` or fewer | no discount; the PDP prompt says how many more to add |
| product has only one of the two values | treated as unset (both or neither) — falls back to the shop-wide tiers |
| product has neither | shop-wide Growth tiers apply, as before |
| variants | counts **all variants of that product combined** |
| promotions switched off in Admin | no discount quoted or applied anywhere |
