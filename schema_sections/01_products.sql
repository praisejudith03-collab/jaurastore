-- Jaura Store — Supabase schema. Run once in the Supabase SQL editor.
-- Idempotent: re-running is safe. PostgreSQL is the production source of
-- truth; SQLite on Render is only a boot cache restored from these tables.
-- Required env vars: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.

-- SECTION: products
-- ------------------------------------------------------------ products
-- Canonical columns are the source of truth (priceNgn, priceCfa, image_url,
-- stock_quantity, ...); legacy camelCase aliases (image, stock, ...) are kept
-- and production writes both. camelCase MUST be quoted or Postgres folds it to
-- lowercase (PGRST204).
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
  "optionPrices"   jsonb,
  "optionCompareAt" jsonb,
  "optionSupplierSku" jsonb,
  "optionSku"      jsonb,
  reviews          jsonb,
  "enableCustomNote" boolean not null default false,
  "customNotePrompt" text not null default '',
  dimensions       text,
  "bulkQty"        integer,
  "bulkPercent"    integer,
  "placeholderImage" text,
  "usesPlaceholder"  boolean default false,
  source           text default 'admin',
  "supplierId" text not null default '',
  "supplierSku" text not null default '',
  updated_at       timestamptz default now(),
  constraint products_price_positive check ("priceCfa" >= 0 and "priceNgn" >= 0
    and "compareCfa" is null or "compareCfa" >= 0
    and "compareNgn" is null or "compareNgn" >= 0),
  constraint products_stock_nonnegative check (stock_quantity >= 0
    and stock is null or stock >= 0)
);

-- Repair an EXISTING hand-built table: add-only, never drops data. Covers
-- every column in supabase_store._CRITICAL_PRODUCT_COLUMNS (a product cannot
-- be sold without them); "id" is the primary key and cannot be added.
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
alter table products add column if not exists "optionPrices"     jsonb;
alter table products add column if not exists "optionCompareAt"  jsonb;
alter table products add column if not exists "optionSupplierSku" jsonb;
alter table products add column if not exists "optionSku"         jsonb;
alter table products add column if not exists reviews             jsonb;
alter table products add column if not exists "enableCustomNote" boolean not null default false;
alter table products add column if not exists "customNotePrompt" text not null default '';
alter table products add column if not exists dimensions         text;
alter table products add column if not exists "bulkQty"      integer;
alter table products add column if not exists "bulkPercent"  integer;
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
alter table products add column if not exists "supplierId" text not null default '';
alter table products add column if not exists "supplierSku" text not null default '';

-- Dead leftovers (price_cfa, price_ngn, name_fr, compare_cfa, compare_ngn,
-- option_stock) are harmless legacy junk. Never drop them and never rename
-- them - the app uses camelCase only; live production rows still exist.

