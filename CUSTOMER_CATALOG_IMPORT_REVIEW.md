# Customer catalogue import review

**Status: dry-run only. No import has been executed.**

## Approved source and exclusions

`data/seed.json` is the approved legacy/Wix seed. It contains **258** customer
rows (`wix-*`). The import rejects every other prefix; the 17 `jau-*` rows are
fixtures and are reported under `fixture_exclusions`. No image is uploaded or
changed by this migration: the report records the exact product-to-image
mapping for separate review.

## Review procedure

Run this against the target Supabase project (the command does not print
credentials):

```sh
python3 import_customer_catalog.py --report customer_catalog_import_report.json
```

The resulting report is the complete dry-run report and includes local source
count, live Supabase count, missing and existing IDs, duplicate detection,
proposed inserts/updates, image mappings, price/stock validation, fixture
exclusions, and the `online=true` decisions. It refuses to guess the live
count when Supabase credentials are unavailable. It does not write products.

The report must show 258 source rows, zero duplicate source or Supabase IDs,
and a valid price/stock check before approval. Missing IDs are explicit, never
silently inferred. `existing_supabase_count` is read from Supabase, not from
the local cache.

## Explicit approval gate

Only after the attached dry-run report has been reviewed may an operator run:

```sh
python3 import_customer_catalog.py \
  --confirm-real-import \
  --report customer_catalog_import_report.json
```

The real mode performs ID-keyed upserts only. It preserves IDs and existing
nonblank values, fills only missing values, and intentionally sets `online`
true on all 258 `wix-*` rows. A rerun is safe and creates no duplicates. It
never calls `replace_all`, deletes products, orders, reviews, receipts, or
local files, and performs no image upload.

The production storefront continues to read Supabase products and filters
offline rows; stock quantity is not rendered to customers. Admin-created
products default to `online=true` (the existing admin normalization in
`catalog.py` enforces this). Product deletion remains a reviewed admin action:
when orders, reviews, or history exist, unpublish (`online=false`) is preferred
rather than removing the record.

## Safety notes

- `SUPABASE_SERVICE_ROLE_KEY` is read from the environment and never included
  in the report.
- The migration intentionally does not use the existing broad catalogue push
  script; this is a separately reviewed, customer-only import.
- The 17 fixture IDs are excluded even if they happen to exist locally.
