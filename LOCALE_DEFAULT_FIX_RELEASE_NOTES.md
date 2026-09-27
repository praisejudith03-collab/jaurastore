# Locale-selection / defaulting fix — release notes (2026-09-27)

## The bug

The live storefront was opening in **French** (F CFA, French checkout,
French delivery-zone headings, French "Most viewed" rail) for visitors who
should have seen the **English** storefront (₦ Naira). Root cause:
`js/i18n.js` picked the interface language from the visitor's
**device/browser locale** (`navigator.languages` / `navigator.language`),
with no way for a shopper to opt back into English, and no code path ever
read back an explicit choice that had been written to storage.

This is a locale **selection / defaulting** bug — the French catalogue
translations themselves are correct and untouched.

## The fix

1. **English is now the default for every visitor.** `js/i18n.js` no longer
   consults `navigator.language(s)` (or any IP/geo signal — none ever
   existed here) to pick the interface language. `detectBrowserLang()` is
   kept only as an inert diagnostic helper; it is not called by the
   language-decision path any more.
2. **French is opt-in only**, via:
   - opening a link with `?lang=fr` (support links, WhatsApp broadcasts,
     QA), or
   - a call to `I18N.setLang("fr")` (dev tools / the e2e harness / any
     future language control).
   Either action now genuinely **persists** the choice to `localStorage`
   (`jaura_lang` — the existing storage mechanism), mirrored to
   `sessionStorage` and a cookie for private-mode browsers, **and the
   choice is read back on every later page load** — this read-back was the
   missing half of the previous implementation, which wrote the choice but
   then ignored it on the next navigation.
3. **Stale/garbage values are migrated away.** Anything under `jaura_lang`
   that is not exactly `"en"` or `"fr"` is purged on load
   (`migrateStaleLocale()`), so corrupted or leftover data can never leak
   French/F CFA onto a fresh English visitor. A *valid* prior "en"/"fr" is
   left untouched — it is a real, explicit past choice and must keep being
   honoured in both directions (English stays English/₦, French stays
   French/F CFA).
4. **Two real localisation gaps were closed** (found while auditing every
   surface the owner listed):
   - `paintMostViewed()` in `js/app.js` had the "Most viewed right now"
     heading and "Shop all ›" link hard-coded in English; they now render
     through `t("home.mostViewed")` / `t("home.shopAllArrow")` (new i18n
     keys, English + French) like every other storefront string.
   - The checkout delivery-zone `<select>` built from the server's zone
     list (`zoneGroups()` in `js/app.js`) had its three optgroup headings —
     "Nigeria (₦ Naira)", "Benin & Togo (F CFA)", "Pickup / collection" —
     hard-coded in English, and the placeholder option referenced a
     `window.JA_i18n` global that never existed (so it silently fell back
     to English every time). Both are now driven by the same `t()`
     helper the rest of the file already uses, with new i18n keys
     (`ck.zoneGroupNaira`, `ck.zoneGroupCfa`, `ck.zoneGroupPickup`).
5. **The floating currency pill is smaller and finer** (owner follow-up
   request): 10px labels (was 11px), 5px side padding (was 6px), tighter
   fixed minimum widths per currency (₦ 24px / F CFA 36px, was 30/48) — it
   still cannot resize when the active currency changes, still sits pinned
   above the WhatsApp bubble, and the cart-drawer fade-away behaviour for
   both floating controls is unchanged.
6. Two **pre-existing, unrelated** static-CSS test assertions
   (`test_header_layout_lock.py::test_currency_pill_position_and_palette_are_pinned`
   and `::test_whatsapp_bubble_position_is_pinned_under_the_pill`) were
   anchoring a regex to the start of the CSS value, which broke once the
   pinned geometry was wrapped in `calc(... + env(safe-area-inset-bottom,
   0px))` for phone safe areas in an earlier change. Fixed to search for the
   pixel figure instead of requiring it at the start of the string — no
   production CSS changed by this. Likewise
   `test_naira_first_and_cart_pill.py::test_the_floats_clear_the_bottom_dock`
   was a plain, cascade-unaware text search that could match a superseded
   `.wa-float` rule from an earlier redesign instead of the one the browser
   actually applies; it now resolves the winning declaration the same way
   the rest of the suite does (last declaration wins for an exact selector).

## What did NOT change

* The French catalogue translations (`nameFr`, `descriptionFr`, category
  names, option values) — untouched, still full.
* The delivery-zone dropdown's own internal wiring (fare formatting,
  `zoneLabel()`) — it was already reading the active locale correctly; it
  is the *default* language that was wrong, plus the two gaps in item 4
  above.
* Admin image compression/upload behaviour.
* The header/menu layout lock (no language or currency dropdown returns to
  the header or the slide-out menu — French stays reachable only through
  `?lang=fr` / `I18N.setLang`, exactly as before).

## Verified

* `node --check js/store.js js/app.js js/admin.js sw.js` — OK.
* `python3 -m pytest tests/ -q --ignore=tests/test_delivery_seeds_postgres.py --ignore=tests/test_stock_rpc_concurrency.py`
  — 1339 passed, 18 skipped (the skips are the real-Chromium
  `test_browser_smoke.py` tests; no Chromium download is possible in this
  sandbox, they run in CI).
* `node tests/_auto_locale_sim.mjs` and `node tests/_naira_first_cart_pill_sim.mjs`
  — every check passes, including the new regression coverage:
  - a fresh visitor defaults to English/₦ Naira, even when the simulated
    device reports French;
  - an explicit `?lang=fr` (or a previously stored explicit French choice)
    stays French/F CFA across a brand-new "page load" sandbox (simulating
    reopening the tab / navigating to another page);
  - an explicit `?lang=en` (or a previously stored explicit English choice)
    stays English/₦ Naira the same way, even on a simulated French device;
  - stale/garbage values under `jaura_lang` are purged and cannot force
    French onto a fresh English visitor;
  - the checkout delivery-zone dropdown's group headings match the active
    locale in both directions;
  - the "Most viewed right now" heading, "Shop all" link and product-card
    buttons match the active locale in both directions.
* The shared asset cache token was bumped `153 → 154` (all `?v=` references,
  `sw.js` `VERSION`/precache list, `tools/sync_favicon_head.py` `TOKEN`, and
  the `SHARED_TOKEN` / `ASSETS` fingerprints in
  `tests/test_brand_icons.py` / `tests/test_asset_cache_token.py`).
