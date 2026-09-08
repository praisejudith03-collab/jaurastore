# Apply the schema from a phone

These 16 numbered SQL files are generated from the `-- SECTION:` banners in
`supabase_schema.sql`. Concatenating **only the numbered `.sql` files**, in
numeric order, reproduces the source byte-for-byte. Each is a complete SQL
batch (no split statements or function bodies), under 6 KB, and idempotent.
Run them **in numeric order**: later batches depend on tables created earlier;
“independently runnable” means a separate SQL Editor query, not arbitrary order
on an empty database. Edit the source, not these generated SQL files.

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
   09 product_compatibility → 10 admin_credentials → 11 delivery_zones →
   12 coupon_redemptions → 13 product_reviews → 14 delivery_seeds →
   15 storage → 16 stock.

   Use the filenames in this directory, including their numeric prefixes.
   There is no need to run the full schema again after all 16 succeed.
7. In a final **new query**, run these read-only checks:

   ```sql
   select id, name, public from storage.buckets order by id;
   select table_name from information_schema.tables
   where table_schema = 'public'
     and table_name in ('products', 'orders', 'receipts', 'product_reviews',
                        'coupon_uses', 'admin_users', 'admin_reset_tokens',
                        'delivery_zones')
   order by table_name;
   ```

   The schema provisions only `uploads`. Existing other buckets are **not
   deleted**. If `uploads` already exists, its visibility is **not changed**;
   if it is not public, stop and review rather than changing it blindly.
   The `receipts` entry in the table check is a **database table**, not a bucket.

## Important storage limitations

- New receipts use `uploads/proofs/...` with 128-bit random path tokens.
- `uploads` is public. **Anyone possessing a direct public object URL can
  access that file. Signed URL expiry does not make a public object private.**
- The application's receipt listing requires Admin authentication and is marked
  `private, no-store`. Do not share receipt URLs or put them in public catalog data.
- Known legacy URLs in `uploads` can be refreshed for the Admin view. Other
  legacy URLs are preserved unchanged; an expired unsupported signed URL may
  require separate operator review. No legacy objects are moved or deleted.
- No live database or Storage changes were applied by generating these files.
  Applying section 15 yourself provisions uploads if absent and restricts its
  named write policy to `service_role`; it does not delete existing objects.
- **Do not run the image migration**, trigger its workflow, or set `dry_run=false`
  as part of these SQL steps. Do not run the obsolete receipt migration.

## Developer verification

```sh
python3 tools/split_schema.py          # regenerate from schema banners
python3 tools/split_schema.py --check  # read-only drift detection
```
