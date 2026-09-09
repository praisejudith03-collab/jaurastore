# Environment variable cleanup

`ADMIN_MASTER_PASSWORD` is the PRIMARY master password for the admin portal, and
`ADMIN_BOOTSTRAP_PASSWORD` is the secondary/permanent fallback. Both are read
directly from the process environment for every login attempt (a Render
environment update takes effect on the next attempt, with no database change).
Set them privately in the deployment host and never commit or paste their values
into code. `ADMIN_EMAILS` still controls which configured admin email identities
may sign in.

The following variables are obsolete and safe to delete from Render, local `.env`
files, deployment dashboards, and secret stores. The application does not read
them and boots without them:

- `MAIL_MODE`
- `MAIL_FROM`
- `RESEND_API_KEY`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS`
- `ADMIN_PASSWORD`
- `ADMIN_RESET_*`, `RECOVERY_*`, and `OTP_*` variables
- Any other prior admin recovery, reset, OTP, or mail-provider variables

Do not delete `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `UPLOAD_MODE`,
`ADMIN_MASTER_PASSWORD`, or `ADMIN_BOOTSTRAP_PASSWORD`; those support production
data, storage, and the admin login. Removing obsolete variables does not mutate products, orders,
customers, receipts, reviews, or catalogue data.
