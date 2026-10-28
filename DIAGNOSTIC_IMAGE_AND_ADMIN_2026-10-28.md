# Diagnostic: disappearing product/category images & blocked admin actions

**Date:** 2026-10-28 · **Scope:** image URL resolution/storage, automated
availability writes, admin unhide/save paths · **Status:** root causes fixed,
regressions pinned by `tests/test_image_durability_and_admin_visibility.py`,
full suite green (2 464 passed, 20 skipped; 3 modules that need Playwright /
pgserver were not installed in this sandbox).

---

## 0. Executive summary — the four exact causes

| # | Symptom the owner sees | Exact cause | Where |
|---|---|---|---|
| 1 | “I add photos and they vanish a couple of days later.” “Category tiles lose their picture.” | The **nightly storage sweeper** decided an object was unreferenced from a *catalogue read that cannot see* (a) products in the Trash Bin and (b) category covers stored in Supabase. It then deleted them, permanently. | `scheduler._storage_sweeper` → `storage_cleanup._referenced_keys` |
| 2 | “Some products can’t be saved or un-hidden — I get ‘could not be saved’.” “Category save fails.” | The **production write validator** accepted only absolute `<project>/storage/v1/object/public/uploads/...` covers. Every product whose cover is a **committed repo photo** (`images/products/x.jpg` — what the bundled catalogue, `resolve_image` and the photo-repair path all use) was rejected, so the whole save 503’d. The Admin Hide/Show/Unhide buttons post the *whole* product, so they always took that path. | `supabase_store._upsert_products_resilient` → `_canonical_catalog_image_url` |
| 3 | “My extra photos disappear after a save.” | Same validator: gallery entries that were not bucket URLs were **silently dropped** (`continue`) on every successful save. | `supabase_store._upsert_products_resilient` |
| 4 | “I hide/delete something and it comes back.” “The Trash empties itself.” | The **boot schema migration** ran `UPDATE products SET is_active = true, is_deleted = false, online = true …` on *every* start (and on a retried boot after a failed Admin save), re-publishing hidden products and un-deleting the Trash. | `auto_migrate.PRODUCT_SCHEMA_STATEMENT` (executed from `app.create_app`) |

No scheduled job ever set `is_active = false` / `is_deleted = true`. The
"products get blocked" experience came from **writes being refused** (cause 2),
from rows already carrying a tombstone (`source='replaced'` after a bulk
replace, `is_deleted=true` after a Trash delete) and from media being deleted
underneath a row that still pointed at it (cause 1).

---

## 1. How a product/category image is written, read and deleted

### 1.1 Write path

```
Admin editor (js/store.js upsertProduct)
   └─ POST /api/admin/products            api.py:5591 → _product_save_response  api.py:5370
        ├─ fast path (mergeBase + mergeFields, no media field)
        │     → catalog.upsert(..., _saveDirtyFields)                     catalog.py:3515
        │       → supabase_store.update_product_fields                    supabase_store.py:894
        └─ full path (everything else, including Hide/Unhide)
              → catalog.upsert → catalog.normalize                        catalog.py:669
                → supabase_store.upsert_products
                  → _upsert_products_resilient                            supabase_store.py:781
                      · _canonical_catalog_image_url (cover)   ← REJECTED repo covers
                      · gallery loop                          ← DROPPED non-bucket entries
```

`upsertProduct` only ships `mergeBase`/`mergeFields` when its caller sets them.
The Hide / Show / Unhide handlers spread the whole row:

```js
// js/admin.js:1853, 1873, 1829
const res = await JA.upsertProduct({ ...product, online: !hiding });
const res = await JA.upsertProduct({ ...product, online: true });
const results = await Promise.all(products.map((p) => JA.upsertProduct({ ...p, online })));
```

so they always land on the **full path**, which is exactly the one that
refused committed covers.

### 1.2 Read path

`catalog.merged()` (`catalog.py:1913`) unions Supabase rows + seed rows +
local overrides, then drops tombstones (`_durable_deleted_ids`,
`_supabase_dead_ids`, `is_deleted_product`, `is_permanently_removed`) and
finally runs `resolve_images()` → `catalog.resolve_image` (`catalog.py:224`),
which explicitly treats a **committed repo photo as a first-class cover**
("A committed repo photo wins"). `catalog_health()` (`supabase_store.py:1190`)
also counts `images/…` as a valid cover.

**The reader and the writer disagreed**: the reader publishes repo covers, the
writer refused them. Everything in causes 2 and 3 follows from that
asymmetry.

### 1.3 Delete paths (what actually wiped images)

Three independent deleters:

| Deleter | When | Reference scan |
|---|---|---|
| `scheduler._storage_sweeper` (`scheduler.py:72`) | nightly at 02:00 local, `STORAGE_SWEEPER_NIGHTLY` defaults to `1` | `storage_cleanup._referenced_keys` |
| Admin card / `POST /api/admin/storage/cleanup` (`api.py:6666`) | manual | same |
| `tools/cleanup_supabase_storage.py` (GitHub workflow, manual + protected) | manual | scans raw tables |
| `catalog._purge_removed_media` (`catalog.py:3301`) | on save/delete, when an edit unlinks a file | `storage.delete_upload` → `_referenced_by_a_product` |

`storage_cleanup._referenced_keys` (`storage_cleanup.py:85`) before the fix
collected references from: `catalog.merged(include_hidden=True)`, orders +
receipts, `site_settings`, and `data/categories.json`.

**Two holes:**

1. `catalog.merged()` **never returns a soft-deleted row** (by design — the
   storefront must not show it). The Trash Bin keeps its snapshots in a
   separate `growth_settings` row (`product_trash_json`), which the scan never
   read. So every photo of a trashed product was an orphan after the two-day
   grace period — even though `delete_products_strict` promises "Media and the
   row itself are kept so Restore can bring the piece back".
2. Production serves categories from the Supabase `categories` table + the
   `categories_json` details row, **not** from the repo file. The scan read
   only `data/categories.json` (which holds the shipped artwork paths), so
   **every uploaded category cover was treated as an orphan**. That helper also
   mishandled the file reader, which returns `(data, path)`:

   ```python
   rows = catalog._read_categories_file()      # -> (data, path) TUPLE
   if isinstance(rows, dict):                  # never true
       rows = rows.get("categories") or []
   return rows or []
   ```

Reproduced against a fake production client before the fix
(`_referenced_keys` → `set()`, both objects listed as delete candidates):

```
category cover referenced?  False
trash-bin photo referenced?  False
nightly sweeper delete candidates: ['categories/2026/10/kitchen.jpg',
                                    'products/2026/10/trashed.jpg']
```

`storage.py` already had the correct, production-aware reader
(`_live_category_rows`, `storage.py:1118`, used by `delete_upload`) — the
sweeper simply had its own broken copy.

### 1.4 Did a background script overwrite absolute URLs with relative ones?

Not directly, but the combination was destructive and is now closed:

* `catalog.repair_dead_photos` (scheduler **every 5-minute tick**) re-points a
  row whose uploaded cover is *definitively missing* at the first surviving
  alternative — which may be a committed repo photo (`images/products/…`).
  With the strict validator the write was refused and the function still
  reported `repaired`, so the row kept the dead URL and the card stayed on
  "PHOTO COMING SOON". It now reports `failed` with a reason instead of lying,
  and the write succeeds.
* `stock_guardrail_sweep` (`catalog.py:1082`, every tick) and
  `remirror_strays` (`catalog.py:3480`, every tick) both re-`upsert()` whole
  resolved rows. That is the mechanism by which a resolved (possibly
  repo-relative) cover could be written back over a database value — it is now
  safe because a committed cover is accepted as-is and an absolute bucket URL
  is preserved by `resolve_image` in production.

---

## 2. Availability audit — what toggles `is_active` / `is_deleted`

| Writer | Trigger | Effect |
|---|---|---|
| `auto_migrate.PRODUCT_SCHEMA_STATEMENT` | **every boot** (`app.py:335`) | `is_active=true, is_deleted=false, online=true, source='admin'` for the whole catalogue → **cause 4** |
| `supabase_store.delete_products_strict` | Admin Delete / Trash | `source='deleted', is_deleted=true, is_active=false` via `admin_trash_products` RPC (correct, audited) |
| `supabase_store.set_product_online` | Admin Hide | `admin_set_product_online` RPC (correct) |
| `supabase_store.replace_all_products` | `PUT /api/admin/products` (bulk replace / CSV) | every admin row **not in the payload** becomes `source='replaced'` → hidden by `is_deleted_product`. Latent hazard: a partial list hides the rest — documented below, unchanged (it is an explicit "replace the catalogue" call) |
| `catalog.purge_test_fixtures` / `purge_permanently_removed` | boot, production/staging | tombstones only fixtures / `PERMANENTLY_REMOVED_*` ids |
| `products_catalog_guard` trigger (`recover_unavailable_products.sql`) | on every UPDATE | reverts *background* availability writes and refuses blanking `image_url`/`image`/`category_id`; the admin RPCs set `jaura.catalog_admin` for their own transaction |
| supplier watchdog (`catalog.apply_supplier_status`, `apply_supplier_stock`) | every 20 min | **only** `supplier_status` / stock columns — never availability (verified) |
| stock guardrail (`catalog.stock_guardrail_fix`) | every tick | **only** option-stock totals — never availability (verified) |

So: nothing turns products off automatically. Cause 4 is the opposite
direction, and it is what made Hide/Delete look unreliable.

---

## 3. Admin UI / unhide / save restrictions

1. **Why "Unhide" failed** — cause 2. Verified end-to-end against the real
   Flask app in production configuration with a fake Supabase client:

   ```
   BEFORE: POST /api/admin/products {product: <repo-cover row, online:true>}
           [supabase] products upsert refused: image_url must be an absolute
           public Supabase Storage URL for this project; …
           HTTP 503 {"error": "The product could not be saved to Supabase.
                             No changes were made."}
   AFTER:  HTTP 200 {"ok": true, "action": "updated", …}
   ```

2. **Why "Save" failed on some records only** — the same validator, gated by
   `_enforce_public_catalog_images()`: it only fires on a real
   `*.supabase.co` project outside tests, so the local suite never saw it. The
   refusal applied to the **whole batch**: one bad cover in a bulk write
   refused every row in it.

3. **Error handling was the reason this was hard to diagnose** — every refusal
   was a `print()` plus a bare `False`, so the portal could only say "could not
   be saved" and the server log was the only trace. `_upsert_products_resilient`
   now records the reason (`supabase_store.last_write_blocker()`), the 503
   response carries it as `detail`, and the audit row includes it.

4. **Photo repair reported success without saving** — `repair_dead_photos`
   discarded the `upsert()` result and appended to `repaired` before writing.
   Fixed: only a confirmed, mirrored write counts; everything else lands in
   `failed` with the backend's reason, and the audit line counts it.

5. **Featured products** — the save path itself is sound (verified: `HTTP 200`,
   `growth_settings` upsert). What it cannot do is survive causes 1 and 4: a
   featured product whose photo had been swept showed a placeholder, and a
   restart silently re-published hidden items the owner had curated. Note also
   that `save_homepage_featured` deliberately restores (`online=true`) every
   selected product, and the picker is capped at 12 — both by design.

---

## 4. Fixes implemented

### 4.1 `supabase_store.py` — accept the shop's own covers, keep galleries
* New `_is_committed_catalog_asset()` (`:683`): a scheme-less `images/…` path
  that **exists in the deployed repo** is a durable cover. A made-up relative
  path, an unverified `/uploads/…` link, `data:`/`blob:` and foreign hosts are
  still refused — `tests/test_storage_supabase.py::
  test_catalog_persistence_refuses_ephemeral_image_paths` still passes
  unchanged.
* `_canonical_catalog_image_url()` (`:712`) returns those paths untouched, so
  `_upsert_products_resilient` (products) and `_upsert_categories_resilient`
  (categories) both accept them.
* Gallery members that pass the contract are **kept**; only genuinely invalid
  entries are dropped.
* `last_write_blocker()` / `_note_write_blocker()` (`:765`) — every refusal is
  now nameable.
* `api.py:5468` — the 503 body gains `detail` and the audit row records the
  reason.

### 4.2 `storage_cleanup.py` — a reference scan that cannot miss live media
* `_category_rows()` (`:180`) delegates to `storage._live_category_rows()`
  (Supabase table + details row in production, file on a local checkout) and
  raises when the live list is unreadable, so the sweep aborts instead of
  guessing.
* New `_trash_rows()` (`:204`): the Trash Bin snapshots (production
  `growth_settings.product_trash_json`, local mirror as fallback) are
  references; an unreadable production trash aborts the sweep.
* `_referenced_keys()` (`:85`) adds the `trash` source next to products, orders,
  receipts, settings and categories.

After the fix, the same reproduction:

```
reference sources: {'categories': 2}
referenced keys:   {'categories/2026/10/kitchen.jpg', 'products/2026/10/trashed.jpg'}
nightly sweeper delete candidates: []
```

### 4.3 `auto_migrate.py` — schema only, never data
`PRODUCT_SCHEMA_STATEMENT` is now DDL only (`ALTER TABLE … ADD COLUMN IF NOT
EXISTS`). The `set_config('jaura.catalog_admin') + UPDATE … set is_active/
is_deleted/online` block is gone, so a restart can never re-publish a hidden
product or drain the Trash. Recovery stays where the owner asks for it:
`catalog.recover_unavailable_products()` → `recover_unavailable_products.sql`
(audited, per-row, reversible).

### 4.4 `catalog.repair_dead_photos` — honest reporting
Only a confirmed + mirrored write is reported as repaired; otherwise
`failed: [{id, image, reason}]`, and the admin audit line counts failures.

### 4.5 `tools/cleanup_supabase_storage.py` — same class of hole
`growth_settings` is now in the scanned `TABLES` tuple, so the manual
maintenance workflow also protects the category/details JSON, homepage
featured, banners and the Trash snapshots.

### 4.6 Regression pins
`tests/test_image_durability_and_admin_visibility.py` (10 tests) locks in:
a committed cover is accepted and a fake one is not; a save keeps committed
gallery photos; a category cover and a trashed photo are references; an
unreadable category list aborts; the boot migration contains no `UPDATE`.

---

## 5. What to do on the live site (rollout)

1. Deploy this branch. The next boot no longer rewrites availability.
2. Photos/category covers already deleted by the sweeper are **gone from the
   bucket** — re-upload them once. Existing rows that kept a relative
   `/uploads/…` cover can be converted with **Admin → Advanced settings →
   “Make cover URLs absolute”** (`POST /api/admin/catalog/repair-image-urls`).
3. If `recover_unavailable_products.sql` has not been applied, the guarded RPCs
   fall back to REST automatically (`writeLock: false` on the Catalogue card);
   applying it is still recommended so background writes cannot flip flags.
4. Optional, while confirming on production: `STORAGE_SWEEPER_NIGHTLY=0`
   disables only the automatic purge; the Admin card and the CLI tool keep
   working.
5. Keep `PUT /api/admin/products` (full catalogue replace) for complete
   exports only — anything missing from that payload is marked
   `source='replaced'` and disappears from the shop.

## 6. Verification run

```
$ pytest tests/ -q --ignore=tests/test_browser_smoke.py \
      --ignore=tests/test_delivery_seeds_postgres.py \
      --ignore=tests/test_stock_rpc_concurrency.py
2464 passed, 20 skipped, 4 warnings in 222s
```

(The three ignored modules require Playwright / pgserver, which are not
installed in this sandbox; they fail at import before any test runs.)
