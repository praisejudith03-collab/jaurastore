# Environment variables

`ADMIN_MASTER_PASSWORD` is the PRIMARY master password for the admin portal, and
`ADMIN_BOOTSTRAP_PASSWORD` is the secondary/permanent fallback. Both are read
directly from the process environment for every login attempt (a Render
environment update takes effect on the next attempt, with no database change).
Set them privately in the deployment host and never commit or paste their values
into code. `ADMIN_EMAILS` still controls which configured admin email identities
may sign in.

## Shop email — orders + payment receipts (set on BOTH Render services)

Every paid order and every customer-uploaded payment receipt is emailed to the
shop; the receipt email carries **the customer's own file as an attachment**
(the exact bytes they uploaded). Render's free/starter instances block outbound
SMTP ports (25/465/587), so the app sends over HTTPS first and only falls back
to SMTP — see `mailer.py`. The transport used, in order:

1. **Resend** (preferred) — `RESEND_API_KEY` → `POST https://api.resend.com/emails`
2. **Brevo** — `BREVO_API_KEY` → `POST https://api.brevo.com/v3/smtp/email`
3. **SMTP** — `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` (smtplib).
   This fallback exists for local runs and hosts with open SMTP; it will **not
   work on Render free/starter instances** because the outbound ports are
   blocked there.

Variables (set in the Render dashboard for `jaurastore-staging` AND
`jaurastore-production`; `sync: false` in render.yaml keeps the secrets out of
git):

- `MAIL_FROM` — the sender, e.g. `Jaura Store <orders@yourdomain>`. Must be a
  sender the provider has verified, or the provider rejects the send.
- `MAIL_TO` — the shop inbox that receives every order + receipt email.
- `RESEND_API_KEY` — create an API key at resend.com and verify your sending
  domain there.
- `BREVO_API_KEY` — alternative HTTPS provider, used when `RESEND_API_KEY` is
  unset.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` — SMTP fallback only.

**Verify after the deploy:** sign in to the admin portal → **Orders** — the
status line above the receipts table must read
`Receipt emails: on via resend to …` (or brevo/smtp). Press **"Email a test"**
to send a probe email to `MAIL_TO` and confirm delivery. When the variables are
missing the line reads `Receipt emails: off — set …` and orders/receipts still
appear in the portal as usual; email is an extra channel, not a dependency.

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
