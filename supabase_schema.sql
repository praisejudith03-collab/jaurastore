-- =====================================================================
-- Jaura Store — Supabase (PostgreSQL) schema
-- =====================================================================
-- Run this once in the Supabase SQL editor (Dashboard → SQL → New query).
-- Every statement is idempotent (IF NOT EXISTS), so re-running is safe.
--
-- Supabase PostgreSQL is the production source of truth for products,
-- orders, receipts, categories, site settings, referral commission
-- settings and admin reset tokens. SQLite on the Render disk is only a
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
-- description, featured, online, updated_at. The legacy camelCase
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
-- writes. Only adds columns; never drops or rewrites data. Run it if the
-- Render log says "[supabase] products upsert: stored without columns [...]"
-- or "products upsert failed". It covers every column in
-- supabase_store._CRITICAL_PRODUCT_COLUMNS (a product cannot be sold without
-- them) - "priceCfa" and "priceNgn" were missing from this block while the
-- create-table above already had them, which is what left the production
-- table unable to hold the two price columns the app writes on every save.
-- ("id" is the primary key and cannot be added to an existing table.)
alter table products add column if not exists name               text;
alter table products add column if not exists "nameFr"           text;
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

-- SECTION: orders
-- -------------------------------------------------------------- orders
create table if not exists orders (
  id            text primary key,
  email         text,
  customer_name text,
  phone         text,
  country       text,
  city          text,
  zone          text,
  address       text,
  note          text,
  payment       text,
  proof_url     text,
  items_count   integer default 0,
  total         numeric,
  currency      text,
  source        text default 'web',
  status        text default 'pending',
  payload       jsonb,
  at            timestamptz,
  updated_at    timestamptz default now()
);
-- Repair an older orders table that may be narrower. Add-only, idempotent,
-- preserves every existing order row, ids and values. Columns are added
-- nullable (or with a harmless default) so legacy rows are not given invented
-- data, and they are added before the indexes that reference them.
alter table orders add column if not exists email         text;
alter table orders add column if not exists customer_name text;
alter table orders add column if not exists phone         text;
alter table orders add column if not exists country       text;
alter table orders add column if not exists city          text;
alter table orders add column if not exists zone          text;
alter table orders add column if not exists address       text;
alter table orders add column if not exists note          text;
alter table orders add column if not exists payment       text;
alter table orders add column if not exists proof_url     text;
alter table orders add column if not exists items_count   integer default 0;
alter table orders add column if not exists total         numeric;
alter table orders add column if not exists currency      text;
alter table orders add column if not exists source        text default 'web';
alter table orders add column if not exists status        text default 'pending';
alter table orders add column if not exists payload       jsonb;
alter table orders add column if not exists at            timestamptz;
alter table orders add column if not exists updated_at    timestamptz default now();
create index if not exists idx_orders_at on orders (at desc);
create index if not exists idx_orders_status on orders (status);

-- SECTION: receipts
-- ------------------------------------------------------------ receipts
create table if not exists receipts (
  id         text primary key,
  order_id   text,
  name       text,
  phone      text,
  email      text,
  method     text,
  items      text,
  quantity   text,
  amount     text,
  note       text,
  file_url   text,
  file_name  text,
  file_size  bigint,
  file_type  text,
  emailed    boolean default false,
  email_info text,
  created_at timestamptz default now()
);
-- Repair an older receipts table. Add-only, preserves all existing receipt
-- rows. Added before the index that uses order_id.
alter table receipts add column if not exists order_id   text;
alter table receipts add column if not exists name       text;
alter table receipts add column if not exists phone      text;
alter table receipts add column if not exists email      text;
alter table receipts add column if not exists method     text;
alter table receipts add column if not exists items      text;
alter table receipts add column if not exists quantity   text;
alter table receipts add column if not exists amount     text;
alter table receipts add column if not exists note       text;
alter table receipts add column if not exists file_url   text;
alter table receipts add column if not exists file_name  text;
alter table receipts add column if not exists file_size  bigint;
alter table receipts add column if not exists file_type  text;
alter table receipts add column if not exists emailed    boolean default false;
alter table receipts add column if not exists email_info text;
alter table receipts add column if not exists created_at timestamptz default now();
create index if not exists idx_receipts_order on receipts (order_id);

-- SECTION: referrals
-- ------------------------------------------------- referral programme
create table if not exists referral_codes (
  code          text primary key,
  email         text not null,
  name          text,
  uses          integer default 0,
  reward_issued integer default 0,
  reward_coupon text,
  created_at    timestamptz default now()
);
-- Repair older referral_codes tables. Add-only, preserves existing codes.
alter table referral_codes add column if not exists email         text;
alter table referral_codes add column if not exists name          text;
alter table referral_codes add column if not exists uses          integer default 0;
alter table referral_codes add column if not exists reward_issued integer default 0;
alter table referral_codes add column if not exists reward_coupon text;
alter table referral_codes add column if not exists created_at    timestamptz default now();
create index if not exists idx_referral_email on referral_codes (email);

-- one row per successful purchase made with a referral code
create table if not exists referral_uses (
  id          bigint generated always as identity primary key,
  code        text not null,
  order_id    text,
  buyer_email text,
  at          timestamptz default now()
);
-- Repair older referral_uses tables. Add-only, before the index.
alter table referral_uses add column if not exists code        text;
alter table referral_uses add column if not exists order_id    text;
alter table referral_uses add column if not exists buyer_email text;
alter table referral_uses add column if not exists at          timestamptz default now();
create index if not exists idx_referral_uses_code on referral_uses (code);

-- SECTION: coupons
-- ------------------------------------------------------------- coupons
create table if not exists coupons (
  code       text primary key,
  percent    integer not null,
  kind       text default 'manual',       -- manual | reward
  email      text,
  note       text,
  active     integer default 1,
  max_uses   integer,
  uses       integer default 0,
  expires_at text,
  created_at timestamptz default now()
);
-- Repair older coupons tables. Add-only, idempotent, preserves existing rows.
alter table coupons add column if not exists percent    integer;
alter table coupons add column if not exists kind       text default 'manual';
alter table coupons add column if not exists email      text;
alter table coupons add column if not exists note       text;
alter table coupons add column if not exists active     integer default 1;
alter table coupons add column if not exists max_uses   integer;
alter table coupons add column if not exists uses       integer default 0;
alter table coupons add column if not exists expires_at text;
alter table coupons add column if not exists created_at timestamptz default now();

-- SECTION: growth_settings
-- ----------------------------------------------------- growth settings
-- key/value map: referralEnabled, abandonedEnabled, minSpendNgn, cfaRate,
-- buyerPercent, referrerPercent, milestone, abandonedHours,
-- abandonedSubject, abandonedTemplate
create table if not exists growth_settings (
  key   text primary key,
  value text
);
-- Repair an older growth_settings table that may be missing the value column.
alter table growth_settings add column if not exists value text;

-- SECTION: site_settings
-- ===================================================== Jaura production tables
-- These are the source of truth for runtime configuration and recovery.
create table if not exists site_settings (
  id bigint primary key check (id = 1),
  bank_name text not null default '',
  account_number text not null default '',
  account_name text not null default '',
  referral_commission_percentage numeric(5,2) not null default 0 check (referral_commission_percentage between 0 and 100),
  hero_banner_title text not null default '',
  hero_banner_subtitle text not null default '',
  contact_email text not null default '',
  contact_phone text not null default '',
  site_logo_url text not null default '',
  hero_video_url text not null default '',
  hero_poster_url text not null default '',
  hero_doc_url text not null default '',
  shop_banner_url text not null default '',
  shipping_note text not null default '',
  banner_from text not null default '',
  banner_to text not null default '',
  conv_banner text not null default '',
  conv_bold text not null default '',
  updated_at timestamptz not null default now()
);
insert into site_settings (id) values (1) on conflict (id) do nothing;
-- Repair an older site_settings table that predates newer columns. Add-only,
-- preserves the single id=1 row and all Admin-edited settings.
alter table site_settings add column if not exists bank_name text not null default '';
alter table site_settings add column if not exists account_number text not null default '';
alter table site_settings add column if not exists account_name text not null default '';
alter table site_settings add column if not exists referral_commission_percentage numeric(5,2) not null default 0;
alter table site_settings add column if not exists hero_banner_title text not null default '';
alter table site_settings add column if not exists hero_banner_subtitle text not null default '';
alter table site_settings add column if not exists contact_email text not null default '';
alter table site_settings add column if not exists contact_phone text not null default '';
alter table site_settings add column if not exists site_logo_url text not null default '';
alter table site_settings add column if not exists hero_video_url text not null default '';
alter table site_settings add column if not exists hero_poster_url text not null default '';
alter table site_settings add column if not exists hero_doc_url text not null default '';
alter table site_settings add column if not exists shop_banner_url text not null default '';
alter table site_settings add column if not exists shipping_note text not null default '';
alter table site_settings add column if not exists banner_from text not null default '';
alter table site_settings add column if not exists banner_to text not null default '';
alter table site_settings add column if not exists conv_banner text not null default '';
alter table site_settings add column if not exists conv_bold text not null default '';
alter table site_settings add column if not exists updated_at timestamptz not null default now();

-- Payment details shown at checkout. These were hardcoded in checkout.html /
-- js/app.js (bank "UBA", account 23474678931, the MoMo Benin and Moov Togo
-- numbers) which meant changing an account number needed a redeploy and the
-- values were visible in the shipped bundle. They are now columns on the
-- id=1 site_settings row, served by GET /api/site and edited from the Admin
-- Portal, so the storefront never carries a payment fallback.
alter table site_settings add column if not exists cfa_payment_provider     text not null default '';
alter table site_settings add column if not exists cfa_payment_name         text not null default '';
alter table site_settings add column if not exists cfa_payment_account      text not null default '';
alter table site_settings add column if not exists cfa_payment_instructions text not null default '';
alter table site_settings add column if not exists togo_payment_provider     text not null default '';
alter table site_settings add column if not exists togo_payment_name         text not null default '';
alter table site_settings add column if not exists togo_payment_account      text not null default '';
alter table site_settings add column if not exists togo_payment_instructions text not null default '';
alter table site_settings add column if not exists naira_payment_bank         text not null default '';
alter table site_settings add column if not exists naira_payment_name         text not null default '';
alter table site_settings add column if not exists naira_payment_account      text not null default '';
alter table site_settings add column if not exists naira_payment_instructions text not null default '';

-- SECTION: categories
create table if not exists categories (
  id text primary key,
  name text not null,
  name_fr text not null default '',
  image_url text not null default '',
  hidden boolean not null default false,
  updated_at timestamptz not null default now()
);
-- Repair an older categories table. Add-only, preserves all existing rows.
alter table categories add column if not exists name       text;
alter table categories add column if not exists name_fr    text not null default '';
alter table categories add column if not exists image_url  text not null default '';
alter table categories add column if not exists hidden     boolean not null default false;
alter table categories add column if not exists updated_at timestamptz not null default now();

-- SECTION: product_compatibility
-- Legacy id alias. A product's `id` is its primary key and is NEVER renamed
-- while orders, reviews, carts or analytics still reference it. When a row is
-- eventually given a canonical jau-* id, the previous wix-* id is copied here
-- so old product links, order lines, reviews and cart entries keep resolving
-- through catalog.product_index() / supabase_store.product_by_id(). It stays
-- NULL for rows that were created with a canonical id and have no history.
alter table products add column if not exists "legacyId" text;
create unique index if not exists products_legacy_id_key
  on products ("legacyId") where "legacyId" is not null;

alter table products add column if not exists image_url text;
alter table products add column if not exists stock_quantity integer not null default 0;
do $$ begin
  alter table products add constraint products_stock_nonnegative check (stock_quantity >= 0) not valid;
exception when duplicate_object then null;
end $$;

-- SECTION: admin_credentials
-- Admin credentials. The SQLite `admins` table lives on the Render disk, which
-- is EPHEMERAL: after a redeploy it is re-seeded with a deliberately unusable
-- random hash, which locked every admin out until someone got shell access.
-- This table is the durable copy of the password hash, so a restart restores
-- it instead of destroying it. Only a werkzeug hash is ever stored - never a
-- plaintext password, and never anything derived from SECRET_KEY.
create table if not exists admin_users (
  id            bigint generated always as identity primary key,
  email         text not null unique,
  password_hash text not null,
  role          text not null default 'admin',
  enabled       boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  last_login_at timestamptz
);
-- Repair an older admin_users table. Add-only, preserves existing admins.
alter table admin_users add column if not exists email         text;
alter table admin_users add column if not exists password_hash text;
alter table admin_users add column if not exists role          text not null default 'admin';
alter table admin_users add column if not exists enabled       boolean not null default true;
alter table admin_users add column if not exists created_at    timestamptz not null default now();
alter table admin_users add column if not exists updated_at    timestamptz not null default now();
alter table admin_users add column if not exists last_login_at timestamptz;

create table if not exists admin_reset_tokens (
  id bigint generated always as identity primary key,
  email text not null,
  purpose text not null default 'reset',
  token_hash text not null,
  expires_at timestamptz not null,
  attempts integer not null default 0 check (attempts >= 0),
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);
-- Repair an older admin_reset_tokens table. Add-only, before the index.
alter table admin_reset_tokens add column if not exists email       text;
alter table admin_reset_tokens add column if not exists purpose     text not null default 'reset';
alter table admin_reset_tokens add column if not exists token_hash  text;
alter table admin_reset_tokens add column if not exists expires_at  timestamptz;
alter table admin_reset_tokens add column if not exists attempts    integer not null default 0;
alter table admin_reset_tokens add column if not exists consumed_at timestamptz;
alter table admin_reset_tokens add column if not exists created_at  timestamptz not null default now();
create index if not exists admin_reset_tokens_lookup on admin_reset_tokens(email, purpose, created_at desc);

-- SECTION: delivery_zones
-- Delivery zones and their fare ranges. Admin-editable, served to the
-- storefront by GET /api/site so checkout no longer hardcodes the list.
-- The fare is a RANGE because transport varies with weight; the exact figure
-- is agreed with the customer after payment. `kind`:
--   delivery = a published min..max range in `currency`
--   pickup   = free collection, no fare
--   quote    = fare agreed per order, no range published
create table if not exists delivery_zones (
  id          text primary key,
  name        text not null unique,
  currency    text not null default 'CFA' check (currency in ('CFA','NGN')),
  fare_min    integer not null default 0 check (fare_min >= 0),
  fare_max    integer not null default 0 check (fare_max >= 0),
  kind        text not null default 'delivery'
                check (kind in ('delivery','pickup','quote')),
  active      boolean not null default true,
  sort_order  integer not null default 0,
  note        text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
-- REPAIR an older delivery_zones table that predates these columns. A hand-made
-- or earlier table can be missing `active` / `sort_order`, and then the index
-- and the seed below fail with "column does not exist". Every statement is
-- add-only and idempotent: existing rows keep their ids, names, fares, active
-- and sort_order values, and no row is ever rewritten, dropped or removed.
alter table delivery_zones add column if not exists name        text;
alter table delivery_zones add column if not exists currency    text not null default 'CFA';
alter table delivery_zones add column if not exists fare_min    integer not null default 0;
alter table delivery_zones add column if not exists fare_max    integer not null default 0;
alter table delivery_zones add column if not exists kind        text not null default 'delivery';
alter table delivery_zones add column if not exists active      boolean not null default true;
alter table delivery_zones add column if not exists sort_order  integer not null default 0;
alter table delivery_zones add column if not exists note        text;
alter table delivery_zones add column if not exists created_at  timestamptz not null default now();
alter table delivery_zones add column if not exists updated_at  timestamptz not null default now();
-- `name` is added nullable on purpose: a legacy row must keep whatever name it
-- already has, and a row without one is left for a human to name rather than
-- being given an invented value here.
create unique index if not exists delivery_zones_name
  on delivery_zones(name) where name is not null;
create index if not exists delivery_zones_active on delivery_zones(active, sort_order);

-- SECTION: coupon_redemptions
-- Coupon redemption log. `coupons.uses` is a counter and stays the fast path
-- for the max_uses check, but a counter cannot answer "which order used this
-- code" and cannot stop a retried order from counting twice. The unique pair
-- (code, order_id) makes a redemption idempotent, so a confirm/retry loop
-- cannot inflate the usage count.
create table if not exists coupon_uses (
  id        bigint generated always as identity primary key,
  code      text not null,
  email     text,
  order_id  text not null,
  percent   integer,
  used_at   timestamptz not null default now(),
  unique (code, order_id)
);
-- REPAIR an older coupon_uses table that predates required columns. An
-- existing table hand-built with fewer columns would otherwise fail on
-- inserts and on the indexes below with "column does not exist". Every
-- statement is add-only and idempotent: existing rows keep their ids and
-- values, no row is rewritten, dropped or given an invented coupon code or
-- order id. code and order_id are added nullable so legacy rows without them
-- are preserved and can be left for human review rather than being given
-- fake values here.
alter table coupon_uses add column if not exists code     text;
alter table coupon_uses add column if not exists email    text;
alter table coupon_uses add column if not exists order_id text;
alter table coupon_uses add column if not exists percent  integer;
alter table coupon_uses add column if not exists used_at  timestamptz default now();
-- Preserve idempotency for any table that already exists without the unique
-- pair. The constraint in the create-table above only applies to fresh tables;
-- an older table needs this index added separately. It is a unique index on
-- (code, order_id) so a retry cannot insert the same order twice.
create unique index if not exists coupon_uses_code_order on coupon_uses(code, order_id);
create index if not exists coupon_uses_code on coupon_uses(code, used_at desc);

-- SECTION: product_reviews
-- Product reviews. Mirrors the SQLite product_reviews table one-for-one so
-- the same shape can be read from either side. unique(product_id, email) is
-- what enforces one review per customer per product - the same rule the
-- purchase-verified check relies on.
-- Product reviews. unique(product_id, email) is what enforces one review per
-- customer per product - the same rule the purchase-verified check relies on.
--
-- Column names are the agreed contract: rating / title / body / created_at /
-- updated_at. An earlier draft shipped this table as stars / note / at; the
-- guarded renames below move any database that already has it, preserving
-- every row. A column rename rewrites no data and drops no row.
create table if not exists product_reviews (
  id          bigint generated always as identity primary key,
  product_id  text not null,
  order_id    text,
  email       text not null,
  name        text,
  rating      integer not null default 5 check (rating between 1 and 5),
  title       text,
  body        text,
  hidden      boolean not null default false,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (product_id, email)
);

-- Idempotent upgrade path for a database created from the earlier draft.
-- Each step is guarded so re-running the file changes nothing.
do $$
begin
  if exists (select 1 from information_schema.columns
             where table_name = 'product_reviews' and column_name = 'stars') then
    alter table product_reviews rename column stars to rating;
  end if;
  if exists (select 1 from information_schema.columns
             where table_name = 'product_reviews' and column_name = 'note') then
    alter table product_reviews rename column note to body;
  end if;
  if exists (select 1 from information_schema.columns
             where table_name = 'product_reviews' and column_name = 'at') then
    alter table product_reviews rename column at to created_at;
  end if;
end $$;

-- Repair an older product_reviews table that is missing columns added after
-- the first release. Add-only, preserves every existing review, ids, product
-- ids, emails, names, ratings and text. Added before indexes and the unique
-- rule that depends on these columns. Each column is added nullable (or with
-- a harmless default) so legacy rows are not given invented data.
do $$
begin
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'product_id') then
    alter table product_reviews add column product_id text;
  end if;
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'order_id') then
    alter table product_reviews add column order_id text;
  end if;
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'email') then
    alter table product_reviews add column email text;
  end if;
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'name') then
    alter table product_reviews add column name text;
  end if;
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'rating') then
    alter table product_reviews add column rating integer not null default 5;
  end if;
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'body') then
    alter table product_reviews add column body text;
  end if;
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'title') then
    alter table product_reviews add column title text;
  end if;
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'hidden') then
    alter table product_reviews add column hidden boolean not null default false;
  end if;
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'created_at') then
    alter table product_reviews
      add column created_at timestamptz not null default now();
  end if;
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'updated_at') then
    alter table product_reviews
      add column updated_at timestamptz not null default now();
  end if;
end $$;

-- Ensure the one-review-per-product/email rule survives for older tables
-- that were created without the inline unique constraint. The create-table
-- above only applies to fresh tables; an existing narrower table needs this
-- index added separately. It is idempotent.
create unique index if not exists product_reviews_product_email on product_reviews(product_id, email);
create index if not exists product_reviews_pid on product_reviews(product_id);

-- SECTION: delivery_seeds
-- Seed missing zones only; never overwrite Admin-edited rows. Section 11
-- supplies the current columns without changing legacy data. Some existing
-- tables ALSO require zone_name: populate it with the seed name on INSERT,
-- but do not add it on fresh tables or backfill/rename existing names.
do $$
declare
  has_zone_name boolean;
begin
  -- Inspect the same relation resolved by the INSERT (including search_path).
  select exists (
    select 1 from pg_catalog.pg_attribute
    where attrelid = 'delivery_zones'::regclass
      and attname = 'zone_name' and attnum > 0 and not attisdropped
  ) into has_zone_name;

  -- Dynamic SQL never references the absent legacy column on a fresh table.
  -- Both format arguments are fixed SQL fragments, not user-supplied values.
  -- No conflict target: also skip unique name/zone_name collisions where an
  -- Admin has retained a default name under a different ID.
  execute format($seed$
insert into delivery_zones (id, name, currency, fare_min, fare_max, kind, sort_order%s)
select id, name, currency, fare_min, fare_max, kind, sort_order%s
from (values
  ('lagos-mainland', 'Lagos Mainland', 'NGN', 2000, 5000, 'delivery', 1),
  ('lagos-island',   'Lagos Island',   'NGN', 3500, 6000, 'delivery', 2),
  ('ng-other',       'Other Nigeria',  'NGN',    0,    0, 'quote',    3),
  ('cotonou',        'Cotonou',        'CFA', 1000, 3000, 'delivery', 4),
  ('calavi',         'Calavi',         'CFA', 1500, 3500, 'delivery', 5),
  ('porto-novo',     'Porto-Novo',     'CFA', 1500, 3500, 'delivery', 6),
  ('bj-other',       'Other Benin',    'CFA',    0,    0, 'quote',    7),
  ('lome',           'Lomé',           'CFA', 2500, 3500, 'delivery', 8),
  ('tg-other',       'Other Togo',     'CFA',    0,    0, 'quote',    9),
  ('pickup-cotonou', 'Pickup in Cotonou is free for lighter products',
                     'CFA',    0,    0, 'pickup',   10)
) as seeds(id, name, currency, fare_min, fare_max, kind, sort_order)
on conflict do nothing;
$seed$,
    case when has_zone_name then ', zone_name' else '' end,
    case when has_zone_name then ', name' else '' end
  );
end $$;

-- SECTION: storage
-- Storage is provisioned once in Dashboard or with this statement. The service
-- role is used only server-side; public objects are safe to render directly.
-- The application uses exactly one bucket: uploads. Never a receipts bucket.
-- This statement is idempotent and never changes an existing bucket's
-- visibility: if uploads already exists its public flag is preserved, and no
-- existing bucket or object is deleted or modified. Receipts are rows in the
-- receipts table and their files live under uploads/proofs/... . No
-- SUPABASE_PRIVATE_BUCKET handling is required.
insert into storage.buckets (id, name, public)
values ('uploads', 'uploads', true)
on conflict (id) do nothing;
-- Idempotent policies: do not create duplicates if they already exist.
do $$ begin
  create policy "public read uploads" on storage.objects for select using (bucket_id = 'uploads');
exception when duplicate_object then null;
end $$;
do $$ begin
  create policy "service role writes uploads" on storage.objects for all to service_role using (bucket_id = 'uploads') with check (bucket_id = 'uploads');
exception when duplicate_object then null;
end $$;
alter policy "service role writes uploads" on storage.objects to service_role
  using (bucket_id = 'uploads') with check (bucket_id = 'uploads');

-- Proofs share uploads/proofs/<date>/<hash>-<128-bit-token>.<ext>.
-- uploads is PUBLIC: signed links do not make objects private.
-- This schema does not delete any existing bucket or stored object.

-- SECTION: stock
-- ------------------------------------------------------------------ stock
-- Atomic stock reservation/release used by checkout when Supabase is the
-- source of truth. A single UPDATE with a guard on stock_quantity prevents
-- two concurrent checkouts from overselling the same product; FOUND tells
-- the caller whether the whole quantity could be reserved.
-- Ensure required products columns exist before the functions that reference
-- them. Add-only, idempotent, preserves all existing product rows and stock
-- values and never overwrites stock during schema setup.
alter table products add column if not exists stock_quantity integer not null default 0;
alter table products add column if not exists stock integer default 0;
alter table products add column if not exists updated_at timestamptz default now();
alter table products add column if not exists online boolean default true;
create or replace function reserve_product_stock(p_id text, p_qty integer)
returns boolean language plpgsql security definer as $$
declare reserved boolean;
begin
  if p_qty is null or p_qty <= 0 then
    return false;
  end if;
  update products
     set stock_quantity = stock_quantity - p_qty,
         stock = stock_quantity - p_qty,
         updated_at = now()
   where id = p_id
     and online is not false
     and stock_quantity >= p_qty
   returning true into reserved;
  return coalesce(reserved, false);
end $$;

create or replace function release_product_stock(p_id text, p_qty integer)
returns boolean language plpgsql security definer as $$
declare released boolean;
begin
  if p_qty is null or p_qty <= 0 then
    return false;
  end if;
  update products
     set stock_quantity = stock_quantity + p_qty,
         stock = stock_quantity + p_qty,
         updated_at = now()
   where id = p_id
   returning true into released;
  return coalesce(released, false);
end $$;
