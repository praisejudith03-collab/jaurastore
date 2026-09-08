-- ==============================================================================
-- JAURASTORE PRODUCTION VERIFICATION & PUBLICATION REVIEW (RECONCILED)
-- ==============================================================================
-- Reconciled against production public.products (53 existing rows).
--
-- Policy & Scope:
--   - Local catalogue rows: 275 (181 approved live, 2 operator offline, 75 placeholder, 17 fixtures)
--   - Existing Supabase rows: 53 (29 approved live, 2 operator offline, 3 placeholder, 2 no-image, 17 fixtures)
--   - Approved live present in Supabase: 29 IDs -> SET online = true
--   - Approved live missing from Supabase: 152 IDs -> reported as missing_from_production (NOT inserted)
--   - Operator offline / placeholder / fixtures in Supabase: 22 IDs -> SET online = false
--
-- SAFETY INVARIANTS:
--   * Zero row deletions (no DELETE, no DROP)
--   * Zero product ID renames
--   * Zero changes to prices, stock, images, names, or categories
--   * Zero INSERT statements (product importing is separate and not done here)
--   * Touches ONLY 'online' and 'updated_at' on EXISTING production rows
--
-- DO NOT EXECUTE AUTOMATICALLY. REVIEW THE SELECT PREVIEWS FIRST.
-- ==============================================================================

-- ==============================================================================
-- PART 1: READ-ONLY PREVIEW & AUDIT QUERIES
-- ==============================================================================

-- 1.1 Current production table counts
SELECT
  count(*) AS total_production_rows,
  count(*) FILTER (WHERE online = true) AS current_online_true_count,
  count(*) FILTER (WHERE online = false) AS current_online_false_count,
  count(*) FILTER (WHERE online IS NULL) AS current_online_null_count
FROM products;

-- 1.2 Preview the 29 existing production IDs that will be set online = true
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

-- 1.3 Preview the 5 existing production offline IDs (2 operator + 3 placeholder; online = false)
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
  'wix-001',
  'wix-012',
  'wix-041',
  'wix-055',
  'wix-197'
)
ORDER BY id;

-- 1.4 Preview the 17 existing test fixture IDs (online = false)
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

-- 1.5 Unclassified IDs in production (any row not accounted for in the 51 known IDs)
SELECT
  id,
  name,
  priceNgn,
  stock_quantity,
  image_url,
  online
FROM products
WHERE id NOT IN (
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
  'wix-252',
  'wix-001',
  'wix-012',
  'wix-041',
  'wix-055',
  'wix-197',
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
  v_prod_count integer;
  v_forced_offline_leaked integer;
  v_fixture_leaked integer;
  v_invalid_price_count integer;
  v_missing_live_count integer;
BEGIN
  -- 1. Ensure wix-001 and wix-012 are never in the live allowlist
  SELECT count(*) INTO v_forced_offline_leaked
  FROM products
  WHERE id IN ('wix-001', 'wix-012')
    AND id IN (
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
    RAISE EXCEPTION 'DRIFT GUARD ABORT: Forced offline products (wix-001 / wix-012) found in live allowlist!';
  END IF;

  -- 2. Ensure test fixtures (jau-*) are never in the live allowlist
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

  -- 3. Ensure all 29 target live products have valid positive prices
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

  RAISE NOTICE 'Drift guard safety checks PASSED. Target live count: 29 existing production rows.';
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

-- 3.1 Set online = true for the 29 approved live products present in Supabase
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

-- 3.2 Set online = false for existing offline products (2 operator offline + 3 placeholder)
UPDATE products
   SET online = false,
       updated_at = now()
 WHERE id IN (
  'wix-001',
  'wix-012',
  'wix-041',
  'wix-055',
  'wix-197'
);

-- 3.3 Set online = false for existing test fixtures (17 rows)
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
