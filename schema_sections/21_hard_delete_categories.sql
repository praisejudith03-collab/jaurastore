-- SECTION: hard_delete_categories
-- Permanent category deletion and anti-resurrection guard.
-- Run this migration before deploying the matching application changes.
-- Idempotent; existing category rows and details are not modified.
create table if not exists public.deleted_categories (
  category_id text primary key,
  deleted_at timestamptz not null default now()
);
alter table public.deleted_categories enable row level security;
revoke all on table public.deleted_categories from public;
do $$ declare r text; begin
  foreach r in array array['anon', 'authenticated', 'authenticator'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on table public.deleted_categories from %I', r);
    end if;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant select, insert on table public.deleted_categories to service_role';
  end if;
end $$;

create or replace function public.reject_deleted_category_id()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  -- Serialize against a concurrent category upsert, exactly as the delete
  -- RPC locks the same id before writing its tombstone.
  perform pg_advisory_xact_lock(hashtextextended(new.id::text, 0));
  if exists (select 1 from public.deleted_categories d
             where d.category_id = new.id::text) then
    raise exception 'category id % was permanently deleted', new.id
      using errcode = '23514';
  end if;
  return new;
end;
$$;
revoke all on function public.reject_deleted_category_id() from public;
do $$ begin
  if not exists (
    select 1 from pg_trigger
     where tgrelid = 'public.categories'::regclass
       and tgname = 'categories_reject_deleted_id'
       and not tgisinternal
  ) then
    execute 'create trigger categories_reject_deleted_id '
         || 'before insert or update of id on public.categories '
         || 'for each row execute function public.reject_deleted_category_id()';
  end if;
end $$;

create or replace function public.hard_delete_categories(category_ids text[])
returns text[]
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  requested text[];
  cid text;
begin
  select coalesce(array_agg(distinct btrim(u.category_id) order by btrim(u.category_id)), '{}'::text[])
    into requested
    from unnest(coalesce(category_ids, '{}'::text[])) as u(category_id)
   where btrim(u.category_id) <> '';
  if cardinality(requested) = 0 then
    return requested;
  end if;
  foreach cid in array requested loop
    perform pg_advisory_xact_lock(hashtextextended(cid, 0));
  end loop;

  -- Tombstone and physical row removal share one transaction. The trigger
  -- makes every later REST upsert/import/seed of these ids fail permanently.
  insert into public.deleted_categories(category_id)
  select unnest(requested)
  on conflict (category_id) do nothing;
  -- Products keep their free-text category slug, but the
  -- products_category_id_fkey foreign key would refuse the category delete
  -- while any product row still points at it (live error 23503, 2026-10-30).
  -- Detach first, in the same transaction. Guarded: some deployments and
  -- test schemas have no products.category_id column.
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'products'
                and column_name = 'category_id') then
    update public.products set category_id = null
     where category_id::text = any(requested);
  end if;
  delete from public.categories where id::text = any(requested);
  return requested;
end;
$$;
revoke all on function public.hard_delete_categories(text[]) from public;
do $$ declare r text; begin
  foreach r in array array['anon', 'authenticated', 'authenticator'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on function public.hard_delete_categories(text[]) from %I', r);
    end if;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant execute on function public.hard_delete_categories(text[]) to service_role';
  end if;
end $$;
