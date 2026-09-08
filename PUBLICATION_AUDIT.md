# Jaurastore Production Publication Audit (Reconciled 29 / 7 / 17 = 53 Rows)

**Date:** 2026-09-08
**Status:** Complete Read-Only Audit · Production Reconciliation (29 Live / 7 Offline / 17 Fixtures)

---

## 1. Production Scope & Approved Classification

Production Supabase `public.products` contains exactly **53 rows**. All 53 rows are classified under the owner's policy:

```
29 existing valid products (online=true) + 7 offline/review (online=false) + 17 test fixtures (online=false) = 53 rows
```

| Classification | Count | Target `online` | Policy Criteria & Status |
|---|---|---|---|
| **Approved Live Products** | **29** | `true` | Real committed photo found, valid `priceNgn > 0`, `priceCfa > 0`, valid `stock > 0`. |
| **Offline / Review Products** | **7** | `false` | Operator offline decisions (2), placeholder-only (3), and unconfirmed no-image items (2). |
| **Test Fixtures** | **17** | `false` | Pytest fixtures (`jau-stock-*`, `jau-mirror-*`) excluded from customer storefront. |
| **TOTAL PRODUCTION ROWS** | **53** | — | **Every single production Supabase row is accounted for.** |

### Local Catalogue Comparison
- **Full Local Merged Catalogue:** **275 rows** (181 approved live + 2 operator offline + 75 placeholder + 17 fixtures).
- **Approved Local Products Missing from Supabase (`missing_from_production`):** **152 rows**.
- **Importing Separation:** The 152 missing products are **NOT** inserted by the publication SQL. Product importing remains separate and requires a distinct reviewed migration for names, categories, prices, stock, and images.

---

## 2. Approved Live Products in Production (29 IDs — Target `online = true`)

These 29 products exist in Supabase and meet all live publication criteria:

| Product ID | Name | Category | Price ₦ | Price CFA | Stock | Image Path |
|---|---|---|---|---|---|---|
| `wix-011` | 24 k gold skincare set | beauty | ₦14,700 | 8,000 CFA | 24 | `images/products/24-k-gold-skincare-set.jpg` |
| `wix-030` | Acne removal face mask | beauty | ₦550 | 500 CFA | 24 | `images/products/acne-removal-face-mask-550.jpg` |
| `wix-032` | Advanced snail cleanser (cosrx) | beauty | ₦8,550 | 3,500 CFA | 24 | `images/products/advanced-snail-cleanser-cosrx.jpg` |
| `wix-033` | Advanced snail toner (cosrx) | beauty | ₦8,550 | 3,500 CFA | 24 | `images/products/advanced-snail-toner-cosrx.jpg` |
| `wix-042` | Argan body oil | beauty | ₦6,150 | 2,500 CFA | 24 | `images/products/argan-body-oil.jpg` |
| `wix-053` | Bioaqua Vitamin C Brightening Essence Water | beauty | ₦4,850 | 2,000 CFA | 24 | `images/products/bioaqua-vitamin-c-brightening-essence-water.jpg` |
| `wix-054` | Bioaqua Vitamin C Moisturizing Cleanser | beauty | ₦3,200 | 1,300 CFA | 24 | `images/products/bioaqua-vitamin-c-moisturizing-cleanser.jpg` |
| `wix-070` | Coconut oil deodorant | beauty | ₦1,348 | 900 CFA | 24 | `images/products/coconut-oil-deodorant.jpg` |
| `wix-072` | Collagen Snail Serum 30ml | beauty | ₦2,750 | 1,500 CFA | 24 | `images/products/collagen-snail-serum-30ml.jpg` |
| `wix-079` | Dr Rashel Sun Cream | beauty | ₦6,000 | 3,500 CFA | 24 | `images/products/dr-rashel-sun-cream.jpg` |
| `wix-080` | Dr Rashel Vitamin C Set | beauty | ₦24,500 | 13,500 CFA | 24 | `images/products/dr-rashel-vitamin-c-set.jpg` |
| `wix-088` | Exfoliating foot peel mask | beauty | ₦550 | 1,500 CFA | 24 | `images/products/exfoliating-foot-peel-mask.jpg` |
| `wix-091` | Face mask sheet | beauty | ₦490 | 400 CFA | 24 | `images/products/face-mask-sheet.jpg` |
| `wix-117` | Vaseline Hand cream | beauty | ₦1,100 | 450 CFA | 24 | `images/products/hand-cream.jpg` |
| `wix-118` | Hand cream 30g | beauty | ₦550 | 250 CFA | 24 | `images/products/hand-cream-30g-550.jpg` |
| `wix-163` | Mailer bag (25 PCs) | gift-set | ₦4,400 | 1,800 CFA | 24 | `images/products/mailer-bag-25-pcs.jpg` |
| `wix-164` | Manicure set | beauty | ₦2,000 | 850 CFA | 24 | `images/products/manicure-set.jpg` |
| `wix-189` | Niacinamide 5% Serum | beauty | ₦2,450 | 1,000 CFA | 24 | `images/products/niacinamide-5-serum.jpg` |
| `wix-198` | Press on nail B | beauty | ₦2,950 | 1,200 CFA | 24 | `images/products/press-on-nail-1.jpg` |
| `wix-199` | Press on Nail | beauty | ₦4,400 | 1,200 CFA | 24 | `images/products/press-on-nail-2.jpg` |
| `wix-200` | Press on nail C | beauty | ₦2,950 | 1,200 CFA | 24 | `images/products/press-on-nail-c.jpg` |
| `wix-201` | Press on nail D | beauty | ₦3,200 | 1,300 CFA | 24 | `images/products/press-on-nail-d.jpg` |
| `wix-202` | Press on nail E | beauty | ₦2,950 | 1,200 CFA | 24 | `images/products/press-on-nail-e.jpg` |
| `wix-222` | Smoothing solution hair removal care rollon | beauty | ₦3,553 | 1,450 CFA | 24 | `images/products/smoothing-solution-hair-removal-chair.jpg` |
| `wix-228` | Thank you sticker | gift-set | ₦1,350 | 550 CFA | 24 | `images/products/thank-you-sticker.jpg` |
| `wix-229` | Thank you sticker 2 | gift-set | ₦2,400 | 1,000 CFA | 24 | `images/products/thank-you-sticker-2.png` |
| `wix-237` | Tumeric peel off mask | beauty | ₦3,700 | 1,500 CFA | 24 | `images/products/tumeric-peel-off-mask.jpg` |
| `wix-244` | Vegetable peeler | beauty | ₦550 | 6,000 CFA | 24 | `images/products/vegetable-peeler.jpg` |
| `wix-252` | Wipes 5 packs | beauty | ₦2,500 | 1,100 CFA | 24 | `images/products/wipes-5-packs-2500.jpg` |

---

## 3. Non-Fixture Offline / Review Products (7 IDs — Target `online = false`)

These 7 products exist in Supabase and must remain `online = false`:

| Product ID | Name | Reason for Offline Status |
|---|---|---|
| `wix-001` | 10000 mah power bank | Uses placeholder image and `stock_quantity = 0`. |
| `wix-012` | 24 K nicotinamide toner 300 ml | `priceNgn = 0` (invalid retail price; free checkout hazard). |
| `wix-041` | Adire maxi skirt | Uses placeholder image. |
| `wix-055` | Alloy wheel brush | Uses placeholder image. |
| `wix-197` | Press on nail | Uses placeholder image. |
| `jau-mtot3318` | Tote bag (no image) | No image confirmed; remains offline until image is confirmed. |
| `wix-002` | 100L storage bag (no image) | No image confirmed; remains offline until image is confirmed. |

---

## 4. Test Fixtures in Production (17 IDs — Target `online = false`)

Automated pytest fixtures that must never be published to the customer store:

```
jau-mirror-fail, jau-mirror-ok, jau-mirror-post, jau-stock-a, jau-stock-b, jau-stock-dcf, jau-stock-dec, jau-stock-del, jau-stock-dnp, jau-stock-dpn, jau-stock-em2, jau-stock-eml, jau-stock-idem, jau-stock-opr, jau-stock-opt, jau-stock-pay, jau-stock-rop
```

---

## 5. Approved Local Products Missing from Supabase (152 IDs — `missing_from_production`)

These 152 products are approved in the repository catalogue but **do not currently exist in the 53-row Supabase database**.

**They are NOT inserted by the publication SQL.** Importing them requires a separate reviewed data migration.

```
wix-003, wix-004, wix-005, wix-008, wix-009, wix-010, wix-013, wix-014, wix-015, wix-016, wix-017, wix-018, wix-019, wix-020, wix-021, wix-023, wix-024, wix-025, wix-027, wix-029, wix-031, wix-034, wix-035, wix-036, wix-039, wix-040, wix-043, wix-048, wix-049, wix-050, wix-051, wix-052, wix-056, wix-057, wix-058, wix-059, wix-060, wix-061, wix-063, wix-064, wix-066, wix-067, wix-068, wix-069, wix-071, wix-073, wix-075, wix-076, wix-077, wix-078, wix-081, wix-082, wix-083, wix-084, wix-085, wix-086, wix-087, wix-092, wix-094, wix-095, wix-096, wix-097, wix-100, wix-101, wix-102, wix-103, wix-104, wix-105, wix-110, wix-113, wix-114, wix-116, wix-119, wix-132, wix-133, wix-134, wix-135, wix-136, wix-137, wix-138, wix-139, wix-140, wix-141, wix-142, wix-145, wix-147, wix-154, wix-162, wix-165, wix-166, wix-167, wix-168, wix-169, wix-170, wix-172, wix-174, wix-175, wix-177, wix-178, wix-179, wix-180, wix-181, wix-182, wix-183, wix-184, wix-185, wix-186, wix-187, wix-188, wix-190, wix-193, wix-194, wix-195, wix-203, wix-206, wix-207, wix-208, wix-209, wix-210, wix-211, wix-212, wix-213, wix-214, wix-218, wix-219, wix-220, wix-221, wix-223, wix-224, wix-225, wix-227, wix-230, wix-231, wix-232, wix-234, wix-235, wix-238, wix-239, wix-241, wix-243, wix-245, wix-246, wix-247, wix-248, wix-249, wix-250, wix-251, wix-253, wix-254, wix-255, wix-256, wix-257
```

---

## 6. Safety & Invariance Contract

1. **Zero Insertions:** `publication_review.sql` contains NO `INSERT` statements.
2. **Zero Deletions:** No rows are deleted (no `DELETE`, no `DROP`).
3. **Zero ID Renames:** All primary keys remain unchanged.
4. **Zero Column Mutations:** No changes to prices, stock, image URLs, categories, or names.
5. **Drift Guard:** `drift_guard.py` and the SQL `DO $$` block abort if any production ID is unclassified.
6. **Admin Behavior:** Valid new products saved through Admin default to `online = true`.
7. **Storefront Filtering:** Storefront queries filter `online IS TRUE`.
8. **Cache & ETag Invalidation:** Response body-based SHA-256 ETag ensures instant cache invalidation across all mobile devices.
