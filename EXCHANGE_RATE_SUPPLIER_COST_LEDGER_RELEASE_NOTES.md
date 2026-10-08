# Release notes — live exchange rates, supplier-cost automation, ledger Location, UI polish, SSRF guards

Branch `arena/f062eef6-jaurastore` (from `main` @ d86da2f, the branch that
passed all 2,082 tests). Worked from
`NEXT_SESSION_PR_DEPLOYMENT_CHECKLIST.md`.

**Suite:** 2,112 passed, 0 failed, 32 skipped locally — a **+55** delta over
the 2,057 this environment runs at HEAD, which is exactly the four new test
modules. No pre-existing test changed or was weakened to accommodate a
behaviour change.

---

## 1. Dynamic exchange rates — no hardcoded fallbacks

The store now has **one** exchange rate: the admin-controlled `cfaRate`
growth setting (Admin → Settings → Currency & exchange rate). A literal
`0.44` in a conversion path is gone everywhere.

**Backend**

- `currency.py` — `NGN_TO_CFA` deleted. New `live_rate()` reads the
  `cfaRate` setting (bounded to 0.01–100, falling back to the setting's own
  configured default — never a literal). `to_cfa()` / `to_ngn()` use it by
  default; an explicit `rate` still wins so locked snapshots never reprice.
- `growth.py` — `NGN_TO_CFA` deleted; `_cap()` and `total_in_ngn()` read
  `DEFAULTS["cfaRate"]` (configuration data, not a copied number).
- `api.py` — the Benin & Togo floor and the new `site["cfaRate"]` field.
- `app.py` — the shared-link price line converts at the live rate.
- `accounting.py` — `current_exchange_rate()` falls back to
  `growth.DEFAULTS`, never `LEGACY_RATE`.

**Frontend** (the important half — a stale bundle used to keep its own rate)

- `js/store.js` — no rate literal; the rate arrives on `GET /api/site`
  (`cfaRate`), is cached under `jaura_fx` for offline repaints, and is read
  through `currentRate()`. `toCfa()` returns 0 rather than guessing when no
  rate has been confirmed, and `JA.currentRate()` is exported.
- `js/admin.js` — one `liveFxRate()` / `toCfaLive()` pair for every CFA
  conversion (proof line, product rows, marketing picker, broadcast); the
  rate input no longer invents a value when the endpoint is unreadable.
- `js/app.js` — the Benin/Togo Naira floor derives from the live rate.
- `js/accounting.js` — the rate is server-supplied; shows `…` until it lands.
- `js/i18n.js` / `js/i18n-phrases.js` — footer and FAQ copy carries a
  `{rate}` token filled at render time (French uses a comma). Copy painted
  before the site row lands renders a pending marker and **re-renders on
  `ja:site`**, so a first-time visitor never sees a raw token or a stale
  number (proved in jsdom by `tests/_rate_copy_dom_check.mjs`).

`accounting.LEGACY_RATE` is deliberately kept: it labels pre-feature
snapshots whose rate was never captured. It is no longer a fallback for any
live calculation.

## 2. Supplier cost automation & dual-currency conversion

- `accounting.saved_supplier_defaults()` reads the supplier watchdog's
  persisted price book (`supplier_price_watch_json`) to get each item's base
  supplier cost **in Naira**, plus the saved supplier link when there is one.
  It is additive: the owner can still type the unit price, total, quantity or
  link by hand.
- `new_snapshot()` accepts `supplier_cost_ngn` / `supplier_qty` /
  `supplier_link`, deriving the unit price, so a confirmed order stages
  **already priced** instead of starting at 0.
- `api.py` calls it at confirmation. The FCFA ledger converts that NGN cost
  with the rate locked at confirmation (`supplier_cost_cfa`), and the NGN
  ledger keeps the Naira original.

## 3. Customer Location / Destination column

- `google_sheets.py` — both ledgers gain a `Location / Destination` column
  (appended, so every existing column keeps its index and current workbooks
  keep working); grid width and header ranges moved 11 → 12 columns.
- `accounting.order_location()` maps it from the order — country first, then
  city, then zone — and `entry_from_order()` exposes it to the desk.
- `js/accounting.js` — the staging queue shows the column.

## 4. Dead code & mobile UI

- `css/accounting-clean.css` **deleted**; its rules are consolidated into
  `css/style.css` (257 lines, one cache-busted URL instead of two).
- **Mobile tab clipping** ("Dashboard" → "ard"): the dock keeps its six
  tracks and `minmax(0, 1fr)` stops a label widening its own track; labels
  are `nowrap` and scale with the viewport (`clamp(8px, 2.55vw, 9.5px)`) with
  the letter-spacing dropped at ≤640px, so all six stay whole down to 320px.
- **Cramped rate preview**: the live preview is its own block
  (`.admin-fx-preview`) instead of being squeezed inside the label, and the
  static "₦10,000 ≈ FCFA 4,500" example is now generated from the typed rate.

## 5. SSRF safeguards & asset cache bump

- `security.py` — new guarded fetch helpers (`check_public_url`,
  `guarded_open`, `safe_fetch`, `safe_fetch_text`, `UnsafeURLError`):
  **https only**, the hostname must resolve and **every** resolved address
  must be public (blocking loopback, private, link-local, reserved,
  carrier-NAT and `169.254.169.254` metadata), **every redirect hop is
  re-validated**, with a hard timeout and a chunked read capped by a size
  limit.
- Routed through it: `supplier_watchdog.fetch_url` (owner-entered supplier
  URLs), all `google_sheets.py` requests, `mailer._http_post_json`, the crash
  reporter, `repo_sync` and `i18n.product_names`.
- **Asset token bumped 201 → 202** across every HTML page, `sw.js`,
  `js/*.js`, `css/*.css`, `tools/sync_favicon_head.py` and the pinning tests,
  with fingerprints refreshed, so mobile browsers fetch the new files
  immediately after the Render deploy.

## New tests

| Module | Covers |
| --- | --- |
| `tests/test_ssrf_guards.py` (28) | private/loopback/metadata refusal, non-https, size cap, timeout, redirect re-validation, unresolvable & rebound hosts |
| `tests/test_supplier_cost_and_location.py` (18) | saved defaults prefill (incl. variant price, link, never-raises), NGN→FCFA conversion at the active rate, Location column & row mapping |
| `tests/test_no_hardcoded_rate.py` (7) | no rate constant, no rate literal in the shipped bundles, conversions follow the setting, `LEGACY_RATE` is not a live fallback |
| `tests/test_rate_copy_in_dom.py` + `_rate_copy_dom_check.mjs` (2) | rate copy in jsdom: no raw token, no invented number, re-render on `ja:site`, live updates, French comma |

Updated to the new contract: `tests/_cfa_rounding_sim.mjs` (now also asserts
the rate is live), `tests/test_cfa_rounding.py`,
`tests/_admin_attention_drawer_sim.mjs` and
`tests/_homepage_currency_dom_sim.mjs` (both now serve the site row, as the
deployed endpoint does), `tests/test_editor_sku_and_note_prompt.py` (fed the
server's real rate instead of a pinned number), and
`tests/test_supplier_link_budget.py` (patched at the new network seam).

## Deployment

Render auto-deploys `main`. After merge: check `/healthz`, confirm Admin →
Settings shows the owner's rate, run the `browser-smoke` workflow, and push
one test batch in each currency to verify the converted FCFA supplier cost
and the Location column in both ledger tabs.
