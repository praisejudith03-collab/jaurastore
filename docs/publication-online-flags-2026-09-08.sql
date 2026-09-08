-- ============================================================================
-- Jaura Store — publication flags for the 53-row Supabase products table
-- Prepared 2026-09-08 from protected run #6 (34241484557) + read-only audit.
--
-- STATUS: PROPOSAL. DO NOT EXECUTE SECTIONS 2–3 UNTIL THE ID LIST IS APPROVED.
--
-- What this does, and only this:
--   * online = true   for the exact 29 approved live IDs
--   * online = false  for the exact 24 non-approved IDs
--   * updated_at = now() on the rows whose online value actually changes
-- What it never does:
--   * no DELETE, no INSERT, no id/legacyId change
--   * no priceNgn / priceCfa / compareNgn / compareCfa change
--   * no stock_quantity / option_stock change
--   * no image_url / images change
--   * no name / name_fr / category / slug / sku / description / badge /
--     featured / colors / options change
--   Every other column is preserved because only `online` and `updated_at`
--   appear in any SET clause below. Both UPDATEs use an explicit allowlist of
--   literal ids (no LIKE, no NOT IN, no subquery over the whole table).
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 0. Allowlists (read these first; they are the whole decision)
-- ----------------------------------------------------------------------------
-- 29 approved live   (real committed photo now on public uploads, price > 0,
--                     stock > 0)
--   wix-011, wix-030, wix-032, wix-033, wix-042, wix-053, wix-054, wix-070,
--   wix-072, wix-079, wix-080, wix-088, wix-091, wix-117, wix-118, wix-163,
--   wix-164, wix-189, wix-198, wix-199, wix-200, wix-201, wix-202, wix-222,
--   wix-228, wix-229, wix-237, wix-244, wix-252
--
-- 24 non-approved -> offline
--   operator-offline (2):  wix-001, wix-012
--   placeholder-only (3):  wix-041, wix-055, wix-197
--   no-image (2):          jau-mtot3318, wix-002
--   test fixtures (17):    jau-mirror-fail, jau-mirror-ok, jau-mirror-post,
--                          jau-stock-a, jau-stock-b, jau-stock-dcf,
--                          jau-stock-dec, jau-stock-del, jau-stock-dnp,
--                          jau-stock-dpn, jau-stock-em2, jau-stock-eml,
--                          jau-stock-idem, jau-stock-opr, jau-stock-opt,
--                          jau-stock-pay, jau-stock-rop
-- 29 + 24 = 53. No id appears in both lists.


-- ----------------------------------------------------------------------------
-- 1. PREVIEW  (SELECT only — safe to run now)
-- ----------------------------------------------------------------------------

-- 1a. Row-by-row: what each of the 53 ids has now and what it would become.
--     `will_change = true` rows are the only ones the UPDATEs would touch.
with allowlist(id, target_online, bucket) as (
  values
    -- approved live (29)
    ('wix-011', true,  'approved_live'), ('wix-030', true,  'approved_live'),
    ('wix-032', true,  'approved_live'), ('wix-033', true,  'approved_live'),
    ('wix-042', true,  'approved_live'), ('wix-053', true,  'approved_live'),
    ('wix-054', true,  'approved_live'), ('wix-070', true,  'approved_live'),
    ('wix-072', true,  'approved_live'), ('wix-079', true,  'approved_live'),
    ('wix-080', true,  'approved_live'), ('wix-088', true,  'approved_live'),
    ('wix-091', true,  'approved_live'), ('wix-117', true,  'approved_live'),
    ('wix-118', true,  'approved_live'), ('wix-163', true,  'approved_live'),
    ('wix-164', true,  'approved_live'), ('wix-189', true,  'approved_live'),
    ('wix-198', true,  'approved_live'), ('wix-199', true,  'approved_live'),
    ('wix-200', true,  'approved_live'), ('wix-201', true,  'approved_live'),
    ('wix-202', true,  'approved_live'), ('wix-222', true,  'approved_live'),
    ('wix-228', true,  'approved_live'), ('wix-229', true,  'approved_live'),
    ('wix-237', true,  'approved_live'), ('wix-244', true,  'approved_live'),
    ('wix-252', true,  'approved_live'),
    -- operator offline (2)
    ('wix-001', false, 'operator_offline'), ('wix-012', false, 'operator_offline'),
    -- placeholder only (3)
    ('wix-041', false, 'placeholder_only'), ('wix-055', false, 'placeholder_only'),
    ('wix-197', false, 'placeholder_only'),
    -- no verifiable image (2)
    ('jau-mtot3318', false, 'no_image'), ('wix-002', false, 'no_image'),
    -- test fixtures (17)
    ('jau-mirror-fail', false, 'fixture'), ('jau-mirror-ok',  false, 'fixture'),
    ('jau-mirror-post', false, 'fixture'), ('jau-stock-a',    false, 'fixture'),
    ('jau-stock-b',    false, 'fixture'), ('jau-stock-dcf',  false, 'fixture'),
    ('jau-stock-dec',  false, 'fixture'), ('jau-stock-del',  false, 'fixture'),
    ('jau-stock-dnp',  false, 'fixture'), ('jau-stock-dpn',  false, 'fixture'),
    ('jau-stock-em2',  false, 'fixture'), ('jau-stock-eml',  false, 'fixture'),
    ('jau-stock-idem', false, 'fixture'), ('jau-stock-opr',  false, 'fixture'),
    ('jau-stock-opt',  false, 'fixture'), ('jau-stock-pay',  false, 'fixture'),
    ('jau-stock-rop',  false, 'fixture')
)
select
  a.bucket,
  a.id,
  p.name,
  p.online                          as online_now,
  a.target_online                   as online_after,
  (p.online is distinct from a.target_online) as will_change,
  p."priceNgn", p."priceCfa", p.stock_quantity,
  p.image_url,
  p.updated_at,
  (p.id is null)                    as MISSING_IN_TABLE   -- must be false for all 53
from allowlist a
left join public.products p on p.id = a.id
order by a.bucket, a.id;

-- 1b. Sanity totals. Expected before the update:
--     rows_in_allowlist = 53, rows_found = 53, table_rows = 53,
--     not_in_allowlist = 0  (any extra row means the table drifted; STOP)
with allowlist(id) as (
  values
    ('wix-011'),('wix-030'),('wix-032'),('wix-033'),('wix-042'),('wix-053'),
    ('wix-054'),('wix-070'),('wix-072'),('wix-079'),('wix-080'),('wix-088'),
    ('wix-091'),('wix-117'),('wix-118'),('wix-163'),('wix-164'),('wix-189'),
    ('wix-198'),('wix-199'),('wix-200'),('wix-201'),('wix-202'),('wix-222'),
    ('wix-228'),('wix-229'),('wix-237'),('wix-244'),('wix-252'),
    ('wix-001'),('wix-012'),
    ('wix-041'),('wix-055'),('wix-197'),
    ('jau-mtot3318'),('wix-002'),
    ('jau-mirror-fail'),('jau-mirror-ok'),('jau-mirror-post'),
    ('jau-stock-a'),('jau-stock-b'),('jau-stock-dcf'),('jau-stock-dec'),
    ('jau-stock-del'),('jau-stock-dnp'),('jau-stock-dpn'),('jau-stock-em2'),
    ('jau-stock-eml'),('jau-stock-idem'),('jau-stock-opr'),('jau-stock-opt'),
    ('jau-stock-pay'),('jau-stock-rop')
)
select
  (select count(*) from allowlist)                                  as rows_in_allowlist,
  (select count(*) from allowlist a join public.products p using (id)) as rows_found,
  (select count(*) from public.products)                            as table_rows,
  (select count(*) from public.products p
     where p.id not in (select id from allowlist))                  as not_in_allowlist,
  (select count(*) from public.products where online is true)       as online_now;

-- 1c. Fingerprint of every column we promise NOT to change. Save this output;
--     re-run after the UPDATE and the two hashes must be identical.
select md5(string_agg(
         concat_ws('|', id, "legacyId", sku, slug, name, "nameFr", category,
                   "priceNgn", "priceCfa", "compareNgn", "compareCfa",
                   stock_quantity, stock, "optionStock"::text,
                   image, image_url, images::text, description, badge,
                   featured, colors::text, options::text,
                   "placeholderImage", "usesPlaceholder", source),
         E'\n' order by id)) as untouched_columns_fingerprint,
       count(*)              as rows_hashed
from public.products;


-- ----------------------------------------------------------------------------
-- 2. THE UPDATE  (do not run until the 29 / 24 lists above are approved)
--    Wrapped in a transaction with assertions: if any count is off, the whole
--    thing rolls back and nothing is published.
-- ----------------------------------------------------------------------------
/*
begin;

-- 2a. Guard: the table must still be exactly the 53 audited ids.
do $$
declare
  n_total int;
  n_unknown int;
begin
  select count(*) into n_total from public.products;
  select count(*) into n_unknown from public.products
   where id not in (
    'wix-011','wix-030','wix-032','wix-033','wix-042','wix-053','wix-054',
    'wix-070','wix-072','wix-079','wix-080','wix-088','wix-091','wix-117',
    'wix-118','wix-163','wix-164','wix-189','wix-198','wix-199','wix-200',
    'wix-201','wix-202','wix-222','wix-228','wix-229','wix-237','wix-244',
    'wix-252',
    'wix-001','wix-012',
    'wix-041','wix-055','wix-197',
    'jau-mtot3318','wix-002',
    'jau-mirror-fail','jau-mirror-ok','jau-mirror-post',
    'jau-stock-a','jau-stock-b','jau-stock-dcf','jau-stock-dec','jau-stock-del',
    'jau-stock-dnp','jau-stock-dpn','jau-stock-em2','jau-stock-eml',
    'jau-stock-idem','jau-stock-opr','jau-stock-opt','jau-stock-pay',
    'jau-stock-rop');
  if n_total <> 53 or n_unknown <> 0 then
    raise exception 'products table drifted: total=% unknown=% — aborting, nothing changed',
      n_total, n_unknown;
  end if;
end $$;

-- 2b. Offline: the exact 24 non-approved ids. Only rows not already false
--     are touched, so updated_at moves only where online really changes.
update public.products
   set online = false,
       updated_at = now()
 where id in (
    -- operator offline (2)
    'wix-001','wix-012',
    -- placeholder only (3)
    'wix-041','wix-055','wix-197',
    -- no verifiable image (2)
    'jau-mtot3318','wix-002',
    -- test fixtures (17)
    'jau-mirror-fail','jau-mirror-ok','jau-mirror-post',
    'jau-stock-a','jau-stock-b','jau-stock-dcf','jau-stock-dec','jau-stock-del',
    'jau-stock-dnp','jau-stock-dpn','jau-stock-em2','jau-stock-eml',
    'jau-stock-idem','jau-stock-opr','jau-stock-opt','jau-stock-pay',
    'jau-stock-rop')
   and online is distinct from false;

-- 2c. Online: the exact 29 approved ids. Also pins any NULL to true.
update public.products
   set online = true,
       updated_at = now()
 where id in (
    'wix-011','wix-030','wix-032','wix-033','wix-042','wix-053','wix-054',
    'wix-070','wix-072','wix-079','wix-080','wix-088','wix-091','wix-117',
    'wix-118','wix-163','wix-164','wix-189','wix-198','wix-199','wix-200',
    'wix-201','wix-202','wix-222','wix-228','wix-229','wix-237','wix-244',
    'wix-252')
   and online is distinct from true;

-- 2d. Post-conditions inside the transaction. Any failure => rollback.
do $$
declare
  n_online int;
  n_offline int;
  n_bad_live int;
  n_w1 int;
begin
  select count(*) into n_online  from public.products where online is true;
  select count(*) into n_offline from public.products where online is false;
  -- every online row must be fully sellable
  select count(*) into n_bad_live from public.products
   where online is true
     and ( image_url is null
        or image_url not like 'https://%/storage/v1/object/public/uploads/%'
        or "priceNgn" is null or "priceNgn" <= 0
        or "priceCfa" is null or "priceCfa" <= 0
        or stock_quantity is null or stock_quantity <= 0 );
  select count(*) into n_w1 from public.products
   where id in ('wix-001','wix-012') and online is not false;
  if n_online <> 29 or n_offline <> 24 or n_bad_live <> 0 or n_w1 <> 0 then
    raise exception 'post-condition failed: online=% offline=% bad_live=% wix001/012_online=% — rolled back',
      n_online, n_offline, n_bad_live, n_w1;
  end if;
end $$;

commit;
*/


-- ----------------------------------------------------------------------------
-- 3. VERIFY AFTER COMMIT  (SELECT only)
-- ----------------------------------------------------------------------------
-- 3a. Expect: total 53, live 29, live_https 29, live_ngn 29, live_cfa 29, live_stock 29
select
  count(*)                                                            as total_products,
  count(*) filter (where online is true)                              as live_products,
  count(*) filter (where online is true
                     and image_url like 'https://%/storage/v1/object/public/uploads/%')
                                                                      as live_products_with_https_uploads,
  count(*) filter (where online is true and "priceNgn" > 0)           as live_products_with_ngn_prices,
  count(*) filter (where online is true and "priceCfa" > 0)           as live_products_with_cfa_prices,
  count(*) filter (where online is true and stock_quantity > 0)       as live_products_with_stock
from public.products;

-- 3b. Expect: ZERO rows
select id, name, image_url, "priceNgn", "priceCfa", stock_quantity, online
  from public.products
 where online is true
   and ( image_url is null
      or image_url not like 'https://%'
      or "priceNgn" is null or "priceNgn" <= 0
      or "priceCfa" is null or "priceCfa" <= 0
      or stock_quantity is null or stock_quantity <= 0 )
 order by id;

-- 3c. Expect: both online = false, image_url / prices / stock unchanged
select id, online, image_url, "priceNgn", "priceCfa", stock_quantity, updated_at
  from public.products
 where id in ('wix-001','wix-012')
 order by id;

-- 3d. Re-run the fingerprint from 1c. It MUST equal the value captured before.
select md5(string_agg(
         concat_ws('|', id, "legacyId", sku, slug, name, "nameFr", category,
                   "priceNgn", "priceCfa", "compareNgn", "compareCfa",
                   stock_quantity, stock, "optionStock"::text,
                   image, image_url, images::text, description, badge,
                   featured, colors::text, options::text,
                   "placeholderImage", "usesPlaceholder", source),
         E'\n' order by id)) as untouched_columns_fingerprint,
       count(*)              as rows_hashed
from public.products;
