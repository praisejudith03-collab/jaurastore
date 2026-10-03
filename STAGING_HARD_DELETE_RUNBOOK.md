# Staging runbook: products schema + hard delete

The operator procedure for a **disposable, non-production Supabase project**
that is missing `public.products`. Every step is manual and runs in the
Supabase SQL editor (or the Admin portal) because the migration needs database
access that is deliberately not available to a build sandbox.

> **Nothing in this file has been applied to any live project.** The SQL is
> covered by disposable-PostgreSQL tests
> (`tests/test_hard_delete_sql.py`, including the staging bootstrap recipe) and
> the app paths by `tests/test_deleted_products_stay_deleted.py`,
> `tests/test_site_settings_supabase.py` and
> `tests/test_image_replacement_persistence.py`; the live steps below still
> have to be run by a human and their output recorded.

## Safety rules

1. Confirm the project is a disposable staging project before the first write.
   A missing `public.products` does **not** prove the rest of the database is
   empty.
2. Never paste keys, passwords or database URLs into a query or a chat. The
   application reads `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` from the
   server environment only.
3. Do **not** run `supabase_schema.sql`. Apply only the approved, non-held
   sections named below.
4. Sections **15 (storage)** and **16 (stock)** stay on hold, and the image
   migration must not be run (`dry_run=false` is forbidden).
5. Never reuse an id that has been tombstoned.

## Step 0 - read-only preflight

Run these in the SQL editor. **Stop** if the answers say this is not staging,
if `public.products` already exists with data, or if the table list contains
anything you did not expect to be disposable.

```sql
-- 0a. which database/role is this? Compare the project with the host inside
--     SUPABASE_URL in the SERVER environment (never print the key itself).
select current_database() as db, current_user as role;

-- 0b. the table the whole procedure is about
select to_regclass('public.products') as products_table;   -- expect: NULL

-- 0c. what actually exists already
select table_name
  from information_schema.tables
 where table_schema = 'public'
 order by table_name;

-- 0d. anything catalog-shaped that already holds rows is a red flag. Guarded,
--     because referencing an absent table fails the query before any WHERE.
do $$
declare n bigint;
begin
  if to_regclass('public.products') is null then
    raise notice 'public.products does not exist';
  else
    execute 'select count(*) from public.products' into n;
    raise notice 'public.products rows: %', n;
  end if;
  if to_regclass('public.growth_settings') is null then
    raise notice 'public.growth_settings does not exist';
  else
    execute 'select count(*) from public.growth_settings' into n;
    raise notice 'public.growth_settings rows: %', n;
  end if;
end $$;

-- 0e. storage buckets (read-only; do not change visibility)
select id, name, public from storage.buckets order by id;
```

If `public.products` is absent but the rest of the project is **not** clearly
disposable, stop here and get that confirmed in writing before continuing.

## Step 1 - apply the approved sections, in this order

Paste each file from `schema_sections/` into a **new** SQL editor query, one at
a time, and wait for success before the next. Both are idempotent; a retried
query is safe.

| Order | File | Why it is needed |
| --- | --- | --- |
| 1 | `01_products.sql` | Creates `public.products` (canonical `id text` primary key) and repairs any older hand-built table add-only. |
| 2 | `09_product_compatibility.sql` | Products-only repair: `"legacyId"`, `image_url`, `stock_quantity` + the non-negative check and `sync_supplier_stock`. |

**Not applied:** 15 (storage), 16 (stock) and the image migration are on hold.
After these two queries:

```sql
select to_regclass('public.products') as products_table;         -- expect public.products
select column_name, data_type
  from information_schema.columns
 where table_schema = 'public' and table_name = 'products'
   and column_name in ('id', 'legacyId', 'image_url', 'stock_quantity', 'images')
 order by column_name;
```

`id` must be `text`. If it is anything else (for example `uuid`), stop: the
delete migration's foreign keys are declared `text` and cannot be attached to
another type.

## Step 2 - apply the delete migration

Paste all of `hard_delete_products.sql` into one new query and run it. It is
idempotent. It creates `product_variants` / `product_prices` /
`product_options` if absent, attaches `ON DELETE CASCADE` to every per-product
table it can, and creates `deleted_products` plus `hard_delete_products(text[])`.

## Step 3 - verify the function and its privileges (read-only)

```sql
-- exists, with the exact signature the app calls
select p.oid::regprocedure as signature
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
 where n.nspname = 'public' and p.proname = 'hard_delete_products';

-- service_role may execute it; anon and authenticated may not (Expect: t, f, f)
select has_function_privilege('service_role',
         'public.hard_delete_products(text[])', 'EXECUTE') as service_role_can,
       has_function_privilege('anon',
         'public.hard_delete_products(text[])', 'EXECUTE') as anon_can,
       has_function_privilege('authenticated',
         'public.hard_delete_products(text[])', 'EXECUTE') as authenticated_can;

-- the tombstone ledger exists and is not readable by the public roles
select to_regclass('public.deleted_products') as ledger;
```

Expected on Supabase: signature `hard_delete_products(text[])`, privileges
`t, f, f`, ledger `deleted_products`. The migration's `service_role` grant is
guarded by the role existing, so if `service_role_can` is false, re-run
`hard_delete_products.sql` (the roles always exist on Supabase; a bare
PostgreSQL without them would silently skip only the grant).

## Step 4 - live test with one disposable product

Use a unique id, e.g. `jau-staging-<UTC timestamp>`, and dummy uploaded
images. Never use a real catalogue id.

1. **Create** the disposable product in `/admin` with a dummy photo, save it.
2. **Replace its photo** through the normal Admin flow (the editor publishes a
   media edit). Verify:
   - the save response / reload shows the new gallery, `image` and `image_url`
     in sync;
   - the old object disappears from the bucket **only** after the new one is
     saved, and **only** if no other product references it;
   - the product still exists (an image replacement is a save, never a
     hard-delete; the id must not be tombstoned).
3. **Delete** the disposable product through `/admin`. Verify:
   - the response is `ok: true` with `deleteMode: "supabase-hard"` (a local-only
     delete reports `"local-only"` and must not be mistaken for a Supabase one);
   - the row is gone:
     ```sql
     select count(*) from public.products
      where id = 'jau-staging-<UTC timestamp>';                 -- 0
     select * from public.deleted_products
      where product_id = 'jau-staging-<UTC timestamp>';         -- 1 tombstone row
     ```
   - child rows are gone (`product_variants`, `product_prices`,
     `product_options`, `variant_stock`, `product_reviews`, `product_views`,
     `featured_products` - only for tables that exist);
   - the product's unshared Storage object is purged. A 503 with a `report`
     means the row was removed but a cleanup step failed: **retry** so no media
     or tombstones are left behind.
4. Do **not** reuse the id: the trigger rejects it with
   `product id ... was permanently deleted`, by design.

## Step 5 - what to record

For each step: the query/file, the project reference (never a key), the result,
and any error verbatim. Anything not run must be reported as not run -
"CI passed" and "the tests pass locally" are not live verification.
