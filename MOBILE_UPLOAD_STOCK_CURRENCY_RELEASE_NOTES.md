# Currency pill · out-of-stock buttons · phone-side image compression

Release notes for the three owner requests of **2026-09-27**. It records what
changed, **what was verified automatically**, **what could not be run in the
build sandbox**, and the short checklist to run on a real phone after deploy.

---

## 1. The floating currency pill

### 1A — it is compact again

`.cur-float` (the white ₦ / F CFA badge that floats above the WhatsApp bubble)
read as a long stretched bar: 12px labels inside `9px 13px` of padding on a
`3px` shell.

| | before | after |
| --- | --- | --- |
| shell padding | `3px` | `2px` |
| button padding | `9px 13px` | `8px 6px` |
| label font | `12px` / `0.06em` | `11px` / `0.04em` |
| `₦` (NGN) | no width — the pill reflowed when the bold moved | `min-width: 30px` |
| `F CFA` | idem | `min-width: 48px` |
| total reserved width | ≈ 107px | ≈ 86px |

Each currency now owns a fixed minimum width that is wider than its own **bold**
text, so switching ₦ ↔ F CFA (the active one turns bold) can no longer resize
the pill. The corner, the colours and the purple active chip are untouched:
`bottom: 85px`, `right: 20px`, `z-index: 9999`, white shell, `#D8B4FE` border,
`#7C3AED` active.

### 1B — it no longer covers "View bag" / "Checkout"

The slide-out bag (`.mini-cart`, `z-index: 5000`) parks **View bag** and
**Checkout** in the bottom-right corner — exactly where the currency pill
(9999) and the WhatsApp bubble (9998) float, and both floated *above* the
drawer, sitting on top of those two buttons on a phone.

While the bag is open both floats now drop to `z-index: 4990` — below the pane
*and* below its mask — and fade out over 220ms (`opacity`, `visibility`, a
small `translateY` on the pill; `prefers-reduced-motion` gets the fade without
the slide). Closing the bag brings them straight back.

* CSS: `body.mini-open .cur-float`, `body.mini-open .wa-float`, plus the
  equivalent `.is-behind-cart` class (block “4b” in `css/style.css`).
* JS: `floatsBehindCart()` in `js/store.js` is called from `openMini()` /
  `closeMini()`. It adds the class, sets `aria-hidden="true"` and `inert`, so
  the hidden floats also leave the tab order and the screen-reader tree — the
  only reachable controls while the bag is open are the ones inside it.

### 1C — English opens strictly in Naira

`currency()` used to read `localStorage.jaura_currency` and trust it forever.
That key is **also written by the French storefront** (where CFA is forced), so
one French visit — or one tap weeks ago — made every later English page load
open in F CFA.

The manual choice is now scoped to the **visit**:

| storage | key | meaning |
| --- | --- | --- |
| localStorage | `jaura_currency` | the last currency picked (unchanged) |
| sessionStorage | `jaura_currency_manual` | *this visit's* deliberate tap |

* a fresh English visit **always** opens in ₦ Naira;
* tapping **F CFA** keeps F CFA while the shopper browses — page to page,
  product to checkout — so the toggle stays fully useful;
* the French lock is the device's language talking, never the shopper: it
  clears the manual marker instead of writing one, so it can never leak into an
  English session;
* French itself is unchanged — `lang() === "fr"` still hard-locks F CFA and
  hides the pill.

---

## 2. Out-of-stock buttons

A sold-out piece kept the shop's normal white/cream **Add to cart** button. The
pale-grey rule for `.card.is-oos .add-mini` sat at line ~437 of
`css/style.css`, while the three later button skins (~3215, ~3426, ~6054) are
all `!important` — so the grey never applied and shoppers kept tapping a dead
button.

Anything that cannot be bought is now painted in the shop's red:

| token | value | used for |
| --- | --- | --- |
| `--oos` | `#a82e22` | the dead button, the corner ribbon |
| `--oos-deep` | `#7f2119` | its border and hover |
| `--oos-soft` | `#fdeeeb` | the blush behind a sold-out variant chip |

The one red is the same family as the existing low-stock line, and it is not a
gold-family hue, so the 40% gold cap in `tools/vivid_palette.py` does not apply.

* `.card.is-oos .add-mini`, `.mv-card.is-oos .add-mini`, `.add-mini:disabled`
  and `[data-buy]:disabled` → solid red, white text, `cursor: not-allowed`,
  `opacity: 1` (no more washed-out ghost), no shadow, no hover lift; `:hover`
  darkens to `--oos-deep` instead of animating.
* The label is already wired: the card and most-viewed buttons render
  `t("card.oos")` (**Out of stock** / **Rupture**) with the `disabled`
  attribute, and the product page's buy button switches to `t("pdp.oos")`
  (**Out of stock** / **Rupture de stock**) in `updateStockUI()`.
* `.pill.oos` (the ribbon on the photo) matches the button.
* `.opt-chip.is-oos` — a variant the server marks sold out — stays
  struck-through but now reads red on blush, and red-on-white when selected.

The block sits **last** in `css/style.css` and carries `!important` on purpose:
it has to win over the three `!important` button skins above it. A test pins
both the importance and the position.

---

## 3. Phone-side image compression (uploads)

Every product photo comes straight out of a phone camera — 3–6 MB of 4000px
pixels for a card the shop paints ~600px wide. Uploading that over mobile data
is slow, times out on a weak signal, and burns Storage for pixels no shopper
ever sees. Worse, the admin's 6 MB gate **rejected** those photos outright
(*“That photo is 7.4 MB. The limit is 6 MB.”*) before anything could shrink
them.

`js/admin.js` now carries a small compressor (`compressImageFile`, top of the
file) that runs **before** the file leaves the phone:

* the longest side is capped at **1200px** — never upscaled, so a small photo
  keeps its own size;
* re-encoded to **WebP** where the canvas can encode it (every current Chrome /
  Safari / Firefox), **JPEG** otherwise, at quality `0.82`;
* **EXIF orientation applied** while decoding (`createImageBitmap(file,
  { imageOrientation: "from-image" })`), so a portrait photo is never stored
  sideways; `FileReader` + `<img>` is the fallback path;
* JPEG output is flattened onto white first, so a transparent PNG does not come
  back with a black background;
* the **original is kept** whenever the squeeze cannot help: GIF, SVG, video,
  a file the canvas refuses to decode, a picture already small enough, or a
  re-encode that does not actually come out smaller;
* the 6 MB ceiling is now measured on **what will really be uploaded**, and the
  toast reports the saving (*“Photo uploaded — compressed 6.0 MB → 78 KB.”*).

A typical 4032×3024 / 6 MB camera original becomes a 1200×900 WebP of ~80 KB —
**about 79× smaller**, i.e. seconds instead of minutes on 3G.

The same squeeze runs for **category covers**, the **logo** and the **shop
banner**. The server side is unchanged: `storage.optimize_image_bytes()` still
runs on what it receives, `webp` was already an accepted extension, and
`validate_image()` still sniffs magic bytes.

---

## 4. What was verified

```
python3 -m pytest tests/ -q          1286 passed, 21 skipped
  (--ignore the two pgserver files; the 21 skips are environment-only:
   no PyYAML, no Playwright browser in the sandbox)
node --check js/store.js js/admin.js js/app.js        clean
```

New coverage:

| file | what it proves |
| --- | --- |
| `tests/_image_compression_sim.mjs` (23 checks) | boots the **real** `js/admin.js` against a stubbed canvas: 6 MB → 79 KB, 1200×900, `IMG_….webp`, EXIF, portrait cap, no upscale, GIF/SVG/video/undecodable pass-through, JPEG fallback + white flatten, no-gain keeps the original |
| `tests/_naira_first_cart_pill_sim.mjs` (21 checks) | boots the **real** `js/store.js`: NGN on a fresh visit even with `jaura_currency=CFA` stored, manual CFA survives navigation, French locks CFA and writes no marker, `openMini()`/`closeMini()` move both floats and their `aria-hidden` |
| `tests/test_image_compression.py` | the sim + the wiring (1200px constant, WebP-preferred, compress **before** the 6 MB gate and the upload, every call site) |
| `tests/test_naira_first_and_cart_pill.py` | the sim + the compact-pill width budget, the pinned corner, the collision z-index/fade, the Naira-first source pins |
| `tests/test_out_of_stock_styling.py` | the three reds really are red (HSL), the four dead-button selectors resolve to them, the block is `!important` **and** last, hover, ribbon, chips, and the en/fr labels |

**Not verifiable here:** the sandbox has no browser (Playwright's chromium
download fails), so nothing was rendered. The checks above are DOM/CSS
simulations of the real source files, not screenshots.

---

## 5. Owner checklist on a real phone

1. **Pill size** — open the shop in English: the badge above the WhatsApp
   bubble should look like a small tablet, not a bar, and must **not** change
   width when you tap between ₦ and F CFA.
2. **Cart collision** — add something, open the bag: the pill and the WhatsApp
   bubble should fade away, leaving **View bag** and **Checkout** completely
   clear. Close the bag: both come back.
3. **Naira first** — tap F CFA, browse two or three pages (prices stay in
   F CFA), then **fully close the tab and reopen the shop**: it must open in
   ₦ Naira.
4. **Out of stock** — find a sold-out piece: the button must be red and read
   *Out of stock*; tapping it must do nothing. On its product page, pick a
   sold-out size/colour: the chip turns red and struck through and the buy
   button turns red.
5. **Upload speed** — in the admin, add a photo straight from the camera roll:
   it should upload in a couple of seconds and the toast should quote the
   saving. Check the picture on the shop — it should still look sharp, and
   portraits must not be rotated.
