"""Owner request 2026-09-28: admin-editable social media links with automatic
icon rendering, Facebook included.

What is pinned here:

* The Admin portal carries four clean inputs - WhatsApp, Instagram, TikTok
  and Facebook - inside the settings panel, wired to the same
  only-what-changed site patch every other settings form uses.
* POST /api/admin/site stores the four links in the Supabase
  ``site_settings`` row (social_whatsapp_url, social_instagram_url,
  social_tiktok_url, social_facebook_url) and GET /api/site serves them;
  dangerous schemes never survive the write.
* The storefront picks the logo from the LINK, not from the box it was
  typed into, and the Facebook logo ships alongside Instagram, TikTok and
  WhatsApp. The browser half runs in tests/_social_links_sim.mjs against
  the real js/store.js.
* The SQL to add the four columns exists, is add-only and idempotent.

Run with:  python3 -m pytest tests/test_social_links.py -q
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("SITE_CONFIG_PATH", "/tmp/jaura_test_site_social.json")

import api  # noqa: E402
import app as appmod  # noqa: E402
import supabase_settings  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = "jaurastore@gmail.com"
SIM = os.path.join(ROOT, "tests", "_social_links_sim.mjs")
ADMIN_SIM = os.path.join(ROOT, "tests", "_admin_social_preview_sim.mjs")
SOCIAL_COLUMNS = ("social_whatsapp_url", "social_instagram_url",
                  "social_tiktok_url", "social_facebook_url")


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app, monkeypatch, tmp_path):
    init_db()
    execute("DELETE FROM rate_limits")
    monkeypatch.setenv("SITE_CONFIG_PATH", str(tmp_path / "site.json"))
    with app.test_client() as c:
        yield c


def csrf(client):
    execute("DELETE FROM rate_limits")
    return client.get("/api/config").get_json()["csrf"]


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def save_site(client, patch):
    r = client.post("/api/admin/site", headers={"X-CSRF-Token": login(client)},
                    json=patch)
    assert r.status_code == 200, r.data
    return r.get_json()["site"]


# ------------------------------------------------------------------ server
def test_the_four_links_are_writable_columns():
    for column in SOCIAL_COLUMNS:
        assert column in api.SITE_KEYS, f"{column} must be admin-writable"
        assert column in api.SITE_WRITABLE_COLUMNS
        assert column in supabase_settings.DEFAULT_SETTINGS, (
            f"{column} must default to '' on a row that predates it")
        assert supabase_settings.DEFAULT_SETTINGS[column] == ""


def test_admin_saves_all_four_links_and_the_site_row_serves_them(client):
    saved = save_site(client, {
        "social_whatsapp_url": "https://wa.me/2349161670236",
        "social_instagram_url": "https://www.instagram.com/j_aura_store",
        "social_tiktok_url": "https://www.tiktok.com/@j_aura_store",
        "social_facebook_url": "https://www.facebook.com/jaurastore",
    })
    for column in SOCIAL_COLUMNS:
        assert saved[column], f"{column} was dropped by the save"
    site = client.get("/api/site").get_json()["site"]
    assert site["social_facebook_url"] == "https://www.facebook.com/jaurastore"
    assert site["social_instagram_url"] == "https://www.instagram.com/j_aura_store"
    assert site["social_tiktok_url"] == "https://www.tiktok.com/@j_aura_store"
    assert site["social_whatsapp_url"] == "https://wa.me/2349161670236"


def test_a_saved_link_can_be_cleared_but_never_blanked_by_accident(client):
    save_site(client, {"social_facebook_url": "https://www.facebook.com/jaurastore"})
    # A form that never loaded the row posts nothing for this field: the
    # stored link must survive (the shared _clear contract).
    save_site(client, {"social_instagram_url": "https://www.instagram.com/j_aura_store"})
    site = client.get("/api/site").get_json()["site"]
    assert site["social_facebook_url"] == "https://www.facebook.com/jaurastore"
    # Clearing is explicit and does work.
    save_site(client, {"social_facebook_url": "", "_clear": ["social_facebook_url"]})
    site = client.get("/api/site").get_json()["site"]
    assert site["social_facebook_url"] == ""


def test_dangerous_social_addresses_are_refused(client):
    save_site(client, {"social_facebook_url": "javascript:alert(1)"})
    site = client.get("/api/site").get_json()["site"]
    assert site["social_facebook_url"] == "", (
        "a javascript: address must never be stored as a social link")


# ------------------------------------------------------------- admin portal
def test_the_admin_panel_has_one_input_per_platform():
    admin = read("js/admin.js")
    assert "<summary>Social media links</summary>" in admin, (
        "the settings panel must carry a Social media links section")
    for column in SOCIAL_COLUMNS:
        assert f'name="{column}"' in admin, f"the {column} input is missing"
        assert f'data-social-preview="{column}"' in admin, (
            f"the {column} input must show its auto-detected logo")
    assert "function bindSocialLinks()" in admin
    assert "bindSocialLinks();" in admin, "the form is never bound"
    assert "JA.socialNetwork" in admin and "JA.socialIcon" in admin, (
        "the admin preview must auto-detect the platform from the link")
    assert "siteFieldPatch(candidate, loadedRow)" in admin.split(
        "function bindSocialLinks()")[1][:2000], (
        "social links must use the only-what-changed patch like every other "
        "settings form")


# -------------------------------------------------------------- storefront
def test_the_storefront_ships_all_four_logos_including_facebook():
    store = read("js/store.js")
    for network in ("whatsapp", "instagram", "tiktok", "facebook"):
        assert f"{network}: `<svg" in store, f"the {network} logo is missing"
    assert "function socialNetwork(" in store
    assert "function socialIcon(" in store
    assert "function socialLinksHTML(" in store
    assert "function paintSocialLinks(" in store
    assert '<div class="foot-social" data-social-links>' in store, (
        "the footer must render the auto-detected social row")
    assert "paintSocialLinks();" in store.split("function applySiteConfig")[1][:6000], (
        "the live site row must repaint the social icons")


def test_the_social_row_is_styled_for_every_platform():
    css = read("css/style.css")
    assert ".foot-social {" in css
    for network in ("whatsapp", "instagram", "tiktok", "facebook"):
        assert f".social-btn--{network}" in css, (
            f"the {network} button has no brand styling")
    assert ".social-ico" in css, "the admin preview icon is unstyled"


def test_the_browser_behaviour_matches_the_owner_request():
    """The real js/store.js, booted in a stubbed browser: links reach the
    footer, and the logo follows the LINK rather than the input box."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed (CI explicitly installs Node)")
    result = subprocess.run([node, SIM], capture_output=True, text=True,
                            timeout=120, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all social link checks passed" in result.stdout


def test_the_admin_editor_previews_the_detected_logo():
    """The real js/admin.js, booted in a stubbed browser: the four inputs
    render and each preview follows the link that was typed."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed (CI explicitly installs Node)")
    result = subprocess.run([node, ADMIN_SIM], capture_output=True, text=True,
                            timeout=120, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all admin social preview checks passed" in result.stdout


# -------------------------------------------------------------------- SQL
def test_the_migration_is_add_only_and_idempotent():
    for name in ("add_social_link_columns.sql",
                 "schema_sections/20_site_settings_social.sql"):
        sql = read(name).lower()
        for column in SOCIAL_COLUMNS:
            assert f"add column if not exists {column}" in sql, (
                f"{name} does not add {column}")
        for destructive in ("drop ", "truncate ", "delete "):
            assert destructive not in sql, f"{name} must stay add-only"
    schema = read("supabase_schema.sql").lower()
    for column in SOCIAL_COLUMNS:
        assert f"add column if not exists {column}" in schema


def test_a_missing_column_fails_the_save_with_the_repair_statement():
    """Until add_social_link_columns.sql has been run, a social link cannot
    be stored. The save must say so - naming the column and the single ALTER
    that fixes it - instead of answering "saved" and losing the link."""
    import supabase_settings

    for column in SOCIAL_COLUMNS:
        assert column in supabase_settings.CRITICAL_SETTINGS, (
            f"{column} may not be silently dropped from a save")

    class _MissingColumn(Exception):
        pass

    class _Table:
        def update(self, payload):
            self.payload = payload
            return self

        def eq(self, *a, **k):
            return self

        def execute(self):
            raise _MissingColumn(
                "{'message': \"Could not find the 'social_facebook_url' "
                "column of 'site_settings' in the schema cache\"}")

    class _Client:
        def table(self, name):
            return _Table()

    with pytest.raises(RuntimeError) as err:
        supabase_settings._update_site_settings_resilient(
            _Client(), {"social_facebook_url": "https://facebook.com/jaurastore"})
    message = str(err.value)
    assert "social_facebook_url" in message
    assert ("alter table site_settings add column if not exists "
            "social_facebook_url text not null default ''") in message
