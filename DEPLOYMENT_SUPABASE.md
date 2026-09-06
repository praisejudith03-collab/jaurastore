# Supabase/Render production cutover

Supabase PostgreSQL is the production source of truth for **site_settings,
products, categories, orders, payment receipts, admin reset tokens and the
referral commission**; Supabase Storage is the source of truth for product
images/videos, category images, banners/logos and checkout receipts. Local
JSON files, `data/uploads/`, `/static/uploads/` and `localStorage` are never
written by production code — they only remain as test-only compatibility
paths under `FLASK_ENV=testing` (and as offline paint caches that are never
re-read over the server value).

## 1. Apply the schema (idempotent — safe to re-run)

In the Supabase SQL editor run the complete `supabase_schema.sql`
(Dashboard → SQL → New query). It creates/repairs:

* `products` — canonical `image_url`, `images`, `stock_quantity` (+ legacy
  `image`, `stock` aliases), non-negative price and stock constraints
* `site_settings` — the id=1 row with bank details, referral commission,
  hero/contact/logo columns
* `categories` — `image_url`
* `orders`, `receipts` (with `file_url`), `admin_reset_tokens` (hashed
  tokens), referral/coupon tables
* `reserve_product_stock` / `release_product_stock` RPCs (atomic stock)
* Storage buckets: `uploads` (public) and `receipts` (PRIVATE) + policies

Alternatively, from the server:

```bash
python3 migrate_supabase.py --schema   # requires SUPABASE_DB_URL + psql
```

## 2. Import the products

```bash
python3 migrate_supabase.py            # reads data/seed.json + data/catalog.json
python3 migrate_supabase.py --reset    # ONLY to wipe rows first (dangerous)
```

Every row is written with the **canonical** `image_url`/`images`/
`stock_quantity` **and** the legacy `image`/`stock` aliases. Prices are
preserved exactly as stored. Existing Supabase rows are never deleted
unless `--reset`.

## 3. Migrate legacy receipts into the PRIVATE bucket

```bash
python3 migrate_supabase.py --receipts --report receipts_migration.json
```

* Copies public-bucket objects (`.../sign/uploads/proofs/...`) into the
  private `receipts` bucket — the old object is **never deleted**.
* Uploads local-disk files (`/uploads/proofs/...`) into the private bucket —
  the local file is **never deleted**.
* Re-points `receipts.file_url` at a fresh signed URL and writes a JSON
  report (migrated / skipped / errors) for review.
* Run BEFORE switching `UPLOAD_MODE=supabase` in production. Until then,
  `storage.signed_url_for()` keeps admins able to open legacy receipts from
  their original bucket (see `storage.py`), so there is no broken window.

## 4. Render environment

Set:

```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service-role key>      # server-side only
SUPABASE_BUCKET=uploads
SUPABASE_PRIVATE_BUCKET=receipts
UPLOAD_MODE=supabase
FLASK_ENV=production
SECRET_KEY=<random>
ADMIN_EMAILS=<admin email>
MAIL_* / RESEND_API_KEY  (admin password reset + receipts emails)
RECAPTCHA_SITE_KEY / RECAPTCHA_SECRET_KEY (optional, recommended)
```

Never expose the service-role key to browser JavaScript.

## 5. What happens when Supabase is down

* `GET /api/site`, `POST /api/admin/site`, product/category/order/receipt
  admin writes answer a clear `503` with `ok:false` and `error` — the admin
  portal shows the error and never reports success.
* Checkout: `POST /api/orders` loads each product from Supabase, recomputes
  totals server-side, validates stock and reserves it atomically; a failed
  reserve releases the reserved units and returns a clear error.
* Referral rewards read `site_settings.referral_commission_percentage` at
  payout time; when Supabase is unreachable the reward is skipped (never a
  stale process-local percentage).

## 6. Delete the legacy files (only after cutover is verified)

After the smoke test below passes, these are safe to remove from the deploy
host (nothing in production reads them):

* `data/site.json` and `site.json` (if present)
* `data/uploads/` and `/static/uploads/` (legacy upload dirs)
* `data/backups/orders-backup.json` etc. (boot cache only; Supabase holds the
  record)
* committed product/category upload images that were re-uploaded to Storage
  (keep `images/products/*` and branding source assets that the static site
  ships by design)

Never delete the Supabase objects listed in the migration report until the
report's rows are confirmed migrated and the smoke test passed.

## 7. Verify after deploy

* `/healthz`, `GET /api/site` (bank fields + referral % from Supabase)
* Admin upload (product photo → complete HTTPS URL), category image upload,
  logo/banner upload
* Product save/delete (row comes back re-queried from Supabase, stock number
  never shown to customers — public catalog only emits `stock_status`)
* Checkout: tampered browser price is ignored; qty over stock is rejected;
  receipt upload lands in the private bucket and the admin can View/Delete
* Password reset email → 6-digit code → new password (token row persisted in
  `admin_reset_tokens`)
* Referral: complete an order on the milestone and confirm the reward coupon
  percent equals `site_settings.referral_commission_percentage`

## 8. Rollback

The app still runs without Supabase when `UPLOAD_MODE` / `SUPABASE_*` are
unset (test/dev path) — but that is NOT production-safe and only serves the
static/test flows. For a real rollback:

1. Unset `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` and set
   `FLASK_ENV=development` on Render (the shop then uses the local files,
   which the migration never deleted).
2. The Supabase schema, buckets and rows are untouched by rollback; re-set
   the env vars to return to the Supabase-backed build.
3. For storage rollback (worst case), the legacy objects/files listed in the
   migration report were never deleted, so they can be restored manually.

## 9. Route map (Supabase-backed production paths)

| Route | Purpose |
|---|---|
| `GET /api/site` | live `site_settings` (canonical + legacy aliases) |
| `POST /api/admin/site` | save site_settings id=1 (auth + CSRF + validate, returns saved row) |
| `GET /api/catalog`, `GET /api/products` | public catalogue (no numeric stock; `stock_status` only) |
| `POST /api/admin/products` | save product (server-normalised, Supabase-first, returns row) |
| `DELETE /api/admin/products/<id>` | soft-delete; 503 unless Supabase confirmed |
| `PUT /api/admin/categories` | save categories (image_url from Storage) |
| `POST /api/admin/uploads/{image,video,category,product,hero}` | Storage uploads |
| `POST /api/orders` | checkout: server prices, stock validation, atomic reserve |
| `PATCH /api/admin/orders/<id>` | confirm/decline/reopen |
| `DELETE /api/admin/orders/<id>` | delete order + its receipts + Storage objects |
| `GET /api/admin/payment-proofs` | receipt list with fresh signed URLs |
| `DELETE /api/admin/payment-proofs/<id>` | delete receipt row + Storage object |
| `POST /api/admin/growth/settings` | marketing + referral settings |
| `POST /api/admin/coupons` | create coupon |
| `POST /api/admin/password`, `/admin/otp/*` | admin auth/reset |
| `POST /api/admin/backup`, `/api/admin/sync/repo` | ops |

## 10. Branch / PR

Work on `arena/01a07876-jaurastore`, push that branch, and open a pull
request targeting `main`. Do not push directly to `main`. Render deploys the
merge commit; run the verification checklist above after deploy.
