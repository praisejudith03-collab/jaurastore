# Jaura Store full website and Admin Portal review

**Review date:** 16 September 2026  
**Branch reviewed:** `arena/01a0a8fa-jaurastore`  
**Scope:** customer storefront, checkout and order lifecycle; customer account and wishlist; public order tracking; delivery/content pages; admin authentication, dashboard, catalogue, stock, orders, sales, marketing, categories, delivery, settings and account; SQLite/Supabase boundaries; mail and schema contracts.

## Executive result

The full available regression suite is green: **1,057 passed and 22 skipped**. The HTML smoke pass returned **HTTP 200 for all 20 HTML pages**, and the application/server modules and browser bundles compile. No new blocking application defect was found in the reviewed flows.

The skipped tests are browser/integration paths described below. They are environment limitations, not passing substitutes for those checks.

## Corrected items 6–11 review

The corrected scope was reviewed against the original requirements rather than the previously reported bonus work:

- **Item 6 — search and dates:** Orders now expose search, From and To controls; server-side status/date/search predicates run before pagination and the filtered CSV follows the same criteria. Sales now filter confirmed totals, pending counts and top products by customer/order/product search and inclusive custom dates, while preserving relative ranges. Marketing now filters the campaign log by campaign text/type and inclusive dates, including the remote fallback path.
- **Item 7 — Delivery Zones:** the former table has been replaced with responsive fare/status cards. Existing edit, delete, save, cancel and server-confirmation behavior remains in place without rebuilding the form while an operator is typing.
- **Item 8 — Settings:** homepage hero, branding, moving banner, and live site settings are independently collapsible `details` sections. Existing IDs, submit handlers, upload controls and server-backed form behavior remain unchanged.
- **Item 9 — bulk actions:** Orders and Products have checkbox selection controls, visible-item selection, selected counts, clear selection, confirmation prompts, action buttons, and success/failure feedback. Order confirmation/deletion and product show/hide/deletion reuse the existing authenticated mutation paths; failed actions are not reported as successful.
- **Item 10 — Orders badge:** the Orders navigation badge is present in the desktop sidebar and mobile dock, starts hidden at zero, and is updated from the needs-attention pending-order count or the filtered Orders response.
- **Item 11 — category names:** category card fields now use min/max width and box sizing constraints, allow long text to remain usable, and switch to a single-column responsive layout on narrow screens.

The UI and API changes were checked with syntax compilation, API regression tests, responsive CSS tests, and a custom date/search endpoint check. Real Chromium visual interaction could not be completed in this workspace because the Playwright browser binary download failed; this remains an explicit review limitation below.

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
- Orders, receipts, status changes, partial-payment notices, search/date filters, bulk actions, Orders pending badge and sales CSV export.
- Marketing campaign composition, search/date filters, recipient count, campaign audit log, private per-recipient sends, customer-contact export and signed unsubscribe links.
- Referral/coupon tools, review moderation, categories (including responsive name inputs), delivery-zone cards/page, collapsible payment/site settings, branding and account/sync controls.

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

The API and PostgreSQL paths ran in this workspace, but `tests/test_browser_smoke.py` skipped its 22 browser cases because the Playwright Chromium executable was not present. A browser download was attempted and failed at the network/TLS step. The static/API suite passed, but this does not validate real mobile layout, browser event wiring, card rendering, details disclosure interaction, bulk-action click flows, autoplay or file uploads.

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
/tmp/jaura-test-venv/bin/python -m pytest -q
  1057 passed, 22 skipped

python -m py_compile api.py analytics.py
node --check js/admin.js
  passed

python tools/split_schema.py --check
  18 schema sections match supabase_schema.sql

20 HTML pages requested from the existing local Flask smoke review
  20 returned HTTP 200

Playwright browser review
  Chromium download attempted; network/TLS failure, so 22 browser tests remain skipped
```

The API, PostgreSQL-compatible paths, static checks and responsive CSS checks passed. The remaining browser dependency prevents a real Chromium review of mobile layout, disclosure interaction, cards, selection flows and file uploads; run `playwright install chromium` in CI before merging.
