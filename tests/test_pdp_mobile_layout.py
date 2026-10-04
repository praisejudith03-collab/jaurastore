"""Phone-first PDP: name, price, options and Buy inside one screen.

The owner's report: on her phone the product page needed too much scrolling
before the Buy button, because desktop spacing (a 56px gap, 40px of padding, a
36-56px headline, 22px around the actions) was applied at every width. The
fix is a compaction pass written at the END of the sheet (the file has older
.pdp rules further down, so anything earlier loses) that only applies at phone
widths. These tests pin the numbers that matter and - just as important - that
the mobile block really is last, because a later desktop rule would silently
re-inflate the page.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(ROOT, "css", "style.css")


def _css():
    with open(CSS, encoding="utf-8") as fh:
        return fh.read()


def _mobile_block(css):
    """The last max-width:640px block that mentions the PDP layout."""
    blocks = []
    for m in re.finditer(r"@media\s*\(max-width:\s*640px\)\s*\{", css):
        start = m.end()
        depth, i = 1, start
        while i < len(css) and depth:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        blocks.append(css[start:i - 1])
    for block in reversed(blocks):
        if ".pdp-actions" in block or ".wrap.pdp" in block:
            return block
    raise AssertionError("no mobile PDP compaction block found in css/style.css")


def test_the_mobile_compaction_block_is_last_so_it_cannot_be_re_inflated():
    css = _css()
    block = _mobile_block(css)
    end = css.index(block) + len(block)
    tail = css[end:]
    # Any LATER rule that re-inflates the padded areas would win over the
    # compaction, which is exactly the bug this pass exists to fix.
    for prop in ("margin: 22px 0", "gap: 56px", "padding: 40px 0 80px"):
        assert prop not in tail, f"a later rule re-inflates the mobile PDP: {prop}"


def test_the_phone_page_tightens_the_stack_and_the_buy_area():
    block = _mobile_block(_css())
    assert "gap: 14px !important" in block, "the two PDP columns must sit close"
    assert "padding: 10px 0 32px !important" in block
    assert ".pdp-actions" in block and "margin: 12px 0 4px !important" in block
    assert "padding: 13px 16px !important" in block, "the buy button stays tappable"
    assert "min-width: 0 !important" in block, \
        "the desktop 180px minimum must not wrap the button on a phone"


def test_the_phone_headline_and_price_stop_eating_the_screen():
    block = _mobile_block(_css())
    assert ".pdp h1" in block and "font-size: clamp(17px, 5.4vw, 21px) !important" in block
    assert ".pdp .price" in block, "the price rhythm is tightened too"
    assert "max-height: 58vh" in block, \
        "the square gallery must not push the buy area off the first screen"


def test_the_note_field_is_a_short_single_line_on_a_phone():
    block = _mobile_block(_css())
    assert ".pdp-product-note { margin: 10px 0 !important" in block
    css = _css()
    box = css[css.index(".pdp-product-note input {"):]
    box = box[:box.index("}")]
    assert "height: 42px" in box and "max-height: 42px" in box
    assert "appearance: none" in box, \
        "the browser's own control look is off so a phone cannot make it tall"


def test_the_desktop_layout_is_untouched():
    css = _css()
    assert "grid-template-columns: 1.05fr 0.95fr !important" in css, \
        "the two-column desktop PDP must survive the mobile pass"
    # The desktop gap lives in the min-width rule that sets the two columns.
    at = css.index("grid-template-columns: 1.05fr 0.95fr !important")
    desktop = css[max(0, at - 400):at + 200]
    assert "gap: 36px !important" in desktop, \
        "the desktop gap belongs to the min-width rule, not the phone one"
