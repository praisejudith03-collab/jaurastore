# JauraStore — Unified Master Fix: investigation, repairs and audit

**Date:** 2026-10-19 · **Branch:** `arena/70ab11b8-jaurastore` (based on `main` @ `6147fe3`)
**Scope:** the seven-item "CRITICAL UNIFIED MASTER FIX" directive — investigate, repair,
verify. The earlier audit-only agreement was superseded by it; everything below that is
marked **patched** is implemented in this tree, and everything marked **reported** is a
finding with a plan rather than a change (mostly where an existing test pins the opposite
design, which is a decision for you).

**Test status of this tree:** `2340 passed, 36 skipped, 0 failed` in 227 s
(`python -m pytest tests/ -q -rs`).
`main` before any edit here: `2276 passed, 36 skipped`. Net new tests in this tree: **+64**.
All 36 skips are environment-gated (playwright chromium, jsdom, acorn — not installed in
this sandbox), so the browser-level layout checks and two DOM sims did not run here.

---

## 0. Verdict against the seven items

| # | Reported | What was actually found | Disposition |
|---|---|---|---|
| 1 | Stale mobile pages force `?v=live` hacks / cache clearing | **Confirmed, five causes.** The homepage was *deliberately* given `s-maxage=3600` + `stale-if-error=604800`; `/api/categories` had `max-age=60` + `stale-while-revalidate=120`; three different strengths of "no-store" were in use; and the asset token was a 24-file manual chore | **Patched** — one strict constant for every document/API response, both cache windows removed, `tools/asset_version.py` automates the fingerprint |
| 2 | French locale auto-detection lost | **Confirmed** — and the reason it was removed is documented: the stored choice was not read back and the visible switch had been deleted, so a French device had no way out. The `[data-lang]` binder survived as dead code with no markup | **Patched** — device locale on every entry, session-scoped `?lang=` link, no visible selector at all, server-side first paint; plus a real FCFA bug found on the way (§2.3, revised §2.6) |
| 3 | Category image upload engine broken; media not persisted | Upload/persistence engine is sound (production never falls back to disk; bucket pinned). The real defects were **format validation** and **thumbnails** | **Patched** (3 gaps); backfill left as a plan |
| 4 | Watchdog misses supplier sold-out; manual "Out of Stock" reverts | **Confirmed — reproduced both halves.** The one critical data bug | **Patched** — durable manual lock + one-directional page-availability fallback + admin release route |
| 5 | Multi-item orders lumped; no per-line cost/URL; net profit not computable | Per-line breakdown **already exists** (`accounting.line_items_for_order`); what was missing is batch-level "other costs" and a manual net-profit override | **Patched** through API + admin table; **one sign convention is your call** (§5.3) |
| 6 | Ledger buttons point at the wrong place | Confirmed: `href="#"`/disabled placeholders, and the only workbook id the app carried is the *reference* sheet, not the ledger | **Patched** — active ledger id with deep links to the right tab |
| 7 | Deep audit + full re-test before commit/deploy | Done | This document, green suite, and §7's risk table |

Multi-currency precision (item 5's last bullet) is **reported, not changed**: five
float-`round()` sites remain (§5.4) because you chose the audit-report path there in the
first round and no instruction reversed it. Say the word and they are a 20-line change.

---

## 1. Zero-cache documents and automated asset versioning

### 1.1 What was already right (and stays)
* Every `/api/*` answer without an explicit policy got `no-store`, and admin writes call
  `_invalidate_catalog_cache()` / `_invalidate_category_cache()` so a save is visible on the
  next request regardless of any client cache.
* `sw.js` is **network-first for navigations** (`networkFirst(req, true)`) and steps aside
  entirely for a forced catalogue refresh (`?_fresh=`), so the worker was never the stale-HTML
  culprit. Offline gets a synthetic page, not a cached shop.
* Uploaded photos are served `public, max-age=300, must-revalidate` — the deliberate
  anti-ghost-image rule (a deleted photo must stop showing within minutes). Unchanged.

### 1.2 The five causes found
1. **The homepage was cacheable by design.** `app.py` served `index.html` with
   `public, max-age=300, s-maxage=3600, stale-while-revalidate=86400, stale-if-error=604800`
   — to shave a cold-start round trip off Google entries. Consequence: the edge could answer
   for an hour, a phone could render a five-minute-old page without asking, and after a failed
   origin it would keep rendering **a page up to seven days old**. This alone explains
   "stale layout / legacy prices after a deploy".
2. **The category list was cacheable and knowingly stale.** `/api/categories` answered
   `public, max-age=60, s-maxage=60, stale-while-revalidate=120`: up to three minutes of a
   stale menu, shared across visitors. A category cover that had just uploaded therefore read
   as broken/absent for minutes — which is also half of what item 3 reported as "images
   disappearing after updates".
3. **Three different strengths of "don't cache"** were hand-spelled across the app: bare
   `no-store`, `no-store, no-cache, must-revalidate`, and `private, no-store` on seven admin
   routes. Any of them satisfies "no storage", but a fleet of literals is how one route ends up
   weaker than its neighbour.
4. **Shipping a JS change required a 24-file manual token bump.** The versioned URLs are
   served `immutable` for a year, so an unbumped token means every returning phone runs the
   previous bundle. The repo's own history proves it happens: `tests/test_brand_icons.py`
   records "*Bumped to 145 to flush the service worker and mobile browser caches … phones kept
   serving the stale js/admin.js?v=144 bundle*".
5. **Nothing stopped `?v=live`-style guesswork** and nothing verified that every page carried
   the same token, so a half-bumped release was possible.

### 1.3 What is in the tree
* **One policy, spelled once:** `security.NO_STORE` =
  `no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0`, and
  `security.NO_STORE_PRIVATE` (same set, `private` in front) for session-scoped answers.
  `sec.apply_headers()` now **forces `NO_STORE` on any `text/html` response** that did not ask
  for something else — so a route that forgets a policy cannot re-open a cache window on a
  page — and `app.py` pins the same on `sw.js`. All eight explicit sites in `api.py`/`app.py`
  were replaced by the constants (health probe, receipts, audit/live reports, accounting
  documents, payment proofs).
* **Both stale windows removed**: the homepage special case and the category `max-age` are gone;
  a test (`test_no_page_admits_a_shared_cache_window`) fails the build if `s-maxage`,
  `stale-while-revalidate` or `stale-if-error` returns on a response.
* **`tools/asset_version.py`** is the automated build fingerprinting:
  `--check` compares the shipped bytes of every fingerprinted asset against the recorded hashes
  (CI runs it inside `tools/ci_check.sh`), `--write` increments the shared `?v=` token across
  all 20 pages, `sw.js`, the three bundles and `css/fonts.css`, updates the three pinning
  constants and re-records the hashes — and is a **no-op when no asset changed**, so a
  backend-only release does not invalidate a year of phone caching. This round it bumped
  **205 → 206** across 28 files by itself.
* **ETag/304 kept where it is free:** `/api/catalog` stays
  `private, no-cache, must-revalidate` + ETag (a ~125 KB grid revalidates with a 304 instead of
  re-downloading, and can never be reused without asking), and `/api/categories` keeps its ETag
  under `no-store` for proxies that ignore the directive. `?v=<digits>` remains the only way to
  buy immutability — `?v=live` buys nothing, and a new test asserts no shipped page advertises
  such a workaround and that every `?v=` equals the build token.

### 1.4 Verification
`tests/test_zero_cache_policy.py` (59 tests): 19 document routes must answer *exactly*
`NO_STORE`; `sw.js` too; 9 dynamic API endpoints carry all five directives; no shared-cache
window anywhere; tokened assets immutable / untokened revalidate; non-numeric tokens never
immutable; ETag 304 on both catalogue and category reads; `Vary: Accept-Language, Cookie`;
`tools/asset_version.py --check` passes and `--write` is idempotent; the fingerprint guard's
failure message names the tool. `tests/test_regression_headers.py` was updated to the new
contract rather than deleted, and the two tests that pinned the removed windows
(`test_ghost_images`, `test_variant_price_range_speed_and_popup`) now assert the strict set with
the reason inline.

### 1.5 Trade-off you should know about
The homepage (and every other page) now costs one origin round trip per navigation — no edge
answer, no `stale-if-error` safety net. Assets (≈350 KB of CSS/JS/fonts) are untouched by that:
they stay immutable and are served from the phone. If a future traffic spike makes HTML TTFB a
problem again, the safe knob is `public, max-age=60, stale-if-error=...` on *non-commercial*
pages only (about/faq/terms) — never on the shop or product pages.

---

## 2. French locale auto-detection and translated content

### 2.1 The history that had to be respected
`LOCALE_DEFAULT_FIX_RELEASE_NOTES.md` (2026-09-27) switched the storefront to
English-always for a specific reason: the device locale was the *only* decision-maker, the
stored choice was written but **never read back**, and the header/menu EN|FR toggles had just
been deleted — so a shopper on a French-language phone in Nigeria was stuck in French with F
CFA and no way out. `tests/test_header_layout_lock.py:128` still forbids putting switches back
in the header or the slide-out menu, and `test_auto_locale_rules.py` pinned
`navigator.language` out of the decision path entirely.

That is why the fix is not simply "turn detection back on".

### 2.2 What is in the tree
* **Precedence in `js/i18n.js` (final, after §2.6):** `?lang=fr|en` on the arriving link,
  remembered for the **browsing session** → **device locale, on every entry** → English. The
  device read is never persisted at all, so nothing can latch a visitor into a language: changing
  the phone's language changes the shop. `fr`, `fr-FR`, `fr-BJ`, `fr-CI`, … all mean French; an
  `en` tag listed earlier wins, because that is how the browser itself picks.
* **No language control exists in the storefront.** The header and the slide-out menu were
  already switch-free (`tests/test_header_layout_lock.py`), and a floating EN|FR pill was built
  for this fix and then **removed on the owner's instruction the same day** — see §2.6. What is
  left of that control is pinned out of the tree: `js/store.js` carries no `data-lang` markup,
  no `[data-lang]` binder and no `.is-on` highlight, `css/style.css` has no `.lang-float` rule,
  and `test_auto_locale_rules.py::test_no_language_selector_exists_anywhere_in_the_storefront`
  fails the build if any of it comes back. The pre-existing `.lang-switch` CSS block is *not* a
  control — it is the currency switch's shared styling, and `test_browser_smoke.py` proves no
  `.lang-switch` element renders.
* **First paint, server-side:** `app.py` resolves the same precedence ( `jaura_lang` cookie →
  `Accept-Language` → English) and stamps `<html lang="fr">` on the way out, with
  `Vary: Accept-Language, Cookie`. An English visitor gets the file **byte-for-byte as it ships**
  (asserted), so nothing about the common path changed, and the rewrite is wrapped so a failure
  degrades to a language flash, never a 500. It was verified to survive the gzip pass with a
  correct `Content-Length` (that pairing is now a test, because `app.py` compresses after the
  rewrite and reads the body itself).
* **Translated content** was already wired: product/category `name_fr` / `description_fr` are
  normalised from the **live** catalogue in `js/store.js:414-425`, banner and welcome copy read
  their `_fr` site fields, and the strings that were hard-coded in English ("Most viewed right
  now", the checkout zone-group headings) already go through `t()`.

### 2.3 A real FCFA bug found while proving item 2's second bullet
> "Ensure French locale views dynamically pull live real-time database prices in FCFA and sync
> accurately with active catalog records."

They did not. `toCfa()` intentionally returns **0** when no live FX rate has landed yet ("a
conversion never runs on a hardcoded rate"), and `priceOf()` preferred the conversion over the
row's own `priceCfa`, so **a French-locale visitor — whose currency is *locked* to F CFA by
`currencyLocked()`, with no ₦ to fall back to — saw "0 F CFA" on every card** until `/api/site`
answered, and for the whole visit if the rate upstream was down. Fixed: `priceOf()` now falls
back to the catalogue's own CFA figure (rounded up to the shop's 50 F CFA step) whenever the
conversion cannot run. The raw converter keeps its refuse-to-guess behaviour, and a test pins
both halves (`the sandbox really has no live rate`, `with no live rate a CFA price is the
database figure, never 0`, `French device: every product in the grid carries a price`).

### 2.4 One consequence to confirm
Detection keys on **language, not country**: a visitor in France with `fr-FR` now gets French and
F CFA. That follows your instruction literally, and the app has never used an IP/geo signal (by
design). If you want FCFA only for francophone *West African* locales while a Paris visitor gets
French with ₦, that is a new rule and needs saying — it is a 5-line change in
`js/store.js:currencyLocked()` plus its test.

### 2.5 Verification
`tests/_auto_locale_sim.mjs` boots the real `i18n.js` + `store.js` + `app.js` in a stubbed
browser: **109 checks, all passing**, covering device-decides-on-every-entry (both language
directions, list order, unknown locales), the session-scoped `?lang=` link surviving to the next
page and *not* to a new visit, the retired `localStorage` key being purged rather than honoured,
`<html lang>` on the French path, the absence of any language control in the rendered chrome, and
the CFA price fallback. `tests/_naira_first_cart_pill_sim.mjs` (Naira-first + cart drawer) moved
to the same session semantics and passes. The static rule tests were rewritten rather than
removed, and they still forbid `navigator.language` everywhere *except* the detector.

### 2.6 Same-day revision: the selector went away, the device became the whole rule

The first version of this fix restored detection *and* shipped a visible EN|FR pill, on the
reasoning that detection without a way out was the 2026-09-27 defect. The owner's revision was
explicit and removes that reasoning: **no visible language selector at all**, and the shop listens
**strictly** to the device/browser locale on entry, with French ⇒ FCFA from the live rate.

Implemented as:

| Piece | Change |
|---|---|
| `js/store.js` | floating EN\|FR pill, its `[data-lang]` click handler, its `.is-on` highlight and a dead shadowing `const currentLang` all deleted; the footer's floating column is currency + WhatsApp again, exactly as before |
| `css/style.css` | every `.lang-float` rule deleted (the pill's `bottom` restatements in all three contexts and the `body.ja-fr` visibility override) — the file returned byte-for-byte to its pre-fix state |
| `js/i18n.js` | `persist()` writes **sessionStorage + a session cookie** only; `storedChoice()` reads only the session; the device path writes nothing; `migrateStaleLocale()` now **clears the retired `localStorage` key unconditionally** |
| `checkout.html` | the pre-paint payment-method default no longer reads `localStorage`; it resolves the session choice and otherwise runs its own copy of the device detector, so a French phone pre-selects the Benin/Togo CFA card instead of the Naira one |
| `app.py` | `_document_lang()` unchanged in logic (session cookie → `Accept-Language`) and re-documented, because the cookie it mirrors is now session-scoped |
| `i18n.py` | the translation collector seeds `sessionStorage` instead of `localStorage`, which is the only store the storefront reads now |

Why the persistent key had to be **purged**, not merely ignored: with no control on screen, a
`jaura_lang` value stored in the pill era would override the device forever and could never be
undone — that is precisely the failure that made auto-detection get switched off on 2026-09-27.
Clearing it means every visitor's language is, from the next page load, their browser's.

What is deliberately **kept**: the `?lang=fr|en` link override, scoped to the session. French
WhatsApp broadcast and support links are an owner feature (`categories.html?lang=fr`, order
notifications) and they must not flip to English when the shopper taps the next product; dying with
the tab means they also cannot quietly become a permanent override. Session scope is also what
keeps server and client in lockstep — `app.py` reads the same session cookie, so there is no
language flash on the first paint of a linked visit.

Two pinned behaviours were **re-pinned rather than preserved**, and this is stated because it
reverses a previous owner note: the 2026-09-27 rule "French is opt-in only" is now dead, and the
pill's own layout tests were replaced by a test that the pill does not exist.
`tests/test_banner_french.py::test_language_switch_repaints_the_banner` pinned the banner repaint
inside the pill's click handler; the guarantee is real but the click is not, so it is re-pinned on
the `ja:lang` listener that every language change fires (and on the absence of a `[data-lang]` binder).

**One gap the revision exposed, and closed:** "FCFA from the live exchange rate" was only true once
`GET /api/site` answered — and on a slow phone the grid paints first. A francophone visitor could
therefore sit on the per-product `priceCfa` fallback indefinitely, with no currency pill to tap to
force a repaint (the pill is hidden in French mode by design). `applySiteConfig()` now dispatches
`ja:rerender` when the live figure differs from the one the page priced with — in-memory redraw
only, deliberately *not* `ja:currency`, which would also refetch the whole catalogue past every
cache on every page load. Pinned at the end of the English block in `tests/_auto_locale_sim.mjs`
(price moves, exactly one repaint, zero catalogue refetches).

---

## 3. Category images and persistent cloud media

**Already correct, verified:** `storage._save()` (`storage.py:380`) never falls back to the
container disk in production — a failed cloud write errors instead of parking a file that a
redeploy deletes; `supabase_store._bucket()` ignores `SUPABASE_BUCKET` so no environment mistake
can send media to a private bucket (`tests/test_storage_boundaries.py:46`); category covers
round-trip `image_url` through `save_categories_table` **and** the `growth_settings` mirror, so a
saved cover survives a cold start. The README's opposite claim was stale and is corrected.

**Patched here:**
1. `storage.ensure_browser_displayable()` (`:320`), called from `save_image` and `save_asset`:
   a `.heic`/`.tiff` phone original is converted to a progressive JPEG or refused in plain
   language, instead of being stored and rendering as a broken icon. Allowlisted formats are
   never rejected on decode failure, so nothing that used to upload stops uploading.
2. Category covers now queue a thumbnail like product media does.
3. `store_thumbnail_bytes` (`:516`) wrote a `/uploads/...` companion next to the cloud object —
   ephemeral on Render, so a thumbnail URL could outlive its bytes. It now fails closed in
   production: full-size cloud URL kept, thumbnail skipped, nothing half-persisted.

**Left as a plan:** a backfill for historical rows still holding relative `/uploads/...` paths,
and two docs (`DEPLOYMENT_SUPABASE.md:67`, `.env.example:108`) that still present
`SUPABASE_BUCKET` as if the app read it. Note that item 1's `/api/categories` fix is also a
media-persistence fix: an uploaded cover used to be invisible for up to three minutes, which is
how "the image disappeared after updating" was experienced.

---

## 4. Watchdog supplier sync and the out-of-stock lock

### 4.1 Root cause, reproduced
`catalog.normalize()` (`catalog.py:669`) does not persist `stockStatus`, so an admin marking a
product "Out of stock" survives only as `stock_quantity = 0`. The watchdog then treated
"supplier reachable, product in stock" as authority and restocked to `SUPPLIER_IN_STOCK_UNITS`
(10) — behaviour pinned at `tests/test_supplier_watchdog.py:67` — and the next mirror write read
"In Stock" again. Repro: `/tmp/repro_oos.py`.

The lock cannot be a product column: `supabase_store._upsert_products_resilient` refuses the
whole save on an unknown key, and `schema_sections/15`–`16` are on hold
(`schema_sections/README.md:21`). So it is durable data instead — no migration, and it survives
deploys.

### 4.2 What is in the tree
* Manual stock lock in `growth_settings` (`supabase_store.MANUAL_STOCK_LOCKS_KEY`,
  `:2327-2414`, dev file mirror) + `catalog.manual_stock_locks / is_stock_locked /
  remember_ / forget_` (`:1214-1299`), settled inside `upsert` (`:3087`) and `replace_all`
  (`:3454`) so **every** write path passes the gate — admin save, bulk import, catalogue mirror.
* While locked: `apply_supplier_stock` returns `(None, "locked-out", True)` and
  `derived_stock_status` reports `"out"`. Owner restock clears the lock automatically; a plain
  `quantity = 0` without the flag does not lock (verified in `/tmp/verify_lock2.py`).
* Watchdog is lock-aware: `is_locked_out` / `_restock_refused` (new `manual_stock_lock` warning),
  locked products are dropped out of `_active_supplier_urls` so they stop eating the 2-links-per-tick
  budget, and `tick` / `nightly_sweep` / `_last_summary` report a `locked` count.
* Second half of the report — "sold out at the supplier still shows In Stock": a variant-free
  product whose supplier page states sold-out in plain HTML parsed to **zero rows**, warned
  `supplier_no_variants` and wrote nothing. `supplier_watchdog._page_availability_row()` (`:793`)
  now reads availability around the product name. It is **one-directional on purpose**: it can
  mark an item out, never restock from a loose "in stock" phrase, so a rewritten supplier
  template can never invent stock. Verified: `p-oos` 5 → 0.
* Admin: `GET /api/admin/stock-locks`, `POST /api/admin/stock-locks/<pid>/release`
  (`api.py:5730/5779`), registered in the admin route ledger and the reporting matrix.

### 4.3 Boundaries kept (not bugs I will "fix" silently)
* `tests/test_no_background_supplier_sync.py:13,18` forbids a separate supplier worker; the tick
  stays in-process at 2 links with a 1 h per-URL cooldown, so "periodic" means bounded. True
  high-frequency rechecks need that pin consciously rewritten plus a Render cron (P3).
* A manual per-**variant** "Out of stock" is still not lockable, because availability is derived
  from variant quantities (`tests/test_admin_stock_payload.py:85`). Locking one variant needs a
  variant-level override concept (P2).

---

## 5. Multi-item orders, batch costs, profit and currency precision

### 5.1 Already present
`accounting.line_items_for_order` / `apply_line_items` store per-line `saleAmount`,
`supplierCostNgn`, `supplierLink` keyed by `lineId`, and `push_batch_lines` writes those lines
into the workbook. Orders were never lumped server-side; the gap was batch-level.

### 5.2 Patched in this tree
`accounting.batch_custom_cost` (`:972`), `batch_net_profit_override` (`:985`),
`batch_profit_source` (`:1039`), rewritten `batch_net_profit` (`:1004`),
`batch_totals(..., custom_cost=)` (`:1046`); `sales_summary` folds `customCosts` /
`manualAdjustment` into net profit (`:1239`). API: `PUT /api/admin/accounting/batches/<bid>`
accepts `customCost` (alias `otherCost`) and `netProfitOverride`; validators at
`api.py:3032/3047`; batch cards expose `customCost` / `netProfitOverride` / `profitSource`; the
push route charges a custom cost **once**, on the fee-ledger currency group, so a mixed-currency
push cannot double-count it. Admin: the sales-insights batch table has an "Other costs" column and
an editable Net profit cell, saved field-by-field through `saveBatchLedgerField`
(`js/admin.js:4445`) so editing one never clobbers the other; blank override = calculated, and
`profitSource` says which you are looking at.

### 5.3 Your call — the sign of batch transport
The directive says *"summing supplier costs, subtracting discounts, and accounting for batch
transport fees"*; the code and `tests/test_accounting*.py` have always **subtracted** transport:

```
net = revenue − discounts − supplier − transport − other costs
```

I did not flip it (the tests pin it and "accounting for" is ambiguous). If transport is meant to
be recovered from the customer and therefore added back, it is one line in
`accounting.batch_net_profit` plus two test updates.

### 5.4 Reported, not changed — float `round()` on money
`accounting.py:245`, `api.py:1421`, `api.py:1828`, `api.py:4432`, `currency.py:120` all do
`int(round(float_expr))`. Python's `round()` is half-to-even and the float multiply loses
exactness first, so an exact `.5` can land 1 unit low while everything else in the ledger uses
`ROUND_HALF_UP`. Worst case observed is a ₦1 line-level difference against a pushed sheet. Fix is
mechanical (P1). Everything new in this tree uses Decimal + explicit rounding mode.

Also: a push may mix NGN and CFA orders under one `exchangeRate`/`transferFee`, with the fee
charged once to the fee-ledger group — worth confirming against how you reconcile.

---

## 6. Ledger buttons

`google_sheets.ACTIVE_LEDGER_SPREADSHEET_ID = 1qT_YYqPt4pfTL7URxbePZnmFLLVljaRbBhh0qt_5AzE`
(`:74`) with `ACTIVE_LEDGER_TABS` (`:75`: NGN → `NGN Ledger`, CFA → `CFA Ledger`),
`active_ledger_spreadsheet_id()` (`:77`, optional `GOOGLE_LEDGER_SPREADSHEET_ID` override),
`ledger_tab_url()` (`:96`, resolves the real `gid` so the click opens the **tab**, not the top of
the file) and `ledger_link()` (`:120`). `integration_status()` (`:798`) always returns
`ledgers[NGN|CFA].url` + `activeSheet`, and `connected` is computed only from the app's own
workbook ids — a fallback link can no longer pretend to be a live Drive connection.
`js/accounting.js` gained `ledgerButton(currency, label)`, rendering an anchor only for
`https://docs.google.com/` URLs, in both connected and disconnected states (the old markup was
`href="#"` plus disabled placeholders — the reported symptom). `render.yaml:76 GOOGLE_SHEET_ID`
stays the ITEMFLOW reference workbook and must not be repurposed.

---

## 7. Audit findings, risks and what this sandbox could not verify

| Item | Risk / finding | Mitigation |
|---|---|---|
| §1 | Every navigation now costs an origin round trip; no `stale-if-error` cushion when the dyno is down | Intended. A cold/failed instance now shows the app's own 404/500 shell rather than a week-old page; assets stay immutable |
| §1 | Two pinned tests asserted the removed windows and had to be updated | Updated with the reason inline, not deleted — `test_ghost_images`, `test_variant_price_range_speed_and_popup`, `test_regression_headers`, `test_api` |
| §2 | A French-tagged device in Nigeria now gets French + FCFA, with no in-page way back | That is the directive, and the only escape is the device's own language setting — safe only because nothing is latched (the store is session-scoped and the legacy key is purged). If the metric moves, the fix is a device-setting guide, not a toggle |
| §2 | `fr-FR` (France) also gets F CFA | §2.4 — say if you want currency keyed on region instead |
| §2 | Currency now has a second source (the row's `priceCfa`) when the rate is unavailable | Only used when conversion cannot run at all; pinned by two sim checks |
| §4 | A deliberately locked-out product can look like "the sync is broken" | `manual_stock_lock` warning, `locked` count, and the release route exist for exactly this |
| §4 | Cloud behaviour ran against the repo's PostgREST/Drive fakes, not real Supabase | Watch the first production tick |
| §5 | Batch ledger fields are admin-table only; the Accounting desk shows revenue/expenses but not `profitSource` | P5 |
| §7 | 36 skips include the browser smoke suite and two jsdom DOM checks | `playwright install chromium` + `npm install --no-save --prefix /tmp/uidom jsdom acorn` in CI before merge |
| §7 | `tools/catalog_watchdog.py:94` writes `.watchdog-stock.json` into the repo root when `WATCHDOG_STOCK_STATE` is unset (it appeared during these runs) | Add to `.gitignore` or default to `tempfile.gettempdir()` |
| §7 | Per-file content hashing (`style.a1b2c3.css`) was considered and **not** done | Needs renaming shipped files + rewriting refs + CSP review; the token mechanism gives the same guarantee, and it is now automated and CI-enforced |

---

## 8. Suggested patch plan (next session)

| P | Change | Files | Risk | Effort |
|---|---|---|---|---|
| P1 | Replace the five float `round()` money sites with `decimal_value(...).quantize(…, ROUND_HALF_UP)`, one test each | `accounting.py`, `api.py`, `currency.py` | low | S |
| P2 | Variant-level unavailability so a manual per-variant OUT survives the watchdog | `catalog.py`, `api.py`, `js/admin.js` + `test_admin_stock_payload` pin | medium | M |
| P3 | Supplier-sync cadence decision: keep the bounded in-process tick, or relax `test_no_background_supplier_sync.py` + add a Render cron | `render.yaml`, `supplier_watchdog.py`, tests | medium | M |
| P4 | Backfill legacy `/uploads/...` rows to bucket URLs; clarify `SUPABASE_BUCKET` in the two docs | new `tools/` script, 2 docs | medium | M |
| P5 | Surface `profitSource` + batch custom cost in the Accounting desk; settle the transport sign (§5.3) | `js/accounting.js`, `accounting.html` | low | S |
| P6 | CI: install playwright/jsdom/acorn; ignore `.watchdog-stock.json`; add `tools/asset_version.py --write` to the release workflow | `tools/ci_check.sh`, `.gitignore`, workflow | trivial | XS |
| P7 | Optional: first-visit language hint ("Shop in English ?") for device-inferred French, dismissible without writing a choice | `js/store.js`, `js/i18n.js` | low | S |

**Deploy checklist:** no schema migration anywhere in this tree (locks live in
`growth_settings`, batch fields in the batch JSON); `schema_sections/15`–`16` stay on hold;
deploy `app.py`, `security.py`, `api.py`, `catalog.py`, `supplier_watchdog.py`, `storage.py`
together; the shipped assets already carry token **v206**, so no manual bump is needed; then
`bash tools/ci_check.sh` (it now runs `tools/asset_version.py --check` before pytest).

---

## 9. Repro / verification scripts (kept out of the repo so `git status` stays clean)

`/tmp/repro_oos.py` (manual OUT restocked to 10) · `/tmp/repro_lock.py` (pre-fix failure mode) ·
`/tmp/verify_lock.py`, `/tmp/verify_lock2.py` (lock blocks supplier restock; owner restock clears
it; quantity 0 without the flag does not lock) · `/tmp/check_headers.py`, `/tmp/check_cookie.py`
(per-route headers, cookie-vs-device precedence) · `/tmp/etag.py` (304 revalidation) ·
`/tmp/gzip_lang.py` (rewrite + gzip + Content-Length) · `/tmp/baseline.log`,
`/tmp/final.log`, `/tmp/final2.log`, `/tmp/final3.log` (2276 → 2 failures, both guard rails
catching my own edits → 2277 → **2340 passed, 36 skipped**).

## 10. Files changed in this tree

`git diff --stat` → **51 files changed, 1989 insertions(+), 435 deletions(-)**, plus three new
files (`tools/asset_version.py`, `tests/test_zero_cache_policy.py`, this report).

```
app.py  security.py  api.py          cache contract, lang first paint, asset refs
js/i18n.js  js/store.js  css/style.css  checkout.html  device locale, session-scoped link, no selector, CFA fallback
accounting.py catalog.py supabase_store.py supplier_watchdog.py storage.py
google_sheets.py js/accounting.js js/admin.js                  items 3-6 (earlier round)
20 × *.html sw.js css/fonts.css tools/sync_favicon_head.py    token 204→205→206
README.md LOCALE_DEFAULT_FIX_RELEASE_NOTES.md                 docs brought in line
tests/test_zero_cache_policy.py (new) test_regression_headers test_auto_locale_rules
      test_header_layout_lock test_asset_cache_token test_admin_features
      test_ghost_images test_variant_price_range_speed_and_popup test_api test_brand_icons
```

Suggested commit split: **(1)** zero-cache documents + automated asset versioning, **(2)**
French locale restore + FCFA price fallback, **(3)** manual stock lock + watchdog sync, **(4)**
media format/thumbnail gate, **(5)** accounting batch ledger fields + ledger deep links.
