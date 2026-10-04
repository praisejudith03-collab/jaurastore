# Staging runbook: products schema + hard delete

The operator procedure for a **disposable, non-production Supabase project**
that is missing `public.products`.

Two ways to run it:

* **Automated (recommended).** Merge
  `.github/workflows/staging-schema-migration.yml` to `main`, then run the
  **Staging schema migration (products + hard delete)** workflow from the
  Actions tab. It applies the three approved files with the credentials
  already stored as repository secrets and verifies the result. See
  [Automated path](#automated-path-github-actions) below.
* **By hand.** The SQL editor steps in this document.

Neither path runs `supabase_schema.sql` as a whole, sections 15/16, or the
image migration. Do not "just run everything": sections 15 and 16 are on hold
per `schema_sections/README.md`, and 16 ends in a `validate constraint` that
fails on any existing row with negative stock.

## Automated path (GitHub Actions)

The workflow is guarded: it refuses a project ref that does not match
`SUPABASE_URL`, defaults to a **read-only preflight**, and refuses to write
when `public.products` already holds rows unless you say otherwise.

1. **Add the DDL credential** (Settings → Secrets and variables → Actions).
   One of:

   | Secret | How to get it | Transport |
   | --- | --- | --- |
   | `SUPABASE_DB_URL` | Dashboard → Project Settings → Database → Connection string (URI). The same credential `python3 migrate_supabase.py --schema` documents. | `psql` |
   | `SUPABASE_ACCESS_TOKEN` | Supabase → Account → Access Tokens → Generate new token. | Management API (no `psql`) |

   `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are already configured for
   the other workflows - the service-role key is **not** enough here, because
   PostgREST does not expose DDL.

2. **Run the workflow with `apply = false`** and your project ref typed into
   `confirm_project_ref`. This is read-only: it prints the existing tables, the
   `products` row count, and whether the delete function is already there.
   Compare that output with Step 0 below before going further.
3. **Run it again with `apply = true`** (and `allow_existing_rows = true` only
   if the preflight proved the existing rows are disposable). It applies
   `01_products.sql` → `09_product_compatibility.sql` →
   `hard_delete_products.sql`, then verifies `to_regclass`,
   `to_regprocedure('public.hard_delete_products(text[])')`, the
   `deleted_products` ledger and the execute privileges.
4. **Optional, and the real end-to-end proof:** run it once more with
   `apply = true` and `live_purge_check = true`. That adds a disposable
   product (`jau-staging-check-<uuid>`, `online=false`) plus one dummy object,
   deletes them through the same function the Admin API calls, and verifies
   from the outside that the row is gone, the object 404s and the id cannot be
   re-created. Nothing else is touched.

From a server shell with the same environment variables you can run the two
tools directly:

```sh
python3 tools/apply_staging_schema.py --confirm-project-ref <ref>            # read-only
python3 tools/apply_staging_schema.py --confirm-project-ref <ref> --apply    # write
python3 tools/staging_delete_check.py --confirm-project-ref <ref>            # live check
python3 tools/staging_delete_check.py --dry-run                              # plan only
```

The manual steps below remain the fallback and the reference for what the
automated path does.

> **Nothing in this repository's tooling has been run against a live
> project.** The SQL is covered by disposable-PostgreSQL tests
> (`tests/test_hard_delete_sql.py`, including the staging bootstrap recipe) and
> the app paths by `tests/test_deleted_products_stay_deleted.py`,
> `tests/test_site_settings_supabase.py`,
> `tests/test_image_replacement_persistence.py` and
> `tests/test_hard_delete_live_path.py` (which drives the real store against
> real PostgreSQL); the live steps below still have to be run by a human and
> their output recorded.

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
3. **Delete** the disposable product through `/admin`. The portal now answers
   before the heavy half runs, so there are two things to verify - the reply,
   and the job that finishes it.
   - the reply is `ok: true` with `deleteMode: "queued"` and a `jobId`
     (a local-only delete reports `"local-only"` and must not be mistaken for a
     Supabase one). `deleted: true` here means *hidden and tombstoned*, not
     "the row is already gone" - the request deliberately does not wait for the
     RPC;
   - the delete is confirmed in the job panel (`/admin` -> Marketing ->
     "Background jobs", or `GET /api/admin/tasks/jobs`): the same `jobId`
     reaches `state: "done"` with `filesRemoved` set. A `state: "failed"` job
     carries the reason and can be retried from the panel; the product stays
     hidden either way because the tombstone was already written;
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
   - the product's unshared Storage object is purged. A job that ends
     `partial` means the row was removed but a media/tombstone step failed:
     **retry** from the panel so no media or tombstones are left behind.
   - operators who want the old blocking answer can call the same endpoint with
     `?sync=1`: that request waits for the RPC and answers `deleteMode:
     "supabase-hard"` (`200`, with `filesRemoved`) or a `503` with the RPC
     `report`. Use it when a single request must not return until the row is
     provably gone; use the default when the portal must not time out.
4. Do **not** reuse the id: the trigger rejects it with
   `product id ... was permanently deleted`, by design.

## Step 5 - what to record

For each step: the query/file, the project reference (never a key), the result,
and any error verbatim. Anything not run must be reported as not run -
"CI passed" and "the tests pass locally" are not live verification.

## Step 6 - the rest of the overhaul, in one place

**Broadcast hub (email).** `/admin/marketing/broadcast` opens the Marketing
desk with the Broadcast hub. Three things live there, in order:

1. **Audience** - `GET /api/admin/marketing/broadcast/audience` streams the
   contact book (`total`, `fromOrders`, `fromAccounts`, `remoteOnly`,
   `suppressed`, a `sample`) and the four compose kinds (New Arrivals, Promo,
   Coupon, Appreciation). Suppressed addresses are counted, never listed, and
   never sent to.
2. **Preview** - `POST .../broadcast/preview` renders the exact email
   (`html`, `recipients`) and sends nothing (`dryRun: true`). Always preview
   before queueing: the preview is the only place the real markup is visible.
3. **Queue** - `POST .../broadcast` creates the campaign row with
   `status: "queued"` and answers immediately with `campaignId`,
   `recipientCount` and a `jobId`; the worker sends at most 30 addresses per
   pass, logs every address (`marketing_campaign_sends`) and re-queues itself
   until the list is done. Watch it on the same card (progress) or
   `GET /api/admin/marketing/broadcast/<campaignId>`; stop it with
   `POST /api/admin/marketing/broadcast/<campaignId>/cancel`. A restart is
   safe: the per-address log means a resumed run never re-sends and never
   re-walks the whole list.

Testing mail delivery needs `RESEND_API_KEY` + `MAIL_FROM`; without them the
queued job parks with "Resend is not configured" and the panel shows it.

**Thumbnails for photos uploaded before the queue existed.** New uploads get
their `.400w.webp` companion from the background queue
(`storage.save_image` -> `enqueue_thumbnail`). Older bucket objects do not have
one yet, and that is what the backfill tool is for:

```bash
python3 tools/backfill_thumbs.py            # dry run: what it would write
python3 tools/backfill_thumbs.py --apply    # write the companions
```

It needs `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` in the environment, never
touches `uploads/proofs/*`, is idempotent, and never rewrites an original.

**Background jobs panel.** Every queued deletion and every broadcast pass is
listed with its state (`queued`, `running`, `done`, `failed`) on the Marketing
desk ("Background jobs"), and in `GET /api/admin/tasks/jobs`. A `failed` job
carries the reason and a Retry button; `POST /api/admin/tasks/jobs/<jobId>/retry`
does the same from an integration.
