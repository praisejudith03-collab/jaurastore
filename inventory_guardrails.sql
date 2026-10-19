-- Inventory guardrails migration. Safe to apply more than once after the
-- products table exists. Apply through the Supabase SQL editor/migrations.
begin;

-- SECTION: stock
alter table products add column if not exists stock_quantity integer not null default 0;
alter table products add column if not exists stock integer default 0;
alter table products add column if not exists updated_at timestamptz default now();
alter table products add column if not exists online boolean default true;
update products set stock_quantity = greatest(coalesce(stock_quantity,0),0),
 stock = greatest(coalesce(stock_quantity,0),0)
where stock_quantity is null or stock_quantity < 0
  or stock is distinct from greatest(coalesce(stock_quantity,0),0);
alter table products alter column stock_quantity set default 0;
alter table products alter column stock_quantity set not null;
do $$ begin
 if not exists (select 1 from pg_constraint where conrelid='public.products'::regclass
               and conname='products_inventory_nonnegative') then
  alter table public.products add constraint products_inventory_nonnegative
    check (stock_quantity >= 0 and (stock is null or stock >= 0)) not valid;
 end if;
end $$;
alter table products validate constraint products_inventory_nonnegative;

create or replace function reserve_product_stock(p_id text,p_qty integer,p_option text default null)
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
begin
 if p_qty is null or p_qty<=0 then return false; end if;
 update products set stock_quantity=coalesce(stock_quantity,0)-p_qty,
  stock=coalesce(stock_quantity,0)-p_qty,
  "optionStock"=case when p_option is not null and p_option<>'' then
    jsonb_set(coalesce("optionStock",'{}'::jsonb),array[p_option],
      to_jsonb(coalesce(("optionStock"->>p_option)::int,0)-p_qty))
    else "optionStock" end, updated_at=now()
 where id=p_id and online is not false and coalesce(stock_quantity,0)>=p_qty
  and case when p_option is not null and p_option<>'' then
    ("optionStock" ? p_option and coalesce(("optionStock"->>p_option)::int,0)>=p_qty)
  else coalesce("optionStock",'{}'::jsonb)='{}'::jsonb and
    (options is null or options in ('null'::jsonb,'[]'::jsonb,'{}'::jsonb)) end;
 return found;
end $$;

create or replace function release_product_stock(p_id text,p_qty integer,p_option text default null)
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
begin
 if p_qty is null or p_qty<=0 then return false; end if;
 update products set stock_quantity=coalesce(stock_quantity,0)+p_qty,
  stock=coalesce(stock_quantity,0)+p_qty,
  "optionStock"=case when p_option is not null and p_option<>''
    and "optionStock" ? p_option then jsonb_set("optionStock",array[p_option],
      to_jsonb(coalesce(("optionStock"->>p_option)::int,0)+p_qty)) else "optionStock" end,
  updated_at=now()
 where id=p_id;
 return found;
end $$;

create or replace function sync_supplier_stock(p_id text,p_stock integer,
 p_option_stock_changes jsonb,p_allow_increase boolean,
 p_option_snapshot_keys jsonb)
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
declare current_options jsonb; product_options jsonb; merged_options jsonb; safe_changes jsonb;
 total_stock numeric;
begin
 if p_id is null or btrim(p_id)='' or p_stock is null or p_stock<0 or p_stock>10000000 then
  return false;
 end if;
 if p_option_snapshot_keys is not null and jsonb_typeof(p_option_snapshot_keys)<>'array' then
  return false;
 end if;
 perform pg_advisory_xact_lock(hashtextextended(p_id,0));
 select coalesce("optionStock",'{}'::jsonb),coalesce(options,'null'::jsonb)
  into current_options,product_options from products where id=p_id for update;
 if not found then return false; end if;
 if p_option_stock_changes is null then
  if current_options<>'{}'::jsonb or
    product_options not in ('null'::jsonb,'[]'::jsonb,'{}'::jsonb)
    then return false; end if;
  update products set stock_quantity=case when p_allow_increase then p_stock
    else least(coalesce(stock_quantity,0),p_stock) end,
    stock=case when p_allow_increase then p_stock
    else least(coalesce(stock_quantity,0),p_stock) end,updated_at=now()
    where id=p_id;
  return found;
 end if;
 if jsonb_typeof(p_option_stock_changes)<>'object' then return false; end if;
 if exists (select 1 from jsonb_each(p_option_stock_changes) x(k,v)
  where jsonb_typeof(v) not in ('number','string')
    or case when coalesce(v #>> '{}','') ~ '^[0-9]+$'
      then (v #>> '{}')::numeric>10000000 else true end)
  then return false; end if;
 if jsonb_typeof(current_options)<>'object' then current_options:='{}'::jsonb; end if;
 if current_options='{}'::jsonb and
  product_options in ('null'::jsonb,'[]'::jsonb,'{}'::jsonb)
  then return false; end if;
 -- A supplier response was fetched against an earlier option snapshot. A key
 -- that was in that snapshot but has since disappeared from optionStock is a
 -- tombstone: do not merge it back. A previously untracked key may be added
 -- only while its value still exists in the current product options.
 select coalesce(jsonb_object_agg(k,to_jsonb(case when p_allow_increase
  then (v #>> '{}')::integer else least(case when current_options ? k
    and (current_options->>k) ~ '^[0-9]+$' then (current_options->>k)::integer
    else 0 end,(v #>> '{}')::integer) end)),'{}'::jsonb)
  into safe_changes
  from jsonb_each(p_option_stock_changes) x(k,v)
  where (current_options ? k or not coalesce(p_option_snapshot_keys ? k,false))
    and (
      exists (
        select 1 from jsonb_array_elements(
          case when jsonb_typeof(product_options)='array' then product_options else '[]'::jsonb end) o
        cross join lateral jsonb_array_elements_text(
          case when jsonb_typeof(o->'values')='array' then o->'values' else '[]'::jsonb end) val(value)
        where lower(btrim(val.value))=lower(k)
           or lower(btrim(coalesce(o->>'title','')) || ': ' || btrim(val.value))=lower(k)
      )
      or (position(' · ' in k)>0 and not exists (
        select 1 from unnest(string_to_array(k,' · ')) part(value)
        where not exists (
          select 1 from jsonb_array_elements(
            case when jsonb_typeof(product_options)='array' then product_options else '[]'::jsonb end) o
          cross join lateral jsonb_array_elements_text(
            case when jsonb_typeof(o->'values')='array' then o->'values' else '[]'::jsonb end) val(value)
          where lower(btrim(val.value))=lower(btrim(part.value))
             or lower(btrim(coalesce(o->>'title','')) || ': ' || btrim(val.value))=lower(btrim(part.value))
        )
      ))
    );
 merged_options:=current_options||safe_changes;
 select coalesce(sum(case when (v #>> '{}') ~ '^[0-9]+$'
  then (v #>> '{}')::numeric else 0 end),0)
  into total_stock from jsonb_each(merged_options) x(k,v);
 if total_stock>10000000 then return false; end if;
 update products set "optionStock"=merged_options,stock_quantity=total_stock::integer,
  stock=total_stock::integer,updated_at=now() where id=p_id;
 return found;
end $$;

revoke all on function public.reserve_product_stock(text,integer,text) from public;
revoke all on function public.release_product_stock(text,integer,text) from public;
revoke all on function public.sync_supplier_stock(text,integer,jsonb,boolean,jsonb) from public;
do $$ begin
 if exists (select 1 from pg_roles where rolname='service_role') then
  execute 'grant execute on function public.reserve_product_stock(text,integer,text) to service_role';
  execute 'grant execute on function public.release_product_stock(text,integer,text) to service_role';
  execute 'grant execute on function public.sync_supplier_stock(text,integer,jsonb,boolean,jsonb) to service_role';
 end if;
end $$;

commit;
