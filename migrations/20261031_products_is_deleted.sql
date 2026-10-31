-- Safe, additive migration: adds the Trash Bin flag public.products.is_deleted.
-- Run in the Supabase SQL editor with "Run without RLS" after review.
--
-- * Idempotent: "if not exists" makes a re-run a no-op.
-- * Column only. No row is updated, so the write-lock in
--   recover_unavailable_products.sql is not involved.
-- * Existing rows read as not deleted (the default).
-- * The app never writes NULL here. catalog.is_deleted_product() reads only
--   "is_deleted = true" as a tombstone, so a NULL is treated as not deleted.
--   schema_sections/01_products.sql and auto_migrate.py add the same column
--   as NOT NULL; if either has run first, this statement does nothing.
begin;

ALTER TABLE public.products ADD COLUMN IF NOT EXISTS is_deleted boolean DEFAULT false;

commit;
