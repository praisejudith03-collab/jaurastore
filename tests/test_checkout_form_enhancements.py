"""Checkout form enhancements tests:
1. reCAPTCHA silent refresh, graceful mobile fallback, and background warnings.
2. 'Place Your Order' action button text & loading spinner state.
3. Delivery zone hierarchy & Inter-State Notice banner.
4. Soft field validation, inline tooltip ('Please fill in this detail to complete your order.'), red borders & direct smooth scroll.
"""
import os
import re
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_checkout_button_text_is_place_your_order():
    html = _read("checkout.html")
    assert 'data-i18n="ck.place">Place Your Order</button>' in html
    assert 'Place order</button>' not in html


def test_i18n_has_place_your_order_and_spinner_phrases():
    i18n = _read("js", "i18n.js")
    assert '"ck.place": "Place Your Order"' in i18n
    assert '"ck.placing": "Placing your order…"' in i18n
    assert '"ck.interstateNotice":' in i18n
    assert "Inter-State Delivery: Your order will be dispatched via courier." in i18n
    assert '"ck.errorRequired": "Please fill in this detail to complete your order."' in i18n


def test_css_has_spinner_and_validation_styles():
    css = _read("css", "style.css")
    assert ".ck-spinner" in css
    assert "animation: ck-spin" in css or "animation:ck-spin" in css
    assert ".ck-place.is-loading" in css
    assert ".ck-interstate-notice" in css
    assert ".field-error" in css
    assert ".ck-inline-tooltip" in css
    assert "border-color: #dc2626" in css


def test_checkout_html_has_hierarchy_and_interstate_notice():
    html = _read("checkout.html")
    assert "data-interstate-notice" in html
    assert "Pick up only" in html
    assert "🇳🇬 Lagos State (Express Delivery)" in html
    assert "🇳🇬 Other States in Nigeria (Inter-State Dispatch)" in html
    assert "Cotonou (1,000 – 3,000 CFA)" in html
    # Verify order in HTML
    pickup_idx = html.index("Pick up only")
    ng_idx = html.index("🇳🇬 Lagos State (Express Delivery)")
    cfa_idx = html.index("Cotonou (1,000 – 3,000 CFA)")
    assert pickup_idx < ng_idx < cfa_idx, "Hierarchy must be Pickup -> Nigeria -> Benin & Togo"


def test_app_js_handles_zone_hierarchy_and_interstate_notice():
    app = _read("js", "app.js")
    assert "Pick up only" in app
    assert "🇳🇬 Lagos State (Express Delivery)" in app
    assert "🇳🇬 Other States in Nigeria (Inter-State Dispatch)" in app
    assert "function updateInterStateNotice" in app
    assert "data-interstate-notice" in app


def test_app_js_button_spinner_and_reset():
    app = _read("js", "app.js")
    assert "ck-spinner" in app
    assert "is-loading" in app
    assert "resetButton" in app or "btn.classList.remove" in app


def test_app_js_validation_and_scrolling():
    app = _read("js", "app.js")
    assert "Please fill in this detail to complete your order." in app
    assert "ck-inline-tooltip" in app
    assert "scrollIntoView" in app
    assert "firstControl" in app


def test_security_recaptcha_gate_fails_open_and_records_warning():
    import security as sec
    import flask
    from config import Config

    app = flask.Flask(__name__)
    with app.test_request_context("/", headers={"X-CSRF-Token": "tok"}):
        # When RECAPTCHA_REQUIRED is False (default), gate returns None (passes through)
        res = sec.recaptcha_gate("checkout")
        assert res is None
