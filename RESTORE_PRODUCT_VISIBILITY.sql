-- Catalog recovery operation requested for the production Supabase project.
-- Review before running: this intentionally reactivates EVERY soft-deleted or
-- inactive product, including rows that may have been disabled/deleted on
-- purpose. Run in the Supabase SQL Editor only after confirming that is desired.
BEGIN;
UPDATE public.products
SET is_active = true,
    is_deleted = false;
COMMIT;

-- Verify the result and review any deliberately excluded rows afterwards:
-- SELECT is_active, is_deleted, count(*) FROM public.products
-- GROUP BY is_active, is_deleted ORDER BY is_active, is_deleted;
