-- SECTION: stock
-- ------------------------------------------------------------------ stock
-- Atomic stock reservation/release used by checkout when Supabase is the
-- source of truth. A single UPDATE with a guard on stock_quantity prevents
-- two concurrent checkouts from overselling the same product; FOUND tells
-- the caller whether the whole quantity could be reserved.
-- Ensure required products columns exist before the functions that reference
-- them. Add-only, idempotent, preserves all existing product rows and stock
-- values and never overwrites stock during schema setup.
alter table products add column if not exists stock_quantity integer not null default 0;
alter table products add column if not exists stock integer default 0;
alter table products add column if not exists updated_at timestamptz default now();
alter table products add column if not exists online boolean default true;
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
