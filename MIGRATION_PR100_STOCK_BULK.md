# Migration (PR #100): variant-aware stock RPCs + per-product bulk discount columns

**What this changes:** two things the PR #100 code depends on.

| change | what it is | what breaks without it |
| --- | --- | --- |
| `products."bulkQty"` / `products."bulkPercent"` | two new nullable integer columns (the per-product bulk discount: order MORE than `bulkQty` units → `bulkPercent`% off) | **every product save fails** — the new app writes these columns on every save and PostgREST answers PGRST204 (column not found) |
| `reserve_product_stock` / `release_product_stock` | the checkout stock functions gain a third parameter `p_option` so a reservation guards **the chosen variant** as well as the product total, in one atomic `UPDATE` | per-variant stock is not enforced on the shop floor; variant oversells stay possible |

**Time:** about two minutes. **Downtime:** none — the whole script runs in one
transaction; a checkout that arrives mid-run waits a few milliseconds on a
lock and then proceeds against the new functions.

**Order matters:** run this **before** merging/deploying PR #100. The old
deployed code keeps working after the migration (it calls the functions with
two arguments and `p_option` simply takes its default), but the new code does
not work before it.

---

## Why the script drops the old functions first

The new signatures take a **third parameter**. In PostgreSQL that is a *new
overload*, not a replacement: `create or replace` alone would leave the old
two-argument versions in place next to the new ones, and PostgREST cannot
choose between overloads that differ only by a defaulted parameter — every
checkout call would fail with *“Could not choose the best candidate function”*
(PGRST203). The `drop function if exists` lines remove the old signatures so
exactly one function of each name remains. They are a no-op on a database
that never had the old versions.

The canonical `supabase_schema.sql` deliberately contains no `DROP` (a test
enforces that the file is paste-safe), which is why this migration lives here
instead of there.

---

## Step 1 — open the Supabase SQL Editor

1. Go to **https://supabase.com/dashboard** and sign in.
2. Pick the correct organization, then the **Jaurastore** project. Check the
   project reference in the URL matches the host in your configured
   `SUPABASE_URL` — this is the one step worth double-checking, because a
   second/old project is the classic way to “run the migration on nothing”.
3. In the left sidebar, click **SQL Editor** (the terminal/prompt icon).
4. Click **+ New query** (top left of the editor pane).

## Step 2 — paste and run the migration

Copy **everything** in the block below, paste it into the editor, and press
**Run** (or Ctrl/Cmd + Enter). Expect `Success. No rows returned`.

```sql
begin;

-- 1. The two per-product bulk-discount columns (nullable = not configured).
alter table products add column if not exists "bulkQty"     integer;
alter table products add column if not exists "bulkPercent" integer;

-- 2. Remove the old two-argument stock functions. Adding a parameter is a
--    NEW OVERLOAD, not a replacement: with both present PostgREST answers
--    "Could not choose the best candidate function" on every call. No-op
--    on a database that never had the old versions.
drop function if exists reserve_product_stock(text, integer);
drop function if exists release_product_stock(text, integer);

-- 3. The variant-aware replacements.
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

commit;
```

If any statement fails, the `begin; … commit;` wrapper rolls the whole thing
back — nothing is left half-applied. Fix nothing by hand: check the error,
and re-run the whole script (every statement in it is idempotent).

## Step 3 — verify

In the same SQL Editor, run each block separately:

```sql
-- (a) the two columns exist and are nullable integers
select column_name, data_type, is_nullable
  from information_schema.columns
 where table_name = 'products'
   and column_name in ('bulkQty', 'bulkPercent')
 order by column_name;
```
Expected: two rows — `bulkPercent | integer | YES` and `bulkQty | integer | YES`.

```sql
-- (b) exactly ONE reserve and ONE release function, each with 3 parameters
select p.proname, pg_get_function_identity_arguments(p.oid) as args
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
 where n.nspname = 'public'
   and p.proname in ('reserve_product_stock', 'release_product_stock')
 order by p.proname;
```
Expected: exactly **two rows**, each ending in `p_option text DEFAULT NULL`.
If you see four rows, the old two-argument versions are still there — run the
`drop function if exists …` lines from Step 2 again and re-check.

```sql
-- (c) the functions execute (touches nothing: unknown id → false)
select release_product_stock('migration-probe-does-not-exist', 1, null);
```
Expected: one row, `false`.

You can also confirm visually:
- **Table Editor → products** → scroll right: `bulkQty` and `bulkPercent`
  columns appear at the end, empty (empty = no per-product discount).
- **Database → Functions** → `reserve_product_stock` and
  `release_product_stock` show `p_id, p_qty, p_option`.

## Step 4 — deploy

Merge PR #100 and deploy as usual. The columns may stay empty — the admin
editor writes them only when you fill in a per-product bulk discount.

---

## Rollback (only if you must revert the app to pre-PR-100 code)

The pre-PR functions work fine against the new columns (they never touch
them), so a rollback of the app does **not** require reverting the database.
If you ever want the old two-argument functions back anyway:

```sql
begin;
drop function if exists reserve_product_stock(text, integer, text);
drop function if exists release_product_stock(text, integer, text);

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
commit;
```

Leave the `bulkQty` / `bulkPercent` columns in place if you do this — they are
inert when unused.
