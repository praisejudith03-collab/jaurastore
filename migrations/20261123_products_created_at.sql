-- Safe, additive migration: adds public.products.created_at, the column the
-- Admin Products desk's "Newest to Oldest" / "Oldest to Newest" sort reads.
--
-- Why a new column at all?
--   * updated_at is rewritten by EVERY save (price edit, stock restock, hide),
--     so ordering by it answers "what did I touch last?", not "what did I add
--     last?" - an item restocked today would jump above a piece added
--     yesterday.
--   * created_at is written ONCE, by the database default, when the row is
--     first inserted. The app never sends it back on a save
--     (supabase_store.upsert_products strips it), so it cannot drift.
--
-- * Idempotent: "if not exists" makes a re-run a no-op.
-- * No row is UPDATEd here: older rows keep NULL, and the Products desk sorts
--   those by updated_at instead. That is why this migration does not touch the
--   catalogue write-lock in recover_unavailable_products.sql.
-- * schema_sections/01_products.sql, supabase_schema.sql and auto_migrate.py
--   carry the same column, so whichever runs first the others do nothing.
begin;

alter table public.products add column if not exists created_at timestamptz;
alter table public.products alter column created_at set default now();

commit;

-- PostgREST caches the schema; without this the Admin page would not see the
-- new column until the next reload of the project's schema cache.
notify pgrst, 'reload schema';
