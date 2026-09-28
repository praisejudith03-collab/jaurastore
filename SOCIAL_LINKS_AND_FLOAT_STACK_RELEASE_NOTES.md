# Release notes — 2026-09-28

Owner request: permanent floating-layout fix, compact nude/brown currency
pill, admin-editable social links with automatic icons (Facebook included),
and the pop-up / referral / minimum-order switches.

---

## 1. The currency pill can never sit on the WhatsApp bubble again

**The defect was in the CASCADE, not in a missing rule.** Both offsets were
already written down, but on a **phone homepage** the dock-less rule

```css
body[data-page="home"] .cur-float { bottom: 92px !important; }   /* 0-2-1 */
```

out-ranked the phone rule

```css
@media (max-width: 640px) { .cur-float { bottom: 162px; } }      /* 0-1-0 */
```

so the pill dropped to **92px** while the bubble stayed at **86px with a 50px
body (86 → 136px)**. The pill landed inside the bubble — exactly what the
owner photographed.

Fixed by restating the phone offset for **both** selectors and pinning every
offset with `!important`:

| context | WhatsApp bubble | pill | clear gap |
|---|---|---|---|
| homepage, desktop | bottom 20px, 58px body (top 78px) | **106px** | 28px |
| every other page, desktop | bottom 84px, 58px body (top 142px) | **170px** | 28px |
| any page, ≤640px phone | bottom 86px, 50px body (top 136px) | **162px** | 26px |

Each pill offset also clears the bubble's `scale(1.85)` glow ring, so nothing
touches at any frame of the animation.

**Locked in** (`tests/test_header_layout_lock.py` + `tools/float_geometry.py`):
the tests now *resolve the CSS cascade the way a browser does* — width-scoped
media queries, specificity, `!important`, source order — at 12 viewport widths
× 4 pages, and fail the build if the pill ever overlaps the bubble, enters its
glow envelope, loses its `!important` pin, or if the ≤640px block stops listing
the homepage selector. Reverting any part of the fix fails CI before it can
reach jaurastore.com.ng.

## 2. Currency pill — compact, nude/brown, no purple

Unchanged from the previous release and still pinned by tests: white pill,
blush-tan border `#E8CDAC`, espresso `#33251A` active fill, 10px labels, 2px
shell, ~63px total width. `test_currency_pill_carries_no_purple` fails on any
purple hex/rgba inside the pill block.

## 3. Social media links — admin inputs + automatic icons (with Facebook)

* **Admin → Settings → “Social media links”**: four clean inputs — WhatsApp,
  Instagram, TikTok, **Facebook**. Saved with the shared
  only-what-changed patch (`siteFieldPatch`), so a half-loaded form can never
  blank a stored link; clearing is explicit.
* **Automatic icon rendering**: the logo is detected from the **link itself**
  (`socialNetwork()` in `js/store.js`), not from the box it was typed into — a
  `facebook.com/...` address pasted into the Instagram field still renders the
  Facebook logo, in the admin preview *and* in the storefront footer. The
  Facebook logo ships alongside Instagram, TikTok and WhatsApp.
* **What owners actually type is accepted**: full URLs, bare domains
  (`facebook.com/jaurastore`), `@handles`, and phone numbers (→ `wa.me/…`).
  `javascript:` / `data:` addresses are refused client- and server-side.
* Empty box → the built-in default for that platform (the shop's TikTok
  account and its market-aware WhatsApp chat link survive a fresh row).
  Typing **OFF** removes that icon from the storefront completely.
* Storage: four new `site_settings` columns —
  `social_whatsapp_url`, `social_instagram_url`, `social_tiktok_url`,
  `social_facebook_url` — always served on `GET /api/site`.

### Supabase migration (run once, add-only and idempotent)

```sql
-- add_social_link_columns.sql  (also schema_sections/20_site_settings_social.sql)
alter table site_settings add column if not exists social_whatsapp_url  text not null default '';
alter table site_settings add column if not exists social_instagram_url text not null default '';
alter table site_settings add column if not exists social_tiktok_url    text not null default '';
alter table site_settings add column if not exists social_facebook_url  text not null default '';
```

Until it is run, the storefront simply keeps its built-in defaults — saves
degrade gracefully (`supabase_settings.update_site_settings` drops unknown
columns and logs the exact `ALTER`), nothing breaks.

## 4. Admin switches (verified, already live)

* **Welcome pop-up**: `#welcome-enabled` toggle writes `welcome_enabled=0/1`;
  OFF renders nothing at all. The pop-up is one unified `.welcome-panel` box
  and its entrance carries no rotation (`@keyframes welcomeIn`).
* **Promotions master switch** (`promosEnabled`): coupons refused, discount
  tiers withheld from `/api/site`, bulk discount not priced server-side —
  nothing deleted, everything returns when switched back ON.
* **Referral programme** (`referralEnabled`): independent switch; the welcome
  pop-up and checkout drop every referral prompt while it is OFF.
* **Minimum order** (`minOrderCfa`): editable up and down from Admin →
  Marketing, `0` disables the rule completely (client *and* server), and the
  Naira floor follows the exchange rate automatically.

## 5. Verification

* `python3 -m pytest tests/ -q` → **1382 passed** locally (browser-smoke
  excluded: Chromium cannot be downloaded in the sandbox; CI runs it).
* New: `tests/test_social_links.py` (9 checks) and
  `tests/_social_links_sim.mjs` (18 browser-level checks against the real
  `js/store.js`).
* Asset cache token bumped **155 → 156** across every HTML page, `sw.js`,
  the shipped JS/CSS and the fingerprint guard, so returning shoppers get the
  new stylesheet and bundle instead of a cached copy.
