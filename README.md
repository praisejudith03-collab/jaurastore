# J Aura Store

Premium household items,skincare, Ankara wears,fashion, beauty and lifestyle store for West Africa. Prices are entered
in Naira and shown in F CFA at 1 ₦ = 0.44 F CFA. Payments are taken by bank
transfer (UBA ₦ / MTN MoMo & Moov CFA) and confirmed by the shop.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # then set SECRET_KEY
python3 seed_admin.py jaurastore@gmail.com   # set the admin password
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
  "Receipts customers uploaded" table under it lists every receipt sent
  through `/pay.html`, with the file itself and whether the email went out.
* **My account** — change the password on this device.
* Forgotten passwords are reset with a 6-digit code emailed to the admin address.
* The admin email list is the only way accounts exist:
  `ADMIN_EMAILS=you@example.com,sister@example.com` in `.env`. There is no
  public registration endpoint.

## Payment receipts

Customers send proof of payment from **`/pay.html`** (linked in the footer, from
the checkout confirmation, and from the order-tracking page). They fill in
their name, phone, email, order ID, the products as ordered, the quantity, the
amount and the payment method, then attach their receipt.

* Accepted: **JPG, PNG, PDF** up to **8 MB**.
* Every file is re-identified from its own bytes before it is stored, so an
  `.exe` renamed to `.pdf` is refused, and a truncated PDF is refused too.
* The shop receives **one email per receipt at `jaurastore@gmail.com`** with
  the **original file attached** — the same bytes that were uploaded, not a
  re-rendered copy — plus the customer's name, phone, email, order ID,
  products, quantity, payment method and amount.
* The customer gets a short confirmation email, and the file is also kept on
  the server (`data/uploads/proofs/…`, or S3/R2 when `UPLOAD_MODE=s3`) and
  listed in the admin portal, so a receipt is never lost to a spam folder.
* If the phone is offline the receipt is queued on the device and sent when
  the connection returns (`js/net.js`).
* Works on a phone: single-column layout, `capture` on the file input so
  customers can shoot the receipt, and no sideways scrolling at 390 px.

### Which Gmail should send the receipts?

Use the **same Gmail account** you want the receipts to arrive at
(`jaurastore@gmail.com`) - it keeps three settings identical:

| Setting | Value | Why |
| --- | --- | --- |
| `ADMIN_EMAILS` | `jaurastore@gmail.com` | where receipts arrive, and the only admin login |
| `MAIL_FROM` | `jaurastore@gmail.com` | the address receipts are sent from |
| `SMTP_USER` | `jaurastore@gmail.com` | the Gmail account the server signs in as |

Gmail always lets an account send as *itself*, so nothing else has to be set
up. If you ever change `MAIL_FROM` to a different address, Gmail refuses or
rewrites it unless that address is added under
*Settings > Accounts > Send mail as*.

The shop's own address is both the sender and the recipient, so every receipt
email sets `Reply-To` to the customer - pressing **Reply** writes to the
customer, not back to yourself.

Two fair warnings about a personal Gmail: it is capped at about 500 messages
a day, and every receipt also lands in your Sent folder. If you outgrow that,
switch to `MAIL_MODE=resend` with a verified domain - no code change needed.

You can add another person to receive receipts at any time without touching
the sender: `ADMIN_EMAILS=jaurastore@gmail.com,second@email.com`.

### Check the mail setup

```bash
python3 check_mail.py
```

It walks the whole chain and says where it stops: the settings, reaching
Gmail, signing in with the App Password, sending a receipt with a PDF
attached, then opening your mailbox over IMAP and comparing the attachment
byte for byte. If it prints *receipts will be emailed to ...*, the email is
genuinely in your inbox.

### Mail has one setting left

`.env` ships with Gmail ready and waiting (`MAIL_MODE=smtp`,
`SMTP_HOST=smtp.gmail.com`, `SMTP_USER=jaurastore@gmail.com`). Add a Gmail
**App Password** (Account → Security → 2-Step Verification → App passwords)
into `SMTP_PASS=` and receipts start arriving. `MAIL_MODE=resend` with
`RESEND_API_KEY=` works too. Until one of those is filled in, the site still
saves every receipt and shows it in the admin portal — it just cannot hand it
to your inbox.

## Tests

```bash
python3 -m pytest tests/test_api.py -q    # 29 backend + delivery tests
python3 tests/e2e.py                      # browser suite, needs the server up
python3 tests/responsive.py               # layout audit on 18 pages x 3 screens
```

`tests/test_api.py` proves the mail really leaves the app: it starts an
in-process SMTP server (`tests/mail_sink.py`, standard library only), sends a
receipt through the real endpoint and asserts the bytes that land in the inbox
are identical to the file the customer uploaded.

## Layout

| Path | What it holds |
| --- | --- |
| `app.py` | Flask app, static hosting, `/uploads`, legacy `/JauraStore/*` redirects |
| `api.py` | JSON endpoints (all writes CSRF-protected) |
| `auth.py` | admin accounts, sessions, OTP reset (Supabase Auth when configured) |
| `security.py` | input cleaning, CSRF, rate limits, security headers |
| `analytics.py` | server-side visitor/engagement counting and reporting |
| `catalog.py` | catalogue: `data/seed.json` + `data/catalog.json` overrides (Supabase when configured) |
| `supabase_store.py` | server-side Supabase gateway (products, auth) — no-op without credentials |
| `migrate_supabase.py` | one-time import of `data/seed.json` + `data/catalog.json` into Supabase |
| `storage.py` | uploads: local disk or S3/R2 (`UPLOAD_MODE`) |
| `css/`, `js/` | stylesheet and client JS (store, admin, i18n, offline net, service worker) |
| `images/` | product, category and brand assets |
| `data/` | `seed.json`, `catalog.json`, SQLite DB, uploaded proofs |
| `js/net.js` | fetch wrapper + offline outbox |
| `sw.js` | offline caching |
| `tests/` | pytest API tests, Playwright end-to-end and layout audits |

## Tests

```bash
python3 -m pytest tests/test_api.py -q   # backend
python3 tests/e2e.py                     # full browser flow (app must be running)
python3 tests/responsive.py              # overflow / tap-target audit
```

## Environments

`develop` deploys to staging, `main` to production (`.github/workflows/deploy.yml`
needs each Render service's deploy hook in `RENDER_DEPLOY_HOOK_STAGING` /
`RENDER_DEPLOY_HOOK_PRODUCTION`). CI runs the tests and a broken-link check on
every push and pull request.

The database and uploaded proofs are files on disk. On hosts with an ephemeral
filesystem, mount a persistent disk at `data/` (see `render.yaml`) or set
`UPLOAD_MODE=s3` — otherwise they reset on each deploy.
