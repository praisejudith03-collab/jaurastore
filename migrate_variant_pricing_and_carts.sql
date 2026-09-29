-- Migration: variant pricing persistence + abandoned-cart safeguard
-- ------------------------------------------------------------------
-- Apply in the Supabase SQL editor (or psql) against the production
-- database. Every statement is idempotent and safe to re-run.
--
-- Why this exists
--   1. Per-option price overrides ("optionPrices") and their new
--      strike-through "was" prices ("optionCompareAt") are stored as JSONB
--      columns on the products table. If those columns are missing, the
--      resilient product upsert silently DROPS them on every save, so the
--      overrides "revert to defaults" after a catalog sync. Adding the
--      columns locks the overrides into the database permanently.
--   2. The background abandoned-cart worker reads the abandoned_carts table.
--      A missing table answers PGRST205 ("table not found"); the worker now
--      degrades gracefully, but creating the table restores the feature.

-- 1) Variant pricing columns (JSONB, nullable) -----------------------------
alter table if exists products
  add column if not exists "optionPrices"    jsonb;
alter table if exists products
  add column if not exists "optionCompareAt" jsonb;
-- Free-text physical dimensions shown on the product page / WhatsApp caption.
alter table if exists products
  add column if not exists dimensions       text;
-- Per-option supplier links (component -> Splendall URL) so the automated
-- stock sync mirrors availability per variant option, not per product.
alter table if exists products
  add column if not exists "optionSupplierSku" jsonb;
-- Kept alongside for completeness (older tables sometimes lack these too):
alter table if exists products
  add column if not exists "optionStock"     jsonb;
alter table if exists products
  add column if not exists "compareNgn"      numeric;
alter table if exists products
  add column if not exists "compareCfa"      numeric;

-- 1b) Checkout storage decoupling: fallback status on the order row ---------
-- Set true when a payment proof was provided but Storage was full/failed. The
-- order still completes; the flag also rides inside the order payload JSON, so
-- this column is optional and the order write drops it gracefully if absent.
alter table if exists orders
  add column if not exists proof_upload_failed boolean default false;

-- 2) Abandoned-cart table (matches supabase_schema.sql) ---------------------
create table if not exists abandoned_carts (
  token             text primary key,
  email             text not null,
  customer_name     text,
  items             jsonb,
  currency          text,
  total             numeric,
  last_activity_at  text,
  reminder_sent     boolean default false,
  reminder_sent_at  text,
  converted_at      text,
  created_at        text,
  updated_at        text
);
create index if not exists idx_abandoned_due
  on abandoned_carts (reminder_sent, last_activity_at);
create index if not exists idx_abandoned_email
  on abandoned_carts (email);

-- Reload PostgREST's schema cache so the new columns/table are visible
-- immediately (otherwise the first request may still answer PGRST20x).
notify pgrst, 'reload schema';
