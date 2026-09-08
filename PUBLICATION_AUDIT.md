# Jaurastore Publication Audit & Production Verification Report (Reconciled)

**Date:** 2026-09-08
**Status:** Complete Read-Only Audit · Reconciled Against 53-Row Production Supabase Database

---

## 1. Production Reconciliation & Policy Summary

Production Supabase `public.products` currently contains **53 rows**, whereas the full repository catalogue contains **275 rows** (258 seed/catalogue items + 17 fixtures).

This audit clearly separates existing Supabase rows from local catalogue rows. The publication SQL updates **only existing production rows** and **never silently inserts** missing local products.

### Complete Scope Separation

| Dataset | Row Count | Approved Live | Operator Offline | Placeholder Only | Test Fixtures | No Image / Review |
|---|---|---|---|---|---|---|
| **Local Catalogue (Merged)** | **275** | **181** | **2** | **75** | **17** | **0** |
| **Existing Production Supabase** | **53** | **29** | **2** | **3** | **17** | **2** |
| **Present in Both (Supabase Live Target)** | **29** | **29** | — | — | — | — |
| **Approved Live Missing from Supabase** | **152** | — | — | — | — | — |

### Key Policy Decisions & Invariants
1. **Existing Supabase Live Target (29 IDs):** Valid products with real images, positive retail prices (`priceNgn > 0`), and valid stock (`stock > 0`) existing in Supabase are updated to `online = true`.
2. **Missing from Production (152 IDs):** Approved local products that do not exist in Supabase are reported as `missing_from_production`. They are **NOT** inserted by this publication SQL.
3. **Importing is Kept Separate:** Importing the 152 missing products requires a separate, reviewed schema and data migration covering names, descriptions, categories, prices, stock, and image URLs.
4. **Existing Offline Products (5 IDs):** `wix-001` (placeholder/0 stock), `wix-012` (priceNgn=0), and 3 placeholder products (`wix-041`, `wix-055`, `wix-197`) remain `online = false`.
5. **Test Fixtures (17 IDs):** Pytest fixtures (`jau-*`) remain `online = false`.
6. **Strict Non-Destructive Invariants:**
   - **0 rows deleted** (no `DELETE`, no `DROP`)
   - **0 IDs renamed**
   - **0 price modifications**
   - **0 stock modifications**
   - **0 image_url modifications**
   - **0 name or category modifications**
   - **Mutation scope:** ONLY `online` and `updated_at` on existing production rows.

---

## 2. Existing Production Live Products (29 IDs — Target `online = true`)

These 29 products exist in Supabase, have committed photos, positive prices, and valid stock:

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

## 3. Approved Live Products Missing from Supabase (152 IDs — `missing_from_production`)

These 152 products are approved under the publication policy (real photo, valid price, valid stock) in the local catalogue but **do not currently exist in the 53-row Supabase database**.

**They are NOT inserted by the publication SQL.** They require a separate data migration before going live.

```
wix-003, wix-004, wix-005, wix-008, wix-009, wix-010, wix-013, wix-014, wix-015, wix-016, wix-017, wix-018, wix-019, wix-020, wix-021, wix-023, wix-024, wix-025, wix-027, wix-029, wix-031, wix-034, wix-035, wix-036, wix-039, wix-040, wix-043, wix-048, wix-049, wix-050, wix-051, wix-052, wix-056, wix-057, wix-058, wix-059, wix-060, wix-061, wix-063, wix-064, wix-066, wix-067, wix-068, wix-069, wix-071, wix-073, wix-075, wix-076, wix-077, wix-078, wix-081, wix-082, wix-083, wix-084, wix-085, wix-086, wix-087, wix-092, wix-094, wix-095, wix-096, wix-097, wix-100, wix-101, wix-102, wix-103, wix-104, wix-105, wix-110, wix-113, wix-114, wix-116, wix-119, wix-132, wix-133, wix-134, wix-135, wix-136, wix-137, wix-138, wix-139, wix-140, wix-141, wix-142, wix-145, wix-147, wix-154, wix-162, wix-165, wix-166, wix-167, wix-168, wix-169, wix-170, wix-172, wix-174, wix-175, wix-177, wix-178, wix-179, wix-180, wix-181, wix-182, wix-183, wix-184, wix-185, wix-186, wix-187, wix-188, wix-190, wix-193, wix-194, wix-195, wix-203, wix-206, wix-207, wix-208, wix-209, wix-210, wix-211, wix-212, wix-213, wix-214, wix-218, wix-219, wix-220, wix-221, wix-223, wix-224, wix-225, wix-227, wix-230, wix-231, wix-232, wix-234, wix-235, wix-238, wix-239, wix-241, wix-243, wix-245, wix-246, wix-247, wix-248, wix-249, wix-250, wix-251, wix-253, wix-254, wix-255, wix-256, wix-257
```

---

## 4. Existing Production Offline Products (5 IDs — Target `online = false`)

### A. Operator Offline (2 IDs)
1. **`wix-001`** (*10000 mah power bank*): Uses `images/products/_placeholder.jpg` and has `stock_quantity = 0`.
2. **`wix-012`** (*24 K nicotinamide toner 300 ml*): Has `priceNgn = 0` (invalid retail price).

### B. Placeholder-Only in Production (3 IDs)
1. **`wix-041`** (*Adire maxi skirt*): Uses placeholder image.
2. **`wix-055`** (*Alloy wheel brush*): Uses placeholder image.
3. **`wix-197`** (*Press on nail*): Uses placeholder image.

---

## 5. Existing Production Test Fixtures (17 IDs — Target `online = false`)

```
jau-mirror-fail, jau-mirror-ok, jau-mirror-post, jau-stock-a, jau-stock-b, jau-stock-dcf, jau-stock-dec, jau-stock-del, jau-stock-dnp, jau-stock-dpn, jau-stock-em2, jau-stock-eml, jau-stock-idem, jau-stock-opr, jau-stock-opt, jau-stock-pay, jau-stock-rop
```

---

## 6. Architecture, Admin & Storefront Guarantees

1. **Admin Default:** New valid products created through the Admin panel default to `online = true` (`catalog_mod.normalize` and admin form).
2. **Storefront Filtering:** Storefront queries (`/api/catalog`, `js/store.js`) filter `online IS TRUE`, hiding offline products from visitors while allowing authenticated admins to view all products with `?all=1`.
3. **Cache & ETag Invalidation:** ETag is calculated via SHA-256 over the response JSON payload (`Cache-Control: public, max-age=30, must-revalidate`), guaranteeing that any change immediately invalidates mobile and browser caches.
4. **Drift Guard & Safety Contract:** `drift_guard.py`, `tests/test_publication_audit.py`, and `publication_review.sql` enforce that zero rows are deleted, zero IDs are renamed, zero prices/stock/images are modified, and no missing products are inserted.
