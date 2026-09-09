# Admin recovery (mobile-safe)

## Master password (primary credential)

`ADMIN_MASTER_PASSWORD` is the primary password for the admin portal. When it is set in Render (dashboard → jaurastore-production → Environment), it signs in to any configured admin account and is accepted as the "current password" in the Change Password form.

- Updating it in Render works **immediately on the next login** — no database update, because no database hash is involved. (Render redeploys on save; the value is also re-read live on every attempt.)
- Set it privately in the dashboard (`sync: false` keeps it out of git). Never put it in chat, screenshots or source control.
- The account's own database password keeps working alongside it, and every login stays rate-limited with identical wrong-password errors (no account enumeration).
- The audit log records which sign-ins used the master password (`admin.login · master password`).

## Email OTP (forgotten password)

Email OTP is the preferred forgotten-password method. Use Owner emergency recovery only when OTP delivery is unavailable.

1. In Render, privately set `ADMIN_RECOVERY_SECRET` for the web service. Never put it in chat, source control, screenshots, or a URL.
2. Open the site using its HTTPS address on the phone (not an HTTP preview or localhost).
3. Open Admin, choose **Forgot password?**, then **Owner emergency recovery**.
4. Enter the admin email, recovery secret, and a new strong password privately. Do not use a shared/public device or keyboard suggestions for the secret.
5. Submit once and sign in with the new password. The recovery secret is single-use; clear the Render variable after success.

The server compares the secret in constant time, rate-limits failures, stores only a password hash, and rejects replay. The recovery endpoint does not reveal whether an account exists.

For an authenticated admin, use the Change Password form with the current password (the master password is accepted here too). A successful change signs out the current session; sign in again with the new password or the master password.

## One-shot bootstrap (last resort, unchanged)

`ADMIN_BOOTSTRAP_PASSWORD` remains the final fallback: set it, reboot once, and it forces the shared database password a single time (stamping `admin_bootstrap_applied` so it can never fire twice). Clear the variable again after recovering.
