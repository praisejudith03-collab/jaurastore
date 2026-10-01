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

## Supplier stock sync (in-process watchdog)

Product stock can follow the supplier page attached to a product's **Supplier
URL for Auto Stock Sync** field. The sync runs quietly inside the web service
(consolidated scheduler loop, bounded hourly batches — no worker service),
never deletes a supplier link, and writes a warning row for anything it could
not read with confidence instead of guessing.

The stock rule, per matched product/variant: supplier **out** → Jaura out;
supplier **lower** → Jaura reduced to the supplier count; supplier **higher**
→ Jaura is **not** raised above the owner's hand-entered quantity unless the
safe setting below is on. Variants with no confident supplier match, products
whose supplier page cannot be fetched/parsed, and uncertain readings are all
left exactly as they are (with a logged warning).

- `SUPPLIER_WATCHDOG_ENABLED` (default `1`) — master switch for the sync.
- `SUPPLIER_STOCK_AUTO_INCREASE` (default `0`/off) — the safe setting. Set it
  to `1` ONLY if supplier restocks should automatically raise Jaura stock
  above the owner's manually entered quantity. Reductions and out-of-stock
  are always applied regardless of this setting.

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
