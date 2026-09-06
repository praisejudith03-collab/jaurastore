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

-- Dead leftovers from the original hand-built table: price_cfa, price_ngn,
-- name_fr, compare_cfa, compare_ngn, option_stock. The app reads and writes
-- the camelCase columns only, so these are never used. NEVER drop them and
-- never rename them - they are harmless legacy junk, but live rows still
-- exist in the production table.

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
alter table site_settings add column if not exists hero_video_url text not null default '';
alter table site_settings add column if not exists hero_poster_url text not null default '';
alter table site_settings add column if not exists hero_doc_url text not null default '';
alter table site_settings add column if not exists shop_banner_url text not null default '';
alter table site_settings add column if not exists shipping_note text not null default '';
alter table site_settings add column if not exists banner_from text not null default '';
alter table site_settings add column if not exists banner_to text not null default '';
alter table site_settings add column if not exists conv_banner text not null default '';
alter table site_settings add column if not exists conv_bold text not null default '';

create table if not exists categories (
  id text primary key,
  name text not null,
  name_fr text not null default '',
  image_url text not null default '',
  hidden boolean not null default false,
  updated_at timestamptz not null default now()
);

alter table products add column if not exists image_url text;
alter table products add column if not exists stock_quantity integer not null default 0;
do $$ begin
  alter table products add constraint products_stock_nonnegative check (stock_quantity >= 0) not valid;
exception when duplicate_object then null;
end $$;

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
create index if not exists admin_reset_tokens_lookup on admin_reset_tokens(email, purpose, created_at desc);

-- Storage is provisioned once in Dashboard or with this statement. The service
-- role is used only server-side; public objects are safe to render directly.
insert into storage.buckets (id, name, public)
values ('uploads', 'uploads', true)
on conflict (id) do update set public = true;
do $$ begin
  create policy "public read uploads" on storage.objects for select using (bucket_id = 'uploads');
exception when duplicate_object then null;
end $$;
do $$ begin
  create policy "service role writes uploads" on storage.objects for all using (bucket_id = 'uploads') with check (bucket_id = 'uploads');
exception when duplicate_object then null;
end $$;

-- Payment receipts/proofs live in a PRIVATE bucket: the only URL ever handed
-- out is a short-lived signed URL minted server-side, so a receipt is never
-- publicly guessable and never reaches a customer/storefront response.
insert into storage.buckets (id, name, public)
values ('receipts', 'receipts', false)
on conflict (id) do update set public = false;
do $$ begin
  create policy "service role writes receipts" on storage.objects for all using (bucket_id = 'receipts') with check (bucket_id = 'receipts');
exception when duplicate_object then null;
end $$;

-- ------------------------------------------------------------------ stock
-- Atomic stock reservation/release used by checkout when Supabase is the
-- source of truth. A single UPDATE with a guard on stock_quantity prevents
-- two concurrent checkouts from overselling the same product; FOUND tells
-- the caller whether the whole quantity could be reserved.
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
