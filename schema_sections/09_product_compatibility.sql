-- SECTION: product_compatibility
-- Legacy id alias. A product's `id` is its primary key and is NEVER renamed
-- while orders, reviews, carts or analytics still reference it. When a row is
-- eventually given a canonical jau-* id, the previous wix-* id is copied here
-- so old product links, order lines, reviews and cart entries keep resolving
-- through catalog.product_index() / supabase_store.product_by_id(). It stays
-- NULL for rows that were created with a canonical id and have no history.
-- Additional repair for any older table missing the remaining non-key columns.
-- These are add-only and nullable for legacy rows so no invented values are
-- written and every existing row keeps its id, name, prices and stock.
alter table products add column if not exists "legacyId"       text;
alter table products add column if not exists sku              text;
alter table products add column if not exists slug             text;
alter table products add column if not exists category         text;
alter table products add column if not exists category_id      text;
alter table products add column if not exists image            text;
alter table products add column if not exists image_url        text;
alter table products add column if not exists description      text;
alter table products add column if not exists stock_quantity   integer not null default 0;
alter table products add column if not exists "supplierId" text not null default '';
alter table products add column if not exists "supplierSku" text not null default '';
-- Variant supplier costing + the Trash Bin soft-delete flag.
alter table products add column if not exists "supplierCost" numeric;
alter table products add column if not exists "optionSupplierCost" jsonb;
alter table products add column if not exists supplier_status text not null default 'ok';
do $$ begin
  alter table products add constraint products_supplier_status_check
    check (supplier_status in ('out_of_stock','low_stock','ok')) not valid;
exception when duplicate_object then null;
end $$;
alter table products validate constraint products_supplier_status_check;
alter table products add column if not exists is_deleted boolean not null default false;

alter table products add column if not exists "legacyId" text;
create unique index if not exists products_legacy_id_key
  on products ("legacyId") where "legacyId" is not null;

alter table products add column if not exists image_url text;
alter table products add column if not exists stock_quantity integer not null default 0;
do $$ begin
  alter table products add constraint products_stock_nonnegative check (stock_quantity >= 0) not valid;
exception when duplicate_object then null;
end $$;

-- Read-path indexes. The products table is the shop's hot path: every page
-- load filters online / is_deleted and the admin desk filters by category.
-- Without these, PostgREST scans the whole table on each catalogue rebuild.
-- The partial online index is the exact predicate merged() uses.
create index if not exists products_online_live_idx
  on products (online, updated_at desc) where is_deleted is not true;
create index if not exists products_category_live_idx
  on products (category) where is_deleted is not true;
create index if not exists products_deleted_idx
  on products (is_deleted);
create index if not exists products_updated_idx
  on products (updated_at desc);

-- Supplier watchdogs may change only this isolated diagnostic flag.
create or replace function set_supplier_status(p_id text, p_status text)
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
begin
 if p_id is null or btrim(p_id)='' or p_status not in ('out_of_stock','low_stock','ok') then return false; end if;
 update products set supplier_status=p_status where id=p_id and is_deleted is not true;
 return found;
end $$;
revoke all on function set_supplier_status(text,text) from public;
do $$ begin
  execute 'grant execute on function public.set_supplier_status(text,text) to service_role';
exception when undefined_object then null;
end $$;

create or replace function sync_supplier_stock(p_id text,p_stock integer,
 p_option_stock_changes jsonb,p_allow_increase boolean,
 p_option_snapshot_keys jsonb)
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
declare status_value text;
begin
 -- Compatibility RPC: ignore all observed quantities and touch only status.
 if p_id is null or btrim(p_id)='' then return false; end if;
 if p_option_stock_changes is not null and jsonb_typeof(p_option_stock_changes)<>'object' then return false; end if;
 if p_option_stock_changes is null then
   if p_stock is null or p_stock<0 then return false; end if;
   status_value := case when p_stock=0 then 'out_of_stock' when p_stock<=9 then 'low_stock' else 'ok' end;
 else
   if exists (select 1 from jsonb_each(p_option_stock_changes) x(k,v)
     where coalesce(v #>> '{}','') !~ '^[0-9]+$' or (v #>> '{}')::numeric<0) then return false; end if;
   status_value := case when not exists(select 1 from jsonb_each(p_option_stock_changes) x(k,v) where (v #>> '{}')::numeric>0)
     then 'out_of_stock'
     when exists(select 1 from jsonb_each(p_option_stock_changes) x(k,v) where (v #>> '{}')::numeric<=9)
     then 'low_stock' else 'ok' end;
 end if;
 update products set supplier_status=status_value where id=p_id and is_deleted is not true;
 return found;
end $$;

