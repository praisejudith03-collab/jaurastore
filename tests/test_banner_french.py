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
    """Emptying the French field is a real value (fall back to English), not a
    stale copy of an earlier save - and it is sent as an explicit clear, so a
    form that has not loaded the row can never blank the text by itself."""
    admin.post("/api/admin/site", json={
        "convBanner": "English only", "_clear": ["convBannerFr", "convBold"]})
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
    """The banner line is chosen from the ACTIVE language.

    bannerLineFor() is the single selector convBannerHTML() paints from:
    French text when FR is active, the English text as the fallback when the
    owner has not written a French line (never a blank bar), English when EN
    is active. The behaviour itself is exercised end to end by
    tests/_store_sim.mjs; this locks the wiring in place."""
    src = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    m = re.search(r"function bannerLineFor\(lang\) \{(.*?)\n  \}", src, re.S)
    assert m, "could not find bannerLineFor in js/store.js"
    body = m.group(1)
    assert "_bannerText.convFr" in body, "the French text must win when lang=fr"
    assert "_bannerText.conv" in body, "English must remain the fallback"
    assert 'indexOf("fr") === 0' in body, \
        "the active language decides which line is painted"

    html = re.search(r"function convBannerHTML\(\) \{(.*?)\n  \}", src, re.S)
    assert html, "could not find convBannerHTML in js/store.js"
    assert "bannerLineFor(lang)" in html.group(1), \
        "convBannerHTML must paint the line for the active language"
    # The retired delivery-window inputs must not come back as a fallback
    # that overrides the owner's custom banner.
    assert "_bannerDates" not in src, \
        "the delivery-window date fallback must be gone from the storefront"
    assert "bannerFrom" not in src and "bannerTo" not in src, \
        "the storefront must not read the retired banner_from/banner_to row"

    cfg = re.search(r"function applySiteConfig\(site\) \{(.*?)\n    paintConvBanner\(\);",
                    src, re.S)
    assert cfg, "could not find applySiteConfig in js/store.js"
    assert '"convBannerFr" in site' in cfg.group(1), \
        "applySiteConfig must read the site row's French banner field"
    assert '"conv_banner_fr" in site' in cfg.group(1), \
        "the canonical Supabase column must be accepted too"


def test_admin_js_form_and_save_carry_the_french_banner_field():
    """The Admin form has a "Banner text (French)" input and the submit
    posts it as convBannerFr."""
    src = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert re.search(r'name="convBannerFr"', src), \
        "the admin banner form must have a convBannerFr input"
    assert re.search(r'Banner text \(French', src), \
        "the admin form must label the French field"
    # The banner save goes through siteFieldPatch (only changed fields, with
    # an explicit _clear for an emptied line) rather than posting the raw form.
    m = re.search(r"siteFieldPatch\(\s*\{ convBanner: conv, (.*?)\}, bannerLoaded\)",
                  src, re.S)
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


# ------------------------------------------------- canonical-name persistence
def test_banner_saves_under_the_canonical_supabase_column_names(admin):
    """A save posting the REAL column names must persist.

    This is the "Moving Banner Text does not save" bug: /api/admin/site only
    collected SITE_KEYS plus the legacy aliases, so a client posting
    {"conv_banner": "..."} had every banner field silently dropped. The
    request still answered 200 with the OLD row, so the Admin form repainted
    the previous text and the storefront never changed - a save that looked
    like it worked and stored nothing.
    """
    r = admin.post("/api/admin/site", json={
        "conv_banner": "Canonical English line",
        "conv_banner_fr": "Ligne canonique en français",
        "conv_bold": "today only",
    })
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True

    site = admin.get("/api/site").get_json()["site"]
    # stored under the canonical column AND served under the legacy alias
    assert site["conv_banner"] == "Canonical English line"
    assert site["convBanner"] == "Canonical English line"
    assert site["conv_banner_fr"] == "Ligne canonique en français"
    assert site["convBannerFr"] == "Ligne canonique en français"
    assert site["conv_bold"] == "today only"
    assert site["convBold"] == "today only"


def test_a_banner_save_is_never_served_from_a_cache(admin):
    """GET /api/site must be uncacheable, or an edit "does not show up"."""
    r = admin.get("/api/site")
    assert r.status_code == 200
    cache = r.headers.get("Cache-Control", "")
    assert "no-store" in cache, f"/api/site is cacheable: {cache!r}"
    # ... and the storefront asks for it with cache: "no-store" too.
    src = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    m = re.search(r"function loadSiteRow\(\) \{(.*?)\n  \}", src, re.S)
    assert m, "could not find loadSiteRow in js/store.js"
    assert 'cache: "no-store"' in m.group(1)
    # The service worker must not stand in front of it either: only
    # /api/catalog is cached.
    sw = open(os.path.join(ROOT, "sw.js"), encoding="utf-8").read()
    api_branch = re.search(r'indexOf\("/api/"\) === 0\) \{(.*?)\n  \}', sw, re.S)
    assert api_branch, "could not find the /api/ branch in sw.js"
    assert "/api/catalog" in api_branch.group(1)
    assert "/api/site" not in api_branch.group(1)


# --------------------------------------------- the retired delivery window
def test_the_delivery_window_date_pickers_are_gone(admin):
    """banner_from / banner_to are no longer writable or rendered.

    The auto-generated "order between {from} and {to}" line competed with
    the owner's own banner text and went stale every batch. The inputs are
    removed from the Admin UI, the API refuses to write the columns, and the
    storefront has no date fallback left.
    """
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    for name in ("bannerFrom", "bannerTo"):
        assert f'name="{name}"' not in admin_js, \
            f"the admin panel still has a {name} input"
    assert "Delivery window" not in admin_js

    import api as apimod
    assert "bannerFrom" not in apimod.SITE_LEGACY_MAP
    assert "bannerTo" not in apimod.SITE_LEGACY_MAP
    assert "banner_from" not in apimod.SITE_WRITABLE_COLUMNS
    assert "banner_to" not in apimod.SITE_WRITABLE_COLUMNS

    # A client still posting the retired fields cannot resurrect them, and
    # the banner it was fighting with is untouched.
    admin.post("/api/admin/site", json={"convBanner": "Owner's own line"})
    r = admin.post("/api/admin/site", json={
        "bannerFrom": "2026-10-01", "bannerTo": "2026-10-12",
        "banner_from": "2026-10-01", "banner_to": "2026-10-12",
    })
    assert r.status_code == 200, r.data
    site = r.get_json()["site"]
    assert not site.get("bannerFrom") and not site.get("banner_from")
    assert site["convBanner"] == "Owner's own line"


def test_the_default_banner_carries_no_dates():
    """With no custom text the default line is fixed and translated - it can
    never promise a delivery window that has already passed."""
    for path in ("js/store.js", "js/i18n.js"):
        src = open(os.path.join(ROOT, path), encoding="utf-8").read()
        for m in re.finditer(r'"conv\.banner": "(.*?)",\n', src):
            line = m.group(1)
            assert "{from}" not in line and "{to}" not in line, \
                f"{path} still interpolates a delivery window: {line!r}"
    src = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    assert "formatBannerDay" not in src, \
        "the delivery-window date formatter must be gone"


def test_a_dropped_column_is_retried_once_the_table_is_repaired(
        monkeypatch, _blank_site_shape):
    """The "column is missing" cache must EXPIRE.

    It used to last for the life of the worker: after one PGRST204 the owner
    would run the ALTER the log told them to run, and every later save would
    still strip conv_banner_fr - the French banner silently never persisting
    - until the next deploy. The drop is now retried, so the save starts
    working by itself once the table is repaired.
    """
    row = {"id": 1, "conv_banner": "", "conv_banner_fr": "", "conv_bold": ""}
    table = _LegacySiteTable(row, missing=("conv_banner_fr",))
    client = _FakeClient(table)
    monkeypatch.setattr(supabase_settings, "client", lambda: client)
    monkeypatch.setattr(supabase_settings, "enabled", lambda: True)
    monkeypatch.setattr(Config, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-key")

    supabase_settings.update_site_settings({"conv_banner_fr": "Soldes FR"})
    assert row["conv_banner_fr"] == ""          # dropped: the table lacks it
    assert supabase_settings._drop_active("conv_banner_fr")

    # The owner runs the ALTER the log printed.
    table.missing = ()
    # Before the retry window elapses the column is still skipped...
    supabase_settings.update_site_settings({"conv_banner_fr": "Soldes FR"})
    assert row["conv_banner_fr"] == ""
    # ... and once it does, the save lands without a restart.
    monkeypatch.setattr(
        supabase_settings.time, "monotonic",
        lambda: 10 ** 6 + supabase_settings.DROP_RETRY_SECONDS)
    saved = supabase_settings.update_site_settings({"conv_banner_fr": "Soldes FR"})
    assert row["conv_banner_fr"] == "Soldes FR"
    assert saved["conv_banner_fr"] == "Soldes FR"
