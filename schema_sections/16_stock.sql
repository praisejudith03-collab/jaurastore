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
create or replace function reserve_product_stock(p_id text, p_qty integer, p_option text default null)
returns boolean language plpgsql security definer as $$
declare reserved boolean;
begin
  if p_qty is null or p_qty <= 0 then
    return false;
  end if;
  if p_option is not null and p_option <> '' then
    -- Variant line: guard the product total AND the variant's own quantity,
    -- and decrement both in the same single UPDATE. Two concurrent checkouts
    -- of the last Red unit cannot both pass: only one UPDATE lands.
    update products
       set stock_quantity = stock_quantity - p_qty,
           stock = stock_quantity - p_qty,
           "optionStock" = jsonb_set(
             "optionStock", array[p_option],
             to_jsonb(coalesce(("optionStock"->>p_option)::int, 0) - p_qty)),
           updated_at = now()
     where id = p_id
       and online is not false
       and stock_quantity >= p_qty
       and "optionStock" ? p_option
       and coalesce(("optionStock"->>p_option)::int, 0) >= p_qty
     returning true into reserved;
    return coalesce(reserved, false);
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

create or replace function release_product_stock(p_id text, p_qty integer, p_option text default null)
returns boolean language plpgsql security definer as $$
declare released boolean;
begin
  if p_qty is null or p_qty <= 0 then
    return false;
  end if;
  if p_option is not null and p_option <> '' then
    update products
       set stock_quantity = stock_quantity + p_qty,
           stock = stock_quantity + p_qty,
           "optionStock" = case
             when "optionStock" ? p_option then jsonb_set(
               "optionStock", array[p_option],
               to_jsonb(coalesce(("optionStock"->>p_option)::int, 0) + p_qty))
             else "optionStock" end,
           updated_at = now()
     where id = p_id
     returning true into released;
    return coalesce(released, false);
  end if;
  update products
     set stock_quantity = stock_quantity + p_qty,
         stock = stock_quantity + p_qty,
         updated_at = now()
   where id = p_id
   returning true into released;
  return coalesce(released, false);
end $$;
