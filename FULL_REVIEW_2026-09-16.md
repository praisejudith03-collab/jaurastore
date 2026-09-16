# Jaura Store full website and Admin Portal review

**Review date:** 16 September 2026  
**Branch reviewed:** `arena/01a0a8fa-jaurastore`  
**Scope:** customer storefront, checkout and order lifecycle; customer account and wishlist; public order tracking; delivery/content pages; admin authentication, dashboard, catalogue, stock, orders, sales, marketing, categories, delivery, settings and account; SQLite/Supabase boundaries; mail and schema contracts.

## Executive result

The dependency-complete regression suite is green: **1,025 passed and 2 skipped**. The HTML smoke pass returned **HTTP 200 for all 20 HTML pages**, and the application/server modules and browser bundles compile. No new blocking application defect was found in the reviewed flows.

The two skipped tests are the PostgreSQL and browser/integration paths described below. They are environment limitations, not passing substitutes for those checks.

## What was checked

### Customer experience

- Home, category, shop, product detail, wishlist and cart rendering.
- Search, category navigation, price/colour/size filtering, currency switching and pagination.
- Product gallery, variant selection, stock gates and add-to-cart behaviour.
- Guest checkout validation, payment receipt upload, order persistence and completion state.
- Captured checkout email and abandoned-cart reminder lifecycle, including conversion suppression.
- English/French content switching, WhatsApp country routing, account sign-in/profile/order history.
- Self-service order lookup at `track-order.html`, including status, total, items and country-specific WhatsApp follow-up.
- Delivery, contact, FAQ, terms, privacy and returns content.

### Admin Portal

- Admin authentication/session and CSRF-protected writes.
- Dashboard analytics, live activity, needs-attention queue, pending orders and low-stock queue.
- Product create/update/delete, stock visibility, photo flows and authenticated CSV export.
- Orders, receipts, status changes, partial-payment notices and sales CSV export.
- Marketing campaign composition, recipient count, campaign audit log, private per-recipient sends, customer-contact export and signed unsubscribe links.
- Referral/coupon tools, review moderation, categories, delivery zones/page, payment/site settings, branding and account/sync controls.

### Data and deployment boundaries

- SQLite schema and Supabase schema sections: **18 sections match**.
- Public catalogue does not expose stock quantities.
- Admin-only catalogue/customer exports are authenticated.
- Campaign content is escaped as plain text rather than treated as arbitrary HTML.
- Customer payment receipts remain behind the existing authenticated/admin storage flow.
- No credentials were added to source or generated files.

## Findings and recommendations

Severity: **P0** blocks a release; **P1** can cause material loss or outage; **P2** is a meaningful follow-up; **P3** is polish.

### P1 — Production configuration must be verified before deployment

In this sandbox, with no Supabase credentials or local production site-settings source, `GET /api/site` returns `503`. This is the intended fail-closed behaviour for payment/site configuration: the storefront must not invent or display bank details. On a real deployment, verify `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, the site-settings row, storage configuration and the schema migration before announcing the store. A missing setting row will leave checkout payment details unavailable rather than silently showing stale account data.

**Action:** run the deployment checklist in `DEPLOYMENT_SUPABASE.md`, then manually open checkout in both NGN and CFA and confirm the displayed details.

### P1 — Browser and PostgreSQL integration coverage still needs a dependency-complete runner

`tests/test_browser_smoke.py` and `tests/test_delivery_seeds_postgres.py` could not be collected in this workspace because `playwright` and `pgserver` are not installed. `tests/e2e.py` therefore could not run either. The static/API suite passed, but this does not validate real mobile layout, browser event wiring, autoplay, file upload rendering, or PostgreSQL delivery seed behaviour.

**Action:** run the complete CI/browser environment (`pip install -r requirements-test.txt`, Playwright browser installation if required) before merging.

### P1 — Campaign sends are synchronous

The admin campaign endpoint sends one provider request per recipient inside the HTTP request. This is acceptable for a small list, but a large customer list can hit provider rate limits or the web-server timeout, leaving a campaign log in `sending` or returning a partial result.

**Action:** move sends to a durable background job with batches, provider backoff, an explicit retry state and a resumable campaign id. Keep the current one-recipient-per-message privacy behaviour.

### P2 — Public order lookup is intentionally ID-only

`GET /api/orders/<id>` returns only the order status, total, currency, item names/quantities, city and notices; it does not return the customer's name, phone, address, email or receipt. This is a reasonable low-friction tracking flow, but an order ID copied into a public URL is still a bearer lookup token.

**Action:** if order IDs may be exposed publicly, add a second lightweight check such as the checkout email or phone suffix, or issue a separate tracking token in confirmation emails. Do not put payment receipt URLs in the public response.

### P2 — Production `SECRET_KEY` is required for unsubscribe-link strength

Marketing unsubscribe links are HMAC-signed with the application secret. The configuration has a development fallback for local operation, so production must set a long random `SECRET_KEY`; otherwise the signed links are not a meaningful authorization boundary.

**Action:** verify the production secret is present and rotated through the deployment host, never source control or the Admin UI.

### P2 — Campaign permission and suppression policy should be documented operationally

The campaign recipient list includes checkout guests and account holders with an email on file, while signed unsubscribe suppression is now honoured. The privacy page says promotional messages are sent only where requested.

**Action:** decide and document the store's consent rule (explicit opt-in versus an applicable customer relationship), update the checkout/account copy if explicit consent is required, and ensure every operator uses the unsubscribe link rather than importing an external list without consent.

### P2 — Campaign audit log does not retain recipient-level delivery evidence

The log intentionally stores counts, not customer addresses. That is good data minimisation, but it means the operator cannot identify which individual provider requests failed after a partial send.

**Action:** if support needs recipient-level troubleshooting, store provider message IDs and a short-lived, access-controlled delivery record; do not put the full recipient list in the campaign audit table.

### P3 — Language metadata is less complete than visible page copy

The storefront swaps many visible labels between English and French, but static document titles and social metadata remain English on most pages. This does not block checkout, but it weakens French browser history, sharing previews and search presentation.

**Action:** update `document.title`, `lang` and locale-specific description/social metadata when the language toggle changes, while keeping canonical URLs stable.

### P3 — Profile save feedback is local to the form

Customer profile updates show a success message but do not rehydrate every visible account value until the next render/page load. The data is saved server-side; this is a feedback polish issue rather than a persistence failure.

**Action:** merge the returned customer object into the current account view after a successful save.

## Verification commands and results

```text
/tmp/jaura-venv/bin/pytest -q \
  --ignore=tests/test_browser_smoke.py \
  --ignore=tests/test_delivery_seeds_postgres.py
  1025 passed, 2 skipped

python tools/split_schema.py --check
  18 schema sections match supabase_schema.sql

find . -maxdepth 2 -name '*.py' ... -m py_compile
  all Python modules compile

node --check js/app.js
node --check js/admin.js
node --check js/store.js
  passed

20 HTML pages requested from a local Flask smoke server
  20 returned HTTP 200
```

The local smoke server was stopped after inspection. The missing `playwright` and `pgserver` packages remain the only reason the full test command cannot be collected in this workspace.
