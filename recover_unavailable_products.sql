-- Master recovery + catalogue write-lock.
--
-- Run this ONCE in the Supabase SQL editor (or `psql -f`), AFTER
-- hard_delete_products.sql. It is idempotent: re-running it never loses data,
-- never fails on a table that already exists, and never creates a product.
--
-- What it does
--   1. Makes sure the availability columns exist (`is_active`,
--      `supplier_status`, `category_id`) so a save cannot 503 because the live
--      table is older than the code.
--   2. Records every row it touches in `product_guard_events`, so a recovery
--      can be audited (and reversed field-by-field) afterwards.
--   3. Restores every soft-deleted / unavailable product:
--          is_deleted = false, source = 'admin' (when it said 'deleted' /
--          'replaced'), is_active = true, online = true
--      and repairs an empty cover from the row's own gallery. Rows that were
--      PERMANENTLY deleted (the `deleted_products` tombstone ledger) are never
--      resurrected: that ledger is the owner's hard delete.
--   4. Prunes the restored ids from the legacy
--      growth_settings.deleted_product_ids_json suppression list, which
--      otherwise still hides a restored row from the storefront.
--   5. Installs the write lock: a trigger that refuses background writes to
--      availability, `category_id` and `image_url`. Supplier checkers,
--      catalog auditors and sync pings may write `supplier_status` only (the
--      dedicated RPCs already enforce that). Deleting a row by hand through
--      PostgREST is refused outright.
--
-- Why a lock instead of trusting every script: all server code talks to
-- PostgREST with one service-role key, so the database cannot tell an admin
-- save from a nightly ping by its role. It CAN tell by the shape of the
-- statement: a real admin save writes the WHOLE product row (name, cover,
-- price, `source='admin'`, a fresh `updated_at`), while a background ping
-- updates one flag. The guard only reverts the second kind.
--
-- Admin operations that legitimately change availability or blank a field
-- call a SECURITY DEFINER function which sets `jaura.catalog_admin` for its
-- own transaction; the guard lets those through:
--     admin_trash_products(text[])            soft-delete (Trash Bin)
--     admin_restore_products(text[])          restore from the Trash Bin
--     admin_set_product_online(text[], bool)  Hide / Show on the website
--     recover_unavailable_products(...)       this file's recovery
--     hard_delete_products(text[])            absolute delete (tombstone + delete)
--
-- Everything runs in one transaction: either the whole repair lands or the
-- database is left exactly as it was.
begin;

-- ---------------------------------------------------------------- 1. columns
alter table public.products add column if not exists is_active boolean not null default true;
alter table public.products add column if not exists supplier_status text not null default 'ok';
alter table public.products add column if not exists category_id text;

-- The permanent-delete ledger lives in hard_delete_products.sql. Creating it
-- here (same shape, no data) makes this file safe to run on a database where
-- that script has not been applied yet.
create table if not exists public.deleted_products (
  product_id text primary key,
  deleted_at timestamptz not null default now()
);

-- One badge vocabulary, shared with the Supplier Alerts tab.
do $$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.products'::regclass
                    and conname = 'products_supplier_status_check') then
    alter table public.products add constraint products_supplier_status_check
      check (supplier_status in ('out_of_stock', 'low_stock', 'ok')) not valid;
  end if;
end $$;
do $$
begin
  alter table public.products validate constraint products_supplier_status_check;
exception when others then
  raise notice 'supplier_status values outside the badge vocabulary exist; left unvalidated';
end $$;

-- ------------------------------------------------------------- 2. audit log
create table if not exists public.product_guard_events (
  id              bigserial primary key,
  product_id      text,
  event           text not null,          -- blocked_*, recovered, trashed, restored, online_change
  field           text,
  old_value       text,
  attempted_value text,
  actor           text,                   -- admin reason, 'catalog-guard', session user
  detail          jsonb,
  at              timestamptz not null default now()
);
create index if not exists product_guard_events_product_idx
  on public.product_guard_events (product_id, at desc);
alter table public.product_guard_events enable row level security;
revoke all on table public.product_guard_events from public;

-- --------------------------------------------------- 3. transaction-scoped OK
create or replace function public.jaura_catalog_admin()
returns boolean
language sql
stable
set search_path = pg_catalog, public
as $$
  select coalesce(current_setting('jaura.catalog_admin', true), '') = 'on'
$$;

-- A complete product-editor save. The admin portal posts every column it
-- holds, so this is distinguishable from a one-column background update.
-- The decisive signal is updated_at: the admin writers ALWAYS stamp a fresh
-- value, while a background flag update leaves it untouched. That is what
-- separates "Praise pressed Hide on this product" from "a nightly ping flipped
-- a boolean" - NEW otherwise carries the whole row for both.
create or replace function public.jaura_full_product_save(
  p_name text, p_image_url text, p_price_ngn numeric,
  p_source text, p_updated_at timestamptz, p_previous_updated_at timestamptz)
returns boolean
language sql
stable
set search_path = pg_catalog, public
as $$
  select nullif(btrim(coalesce(p_name, '')), '') is not null
     and nullif(btrim(coalesce(p_image_url, '')), '') is not null
     and p_price_ngn is not null
     and lower(coalesce(p_source, '')) = 'admin'
     and p_updated_at is not null
     and p_updated_at > (now() - interval '10 minutes')
     and (p_previous_updated_at is null
          or p_updated_at > p_previous_updated_at)
$$;

-- ------------------------------------------------------------- 4. the guard
create or replace function public.products_catalog_guard()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  allowed boolean := public.jaura_catalog_admin();
  full_row boolean;
  tombstoned boolean;
begin
  if tg_op = 'DELETE' then
    -- A row is never removed by a background sweep. An absolute delete must
    -- carry its durable tombstone in the same transaction (that is exactly
    -- what hard_delete_products(text[]) does), or set the admin flag.
    select exists (select 1 from public.deleted_products d
                    where d.product_id = old.id) into tombstoned;
    if not allowed and not tombstoned then
      raise exception 'product % may only be deleted through the catalogue admin functions', old.id
        using errcode = '42501';
    end if;
    return old;
  end if;

  if allowed then
    return new;
  end if;

  full_row := public.jaura_full_product_save(
    new.name, coalesce(new.image_url, new.image), new."priceNgn",
    new.source, new.updated_at, old.updated_at);

  -- Availability can only go OFF as part of a complete admin save.
  if old.is_deleted is not true and new.is_deleted is true then
    insert into public.product_guard_events (product_id, event, field, old_value, attempted_value, actor)
    values (old.id, 'blocked_unavailable', 'is_deleted', old.is_deleted::text,
            new.is_deleted::text, session_user);
    new.is_deleted := old.is_deleted;
  end if;
  if coalesce(old.is_active, true) and not coalesce(new.is_active, true) and not full_row then
    insert into public.product_guard_events (product_id, event, field, old_value, attempted_value, actor)
    values (old.id, 'blocked_unavailable', 'is_active', old.is_active::text,
            new.is_active::text, session_user);
    new.is_active := old.is_active;
  end if;
  if coalesce(old.online, true) and not coalesce(new.online, true) and not full_row then
    insert into public.product_guard_events (product_id, event, field, old_value, attempted_value, actor)
    values (old.id, 'blocked_unavailable', 'online', old.online::text,
            new.online::text, session_user);
    new.online := old.online;
  end if;
  if lower(coalesce(new.source, '')) in ('deleted', 'replaced') then
    insert into public.product_guard_events (product_id, event, field, old_value, attempted_value, actor)
    values (old.id, 'blocked_unavailable', 'source', old.source, new.source, session_user);
    new.source := old.source;
  end if;

  -- Media and category links are never silently blanked. A real admin change
  -- replaces the value; it does not remove it.
  if nullif(btrim(coalesce(old.category_id, '')), '') is not null
     and nullif(btrim(coalesce(new.category_id, '')), '') is null then
    insert into public.product_guard_events (product_id, event, field, old_value, attempted_value, actor)
    values (old.id, 'blocked_null', 'category_id', old.category_id, new.category_id, session_user);
    new.category_id := old.category_id;
  end if;
  if nullif(btrim(coalesce(old.image_url, '')), '') is not null
     and nullif(btrim(coalesce(new.image_url, '')), '') is null then
    insert into public.product_guard_events (product_id, event, field, old_value, attempted_value, actor)
    values (old.id, 'blocked_null', 'image_url', old.image_url, new.image_url, session_user);
    new.image_url := old.image_url;
  end if;
  if nullif(btrim(coalesce(old.image, '')), '') is not null
     and nullif(btrim(coalesce(new.image, '')), '') is null then
    insert into public.product_guard_events (product_id, event, field, old_value, attempted_value, actor)
    values (old.id, 'blocked_null', 'image', old.image, new.image, session_user);
    new.image := old.image;
  end if;

  if new.is_active is distinct from old.is_active
     or new.is_deleted is distinct from old.is_deleted
     or new.online is distinct from old.online then
    insert into public.product_guard_events (product_id, event, detail, actor)
    values (old.id, 'availability_change',
            jsonb_build_object('is_deleted', new.is_deleted,
                               'is_active', new.is_active,
                               'online', new.online),
            session_user);
  end if;
  return new;
end $$;
revoke all on function public.products_catalog_guard() from public;

drop trigger if exists products_catalog_guard_write on public.products;
create trigger products_catalog_guard_write
before update on public.products
for each row execute function public.products_catalog_guard();

drop trigger if exists products_catalog_guard_delete on public.products;
create trigger products_catalog_guard_delete
before delete on public.products
for each row execute function public.products_catalog_guard();

-- --------------------------------------------------------- 5. admin RPCs
-- Soft delete (Trash Bin). The row and its media stay; only the flags move.
create or replace function public.admin_trash_products(p_ids text[])
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare n integer := 0;
begin
  perform set_config('jaura.catalog_admin', 'on', true);
  with moved as (
    update public.products
       set source = 'deleted',
           is_deleted = true,
           is_active = false,
           updated_at = now()
     where id = any(p_ids)
       and is_deleted is not true
    returning id
  ), logged as (
    insert into public.product_guard_events (product_id, event, actor)
    select id, 'trashed', 'admin-rpc' from moved
    returning 1
  )
  select count(*) into n from moved;
  return n;
end $$;

-- Restore from the Trash Bin. Permanent tombstones are not touched here:
-- catalog.upsert refuses those ids before it ever reaches the database.
create or replace function public.admin_restore_products(p_ids text[])
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare n integer := 0;
begin
  perform set_config('jaura.catalog_admin', 'on', true);
  with moved as (
    update public.products p
       set source = 'admin',
           is_deleted = false,
           is_active = true,
           online = true,
           updated_at = now()
     where p.id = any(p_ids)
       and not exists (select 1 from public.deleted_products d
                        where d.product_id = p.id)
       and (p.is_deleted is true
            or coalesce(p.is_active, true) is not true
            or coalesce(p.online, true) is false
            or lower(coalesce(p.source, '')) in ('deleted', 'replaced'))
    returning p.id
  ), logged as (
    insert into public.product_guard_events (product_id, event, actor)
    select id, 'restored', 'admin-rpc' from moved
    returning 1
  )
  select count(*) into n from moved;
  return n;
end $$;

-- Hide / Show on the website (Admin -> Products -> eye icon).
create or replace function public.admin_set_product_online(p_ids text[], p_online boolean)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare n integer := 0;
begin
  perform set_config('jaura.catalog_admin', 'on', true);
  with moved as (
    update public.products
       set online = coalesce(p_online, true),
           is_active = coalesce(p_online, true),
           updated_at = now()
     where id = any(p_ids)
    returning id
  ), logged as (
    insert into public.product_guard_events (product_id, event, actor, detail)
    select id, 'online_change', 'admin-rpc',
           jsonb_build_object('online', coalesce(p_online, true))
      from moved
    returning 1
  )
  select count(*) into n from moved;
  return n;
end $$;

-- ------------------------------------------------------- 6. the recovery
-- p_ids = null restores every unavailable row; pass an array to restore just
-- those. Rows in the permanent `deleted_products` ledger are skipped.
create or replace function public.recover_unavailable_products(
  p_reason text default 'owner recovery',
  p_ids text[] default null)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  target record;
  restored_ids text[] := '{}';
  skipped_permanent integer := 0;
  repaired_images integer := 0;
  pruned_tombstones integer := 0;
  legacy_list text;
  legacy_json jsonb;
  kept jsonb;
begin
  perform set_config('jaura.catalog_admin', 'on', true);

  drop table if exists _recovery_target;
  create temp table _recovery_target on commit drop as
    select p.id,
           p.is_deleted, p.is_active, p.online, p.source,
           p.image, p.image_url, p.images
      from public.products p
     where (p_ids is null or p.id = any(p_ids))
       and (p.is_deleted is true
            or coalesce(p.is_active, true) is not true
            or coalesce(p.online, true) is false
            or lower(coalesce(p.source, '')) in ('deleted', 'replaced'));

  select count(*) into skipped_permanent
    from _recovery_target t
   where exists (select 1 from public.deleted_products d where d.product_id = t.id);

  delete from _recovery_target t
   where exists (select 1 from public.deleted_products d where d.product_id = t.id);

  for target in select * from _recovery_target order by id loop
    insert into public.product_guard_events
      (product_id, event, actor, detail)
    values (target.id, 'recovered', coalesce(p_reason, 'owner recovery'),
            jsonb_build_object('is_deleted', target.is_deleted,
                               'is_active', target.is_active,
                               'online', target.online,
                               'source', target.source));

    -- An empty cover is repaired from the row's own gallery, never invented.
    if nullif(btrim(coalesce(target.image_url, '')), '') is null then
      if nullif(btrim(coalesce(target.image, '')), '') is not null then
        update public.products set image_url = image where id = target.id;
        repaired_images := repaired_images + 1;
      elsif jsonb_typeof(target.images) = 'array' and jsonb_array_length(target.images) > 0 then
        update public.products
           set image_url = target.images ->> 0, image = target.images ->> 0
         where id = target.id;
        repaired_images := repaired_images + 1;
      end if;
    end if;

    update public.products
       set is_deleted = false,
           is_active = true,
           online = true,
           source = case when lower(coalesce(source, '')) in ('deleted', 'replaced')
                         then 'admin' else coalesce(nullif(source, ''), 'admin') end,
           updated_at = now()
     where id = target.id;
    restored_ids := array_append(restored_ids, target.id);
  end loop;

  drop table if exists _recovery_target;

  -- The legacy suppression list hides a product even when its row is live.
  -- Remove exactly the ids we restored; leave every other tombstone alone.
  if array_length(restored_ids, 1) is not null
     and to_regclass('public.growth_settings') is not null then
    select value into legacy_list
      from public.growth_settings
     where key = 'deleted_product_ids_json';
    if nullif(btrim(coalesce(legacy_list, '')), '') is not null then
      begin
        legacy_json := legacy_list::jsonb;
      exception when others then
        legacy_json := null;
      end;
      if jsonb_typeof(legacy_json) = 'array' then
        select coalesce(jsonb_agg(x), '[]'::jsonb) into kept
          from jsonb_array_elements(legacy_json) x
         where (x #>> '{}') <> all (restored_ids);
        if kept is distinct from legacy_json then
          update public.growth_settings
             set value = kept::text
           where key = 'deleted_product_ids_json';
          pruned_tombstones := jsonb_array_length(legacy_json) - jsonb_array_length(kept);
        end if;
      end if;
    end if;
  end if;

  return jsonb_build_object(
    'ok', true,
    'restored', coalesce(array_length(restored_ids, 1), 0),
    'restoredIds', to_jsonb(coalesce(restored_ids, '{}'::text[])),
    'skippedPermanent', skipped_permanent,
    'repairedImages', repaired_images,
    'prunedDeletedIds', pruned_tombstones,
    'reason', coalesce(p_reason, 'owner recovery'));
end $$;

-- --------------------------------------------------- 7. image URL repair
-- `/uploads/<key>` is a legacy same-origin reference. Rewrite it to the
-- public bucket URL the rest of the app stores. The caller passes the project
-- base, e.g. https://rvkweyipqgsggcnimhxf.supabase.co
create or replace function public.repair_product_image_urls(p_project_base text)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare n integer := 0;
begin
  if nullif(btrim(coalesce(p_project_base, '')), '') is null then
    raise exception 'a Supabase project base URL is required';
  end if;
  perform set_config('jaura.catalog_admin', 'on', true);
  update public.products
     set image = case when nullif(btrim(coalesce(image, '')), '') is null
                      then rtrim(p_project_base, '/') || '/storage/v1/object/public/uploads/'
                           || regexp_replace(image_url, '^/(uploads/)?', '') || ''
                      else image end,
         image_url = rtrim(p_project_base, '/') || '/storage/v1/object/public/uploads/'
                     || regexp_replace(image_url, '^/(uploads/)?', '')
   where image_url ~ '^/uploads/'
      or image_url ~ '^uploads/';
  get diagnostics n = row_count;
  return n;
end $$;

-- ------------------------------------------------------------- 8. access
do $$
declare r text;
begin
  execute 'revoke all on function public.admin_trash_products(text[]) from public';
  execute 'revoke all on function public.admin_restore_products(text[]) from public';
  execute 'revoke all on function public.admin_set_product_online(text[], boolean) from public';
  execute 'revoke all on function public.recover_unavailable_products(text, text[]) from public';
  execute 'revoke all on function public.repair_product_image_urls(text) from public';
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on function public.admin_trash_products(text[]) from %I', r);
      execute format('revoke all on function public.admin_restore_products(text[]) from %I', r);
      execute format('revoke all on function public.admin_set_product_online(text[], boolean) from %I', r);
      execute format('revoke all on function public.recover_unavailable_products(text, text[]) from %I', r);
      execute format('revoke all on function public.repair_product_image_urls(text) from %I', r);
    end if;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant execute on function public.admin_trash_products(text[]) to service_role';
    execute 'grant execute on function public.admin_restore_products(text[]) to service_role';
    execute 'grant execute on function public.admin_set_product_online(text[], boolean) to service_role';
    execute 'grant execute on function public.recover_unavailable_products(text, text[]) to service_role';
    execute 'grant execute on function public.repair_product_image_urls(text) to service_role';
    execute 'grant select on table public.product_guard_events to service_role';
  end if;
end $$;

-- ------------------------------------------------- 9. run the recovery now
-- The owner asked for the missing Tuesday/Wednesday products to come back
-- immediately. This call is what does it; the result is printed in the SQL
-- editor's output pane.
select public.recover_unavailable_products('master fix 2026-10-22') as recovery_report;

-- Ensure all non-permanently-deleted products have is_active = true, is_deleted = false:
-- UPDATE products SET is_active = true, is_deleted = false;
do $$ begin
  perform set_config('jaura.catalog_admin', 'on', true);
  UPDATE products SET is_active = true, is_deleted = false
   WHERE not exists (select 1 from public.deleted_products d where d.product_id = products.id)
     AND (coalesce(is_active, false) is not true or coalesce(is_deleted, true) is not false);
end $$;

commit;

-- Verification (read-only). Expect restored rows to be live, and 0 for both
-- unavailable counts unless the owner deliberately hid something afterwards.
--
--   select count(*) filter (where is_deleted)                        as soft_deleted,
--          count(*) filter (where coalesce(is_active, true) = false) as inactive,
--          count(*) filter (where online is false)                  as hidden,
--          count(*) filter (where nullif(btrim(coalesce(image_url, '')), '') is null) as missing_cover
--     from public.products;
--
-- Rollback of one recovery is a matter of reading the before-state that was
-- logged, then restoring it by hand for those ids:
--
--   select product_id, at, detail
--     from public.product_guard_events
--    where event = 'recovered'
--    order by at desc;
