# Catalogue recovery runbook (production)

The fix for **"Photo update could not be saved. The previous saved photo is
safe. That product is no longer available."** and for the products that
disappeared from the shop.

The database is the only place this can be repaired: the app can restore rows
through the guarded functions once they exist, but the write-lock itself is
DDL and must be applied once, by hand, in the Supabase SQL editor.

Project: `rvkweyipqgsggcnimhxf` → **SQL editor** → new query.

## 1. Run the two files, in this order

| # | File | Why |
| --- | --- | --- |
| 1 | [`hard_delete_products.sql`](hard_delete_products.sql) | Creates the permanent-delete ledger `deleted_products` and `hard_delete_products(text[])`. Safe to re-run; skip it only if the function already exists. |
| 2 | [`recover_unavailable_products.sql`](recover_unavailable_products.sql) | Adds the availability columns, installs the catalogue write-lock, restores every hidden product and runs the recovery immediately. Idempotent. |

`recover_unavailable_products.sql` opens with `begin;` and ends with `commit;`,
so paste the **whole file** at once. Its last statement calls the recovery and
prints a report like:

```json
{"ok": true, "restored": 12, "restoredIds": ["jau-...", "..."],
 "skippedPermanent": 3, "repairedImages": 4, "prunedDeletedIds": 12,
 "reason": "master fix 2026-10-22"}
```

`restored` is the number of products back on the shop. `skippedPermanent`
counts products that were deliberately hard-deleted (they stay deleted).
`repairedImages` counts covers recovered from the row's own gallery.

## 2. What the recovery does to a row

```sql
is_deleted = false
is_active  = true
online     = true
source     = 'admin'          -- only when it said 'deleted' / 'replaced'
image_url  = <gallery photo>  -- only when the cover was empty
```

Nothing is uploaded, re-created or renamed: the photos, prices, categories and
variants already attached to the row are reused. The ids are also removed from
the legacy `growth_settings.deleted_product_ids_json` suppression list, which
is what otherwise kept a restored row hidden from the storefront.

`is_active` is the column the owner's SQL asked for. The live table used
`online` + `source='deleted'` instead, so the migration **adds** `is_active`
and keeps the three flags in lock-step from now on; every reader treats any one
of them as "hidden".

## 3. The write lock (what changes for background scripts)

A trigger on `public.products` refuses, for anything that is not an admin
action:

* setting `online`, `is_active` or `is_deleted` to a hidden value,
* setting `source` to `deleted` / `replaced`,
* blanking `category_id`, `image_url` or `image`,
* deleting the row at all.

A **complete admin save** still goes through: the portal writes the whole row
(name, cover, price, `source='admin'`, a freshly stamped `updated_at`). The
decisive signal is the fresh `updated_at`, not the row's shape, because a
background ping carries the same columns.

Supplier checkers, catalog auditors and sync pings may write **only**
`supplier_status` (`out_of_stock` | `low_stock` | `ok`). Every blocked attempt
is written to `public.product_guard_events` with the column, the old value and
the attempted value.

Admin operations that legitimately change availability run through
`SECURITY DEFINER` functions, which set a transaction-local flag:

```sql
select public.admin_trash_products(array['jau-...']);       -- Trash Bin
select public.admin_restore_products(array['jau-...']);     -- Restore
select public.admin_set_product_online(array['jau-...'], false); -- Hide
select public.recover_unavailable_products();               -- everything back
```

## 4. Verify

```sql
-- Everything is live again
select count(*) filter (where is_deleted)                        as soft_deleted,
       count(*) filter (where coalesce(is_active, true) = false) as inactive,
       count(*) filter (where online is false)                  as hidden,
       count(*) filter (where nullif(btrim(coalesce(image_url, '')), '') is null)
                                                                as missing_cover
  from public.products;

-- The lock is installed
select tgname from pg_trigger
 where tgrelid = 'public.products'::regclass and not tgisinternal;

-- What the guard blocked today
select product_id, event, field, attempted_value, at
  from public.product_guard_events
 order by at desc limit 50;
```

Then open **Admin → Settings → Advanced → Catalogue recovery & write lock**:

* **Check catalogue** shows `products · unavailable · without a photo ·
  relative cover URL · write lock active`.
* **Recover unavailable products** runs the same recovery through the API
  (`POST /api/admin/catalog/recover`) — use it after a future mishap instead of
  the SQL editor.
* **Make cover URLs absolute** rewrites legacy `/uploads/<key>` covers to
  `https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/uploads/<key>`.

## 5. Rollback

The recovery is an update, never a delete, and every restored row is logged
with its before-state:

```sql
select product_id, at, detail
  from public.product_guard_events
 where event = 'recovered'
 order by at desc;
```

To re-hide a single product, use the admin **Hide** button (or the
`admin_set_product_online` RPC) — that is the supported path and it is what
keeps the lock meaningful. To remove the lock itself:

```sql
drop trigger if exists products_catalog_guard_write on public.products;
drop trigger if exists products_catalog_guard_delete on public.products;
```
