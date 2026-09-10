# Currency pill sizing + French catalogue — release notes and owner checklist

This is the companion document for the change that puts PART 2 (fixed currency
size, French for the catalogue) on `main`. It records **what was verified
automatically**, **what could not be run in the build sandbox**, and **the
three things the owner has to do**.

---

## 1. What changed

### 2A — the currency / language pill no longer resizes

The stylesheet carried **six competing rules** for `.currency-switch button`
(base ~line 146, the 640px and 800px media queries, the `.lang-switch button`
restore block, and the "no control under 34px" block), each setting a different
`padding` or `font-size`, and **no rule ever set a width**. So `₦` and `F CFA`
rendered at different widths and the pill visibly jumped as the highlight moved
between them.

A single authoritative block now sits at the **end** of `css/style.css`:

| Property | Desktop | ≤ 640px |
| --- | --- | --- |
| switch height | `34px` fixed, `overflow: hidden`, `flex-shrink: 0` | `32px` fixed |
| button height | `32px` fixed | `30px` fixed |
| button padding | `0 10px` (vertical padding would fight the fixed height) | `0 8px` |
| font | `12px` / `600` / `0.06em`, constant in both states | `11px` / `600` |
| `₦` (NGN) and `EN`/`FR` | `min-width: 40px` | `min-width: 38px` |
| `F CFA` | `min-width: 64px` | `min-width: 60px` |

The base selector and its `.is-on` state are declared in **one shared rule**,
which is what makes it structurally impossible for selecting a currency to
change the box: `.is-on` has colour left as its only lever. Width belongs to
the **label**, not the state.

### 2B — the catalogue follows the language switch

`displayName` already answered `nameFr`. Now so do:

* `displayDescription` — French description, falling back to **English**, never
  to blank;
* `displayOptionValue` — French colour/scent/pack labels from a shared
  vocabulary in `js/i18n-phrases.js` (`window.I18N_OPTION_VALUES`), with a
  per-product `valuesFr` list taking priority;
* `categoryName` — every one of the 14 default categories now carries a
  `nameFr`, so no category renders in English by default.

Also:

* `normalizeServerProduct` folds `name_fr` / `description_fr` onto the camelCase
  keys, so a row served with the snake_case spelling does not silently lose its
  translation. An untouched row is returned **unchanged (same object)**, so
  nothing is copied on every catalogue load.
* `supabase_store._canonicalize_product` folds the same aliases on read,
  because the hand-built production table has both a quoted `"nameFr"` column
  and a legacy `name_fr` one.
* The `ja:lang` handler now runs `mountChrome → I18N.apply → ja:rerender →
  I18N.apply → paint banner`. **The second `I18N.apply()` is the fix**: the
  rerender rebuilds big parts of the page from template strings that are baked
  in *after* the first sweep, so without it the newly painted controls stayed
  English until a reload.
* Admin product form has a **Description (French)** field; the submit payload
  includes `descriptionFr`.
* `catalog.normalize` accepts both spellings and sanitises the French copy the
  same way as the English one.

#### The rule that protects revenue

A translated option label is **display only**. The raw value is the variant's
identity — `optionStock` is keyed by it, the cart stores it, the order writes it
to Postgres. So:

* `data-val` on every option chip keeps the **raw** value
  (`JA.displayOptionRaw`);
* only the visible `<span>` is translated;
* `data-fcolor` / `data-fsize` on the shop filters stay raw.

If the French label ever reached the stock lookup, a French shopper would see
"Noir" while the shop looked up stock for a variant that does not exist.
`tests/_store_sim.mjs` pins this with a product whose total is 9 and whose
`Black` variant is 3: `stockFor(p, "Black") === 3` but `stockFor(p, "Noir")
=== 9` — the French label must **not** resolve to the variant.

### Schema

`"descriptionFr" text` added to `create table products` **and** to the
add-column repair block (the production table already exists, so the
`create table` alone never reaches it). `schema_sections/01_products.sql` is
**5848 bytes**, still under the 6 KB limit that keeps it pasteable into the
Supabase SQL Editor on a phone.

---

## 2. What was verified automatically (and how)

| Check | Command | Result |
| --- | --- | --- |
| Full suite | `python -m pytest tests/ -q` | **747 passed** (721 baseline + 26 new) |
| Storefront pipeline, real `js/store.js` | `node tests/_store_sim.mjs` | all passed (19 new French checks) |
| reCAPTCHA token | `node tests/_recaptcha_token_sim.mjs` | 11/11 |
| Schema sections | `python tools/split_schema.py --check` | 16 sections match |
| JS parse | `node --check` on every `js/*.js` | OK |
| Real DOM + real Flask server | `node tests/_frontend_sim.mjs` | 21/22 (see below) |
| Live API | `GET /api/catalog` | 258 products, `nameFr` served |
| Live API | `GET /api/categories` | all 14 `nameFr` populated |

The 26 new tests in `tests/test_french_catalog.py` cover the Python folding, the
schema, server↔storefront agreement on the 14 French category names, the colour
vocabulary against every option value the catalogue actually sells, and the CSS
cascade (parsed media-query-aware, so a mobile rule can never be mistaken for
the desktop one).

**These tests were mutation-checked** — deliberately breaking each behaviour
makes the matching test fail:

* removing `descriptionFr` from `catalog.normalize` → 4 failures;
* adding `padding`/`font-weight` to `.is-on` → `test_is_on_rules_for_the_switches_only_change_colour` fails;
* making `displayDescription` ignore French → the store sim reports `FAIL displayDescription answers the French copy in French`.

### Dead-control audit

A script walked every `<button>`/`<a>` emitted by `js/*.js` and the static
`*.html` pages and checked each one's hooks (`data-*`, `id`, `href`,
`type=submit`, `onclick`) against the JS. Result:

* **0** clickable elements whose hooks nothing references;
* **0** buttons/anchors with an `id` that no code wires up.

The one apparent hit — `index.html`'s `<a href="#">View hero document</a>` — is
a placeholder inside `#hero-doc-slot`, which `js/app.js` sets `hidden = !doc` on
and rewrites the `href` for at runtime. It is never clickable with an empty
`href`.

`#pw-form` (the purged in-portal change-password form) appears in **0** shipped
files; `tests/e2e.py` used to wait for it and has been updated to assert the
env-only passwords instead. No OTP / email-code / password-recovery UI exists
anywhere in `js/*.js` or `*.html`.

### The one failing jsdom check is pre-existing, not ours

`checkout order places OK (PDF receipt)` returns **400 `Supabase Storage upload
failed.`** This sandbox has no Supabase credentials, so the receipt upload has
nowhere to go. Proven by running the same harness against a clean worktree of
the base commit `ae77cd0` on a second port:

```
BASE='http://127.0.0.1:8081' node tests/_frontend_sim.mjs
FAIL  checkout order places OK (PDF receipt)  ->  status=400 proof=undefined
=== 21/22 checks passed ===
```

Identical result on unmodified `main`. It is also the storage boundary working
as intended: `UPLOAD_MODE=supabase`, no local-disk fallback.

---

## 3. Playwright: could not run, no production impact

`playwright install chromium` **failed in this sandbox**, exactly as the
handoff predicted. This is an environment limit, not an app bug — Playwright is
an optional end-to-end harness (`tests/e2e.py`) and is not even a declared
dependency in `requirements.txt` / `requirements-test.txt`.

```
Downloading Chrome for Testing 151.0.7922.34 (playwright chromium v1234)
  from https://cdn.playwright.dev/builds/cft/151.0.7922.34/linux64/chrome-linux64.zip
Error: Client network socket disconnected before secure TLS connection was established
  code: 'ECONNRESET', host: '150.171.110.152', port: 443
Failed to install browsers

$ python -m playwright install-deps chromium
E: Unable to locate package libxdamage1
E: Package 'fonts-liberation' has no installation candidate
E: Unable to locate package xvfb
Failed to install browser dependencies
```

The browser CDN is network-blocked and apt has no package lists. **No browser of
any kind is available in the sandbox** (no chromium, chrome, firefox, or cached
`ms-playwright` build).

Nothing was claimed as passing that did not run. Instead:

* `tests/e2e.py` was **updated** so it is correct for the current admin
  (`#pw-form` → env-only `ADMIN_MASTER_PASSWORD` / `ADMIN_BOOTSTRAP_PASSWORD`,
  plus assertions that no change-password or OTP form came back);
* new steps were **added** to `tests/e2e.py` that measure the currency pill
  before and after a tap (identical width **and** height), assert `₦` is
  narrower than `F CFA`, and assert French reaches the product name and the
  shop's category labels;
* `tests/e2e.py` compiles (`python -m py_compile`) — that is the only thing
  verifiable without a browser, and it is **not** a claim that the e2e passed.

Run it yourself where a browser installs:

```bash
pip install playwright && python -m playwright install --with-deps chromium
ADMIN_PW='<admin password>' BASE='https://jaurastore.com.ng' python tests/e2e.py
```

---

## 4. Owner checklist — after Render deploys

### Once, in Supabase (needed for French descriptions to persist)

Open **Supabase → SQL Editor → New query** and run
`schema_sections/01_products.sql` (idempotent, add-only, never drops data). If
you would rather run the minimum, this is the only statement that matters:

```sql
alter table products add column if not exists "descriptionFr" text;
```

Until this runs, nothing breaks: `_upsert_products_resilient` detects the
missing column, drops it, retries, and logs
`[supabase] products upsert: stored without columns ['descriptionFr']`. The
shop, checkout and admin all keep working — a French **description** simply
will not be saved. French **names** already work, because the `"nameFr"`
column has existed since the original schema.

### In the browser (10 minutes, no tools needed)

**Currency pill — the resize bug**

1. Desktop width: the `₦ | F CFA` pill and the `EN | FR` pill are the same
   height as the icons beside them (34px).
2. Tap `F CFA`, then `₦`, several times. **The pill must not change size at
   all** — only the highlighted button's colour moves.
3. Switch to `FR`, then back. Same: no movement.
4. On a phone (≤640px) repeat 2–3. The pills are slightly shorter (32px) and
   still never resize.

**French**

5. Tap `FR`. The nav, footer and buttons go French — **and so do the product
   names** (e.g. "10000 mah power bank" → "10000 mah batterie externe").
6. Open any product. The **description** is French if you wrote one; otherwise
   it shows the English one. It must never be blank.
7. Category labels are French: *Beauté & soins, Chaussures, Articles ménagers,
   Gadgets / Électronique…* — none left in English.
8. On a product with colours, the chips read *Noir, Rose, Bleu clair…* Select
   one, add to cart, and check the **cart, the checkout summary and the order
   receipt** all show the French colour.
9. Still in French, add a colour variant to the cart and check the **quantity
   buttons and stock behave exactly as in English** (this is the raw-value rule
   holding).
10. Tap `EN`. Everything returns to English without a page reload.

**Admin**

11. Products → edit any product. There is a **Product Name (French)** field and
    a **Description (French)** field. Fill the description, Save, then open the
    product on the shop in French and confirm it appears.
12. A new product with no French name shows its English name in French mode —
    that is the intended fallback. Add a French name in Admin when you can.
13. Delivery zones: save a zone → toast **"Zone saved."**; delete → **"Zone
    deleted."** Reload the page: both changes are still there.
14. Settings → save the moving banner text. The banner on the shop changes, and
    it changes on **every** page, not just the one you were on.
15. Settings: there is **no** hero banner title/subtitle, contact email,
    contact phone or site logo field any more — those five inputs were retired.
16. Account tab: it names **ADMIN_MASTER_PASSWORD** and
    **ADMIN_BOOTSTRAP_PASSWORD** and points at Render. There is **no**
    change-password form, no email login, no OTP / verification-code field.
17. Sign in with `ADMIN_MASTER_PASSWORD`. Then with `ADMIN_BOOTSTRAP_PASSWORD`.
    Both must work.
18. Upload a product photo and confirm it comes back as a **Supabase Storage
    URL** (not a local `/images/...` path).

### Data you can restore now

The category merge marker (`category_merge_v2`) is live in Supabase, so a wiped
Render SQLite will **not** re-run the old force-renames over your names. It is
safe to restore your category photos and names.

---

## 5. Not touched, deliberately

* `_write_categories_file()` and the read-only empty-categories self-heal in
  `api.py::_categories_data()`.
* Layout containers `panel-*`, `prod-grid`, `cat-list`; `more-media` still works
  through its delegated parent listener.
* All 258 `wix-*` products plus `jau-mtot3318`.
* The email / OTP / password-recovery flows — still purged.
