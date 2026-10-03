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
