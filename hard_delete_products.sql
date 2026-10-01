-- Absolute product deletion: one SQL CASCADE, enforced by the database.
--
-- Before this file the shop deleted a product by asking PostgREST to remove
-- one row at a time from whichever child tables the Python code happened to
-- know about. Any table added later - or that only existed on one
-- environment - was simply left behind, and those orphan rows are exactly
-- what the storage sweeper later has to guess about.
--
-- This script makes the cascade a property of the SCHEMA, not of a list in
-- Python:
--
--   * every per-product table gets a foreign key to products(id) with
--     ON DELETE CASCADE, so deleting the parent row can never leave a child,
--   * product_variants / product_prices / product_options are created if they
--     are not there yet (a deployment that normalises options into their own
--     tables instead of keeping them as JSON columns on products),
--   * hard_delete_products(text[]) is the single statement the app calls. It
--     returns only the ids that were REALLY there, so a caller can never be
--     told "deleted" for a row that survived.
--
-- The durable "do not ever serve this id again" list stays in
-- growth_settings.deleted_product_ids_json and is still written by the app
-- (supabase_store.add_deleted_id) immediately after this call. It is
-- deliberately NOT duplicated here: two tombstone lists would eventually
-- disagree, and the disagreement is exactly how a deleted product returns.
--
-- Run it once in the Supabase SQL editor. It is idempotent: re-running it
-- never loses data and never fails on a table that already exists.
--
-- supabase_store.hard_delete_products() calls the function when it exists and
-- falls back to deleting row-by-row (with the same table list) when it does
-- not, so the app behaves identically either way.

-- ---------------------------------------------------------------- children
-- product_variants / product_prices / product_options exist on deployments
-- that normalise a product's options into rows instead of JSON columns.
create table if not exists public.product_variants (
  id          uuid primary key default gen_random_uuid(),
  product_id  text not null references public.products(id) on delete cascade,
  title       text,
  value       text,
  sku         text,
  stock       integer,
  sort_order  integer default 0,
  created_at  timestamptz default now()
);

create table if not exists public.product_prices (
  id           uuid primary key default gen_random_uuid(),
  product_id   text not null references public.products(id) on delete cascade,
  variant_id   uuid references public.product_variants(id) on delete cascade,
  option_key   text,
  price_ngn    numeric,
  compare_ngn  numeric,
  updated_at   timestamptz default now()
);

create table if not exists public.product_options (
  id          uuid primary key default gen_random_uuid(),
  product_id  text not null references public.products(id) on delete cascade,
  title       text not null,
  values_json jsonb default '[]'::jsonb,
  sort_order  integer default 0
);

create index if not exists product_variants_product_id_idx on public.product_variants(product_id);
create index if not exists product_prices_product_id_idx   on public.product_prices(product_id);
create index if not exists product_options_product_id_idx  on public.product_options(product_id);

-- Attach ON DELETE CASCADE to the child tables that already exist. The shop
-- keeps variant quantities in variant_stock; the rest are legacy tables.
do $$
declare
  t       text;
  gone    text;
begin
  foreach t in array array[
    'variant_stock', 'product_reviews', 'product_views', 'featured_products'
  ] loop
    if to_regclass('public.' || t) is null then
      continue;                    -- not in this deployment: nothing to cascade
    end if;

    -- Drop only the constraints that point at products(id) WITHOUT a cascade.
    -- ('a' = no action, 'r' = restrict.) Anything else is left alone.
    for gone in
      select con.conname
        from pg_constraint con
        join pg_class rel      on rel.oid = con.conrelid
        join pg_namespace ns   on ns.oid = rel.relnamespace
        join pg_class parent   on parent.oid = con.confrelid
        join pg_namespace pns  on pns.oid = parent.relnamespace
       where ns.nspname  = 'public'
         and rel.relname = t
         and pns.nspname = 'public'
         and parent.relname = 'products'
         and con.contype = 'f'
         and con.confdeltype <> 'c'
    loop
      execute format('alter table public.%I drop constraint %I', t, gone);
    end loop;

    -- Add the cascade, unless a cascading constraint to products(id) already
    -- exists for this table.
    if not exists (
      select 1
        from pg_constraint con
        join pg_class rel    on rel.oid = con.conrelid
        join pg_namespace ns on ns.oid = rel.relnamespace
        join pg_class parent on parent.oid = con.confrelid
       where ns.nspname   = 'public'
         and rel.relname  = t
         and parent.relname = 'products'
         and con.contype = 'f'
         and con.confdeltype = 'c'
    ) then
      execute format(
        'alter table public.%I add constraint %I foreign key (product_id) '
        'references public.products(id) on delete cascade',
        t, t || '_product_id_fkey');
    end if;
  end loop;
end $$;

-- ---------------------------------------------------------------- the delete
-- One statement, one transaction: the CASCADE takes every child row with it,
-- so there is no window in which the product is gone but its variants,
-- prices, options, stock rows, reviews or view counters are not.
create or replace function public.hard_delete_products(product_ids text[])
returns text[]
language plpgsql
security definer
set search_path = public
as $$
declare
  gone text[];
begin
  if product_ids is null or coalesce(array_length(product_ids, 1), 0) = 0 then
    return '{}'::text[];
  end if;

  -- Report only ids that were really there. An unknown id comes back empty,
  -- so the app can never answer "deleted" for a row that survived.
  select coalesce(array_agg(id::text), '{}'::text[])
    into gone
    from public.products
   where id::text = any(product_ids);

  delete from public.products
   where id::text = any(product_ids);

  return gone;
end;
$$;

grant execute on function public.hard_delete_products(text[]) to service_role;
