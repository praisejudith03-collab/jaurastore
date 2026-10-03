"""Channel Broadcast Feed (admin panel) — owner requests 2026-09-28, revised
2026-09-30 (one-tap "Copy Details" for WhatsApp).

A "Smart Rotation & Selection" section in Marketing that:
  * auto-cycles active, in-stock products across every category, twice a
    day (a morning batch and an evening batch), so the same items are not
    suggested every day;
  * automatically queues the first few picks for each batch the moment it
    is opened (or reshuffled), so there is already a ready-to-post set
    rather than a blank list to build from scratch;
  * gives each card (and the batch as a whole) a single, one-tap
    "Copy Details" action that copies a plain, URL-free caption to the
    clipboard - exactly the bilingual product name, the active prices in
    both currencies, and the in-stock colour/size options when present -
    with no image-export buttons, no native share sheet and no link.

The rotation and message-building logic lives entirely in js/admin.js and
runs in the browser against data already loaded into the admin page
(JA.products()/JA.categories()), so - consistent with how the rest of the
admin UI is covered in this test suite (see
test_receipts_and_crash_reports_render_as_collapsible_cards and the
Homepage Featured accordion tests) - it is pinned down at the source
level: the right filters, the right rotation inputs and the right
copy-to-clipboard behaviour are all present, and cannot silently drift.

Run with:  python3 -m pytest tests/test_broadcast_feed.py -q
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _admin_js():
    with open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8") as f:
        return f.read()


def _func(src, name):
    """The source of one top-level function (plain or async), up to the
    next top-level function/async function declaration."""
    marker = None
    for prefix in ("function ", "async function "):
        candidate = f"{prefix}{name}("
        if candidate in src:
            marker = candidate
            break
    assert marker, f"function {name} not found in js/admin.js"
    start = src.index(marker)
    rest = src[start:]
    m = re.search(r"\n(?:async )?function ", rest[1:])
    end = m.start() + 1 if m else len(rest)
    return rest[:end]


ADMIN_JS = _admin_js()


def test_the_broadcast_card_is_mounted_in_the_marketing_panel():
    assert "broadcastFeedCardHTML()" in ADMIN_JS
    assert 'id="mk-broadcast-card"' in ADMIN_JS
    assert 'bindBroadcastFeed()' in ADMIN_JS


def test_only_active_in_stock_products_are_eligible_for_the_feed():
    body = _func(ADMIN_JS, "broadcastEligibleProducts")
    assert 'p.online !== false' in body
    assert 'broadcastInStock(p)' in body


def test_the_rotation_depends_on_the_day_and_the_slot_not_just_the_name():
    body = _func(ADMIN_JS, "broadcastFeedFor")
    assert "broadcastDaySeed()" in body
    assert '"evening" ? 1 : 0' in body
    # Cycles across categories (one pick per category id), the owner's
    # explicit rotation requirement - twice a day, morning and evening.
    assert "byCat" in body and "cats.map" in body


def test_reshuffle_lets_the_owner_see_a_different_pick_without_waiting_a_day():
    assert "bcShuffle" in ADMIN_JS
    assert '"#mk-bc-reshuffle"' in ADMIN_JS


def test_a_reshuffle_re_queues_a_fresh_automatic_batch():
    body = _func(ADMIN_JS, "bindBroadcastFeed")
    reshuffle = body[body.index('"#mk-bc-reshuffle"'):body.index('"#mk-bc-clear"')]
    assert "bcSelected[bcSlot].clear()" in reshuffle
    assert "bcAutoQueued[bcSlot] = false" in reshuffle


def test_the_first_few_picks_are_automatically_queued_per_batch():
    """Owner request 2026-09-28: "automatically... queue... twice a day" -
    a batch is not just a blank list of checkboxes; it already has a
    starting selection the moment it is opened."""
    assert re.search(r"BC_AUTO_QUEUE_SIZE\s*=\s*4", ADMIN_JS)
    assert "const bcAutoQueued = { morning: false, evening: false }" in ADMIN_JS
    body = _func(ADMIN_JS, "paintBroadcastFeed")
    assert "bcAutoQueued[bcSlot]" in body
    assert "BC_AUTO_QUEUE_SIZE" in body


def test_broadcast_links_resolve_clean_product_paths_without_double_slashes():
    body = _func(ADMIN_JS, "broadcastProductUrl")
    assert "new URL(path, location.origin).href" in body
    assert '"/products/"' in body
    assert '`${location.origin}/${path}`' not in body


def test_each_card_carries_name_price_options_and_a_single_copy_button():
    body = _func(ADMIN_JS, "broadcastCardHTML")
    assert "broadcastProductUrl(p)" in body  # the plain "View" link may still point at the store page
    assert "broadcastPriceLine(p)" in body
    assert "broadcastDisplayName(p)" in body
    assert "broadcastOptionsLine(p)" in body
    assert 'data-bc-copy="${esc(String(p.id))}"' in body
    assert "Copy Details" in body


def test_the_image_export_clutter_buttons_are_gone():
    """Owner request 2026-09-30: no more multi-button image-export clutter -
    Share / Download Catalog Image are removed entirely, replaced by one
    "Copy Details" action."""
    body = _func(ADMIN_JS, "broadcastCardHTML")
    assert "data-bc-share" not in body
    assert "data-bc-download" not in body
    assert "Download Catalog Image" not in body
    assert ">Share<" not in body
    for name in ("broadcastShareNative", "broadcastDownloadAndCopy", "broadcastImageFile",
                 "broadcastCanShareFiles", "broadcastShareBatchNative", "broadcastShareUrl"):
        assert f"function {name}(" not in ADMIN_JS, f"{name} should have been removed"


def test_the_broadcast_name_is_bilingual_when_a_french_name_exists():
    """Owner request 2026-09-28: the post text must show the product name
    in both English and French, not whichever language the admin panel
    happens to be in."""
    body = _func(ADMIN_JS, "broadcastDisplayName")
    assert "p.nameFr" in body
    assert "${en} / ${fr}" in body
    # Never a duplicated "Name / Name" line when there is no real
    # translation on file.
    assert "fr.toLowerCase() !== en.toLowerCase()" in body


def test_the_broadcast_price_line_never_shows_the_struck_through_price():
    body = _func(ADMIN_JS, "broadcastPriceLine")
    code_lines = [ln for ln in body.splitlines() if not ln.strip().startswith("//")]
    code = "\n".join(code_lines)
    assert "compareNgn" not in code
    assert "compareCfa" not in code


def test_the_price_line_shows_both_currencies_like_everywhere_else_in_admin():
    body = _func(ADMIN_JS, "broadcastPriceLine")
    assert 'JA.money(p.priceNgn' in body and '"NGN"' in body
    assert '"CFA"' in body


def test_the_broadcast_message_mentions_available_colours_and_sizes():
    body = _func(ADMIN_JS, "broadcastOptionsLine")
    assert "p.colors" in body
    assert 'startsWith("#")' in body  # hex swatches are never shown as text
    assert re.search(r"size\|length", body)


def test_the_store_link_uses_a_clean_public_product_slug():
    body = _func(ADMIN_JS, "broadcastProductUrl")
    assert "/products/" in body or "JA.productUrl" in body
    assert "location.origin" in body


def test_the_copied_caption_is_exactly_three_lines_name_price_options_no_url():
    """Owner request 2026-09-30: Copy Details produces EXACTLY: line 1 the
    bilingual product name, line 2 the NGN/CFA prices, line 3 the
    colours/options (only when the product has any) - and never a website
    URL."""
    body = _func(ADMIN_JS, "broadcastFullText")
    code_lines = [ln for ln in body.splitlines() if not ln.strip().startswith("//")]
    code = "\n".join(code_lines)
    assert "broadcastDisplayName(p)" in code
    assert "broadcastPriceLine(p)" in code
    assert "broadcastOptionsLine(p)" in code
    assert "broadcastProductUrl(p)" not in code
    assert "location.origin" not in code
    assert "http" not in code.lower()
    assert "compare" not in code.lower()
    assert "stock" not in code.lower()
    assert "Shop now" not in code


def test_copy_details_extracts_the_caption_and_the_photo():
    body = _func(ADMIN_JS, "copyProductDetails")
    assert "copyBroadcastSelection([p])" in body
    shared = _func(ADMIN_JS, "copyBroadcastSelection")
    assert "broadcastFullText" in shared
    assert "navigator.clipboard.writeText(text)" in shared
    assert "broadcastCopyPhotos(list)" in shared
    assert "openBroadcastPhotosDrawer(list)" in shared


def test_out_of_stock_items_are_still_excluded_from_the_eligible_feed():
    """Owner rule: sold-out items (or ones whose every variant is 0) never
    appear in the rotation to be copied/posted in the first place."""
    eligible = _func(ADMIN_JS, "broadcastEligibleProducts")
    assert "broadcastInStock(p)" in eligible


def test_each_card_copy_button_is_wired_to_copy_product_details():
    paint_body = _func(ADMIN_JS, "paintBroadcastFeed")
    assert "[data-bc-copy]" in paint_body
    assert "copyProductDetails(p)" in paint_body


def test_the_bulk_bar_extracts_caption_and_photos_for_every_selected_product():
    """Owner request 2026-10-01: "COPY DETAILS FOR SELECTED" extracts the
    formatted caption AND the images for whichever items the admin selected
    - custom picks and batch suggestions alike (the old native-share
    bundling stays gone)."""
    assert '"#mk-bc-copy-batch"' in ADMIN_JS
    assert "mk-bc-share-batch" not in ADMIN_JS
    body = _func(ADMIN_JS, "bindBroadcastFeed")
    copy_batch = body[body.index('"#mk-bc-copy-batch"'):]
    assert "bcSelected[bcSlot]" in copy_batch
    # resolution against the FULL catalogue, so a custom out-of-stock pick
    # is copied exactly like an automatic suggestion
    assert "JA.products()" in copy_batch
    assert "copyBroadcastSelection(chosen)" in copy_batch
    shared = _func(ADMIN_JS, "copyBroadcastSelection")
    assert "navigator.clipboard.writeText(text)" in shared
    assert 'join("\\n\\n")' in shared


def test_photo_extraction_downloads_and_clipboard_copies():
    """Images are extracted for the selection: a native multi-image
    clipboard copy where the browser supports it, and a download drawer
    (one tap per photo, or "Download all") that works everywhere."""
    url = _func(ADMIN_JS, "broadcastPhotoUrl")
    assert "JA.asset" in url
    blob = _func(ADMIN_JS, "broadcastPhotoBlob")
    assert "broadcastPhotoUrl(p)" in blob
    assert "res.blob()" in blob
    photos = _func(ADMIN_JS, "broadcastCopyPhotos")
    assert "new ClipboardItem" in photos
    grid = _func(ADMIN_JS, "broadcastPhotosGridHTML")
    assert 'download' in grid
    assert 'onerror="fallbackImg(event)"' in grid
    drawer = _func(ADMIN_JS, "openBroadcastPhotosDrawer")
    assert 'mk-bc-photos-grid' in drawer
    assert "broadcastPhotosGridHTML(products)" in drawer
    card = _func(ADMIN_JS, "broadcastFeedCardHTML")
    assert 'id="mk-bc-photos" hidden role="dialog"' in card
    assert 'id="mk-bc-photos-download-all"' in card
    bind = _func(ADMIN_JS, "bindBroadcastFeed")
    assert '"#mk-bc-photos-download-all"' in bind
    assert '"#mk-bc-photos-close"' in bind


def test_the_morning_and_evening_batches_keep_independent_selections():
    assert "const bcSelected = { morning: new Set(), evening: new Set() }" in ADMIN_JS


def test_selecting_nothing_and_copying_a_batch_is_refused_not_a_blank_message():
    body = _func(ADMIN_JS, "bindBroadcastFeed")
    assert "if (!chosen.length)" in body
    assert "Select at least one product first" in body


def test_custom_products_can_be_added_or_swapped_without_stopping_rotation():
    scheduled = _func(ADMIN_JS, "broadcastScheduledFeedFor")
    assert "broadcastFeedFor(slot)" in scheduled
    assert "bcOverrides[slot]" in scheduled
    assert "result.push" in scheduled
    choose = _func(ADMIN_JS, "chooseBroadcastProduct")
    assert "replaces" in choose
    assert "saveBroadcastOverrides()" in choose


def test_custom_product_picker_is_a_searchable_modal_over_the_whole_catalog():
    """Owner request 2026-10-01: SELECT CUSTOM PRODUCT opens a searchable
    modal (never dumps the catalogue on screen), filters by title, SKU or
    category, and lists ANY catalogue product - ready-to-post items ranked
    first, with availability badges so a deliberate out-of-stock pick is
    obvious rather than impossible."""
    card = _func(ADMIN_JS, "broadcastFeedCardHTML")
    assert "Select custom product" in card
    assert 'type="search"' in card
    # it is a modal dialog, not an inline dump of every product
    assert 'id="mk-bc-picker" hidden role="dialog" aria-modal="true"' in card
    assert 'id="mk-bc-picker-category"' in card
    bind = _func(ADMIN_JS, "bindBroadcastFeed")
    assert 'openBroadcastPicker(null)' in bind
    opener = _func(ADMIN_JS, "openBroadcastPicker")
    assert "broadcastPickerRefresh(true)" in opener
    matches = _func(ADMIN_JS, "broadcastPickerMatches")
    assert "JA.products()" in matches
    assert "haystack.includes(term)" in matches          # title / SKU / category search
    assert "JA.categoryName" in matches                  # category display name too
    assert "rank(a) - rank(b)" in matches                # ready-to-post items first
    # the automatic rotation pool itself still excludes sold-out items
    eligible = _func(ADMIN_JS, "broadcastEligibleProducts")
    assert "broadcastInStock(p)" in eligible
    status = _func(ADMIN_JS, "broadcastPickerStatus")
    assert '"In stock"' in status and '"Out of stock"' in status and '"Hidden"' in status
    product_card = _func(ADMIN_JS, "broadcastCardHTML")
    assert "Swap item" in product_card


def test_the_picker_pages_results_with_smooth_scroll_pagination():
    """Only the first page of matches renders; the rest append in place as
    the admin scrolls (or taps Show more) - a 300-product catalogue never
    paints 300 rows at once."""
    assert re.search(r"BC_PICKER_PAGE_SIZE\s*=\s*24", ADMIN_JS)
    assert "let bcPickerMatches = []" in ADMIN_JS
    assert "let bcPickerShown = 0" in ADMIN_JS
    results = _func(ADMIN_JS, "broadcastPickerResultsHTML")
    assert "bcPickerMatches.slice(0, bcPickerShown)" in results
    more = _func(ADMIN_JS, "broadcastPickerMoreHTML")
    assert "Showing ${bcPickerShown} of ${bcPickerMatches.length}" in more
    assert 'data-bc-picker-more-btn' in more
    show_more = _func(ADMIN_JS, "broadcastPickerShowMore")
    assert "insertAdjacentHTML" in show_more       # appends, keeps scroll position
    bind = _func(ADMIN_JS, "bindBroadcastFeed")
    scroll = bind[bind.index('pickerResults.addEventListener("scroll"'):]
    assert "scrollHeight - 180" in scroll
    assert "{ passive: true }" in scroll
    assert "broadcastPickerShowMore()" in scroll
    assert 'closest("[data-bc-picker-more-btn]")' in bind


def test_any_catalogue_product_can_be_pinned_into_a_batch():
    """A custom pick is no longer rejected when it is outside the eligible
    (online + in stock) pool: the owner may deliberately feature a
    sold-out or hidden piece, and the scheduled feed still shows it."""
    choose = _func(ADMIN_JS, "chooseBroadcastProduct")
    assert "broadcastEligibleProducts()" not in choose
    assert "catalogue.find" in choose
    assert "broadcastPickerStatus(chosenProduct)" in choose   # note, not refusal
    scheduled = _func(ADMIN_JS, "broadcastScheduledFeedFor")
    assert "JA.products()" in scheduled
    assert "byId.has(String(row.id))" in scheduled


def test_morning_and_evening_custom_overrides_are_independent_and_daily():
    assert "const bcOverrides = loadBroadcastOverrides()" in ADMIN_JS
    storage = _func(ADMIN_JS, "broadcastOverrideStorageKey")
    assert "broadcastDaySeed()" in storage
    loader = _func(ADMIN_JS, "loadBroadcastOverrides")
    assert 'morning: clean("morning")' in loader
    assert 'evening: clean("evening")' in loader


# --- functional check: the shipped picker logic actually runs -----------------
#
# The source-level pins above prove the right code is present; the test below
# goes one step further and executes the real functions from js/admin.js in a
# Node VM with a small fake DOM, so the search / ranking / pagination / any-
# product-pinning behaviour is verified behaviourally, not just textually.

PICKER_VM_SCRIPT = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";
const src = readFileSync("js/admin.js", "utf8");
const grab = (name) => {
  for (const prefix of ["async function ", "function "]) {
    const start = src.indexOf(prefix + name + "(");
    if (start < 0) continue;
    let depth = 0, i = src.indexOf("{", start);
    for (; i < src.length; i++) {
      if (src[i] === "{") depth++;
      else if (src[i] === "}") { depth--; if (!depth) break; }
    }
    return src.slice(start, i + 1);
  }
  throw new Error(name + " not found");
};
class FakeEl {
  constructor() { this._html = ""; this.value = ""; this.scrollTop = 0;
                  this.clientHeight = 400; this.scrollHeight = 400; }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = v; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  insertAdjacentHTML(_pos, html) { this._html += html; }
  addEventListener() {}
  focus() {}
}
const els = {};
const sandbox = {
  URLSearchParams, parseInt, Number, Math, Date, JSON, Promise, Array, Object,
  String, Set, Map, console,
  setTimeout: (fn) => fn(), clearTimeout() {}, requestAnimationFrame: (fn) => fn(),
  sessionStorage: { store: {}, getItem(k) { return this.store[k] ?? null; },
                    setItem(k, v) { this.store[k] = v; },
                    removeItem(k) { delete this.store[k]; } },
  history: { replaceState() {} },
  navigator: { clipboard: { writeText: async () => {} } },
  location: { origin: "https://jaurastore.example" },
};
sandbox.window = { location: sandbox.location };
sandbox.$ = (sel) => (els[sel] = els[sel] || new FakeEl());
const $ = sandbox.$;
const TOTAL = 63;  // 60 in-stock bags + a sold-out one + a hidden one + an accessory
const PRODUCTS = [];
for (let i = 1; i <= 60; i++) {
  PRODUCTS.push({ id: "p" + i, name: "Bag " + i, sku: "SKU-" + i, category: "bags",
                  priceNgn: 1000 + i, stock: i, online: true,
                  image: "images/products/x" + i + ".jpg" });
}
PRODUCTS.push({ id: "oos1", name: "Sold Out Clutch", sku: "SKU-OOS", category: "bags",
                priceNgn: 5000, stock: 0, online: true });
PRODUCTS.push({ id: "hid1", name: "Hidden Wallet", sku: "SKU-HID", category: "accessories",
                priceNgn: 300, stock: 5, online: false });
PRODUCTS.push({ id: "acc1", name: "Gold ChainAccessory", sku: "ACC-9", category: "accessories",
                priceNgn: 700, stock: 2, online: true });
sandbox.JA = {
  products: () => PRODUCTS,
  categoryName: (id) => (id === "bags" ? "Bags" : "Accessories"),
  money: (n, cur) => (cur === "NGN" ? "N " + n : "F CFA " + n),
  toCfa: (n) => n,
  displayName: (p) => p.name,
  asset: (p) => p,
  toast: (m) => { sandbox.__toasts.push(m); },
};
sandbox.__toasts = [];
sandbox.esc = (v) => String(v);
vm.createContext(sandbox);
const pieces = [
  'let bcSlot = "morning"; let bcShuffle = 0;',
  "const bcSelected = { morning: new Set(), evening: new Set() };",
  "const BC_AUTO_QUEUE_SIZE = 4;",
  "const bcAutoQueued = { morning: false, evening: false };",
  "let bcPickerTarget = null;",
  "let bcOverrides = { morning: [], evening: [] };",
  "const BC_PICKER_PAGE_SIZE = 24; let bcPickerMatches = []; let bcPickerShown = 0;",
  "function paintBroadcastFeed() {}",
];
for (const fn of ["broadcastInStock", "broadcastEligibleProducts", "broadcastDaySeed",
                  "broadcastFeedFor", "broadcastScheduledFeedFor",
                  "broadcastOverrideForProduct", "broadcastProductUrl",
                  "broadcastPriceLine", "broadcastDisplayName", "broadcastOptionsLine",
                  "broadcastFullText", "broadcastPickerStatus", "broadcastPickerMatches",
                  "broadcastPickerItemHTML", "broadcastPickerResultsHTML",
                  "broadcastPickerMoreHTML", "broadcastPickerRefresh",
                  "broadcastPickerShowMore", "closeBroadcastPicker",
                  "chooseBroadcastProduct", "saveBroadcastOverrides",
                  "broadcastPhotoUrl", "broadcastPhotosGridHTML",
                  "openBroadcastPhotosDrawer"]) {
  pieces.push(grab(fn));
}
vm.runInContext(pieces.join("\n"), sandbox);
const run = (code) => vm.runInContext(code, sandbox);
const assert = (cond, msg) => { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } };

$("#mk-bc-picker-search").value = "";
$("#mk-bc-picker-category").value = "";
run("broadcastPickerRefresh(true)");
assert(run("bcPickerMatches.length") === TOTAL,
       "whole catalogue matched (" + TOTAL + ")");
assert(run("bcPickerShown") === 24, "only the first page (24) is shown");
assert(run("bcPickerMatches.slice(0, 61).every(p => p.online !== false && Number(p.stock) > 0)"),
       "all ready-to-post items ranked before sold-out/hidden ones");
let html = run("broadcastPickerResultsHTML()");
assert(html.includes("Showing 24 of 63"), "pagination footer shows progress");
assert((html.match(/data-bc-choose/g) || []).length === 24,
       "one page of rows rendered, not the whole list dumped");

$("#mk-bc-picker-search").value = "sku-oos";
run("broadcastPickerRefresh(true)");
assert(run("bcPickerMatches.length") === 1 && run("bcPickerMatches[0].id") === "oos1",
       "search by SKU finds the sold-out item");
$("#mk-bc-picker-search").value = "accessor";
run("broadcastPickerRefresh(true)");
assert(run("bcPickerMatches.length") === 2, "search by category display name works");

$("#mk-bc-picker-search").value = "";
run("broadcastPickerRefresh(true)");
run("broadcastPickerShowMore()");
assert(run("bcPickerShown") === 48, "show-more appends the next page (24 -> 48)");
run("broadcastPickerShowMore()");
run("broadcastPickerShowMore()");
assert(run("bcPickerShown") === TOTAL, "pagination clamps at the match count");
html = run("broadcastPickerResultsHTML()");
assert(!html.includes("Showing"), "no footer once everything is shown");

run('bcPickerTarget = null; chooseBroadcastProduct("oos1")');
assert(run("bcOverrides.morning.length") === 1 && run("bcOverrides.morning[0].id") === "oos1",
       "an out-of-stock product can be pinned");
assert(sandbox.__toasts.some((t) => t.includes("out of stock")),
       "the pick is confirmed with an out-of-stock note");
assert(run("broadcastScheduledFeedFor('morning')").some((p) => p.id === "oos1"),
       "the scheduled batch renders the custom out-of-stock pick");

assert(run("broadcastPickerStatus({online: true, stock: 5}).label") === "In stock", "In stock badge");
assert(run("broadcastPickerStatus({online: true, stock: 0}).label") === "Out of stock", "Out of stock badge");
assert(run("broadcastPickerStatus({online: false, stock: 5}).label") === "Hidden", "Hidden badge");
assert(run('broadcastPhotoUrl({ image: "images/products/x1.jpg" })') === "images/products/x1.jpg",
       "photo URL resolves the cover image");
assert(run("broadcastPhotoUrl({})").includes("_placeholder"),
       "photo URL falls back to the placeholder");
console.log("ALL FUNCTIONAL CHECKS PASSED");
"""


def test_the_picker_search_ranking_and_pagination_behaviour_runs():
    """Executes the real js/admin.js picker functions in a Node VM with a
    63-product fake catalogue and a fake DOM: the picker must match the whole
    catalogue (not just eligible products), rank ready-to-post items first,
    render one page at a time with a working show-more, find products by SKU
    or category name, and let ANY product - including a sold-out one - be
    pinned into the scheduled batch."""
    import subprocess
    import textwrap

    script = textwrap.dedent(PICKER_VM_SCRIPT)
    result = subprocess.run(["node", "--input-type=module", "-e", script],
                            cwd=ROOT, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, (
        "picker VM run failed:\n" + result.stdout + "\n" + result.stderr)
    assert "ALL FUNCTIONAL CHECKS PASSED" in result.stdout
