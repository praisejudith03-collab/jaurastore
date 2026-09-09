# Admin recovery (mobile-safe)

Email OTP is the preferred forgotten-password method. Use Owner emergency recovery only when OTP delivery is unavailable.

1. In Render, privately set `ADMIN_RECOVERY_SECRET` for the web service. Never put it in chat, source control, screenshots, or a URL.
2. Open the site using its HTTPS address on the phone (not an HTTP preview or localhost).
3. Open Admin, choose **Forgot password?**, then **Owner emergency recovery**.
4. Enter the admin email, recovery secret, and a new strong password privately. Do not use a shared/public device or keyboard suggestions for the secret.
5. Submit once and sign in with the new password. The recovery secret is single-use; clear the Render variable after success.

The server compares the secret in constant time, rate-limits failures, stores only a password hash, and rejects replay. The recovery endpoint does not reveal whether an account exists.

For an authenticated admin, use the Change Password form with the current password. A successful change signs out the current session; sign in again with the new password.
