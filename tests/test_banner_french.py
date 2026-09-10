"""The owner's moving banner speaks English AND French.

The banner is one string per language on the site_settings id=1 row: the
existing conv_banner (English) plus the new conv_banner_fr column. Before
this fix the storefront rendered convBanner verbatim in BOTH languages, so
switching to French never changed the moving line. Now:

* the Admin form saves "Banner text (French)" (legacy alias convBannerFr ->
  canonical conv_banner_fr, end to end);
* GET /api/site answers both the canonical column and the legacy alias;
* js/store.js convBannerHTML() picks the French text when lang=fr and falls
  back to English when it is unwritten, and the language switch repaints;
* supabase_settings.update_site_settings repairs its payload against the
  LIVE table, so a legacy site_settings row that still lacks the new column
  cannot kill the whole banner save with PGRST204 (the unknown column is
  dropped and logged with the one ALTER statement that adds it).

Run with:  python3 -m pytest tests/test_banner_french.py -q
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

import pytest  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

import app as appmod  # noqa: E402
import supabase_settings  # noqa: E402
from config import Config  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = "jaurastore@gmail.com"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def admin(app):
    """A logged-in admin client with the CSRF token attached."""
    init_db()
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        r = c.post("/api/admin/login", json={"email": EMAIL, "password": PW})
        assert r.status_code == 200, r.data
        tok = r.get_json()["csrf"]

        class _A:
            def post(self, url, **kw):
                kw.setdefault("headers", {})
                kw["headers"].setdefault("X-CSRF-Token", tok)
                return c.post(url, **kw)

            def get(self, url, **kw):
                return c.get(url, **kw)

        yield _A()


def test_banner_french_round_trip(admin):
    """Admin saves the French banner text; /api/site answers it under the
    legacy alias AND the canonical column, so every consumer (admin form,
    storefront) reads it back without a reload."""
    r = admin.post("/api/admin/site", json={
        "convBanner": "Back-to-school sale: 10% off every bag",
        "convBannerFr": "Soldes de rentrée : -10% sur tous les sacs",
        "convBold": "ends Sunday",
    })
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True

    site = admin.get("/api/site").get_json()["site"]
    assert site["convBanner"] == "Back-to-school sale: 10% off every bag"
    assert site["convBannerFr"] == "Soldes de rentrée : -10% sur tous les sacs"
    assert site["conv_banner_fr"] == "Soldes de rentrée : -10% sur tous les sacs"
    assert site["convBold"] == "ends Sunday"


def test_banner_french_marks_an_empty_save(admin):
    """An empty French field is a real value (fall back to English), not a
    stale copy of an earlier save."""
    admin.post("/api/admin/site", json={
        "convBanner": "English only", "convBannerFr": "", "convBold": ""})
    site = admin.get("/api/site").get_json()["site"]
    assert site["convBanner"] == "English only"
    assert site["convBannerFr"] == ""
    assert site["conv_banner_fr"] == ""


# ------------------------------------------------- resilient live-table update
def _api_error(code, message):
    return APIError({"message": message, "code": code,
                     "details": None, "hint": None})


class _LegacySiteTable:
    """site_settings as a LEGACY live table behaves: the new conv_banner_fr
    column does not exist yet, so a .update() carrying it dies with PGRST204
    until the payload drops it."""

    def __init__(self, row, missing=()):
        self._row = row
        self.missing = tuple(missing)
        self.attempts = []
        self._pending = None

    def update(self, payload):
        self._pending = dict(payload or {})
        return self

    def eq(self, _key, _val):
        return self

    def select(self, *_a, **_kw):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self._pending is not None:
            self.attempts.append(dict(self._pending))
            for col in self.missing:
                if col in self._pending:
                    raise _api_error(
                        "PGRST204",
                        f"Could not find the '{col}' column of "
                        f"'site_settings' in the schema cache")
            self._row.update(self._pending)
            self._pending = None
        return type("Res", (), {"data": [dict(self._row)]})()


class _FakeClient:
    def __init__(self, table):
        self._table = table

    def table(self, _name):
        return self._table


@pytest.fixture()
def _blank_site_shape():
    shape = getattr(supabase_settings, "_SITE_SHAPE", None)
    if shape is not None:
        shape["fill"].clear()
        shape["drop"].clear()
        shape.get("values", {}).clear()
    yield
    if shape is not None:
        shape["fill"].clear()
        shape["drop"].clear()
        shape.get("values", {}).clear()


def test_update_survives_a_legacy_table_missing_conv_banner_fr(
        monkeypatch, _blank_site_shape):
    """The WARNED production case: the live site_settings table has no
    conv_banner_fr column yet. The whole banner save must still land (the
    unknown column is dropped, not the save), and the next save must land
    first-try from the cached shape."""
    row = {"id": 1, "conv_banner": "", "conv_banner_fr": "", "conv_bold": ""}
    table = _LegacySiteTable(row, missing=("conv_banner_fr",))
    client = _FakeClient(table)
    monkeypatch.setattr(supabase_settings, "client", lambda: client)
    monkeypatch.setattr(supabase_settings, "enabled", lambda: True)
    monkeypatch.setattr(Config, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-key")

    saved = supabase_settings.update_site_settings({
        "conv_banner": "English sale", "conv_banner_fr": "Soldes FR",
        "conv_bold": "now"})
    assert saved["conv_banner"] == "English sale"
    assert saved["conv_bold"] == "now"
    # the live row got everything it CAN hold
    assert row["conv_banner"] == "English sale" and row["conv_bold"] == "now"
    assert len(table.attempts) >= 2, "the PGRST204 must have been repaired"
    assert "conv_banner_fr" not in table.attempts[-1]
    assert "conv_banner_fr" in supabase_settings._SITE_SHAPE["drop"]

    first_attempts = len(table.attempts)
    supabase_settings.update_site_settings({"conv_banner": "English sale 2"})
    assert len(table.attempts) == first_attempts + 1, \
        "the next save must land on the first attempt"


# ------------------------------------------------------- front-end selection
def test_store_js_conv_banner_picks_french_when_lang_is_fr():
    """convBannerHTML() answers the FRENCH line for a French shopper and
    falls back to English when the French field is unwritten."""
    src = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    m = re.search(r"function convBannerHTML\(\) \{(.*?)\n  \}", src, re.S)
    assert m, "could not find convBannerHTML in js/store.js"
    body = m.group(1)
    assert "_bannerText.convFr" in body and "lang === \"fr\"" in body, \
        "the French text must win when lang=fr"
    assert "_bannerText.conv" in body, "English must remain the fallback"

    cfg = re.search(r"function applySiteConfig\(site\) \{(.*?)\n    paintConvBanner\(\);",
                    src, re.S)
    assert cfg, "could not find applySiteConfig in js/store.js"
    assert '"convBannerFr" in site' in cfg.group(1), \
        "applySiteConfig must read the site row's French banner field"


def test_admin_js_form_and_save_carry_the_french_banner_field():
    """The Admin form has a "Banner text (French)" input and the submit
    posts it as convBannerFr."""
    src = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert re.search(r'name="convBannerFr"', src), \
        "the admin banner form must have a convBannerFr input"
    assert re.search(r'Banner text \(French\)', src), \
        "the admin form must label the French field"
    m = re.search(r"saveSiteConfig\(\{ convBanner: conv, (.*?) \}\)", src, re.S)
    assert m, "could not find the banner save call in js/admin.js"
    assert "convBannerFr" in m.group(1), "the save must post convBannerFr"


def test_language_switch_repaints_the_banner():
    """Tapping EN/FR must repaint the moving banner immediately (no
    reload): the data-lang click handler calls paintConvBanner right after
    setLang."""
    src = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    handler = re.search(
        r'setLang\(next\);(.*?)toast\(next === "fr"', src, re.S)
    assert handler, "could not find the data-lang click handler body"
    body = handler.group(1)
    assert re.search(r"paintConvBanner\(\)", body), \
        "the handler must repaint the banner when the language changes"
