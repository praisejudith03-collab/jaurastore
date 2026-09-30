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


def test_the_store_link_points_at_the_real_product_page():
    body = _func(ADMIN_JS, "broadcastProductUrl")
    assert "/product.html?id=" in body
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


def test_copy_details_writes_the_caption_to_the_clipboard():
    body = _func(ADMIN_JS, "copyProductDetails")
    assert "broadcastFullText(p)" in body
    assert "navigator.clipboard.writeText(text)" in body


def test_out_of_stock_items_are_still_excluded_from_the_eligible_feed():
    """Owner rule: sold-out items (or ones whose every variant is 0) never
    appear in the rotation to be copied/posted in the first place."""
    eligible = _func(ADMIN_JS, "broadcastEligibleProducts")
    assert "broadcastInStock(p)" in eligible


def test_each_card_copy_button_is_wired_to_copy_product_details():
    paint_body = _func(ADMIN_JS, "paintBroadcastFeed")
    assert "[data-bc-copy]" in paint_body
    assert "copyProductDetails(p)" in paint_body


def test_the_bulk_bar_copies_details_for_every_selected_product():
    """Owner request 2026-09-30: the old "Share batch to WhatsApp Channel"
    multi-product bundling is gone; selecting several products now copies
    all of their captions (still URL-free, still no image) in one tap."""
    assert '"#mk-bc-copy-batch"' in ADMIN_JS
    assert "mk-bc-share-batch" not in ADMIN_JS
    body = _func(ADMIN_JS, "bindBroadcastFeed")
    copy_batch = body[body.index('"#mk-bc-copy-batch"'):]
    assert "bcSelected[bcSlot]" in copy_batch
    assert "navigator.clipboard.writeText(text)" in copy_batch


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


def test_custom_product_picker_is_searchable_and_only_uses_in_stock_catalog():
    card = _func(ADMIN_JS, "broadcastFeedCardHTML")
    assert "Select custom product" in card
    assert 'type="search"' in card
    picker = _func(ADMIN_JS, "broadcastPickerResultsHTML")
    assert "broadcastEligibleProducts()" in picker
    assert "haystack.includes(term)" in picker
    product_card = _func(ADMIN_JS, "broadcastCardHTML")
    assert "Swap item" in product_card


def test_morning_and_evening_custom_overrides_are_independent_and_daily():
    assert "const bcOverrides = loadBroadcastOverrides()" in ADMIN_JS
    storage = _func(ADMIN_JS, "broadcastOverrideStorageKey")
    assert "broadcastDaySeed()" in storage
    loader = _func(ADMIN_JS, "loadBroadcastOverrides")
    assert 'morning: clean("morning")' in loader
    assert 'evening: clean("evening")' in loader
