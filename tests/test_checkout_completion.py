"""Checkout completion and homepage-performance non-regression pins."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_checkout_has_only_two_steps_and_required_instructions():
    html = _read("checkout.html")
    breadcrumb = re.search(r'<ol class="ck-steps".*?</ol>', html, re.S)
    assert breadcrumb, "checkout breadcrumb is missing"
    steps = breadcrumb.group(0).upper()
    assert "SHOPPING CART" in steps and "CHECKOUT" in steps
    assert "ORDER COMPLETE" not in steps
    assert "Please fill this form before checking out." in html
    assert "Please send the exact amount for your order to be confirmed." in html
    assert 'data-wa-toggle="nigeria"' not in html
    assert 'data-wa-toggle="benin"' not in html
    assert html.index("Please fill this form before checking out.") < html.index('data-checkout>')


def test_order_complete_step_exists_only_on_dedicated_success_page():
    success = _read("order-complete.html")
    breadcrumb = re.search(r'<ol class="ck-steps".*?</ol>', success, re.S)
    assert breadcrumb and "ORDER COMPLETE" in breadcrumb.group(0).upper()
    assert 'data-page="order-complete"' in success
    checkout = _read("checkout.html")
    assert 'data-page="order-complete"' not in checkout


def test_place_order_immediately_shows_the_dedicated_success_view():
    app = _read("js/app.js")
    assert "order.submission ? await order.submission" not in app
    assert "const submission = order.submission" in app
    assert 'const target = "order-complete.html?order="' in app
    assert "window.history.replaceState({ orderId: order.id }, \"\", target)" in app
    assert "paintOrderCompleteChrome();" in app
    assert "showOrderDone(order);" in app
    assert app.index("const submission = order.submission") < app.index("showOrderDone(order);")
    # Refreshing the dedicated page can still recover the server copy.
    assert 'window.JA_NET.api("api/orders/" + encodeURIComponent(id))' in app
    # The success view includes the form information the customer entered and
    # tells them where the later payment confirmation will arrive.
    assert 'class="order-customer-info"' in app
    assert 'class="ck-email-confirmation"' in app
    i18n = _read("js/i18n.js")
    assert ("A confirmation message will be sent to your email once your order "
            "and payment are confirmed.") in i18n
    for field in ('customer.name', 'customer.phone', 'customer.email',
                  'customer.address', 'customer.city', 'customer.zone',
                  'customer.country', 'customer.note'):
        assert field in app
    store = _read("js/store.js")
    assert 'Object.defineProperty(order, "submission"' in store
    assert "enumerable: false" in store


def test_homepage_prioritizes_hero_and_defers_external_scripts():
    html = _read("index.html")
    assert '<link rel="preload" as="image" href="images/products/smartwatch-with-game-pad.jpg" fetchpriority="high"' in html
    assert '<link rel="preload" as="font"' in html
    assert '<link rel="preconnect" href="https://rvkweyipqgsggcnimhxf.supabase.co" crossorigin' in html
    hero = re.search(r'<img id="hero-static-img"[^>]+>', html)
    assert hero
    for attr in ('width="610"', 'height="900"', 'loading="eager"',
                 'fetchpriority="high"', 'decoding="async"'):
        assert attr in hero.group(0)
    external_scripts = re.findall(r'<script\s+([^>]*\bsrc="[^"]+"[^>]*)>', html)
    assert external_scripts and all("defer" in attrs for attrs in external_scripts)
    assert 'http-equiv="cache-control"' not in html.lower()
    assert 'http-equiv="pragma"' not in html.lower()
