# Jaurastore Publication Audit & Production Verification Report

**Date:** 2026-09-08
**Status:** Complete Read-Only Audit · Review Before SQL Execution

---

## 1. Executive Summary & Policy Context

The owner has established the updated product publication policy for Jaurastore:
- **Approved Live Products (`online = true`):** All valid existing products with committed real images, valid positive retail prices (`priceNgn > 0` and `priceCfa > 0`), and valid stock (`stock > 0`) should be public on the storefront.
- **Admin Default:** New valid products created/saved through the Admin portal must default to `online = true`.
- **Excluded from Storefront (`online = false`):** Placeholder-only, no-image, test fixture, invalid-price, or invalid-stock products must remain offline.
- **`wix-001` remains offline:** It points to a placeholder image and has `stock_quantity = 0`.
- **`wix-012` remains offline:** It carries `priceNgn = 0` (invalid retail price) and will remain offline until a valid price is configured by the owner.
- **Strict Non-Destructive Invariants:**
  - **Zero Row Deletions:** No product row is deleted.
  - **Zero ID Renames:** No product ID is modified or renamed (legacy `wix-*` and canonical `jau-*` IDs preserved).
  - **Zero Column Drift:** No prices, stock levels, or image URLs are modified.
  - **Narrow Mutation Scope:** Any subsequent SQL update touches ONLY `online` and `updated_at`.

### Previous Audit vs. Current Classification
| Scope | Total Rows | Approved Live | Operator Offline | Placeholder Only | Test Fixtures | No Image / Review |
|---|---|---|---|---|---|---|
| **Previous Audit (Supabase subset)** | 53 | 29 | 2 | 3 | 17 | 2 |
| **Current Policy (Full Merged Catalogue)** | **275** | **181** | **2** | **75** | **17** | **0** |

---

## 2. Classification Summary

| Classification | Count | Target `online` | Inclusion Criteria |
|---|---|---|---|
| **Approved Live** | **181** | `true` | Real committed photo found, valid `priceNgn > 0`, `priceCfa > 0`, valid `stock > 0`, not in forced offline, not a fixture. |
| **Operator Offline** | **2** | `false` | Explicit operator decision (`wix-001` placeholder/0-stock, `wix-012` 0-price). |
| **Placeholder Only** | **75** | `false` | Image resolves to `_placeholder.jpg` or `usesPlaceholder=true`. |
| **Test Fixtures** | **17** | `false` | Pytest fixtures (`jau-stock-*`, `jau-mirror-*`) excluded from customer store. |
| **No Image** | **0** | `false` | No image path resolvable. |
| **Needs Review** | **0** | `false` | Unverifiable price/stock data outside operator offline. |
| **TOTAL** | **275** | — | Every single product in the repository catalogue classified. |

---

## 3. Operator Offline Products (2 IDs)

These 2 products are kept offline (`online = false`) by explicit operator policy:

1. **`wix-001`** — *10000 mah power bank*
   - **Reason:** Image is `images/products/_placeholder.jpg` and `stock_quantity = 0`. It was flagged online before its assets and inventory were ready.
   - **Policy Action:** Set `online = false`.
2. **`wix-012`** — *24 K nicotinamide toner 300 ml*
   - **Reason:** `priceNgn = 0`. Zero is not a valid retail price and would allow free checkout.
   - **Policy Action:** Set `online = false` until the owner enters a valid positive price.

---

## 4. Test Fixtures Excluded (17 IDs)

These 17 products are automated test scaffolding and must remain `online = false`:

- `jau-mirror-fail`: X
- `jau-mirror-ok`: Y
- `jau-mirror-post`: Mirror Post
- `jau-stock-a`: Stock Test jau-stock-a
- `jau-stock-b`: Stock Test jau-stock-b
- `jau-stock-dcf`: Stock Test jau-stock-dcf
- `jau-stock-dec`: Stock Test jau-stock-dec
- `jau-stock-del`: Stock Test jau-stock-del
- `jau-stock-dnp`: Stock Test jau-stock-dnp
- `jau-stock-dpn`: Stock Test jau-stock-dpn
- `jau-stock-em2`: Stock Test jau-stock-em2
- `jau-stock-eml`: Stock Test jau-stock-eml
- `jau-stock-idem`: Stock Test jau-stock-idem
- `jau-stock-opr`: Stock Test jau-stock-opr
- `jau-stock-opt`: Stock Test jau-stock-opt
- `jau-stock-pay`: Stock Test jau-stock-pay
- `jau-stock-rop`: Stock Test jau-stock-rop

---

## 5. Placeholder-Only Products (75 IDs)

These 75 products lack real committed product photography and remain `online = false`:

| Product ID | Name | Category | Price ₦ | Image Path |
|---|---|---|---|---|
| `wix-002` | 100L storage bag | household | ₦10,300 | `images/products/_placeholder.jpg` |
| `wix-006` | 17 PCs sealed crisper plastics | household | ₦13,000 | `images/products/_placeholder.jpg` |
| `wix-007` | 2 by 6 DD birthmark | hair-care | ₦83,300 | `images/products/_placeholder.jpg` |
| `wix-022` | 4 by 4 full closure bob | hair-care | ₦45,325 | `images/products/_placeholder.jpg` |
| `wix-026` | 7pcs aluminum pot | household | ₦78,500 | `images/products/_placeholder.jpg` |
| `wix-028` | A58 Smartwatch with Jewelry set | gift-set | ₦20,900 | `images/products/_placeholder.jpg` |
| `wix-037` | Ankara Chic set | ankara | ₦15,925 | `images/products/_placeholder.jpg` |
| `wix-038` | Ankara pants | ankara | ₦8,600 | `images/products/_placeholder.jpg` |
| `wix-041` | Apple manicure set | beauty | ₦1,950 | `images/products/_placeholder.jpg` |
| `wix-044` | Pu leather hand bag | bags | ₦9,300 | `images/products/_placeholder.jpg` |
| `wix-045` | Bag C | bags | ₦19,150 | `images/products/_placeholder.jpg` |
| `wix-046` | Bag K | bags | ₦7,500 | `images/products/_placeholder.jpg` |
| `wix-047` | Beach bag and hat | bags | ₦19,000 | `images/products/_placeholder.jpg` |
| `wix-055` | Bioaqua Vitamin C Set | beauty | ₦17,000 | `images/products/_placeholder.jpg` |
| `wix-062` | Bracelet D | accessories | ₦3,000 | `images/products/_placeholder.jpg` |
| `wix-065` | Car diffuser | household | ₦1,225 | `images/products/_placeholder.jpg` |
| `wix-074` | Cover heels | shoes | ₦24,500 | `images/products/_placeholder.jpg` |
| `wix-089` | F15 WIRELESS MIC - double | gadgets | ₦18,000 | `images/products/_placeholder.jpg` |
| `wix-090` | F15 WIRELESS MIC - single | gadgets | ₦13,000 | `images/products/_placeholder.jpg` |
| `wix-093` | Fashion Tote Bag | bags | ₦19,000 | `images/products/_placeholder.jpg` |
| `wix-098` | Flat sandals A | shoes | ₦23,300 | `images/products/_placeholder.jpg` |
| `wix-099` | Flower bouquet with light | decor | ₦6,150 | `images/products/_placeholder.jpg` |
| `wix-106` | Foldable water bottle | household | ₦4,410 | `images/products/_placeholder.jpg` |
| `wix-107` | Fringe bob | hair-care | ₦33,075 | `images/products/_placeholder.jpg` |
| `wix-108` | Fur cap | clothing | ₦3,700 | `images/products/_placeholder.jpg` |
| `wix-109` | Gift box with rose | gift-set | ₦5,400 | `images/products/_placeholder.jpg` |
| `wix-111` | Gucci bag | bags | ₦19,000 | `images/products/_placeholder.jpg` |
| `wix-112` | Hair band (3pcs) | hair-care | ₦1,700 | `images/products/_placeholder.jpg` |
| `wix-115` | Hair dryer | hair-care | ₦6,100 | `images/products/_placeholder.jpg` |
| `wix-120` | Headphone | gadgets | ₦12,500 | `images/products/_placeholder.jpg` |
| `wix-121` | Heels | shoes | ₦27,000 | `images/products/_placeholder.jpg` |
| `wix-122` | Heels | shoes | ₦27,000 | `images/products/_placeholder.jpg` |
| `wix-123` | Heels a 1 | shoes | ₦24,500 | `images/products/_placeholder.jpg` |
| `wix-124` | Heels B | shoes | ₦24,500 | `images/products/_placeholder.jpg` |
| `wix-125` | Heels C | shoes | ₦24,500 | `images/products/_placeholder.jpg` |
| `wix-126` | Heels D | shoes | ₦20,825 | `images/products/_placeholder.jpg` |
| `wix-127` | Heels E | shoes | ₦20,825 | `images/products/_placeholder.jpg` |
| `wix-128` | Heels F | shoes | ₦23,275 | `images/products/_placeholder.jpg` |
| `wix-129` | Heels G | shoes | ₦24,500 | `images/products/_placeholder.jpg` |
| `wix-130` | Heels H | shoes | ₦22,050 | `images/products/_placeholder.jpg` |
| `wix-131` | Heels H 1 | shoes | ₦24,500 | `images/products/_placeholder.jpg` |
| `wix-143` | Key chain | accessories | ₦9,800 | `images/products/_placeholder.jpg` |
| `wix-144` | Key holder | accessories | ₦750 | `images/products/_placeholder.jpg` |
| `wix-146` | Kitten heels | shoes | ₦24,500 | `images/products/_placeholder.jpg` |
| `wix-148` | Knuckles ring b | accessories | ₦3,700 | `images/products/_placeholder.jpg` |
| `wix-149` | L16 Tripod | gadgets | ₦22,000 | `images/products/_placeholder.jpg` |
| `wix-150` | Lacoste bag | bags | ₦19,000 | `images/products/_placeholder.jpg` |
| `wix-151` | Laptop / School Bag | bags | ₦19,000 | `images/products/_placeholder.jpg` |
| `wix-152` | Leather Crocs bag | bags | ₦19,000 | `images/products/_placeholder.jpg` |
| `wix-153` | LED cube light | gadgets | ₦7,350 | `images/products/_placeholder.jpg` |
| `wix-155` | Lip gloss C | beauty | ₦2,200 | `images/products/_placeholder.jpg` |
| `wix-156` | Lip gloss G | beauty | ₦2,205 | `images/products/_placeholder.jpg` |
| `wix-157` | Lip scrub | beauty | ₦2,450 | `images/products/_placeholder.jpg` |
| `wix-158` | Lipgloss F | beauty | ₦2,205 | `images/products/_placeholder.jpg` |
| `wix-159` | Love decor with light | decor | ₦5,650 | `images/products/_placeholder.jpg` |
| `wix-160` | Luki 2 in 1 bag | bags | ₦20,000 | `images/products/_placeholder.jpg` |
| `wix-161` | LV Bag | bags | ₦19,000 | `images/products/_placeholder.jpg` |
| `wix-171` | Min min bag | bags | ₦10,000 | `images/products/_placeholder.jpg` |
| `wix-173` | Mini fan. ₦. 3850 | gadgets | ₦3,850 | `images/products/_placeholder.jpg` |
| `wix-176` | Mini potted flower | decor | ₦1,593 | `images/products/_placeholder.jpg` |
| `wix-191` | Owambe turbo fan | gadgets | ₦14,000 | `images/products/_placeholder.jpg` |
| `wix-192` | H fashion 2 in 1 bag | bags | ₦18,400 | `images/products/_placeholder.jpg` |
| `wix-196` | Portable rechargeable fan | gadgets | ₦14,700 | `images/products/_placeholder.jpg` |
| `wix-197` | Press on nail | beauty | ₦2,950 | `images/products/_placeholder.jpg` |
| `wix-204` | Q8 wireless Mircophone | gadgets | ₦15,700 | `images/products/_placeholder.jpg` |
| `wix-205` | Quality shoes 1 | shoes | ₦22,100 | `images/products/_placeholder.jpg` |
| `wix-215` | Skull cap | hair-care | ₦16,660 | `images/products/_placeholder.jpg` |
| `wix-216` | Skull cap | hair-care | ₦19,600 | `images/products/_placeholder.jpg` |
| `wix-217` | Skull cap jerry | hair-care | ₦19,600 | `images/products/_placeholder.jpg` |
| `wix-226` | T frontal AG Bounce | hair-care | ₦35,525 | `images/products/_placeholder.jpg` |
| `wix-233` | Tory Burch bag | bags | ₦19,000 | `images/products/_placeholder.jpg` |
| `wix-236` | Trapezoid bag | bags | ₦14,700 | `images/products/_placeholder.jpg` |
| `wix-240` | Vacuum phone holder | gadgets | ₦4,900 | `images/products/_placeholder.jpg` |
| `wix-242` | Valentino bag | bags | ₦19,000 | `images/products/_placeholder.jpg` |
| `wix-258` | Zara heels | shoes | ₦25,725 | `images/products/_placeholder.jpg` |

---

## 6. Approved Live Products (181 IDs)

These 181 products satisfy all criteria (committed photo + valid price + valid stock) and are approved for public storefront visibility (`online = true`):

| Product ID | Name | Category | Price ₦ | Price CFA | Stock | Image Path |
|---|---|---|---|---|---|---|
| `wix-003` | 10in1 RAF sandwich maker | household | ₦69,850 | 28,500 CFA | 24 | `images/products/10in1-raf-sandwich-maker.jpg` |
| `wix-004` | 12 pcs dessini pot set | household | ₦85,000 | 34,700 CFA | 24 | `images/products/12-pcs-dessini-pot-set-85000.jpg` |
| `wix-005` | 12pcs spice jar set | household | ₦8,550 | 15,000 CFA | 24 | `images/products/12pcs-spice-jar-set.jpg` |
| `wix-008` | 2 in 1 Lipstick lipgloss | beauty | ₦2,400 | 1,000 CFA | 24 | `images/products/2-in-1-lipstick-lipgloss-2400.jpg` |
| `wix-009` | 2 layers plate rack | household | ₦28,000 | 11,500 CFA | 24 | `images/products/2-layers-plate-rack-28000.jpg` |
| `wix-010` | 2 step soap holder | household | ₦8,550 | 1,200 CFA | 24 | `images/products/2-step-soap-holder.jpg` |
| `wix-011` | 24 k gold skincare set | beauty | ₦14,700 | 8,000 CFA | 24 | `images/products/24-k-gold-skincare-set.jpg` |
| `wix-013` | 24 PCs of gold cutlery set | household | ₦18,500 | 7,600 CFA | 24 | `images/products/24-pcs-of-gold-cutlery-set-18500.jpg` |
| `wix-014` | 2L RAF mixer with bowl | household | ₦24,500 | 10,000 CFA | 24 | `images/products/2l-raf-mixer-with-bowl.jpg` |
| `wix-015` | 3 in 1 fancy cup set | household | ₦9,100 | 3,700 CFA | 24 | `images/products/3-in-1-fancy-cup-set-9100.jpg` |
| `wix-016` | 3 in 1 laptop bag | bags | ₦11,550 | 4,700 CFA | 24 | `images/products/3-in-1-laptop-bag.jpg` |
| `wix-017` | 3 in 1 vacuum flask gift set | gift-set | ₦6,100 | 2,500 CFA | 24 | `images/products/3-in-1-vacuum-flask-gift-set.jpg` |
| `wix-018` | 3 layer bathroom rack | household | ₦8,550 | 5,000 CFA | 24 | `images/products/3-layer-bathroom-rack.jpg` |
| `wix-019` | 3 layers dish washer | household | ₦29,000 | 15,000 CFA | 24 | `images/products/3-layers-dish-washer-27000.jpg` |
| `wix-020` | 3 step trolley | household | ₦33,000 | 20,000 CFA | 24 | `images/products/3-step-trolley.png` |
| `wix-021` | 3in1 Towel | household | ₦7,550 | 7,000 CFA | 24 | `images/products/3in1-towel.jpg` |
| `wix-023` | 4 Partition Lunch Plate With Bowl | household | ₦12,750 | 7,000 CFA | 24 | `images/products/4-partition-lunch-plate-with-bowl.jpg` |
| `wix-024` | 6 in 1 lipgloss set | beauty | ₦4,900 | 2,000 CFA | 24 | `images/products/6-in-1-lipgloss-set.jpg` |
| `wix-025` | 6oup flask | household | ₦2,450 | 1,650 CFA | 24 | `images/products/6oup-flask.jpg` |
| `wix-027` | A soap holder | household | ₦8,550 | 1,000 CFA | 24 | `images/products/a-soap-holder.jpg` |
| `wix-029` | AC Design Fan | gadgets | ₦13,500 | 5,600 CFA | 24 | `images/products/ac-design-fan-13500.jpg` |
| `wix-030` | Acne removal face mask | beauty | ₦550 | 500 CFA | 24 | `images/products/acne-removal-face-mask-550.jpg` |
| `wix-031` | Adire maxi skirt | ankara | ₦24,000 | 15,000 CFA | 24 | `images/products/adire-maxi-skirt-24000.jpg` |
| `wix-032` | Advanced snail cleanser (cosrx) | beauty | ₦8,550 | 3,500 CFA | 24 | `images/products/advanced-snail-cleanser-cosrx.jpg` |
| `wix-033` | Advanced snail toner (cosrx) | beauty | ₦8,550 | 3,500 CFA | 24 | `images/products/advanced-snail-toner-cosrx.jpg` |
| `wix-034` | Afro ponytail | hair-care | ₦6,000 | 2,400 CFA | 24 | `images/products/afro-ponytail.jpg` |
| `wix-035` | Aisy Watch | accessories | ₦8,550 | 3,500 CFA | 24 | `images/products/aisy-watch.jpg` |
| `wix-036` | ALSTOW professional clipper | beauty | ₦20,400 | 15,000 CFA | 24 | `images/products/alstow-professional-clipper.png` |
| `wix-039` | Ankara shorts | ankara | ₦4,900 | 3,000 CFA | 24 | `images/products/ankara-shorts.png` |
| `wix-040` | Ankara sundress/long gown | ankara | ₦12,250 | 5,000 CFA | 24 | `images/products/ankara-sundresslong-gown.jpg` |
| `wix-042` | Argan body oil | beauty | ₦6,150 | 2,500 CFA | 24 | `images/products/argan-body-oil.jpg` |
| `wix-043` | Ashake gown | ankara | ₦12,250 | 8,000 CFA | 24 | `images/products/ashake-gown.png` |
| `wix-048` | Beard balm | beauty | ₦2,950 | 2,000 CFA | 24 | `images/products/beard-balm.jpg` |
| `wix-049` | Big baby wipes | children | ₦2,205 | 1,500 CFA | 24 | `images/products/big-baby-wipes.jpg` |
| `wix-050` | Big hair dryer set | hair-care | ₦18,000 | 9,000 CFA | 24 | `images/products/big-hair-dryer-set.jpg` |
| `wix-051` | Big professional hair straightener | hair-care | ₦9,800 | 6,000 CFA | 24 | `images/products/big-professional-hair-straightener.jpg` |
| `wix-052` | Big wig bag | hair-care | ₦4,700 | 2,800 CFA | 24 | `images/products/big-wig-bag.jpg` |
| `wix-053` | Bioaqua Vitamin C Brightening Essence Water | beauty | ₦4,850 | 2,000 CFA | 24 | `images/products/bioaqua-vitamin-c-brightening-essence-water.jpg` |
| `wix-054` | Bioaqua Vitamin C Moisturizing Cleanser | beauty | ₦3,200 | 1,300 CFA | 24 | `images/products/bioaqua-vitamin-c-moisturizing-cleanser.jpg` |
| `wix-056` | 3 in 1 water bottle set | household | ₦8,550 | 3,500 CFA | 24 | `images/products/black-3-in-1-water-bottle-set.jpg` |
| `wix-057` | Blue idea rechargeable clipper | beauty | ₦11,750 | 6,000 CFA | 24 | `images/products/blue-idea-rechargeable-clipper.jpg` |
| `wix-058` | Bp monitor | gadgets | ₦12,500 | 6,500 CFA | 24 | `images/products/bp-monitor-12000.jpg` |
| `wix-059` | Bracelet A | accessories | ₦3,000 | 1,250 CFA | 24 | `images/products/bracelet-a.jpg` |
| `wix-060` | Bracelet B | accessories | ₦3,000 | 1,250 CFA | 24 | `images/products/bracelet-b.jpg` |
| `wix-061` | Bracelet C | accessories | ₦3,000 | 1,250 CFA | 24 | `images/products/bracelet-c.jpg` |
| `wix-063` | Burgundy heels | shoes | ₦24,700 | 12,000 CFA | 24 | `images/products/burgundy-heels.jpg` |
| `wix-064` | Car air freshener | household | ₦4,900 | 3,000 CFA | 24 | `images/products/car-air-freshener-a.jpg` |
| `wix-066` | Ceramic mug | gift-set | ₦4,400 | 2,800 CFA | 24 | `images/products/ceramic-mug.jpg` |
| `wix-067` | Children 12lcd Writing Tablet | children | ₦4,150 | 2,000 CFA | 24 | `images/products/children-12lcd-writing-tablet.jpg` |
| `wix-068` | Choker set | accessories | ₦9,500 | 4,000 CFA | 24 | `images/products/choker-set-9500.jpg` |
| `wix-069` | Clothes pegs | household | ₦1,470 | 1,000 CFA | 24 | `images/products/clothes-pegs.jpg` |
| `wix-070` | Coconut oil deodorant | beauty | ₦1,348 | 900 CFA | 24 | `images/products/coconut-oil-deodorant.jpg` |
| `wix-071` | Cold keeping vacuum cup | household | ₦11,000 | 7,000 CFA | 24 | `images/products/cold-keeping-vacuum-cup.jpg` |
| `wix-072` | Collagen Snail Serum 30ml | beauty | ₦2,750 | 1,500 CFA | 24 | `images/products/collagen-snail-serum-30ml.jpg` |
| `wix-073` | Corset Defined Bubu gowns 15 types | ankara | ₦12,250 | 8,000 CFA | 24 | `images/products/corset-defined-bubu-gown.jpg` |
| `wix-075` | Dark Brown 7 In 1 Gift Set For Men 2 | gift-set | ₦22,000 | 10,000 CFA | 24 | `images/products/dark-brown-7-in-1-gift-set-for-men-2.jpg` |
| `wix-076` | Deblve unisex set | accessories | ₦12,750 | 5,200 CFA | 24 | `images/products/deblve-unisex-set-12750.jpg` |
| `wix-077` | Decor | decor | ₦3,675 | 3,000 CFA | 24 | `images/products/decor.jpg` |
| `wix-078` | Diary gift set | gift-set | ₦20,800 | 10,000 CFA | 24 | `images/products/diary-gift-set.jpg` |
| `wix-079` | Dr Rashel Sun Cream | beauty | ₦6,000 | 3,500 CFA | 24 | `images/products/dr-rashel-sun-cream.jpg` |
| `wix-080` | Dr Rashel Vitamin C Set | beauty | ₦24,500 | 13,500 CFA | 24 | `images/products/dr-rashel-vitamin-c-set.jpg` |
| `wix-081` | Dry grinder | household | ₦8,550 | 6,800 CFA | 24 | `images/products/dry-grinder.jpg` |
| `wix-082` | Electric foot grinder | household | ₦6,600 | 4,000 CFA | 24 | `images/products/electric-foot-grinder.jpg` |
| `wix-083` | Electric Hand mixer | household | ₦11,750 | 6,000 CFA | 24 | `images/products/electric-hand-mixer.jpg` |
| `wix-084` | Electric Steam iro | household | ₦16,000 | 6,800 CFA | 24 | `images/products/electric-steam-iron-16000.jpg` |
| `wix-085` | Electronic personal scale | household | ₦9,000 | 5,000 CFA | 24 | `images/products/electronic-personal-scale-9000.jpg` |
| `wix-086` | Enny big bag  Comes with a scarf | bags | ₦22,000 | 9,000 CFA | 24 | `images/products/enny-big-bag-comes-with-a-scarf-22000.jpg` |
| `wix-087` | Essential oil for humidifier | decor | ₦5,900 | 850 CFA | 24 | `images/products/essential-oil-for-humidifier.jpg` |
| `wix-088` | Exfoliating foot peel mask | beauty | ₦550 | 1,500 CFA | 24 | `images/products/exfoliating-foot-peel-mask.jpg` |
| `wix-091` | Face mask sheet | beauty | ₦490 | 400 CFA | 24 | `images/products/face-mask-sheet.jpg` |
| `wix-092` | Fashion Bracelet 5600 1 | accessories | ₦5,600 | 2,300 CFA | 24 | `images/products/fashion-bracelet-5600-1.jpg` |
| `wix-094` | Female bag | bags | ₦19,000 | 9,000 CFA | 24 | `images/products/female-bag.jpg` |
| `wix-095` | Female bag | bags | ₦19,000 | 10,000 CFA | 24 | `images/products/female-bag-1.jpg` |
| `wix-096` | Fife set | gift-set | ₦20,200 | 10,000 CFA | 24 | `images/products/fife-set.jpg` |
| `wix-097` | Flat sandals | shoes | ₦23,300 | 9,500 CFA | 24 | `images/products/flat-sandals-23300.jpg` |
| `wix-100` | Flower pot 1 | decor | ₦3,185 | 2,000 CFA | 24 | `images/products/flower-pot-1.jpg` |
| `wix-101` | Flower Tote bag | bags | ₦19,000 | 3,500 CFA | 24 | `images/products/flower-tote-bag.jpg` |
| `wix-102` | Foldable bag | bags | ₦3,000 | 2,500 CFA | 24 | `images/products/foldable-bag.jpg` |
| `wix-103` | Foldable strip bag | bags | ₦19,000 | 6,000 CFA | 24 | `images/products/foldable-strip-bag.jpg` |
| `wix-104` | Foldable travelling bag | bags | ₦9,300 | 5,000 CFA | 24 | `images/products/foldable-travelling-bag.jpg` |
| `wix-105` | Foldable travelling bag with wheels | bags | ₦19,000 | 7,000 CFA | 24 | `images/products/foldable-travelling-bag-with-wheels.jpg` |
| `wix-110` | Glow In The Dark | decor | ₦1,960 | 1,200 CFA | 24 | `images/products/glow-in-the-dark.jpg` |
| `wix-113` | Hair brush set 5 i | hair-care | ₦18,800 | 4,000 CFA | 24 | `images/products/hair-brush-set-4-in-1.jpg` |
| `wix-114` | Hair clip | hair-care | ₦2,695 | 2,000 CFA | 24 | `images/products/hair-clip.jpg` |
| `wix-116` | Hair Scrunchies 4 in 1 | hair-care | ₦3,000 | 1,250 CFA | 24 | `images/products/hair-scrunchies-4-in-1.jpg` |
| `wix-117` | Vaseline Hand cream | beauty | ₦1,100 | 450 CFA | 24 | `images/products/hand-cream.jpg` |
| `wix-118` | Hand cream 30g | beauty | ₦550 | 250 CFA | 24 | `images/products/hand-cream-30g-550.jpg` |
| `wix-119` | Handpat | household | ₦8,100 | 3,300 CFA | 24 | `images/products/handpat.jpg` |
| `wix-132` | Hello master bottle | household | ₦2,450 | 1,000 CFA | 24 | `images/products/hello-master-bottle-2450.jpg` |
| `wix-133` | Hot comb | hair-care | ₦11,000 | 4,500 CFA | 24 | `images/products/hot-comb-1.jpg` |
| `wix-134` | i18pro flip phone | gadgets | ₦26,500 | 10,900 CFA | 24 | `images/products/i18pro-flip-phone-26500.jpg` |
| `wix-135` | i20 ultra 2 max suit | gadgets | ₦19,600 | 8,000 CFA | 24 | `images/products/i20-ultra-2-max-suit.jpg` |
| `wix-136` | i60 Suit Extreme 12 in 1 set | gadgets | ₦12,500 | 12,000 CFA | 24 | `images/products/i60-suit-extreme-12-in-1-set.jpg` |
| `wix-137` | Ice bottle | gift-set | ₦4,900 | 2,000 CFA | 24 | `images/products/ice-bottle.jpg` |
| `wix-138` | Inflatable sofa with leg rest | decor | ₦28,500 | 11,700 CFA | 24 | `images/products/inflatable-sofa-with-leg-rest-28500.jpg` |
| `wix-139` | Insulated mugs | household | ₦10,500 | 4,350 CFA | 24 | `images/products/insulated-mugs-10500.png` |
| `wix-140` | Jelly baby bag | children | ₦4,655 | 1,900 CFA | 24 | `images/products/jelly-baby-bag.jpg` |
| `wix-141` | Jewelry case | accessories | ₦4,300 | 1,750 CFA | 24 | `images/products/jewelry-case.jpg` |
| `wix-142` | July diffuser | decor | ₦4,900 | 2,000 CFA | 24 | `images/products/july-diffuser.jpg` |
| `wix-145` | Kitchen tissue | household | ₦3,000 | 1,250 CFA | 24 | `images/products/kitchen-tissue-3000.jpg` |
| `wix-147` | Knuckles ring a | accessories | ₦2,950 | 1,200 CFA | 24 | `images/products/knuckles-ring-a.jpg` |
| `wix-154` | Lip Gel | beauty | ₦1,150 | 450 CFA | 24 | `images/products/lip-gel.jpg` |
| `wix-162` | M10 earpod | gadgets | ₦9,500 | 3,900 CFA | 24 | `images/products/m10-earpod-9500.jpg` |
| `wix-163` | Mailer bag (25 PCs) | gift-set | ₦4,400 | 1,800 CFA | 24 | `images/products/mailer-bag-25-pcs.jpg` |
| `wix-164` | Manicure set | beauty | ₦2,000 | 850 CFA | 24 | `images/products/manicure-set.jpg` |
| `wix-165` | Marble center table | decor | ₦85,500 | 35,000 CFA | 24 | `images/products/marble-center-table-85500.jpg` |
| `wix-166` | Marble Twin Center Table 100000 | decor | ₦100,000 | 41,000 CFA | 24 | `images/products/marble-twin-center-table-100000.jpg` |
| `wix-167` | Matturi wristwatch | accessories | ₦22,000 | 9,000 CFA | 24 | `images/products/matturi-wristwatch-22000.jpg` |
| `wix-168` | Men 5 in 1 set | gift-set | ₦19,150 | 7,800 CFA | 24 | `images/products/men-5-in-1-set.jpg` |
| `wix-169` | Men 7 in 1 gift set | gift-set | ₦22,500 | 9,400 CFA | 24 | `images/products/men-7-in-1-gift-set.jpg` |
| `wix-170` | Menstrual relief belt | beauty | ₦12,000 | 4,900 CFA | 24 | `images/products/menstrual-relief-belt-12000.jpg` |
| `wix-172` | Mini diffuser | decor | ₦2,300 | 950 CFA | 24 | `images/products/mini-diffuser.jpg` |
| `wix-174` | Mini influencer light | gadgets | ₦11,800 | 4,800 CFA | 24 | `images/products/mini-influencer-light.png` |
| `wix-175` | Mini Mirror | beauty | ₦950 | 400 CFA | 24 | `images/products/mini-mirror.jpg` |
| `wix-177` | Mini q light | gadgets | ₦2,450 | 1,000 CFA | 24 | `images/products/mini-q-light.jpg` |
| `wix-178` | Mop | household | ₦17,500 | 7,200 CFA | 24 | `images/products/mop-17500.jpg` |
| `wix-179` | Mouth spray | beauty | ₦1,400 | 600 CFA | 24 | `images/products/mouth-spray-1400.jpg` |
| `wix-180` | Sand painting table lamp | decor | ₦11,800 | 4,800 CFA | 24 | `images/products/multi-light-decor.jpg` |
| `wix-181` | Naidu pearl watch | accessories | ₦9,800 | 4,000 CFA | 24 | `images/products/naidu-pearl-watch-9800.jpg` |
| `wix-182` | Necklace 1 | accessories | ₦2,950 | 1,200 CFA | 24 | `images/products/necklace-1.jpg` |
| `wix-183` | Necklace 2 | accessories | ₦3,700 | 1,500 CFA | 24 | `images/products/necklace-2.jpg` |
| `wix-184` | Necklace 3 | accessories | ₦3,900 | 1,600 CFA | 24 | `images/products/necklace-3.jpg` |
| `wix-185` | Necklace 4 | accessories | ₦5,000 | 2,000 CFA | 24 | `images/products/necklace-4.jpg` |
| `wix-186` | Necklace 4 | accessories | ₦9,800 | 2,000 CFA | 24 | `images/products/necklace-4-1.jpg` |
| `wix-187` | Necklace 4 | accessories | ₦5,000 | 2,000 CFA | 24 | `images/products/necklace-4-2.jpg` |
| `wix-188` | Neckpiece | accessories | ₦9,800 | 2,400 CFA | 24 | `images/products/neckpiece.jpg` |
| `wix-189` | Niacinamide 5% Serum | beauty | ₦2,450 | 1,000 CFA | 24 | `images/products/niacinamide-5-serum.jpg` |
| `wix-190` | Order me light | household | ₦10,500 | 4,300 CFA | 24 | `images/products/order-me-light.png` |
| `wix-193` | Pink Silk Pillowcase | hair-care | ₦14,700 | 6,000 CFA | 24 | `images/products/pink-silk-pillowcase.jpg` |
| `wix-194` | PO1 auto face tracking selfie tripod | gadgets | ₦12,500 | 10,500 CFA | 24 | `images/products/po1-auto-face-tracking-selfie-tripod.jpg` |
| `wix-195` | Poedagar Watch | accessories | ₦22,000 | 9,000 CFA | 24 | `images/products/poedagar-watch.jpg` |
| `wix-198` | Press on nail B | beauty | ₦2,950 | 1,200 CFA | 24 | `images/products/press-on-nail-1.jpg` |
| `wix-199` | Press on Nail | beauty | ₦4,400 | 1,200 CFA | 24 | `images/products/press-on-nail-2.jpg` |
| `wix-200` | Press on nail C | beauty | ₦2,950 | 1,200 CFA | 24 | `images/products/press-on-nail-c.jpg` |
| `wix-201` | Press on nail D | beauty | ₦3,200 | 1,300 CFA | 24 | `images/products/press-on-nail-d.jpg` |
| `wix-202` | Press on nail E | beauty | ₦2,950 | 1,200 CFA | 24 | `images/products/press-on-nail-e.jpg` |
| `wix-203` | Q13 Ai smart tracking tripod with light | gadgets | ₦40,000 | 16,350 CFA | 24 | `images/products/q13-ai-smart-tracking-tripod-with-light-40000.jpg` |
| `wix-206` | R on the edge Watch | gift-set | ₦24,000 | 9,800 CFA | 24 | `images/products/r-on-the-edge-watch.jpg` |
| `wix-207` | Rabbit Phone holder | gadgets | ₦1,850 | 750 CFA | 24 | `images/products/rabbit-phone-holder.jpg` |
| `wix-208` | Rock 006 Smartwatch gift set | gift-set | ₦23,250 | 9,500 CFA | 24 | `images/products/rock-006-smartwatch-gift-set.jpg` |
| `wix-209` | S11 pro Max Smartwatch | accessories | ₦9,800 | 4,000 CFA | 24 | `images/products/s11-pro-max-smart-watch.jpg` |
| `wix-210` | S25 ultra pro Tab | gadgets | ₦12,500 | 50,000 CFA | 24 | `images/products/s25-ultra-pro-tab.jpg` |
| `wix-211` | Selfie stick | gadgets | ₦5,000 | 2,100 CFA | 24 | `images/products/selfie-stick-5000.jpg` |
| `wix-212` | Shoe wipes | household | ₦1,600 | 680 CFA | 24 | `images/products/shoe-wipes-1600.jpg` |
| `wix-213` | Silicone spoon 12 in 1 set | household | ₦11,800 | 4,800 CFA | 24 | `images/products/silicone-spoon-12-in-1-set.jpg` |
| `wix-214` | Single hexagon floating shelf | decor | ₦10,300 | 4,200 CFA | 24 | `images/products/single-hexagon-floating-shelf.jpg` |
| `wix-218` | Small beaded jelly back | household | ₦4,165 | 1,700 CFA | 24 | `images/products/small-beaded-jelly-back.jpg` |
| `wix-219` | Small foldable washing machine | household | ₦36,750 | 15,000 CFA | 24 | `images/products/small-foldable-washing-machine.jpg` |
| `wix-220` | Small scented candles 100g | decor | ₦2,950 | 1,200 CFA | 24 | `images/products/small-scented-candles.jpg` |
| `wix-221` | Smartwatch with Game pad | gadgets | ₦12,500 | 9,000 CFA | 24 | `images/products/smartwatch-with-game-pad.jpg` |
| `wix-222` | Smoothing solution hair removal care rollon | beauty | ₦3,553 | 1,450 CFA | 24 | `images/products/smoothing-solution-hair-removal-chair.jpg` |
| `wix-223` | Sports big bag | bags | ₦19,000 | 4,500 CFA | 24 | `images/products/sports-big-bag.jpg` |
| `wix-224` | Stanley cup | household | ₦13,000 | 5,400 CFA | 24 | `images/products/stanley-cup-13000.jpg` |
| `wix-225` | Summer tote bag | bags | ₦9,300 | 3,800 CFA | 24 | `images/products/summer-tote-bag.jpg` |
| `wix-227` | Talking cactus | decor | ₦8,575 | 3,500 CFA | 24 | `images/products/talking-cactus.jpg` |
| `wix-228` | Thank you sticker | gift-set | ₦1,350 | 550 CFA | 24 | `images/products/thank-you-sticker.jpg` |
| `wix-229` | Thank you sticker 2 | gift-set | ₦2,400 | 1,000 CFA | 24 | `images/products/thank-you-sticker-2.png` |
| `wix-230` | Toilet fragrance | household | ₦1,500 | 600 CFA | 24 | `images/products/toilet-fragrance.jpg` |
| `wix-231` | Tomi wrist watch 02 | accessories | ₦23,000 | 9,600 CFA | 24 | `images/products/tomi-wrist-watch-02-23000.jpg` |
| `wix-232` | Tomi wrist watch | accessories | ₦16,000 | 6,500 CFA | 24 | `images/products/tomi-wrist-watch-16000.jpg` |
| `wix-234` | Tote bag A | bags | ₦19,000 | 2,800 CFA | 24 | `images/products/tote-bag-a-1.jpg` |
| `wix-235` | Transparent Jewelry box | accessories | ₦9,800 | 4,000 CFA | 24 | `images/products/transparent-jewelry-box.jpg` |
| `wix-237` | Tumeric peel off mask | beauty | ₦3,700 | 1,500 CFA | 24 | `images/products/tumeric-peel-off-mask.jpg` |
| `wix-238` | Two piece (white top with ankara trousers ) | ankara | ₦17,150 | 7,000 CFA | 24 | `images/products/two-piece-white-top-with-ankara-trousers.png` |
| `wix-239` | Underwear hanger | household | ₦5,000 | 2,100 CFA | 24 | `images/products/underwear-hanger-5000.jpg` |
| `wix-241` | Vacuum phone holder | gadgets | ₦4,900 | 2,000 CFA | 24 | `images/products/vacuum-phone-holder-4900.jpg` |
| `wix-243` | Valenzo watch | gift-set | ₦9,800 | 4,000 CFA | 24 | `images/products/valenzo-watch.jpg` |
| `wix-244` | Vegetable peeler | beauty | ₦550 | 6,000 CFA | 24 | `images/products/vegetable-peeler.jpg` |
| `wix-245` | Veggies slicer 16 in 1 | household | ₦8,550 | 5,000 CFA | 24 | `images/products/veggies-slicer.jpg` |
| `wix-246` | Vicky midi bag | bags | ₦18,400 | 7,500 CFA | 24 | `images/products/vicky-midi-bag.jpg` |
| `wix-247` | Video making kit | gadgets | ₦10,250 | 4,200 CFA | 24 | `images/products/video-making-kit.jpg` |
| `wix-248` | Vogue frame | decor | ₦8,550 | 3,500 CFA | 24 | `images/products/vogue-frame.jpg` |
| `wix-249` | Waist/ Cross Bag | bags | ₦5,390 | 2,200 CFA | 24 | `images/products/waist-cross-bag.jpg` |
| `wix-250` | Wall suction hanger | household | ₦1,600 | 650 CFA | 24 | `images/products/wall-suction-hanger.jpg` |
| `wix-251` | White gift set | gift-set | ₦15,400 | 6,300 CFA | 24 | `images/products/white-gift-set.jpg` |
| `wix-252` | Wipes 5 packs | beauty | ₦2,500 | 1,100 CFA | 24 | `images/products/wipes-5-packs-2500.jpg` |
| `wix-253` | Wireless Resonance speaker | gadgets | ₦12,500 | 6,000 CFA | 24 | `images/products/wireless-resonance-speaker.jpg` |
| `wix-254` | Wooden hanger | household | ₦4,150 | 1,700 CFA | 24 | `images/products/wooden-hanger.jpg` |
| `wix-255` | Work table | decor | ₦5,900 | 5,500 CFA | 24 | `images/products/work-table.jpg` |
| `wix-256` | Wrist bp monitor | gadgets | ₦12,500 | 5,200 CFA | 24 | `images/products/wrist-bp-monitor-12500.jpg` |
| `wix-257` | Wrist watch box | accessories | ₦2,200 | 900 CFA | 24 | `images/products/wrist-watch-box-2200.jpg` |

---

## 7. Safety Contract & Invariant Proof

1. **No Deletions:** Total row count remains 275 before and after the audit.
2. **No ID Renames:** The set of product IDs before and after remains identical.
3. **No Price Modifications:** `priceNgn`, `priceCfa`, `compareNgn`, and `compareCfa` remain untouched.
4. **No Stock Modifications:** `stock`, `stock_quantity`, and `optionStock` remain untouched.
5. **No Image URL Modifications:** `image`, `image_url`, and `images` gallery remain untouched.
6. **Narrow Mutation Scope:** The reviewable SQL file `publication_review.sql` updates ONLY `online` and `updated_at`.
7. **Drift Guard:** Pre-flight and CI guards in `drift_guard.py` and `publication_review.sql` verify all counts, ID sets, price validations, and forced-offline invariants before any operation can proceed.
8. **Cache Invalidation:** ETag generation uses SHA-256 over the response JSON payload, guaranteeing that mobile and desktop clients invalidate stale local caches immediately upon catalogue updates.
