"""Checkout payment details must come from Supabase, never from the bundle.

These details (bank "UBA", account 23474678931, the holder name, the MTN MoMo
Benin and Moov Money Togo numbers) used to be hardcoded in js/app.js,
js/store.js and checkout.html. That meant an account change needed a redeploy,
the live account number shipped inside the public bundle, and a misconfigured
site_settings row silently fell back to values that might belong to nobody.

They are now columns on site_settings (id=1), served by GET /api/site and
edited from the Admin Portal. These tests pin that in place.

Run with:  python3 -m pytest tests/test_payment_settings.py -q
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("SITE_CONFIG_PATH", "/tmp/jaura_test_site_pay.json")

import pytest  # noqa: E402
import app as appmod  # noqa: E402
import api as apimod  # noqa: E402
import auth as authmod  # noqa: E402
import supabase_settings  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = "jaurastore@gmail.com"

PAYMENT_COLUMNS = (
    "cfa_payment_provider", "cfa_payment_name", "cfa_payment_account",
    "cfa_payment_instructions",
    "togo_payment_provider", "togo_payment_name", "togo_payment_account",
    "togo_payment_instructions",
    "naira_payment_bank", "naira_payment_name", "naira_payment_account",
    "naira_payment_instructions",
)

# Values that must not appear in anything the browser downloads.
FORBIDDEN = ("23474678931", "OKORAFOR PRAISE", "OKORAFOR GIFT",
             "OKORAFOR GOODNESS", "01 52 01 99 30", "+229 01 68 95 31 10")

SHIPPED_FRONTEND = ("js/app.js", "js/store.js", "js/admin.js", "js/i18n.js",
                    "checkout.html", "index.html", "cart.html")


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture(autouse=True)
def _own_site_config(monkeypatch, tmp_path):
    """Give this module its own site-settings file.

    `_load_site()` reads SITE_CONFIG_PATH at call time, and test_api.py also
    sets it - whichever module imports first would otherwise win, so these
    tests would read and write another module's file.
    """
    path = tmp_path / "site.json"
    path.write_text("{}")
    monkeypatch.setenv("SITE_CONFIG_PATH", str(path))


@pytest.fixture()
def client(app):
    init_db()
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def _login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


# ------------------------------------------------------- the bundle is clean
def test_no_hardcoded_payment_details_in_shipped_frontend():
    for rel in SHIPPED_FRONTEND:
        body = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        for bad in FORBIDDEN:
            assert bad not in body, f"{rel} still hardcodes {bad!r}"


def test_app_js_has_no_payment_fallback_operator():
    """`|| "UBA"` style fallbacks are the mechanism that reintroduces this."""
    app_js = open(os.path.join(ROOT, "js", "app.js"), encoding="utf-8").read()
    assert '|| "UBA"' not in app_js
    assert "payDetails(" in app_js, "checkout must read details via payDetails()"
    assert "payConfigured(" in app_js
    # an unconfigured method must render the notice, not a fabricated account
    assert "ck.payNotConfigured" in app_js


def test_checkout_html_placeholders_are_empty_and_server_driven():
    html = open(os.path.join(ROOT, "checkout.html"), encoding="utf-8").read()
    # every element app.js paints must exist, and start out empty
    for attr in ("data-ngn-name", "data-ngn-bank", "data-ngn-acc",
                 "data-cfa-name", "data-cfa-provider", "data-cfa-acc",
                 "data-togo-provider", "data-togo-acc", "data-togo-name",
                 "data-ngn-notice", "data-cfa-notice"):
        assert attr in html, f"checkout.html is missing {attr}"
    for attr in ("data-ngn-acc", "data-cfa-acc", "data-togo-acc",
                 "data-ngn-name", "data-cfa-name"):
        m = re.search(re.escape(attr) + r"[^>]*>([^<]*)<", html)
        assert m, f"{attr} not found"
        assert m.group(1).strip() == "", f"{attr} ships with content: {m.group(1)!r}"
    # account rows start hidden; app.js reveals them only with a real value
    assert 'data-ngn-row-acc hidden' in html
    assert 'data-cfa-row hidden' in html
    assert 'data-togo-row hidden' in html


def test_store_js_settings_have_no_payment_defaults():
    body = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    for key in ("bank_name", "account_number", "account_name"):
        m = re.search(re.escape(key) + r':\s*"([^"]*)"', body)
        assert m, f"DEFAULT_SETTINGS is missing {key}"
        assert m.group(1) == "", f"DEFAULT_SETTINGS hardcodes {key}={m.group(1)!r}"


# --------------------------------------------------------- the server owns it
def test_site_keys_expose_every_payment_column():
    for col in PAYMENT_COLUMNS:
        assert col in apimod.SITE_KEYS, f"{col} is not served by /api/site"
        assert col in supabase_settings.DEFAULT_SETTINGS, \
            f"{col} is missing from site_settings defaults"


def test_schema_defines_the_payment_columns():
    sql = open(os.path.join(ROOT, "supabase_schema.sql"), encoding="utf-8").read()
    for col in PAYMENT_COLUMNS:
        assert re.search(r"add column if not exists\s+" + col + r"\s+text",
                         sql), f"supabase_schema.sql does not add {col}"


def test_admin_saves_payment_details_and_reads_them_back(client):
    tok = _login(client)
    payload = {
        "naira_payment_bank": "Test Bank",
        "naira_payment_name": "A B C",
        "naira_payment_account": "0123456789",
        "naira_payment_instructions": "Quote your order ID",
        "cfa_payment_provider": "MTN MoMo Benin",
        "cfa_payment_name": "CFA Holder",
        "cfa_payment_account": "9700000000",
        "cfa_payment_instructions": "Send the screenshot",
        "togo_payment_provider": "Moov Money Togo",
        "togo_payment_name": "Togo Holder",
        "togo_payment_account": "9000000000",
        "togo_payment_instructions": "Togo note",
    }
    r = client.post("/api/admin/site", headers={"X-CSRF-Token": tok}, json=payload)
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    saved = body["site"]
    for key, want in payload.items():
        assert saved[key] == want, f"{key} was not returned by the server"

    # and the public endpoint serves the same values
    r = client.get("/api/site")
    assert r.status_code == 200
    site = r.get_json()["site"]
    for key, want in payload.items():
        assert site[key] == want


def test_payment_values_are_sanitised_not_stored_verbatim(client):
    """sec.clean strips markup and keeps the text - so the invariant is that
    no tag survives. The residue is harmless because the storefront paints
    these values with textContent and the Admin form with JA.escape (checked
    below), never as innerHTML.
    """
    tok = _login(client)
    r = client.post("/api/admin/site", headers={"X-CSRF-Token": tok}, json={
        "naira_payment_name": "<script>alert(1)</script>Holder",
        "cfa_payment_instructions": "<b>bold</b> send it",
    })
    assert r.status_code == 200, r.data
    site = r.get_json()["site"]
    for key in ("naira_payment_name", "cfa_payment_instructions"):
        assert "<" not in site[key] and ">" not in site[key], \
            f"{key} still contains markup: {site[key]!r}"
    assert "Holder" in site["naira_payment_name"]
    assert "send it" in site["cfa_payment_instructions"]


def test_the_storefront_paints_payment_values_as_text_not_html():
    """Why stripped-text residue is safe: no innerHTML sink for these fields."""
    app_js = open(os.path.join(ROOT, "js", "app.js"), encoding="utf-8").read()
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    # app.js writes them through the textContent helper only
    for sel in ("[data-ngn-name]", "[data-ngn-bank]", "[data-ngn-acc]",
                "[data-cfa-acc]", "[data-togo-acc]"):
        assert f'"{sel}"' in app_js
    m = re.search(r"const setText = \(sel, val\) => \{(.*?)\};", app_js, re.S)
    assert m and "textContent = val" in m.group(1), \
        "setText must assign textContent, not innerHTML"
    # the Admin form escapes every payment input value
    for col in PAYMENT_COLUMNS:
        assert re.search(r'name="' + col + r'"[^>]*JA\.escape', admin_js), \
            f"admin form does not escape {col}"


def test_writing_payment_details_requires_admin_and_csrf(client):
    r = client.post("/api/admin/site", json={"naira_payment_bank": "Nope"})
    assert r.status_code in (401, 403), r.status_code
    tok = _login(client)
    r = client.post("/api/admin/site", headers={"X-CSRF-Token": "wrong-token"},
                    json={"naira_payment_bank": "Nope"})
    assert r.status_code in (400, 403), r.status_code


# The exact canonical field names the Admin Settings form must submit.
CANONICAL_SETTINGS_FIELDS = (
    "bank_name", "account_number", "account_name",
    "cfa_payment_provider", "cfa_payment_name", "cfa_payment_account",
    "cfa_payment_instructions",
    "togo_payment_provider", "togo_payment_name", "togo_payment_account",
    "togo_payment_instructions",
    "naira_payment_bank", "naira_payment_name", "naira_payment_account",
    "naira_payment_instructions",
    "shipping_note", "contact_email", "contact_phone",
    "referral_commission_percentage", "hero_banner_title",
    "hero_banner_subtitle", "site_logo_url",
)


def test_admin_form_submits_every_canonical_settings_field():
    """Parses the real submit handler, so a dropped field fails here."""
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    m = re.search(r"const payload = \{(.*?)\n    \};", admin_js, re.S)
    assert m, "could not find the settings payload in js/admin.js"
    block = m.group(1)
    sent = set(re.findall(r"^\s*([a-z_]+):", block, re.M))
    sent |= set(PAYMENT_COLUMNS)          # added by PAYMENT_FIELDS.forEach
    missing = [f for f in CANONICAL_SETTINGS_FIELDS if f not in sent]
    assert not missing, f"the Admin form does not send: {missing}"


def test_every_canonical_field_has_a_form_input_and_is_repainted():
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    no_input = [f for f in CANONICAL_SETTINGS_FIELDS
                if f'name="{f}"' not in admin_js]
    assert not no_input, f"no form input named: {no_input}"
    # fillSiteForm must paint each one back from the server response, or the
    # admin sees their own typed value rather than what Supabase stored.
    m = re.search(r"function fillSiteForm\(site\) \{(.*?)\n\}", admin_js, re.S)
    assert m, "fillSiteForm not found"
    body = m.group(1)
    not_repainted = [f for f in CANONICAL_SETTINGS_FIELDS
                     if f not in body and f not in PAYMENT_COLUMNS]
    assert not not_repainted, f"not repainted from the server: {not_repainted}"
    assert "PAYMENT_FIELDS.forEach" in body


def test_server_accepts_and_returns_every_canonical_field(client):
    tok = _login(client)
    payload = {
        "bank_name": "Bank", "account_number": "111", "account_name": "Holder",
        "cfa_payment_provider": "MoMo", "cfa_payment_name": "CFA",
        "cfa_payment_account": "222", "cfa_payment_instructions": "ci",
        "togo_payment_provider": "Moov", "togo_payment_name": "TG",
        "togo_payment_account": "333", "togo_payment_instructions": "ti",
        "naira_payment_bank": "NBank", "naira_payment_name": "NN",
        "naira_payment_account": "444", "naira_payment_instructions": "ni",
        "shipping_note": "Cotonou 1000-3000 CFA",
        "contact_email": "shop@example.com", "contact_phone": "+22900000000",
        "referral_commission_percentage": "7.5",
        "hero_banner_title": "Title", "hero_banner_subtitle": "Subtitle",
        "site_logo_url": "https://cdn.example.com/logo.png",
    }
    r = client.post("/api/admin/site", headers={"X-CSRF-Token": tok}, json=payload)
    assert r.status_code == 200, r.data
    saved = r.get_json()["site"]
    for key, want in payload.items():
        got = str(saved.get(key))
        assert got == want, f"{key}: server returned {got!r}, expected {want!r}"
    served = client.get("/api/site").get_json()["site"]
    for key in CANONICAL_SETTINGS_FIELDS:
        assert key in served, f"GET /api/site does not serve {key}"


def test_save_success_is_only_reported_after_the_server_confirms():
    """The form must not toast success off localStorage or a local echo."""
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    m = re.search(r"#set-form\"\)\?\.addEventListener\(\"submit\"(.*?)\n  \}\);",
                  admin_js, re.S)
    assert m, "settings submit handler not found"
    body = m.group(1)
    assert "await saveSiteConfig(payload)" in body
    # failure path surfaces the server's own error text
    assert "saved.ok === false" in body
    assert "(saved && saved.error)" in body
    # success path repaints from the response, not from the form
    assert "fillSiteForm(site)" in body
    assert "localStorage" not in body, "settings must not rely on localStorage"
    # the save button is disabled while in flight (no double submit)
    assert "btn.disabled = true" in body and "btn.disabled = false" in body


def test_an_empty_payment_row_is_saved_empty_not_defaulted(client):
    """Clearing a field must persist as empty: the storefront then hides the
    row instead of falling back to a stale account number."""
    tok = _login(client)
    client.post("/api/admin/site", headers={"X-CSRF-Token": tok},
                json={"naira_payment_account": "0123456789"})
    r = client.post("/api/admin/site", headers={"X-CSRF-Token": tok},
                    json={"naira_payment_account": ""})
    assert r.status_code == 200
    assert r.get_json()["site"]["naira_payment_account"] == ""
    assert client.get("/api/site").get_json()["site"]["naira_payment_account"] == ""
