# Site redesign + automatic language/currency — release notes and owner checklist

Companion document for the 2026-09-27 mission: automatic language/currency
detection, checkout cleanup, header redesign (Option A), floating currency
pill, and the finer hero. It records **what changed**, **what was verified
automatically**, **what could not run in the build sandbox**, and the
**release checklist** (deploy steps are intentionally NOT done by the agent).

Moving text banner, product prices, database values and every other layout
element were left untouched, exactly as instructed.

---

## 1. What changed

### 1A — automatic language & currency on every page load

* `js/i18n.js` now detects the device's primary language on load
  (`navigator.languages[0]` / `navigator.language`):
  French (`fr`, `fr-FR`, `fr-BJ`, any `fr-*`) → French interface; English
  (`en`, `en-NG`, `en-US`) and everything else → English (default).
  The `?lang=fr|en` URL parameter remains as a support/QA override.
  Previously stored language copies are deliberately no longer read — they
  could only have been written by the removed manual switch.
* `js/store.js` follows the detected language:
  * French → `JA.currency()` is always **CFA**, `setCurrency()` cannot escape
    it, the floating pill is hidden (`currencyLocked()`).
  * English → **Naira first**: with no explicit shopper choice the shop opens
    in ₦. The manual ₦/FCFA choice made through the floating pill is kept
    (localStorage), so tapping FCFA on one page is still FCFA on the next and
    surfaces the FCFA gateway at checkout — per the mission's
    "*unless the user manually toggles active currency to FCFA*".
* **Decision to review (flagged):** "prices render in NAIRA FIRST on page
  load" is implemented as *the default web state* (fresh device / no stored
  choice). A returning English shopper who manually chose FCFA keeps FCFA.
  If you want a hard reset to ₦ on *every* page load instead, it is a
  one-line change (drop the stored read in `JA.currency()`).

### 1B — popups & menu switches removed

* The checkout confirm() popup
  ("Benin/Togo delivery detected. Choose your currency…") is gone, together
  with its zone/country listeners. Delivery fares stay silent and are
  painted in the shopper's active currency (`paintDeliveryZones` +
  `paintCheckoutTotals`, unchanged other than the removal).
* The static **EN | FR** and **₦ | F CFA** toggles at the bottom of the
  slide-out menu are removed (`.au-menu-tools` no longer rendered).

### 1C — header redesign (Option A)

* `headerHTML()` in `js/store.js` now renders:
  **hamburger [≡] left · JAURA logo centred · search [🔍] + cart [🛒] right.**
* The old EN|FR and ₦|F CFA header dropdowns are completely removed; the
  desktop `.nav-left` link row is also gone (the hamburger menu, available
  at every width, carries the same links).
* `.header-inner` is now `display: grid; grid-template-columns: 1fr auto 1fr`
  at every width so the logo is always dead-centre; the ≥ 981px block pins
  the same geometry with `!important`.
* The two golden butterflies (`.header-flies`) are untouched.

### 1D — floating currency pill (English storefront only)

* New `.cur-float` element mounted from `footerHTML()`:
  fixed `bottom: 85px; right: 20px; z-index: 9999`, stacked directly above
  the WhatsApp bubble, which is pinned at
  `bottom: 20px; right: 20px; z-index: 9998`.
* Styling per spec: white `#FFFFFF` pill, thin lavender border `#D8B4FE`,
  soft shadow; active button bold white on vibrant purple `#7C3AED`.
* Tapping a currency calls the existing shared `JA.setCurrency()` path —
  every price on screen recalculates **without a reload** (proven in the
  sim below). Hidden in French mode (JS `hidden` + `body.ja-fr` CSS gate)
  and on the admin page.

### 1E — hero ("the finer look")

* The top subheading is now the greeting **"Welcome. Ready to shop?"**
  (French: **"Bienvenue. Prêt à faire vos achats ?"**), with a soft
  slide-in entry animation.
* The headline keeps its text **verbatim**
  ("Experience effortless elegance and curated essentials") and now renders
  in the luxurious fine serif stack
  (`Cormorant Garamond → Playfair Display → Bodoni MT → Didot`),
  weight 400, open `line-height: 1.14`, elegant `letter-spacing: 0.015em`,
  with a soft slide-in entry.
* The purple CTA pops in with a soft zoom on load, then runs a gentle pulse
  every ~4.6 s. `prefers-reduced-motion` disables all of it.
* The moving text banner was **not** touched (verified by test).

---

## 2. Verified automatically

| Check | Result |
| --- | --- |
| `pytest tests/` (full suite incl. new guards) | **1323 passed**, 18 skipped* |
| Node syntax check of every shipped JS file | pass (via suite) |
| `node tests/_auto_locale_sim.mjs` — boots the real `i18n.js` + `store.js` + `app.js`: detection matrix (fr/fr-FR/fr-BJ/en-NG/en-US/other/empty, `?lang=` overrides), French FCFA lock + pill hiding + body class, English NGN default, pill tap recalculates prices without reload, French checkout hides the Naira pay-card & surfaces the FCFA bank sheet, popup removal | **46/46 pass** |
| `tests/test_auto_locale_rules.py` (static pins + sim wrapper) | 8/8 pass |
| `tests/test_header_layout_lock.py` (rewritten to lock Option A: grid row, centred logo, no header/menu switches, pill & WhatsApp geometry, butterflies) | 12/12 pass |
| Flask server boots; `/`, `/shop.html`, `/checkout.html`, `/css/style.css` | HTTP 200 |

\* The 18 skips are the real-Chromium tests in `tests/test_browser_smoke.py`:
**no Chromium download is possible from this sandbox**, so they are skipped
here. They run in GitHub CI where Playwright installs Chromium.

### Manual-test notes for the reviewer

* French behaviour can be previewed anywhere with `?lang=fr`
  (e.g. `/checkout.html?lang=fr`) and English with `?lang=en`.
* `tests/e2e.py`, `tests/_banner_sim.mjs`, `tests/_admin_banner_click_sim.mjs`
  and `tools/browser_smoke.py` were updated to the new controls (floating
  pill / `I18N.setLang`) and assert the centred-logo geometry, so the
  deployed-site browser check keeps matching reality after the merge.

---

## 3. Release checklist (owner / CI)

1. ✅ **Cache token bumped** `151 → 152` everywhere (254 `?v=` references in
   all `*.html` / `js/*.js` / `css/fonts.css`, `sw.js VERSION` +
   `jaura-v152` precache list, and `SHARED_TOKEN` in
   `tests/test_brand_icons.py`, and the favicon head generator
   `tools/sync_favicon_head.py` TOKEN), so phones holding the v151 service
   worker receive the new JS/CSS without clearing their cache.
2. Merge this PR (`main` auto-deploys on Render).
3. Run the deployed browser check:
   `Actions → Deployed mobile browser check` (or
   `python tools/browser_smoke.py https://jaurastore.com.ng --wait 900`).
4. Spot-verify on a French-locale phone (or DevTools `?lang=fr`):
   French UI, FCFA everywhere, no currency pill, FCFA gateways at checkout —
   and on an English phone: English UI, ₦ prices first, pill bottom-right
   above WhatsApp.
