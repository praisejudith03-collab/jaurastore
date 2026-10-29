# Supabase Section 21 + manual purge of unwanted items (e.g. "Clothing")

**For the owner — run this yourself in the Supabase SQL Editor.**
It unblocks Admin category deletion ("Supabase category deletion is not
ready. Apply schema section 21 and retry") and makes deletions permanent:
a deleted category ID is tombstoned in the database and can never be
re-created by a stale admin tab, a seed, or a re-import.

> ⚠️ A tombstone is **forever**. Once `clothing` is deleted this way, the
> ID `clothing` can never be used again. A future clothes category would
> need a new ID (e.g. `apparel`). If you are not sure, use **Hide** in
> Admin instead — hiding is reversible.

---

## Step 0 — before you paste anything (2 minutes)

1. Open <https://supabase.com/dashboard>, sign in, open the **Jaurastore**
   project. Confirm the project reference in the URL matches the host in
   Render's `SUPABASE_URL` (running SQL on the wrong project is the
   classic way to "apply a migration to nothing").
2. Open **SQL Editor → New query**, role **postgres**, Primary Database.
3. Read-only checks — paste, Run, note the answers:

```sql
select to_regclass('public.deleted_categories') as tombstone_table;
select proname from pg_proc
 where proname in ('hard_delete_categories', 'reject_deleted_category_id');
select count(*) as clothing_products
  from public.products
 where category = 'clothing' or category_id = 'clothing';
```

If `tombstone_table` is `deleted_categories` and both functions are
listed, Section 21 is already applied — skip to Step 3.

## Step 1 — apply Schema Section 21 (exact snippet)

Paste this **entire** block into a new, empty query and press **Run**.
Expect `Success. No rows returned`. It is idempotent (safe to re-run)
and modifies no existing category or product rows.

```sql
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
```

## Step 2 — verify Section 21 landed

New query:

```sql
select to_regclass('public.deleted_categories') as tombstone_table;
select proname from pg_proc
 where proname in ('hard_delete_categories', 'reject_deleted_category_id');
select tgname from pg_trigger where tgname = 'categories_reject_deleted_id';
select * from public.deleted_categories;   -- empty is correct for now
```

All four must answer without error. (From a terminal with the prod
secrets you can also run `python3 verify_schema.py` — it now probes
`deleted_categories` too.)

## Step 3 — move products out of "Clothing" first

Never tombstone a category that still owns products, or those products
point at a category that no longer exists. In **Admin → Products**,
filter by Clothing and either reassign each product's category or send
it to the Trash. Confirm with:

```sql
select id, name from public.products
 where category = 'clothing' or category_id = 'clothing';
-- must return 0 rows before Step 4
```

## Step 4 — delete "Clothing" (preferred: in Admin)

Admin → Categories → remove the **Clothing** row → **Save**.
The server now: upserts the remaining categories, calls
`hard_delete_categories(['clothing'])`, which inserts the tombstone and
deletes the table row in one transaction, and updates the category
details. Do **not** just delete the row in the Table Editor — the
details copy would resurrect it on the next save.

Emergency manual equivalent (only if Admin still refuses after Step 2
passes — then immediately do one ordinary category **Save** in Admin
afterwards so the details copy is cleaned too):

```sql
select public.hard_delete_categories(array['clothing']);
```

## Step 5 — verify it can never come back

```sql
select * from public.deleted_categories;        -- 'clothing' is listed
select id, name from public.categories order by id;  -- no 'clothing'
```

Then open the shop: Clothing must be gone from the filter pills and the
categories page. Trying to re-add a category with ID `clothing` in
Admin must now be refused — that refusal is the tombstone working.

## Old products (same pattern, different button)

Delete in Admin sends a product to the **Trash Bin** (restorable,
invisible everywhere). Trash → **Delete permanently** destroys the row
+ media and tombstones the product ID. That permanent step needs its
own SQL (`hard_delete_products.sql` in the repo root) applied the same
way if it has not been — check with
`select proname from pg_proc where proname = 'hard_delete_products';`
