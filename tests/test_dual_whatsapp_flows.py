"""Dual-WhatsApp workflows (owner directive 2026-09-12) — source pins.

Two separate channels, never mixed:

  * Floating widget / footer buttons -> general product & store inquiries,
    with a clean pre-filled message and NO order data.
  * Checkout / thank-you screen      -> order completion only: customer
    name, Order ID, items, total price (NGN or F CFA), delivery location
    and the transport-fare question.

Country routing: buyers pick the Nigeria line or the Benin/Togo line —
at checkout (before ordering) and again on the thank-you screen. Both
lines come from site settings / the environment, never hardcoded.

Run with:  python3 -m pytest tests/test_dual_whatsapp_flows.py -q
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _fn_body(src, name):
    """Body of `function NAME(...) { ... }` (first match, brace-balanced).
    Parameter lists may themselves contain calls (e.g. `cur = currency()`),
    so scan from the name to the first `{` instead of stopping at `)`."""
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(", src)
    if not m:
        return ""
    brace = src.find("{", m.end())
    if brace == -1:
        return ""
    depth, start = 0, brace
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start + 1:i]
    return ""


# ------------------------------------------------- floating widget: inquiries
def test_floating_widget_carries_a_clean_general_inquiry_message():
    store = _read("js", "store.js")
    body = _fn_body(store, "waInquiryText")
    assert body, "store.js must keep the waInquiryText() helper"
    msg = re.search(r"return\s+\"([^\"]+)\"", body)
    assert msg, "waInquiryText must return a pre-filled message"
    text = msg.group(1)
    assert "inquiry" in text.lower(), text
    # It is the GENERAL channel: no order data may leak into it.
    for banned in ("Order ID", "Total:", "Items:", "transport fare"):
        assert banned not in text, f"general message must not mention {banned!r}"
    # The floating button and the footer button both use the inquiry URL.
    assert "wa-float" in store and "data-wa-inquiry" in store
    assert "data-fare-wa" not in store, \
        "the floating widget must never carry the order-completion link"


def test_floating_widget_is_not_used_for_order_completion():
    app = _read("js", "app.js")
    inquiry = _fn_body(_read("js", "store.js"), "waInquiryText")
    general = re.search(r"return\s+\"([^\"]+)\"", inquiry).group(1)
    done = _fn_body(app, "showOrderDone")
    assert done, "app.js must keep the thank-you screen painter"
    assert general not in done, \
        "the thank-you screen must use the order-completion message, not the general one"


# ------------------------------------------- checkout: order-completion message
def test_checkout_message_compiles_the_full_order():
    app = _read("js", "app.js")
    body = _fn_body(app, "fareWaText")
    assert body, "app.js must keep fareWaText(order) for the order-completion link"
    for needle in (
        "my name is",                                        # customer name
        "Order ID: ",                                        # order id
        "orderSummaryLines(order)",                          # the items
        "JA.money(order.total, order.currency)",            # total price
        "Delivery location: ",                               # where to deliver
        "transport fare",                                    # the fare question
    ):
        assert needle in body, f"fareWaText must compile {needle!r}"
    # Separation: the checkout message is NOT the general inquiry text.
    assert "inquiry about your products" not in body


def test_item_lines_and_total_render_in_both_currencies():
    store = _read("js", "store.js")
    money = _fn_body(store, "money")
    assert money, "store.js must keep money()"
    assert '"₦"' in money or "₦" in money, "NGN totals render with the naira sign"
    assert "F CFA" in money, "CFA totals render as F CFA"
    lines = _fn_body(_read("js", "app.js"), "orderSummaryLines")
    assert "qty" in lines and "JA.money(" in lines, \
        "each item line carries quantity and a currency-aware price"


# ------------------------------------------------------- country routing: two lines
def test_two_markets_route_to_two_whatsapp_numbers():
    store = _read("js", "store.js")
    region = _fn_body(store, "waRegionFor")
    assert "nigeria" in region, "Nigeria maps to the Nigeria line"
    for token in ("benin", "togo"):
        assert token in region, f"{token} maps to the Benin/Togo line"
    wa_number = _fn_body(store, "waNumber")
    assert "whatsapp_number_ng" in wa_number, "Nigeria line comes from settings"
    assert "whatsapp_number_bj" in wa_number, "Benin/Togo line comes from settings"
    wa_link = _fn_body(store, "waLink")
    assert "https://wa.me/" in wa_link, "links are wa.me deep links"
    # Defaults: two distinct numbers, digits only.
    assert re.search(r'whatsapp_number_ng:\s*"\d+"', store)
    assert re.search(r'whatsapp_number_bj:\s*"\d+"', store)


def test_checkout_page_lets_buyers_choose_their_line():
    html = _read("checkout.html")
    assert 'data-wa-toggle="nigeria"' in html, "checkout offers the Nigeria line"
    assert 'data-wa-toggle="benin"' in html, "checkout offers the Benin/Togo line"
    assert "ck.waPickCheckout" in html, "the chooser carries its own label"
    app = _read("js", "app.js")
    assert "bindWaLineChooser(form)" in app, \
        "the checkout form binds the line chooser before the order is placed"
    # Choosing a country in the form follows the buyer onto the line.
    assert re.search(r"paintWaLineChooser\(form\)", app), \
        "changing the Country field repaints the chosen line"


def test_line_labels_read_like_the_owner_words_them():
    i18n = _read("js", "i18n.js")
    assert '"ck.waNg": "Nigeria WhatsApp"' in i18n
    assert '"ck.waBj": "Benin / Togo WhatsApp"' in i18n
    # French carries the same two lines.
    assert '"ck.waNg": "WhatsApp Nigeria"' in i18n
    assert '"ck.waBj": "WhatsApp B\\u00e9nin / Togo"' in i18n


def test_thank_you_screen_keeps_the_country_toggle():
    app = _read("js", "app.js")
    done = _fn_body(app, "showOrderDone")
    assert 'data-wa-toggle="nigeria"' in done and 'data-wa-toggle="benin"' in done
    assert "bindFareWaToggle(order, root)" in _fn_body(app, "showOrderDone") or \
        "bindFareWaToggle(order, root)" in app
    assert "data-fare-wa" in done, \
        "the thank-you CTA is the order-completion WhatsApp button"
