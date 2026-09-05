-- =====================================================================
-- Jaura Store — Supabase (PostgreSQL) schema
-- =====================================================================
-- Run this once in the Supabase SQL editor (Dashboard → SQL → New query).
-- Every statement is idempotent (IF NOT EXISTS), so re-running is safe.
--
-- SQLite on the Render disk stays the working copy; the app mirrors every
-- write into these tables (see supabase_store.py) so products, orders,
-- receipts, referral codes, usage logs, coupons and the growth settings
-- also live in Supabase and can never be lost with the dyno.
--
-- Required environment variables (both Render services):
--   SUPABASE_URL                 https://<project>.supabase.co
--   SUPABASE_SERVICE_ROLE_KEY    the service-role key (server-side only)
-- =====================================================================

-- ------------------------------------------------------------ products
-- These are the 24 keys the app actually writes (catalog.normalize() +
-- supabase_store.upsert_products). camelCase keys MUST be quoted: Postgres
-- folds unquoted identifiers to lowercase, so an unquoted priceCfa would
-- create a pricecfa column and every write would still fail with PGRST204.
create table if not exists products (
  id               text primary key,
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
  images           jsonb,
  description      text,
  stock            integer default 0,
  badge            text,
  featured         boolean default false,
  online           boolean default true,
  colors           jsonb,
  options          jsonb,
  "optionStock"    jsonb,
  "placeholderImage" text,
  "usesPlaceholder"  boolean default false,
  source           text default 'admin',
  updated_at       timestamptz default now()
);

-- Repair an EXISTING products table hand-built narrower than the row the app
-- writes. Only adds columns; never drops or rewrites data. Run it if the
-- Render log says "[supabase] products upsert: stored without columns [...]".
alter table products add column if not exists "nameFr"           text;
alter table products add column if not exists "compareCfa"       numeric;
alter table products add column if not exists "compareNgn"       numeric;
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
create index if not exists idx_orders_at on orders (at desc);
create index if not exists idx_orders_status on orders (status);

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
create index if not exists idx_receipts_order on receipts (order_id);

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
create index if not exists idx_referral_email on referral_codes (email);

-- one row per successful purchase made with a referral code
create table if not exists referral_uses (
  id          bigint generated always as identity primary key,
  code        text not null,
  order_id    text,
  buyer_email text,
  at          timestamptz default now()
);
create index if not exists idx_referral_uses_code on referral_uses (code);

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

-- ----------------------------------------------------- growth settings
-- key/value map: referralEnabled, abandonedEnabled, minSpendNgn, cfaRate,
-- buyerPercent, referrerPercent, milestone, abandonedHours,
-- abandonedSubject, abandonedTemplate
create table if not exists growth_settings (
  key   text primary key,
  value text
);
