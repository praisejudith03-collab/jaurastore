# Apply the schema from a phone

These 21 numbered SQL files are generated from the `-- SECTION:` banners in
`supabase_schema.sql`. Concatenating **only the numbered `.sql` files**, in
numeric order, reproduces the source byte-for-byte. Each is a complete SQL
batch (no split statements or function bodies), under 6 KB, and idempotent.
Run them **in numeric order**: later batches depend on tables created earlier;
“independently runnable” means a separate SQL Editor query, not arbitrary order
on an empty database. Edit the source, not these generated SQL files.

## Delivery seed repair: deployment hold

If section 14 failed with `null value in column "zone_name"`, **do not rerun
section 14 until the compatibility repair PR is merged into `main`**. The
corrected section detects the legacy column and supplies both `name` and
`zone_name` for new rows only. Fresh tables keep using `name` alone. Existing
rows (including Admin-edited data and null current names) are not backfilled,
renamed, or overwritten; conflicts on IDs or unique names are skipped.

This repair does not require dropping or replacing `delivery_zones`, or rerunning
the earlier successful sections. Section 15 now provisions separate public catalogue and private receipt storage.
Section 16 remains outside this storage/category repair. Do not run the image
migration or set `dry_run=false`; the general sequence below is not approval
to resume that paused workflow.

## Legacy zone_name on the live delivery_zones table

The live table predates the zone editor and still has
`zone_name text not null` with no default — the same column section 14
detects and populates when it seeds. That made the seeds work while every
Admin → Delivery zone save failed with
`null value in column "zone_name" … violates not-null constraint` (23502),
and because Supabase is the source of truth the SQLite mirror was never
written either.

The application no longer requires a schema change to save:
`delivery.save_zone()` repairs its payload against the live table (23502 is
answered by filling the named column from the zone, an unknown column is
dropped, a wrong type is retried as 0/false — bounded, and the discovered
shape is cached per worker). If the table still cannot be satisfied the
save fails loudly naming the column, and nothing is written anywhere.

If you prefer to fix the live table instead (or in addition), run this one
**non-destructive** statement in the Supabase SQL editor — no deploy
needed, no data touched:

```sql
alter table delivery_zones alter column zone_name drop not null;
```

It only removes the NOT NULL requirement; existing rows, defaults and the
rest of the schema are unchanged. The app fills `zone_name` for old readers
when it must; it never reads it. Optionally confirm afterwards that no
other legacy NOT NULL column is waiting behind `zone_name` (read-only):

```sql
select column_name, is_nullable, data_type
from information_schema.columns
where table_name = 'delivery_zones'
order by ordinal_position;
```

## Mobile steps

1. Open this directory on GitHub on branch `arena/01a07fac-jaurastore` (after
   merge, use `main`). Open `01_products.sql`, then tap **Raw** (it may be in
   the file's **…** menu). Long-press the SQL text → **Select all** → **Copy**.
   Copy the SQL text only, not the GitHub page, line numbers, or Markdown fences.
2. In a second browser tab, open **https://supabase.com/dashboard**. Sign in,
   choose the correct organization and **Jaurastore project**. Verify the
   project reference against the host in your configured `SUPABASE_URL`.
3. Open the navigation menu **☰ → SQL Editor → New query** (or **+**). If the
   editor is cramped, rotate to landscape or use the browser's **Desktop site**
   option. Use the project's **Primary Database**, with the **postgres** role.
   Do not paste any application keys or credentials into a query.
4. Name the query `Jaura 01 products`. Paste the **entire** file into the empty
   editor. Check the first and last lines match GitHub; keep all `DO $$` blocks
   and final semicolons intact. Deselect any highlighted fragment before running.
5. Tap **Run**. Wait for **Success. No rows returned** (or another success
   result). If there is an error, **stop**, record the error and section number,
   correct the cause, and rerun that section before proceeding. Do not bypass
   an error by dropping tables or deleting rows.
6. Create a **new, empty query** for each next file and repeat steps 1, 4, and 5
   in exactly this order:

   01 products → 02 orders → 03 receipts → 04 referrals → 05 coupons →
   06 growth_settings → 07 site_settings → 08 categories →
   09 product_compatibility → 10 customer_accounts → 11 delivery_zones →
   12 coupon_redemptions → 13 product_reviews → 14 delivery_seeds →
   15 storage → 16 stock → 17 analytics → 18 brand_assets →
   19 site_settings_welcome → 20 site_settings_social →
   21 hard_delete_categories.

   Use the filenames in this directory, including their numeric prefixes.
   Apply section 21 before deploying code that requires category tombstones.
7. In a final **new query**, run these read-only checks:

   ```sql
   select id, name, public from storage.buckets order by id;
   select table_name from information_schema.tables
   where table_schema = 'public'
     and table_name in ('products', 'orders', 'receipts', 'product_reviews',
                        'coupon_uses', 'customers',
                        'delivery_zones')
   order by table_name;
   ```

   `uploads` is for public catalogue/media assets; `receipts` is a separate
   private Storage bucket. The `receipts` entry in the table check is also a
   database table, not a bucket.

## Storage repair and privacy

- Section 15 preserves the visibility of an existing `uploads` bucket. Its
  explicit read policy excludes the legacy `proofs/` folder, while all new
  payment evidence is stored in private `receipts/proofs/` and served with
  expiring signed URLs.
- After applying section 15 and deploying the matching application code, run
  `python tools/repair_supabase_storage.py` with production Supabase environment
  variables for a read-only dry run. Review that it points to the correct
  project. Then, during a maintenance window, run
  `python tools/repair_supabase_storage.py --apply`. It copies every legacy
  `uploads/proofs/` object to the private bucket, updates `orders.proof_url`
  and `receipts.file_url`, removes the old copies, and only then makes
  `uploads` public so product/category images and Gmail thumbnails load.
  The operation stops before changing bucket visibility if a referenced file
  or database update cannot be confirmed; rerunning is safe.
- Do not manually set `uploads.public=true` before the repair script succeeds:
  that would expose any old payment-proof object still in that bucket.
- Section 21 creates the immutable category-deletion ledger and atomic hard-
  delete RPC. If a category ID is tombstoned, it cannot be recreated or seeded
  again; choose a new ID for a genuinely new category.
- No live database, Storage objects, or production bucket settings were
  inspected or changed during this code task. Production completion requires
  applying the SQL sections, deploying the app, running the repair, and
  verifying public image URLs plus signed receipt links in the actual project.
- Do not run the image migration, trigger its workflow, or set `dry_run=false`
  as part of these SQL steps. Do not run the obsolete receipt migration.

## Developer verification

```sh
python3 tools/split_schema.py          # regenerate from schema banners
python3 tools/split_schema.py --check  # read-only drift detection
python3 -m pip install -r requirements.txt pillow pyyaml pgserver==0.1.4
python3 -m pytest tests/ -q            # complete suite, including PostgreSQL regression
```

The delivery seed regression tests use PostgreSQL 16 binaries bundled by the
**test-only** `pgserver` dependency. They start a disposable Unix-socket-only
cluster, reproduce the old `zone_name NOT NULL` failure, and exercise sections
11/14 in rolled-back transactions. They never connect to Supabase, use deployment
credentials, or execute sections 15/16. Run these tests as a non-root user.
