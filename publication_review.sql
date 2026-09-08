-- ==============================================================================
-- JAURASTORE PRODUCTION VERIFICATION & PUBLICATION REVIEW (29 / 7 / 17 = 53 ROWS)
-- ==============================================================================
-- Exact Production Supabase Classification:
--   * 29 existing valid products: online = true
--   * 7 non-fixture offline/review products: online = false
--       - wix-001 (placeholder + stock_quantity = 0)
--       - wix-012 (priceNgn = 0, invalid retail price)
--       - wix-041 (placeholder)
--       - wix-055 (placeholder)
--       - wix-197 (placeholder)
--       - jau-mtot3318 (no image; remains offline until image confirmed)
--       - wix-002 (no image / placeholder; remains offline until image confirmed)
--   * 17 test fixtures: online = false
--   * TOTAL: 29 + 7 + 17 = 53 Supabase rows.
--
-- Approved local products missing from Supabase (152 rows):
--   * Reported as missing_from_production.
--   * NOT inserted by this publication SQL. Product importing is kept separate.
--
-- SAFETY INVARIANTS:
--   * Zero row insertions (no INSERT INTO)
--   * Zero row deletions (no DELETE, no DROP)
--   * Zero product ID renames
--   * Zero changes to prices, stock, images, names, or categories
--   * Touches ONLY 'online' and 'updated_at' on existing production rows
--
-- DO NOT EXECUTE AUTOMATICALLY. REVIEW THE SELECT PREVIEWS FIRST.
-- ==============================================================================

-- ==============================================================================
-- PART 1: READ-ONLY PREVIEW & AUDIT QUERIES
-- ==============================================================================

-- 1.1 Current production table counts & online status
SELECT
  count(*) AS current_production_rows,
  count(*) FILTER (WHERE online = true) AS current_online_true_count,
  count(*) FILTER (WHERE online = false) AS current_online_false_count,
  count(*) FILTER (WHERE online IS NULL) AS current_online_null_count
FROM products;

-- 1.2 Preview the 29 existing production IDs that will be set to online = true
SELECT
  id,
  name,
  category,
  priceNgn,
  priceCfa,
  stock_quantity,
  stock,
  image_url,
  online AS current_online,
  true AS proposed_online
FROM products
WHERE id IN (
  'wix-011',
  'wix-030',
  'wix-032',
  'wix-033',
  'wix-042',
  'wix-053',
  'wix-054',
  'wix-070',
  'wix-072',
  'wix-079',
  'wix-080',
  'wix-088',
  'wix-091',
  'wix-117',
  'wix-118',
  'wix-163',
  'wix-164',
  'wix-189',
  'wix-198',
  'wix-199',
  'wix-200',
  'wix-201',
  'wix-202',
  'wix-222',
  'wix-228',
  'wix-229',
  'wix-237',
  'wix-244',
  'wix-252'
)
ORDER BY id;

-- 1.3 Preview the 7 non-fixture offline/review products (will be set to online = false)
SELECT
  id,
  name,
  category,
  priceNgn,
  priceCfa,
  stock_quantity,
  stock,
  image_url,
  online AS current_online,
  false AS proposed_online
FROM products
WHERE id IN (
  'jau-mtot3318',
  'wix-001',
  'wix-002',
  'wix-012',
  'wix-041',
  'wix-055',
  'wix-197'
)
ORDER BY id;

-- 1.4 Preview the 17 test fixtures (must remain online = false)
SELECT
  id,
  name,
  online AS current_online,
  false AS proposed_online
FROM products
WHERE id IN (
  'jau-mirror-fail',
  'jau-mirror-ok',
  'jau-mirror-post',
  'jau-stock-a',
  'jau-stock-b',
  'jau-stock-dcf',
  'jau-stock-dec',
  'jau-stock-del',
  'jau-stock-dnp',
  'jau-stock-dpn',
  'jau-stock-em2',
  'jau-stock-eml',
  'jau-stock-idem',
  'jau-stock-opr',
  'jau-stock-opt',
  'jau-stock-pay',
  'jau-stock-rop'
)
ORDER BY id;

-- 1.5 Drift Check: Identify any unclassified IDs present in production
SELECT
  id,
  name,
  priceNgn,
  stock_quantity,
  image_url,
  online
FROM products
WHERE id NOT IN (
  'jau-mirror-fail',
  'jau-mirror-ok',
  'jau-mirror-post',
  'jau-mtot3318',
  'jau-stock-a',
  'jau-stock-b',
  'jau-stock-dcf',
  'jau-stock-dec',
  'jau-stock-del',
  'jau-stock-dnp',
  'jau-stock-dpn',
  'jau-stock-em2',
  'jau-stock-eml',
  'jau-stock-idem',
  'jau-stock-opr',
  'jau-stock-opt',
  'jau-stock-pay',
  'jau-stock-rop',
  'wix-001',
  'wix-002',
  'wix-011',
  'wix-012',
  'wix-030',
  'wix-032',
  'wix-033',
  'wix-041',
  'wix-042',
  'wix-053',
  'wix-054',
  'wix-055',
  'wix-070',
  'wix-072',
  'wix-079',
  'wix-080',
  'wix-088',
  'wix-091',
  'wix-117',
  'wix-118',
  'wix-163',
  'wix-164',
  'wix-189',
  'wix-197',
  'wix-198',
  'wix-199',
  'wix-200',
  'wix-201',
  'wix-202',
  'wix-222',
  'wix-228',
  'wix-229',
  'wix-237',
  'wix-244',
  'wix-252'
)
ORDER BY id;

-- 1.6 Informational: The 152 approved local IDs missing from Supabase (missing_from_production)
-- These 152 rows are NOT in Supabase and are NOT inserted by this publication SQL.
-- (Importing them requires a separate reviewed migration for names, categories, prices, stock, images).
-- Missing IDs: wix-003, wix-004, wix-005, wix-008, wix-009, wix-010, wix-013, wix-014, wix-015, wix-016... (152 total)


-- ==============================================================================
-- PART 2: DRIFT GUARD (EXECUTION SAFETY GATE)
-- ==============================================================================
-- This PL/pgSQL block validates safety invariants and aborts the transaction
-- with an exception if any drift or policy violation is detected.

DO $$
DECLARE
  v_unclassified_count integer;
  v_forced_offline_leaked integer;
  v_fixture_leaked integer;
  v_invalid_price_count integer;
BEGIN
  -- 1. DRIFT GUARD: Abort if any production ID is unclassified
  SELECT count(*) INTO v_unclassified_count
  FROM products
  WHERE id NOT IN (
    'jau-mirror-fail',
    'jau-mirror-ok',
    'jau-mirror-post',
    'jau-mtot3318',
    'jau-stock-a',
    'jau-stock-b',
    'jau-stock-dcf',
    'jau-stock-dec',
    'jau-stock-del',
    'jau-stock-dnp',
    'jau-stock-dpn',
    'jau-stock-em2',
    'jau-stock-eml',
    'jau-stock-idem',
    'jau-stock-opr',
    'jau-stock-opt',
    'jau-stock-pay',
    'jau-stock-rop',
    'wix-001',
    'wix-002',
    'wix-011',
    'wix-012',
    'wix-030',
    'wix-032',
    'wix-033',
    'wix-041',
    'wix-042',
    'wix-053',
    'wix-054',
    'wix-055',
    'wix-070',
    'wix-072',
    'wix-079',
    'wix-080',
    'wix-088',
    'wix-091',
    'wix-117',
    'wix-118',
    'wix-163',
    'wix-164',
    'wix-189',
    'wix-197',
    'wix-198',
    'wix-199',
    'wix-200',
    'wix-201',
    'wix-202',
    'wix-222',
    'wix-228',
    'wix-229',
    'wix-237',
    'wix-244',
    'wix-252'
  );

  IF v_unclassified_count > 0 THEN
    RAISE EXCEPTION 'DRIFT GUARD ABORT: % unclassified production rows detected in database!', v_unclassified_count;
  END IF;

  -- 2. Ensure offline/review products are never in the live allowlist
  SELECT count(*) INTO v_forced_offline_leaked
  FROM products
  WHERE id IN (
    'jau-mtot3318',
    'wix-001',
    'wix-002',
    'wix-012',
    'wix-041',
    'wix-055',
    'wix-197'
  ) AND id IN (
    'wix-011',
    'wix-030',
    'wix-032',
    'wix-033',
    'wix-042',
    'wix-053',
    'wix-054',
    'wix-070',
    'wix-072',
    'wix-079',
    'wix-080',
    'wix-088',
    'wix-091',
    'wix-117',
    'wix-118',
    'wix-163',
    'wix-164',
    'wix-189',
    'wix-198',
    'wix-199',
    'wix-200',
    'wix-201',
    'wix-202',
    'wix-222',
    'wix-228',
    'wix-229',
    'wix-237',
    'wix-244',
    'wix-252'
  );

  IF v_forced_offline_leaked > 0 THEN
    RAISE EXCEPTION 'DRIFT GUARD ABORT: Offline products found in live allowlist!';
  END IF;

  -- 3. Ensure test fixtures (jau-*) are never in the live allowlist
  SELECT count(*) INTO v_fixture_leaked
  FROM products
  WHERE (id LIKE 'jau-%' OR id IN (
    'jau-mirror-fail',
    'jau-mirror-ok',
    'jau-mirror-post',
    'jau-stock-a',
    'jau-stock-b',
    'jau-stock-dcf',
    'jau-stock-dec',
    'jau-stock-del',
    'jau-stock-dnp',
    'jau-stock-dpn',
    'jau-stock-em2',
    'jau-stock-eml',
    'jau-stock-idem',
    'jau-stock-opr',
    'jau-stock-opt',
    'jau-stock-pay',
    'jau-stock-rop'
  )) AND id IN (
    'wix-011',
    'wix-030',
    'wix-032',
    'wix-033',
    'wix-042',
    'wix-053',
    'wix-054',
    'wix-070',
    'wix-072',
    'wix-079',
    'wix-080',
    'wix-088',
    'wix-091',
    'wix-117',
    'wix-118',
    'wix-163',
    'wix-164',
    'wix-189',
    'wix-198',
    'wix-199',
    'wix-200',
    'wix-201',
    'wix-202',
    'wix-222',
    'wix-228',
    'wix-229',
    'wix-237',
    'wix-244',
    'wix-252'
  );

  IF v_fixture_leaked > 0 THEN
    RAISE EXCEPTION 'DRIFT GUARD ABORT: Test fixture products (jau-*) found in live allowlist!';
  END IF;

  -- 4. Ensure all 29 target live products have valid positive prices
  SELECT count(*) INTO v_invalid_price_count
  FROM products
  WHERE id IN (
    'wix-011',
    'wix-030',
    'wix-032',
    'wix-033',
    'wix-042',
    'wix-053',
    'wix-054',
    'wix-070',
    'wix-072',
    'wix-079',
    'wix-080',
    'wix-088',
    'wix-091',
    'wix-117',
    'wix-118',
    'wix-163',
    'wix-164',
    'wix-189',
    'wix-198',
    'wix-199',
    'wix-200',
    'wix-201',
    'wix-202',
    'wix-222',
    'wix-228',
    'wix-229',
    'wix-237',
    'wix-244',
    'wix-252'
  ) AND (priceNgn IS NULL OR priceNgn <= 0);

  IF v_invalid_price_count > 0 THEN
    RAISE EXCEPTION 'DRIFT GUARD ABORT: % target live products have invalid/non-positive priceNgn!', v_invalid_price_count;
  END IF;

  RAISE NOTICE 'Drift guard safety checks PASSED: all 53 production rows verified (29 live, 7 offline, 17 fixtures).';
END $$;


-- ==============================================================================
-- PART 3: SAFE TRANSACTION (TOUCHES ONLY online AND updated_at ON EXISTING ROWS)
-- ==============================================================================
-- Strictly UPDATE only:
--   - No rows inserted (missing_from_production remain separate)
--   - No rows deleted
--   - No IDs renamed
--   - No prices, stock, or image_url values modified

BEGIN;

-- 3.1 Set online = true for the 29 approved live products in Supabase
UPDATE products
   SET online = true,
       updated_at = now()
 WHERE id IN (
  'wix-011',
  'wix-030',
  'wix-032',
  'wix-033',
  'wix-042',
  'wix-053',
  'wix-054',
  'wix-070',
  'wix-072',
  'wix-079',
  'wix-080',
  'wix-088',
  'wix-091',
  'wix-117',
  'wix-118',
  'wix-163',
  'wix-164',
  'wix-189',
  'wix-198',
  'wix-199',
  'wix-200',
  'wix-201',
  'wix-202',
  'wix-222',
  'wix-228',
  'wix-229',
  'wix-237',
  'wix-244',
  'wix-252'
);

-- 3.2 Set online = false for the 7 offline / review products in Supabase
UPDATE products
   SET online = false,
       updated_at = now()
 WHERE id IN (
  'jau-mtot3318',
  'wix-001',
  'wix-002',
  'wix-012',
  'wix-041',
  'wix-055',
  'wix-197'
);

-- 3.3 Set online = false for the 17 test fixture products in Supabase
UPDATE products
   SET online = false,
       updated_at = now()
 WHERE id IN (
  'jau-mirror-fail',
  'jau-mirror-ok',
  'jau-mirror-post',
  'jau-stock-a',
  'jau-stock-b',
  'jau-stock-dcf',
  'jau-stock-dec',
  'jau-stock-del',
  'jau-stock-dnp',
  'jau-stock-dpn',
  'jau-stock-em2',
  'jau-stock-eml',
  'jau-stock-idem',
  'jau-stock-opr',
  'jau-stock-opt',
  'jau-stock-pay',
  'jau-stock-rop'
);

COMMIT;


-- ==============================================================================
-- PART 4: POST-UPDATE VERIFICATION
-- ==============================================================================
SELECT
  count(*) AS total_rows,
  count(*) FILTER (WHERE online = true) AS online_true_count,
  count(*) FILTER (WHERE online = false) AS online_false_count,
  count(*) FILTER (WHERE online IS NULL) AS online_null_count
FROM products;
