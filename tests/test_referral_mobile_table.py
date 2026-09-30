"""Referral codes table: fits 100% of the mobile viewport, Delete never clipped.

Regression pinned 2026-09-30: the admin Marketing screen built the Referral
codes card with `id="mk-referrals-card"` but the mobile-responsive rules in
css/style.css were only ever written against the CLASS selector
`.mk-referrals-card`, which nothing in js/admin.js ever attached to the
element. Every phone-only fix for that table (bounded width, stacked rows,
a full-width Delete button) was therefore dead CSS, and the table's
`min-width: 520px` (from the unscoped `.mk-table` rule) silently overflowed
past the viewport - `html, body { overflow-x: hidden }` then clipped the
rightmost column (the Delete/Retire button) off screen instead of it
scrolling into view.

Run with:  python3 -m pytest tests/test_referral_mobile_table.py -q
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _admin_js():
    with open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8") as fh:
        return fh.read()


def _css():
    with open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8") as fh:
        return fh.read()


def test_referrals_card_actually_carries_its_responsive_class():
    js = _admin_js()
    # The element the CSS mobile rules target must be the SAME element that
    # exists in the DOM - id AND class together, not just the id.
    assert re.search(
        r'class="admin-card mk-referrals-card"\s+id="mk-referrals-card"', js
    ), "The referrals card must carry the mk-referrals-card CLASS the CSS targets"


def test_referrals_table_has_stacking_markup():
    js = _admin_js()
    assert "referrals-table" in js
    # Each data cell needs a label for the phone stacked-card layout to show
    # a "Code" / "Customer" / ... caption above its value.
    for label in ("Code", "Customer", "Uses", "Reward", "Created", "Action"):
        assert f'data-label="{label}"' in js
    assert "data-mk-ref-del=" in js  # the Delete/Retire button itself


def test_referrals_table_never_forced_wider_than_viewport_on_phone():
    css = _css()
    # The old fix forced a 560px-wide table (guaranteed overflow on any
    # phone <=560px) instead of fitting it to 100%.
    assert "mk-referrals-card .mk-table { min-width: 560px" not in css
    assert ".mk-referrals-card .referrals-table { min-width: 0 !important; width: 100% !important; }" in css


def test_referrals_table_stacks_into_full_width_cards_on_phone():
    css = _css()
    anchor = css.index(".mk-referrals-card .referrals-table { max-width: none; }")
    tail = css[anchor:]
    block_match = re.search(r"@media \(max-width: 640px\)\s*\{.*?\n\}", tail, re.S)
    assert block_match, "Expected a phone-only stacking block for .mk-referrals-card"
    block = block_match.group(0)
    assert ".mk-referrals-card .referrals-table thead { display: none; }" in block
    assert "display: block" in block
    # The Delete/Retire button must be full-width and comfortably tappable.
    assert re.search(r"\.mk-referrals-card \.referrals-table \.btn\s*\{[^}]*width:\s*100%", block)
    assert re.search(r"\.mk-referrals-card \.referrals-table \.btn\s*\{[^}]*min-height:\s*44px", block)
