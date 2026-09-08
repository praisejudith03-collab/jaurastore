# Publication audit v2 — Supabase `products` (53 rows), 2026-09-08

**Read-only.** Nothing here was executed against Supabase. The companion
`docs/publication-online-flags-2026-09-08-v2.sql` is a *proposal*: section 1
is SELECT-only, section 2 (the UPDATE) is commented out and must stay so until
the allowlist below is approved.

Supersedes `publication-audit-2026-09-08.md` (v1, hand-picked lists). v2
derives every bucket from the **publication policy** now codified in
`publication.py`, which is also what the Admin save path and the tests use —
so the SQL, the code and the audit can no longer drift from each other.

## The policy

A row may be `online = true` **only if all of** the following hold:

| # | Rule | Where it is enforced |
|---|---|---|
| 1 | not a leaked test fixture (`id ~ '^jau-(stock\|mirror\|unit\|sync\|opt)'`) | `publication.FIXTURE_ID_RE` (audit only; a save is judged on completeness) |
| 2 | not explicitly operator-offline: `wix-001`, `wix-012` | `publication.OPERATOR_OFFLINE` |
| 3 | a **real** image: a complete `https://…/storage/v1/object/public/uploads/…` URL (or, in dev, a committed file under `images/`); **not** `_placeholder`, not a relative `/uploads/...` key, not a third-party host, not blank | `publication.image_status` |
| 4 | `priceNgn > 0` **and** `priceCfa > 0` | `publication.decide` |
| 5 | `stock_quantity` is a non-negative integer (0 = out of stock but still shown; `NULL` = incomplete) | `publication.decide` |

Anything else stays `online = false`. The policy never deletes, renames or
touches price / stock / image — its only output is the `online` flag.

## Sources (all read-only)

| Source | Used for |
|---|---|
| Protected run #6 (`34241484557`, SHA `5a9971e`) job log | pinned counts (`total_source_rows 53`, `approved_live 29`, operator-offline 2, placeholder-only 3, no-image 2, fixtures 17) and the fact that every one of the 29 has `stock_quantity > 0` |
| `GET https://jaurastore.com.ng/api/catalog` (anonymous) | each of the 53 Supabase rows' `id`, `image_url`, `priceNgn`, `priceCfa`, `online` as served today |
| `publication.classify()` applied locally to those rows | the buckets below; identical to the run-#6 split |

The public endpoint hides `stock_quantity`, so rule 5 for the live set relies
on the run-#6 invariant (`live_products_with_stock = 29`); the SQL preview
(section 1a) prints the real column so a reviewer confirms it before approving.

## Buckets (exact ids)

### `live` — 29 → `online = true`

```
wix-011  wix-030  wix-032  wix-033  wix-042  wix-053  wix-054  wix-070
wix-072  wix-079  wix-080  wix-088  wix-091  wix-117  wix-118  wix-163
wix-164  wix-189  wix-198  wix-199  wix-200  wix-201  wix-202  wix-222
wix-228  wix-229  wix-237  wix-244  wix-252
```

Each passes rules 1–5: real `https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/uploads/products/<id>/…` photo written by run #6, `priceNgn > 0`, `priceCfa > 0`, `stock_quantity > 0`.

### `operator_offline` — 2 → `online = false`

| id | rule | detail |
|---|---|---|
| `wix-001` | 2 (and 3, 5) | placeholder image + `stock_quantity = 0`; flagged online before it was ready |
| `wix-012` | 2 (and 4) | `priceNgn = 0` is not a retail price. Has a real photo; goes live only when a valid `priceNgn` is supplied through Admin — at that point the policy publishes it automatically (`OPERATOR_OFFLINE` must then be edited in the same PR that documents the price) |

### `placeholder_only` — 3 → `online = false`

```
wix-041  Apple manicure set
wix-055  Bioaqua Vitamin C Set
wix-197  Press on nail
```

Rule 3: `image_url = images/products/_placeholder.jpg`. Price and stock are
otherwise valid; they publish automatically the moment a real photo is
uploaded through Admin.

### `no_image` — 2 → `online = false`

```
jau-mtot3318  Alarm clock       image_url = /uploads/products/2026/09/bc54e8705f8913ef-1e195534.jpg
wix-002       100L storage bag  image_url = /uploads/products/2026/09/7f276ebd90a4e797-b65fa7a1.jpg
```

Rule 3: a **relative** `/uploads/...` key (`image_status = relative`) is not
verifiable from here. Likely real photos in the bucket; a human confirms the
object renders, re-saves the product from Admin (which stores the full public
URL) and the policy publishes it. No image change is proposed in this PR.

### `fixture` — 17 → `online = false`

```
jau-mirror-fail  jau-mirror-ok    jau-mirror-post
jau-stock-a      jau-stock-b      jau-stock-dcf    jau-stock-dec    jau-stock-del
jau-stock-dnp    jau-stock-dpn    jau-stock-em2    jau-stock-eml    jau-stock-idem
jau-stock-opr    jau-stock-opt    jau-stock-pay    jau-stock-rop
```

Rule 1 (and 3: all placeholder). pytest fixtures that leaked into the
production table. **Offline only — not deleted.** Deletion is a separate
decision.

### `incomplete` — 0

No non-fixture row fails rules 4–5 alone.

**Total 29 + 2 + 3 + 2 + 17 + 0 = 53. Offline = 24.** No id is in two
buckets. Same split as v1, now with the rule each row fell under.

## What the SQL changes (and does not)

- Two `UPDATE … SET online = …, updated_at = now() WHERE id IN (<literal list>) AND online IS DISTINCT FROM <target>`.
- A drift guard inside the transaction: the table must still contain exactly
  these 53 ids, or the whole thing raises and rolls back.
- Post-conditions inside the transaction: online = 29, offline = 24, zero
  online rows that violate the policy (placeholder / non-https image, price
  ≤ 0, `stock_quantity` NULL or < 0, fixture id), `wix-001`/`wix-012` false.
- Section 1c / 3d: an `md5` fingerprint of every column the SQL promises not
  to touch, to be captured before and compared after.
- No DELETE, no INSERT, no id rename, no price / stock / `image_url` change.

Regenerate the allowlist + SELECT preview at any time from a saved
catalogue export: `python3 tools/publication_audit.py catalog.json --sql`
(it never prints an UPDATE).

## The code side of the same policy (this PR)

| Requirement | Change |
|---|---|
| New valid Admin product is online at once; an incomplete one is saved but offline | `catalog.upsert` runs `apply_publication_policy` on every Admin/API save; the response carries `publication: {online, publishable, bucket, reasons, codes, requested}` and the Admin form toasts the reason |
| Admin can explicitly publish / unpublish | `POST /api/admin/products/<id>/publish {online}` → `catalog.set_online` → `supabase_store.set_product_online` (UPDATE of `online` + `updated_at` only). Publishing an unready row answers **409** with the reasons; unpublishing is always allowed. Publish/Unpublish buttons on each Admin product card |
| Unavailable products are kept, never deleted | nothing in the policy path deletes; unpublish flips one flag |
| Storefront reads Supabase, filters `online IS TRUE` | production `catalog.merged()` uses **only** Supabase rows (no seed / local-override union) and `is_online(p)` = `p["online"] is True` — `NULL` and `"true"` are offline |
| Admin sees every row | `/api/catalog?all=1` with an admin session returns the unfiltered list, `Cache-Control: private, no-store` |
| ETag includes Supabase `updated_at` and the online count | `catalog.meta()` = `{updatedAt = max(row.updated_at, local stamp), count, online}`; `/api/catalog` ETag = sha256 of `updatedAt|count|online|view`, derived from the same read as the body; `Cache-Control: public, max-age=30, must-revalidate`, `Vary: Cookie` |
| Stock moves never republish / unpublish | `apply_stock_delta` saves with `policy=False` |
| No stale local fallback in production reads | `js/store.js`: once `api/catalog` answers, the bundled `js/products-data.js` snapshot is never mixed in; it is only the offline last resort and is itself filtered to `online === true` |
| Cache invalidation after catalogue changes | `sw.js` `VERSION` → `jaura-v129` and every `?v=128` → `?v=129` (all pages drop the old cached catalogue on next load); Admin drops the cached `/api/catalog` entry from Cache Storage on every save / publish / delete |

## What this audit did NOT do

- No workflow dispatched, no image migration, nothing uploaded.
- No write to Supabase; both SQL files are text only. v1 is marked superseded and must not be run either.
- No product deleted, no id renamed, no price / stock / `image_url` changed.
