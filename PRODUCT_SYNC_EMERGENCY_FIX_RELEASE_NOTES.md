# Product Sync Emergency Fix — Release Notes (2026-10-01)

## What was reported

1. Newly added products (an LV bag and two other bag products) appeared to
   fail to save and "disappeared" after creation.
2. Admin edits were not reflecting immediately on the live website.
3. Admins needed per-admin "return to my list" navigation when several
   managers work the catalogue at the same time.

## 1. Product creation persistence (`POST /api/products`, `/api/products/variants`)

Audited the whole save surface. The all-or-nothing write path was already
sound (a Supabase write failure answers `503` and never reports success);
these gaps are now closed:

* **Audit trail on every save.** `product.create` / `product.update` /
  `product.save_failed` rows land in `audit_log` with the row id, name,
  visibility, stock and price — a "my product disappeared" report can now be
  traced to the exact save, actor and time (`api._product_save_response`).
* **No silent swallows.** A storage exception answers `503 ok:false` and is
  recorded in the failure log; the `/api/products/variants` read no longer
  disguises a transient catalogue failure as `404 Product not found` — a
  blown read answers `503` so a temporarily unreachable database can never
  look like a vanished product.
* **New products default to active/visible.** `catalog.normalize` keeps
  `online: true` unless the admin explicitly unticks "Show in online store"
  (verified live: all created rows land `online=True`).
* Verified end-to-end (live run): images, initial inventory, base and
  compare-at prices, supplier URLs, SKUs and per-variant price/stock/SKU
  tiers all commit and survive a full process restart.

## 2. Real-time cache busting — instant storefront + admin reflection

* `/api/catalog` (public **and** `?all=1`) now serves
  `Cache-Control: private, no-cache, must-revalidate` with an ETag: the
  browser revalidates on every request, so a save is visible **on the next
  request** — the old `max-age=20` disk-cache window (up to ~50s with
  stale-while-revalidate) can no longer hide a fresh save. Unchanged
  catalogues still answer with a cheap `304`.
* Every product/variant/category write purges **all** server-side
  representations synchronously — the catalogue list snapshot **and** the
  category menu cache (`_invalidate_all_catalog_caches`), so the storefront
  grid, the category filters and the /admin dashboard all read fresh
  database rows immediately.
* The in-process snapshot TTL dropped from 20s to 5s as a cross-worker
  safety net (production runs a single worker, so the synchronous purge is
  the norm).
* The storefront live-sync poll moved from 30s to 15s (a `304` when nothing
  changed — cheap), and a server-confirmed save now dispatches `ja:catalog`
  so any open page repaints immediately.
* CDN/edge: `/api/*` stays `no-store` at the edge (`_headers`), and the
  `private` + `no-cache` catalogue policy keeps shared caches out entirely.

## 3. Isolated multi-admin return navigation

When an admin opens a product to edit or create from the products list,
the list state they are leaving — category filter, search, page number and
scroll position — is captured as a canonical return URL
(`/admin/products?category=bags&page=2`):

* **Save Product** (and Cancel / in-editor delete) returns that admin to
  their exact source page and pagination position.
* The state lives in **sessionStorage**, which the browser scopes **per
  tab**: several admins working simultaneously each keep their own list
  position and never overwrite each other's view.
* The captured position is mirrored into the address bar as
  `admin.html?return_url=…` (and `?return_url=` / `?desk=products&…`
  deep links are understood in both the encoded and the loose spelling), so
  a reload or shared link reopens the exact list position.

## 4. UI & performance

* **Multi-variant price ranges** now render as exact formatted ranges —
  `₦1,800.00 – ₦2,500.00` — on storefront grid cards, the product detail
  page, the "most viewed" rail and (new) the /admin catalogue cards.
  (`JA.moneyExact` / `JA.moneyRange` in js/store.js.)
* **Bolder, larger prices** across product pages, modal pop-ups and grid
  cards: card prices `clamp(16px, 4.2vw, 21px)`, weight 800; PDP prices
  `clamp(22px, 6.4vw, 31px)`, weight 800.
* **Pop-up banner ON/OFF toggle** verified end-to-end in /admin → Settings
  (the `Pop-up banner Active` switch writes `popup_banner_active`, and the
  storefront guard `!welcomeEnabled()` stops the modal rendering when off).
* Asset cache token bumped `v171 → v172` so every browser and the service
  worker pick up the new build immediately.

## 5. Channel Broadcast Feed — searchable product picker (Marketing)

The "Select custom product" flow in /admin → Marketing no longer dumps every
catalogue row into the page. It is now a proper picker over the **whole**
catalogue (owner request 2026-10-01):

* **Searchable modal.** A real dialog (`#mk-bc-picker`, aria-modal) with a
  search box ("Search by title, SKU or category…") and a category filter.
  Matches span name / French name / SKU / category id / category display
  name — including hidden and out-of-stock items, with an
  `In stock` / `Out of stock` / `Hidden` badge on every row.
* **Smooth scroll pagination.** Only the first 24 matches are rendered
  (`BC_PICKER_PAGE_SIZE`); the next page is appended as the admin scrolls
  near the bottom (rAF-throttled, passive listener) or taps "Show more" —
  a 250-product catalogue never paints 250 rows at once, and the scroll
  position is kept while pages append. Ready-to-post (online + in-stock)
  items always rank above the rest.
* **ANY catalogue product can be pinned.** A custom pick is no longer
  rejected for being outside the auto-rotation pool: sold-out or hidden
  pieces can be deliberately featured (the pick is confirmed with a
  "— note: out of stock/hidden" toast), and `broadcastScheduledFeedFor`
  builds its lookup from the full catalogue so custom picks render. The
  automatic morning/evening rotation itself still uses in-stock + online
  products only.
* **COPY DETAILS extracts caption AND photos.** "Copy details for selected"
  (and the per-card "Copy Details") now writes the formatted, URL-free
  caption to the clipboard for every selected product, then copies the
  product photos to the clipboard too (up to 10 images, single-image retry),
  and opens a "Photos for your post" drawer as the universal fallback with
  a per-photo Download link and a staggered "Download all photos" button.
* Asset cache token bumped `v172 → v173`.

## 6. Out-of-stock toggle persistence & full field audit

**The report:** switching a product to "Out of stock" (or typing `0`) did
not stick — the admin kept reverting to a stock number, and the storefront
never showed the sold-out state. Re-stocking was equally unreliable.

Semantics now implemented and verified live, end to end:

* **Every explicit OFF spelling commits.** `catalog.normalize` treats an
  explicit whole-product OFF — `stockStatus` / `stock_status` set to an
  out-word, `is_in_stock` / `in_stock` false/0/out-word, or
  `stock: 0` — as FINAL: the row is saved `stock = 0` and **every**
  per-variant quantity is zeroed with it. Absent/other values express no
  opinion: the variant-sum and default-24 invariants stay intact.
* **The `/api/products/variants` endpoint** accepts all the same spellings
  (`stockStatus`, `is_in_stock`, `in_stock`, `stock`), infers `stock: 0` as
  "out" when the payload carries no variant quantities, and **re-stocks**
  an all-sold-out product when it receives an explicit positive stock (the
  whole-product mode resumes; a later per-variant payload wins per variant).
* **The admin editor** honours an explicitly typed `0`, flips the
  availability switch automatically, zeroes the variant boxes live when the
  switch is turned OFF, and — new — **remembers the quantities that were
  live before the flip** (stashed outside the repainted DOM, reset at every
  editor open), so toggling OFF → ON within one session restores them
  instead of leaving the product at 0/0. A re-stock guard resumes
  whole-product mode when the admin types a positive quantity over an
  all-zero variant map.
* **The storefront reacts instantly** (badge, PDP buy button, card pill and
  dead add-button switch on the same save; the catalogue answer is always
  revalidated, never CDN-cacheable) and **persists across full reloads**.
* **Field audit of the same save:** price override (₦14,000 on one variant),
  supplier URL, category, description and dimensions all persist verbatim
  across reloads; the PDP shows the exact min–max range (`₦9,000.00 –
  ₦14,000.00`).

Two adjacent defects found by the end-to-end run and fixed in the same
pass:

* **Return navigation captured nothing when an editor was opened by
  clicking a product card** — only the "Add product" button captured the
  list state, so Cancel/Save restored a *stale* earlier snapshot (wrong
  search, wrong page). Card clicks now capture the return state too.
* **A fresh admin login kept the pre-login (public) catalogue** — the boot
  fetch runs before the session exists, so the products list — and worse,
  the editor, whose save commits what it renders — saw the public answer:
  stock numbers stripped, "in" translated to the 9999 sentinel. A
  successful login now refetches the authenticated `?all=1` catalogue
  before the desk is painted.

Asset cache token bumped `v173 → v176` (picker, stock toggle, login
refetch), then `v176 → v177` (audit fixes).

## 7. Whole-site audit — concurrent-edit safety, production secret key, stale-tab refresh

A full-site audit (auth coverage on all 65 admin routes, CSRF, upload
serving, catalogue write locking, security headers, escaping discipline)
found the foundations sound, plus three real underlying defects — all now
fixed and verified live:

* **Last-write-wins data loss on product saves.** The admin editor saves
  the ENTIRE product from the copy in the tab's memory, so a save built on
  a stale copy silently reverted whatever changed in between — another
  admin, another tab, or an API integration. The editor now ships the
  row's `updated_at` as it was when the editor was OPENED
  (`baseUpdatedAt`); when the stored row has moved on, the server answers
  **409 Conflict** ("This product was changed by someone else while you
  were editing…"), the editor stays open, the optimistic local copy is
  rolled back (no phantom row, no outbox retry of a stale save), and the
  rejected save is audit-logged as `product.save_conflict`. The guard is
  opt-in: API clients, imports and mirrors that send no token keep the
  previous behaviour.
* **A production deployment without `SECRET_KEY` booted with a
  repository-public key** — the key that signs admin session cookies. It
  now falls back to a random per-boot secret (never forgeable; admins
  simply sign in again after a restart) and the boot log says so loudly.
  Development is unchanged; an explicitly set key always wins.
* **An open admin tab never saw other admins' writes.** The catalogue is
  fetched once at boot. When the tab regains focus it now quietly
  refetches (throttled to once a minute, never under an open editor —
  the 409 guard covers that window), keeping the list and its search /
  page state honest.
* Polish: the customer Account page now re-renders from the server after
  a profile save (previously fields stayed stale until the next load);
  `mediaHTML` escapes the `data-ph` attribute and `opts.attrs` values
  (defense-in-depth); Flask bumped 3.0.3 → 3.1.1 and gunicorn 22.0.0 →
  23.0.0 (closes the CVE-2024-6827 request-smuggling issue; every Procfile
  flag re-validated).
* **Functional escaping verification.** A Node-VM test now executes the
  REAL `cardHTML` / `mediaHTML` from `js/store.js` against hostile product
  data (a name carrying `<img onerror=…`, image URLs and placeholder paths
  carrying quote-injection payloads — all storable, because the
  server-side `sec.clean` strips tags but keeps quotes) and asserts the
  rendered HTML carries no injectable attribute or script tag while the
  card still renders correctly.

## Verification

* `tests/test_product_persistence_instant_sync.py` — persistence of every
  field, online-by-default, instant visibility + ETag change on both public
  and admin endpoints, category-cache purge, audited failures,
  revalidation policy, return-navigation and price-range UI contracts.
* `tests/test_broadcast_feed.py` — re-pinned for the new picker spec
  (searchable modal over the whole catalogue, scroll pagination, any-product
  pinning, caption + photo extraction) **plus a functional Node-VM test that
  executes the real `js/admin.js` picker functions** (63-product fake
  catalogue: whole-catalogue matching, ready-first ranking, 24-per-page
  rendering, scroll/show-more pagination, SKU and category-name search, and
  pinning a sold-out product into the scheduled batch).
* `tests/test_stock_toggle_persistence.py` — NEW, 19 tests: every OFF
  spelling normalizes to `stock = 0` + zeroed variants (variant-sum and
  default-24 invariants pinned), the variants endpoint's out/in/variant-
  wins/re-stock/cache-header contracts, admin.js source pins for the
  explicit-0, out-toggle zeroing, the out→in restore stash (window-level,
  never `dataset.prev`, reset per editor open), the re-stock guard, the
  login refetch, and a full normalize round-trip field audit.
* `tests/test_concurrent_edit_safety.py` — NEW, 16 tests: the 409 guard
  (stale copy rejected + not applied + audited, current token accepted,
  no-token saves unaffected, token never persisted), the production
  SECRET_KEY fallback (random, never the public default, explicit keys
  honoured, development unchanged), and source pins for the editor
  freshness stash, the 409 local rollback, the focus refetch, the account
  re-render and the escaping on the product render paths.
* Full suite: **1581 passed, 21 skipped** (was 1578) — including the
  functional escaping VM test and a pin that the offline queue treats a
  409 as permanent (never retried).
* Live run (production-mode instance, catalog cache active — plus a
  testing-mode instance for the Supabase-backed upload/site-settings paths
  which are unreachable from the offline sandbox): **28/28 end-to-end checks
  in a real browser**, covering — the picker modal (paged 24-at-a-time with
  "Showing 24 of 258", search, scroll pagination, show-more, badges),
  pinning a sold-out product with its badge + note, bulk COPY DETAILS with
  the caption verified on the clipboard and the photo drawer with working
  download links; the full admin form save with a real uploaded photo,
  persisting across full browser reloads; instant storefront visibility
  (uncached `/api/catalog` + shop search + the product page); exact
  admin list-state restoration (search + page + count) after the editor;
  the variant min–max price range with enlarged fonts; and the pop-up
  banner ON/OFF toggle rendering/removing the storefront modal.
* Live run #2 (same two instances, after the stock-toggle work):
  **38/38 end-to-end checks in a real browser** — the full regression above
  plus the out-of-stock switch (live zeroing, commit, storefront badge/PDP/
  card, reload persistence, in-session out→in restore, re-entry re-stock),
  the typed-0 path, all three API spellings on `/api/products/variants`
  (stock:0, stock:8 re-stock, is_in_stock:false, per-variant payload), the
  never-cacheable catalogue answer, the full field audit, and a fresh-login
  admin seeing the authenticated stock numbers (4 units, per-variant map)
  without a reload.
* Live run #3 (after the audit fixes): **12/12 end-to-end checks in a
  real browser** — fresh login stock numbers, a fresh editor save
  committing without a false conflict, a stale editor save refused with
  the conflict message (other admin's rename kept, refused edit not
  applied, no phantom copy left in the tab, reopening shows the merged
  version), a refocused tab picking up another admin's write without a
  reload, the out-of-stock switch + PDP regression, return navigation,
  and the refocus throttle.
