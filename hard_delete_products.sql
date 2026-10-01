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
--
-- Two properties matter here, and both come from tables that have lived through
-- a few years of shop changes:
--
--   * A table may hold rows pointing at products that no longer exist - most
--     obviously product_reviews, where product_id has never had a foreign key
--     and so kept every review of every product since deleted. So a table that
--     cannot take the cascade is reported and skipped, and is left EXACTLY as
--     it was. It must not be able to abort the file: the tables that do accept
--     the cascade are the ones the shop actually needs, and losing all of them
--     to one table of old junk is a bad trade.
--   * While a table is being altered it is never left with no foreign key at
--     all, and the expensive part (checking every existing row) runs under a
--     lock that does not block reads or writes.
do $$
declare
  t    text;
  gone text;
  new_name text := '_product_id_cascade_fkey';
begin
  foreach t in array array[
    'variant_stock', 'product_reviews', 'product_views', 'featured_products'
  ] loop
    if to_regclass('public.' || t) is null then
      continue;                    -- not in this deployment: nothing to cascade
    end if;

    -- Already cascades? Nothing to do, and nothing to risk.
    if exists (
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
      continue;
    end if;

    new_name := t || '_product_id_cascade_fkey';

    -- One table at a time, and all or nothing per table.
    begin
      -- (a) Add the cascade FIRST, and add it NOT VALID. NOT VALID checks no
      --     existing row, so this cannot fail on a table holding old orphans,
      --     and the table is never left with no foreign key. The cascade
      --     applies to real rows immediately - NOT VALID only means the
      --     pre-existing rows have not been proved - so deletion already
      --     takes the children with it.
      execute format('alter table public.%I drop constraint if exists %I',
                     t, new_name);
      execute format(
        'alter table public.%I add constraint %I foreign key (product_id) '
        'references public.products(id) on delete cascade not valid',
        t, new_name);

      -- (b) Now the old non-cascading constraint is redundant, and it would
      --     refuse the very delete this migration exists to allow. ('a' = no
      --     action, 'r' = restrict.) Anything else is left alone.
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

      -- (c) Finally check the old rows for real. This takes a weaker lock
      --     than the ADD did, so it does not block the shop. If it fails, the
      --     whole table rolls back to where it started and the notice below
      --     says so.
      execute format('alter table public.%I validate constraint %I', t, new_name);
    exception when others then
      raise notice
        'hard_delete_products: left %.% on its existing constraint (%). '
        'It is still deleted row-by-row by the app.', t, new_name, sqlerrm;
    end;
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

-- ------------------------------------------------------------------ access
-- CREATE FUNCTION grants EXECUTE to PUBLIC, and on Supabase the public schema
-- is the one PostgREST exposes. Without this revoke, a function whose entire
-- job is deleting the catalogue is callable by the anonymous internet: any
-- visitor who can reach the API could send a request body listing the product
-- ids and empty the shop. The cascade above is what makes that reachable, so
-- it is revoked before it is ever useful.
revoke execute on function public.hard_delete_products(text[]) from public;

-- anon / authenticated are Supabase roles and may not exist on a plain
-- PostgreSQL server, so their absence is not treated as an error.
do $$
declare
  r text;
begin
  foreach r in array array['anon', 'authenticated', 'authenticator'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format(
        'revoke execute on function public.hard_delete_products(text[]) from %I',
        r);
    end if;
  end loop;
end $$;

-- The app calls this with the service-role key (server side only, never in a
-- browser). SECURITY DEFINER means the role needs no grants on the tables
-- themselves. Guarded for the same reason as the revokes above: this file is
-- also run against staging and test databases, where the Supabase roles are
-- not present, and a bare GRANT on a missing role aborts the whole script -
-- taking every cascade above it down with it.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant execute on function '
           'public.hard_delete_products(text[]) to service_role';
  end if;
end $$;
