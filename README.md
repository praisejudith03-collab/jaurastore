# J Aura Store

Premium household items,skincare, Ankara wears,fashion, beauty and lifestyle store for West Africa. Prices are entered
in Naira and shown in F CFA at 1 ₦ = 0.44 F CFA. Payments are taken by bank
transfer (UBA ₦ / MTN MoMo & Moov CFA) and confirmed by the shop.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # then set SECRET_KEY
# Set ADMIN_BOOTSTRAP_PASSWORD privately in .env or the deployment host
python3 app.py                # http://127.0.0.1:8080
```

### Supabase (products, carts, orders, admin auth)

The shop keeps working with local files / SQLite when no credentials are set.
To make **Supabase the source of truth** for the catalogue, carts, orders and
admin auth, add to `.env`:

```
SUPABASE_URL=...
SUPABASE_ANON_KEY=...          # reserved; not required for server calls
SUPABASE_SERVICE_ROLE_KEY=...   # server-side only — never ship to the browser
# SUPABASE_KEY=...              # optional alias for SUPABASE_SERVICE_ROLE_KEY
```

`SUPABASE_KEY` is accepted as an alias for `SUPABASE_SERVICE_ROLE_KEY`, so either
spelling turns the Supabase backend on.

Then run the one-time import so Supabase has the catalogue before you switch
over:

```bash
python3 migrate_supabase.py     # reads data/seed.json + data/catalog.json
```

The Flask app talks to Supabase server-side (service role key) behind the same
JSON API, so the storefront and existing flows are unchanged. When the env vars
are absent the app falls back to the local persistence, so a fresh checkout and
the test suite run with no credentials.

#### Uploads (Supabase Storage)

On Render's free tier the disk is wiped on every deploy, so uploaded images
must live in Supabase Storage. Set `UPLOAD_MODE=supabase` (`.env` or the
dashboard) together with the Supabase pair above. The bucket defaults to
`uploads` (must be a **public** bucket; override with `SUPABASE_BUCKET`):

* product photos, category assets, hero video/banner/logo → public URL
  (`…/storage/v1/object/public/uploads/<key>`)
* payment receipts and proofs → **signed URL** (admin-only, never publicly
  guessable); the admin receipts view refreshes them on the fly, and the
  upload still works even after the 7-day Supabase signed-URL cap
* if the bucket is unreachable the upload falls back to the local disk, so a
  payment receipt is never lost — `/uploads/…` keeps serving those files

`/sitemap.xml` is generated on every request from the live category table and
product catalogue (the old committed `sitemap.xml` was deleted — a snapshot
went stale and kept listing categories the owner had deleted).

Production: `gunicorn "app:create_app()" --workers 2 --timeout 90` (see
`Procfile` and `render.yaml`).

## Admin

* `/admin.html` — sign in with the admin email and password (server session,
  works from any phone or laptop; no shared PIN).
* **Insights** — live visitors, unique visitors, page views, a 7/30/90-day
  traffic chart, most-visited pages, top products, and conversion
  (orders, checkout attempts, average order value).
* **Edit products** — saving publishes instantly. Photos upload as real files.
  If the connection drops, the change is queued on the device and pushed as
  soon as it returns (the "Syncing N changes" pill shows what is waiting).
* **Orders** — every checkout form is stored on the server with its payment
  screenshot and is never cleaned up. Confirm / decline from here. A
  "Receipts customers uploaded" table under it lists every receipt attached at
  checkout, with the file itself.
* **My account** — shows the configured admin identity; the permanent password is
  managed outside the application.
* The admin email list is the only way accounts exist:
  `ADMIN_EMAILS=you@example.com,sister@example.com` in `.env`. There is no
  public registration endpoint.

### Permanent admin authentication

`ADMIN_BOOTSTRAP_PASSWORD` is the sole admin authentication method. The server
reads it directly from the process environment for every login attempt and
compares the submitted password without storing it in SQLite, Supabase, a cookie,
or an audit record. Any address listed in `ADMIN_EMAILS` may sign in with the
configured password. If the variable is unset, every admin login is rejected.

There is no password reset, OTP, recovery-secret, email-link, or admin password
change flow in the application. Set the value privately in the deployment host;
never commit or paste it into source or chat. See
[`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md) for the safe-to-delete
environment-variable inventory.

## Payment receipts

Customers upload proof of payment from `/checkout.html`. Accepted JPG, PNG,
and PDF files are validated from their bytes, stored in the configured storage,
and listed in the authenticated admin portal. In production, the receipt row is
written to Supabase PostgreSQL and the file to Supabase Storage before success is
returned; the admin read path reloads receipt values from Supabase. No outbound
notification or delivery service is used.

If the phone is offline, the existing order queue can retry the request when the
connection returns. The receipt remains part of the order/admin data flow and is
not sent to a third party.

## Tests

```bash
python3 -m pytest tests/test_api.py -q
python3 tests/e2e.py
python3 tests/responsive.py
```

## Layout

| Path | What it holds |
| --- | --- |
| `app.py` | Flask app, static hosting, `/uploads`, legacy `/JauraStore/*` redirects |
| `api.py` | JSON endpoints (all writes CSRF-protected) |
| `auth.py` | environment-backed admin authentication and sessions |
| `security.py` | input cleaning, CSRF, rate limits, security headers |
| `analytics.py` | server-side visitor/engagement counting and reporting |
| `catalog.py` | catalogue: `data/seed.json` + `data/catalog.json` overrides (Supabase when configured) |
| `supabase_store.py` | server-side Supabase gateway for catalogue, orders, receipts, stock and storage |
| `migrate_supabase.py` | one-time import of `data/seed.json` + `data/catalog.json` into Supabase |
| `storage.py` | uploads: local disk or S3/R2 (`UPLOAD_MODE`) |
| `css/`, `js/` | stylesheet and client JS (store, admin, i18n, offline net, service worker) |
| `images/` | product, category and brand assets |
| `data/` | `seed.json`, `catalog.json`, SQLite DB, uploaded proofs |
| `js/net.js` | fetch wrapper + offline outbox |
| `sw.js` | offline caching |
| `tests/` | pytest API tests, Playwright end-to-end and layout audits |

## Environments

`develop` deploys to staging, `main` to production (`.github/workflows/deploy.yml`
needs each Render service's deploy hook in `RENDER_DEPLOY_HOOK_STAGING` /
`RENDER_DEPLOY_HOOK_PRODUCTION`). CI runs the tests and a broken-link check on
every push and pull request.

The database and uploaded proofs are files on disk. On hosts with an ephemeral
filesystem, mount a persistent disk at `data/` (see `render.yaml`) or set
`UPLOAD_MODE=s3` — otherwise they reset on each deploy.
