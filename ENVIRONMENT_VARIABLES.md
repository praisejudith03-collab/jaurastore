# Environment variables

`ADMIN_MASTER_PASSWORD` is the PRIMARY master password for the admin portal, and
`ADMIN_BOOTSTRAP_PASSWORD` is the secondary/permanent fallback. Both are read
directly from the process environment for every login attempt (a Render
environment update takes effect on the next attempt, with no database change).
Set them privately in the deployment host and never commit or paste their values
into code. `ADMIN_EMAIL` controls the owner identity that may sign in and the
inbox used for system and order notifications (`ADMIN_EMAILS` remains a legacy
multi-address alias).

## Shop email — orders, receipts + customer confirmations (set on BOTH Render services)

Every paid order and every customer-uploaded payment receipt is emailed to the
shop; the receipt email carries **the customer's own file as an attachment**
(the exact bytes they uploaded). When an admin marks an order **CONFIRMED** in
the portal, the customer automatically receives a confirmation email at the
address they checked out with. All dispatch runs on background daemon threads,
so a slow or dead provider can never block a checkout, a receipt upload or an
admin action — failed attempts only log one quiet `[mailer]` line.
Render's free/starter instances block outbound SMTP ports (25/465/587), so the
app sends over HTTPS first and only falls back to SMTP — see `mailer.py`. The
transport used, in order:

1. **Resend** (preferred) — `RESEND_API_KEY` → `POST https://api.resend.com/emails`
2. **Brevo** — `BREVO_API_KEY` → `POST https://api.brevo.com/v3/smtp/email`
3. **SMTP** — `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` (smtplib).
   This fallback exists for local runs and hosts with open SMTP; it will **not
   work on Render free/starter instances** because the outbound ports are
   blocked there.

Variables (set in the Render dashboard for `jaurastore-staging` AND
`jaurastore-production`; `sync: false` in render.yaml keeps the secrets out of
git):

- `MAIL_FROM` — the verified sender (`orders@jaurastore.com.ng` in Render).
- `ADMIN_EMAIL` — the shop inbox for system and order notifications
  (`jaurastore@gmail.com`).
- `MAIL_TO` is unsupported and must not be configured. Customer campaigns and
  transactional messages use each customer's explicit address; owner alerts
  use `ADMIN_EMAIL`.
- `RESEND_API_KEY` — create an API key at resend.com and verify your sending
  domain there.
- `BREVO_API_KEY` — alternative HTTPS provider, used when `RESEND_API_KEY` is
  unset.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` — SMTP fallback only.

**Verify after the deploy:** sign in to the admin portal → **Orders** — the
status line above the receipts table must read
`Shop emails: on via resend to …` (or brevo/smtp). Press **"Email a test"**
to send a probe email to the shop inbox and confirm delivery (the probe itself
runs on a background thread with a bounded wait, so it cannot time the worker
out). When the variables are missing the line reads
`Shop emails: off — set …` and orders/receipts still appear in the portal as
usual; email is an extra channel, not a dependency. Confirming an order whose
customer has no valid email simply skips the customer email (logged quietly).

> Note: an earlier revision of this file listed `MAIL_FROM`, `RESEND_API_KEY`
> and the `SMTP_*` variables as obsolete. That is no longer true — the app
> reads them again (this is the receipt/order email feature). `MAIL_MODE` is
> still obsolete.

The following variables are obsolete and safe to delete from Render, local `.env`
files, deployment dashboards, and secret stores. The application does not read
them and boots without them:

- `MAIL_MODE`
- `ADMIN_PASSWORD`
- `ADMIN_RESET_*`, `RECOVERY_*`, and `OTP_*` variables
- Any other prior admin recovery, reset, or OTP variables

Do not delete `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `UPLOAD_MODE`,
`ADMIN_MASTER_PASSWORD`, or `ADMIN_BOOTSTRAP_PASSWORD`; those support production
data, storage, and the admin login. Do not delete the shop-email variables
above, or receipts stop reaching the inbox. Removing obsolete variables does not
mutate products, orders, customers, receipts, reviews, or catalogue data.

## Google Drive accounting ledgers

The standalone `/admin/accounting` desk can create two workbooks in the store
owner's Google Drive: **Naira Ledger** and **CFA Ledger**. Confirming an order
adds it to the matching workbook; connecting for the first time also queues a
one-time import of existing confirmed orders. The OAuth grant requests
`openid email https://www.googleapis.com/auth/drive`, which is what lets a push
write to the owner's own pre-existing ITEMFLOW workbook as well as the ledgers
the app creates.

Set these on the production service:

- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` — OAuth 2.0 **Web application**
  client values from Google Cloud Console.
- `GOOGLE_REDIRECT_URI` — must exactly match the OAuth client's authorized
  redirect URI. The Render blueprint uses
  `https://jaurastore.com.ng/api/admin/accounting/google/callback`.
- `GOOGLE_SHEETS_TOKEN_KEY` — optional, recommended stable secret used to
  encrypt the refresh token before storing it in Supabase. Generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`. If omitted,
  the app derives the encryption key from the stable `SECRET_KEY`.
- `GOOGLE_SHEET_ID` — the owner's ITEMFLOW reference workbook whose **NGN** and
  **FCFA** tabs receive pushed batches
  (`1GnBgXl-VNoRzV-jiz4qCeb_BKzs31_Fu`). A reference id saved on the accounting
  desk wins over this value; when both are empty the built-in id is used, so a
  push never silently goes nowhere.

The full walkthrough — Cloud Console project, OAuth client, redirect URIs,
Render variables and the five-step live test — is in
**[GOOGLE_DRIVE_SYNC_SETUP.md](GOOGLE_DRIVE_SYNC_SETUP.md)**. The accounting
desk's **✓ Test Google sync** button calls
`GET /api/admin/accounting/google/verify`, which reports `verification.ok`
plus a per-step breakdown without writing anything.

Then enable the Google Sheets API in the same Cloud project, add the redirect
URI to the OAuth client, and complete Google's OAuth consent-screen setup. If
the consent screen is in External/testing mode, add the owner's Google email as
a test user. Sign in as the store admin, open **Admin → Accounting**, and
connect the Google account whose Drive should own the ledgers. The page shows
the connected email and direct Open NGN / Open FCFA actions.

Each currency workbook has:

- **Orders** — confirmed orders arrive automatically. Supplier costs and
  transport can be entered on the order row; the Net Profit column has a
  dynamic array formula. The Batch column is a dropdown.
- **Expenses** — record unlinked supplier purchases (for example Ankara or
  perfume stock) and transport as dated rows. Select a type and optionally a
  batch; these rows are included in the Admin totals.
- **Lists** — edit the batch names in column A. Those names populate the Batch
  dropdowns in both data tabs and the Selected Batch filter on the admin desk.

A Sheets outage never rolls back an order confirmation; refresh the accounting
desk to retry reading totals, and reconfirming the order safely retries its
idempotent sheet upsert. OAuth refresh tokens are encrypted server-side and
are never sent to the browser. Keep `SECRET_KEY` (or `GOOGLE_SHEETS_TOKEN_KEY`)
stable across deploys so the stored grant remains decryptable.

## Schema auto-migration on boot

`auto_migrate.py` runs once per process at startup (after `init_db`/`migrate`,
never in tests) and applies the additive `ALTER TABLE ... ADD COLUMN IF NOT
EXISTS` statements the live Supabase `site_settings` table may be missing — so
a schema lag can never surface as an admin save error. The owner-statement is
the first one applied:

```sql
alter table site_settings add column if not exists popup_banner_active boolean not null default true;
```

PostgREST cannot run DDL, so the module tries, in order: a direct Postgres
connection, `psql`, then a Supabase SQL RPC (`exec_sql`, `execute_sql`,
`run_sql`, `execute_sql`, `apply_schema_migration`). A failed attempt only logs
the exact statement to run in the Supabase SQL editor; boot is never blocked.

- `SUPABASE_DB_URL` (or `DATABASE_URL`) — the Supabase **connection string**
  (Dashboard → Project Settings → Database → Connection string → URI). With it
  set, the boot pass heals the schema by itself; without it the app still boots
  and the admin save path retries the heal on the next attempt.
- `AUTO_MIGRATE_IN_TESTS=1` (testing only) — run the pass under
  `FLASK_ENV=testing` too.

## Supplier stock sync (in-process watchdog)

Product stock can follow the supplier page attached to a product's **Supplier
URL for Auto Stock Sync** field. The sync runs quietly inside the web service
(consolidated scheduler loop, bounded hourly batches — no worker service),
never deletes a supplier link, and writes a warning row for anything it could
not read with confidence instead of guessing.

The stock rule, per matched product/variant (owner rule, 2026-10-05): the
supplier count is mirrored **1:1** — no safety buffer, no one-unit clamp. A
page that only says “in stock” (a flag, not a count) keeps the shelf the shop
already has and, when that shelf is empty, opens
`SUPPLIER_IN_STOCK_UNITS` units so multi-unit orders go through. Supplier
**out** → Jaura out. Variants with no confident supplier match, products whose
supplier page cannot be fetched/parsed, and uncertain readings are left exactly
as they are (with a logged warning).

The checkout never refuses an order over a mirrored quantity: the request is
taken, the real shelf is drained atomically to zero (never below) and any
shortfall is recorded on the order (`stockShortfall`) for the admin. Only an
item with nothing available at all is refused, as *This item is currently out
of stock.*

- `SUPPLIER_WATCHDOG_ENABLED` (default `1`) — master switch for the sync.
- `SUPPLIER_IN_STOCK_UNITS` (default `10`) — the shelf opened when the
  supplier page confirms availability without printing a number.

### Price watch

The watchdog also reads the supplier page's **prices** (JSON-LD offers,
product JSON, `data-price` attributes) and remembers the last price seen per
product/variant. When a supplier price **rises** it writes a
`supplier_price_increased` warning to the admin's supplier-warning queue
(drops are reported as `supplier_price_dropped`). The shop's own retail price
is **never** rewritten by a supplier page — repricing stays the owner's
decision.

### The 2:00 AM nightly pass

Once per day, after 2:00 AM in the owner's timezone, the scheduler runs a
deep pass: every supplier-linked product is checked **exactly once**
(regardless of day-time batching), followed by the **storage sweeper** that
purges orphaned/duplicate upload media through the same protected plan as the
Admin Portal's *Settings → Advanced settings → Storage cleanup* card (files a
live product, order, receipt or the site still references are never touched;
uploads younger than two days are protected; a failed reference scan aborts
rather than guessing). `/healthz` reports the schedule and the last run under
`background.nightly`. Hard-deleted product ids are skipped by every automated
pass, so a deleted product can never be re-created by a sync.

- `SUPPLIER_WATCHDOG_NIGHTLY` (default `1`) — set `0`/`off` to disable the
  nightly pass entirely.
- `SUPPLIER_WATCHDOG_NIGHTLY_HOUR` (default `2`) — the local hour the pass
  becomes due (24h clock; `0` = midnight).
- `SUPPLIER_WATCHDOG_NIGHTLY_TZ_OFFSET` (default `1`) — the owner's timezone
  as an offset from UTC in hours (default `1` = West Africa Time). The
  scheduler runs on UTC; this is what makes "2:00 AM" mean the owner's 2 AM.
- `SUPPLIER_WATCHDOG_NIGHTLY_MAX` (default `400`) — upper bound on products
  checked in one nightly pass.
- `SUPPLIER_WATCHDOG_MIN_INTERVAL` (default `3600`) — how long a
  supplier-linked product waits before a *day-time* tick may check it again.
  The nightly pass always ignores it.
- `STORAGE_SWEEPER_NIGHTLY` (default `1`) — set `0`/`off` to keep the nightly
  supplier sweep but skip the automatic media purge (the admin card and the
  CLI tool keep working either way).

## Analytics geolocation, bot filtering and crash reporting

Visitor locations come from the CDN edge headers (`CF-IPCity` /
`CF-IPCountry` on Cloudflare, `X-Vercel-IP-*`, `X-Render-*`), which the edge
geocodes from **the shopper's own IP address**. The app reads that client
address through `security.client_ip()`, which prefers `CF-Connecting-IP` /
`True-Client-IP` / `X-Real-IP` and otherwise takes the left-most *public*
address of `X-Forwarded-For` — never `remote_addr`, which is the proxy. This
is what previously pinned visitors to Finland and other datacentres. No IP
address is ever stored.

Automated traffic (crawlers, uptime monitors, scrapers, the keep-alive ping)
is filtered out of analytics entirely — it still gets served normally, it is
just not counted. Two optional variables tune this:

- `ANALYTICS_BOT_IP_NETWORKS` — extra CIDR ranges to treat as bots, comma or
  space separated (e.g. `203.0.113.0/24, 198.51.100.0/24`). Added to the
  built-in crawler/datacentre list in `security.BOT_IP_NETWORKS`; no deploy
  is needed to widen it beyond a restart.
- `ANALYTICS_RETENTION_DAYS` (default `400`) — how long raw page views,
  events and search rows are kept. The lifetime counters in
  `analytics_counters` are **never** pruned, so the headline totals cannot
  fall back to zero.

Background-worker crashes (scheduler ticks, notification sends) are recorded
with their exception, stack trace, timestamp, process memory and payload id
in the `job_failures` table, mirrored to Supabase and listed in the Admin
Portal under **Orders → Background job failures**. When `GITHUB_TOKEN` (or
`GITHUB_API_TOKEN`) and `GITHUB_REPOSITORY` are set, each crash also fires a
`jaura-crash` repository dispatch, which `.github/workflows/crash-report.yml`
turns into a GitHub issue comment — the same pair of variables `repo_sync.py`
already uses, so nothing new needs configuring if repo sync is on.
