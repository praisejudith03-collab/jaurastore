# Next Coding Session — Master Prompt, PR & Deployment Checklist

Prepared 2026-10-08 on `arena/f062eef6-jaurastore` (branched from `main` @ d86da2f,
which merged `arena/dae89200-jaurastore` — the branch that passed all 2,082
tests and resolved the schema alignments).

Every file location below was verified against the current checkout. Line
numbers are where the work starts, not the full blast radius — grep before
editing.

---

## 0. Master prompt (paste at the start of the next session)

> We are continuing the JauraStore exchange-rate, supplier-cost, and ledger
> hardening on branch `arena/f062eef6-jaurastore`. The previous branch passed
> the full suite (2,082 tests via `bash tools/ci_check.sh`). Execute the five
> work items in `NEXT_SESSION_PR_DEPLOYMENT_CHECKLIST.md`:
> (1) remove every hardcoded 0.44 exchange-rate fallback so unpushed staged
> orders and calculations strictly use the live admin-controlled `cfaRate`;
> (2) automate supplier cost capture in NGN and auto-convert to FCFA at ledger
> push time; (3) add a Location / Destination column to both ledgers and the
> staging queue; (4) delete `css/accounting-clean.css` and fix the mobile
> admin tab clipping plus the cramped live-rate preview text; (5) add SSRF
> safeguards (time/size limits, private-IP blocks) to backend URL fetching
> and bump static asset versions so mobile browsers pick up the new files on
> Render deploy. Run `bash tools/ci_check.sh` before pushing; open the PR from
> `arena/f062eef6-jaurastore` only.

---

## 1. Work items — locations & requirements

### 1.1 Dynamic exchange rates — no hardcoded fallbacks

**Rule:** unpushed staged orders and all calculations must read the live,
admin-controlled rate (`growth_settings.cfaRate`, editable in Admin →
Settings). The only acceptable fallback is the server-side
`growth.settings()["cfaRate"]` — never a literal `0.44`.

Verified current hardcoded-`0.44` sites to eliminate or justify:

- [ ] `currency.py:22` — `NGN_TO_CFA = 0.44` (module constant; make it read
  the live rate or delete it).
- [ ] `growth.py:10,27` — `NGN_TO_CFA` constant and the `"cfaRate": 0.44`
  default in settings; `growth.py:81-85` clamps/resets to it;
  `growth.py:156-163` is the canonical `effective rate` helper — route
  everything through it.
- [ ] `app.py:164-166` — `float(growth.settings().get("cfaRate") or 0.44)`
  and the bare `rate = 0.44` fallback.
- [ ] `js/store.js:162,210-211` — `rate: 0.44` and the local
  `NGN_TO_CFA = 0.44` / `fx` seed; the storefront must fetch the live rate
  (same source the admin desk uses) instead of seeding a literal.
- [ ] `js/accounting.js:37` — `currentExchangeRate: 0.44`.
- [ ] `js/admin.js:927,929,931,1189,1521,2904,3400,4885-4886` — inline
  `* 0.44` conversion fallbacks and `settingsFxRate ... || 0.44` catch
  fallbacks. Prefer the shared `JA.toCfa` (already used at 1521/2904) and
  drop the literals.
- [ ] `js/app.js:305,1722` — `0.44` in min-order bound conversion.
- [ ] **Intentional, keep:** `accounting.py:14` `LEGACY_RATE = Decimal("0.44")`
  — the explicitly marked historical baseline for pre-fix batches
  (`accounting.py:277`). Do not wire new code to it; do not delete it.
- [ ] **Copy, not code:** user-facing strings that mention the rate
  (`js/i18n.js:134,409,411`, `js/i18n-phrases.js:159,161`) must render the
  *live* rate, not a frozen "0.44".
- [ ] Admin rate input already exists at `js/admin.js:5016`
  (`NGN → FCFA rate (1 ₦ = ? CFA)`, persisted via `growth.save_settings`,
  `growth.py:116`). The example caption "₦10,000 ≈ FCFA 4,500" should be
  computed from the entered rate, not hardcoded (see item 1.4).

### 1.2 Supplier cost automation & dual-currency conversion

- [ ] `api.py:2985` — order payload fields already include
  `supplierCostNgn`, `supplierCostCfa`, `supplierCostInCurrency`.
- [ ] `api.py:3260-3282` — `fields` / `supplier_fields`
  (`supplierCostNgn`, `supplierUnitPriceNgn`, `supplierQty`, …) — the
  backend order-processing write path.
- [ ] `js/admin.js:4207,4229` — staging/batch display of `supplierCosts`.
- [ ] **Requirement:** fetch the base item supplier cost in **NGN** from
  saved product defaults (admin product form) or an optional supplier
  link/manual input in the staging UI.
- [ ] **Requirement:** when pushing a batch to the **FCFA Ledger**,
  convert the NGN supplier cost with the **active** `cfaRate` at push time
  and save the converted value — never store an FCFA figure derived from a
  hardcoded rate. The NGN Ledger keeps the Naira original.
- [ ] `google_sheets.py:793` reads the `supplier costs` column;
  `_headers()` (`google_sheets.py:397-403`) defines both ledgers'
  `Supplier costs · {unit}` columns — keep header names stable so existing
  workbooks stay compatible.

### 1.3 Customer Location / Destination column

- [ ] `google_sheets.py:397-403` (`_headers`) — add a dedicated
  `Location / Destination` column (e.g. *Nigeria, Benin Republic, Togo*) to
  **both** the NGN and FCFA ledgers.
- [ ] `google_sheets.py:435-446` — `_create_ledger` writes headers to
  `'Orders'!A1:K1`; widen the range to cover the new column (A1:L1).
- [ ] `google_sheets.py:706-709` (`_column`) — header-map lookup with
  positional fallback; add the new column to the fallback map.
- [ ] Staging queue UI (`js/admin.js` staging/order panels) — capture
  customer destination and map it through to the ledger row on push.
- [ ] Backend order processing (`api.py` order/staging endpoints) — accept
  and persist the location field end-to-end.

### 1.4 Dead code cleanup & mobile UI fixes

- [ ] **Delete** `css/accounting-clean.css`. Its only reference is
  `accounting.html:11` (`/css/accounting-clean.css?v=201`) — remove that
  `<link>` (or repoint to the real stylesheet) and confirm `accounting.html`
  still renders correctly. Grep for any other references before deleting.
- [ ] Update `css/style.css` with any styles the accounting page actually
  needs (it currently relies on the clean sheet).
- [ ] **Mobile tab clipping** ("Dashboard" renders as "ard"): the admin
  bottom dock is `.admin-app-nav` — markup at `js/admin.js:4365-4371`
  (`<button data-tab="analytics">…<span>Dashboard</span>`), styles at
  `css/style.css:4230-4246`, mobile overrides at `css/style.css:6754-6759`
  (`font-size: 9.5px; gap: 1px;` — prime suspect for label clipping). Fix so
  all five dock labels render unclipped at 360px width.
- [ ] **Cramped live-rate preview text** (₦10,000 ≈ FCFA 4,500): the note at
  `js/admin.js:5016` (`.admin-note`) — give it room to breathe (spacing /
  wrapping) and compute the example from the live rate.

### 1.5 SSRF safeguards & asset cache bump

- [ ] Backend URL fetching to harden (add a shared guarded-fetch helper):
  - `google_sheets.py:223-232` (`_json_request`, `urllib.request.urlopen`
    with `HTTP_TIMEOUT`) — also `google_sheets.py:257-265,1181-1184`.
  - `mailer.py:136-142`, `observability.py:197-208`, `repo_sync.py:296-300`,
    `i18n.py:83-86`.
- [ ] **Requirement:** enforce (a) a hard timeout (already partially present
  — make it uniform), (b) a **response size limit** (read in chunks, abort
  past a cap, e.g. 2 MB), (c) **private/loopback/link-local IP blocks**
  (`ipaddress.ip_address(...).is_private / is_loopback / is_link_local`,
  covering `127.0.0.0/8`, `10/8`, `172.16/12`, `192.168/16`,
  `169.254/16`, `::1`, `fc00::/7`), and (d) **https-only** for any
  user-supplied URL. Resolve DNS and validate the resolved address, not
  just the hostname string.
- [ ] **Asset cache bump:** static references currently use `?v=201`
  across the HTML files. Bump to **`?v=202`** everywhere (css/js) so mobile
  browsers load the updated frontend immediately after the Render deploy.
  (The blueprint's "v=201" is already live — the bump target is the next
  version.)

---

## 2. PR checklist (before opening the PR)

- [ ] Work only on `arena/f062eef6-jaurastore`; never push to `main`.
- [ ] `grep -rn "0\.44" --include="*.py" --include="*.js" .` — no new
  hardcoded fallbacks; the only remaining hits are the intentional
  `accounting.py` LEGACY_RATE, comments, and live-rate copy.
- [ ] `grep -rn "accounting-clean" --include="*.html" --include="*.css" --include="*.js" .`
  returns nothing; the file is deleted.
- [ ] `grep -rn "v=201" --include="*.html" .` returns nothing (all bumped
  to `v=202`).
- [ ] Run the full gate locally: `bash tools/ci_check.sh`
  (pytest over `tests/` — 171 files / ~2,082 tests — plus the jsdom/DOM
  checks; CI runs the same via `tools/ci_check.sh --ci`).
  Optional but recommended: `bash tools/ci_check.sh --require-browser`.
- [ ] Add/adjust tests for: rate fallback removal, FCFA supplier-cost
  conversion at push time, Location column round-trip, and the SSRF
  guard (private IP, oversize body, timeout).
- [ ] Commit with the repo's message style; push
  `git push origin arena/f062eef6-jaurastore`.
- [ ] Open the PR **from** `arena/f062eef6-jaurastore` **into** `main`
  (`gh pr create --base main --head arena/f062eef6-jaurastore`), body
  referencing this checklist.
- [ ] Wait for the `ci.yml` workflow to go green before merging.

## 3. Deployment checklist (after merge to `main`)

- [ ] Render auto-deploys `main` (see `render.yaml` / `deploy.yml` — the
  workflow is a non-blocking note; no manual deploy action needed).
- [ ] Confirm the new instance is healthy: `GET https://jaurastore.com.ng/healthz`.
- [ ] Verify the live rate endpoint/admin setting reflects the owner's
  current `cfaRate` (Admin → Settings → NGN → FCFA rate).
- [ ] Run the deployed mobile browser check:
  `gh workflow run browser-smoke.yml -f url=https://jaurastore.com.ng -f wait_seconds=900`
  (Playwright; `tools/browser_smoke.py`) — confirms the bumped `v=202`
  assets are served and the mobile storefront renders.
- [ ] On a phone/emulator at ~360px, open `admin.html`: all dock labels
  ("Dashboard", "Products", "Orders", "Sales", "More") fully visible;
  the settings rate note (₦10,000 ≈ FCFA …) is no longer cramped.
- [ ] `accounting.html` renders correctly without `accounting-clean.css`.
- [ ] Push one test staging batch in each currency and confirm:
  supplier cost converts NGN → FCFA at the **active** rate in the FCFA
  Ledger, and the new **Location / Destination** column is populated in
  both ledgers (check the Google Sheet directly; `GOOGLE_SHEET_ID` in
  `render.yaml`).
- [ ] Confirm the catalog watchdog (`catalog-watchdog.yml`, hourly) stays
  green after deploy.
- [ ] Write the release notes: add a `*_RELEASE_NOTES.md` doc at repo root
  summarizing the five items, matching the repo's existing convention.

## 4. Rollback plan

- Revert the merge commit on `main`; Render redeploys the previous build
  automatically.
- If only the frontend is misbehaving, the previous asset version can be
  restored by reverting the `?v=` bump commit — but prefer a forward fix,
  the suite covers the conversion logic.
