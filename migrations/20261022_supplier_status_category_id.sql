-- Safe, additive migration. Run in Supabase SQL editor after review.
-- Keeps legacy products.category untouched for rollback/client compatibility.
begin;

alter table public.products
  add column if not exists supplier_status text not null default 'ok';
do $$ begin
  alter table public.products add constraint products_supplier_status_check
    check (supplier_status in ('out_of_stock','low_stock','ok')) not valid;
exception when duplicate_object then null;
end $$;
alter table public.products validate constraint products_supplier_status_check;

alter table public.products add column if not exists category_id text;

-- Explicit legacy alias: older catalog rows use "clothing" but the category
-- list did not contain that id. Create a real relational target while keeping
-- the legacy text column untouched. Reuse the existing fashion banner URL.
insert into public.categories (id,name,name_fr,image_url,hidden)
values ('clothing','Clothing','Vêtements',
  coalesce((select image_url from public.categories where id='ankara'),
           (select image_url from public.categories where id='accessories'),''), false)
on conflict (id) do nothing;

-- Map only unambiguous legacy id/name matches. Abort the entire transaction if
-- any non-empty legacy category cannot be resolved, rather than losing linkage.
do $$
declare unresolved bigint; ambiguous bigint;
begin
  select count(*) into ambiguous from public.products p
   where p.category_id is null and p.category is not null and btrim(p.category)<>''
     and (select count(*) from public.categories c
       where lower(btrim(p.category))=lower(btrim(c.id))
          or lower(btrim(p.category))=lower(btrim(c.name)))>1;
  if ambiguous>0 then raise exception 'Category migration stopped: % product rows have ambiguous category matches', ambiguous; end if;
  update public.products p set category_id=c.id
  from public.categories c
  where p.category_id is null and p.category is not null
    and (lower(btrim(p.category))=lower(btrim(c.id))
      or lower(btrim(p.category))=lower(btrim(c.name)));
  select count(*) into unresolved from public.products p
    where p.category is not null and btrim(p.category)<>'' and p.category_id is null;
  if unresolved>0 then
    raise exception 'Category migration stopped: % product rows need explicit category mapping', unresolved;
  end if;
end $$;

do $$ begin
  alter table public.products add constraint products_category_id_fkey
    foreign key (category_id) references public.categories(id) on update cascade not valid;
exception when duplicate_object then null;
end $$;
alter table public.products validate constraint products_category_id_fkey;
create index if not exists products_category_id_live_idx
  on public.products(category_id) where is_deleted is not true;

-- Narrowly-scoped RPCs: supplier inventory values are diagnostic only.
create or replace function public.set_supplier_status(p_id text,p_status text)
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if p_id is null or btrim(p_id)='' or p_status not in ('out_of_stock','low_stock','ok') then return false; end if;
  update public.products set supplier_status=p_status where id=p_id and is_deleted is not true;
  return found;
end $$;
revoke all on function public.set_supplier_status(text,text) from public;
grant execute on function public.set_supplier_status(text,text) to service_role;

-- Preserve the legacy RPC signature for old callers, but stop it from writing
-- any inventory. Its only allowed write is the isolated supplier_status flag.
create or replace function public.sync_supplier_stock(p_id text,p_stock integer,
 p_option_stock_changes jsonb,p_allow_increase boolean,p_option_snapshot_keys jsonb)
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
declare status_value text;
begin
  if p_id is null or btrim(p_id)='' then return false; end if;
  if p_option_stock_changes is null then
    if p_stock is null or p_stock<0 then return false; end if;
    status_value:=case when p_stock=0 then 'out_of_stock' when p_stock<=9 then 'low_stock' else 'ok' end;
  else
    if jsonb_typeof(p_option_stock_changes)<>'object' or exists (
      select 1 from jsonb_each(p_option_stock_changes) x(k,v)
      where coalesce(v #>> '{}','') !~ '^[0-9]+$' or (v #>> '{}')::numeric<0
    ) then return false; end if;
    status_value:=case
      when not exists(select 1 from jsonb_each(p_option_stock_changes) x(k,v) where (v #>> '{}')::numeric>0) then 'out_of_stock'
      when exists(select 1 from jsonb_each(p_option_stock_changes) x(k,v) where (v #>> '{}')::numeric<=9) then 'low_stock'
      else 'ok' end;
  end if;
  update public.products set supplier_status=status_value where id=p_id and is_deleted is not true;
  return found;
end $$;
revoke all on function public.sync_supplier_stock(text,integer,jsonb,boolean,jsonb) from public;
grant execute on function public.sync_supplier_stock(text,integer,jsonb,boolean,jsonb) to service_role;

commit;
