-- =====================================================================
-- Jaura Store — Supabase (PostgreSQL) schema
-- =====================================================================
-- Run this once in the Supabase SQL editor (Dashboard → SQL → New query).
-- Every statement is idempotent (IF NOT EXISTS), so re-running is safe.
--
-- Supabase PostgreSQL is the production source of truth for products,
-- orders, receipts, categories, site settings and referral commission
-- settings. SQLite on the Render disk is only a
-- boot-time cache the app restores FROM these tables; production writes
-- go to PostgreSQL first and failures are surfaced, never swallowed.
--
-- Required environment variables (both Render services):
--   SUPABASE_URL                 https://<project>.supabase.co
--   SUPABASE_SERVICE_ROLE_KEY    the service-role key (server-side only)
-- =====================================================================

-- SECTION: products
-- ------------------------------------------------------------ products
-- Canonical columns (source of truth): id, name, category, priceNgn,
-- priceCfa, compareNgn, compareCfa, image_url, images, stock_quantity,
-- description, descriptionFr, featured, online, updated_at. The legacy camelCase
-- columns below (image, stock, ...) are kept as compatibility aliases for
-- the same-origin test/dev path and older rows; production writes both.
-- camelCase keys MUST be quoted: Postgres folds unquoted identifiers to
-- lowercase, so an unquoted priceCfa would create a pricecfa column and
-- every write would still fail with PGRST204.
create table if not exists products (
  id               text primary key,
  "legacyId"       text,
  sku              text,
  slug             text,
  name             text not null,
  "nameFr"         text,
  "descriptionFr"  text,
  category         text,
  "priceCfa"       numeric,
  "compareCfa"     numeric,
  "priceNgn"       numeric,
  "compareNgn"     numeric,
  image            text,
  image_url        text,
  images           jsonb,
  description      text,
  stock            integer default 0,
  stock_quantity   integer not null default 0,
  badge            text,
  featured         boolean default false,
  online           boolean default true,
  colors           jsonb,
  options          jsonb,
  "optionStock"    jsonb,
  "placeholderImage" text,
  "usesPlaceholder"  boolean default false,
  source           text default 'admin',
  updated_at       timestamptz default now(),
  constraint products_price_positive check ("priceCfa" >= 0 and "priceNgn" >= 0
    and "compareCfa" is null or "compareCfa" >= 0
    and "compareNgn" is null or "compareNgn" >= 0),
  constraint products_stock_nonnegative check (stock_quantity >= 0
    and stock is null or stock >= 0)
);

-- Repair an EXISTING products table hand-built narrower than the row the app
-- writes. Add-only: never drops or rewrites data. Run it if the Render log
-- says "[supabase] products upsert: stored without columns [...]" or
-- "products upsert failed". It covers every column in
-- supabase_store._CRITICAL_PRODUCT_COLUMNS - a product cannot be sold without
-- them. ("id" is the primary key and cannot be added to an existing table.)
alter table products add column if not exists name               text;
alter table products add column if not exists "nameFr"           text;
alter table products add column if not exists "descriptionFr"    text;
alter table products add column if not exists "priceCfa"         numeric;
alter table products add column if not exists "compareCfa"       numeric;
alter table products add column if not exists "priceNgn"         numeric;
alter table products add column if not exists "compareNgn"       numeric;
alter table products add column if not exists stock              integer default 0;
alter table products add column if not exists images             jsonb;
alter table products add column if not exists "optionStock"      jsonb;
alter table products add column if not exists "placeholderImage" text;
alter table products add column if not exists "usesPlaceholder"  boolean default false;
alter table products add column if not exists badge              text;
alter table products add column if not exists featured           boolean default false;
alter table products add column if not exists online             boolean default true;
alter table products add column if not exists colors             jsonb;
alter table products add column if not exists options            jsonb;
alter table products add column if not exists source             text default 'admin';
alter table products add column if not exists updated_at         timestamptz default now();
-- Additional repair for any older table missing the remaining non-key columns.
-- These are add-only and nullable for legacy rows so no invented values are
-- written and every existing row keeps its id, name, prices and stock.
alter table products add column if not exists "legacyId"       text;
alter table products add column if not exists sku              text;
alter table products add column if not exists slug             text;
alter table products add column if not exists category         text;
alter table products add column if not exists image            text;
alter table products add column if not exists image_url        text;
alter table products add column if not exists description      text;
alter table products add column if not exists stock_quantity   integer not null default 0;

-- Dead leftovers from the original hand-built table: price_cfa, price_ngn,
-- name_fr, compare_cfa, compare_ngn, option_stock. The app reads and writes
-- the camelCase columns only, so these are never used. NEVER drop them and
-- never rename them - they are harmless legacy junk, but live rows still
-- exist in the production table.

