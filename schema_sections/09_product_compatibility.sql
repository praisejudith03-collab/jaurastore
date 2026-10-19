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

