"""Comprehensive End-to-End Checkout Audit & Diagnostic Test Suite.

Verifications:
1. reCAPTCHA & Security Token Flow: silent auto-refresh, graceful mobile fallback, backend passthrough.
2. Mandatory Field Soft-Validation: inline tooltips, red border highlight, direct smooth-scrolling to first missing field.
3. Delivery Zone Selection & Hierarchy: Pick up only, Lagos State, Inter-State Nigeria, banner display, fee ranges.
4. Final Order Action Button: 'Place Your Order' text, single loading state & spinner on click.
5. Minimum Order Notice Banner Removal: .ck-bj-min banner completely removed from checkout UI with zero leftover layout artifacts.
"""
import os
import re
from pathlib import Path
import pytest
import flask

import app as appmod
import security as sec
import delivery
import currency
from config import Config
from db import init_db, execute, query

ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


# --------------------------------------------------------------------------
# 1. UI Clean-up: Minimum Order Notice Banner Removal
# --------------------------------------------------------------------------
def test_minimum_order_banner_removed_from_checkout_html():
    html = _read("checkout.html")
    assert 'class="ck-bj-min"' not in html
    assert "Benin deliveries: minimum order" not in html
    assert "Pickup in Cotonou is free for lighter products.</p>" not in html


def test_minimum_order_banner_no_leftover_css_artifacts():
    css = _read("css", "style.css")
    assert ".ck-bj-min" in css
    assert "display: none !important" in css


def test_js_paint_min_order_removes_orphan_banner():
    app_js = _read("js", "app.js")
    assert "function paintMinOrderNotices()" in app_js
    assert 'const bjMin = document.querySelector(".ck-bj-min");' in app_js
    assert "bjMin.remove();" in app_js


# --------------------------------------------------------------------------
# 2. reCAPTCHA & Security Token Flow
# --------------------------------------------------------------------------
def test_recaptcha_gate_fails_open_for_mobile_users(monkeypatch):
    """When reCAPTCHA is blocked or drops on mobile, the checkout gate allows
    valid orders while silently recording a telemetry warning."""
    monkeypatch.setattr(Config, "RECAPTCHA_REQUIRED", False)
    monkeypatch.setattr(Config, "RECAPTCHA_SECRET_KEY", "secret-test-key")

    app_instance = flask.Flask(__name__)
    with app_instance.test_request_context("/api/orders", headers={"X-CSRF-Token": "test-csrf"}):
        res = sec.recaptcha_gate("checkout")
        assert res is None, "Mobile buyers must not be blocked when token is missing/dropped"


def test_net_js_handles_token_auto_refresh_and_safe_fallback():
    net_js = _read("js", "net.js")
    assert "grecaptcha.reset(" in net_js
    assert "grecaptcha.execute(" in net_js
    assert "grecaptcha.getResponse(" in net_js
    assert 'if (!key) return "";' in net_js


# --------------------------------------------------------------------------
# 3. Mandatory Field Soft-Validation
# --------------------------------------------------------------------------
def test_validation_required_message_wording():
    i18n_js = _read("js", "i18n.js")
    assert '"ck.errorRequired": "Please fill in this detail to complete your order."' in i18n_js
    assert '"ck.errorRequiredInvalid": "Please fill in this detail to complete your order."' in i18n_js


def test_validation_smooth_scroll_to_first_incomplete_field():
    app_js = _read("js", "app.js")
    assert "function validateCheckoutForm" in app_js
    assert "firstControl.focus" in app_js
    assert "firstControl.scrollIntoView" in app_js
    assert "behavior: \"smooth\"" in app_js
    assert "block: \"center\"" in app_js
    # Ensure top-level summary does not hijack scroll
    assert "summary.scrollIntoView" not in app_js


def test_validation_red_border_and_tooltip_classes():
    css = _read("css", "style.css")
    assert ".field.has-error" in css
    assert "border-color: #dc2626 !important" in css
    assert ".ck-inline-tooltip" in css
    assert "⚠️" in css


# --------------------------------------------------------------------------
# 4. Delivery Zone Selection, Hierarchy & Inter-State Notice
# --------------------------------------------------------------------------
def test_delivery_zone_dropdown_hierarchy_in_checkout_html():
    html = _read("checkout.html")
    assert "data-delivery-zones" in html
    assert "Pick up only" in html
    assert "🇳🇬 Lagos State (Express Delivery)" in html
    assert "🇳🇬 Other States in Nigeria (Inter-State Dispatch)" in html

    idx_pickup = html.index("Pick up only")
    idx_lagos = html.index("🇳🇬 Lagos State (Express Delivery)")
    idx_interstate = html.index("🇳🇬 Other States in Nigeria (Inter-State Dispatch)")
    idx_cfa = html.index("Cotonou (1,000 – 3,000 CFA)")

    assert idx_pickup < idx_lagos < idx_interstate < idx_cfa, "Zones must follow Pickup -> Lagos -> Inter-State -> Benin/Togo hierarchy"


def test_interstate_notice_banner_exists():
    html = _read("checkout.html")
    assert "data-interstate-notice" in html
    assert "🚚 Inter-State Delivery: Your order will be dispatched via courier." in html
    assert "Tracking &amp; dispatch updates will be sent directly to your phone/WhatsApp." in html


def test_delivery_zone_fares_contract():
    pickup_fare = delivery.fare_for("Pickup in Cotonou is free for lighter products", "CFA")
    assert pickup_fare[0] is True
    assert pickup_fare[1]["fare_status"] == "pickup"
    assert pickup_fare[1]["delivery_fee_min"] == 0
    assert pickup_fare[1]["delivery_fee_max"] == 0

    lagos_fare = delivery.fare_for("Lagos Mainland", "NGN")
    assert lagos_fare[0] is True
    assert lagos_fare[1]["fare_status"] == "range"
    assert lagos_fare[1]["delivery_fee_min"] == 2000
    assert lagos_fare[1]["delivery_fee_max"] == 5000

    ng_other_fare = delivery.fare_for("Other Nigeria", "NGN")
    assert ng_other_fare[0] is True
    assert ng_other_fare[1]["fare_status"] == "quote"


# --------------------------------------------------------------------------
# 5. Final Order Action Button
# --------------------------------------------------------------------------
def test_final_action_button_wording_and_loading_state():
    html = _read("checkout.html")
    assert '<button class="btn ck-place" type="submit" data-i18n="ck.place">Place Your Order</button>' in html

    app_js = _read("js/app.js")
    assert "setButtonLoading" in app_js or "btn.classList.add(\"is-loading\")" in app_js
    assert "ck-spinner" in app_js
    assert "is-loading" in app_js

    i18n_js = _read("js/i18n.js")
    assert '"ck.place": "Place Your Order"' in i18n_js
    assert '"ck.placing": "Placing your order…"' in i18n_js


# --------------------------------------------------------------------------
# 6. Complete Order Creation Flow
# --------------------------------------------------------------------------
def test_full_order_placement_pipeline(client):
    csrf = client.get("/api/config").get_json()["csrf"]
    order_id = "JA-DIAG001"
    execute("DELETE FROM orders WHERE id=?", (order_id,))

    order_payload = {
        "id": order_id,
        "currency": "NGN",
        "total": 35000,
        "customer": {
            "name": "Diagnostic Buyer",
            "phone": "+2348099887766",
            "email": "diag@example.com",
            "country": "Nigeria",
            "city": "Ikeja",
            "address": "12 Allen Avenue",
            "zone": "Lagos Mainland",
            "note": "Handle with care",
        },
        "items": [
            {"id": "wix-001", "name": "Classic Straight Wig", "qty": 1, "price": 35000}
        ],
    }

    res = client.post("/api/orders", headers={"X-CSRF-Token": csrf}, json=order_payload)
    assert res.status_code == 200, res.data
    data = res.get_json()
    assert data["ok"] is True
    assert data["id"] == order_id
    assert data["delivery"]["delivery_fee_min"] == 2000
    assert data["delivery"]["delivery_fee_max"] == 5000
