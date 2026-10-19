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
dashboard) together with the Supabase pair above. The app writes to exactly
**one** bucket, `uploads`, which must be **public**. `SUPABASE_BUCKET` is not
an application override — it is only read by the standalone migration/cleanup
tools; `supabase_store._bucket()` deliberately ignores it so no environment
mistake can point an upload at a private or unrelated bucket.

* product photos, category assets, hero video/banner/logo → public URL
  (`…/storage/v1/object/public/uploads/<key>`), stored permanently in the
  bucket, never on the container's disk
* payment receipts and proofs → **signed URL** (admin-only, never publicly
  guessable); the admin receipts view refreshes them on the fly, and the
  upload still works even after the 7-day Supabase signed-URL cap
* **production never falls back to the local disk.** If the bucket cannot be
  reached the upload fails with a clear error and nothing is saved, because a
  file written to `/tmp/uploads` would 404 the next time the service deploys.
  `/uploads/…` keeps *serving* legacy on-disk files (and re-points at the
  bucket when the object is there); it is not a write target outside testing.
* an image whose bytes a browser cannot paint (a HEIC photo from an iPhone,
  for instance) is converted to JPEG on upload, or refused with the reason —
  so a successful upload can never leave a broken-image icon on a category
  card or a product page

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
| `storage.py` | uploads: Supabase Storage in production (never the disk); local files only off production |
| `css/`, `js/` | stylesheet and client JS (store, admin, i18n, offline net, service worker) |
| `images/` | product, category and brand assets |
| `data/` | `seed.json`, `catalog.json`, SQLite DB, uploaded proofs |
| `js/net.js` | fetch wrapper + offline outbox |
| `sw.js` | offline caching |
| `tests/` | pytest API tests, Playwright end-to-end and layout audits |

## Caching, asset versioning and language

Two contracts, both pinned by `tests/test_zero_cache_policy.py`, both added on
owner directive 2026-10-19 after "the mobile page is stale — I have to clear the
cache or add `?v=live`" and "French visitors must see French without hunting for a
toggle".

**A shopper's tap is never answered from a cache.** Every HTML document, the
service worker, every payment proof and every dynamic `/api/*` answer carries
`Cache-Control: no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0`,
spelled once as `security.NO_STORE` (and `NO_STORE_PRIVATE` where the response also
depends on the admin session). The two deliberate exceptions are the price of
keeping a 3G phone usable, and both revalidate instead of stale-serving:

| Response | Policy | Why it is safe |
| --- | --- | --- |
| `GET /api/catalog` | `private, no-cache, must-revalidate` + ETag | the body is only reused after a 304 says nothing changed; an admin save also clears the server snapshot |
| `?v=<digits>` static assets | `public, max-age=31536000, immutable` | the URL is good for exactly one build — see below |

Anything that used to look clever here (`s-maxage=3600`, `stale-if-error=604800`
on the homepage; `max-age=60, stale-while-revalidate=120` on `/api/categories`) is
gone, and a test fails the build if it returns: an edge cache that may answer for
an hour, or a page that may be served a week after the origin failed, is precisely
how a saved price or a newly uploaded category image goes invisible on phones.

**Versioning is automated, so a release cannot be shipped half-cached.** A
returning phone holds `js/store.js?v=206` for a year, so a JS change that does not
bump the token is invisible to exactly the customers who complained. Run:

```bash
python3 tools/asset_version.py --check   # CI: fails if a shipped asset changed without a bump
python3 tools/asset_version.py --write   # bumps ?v= everywhere + re-records the hashes
```

`--write` is a no-op when no shipped asset changed, so it belongs in a pre-deploy
step: a build that touched no frontend does not throw away a year of phone caching.
`tests/test_asset_cache_token.py` keeps the fingerprint list honest, and `sw.js`'s
`const VERSION = "jaura-v<N>"` is the token's source of truth.

**Language: explicit choice > device > English.** `js/i18n.js` honours a `?lang=fr|en`
link or a tap on the floating EN|FR pill (persisted under `jaura_lang`), and only
when a visitor has never chosen does it follow the device's `navigator.languages`
— so a francophone phone opens the French shop with F CFA prices, untouched, and one
tap on EN is remembered permanently (the missing half that made the 2026-09-27
attempt unacceptable). `app.py` mirrors that precedence from the `jaura_lang` cookie
and `Accept-Language` so the very first paint already carries `<html lang="fr">`, and
those documents `Vary: Accept-Language, Cookie`. Geography and IP are never consulted.

## Environments

`develop` deploys to staging, `main` to production (`.github/workflows/deploy.yml`
needs each Render service's deploy hook in `RENDER_DEPLOY_HOOK_STAGING` /
`RENDER_DEPLOY_HOOK_PRODUCTION`). CI runs the tests and a broken-link check on
every push and pull request.

The SQLite database and payment proofs are files on disk **in development only**.
On a host with an ephemeral filesystem, mount a persistent disk at `data/` (see
`render.yaml`) — otherwise they reset on each deploy. In production both halves
are Supabase: catalogue rows through the PostgREST gateway, and every uploaded
image, video or document in the public `uploads` bucket (see
`#### Uploads (Supabase Storage)` above); the app never writes a customer-visible
file to the container.
