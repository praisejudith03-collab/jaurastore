"""Channel Broadcast Feed (admin panel) — owner requests 2026-09-28.

A "Smart Rotation & Selection" section in Marketing that:
  * auto-cycles active, in-stock products across every category, twice a
    day (a morning batch and an evening batch), so the same items are not
    suggested every day;
  * automatically queues the first few picks for each batch the moment it
    is opened (or reshuffled), so there is already a ready-to-post set
    rather than a blank list to build from scratch;
  * turns each pick (and each batch) into a WhatsApp share carrying the
    EXACT product photo as a native image attachment (navigator.share with
    files), a bilingual name, clean active-price-only pricing in both
    currencies, the in-stock colour/size options, and the bare product
    link on its own line - never wrapped in extra words - with a graceful
    fallback to WhatsApp's own text share sheet wherever the browser
    cannot attach a file directly.

The rotation and message-building logic lives entirely in js/admin.js and
runs in the browser against data already loaded into the admin page
(JA.products()/JA.categories()), so - consistent with how the rest of the
admin UI is covered in this test suite (see
test_receipts_and_crash_reports_render_as_collapsible_cards and the
Homepage Featured accordion tests) - it is pinned down at the source
level: the right filters, the right rotation inputs and the right message/
share behaviour are all present, and cannot silently drift.

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
    assert 'Number(p.stock) > 0' in body


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


def test_each_card_carries_name_price_options_and_a_working_store_link():
    body = _func(ADMIN_JS, "broadcastCardHTML")
    assert "broadcastProductUrl(p)" in body
    assert "broadcastPriceLine(p)" in body
    assert "broadcastDisplayName(p)" in body
    assert "broadcastOptionsLine(p)" in body
    assert 'data-bc-share="${esc(String(p.id))}"' in body


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
    message_body = _func(ADMIN_JS, "broadcastMessageFor")
    assert "broadcastOptionsLine(p)" in message_body


def test_the_store_link_points_at_the_real_product_page():
    body = _func(ADMIN_JS, "broadcastProductUrl")
    assert "/product.html?id=" in body
    assert "location.origin" in body


def test_the_caption_ends_with_the_bare_clean_link_only():
    """Owner request 2026-09-28: "the caption includes only the clean,
    direct product link... rather than cluttered site text" - the link
    line itself must never be prefixed with "Shop now:" or similar."""
    body = _func(ADMIN_JS, "broadcastFullText")
    code_lines = [ln for ln in body.splitlines() if not ln.strip().startswith("//")]
    code = "\n".join(code_lines)
    assert "broadcastMessageFor(p)" in code
    assert "broadcastProductUrl(p)" in code
    assert "Shop now" not in code
    # The message block and the bare link are two distinct pieces, not one
    # sentence the link is buried inside of.
    assert "\\n\\n${broadcastProductUrl(p)}" in code


def test_the_message_block_itself_has_no_link_or_shop_now_wording():
    body = _func(ADMIN_JS, "broadcastMessageFor")
    assert "broadcastProductUrl" not in body
    assert "Shop now" not in body


def test_the_exact_product_photo_is_fetched_as_a_real_file():
    """Owner request 2026-09-28: native navigator.share() needs the actual
    image bytes, not just a link WhatsApp may or may not unfurl."""
    body = _func(ADMIN_JS, "broadcastImageFile")
    assert "JA.asset(p.image" in body
    assert "await fetch(src)" in body
    assert "new File([blob]" in body


def test_native_share_is_only_attempted_when_the_browser_actually_supports_files():
    body = _func(ADMIN_JS, "broadcastCanShareFiles")
    assert "navigator.share" in body
    assert "navigator.canShare" in body
    assert "{ files }" in body


def test_share_native_attaches_the_photo_and_falls_back_to_whatsapps_share_sheet():
    body = _func(ADMIN_JS, "broadcastShareNative")
    assert "broadcastImageFile(p)" in body
    assert "navigator.share({ files: [file], text })" in body
    assert "broadcastShareUrl(text)" in body
    # Cancelling the native share sheet is not an error worth falling back
    # from - only a genuinely unsupported/failed share is.
    assert 'e.name === "AbortError"' in body


def test_a_batch_share_also_attaches_every_selected_photo():
    body = _func(ADMIN_JS, "broadcastShareBatchNative")
    assert "Promise.all(chosen.map(broadcastImageFile))" in body
    assert "navigator.share({ files, text })" in body
    assert "broadcastShareUrl(text)" in body


def test_share_opens_whatsapps_own_share_sheet_as_the_fallback_with_no_copy_paste():
    body = _func(ADMIN_JS, "broadcastShareUrl")
    assert "whatsapp://send?text=" in body
    assert "encodeURIComponent(text)" in body
    assert "api.whatsapp.com" not in body
    # The per-card action is a real control the owner taps once - not a
    # link they have to notice and click twice - wired through
    # paintBroadcastFeed to the native-share flow.
    paint_body = _func(ADMIN_JS, "paintBroadcastFeed")
    assert "[data-bc-share]" in paint_body
    assert "broadcastShareNative(p)" in paint_body


def test_a_batch_of_selected_products_is_combined_into_one_message():
    body = _func(ADMIN_JS, "bindBroadcastFeed")
    assert '"#mk-bc-share-batch"' in body
    assert "bcSelected[bcSlot]" in body
    assert "broadcastShareBatchNative(chosen, heading)" in body
    # Bilingual heading too, matching the per-item message.
    assert "Bonjour" in body and "Ce soir" in body


def test_the_morning_and_evening_batches_keep_independent_selections():
    assert "const bcSelected = { morning: new Set(), evening: new Set() }" in ADMIN_JS


def test_selecting_nothing_and_sharing_a_batch_is_refused_not_a_blank_message():
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


def test_legacy_share_fallback_launches_the_native_whatsapp_uri():
    one = _func(ADMIN_JS, "broadcastShareNative")
    batch = _func(ADMIN_JS, "broadcastShareBatchNative")
    assert "window.location.href = broadcastShareUrl(text)" in one
    assert "window.location.href = broadcastShareUrl(text)" in batch
