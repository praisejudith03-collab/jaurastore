# Catalog & Inventory Audit — 2026-09-12

Read-only audit run before the "pinned admin dock + dual-WhatsApp" release.
No product, image or catalog row was modified, deleted or reset.

## Method

- **Live storefront** — `GET https://jaurastore.com.ng/sitemap.xml` (rebuilt
  on every request from `catalog.merged()`, i.e. the same source the shop
  serves) and spot-reads of `GET /api/catalog`.
- **Tracked data state** — `data/seed.json` (immutable Wix import),
  `data/catalog.json` (admin overrides), `js/products-data.js` (snapshot).
- **Regression guards** — the full pytest suite, including
  `test_publication_audit.py`, `test_catalog_watchdog.py`,
  `test_catalog_supabase_258.py`, `test_catalog_mirror.py`, and the
  conftest tracked-file guard that fails any test which rewrites the
  catalog files.

## Findings

| Check | Result |
| --- | --- |
| Live online products | **280** (71 admin-panel `jau-mt*` + 209 approved `wix-*`) |
| Ghost / unauthorized IDs on the live store | **NONE** — every id is either portal-minted (`jau-mt<base36>`, `source: "admin"`) or part of the approved import |
| Live `wix-*` rows missing from the tracked catalog | **NONE** |
| Test / sample / fixture products in tracked data | **NONE** (seed = snapshot = 257 rows, no duplicates, every row has numeric stock + image) |
| Deliberately retired rows (in snapshot, offline live) | 48 — the owner-unpublished set (wix-006, wix-007, wix-108, …) the catalog watchdog already knows about |
| Public stock exposure | Catalog API serves only `stock_status` in/out; numbers are admin-session only |
| Bank-details persistence chain | Verified end-to-end and pinned by 32 green tests in `test_payment_settings.py` + `test_site_settings_supabase.py` |

## Conclusion

The catalog already contains exactly the owner's verified items. **No
cleanup was needed and none was performed** — every live row comes strictly
from the admin panel or the approved import.

## Standing guards (unchanged)

- `Catalog watchdog` GitHub workflow — hourly storefront ↔ Supabase
  reconciliation; opens an issue on any drift.
- `drift_guard.py` + `tests/test_publication_audit.py` — classify every
  production row; abort on any unclassified ID; publication SQL may never
  INSERT or DELETE.
- pytest conftest `TRACKED_DATA_FILES` guard — a test run that rewrites the
  tracked catalog files fails the suite.
- Supabase writes from automation remain locked behind the reviewed
  `image-migration` / `image-migration-review` environments; the service
  key is never present in this sandbox or in source.
