"""Site settings, Supabase-backed (production path).

Supabase PostgreSQL is the production source of truth for the site_settings
row (bank details, referral commission, hero/contact/logo). These tests run
the REAL supabase_settings module against an in-memory fake client - no
mocking of the logic under test - and cover:

* get/update site_settings: canonical fields persist in the id=1 row and the
  saved row is returned to the caller,
* referral_commission_percentage is validated/clamped 0-100 (bad input is
  never written),
* /api/admin/site in the production path posts the exact canonical keys and
  /api/site serves the live row (with legacy aliases for older pages),
* the referral payout reads ONLY site_settings.referral_commission_percentage
  at reward time (a stale process-local 10% never wins).

Run with:  python3 -m pytest tests -q
No real Supabase needed: supabase_settings.client/enabled are monkeypatched.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

from config import Config  # noqa: E402
import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
import growth  # noqa: E402
import supabase_settings  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402  - one strong password per run; ADMIN_PW pins it

EMAIL = "jaurastore@gmail.com"

FAKE_ORIGIN = "https://fake.supabase.co"


class FakeTable:
    """In-memory PostgREST stand-in with the ops supabase_settings uses."""

    def __init__(self, owner, name):
        self._owner = owner
        self._name = name
        self._filters = []
        self._order = None
        self._limit = None
        self._pending_update = None
        self._pending_insert = None
        self._pending_delete = False

    # ------------------------------------------------------- query chain
    def select(self, *a, **k):
        return self

    def eq(self, key, val):
        self._filters.append((key, "==", val))
        return self

    def is_(self, key, val):
        self._filters.append((key, "is", str(val).lower() == "null"))
        return self

    def order(self, key, desc=False, **k):
        self._order = (key, bool(desc))
        return self

    def limit(self, n=None):
        self._limit = int(n) if n is not None else None
        return self

    # -------------------------------------------------------- mutations
    def update(self, row):
        self._pending_update = dict(row or {})
        return self

    def insert(self, row):
        self._pending_insert = dict(row or {})
        return self

    def delete(self):
        self._pending_delete = True
        return self

    # --------------------------------------------------------- execute
    def _rows(self):
        store = self._owner.tables.setdefault(self._name, [])
        out = [dict(r) for r in store]
        for key, op, val in self._filters:
            if op == "==":
                out = [r for r in out if r.get(key) == val]
            elif op == "is":
                out = [r for r in out if (r.get(key) is None) is val]
        if self._order:
            key, desc = self._order
            out.sort(key=lambda r: str(r.get(key) or ""), reverse=desc)
        if self._limit is not None:
            out = out[: self._limit]
        return out

    def execute(self):
        """Return a response object with a .data attribute (real SDK shape)."""
        store = self._owner.tables.setdefault(self._name, [])
        if self._pending_delete:
            keep = []
            for r in store:
                hit = False
                for key, op, val in self._filters:
                    if op == "==" and r.get(key) == val:
                        hit = True
                    elif op == "is" and (r.get(key) is None) is val:
                        hit = True
                if not hit:
                    keep.append(r)
            store[:] = keep
            return _Response([])
        if self._pending_update is not None:
            updated = []
            for r in store:
                if all(
                    (op == "==" and r.get(k) == v) or
                    (op == "is" and (r.get(k) is None) is (str(v).lower() == "null"))
                    for k, op, v in self._filters
                ):
                    r.update(self._pending_update)
                    updated.append(dict(r))
            return _Response(updated)
        if self._pending_insert is not None:
            row = dict(self._pending_insert)
            if "id" in row:
                store[:] = [r for r in store if r.get("id") != row["id"]]
            else:
                # the real table auto-generates id + created_at
                row["id"] = self._owner.next_id()
                row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            store.append(row)
            return _Response([dict(row)])
        return _Response(self._rows())


class _Response:
    """execute() result: the real SDK exposes rows via .data."""

    def __init__(self, data):
        self.data = data or []


class FakeSupabaseClient:
    def __init__(self):
        self.tables = {}
        self._id_seq = 0
        self.tables["site_settings"] = [{
            "id": 1,
            "bank_name": "", "account_number": "", "account_name": "",
            "referral_commission_percentage": 0,
            "hero_banner_title": "", "hero_banner_subtitle": "",
            "contact_email": "", "contact_phone": "", "site_logo_url": "",
            "hero_video_url": "", "hero_poster_url": "", "hero_doc_url": "",
            "shop_banner_url": "", "shipping_note": "", "banner_from": "",
            "banner_to": "", "conv_banner": "", "conv_bold": "",
        }]

    def next_id(self):
        self._id_seq += 1
        return self._id_seq

    def table(self, name):
        return FakeTable(self, name)


@pytest.fixture()
def sb(monkeypatch):
    """A reachable fake Supabase for the REAL supabase_settings module."""
    client = FakeSupabaseClient()
    monkeypatch.setattr(supabase_settings, "client", lambda: client)
    monkeypatch.setattr(supabase_settings, "enabled", lambda: True)
    monkeypatch.setattr(Config, "SUPABASE_URL", FAKE_ORIGIN)
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    return client


@pytest.fixture()
def client(app):
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


# ------------------------------------------------------------------- site
def test_site_settings_write_returns_saved_row(sb):
    saved = supabase_settings.update_site_settings({
        "bank_name": "UBA", "account_number": "23474678931",
        "account_name": "OKORAFOR PRAISE",
        "referral_commission_percentage": "25",
        "hero_banner_title": "Sale", "hero_banner_subtitle": "Up to -30%",
        "contact_email": "shop@jaurastore.com", "contact_phone": "+229 01 68 95 31 10",
        "site_logo_url": "https://cdn.example.com/logo.png",
    })
    assert saved["bank_name"] == "UBA"
    assert saved["account_number"] == "23474678931"
    assert saved["account_name"] == "OKORAFOR PRAISE"
    assert float(saved["referral_commission_percentage"]) == 25.0
    assert saved["contact_email"] == "shop@jaurastore.com"
    assert saved["site_logo_url"] == "https://cdn.example.com/logo.png"
    # the row in Supabase is what the caller got - not a local copy
    row = sb.tables["site_settings"][0]
    assert row["bank_name"] == "UBA" and row["account_number"] == "23474678931"
    assert float(row["referral_commission_percentage"]) == 25.0


def test_site_settings_referral_is_capped_and_bad_input_never_written(sb):
    # over 100 is clamped, not stored raw
    supabase_settings.update_site_settings({"referral_commission_percentage": 450})
    assert float(sb.tables["site_settings"][0]["referral_commission_percentage"]) == 100.0
    supabase_settings.update_site_settings({"referral_commission_percentage": -5})
    assert float(sb.tables["site_settings"][0]["referral_commission_percentage"]) == 0.0
    # fake data never survives: a value the app does not accept cannot land
    sb.tables["site_settings"][0]["referral_commission_percentage"] = 0
    try:
        supabase_settings.update_site_settings(
            {"referral_commission_percentage": "nonsense"})
    except ValueError:
        pass
    else:
        # the module raises the same ValueError the API turns into a 400;
        # either way the row must not contain the junk value
        pass
    assert sb.tables["site_settings"][0]["referral_commission_percentage"] != "nonsense"


def test_get_site_settings_merges_defaults_when_row_is_partial(sb):
    sb.tables["site_settings"][0].pop("conv_bold", None)
    sb.tables["site_settings"][0]["bank_name"] = "Ecobank"
    got = supabase_settings.get_site_settings()
    assert got["bank_name"] == "Ecobank"
    assert "conv_bold" in got                  # defaults fill the gaps
    assert got["referral_commission_percentage"] == 0


def test_admin_site_endpoint_production_path(client, sb, monkeypatch):
    """The admin form's exact keys land in Supabase; /api/site serves them."""
    monkeypatch.setattr(Config, "ENV", "production")
    tok = _login(client)
    r = client.post("/api/admin/site", headers={"X-CSRF-Token": tok}, json={
        "bank_name": "UBA",
        "account_number": "23474678931",
        "account_name": "OKORAFOR PRAISE",
        "referral_commission_percentage": "12",
        "hero_banner_title": "Mid-season",
        "hero_banner_subtitle": "Prizes inside",
        "contact_email": "shop@jaurastore.com",
        "contact_phone": "+229 0168953101",
        "site_logo_url": "https://cdn.example.com/logo.png",
        "shippingNote": "Lagos ₦2000, Cotonou 1000 CFA.",
    })
    assert r.status_code == 200, r.data
    site = r.get_json()["site"]
    assert site["bank_name"] == "UBA"
    assert site["account_number"] == "23474678931"
    assert site["account_name"] == "OKORAFOR PRAISE"
    assert float(site["referral_commission_percentage"]) == 12.0
    assert site["shippingNote"] == "Lagos ₦2000, Cotonou 1000 CFA."
    row = sb.tables["site_settings"][0]
    assert row["bank_name"] == "UBA" and row["account_number"] == "23474678931"
    assert float(row["referral_commission_percentage"]) == 12.0
    # public read serves the live row + the legacy aliases older pages read
    r2 = client.get("/api/site")
    public = r2.get_json()["site"]
    assert public["bank_name"] == "UBA"
    assert public["logoUrl"] == "https://cdn.example.com/logo.png"
    assert public["site_logo_url"] == "https://cdn.example.com/logo.png"
    assert public["shippingNote"] == "Lagos ₦2000, Cotonou 1000 CFA."


def test_admin_site_endpoint_rejects_invalid_referral(client, sb, monkeypatch):
    monkeypatch.setattr(Config, "ENV", "production")
    tok = _login(client)
    r = client.post("/api/admin/site", headers={"X-CSRF-Token": tok}, json={
        "referral_commission_percentage": "abc",
        "bank_name": "SKAM",
    })
    assert r.status_code == 400
    assert "referral" in (r.get_json().get("error") or "").lower()
    # nothing was written
    assert sb.tables["site_settings"][0]["bank_name"] == ""



# ------------------------------------------------- referral payout from DB
def test_referral_payout_reads_site_settings_percentage(sb, monkeypatch):
    """The reward coupon % is exactly what site_settings says at reward time.

    Previously the payout could come from a process-local default (10%); the
    production rule is that an admin change applies immediately, so a code
    that reaches its milestone AFTER the owner set 6% must mint a 6% coupon.
    """
    init_db()
    execute("DELETE FROM referral_codes")
    execute("DELETE FROM coupons")
    execute("DELETE FROM growth_settings")
    execute("INSERT INTO referral_codes (code, email, uses, reward_issued) "
            "VALUES (?,?,?,0)", ("JA-OWN1", "owner@x.com", growth.DEFAULTS["milestone"] - 1))
    sb.tables["site_settings"][0]["referral_commission_percentage"] = 6.0
    sb.tables["growth_settings"] = [{"key": "referralEnabled", "value": "1"}]
    monkeypatch.setitem(growth.__dict__, "_mirror_coupon", lambda *a, **k: None)
    report = growth.record_code_use("JA-OWN1", "buyer@x.com", "JA-1")
    assert report["counted"] and report["rewardIssued"]
    coupon = one("SELECT * FROM coupons WHERE kind='reward'")
    assert coupon and int(coupon["percent"]) == 6


def test_referral_payout_skips_when_site_settings_unreachable(sb, monkeypatch):
    """In production a Supabase outage skips the reward (never a stale %)."""
    monkeypatch.setattr(Config, "ENV", "production")
    init_db()
    execute("DELETE FROM referral_codes")
    execute("DELETE FROM coupons")
    execute("INSERT INTO referral_codes (code, email, uses, reward_issued) "
            "VALUES (?,?,?,0)", ("JA-OWN2", "owner@x.com", growth.DEFAULTS["milestone"] - 1))
    execute("INSERT INTO growth_settings (key, value) VALUES ('referralEnabled','1')")

    def _boom():
        raise RuntimeError("supabase down")

    monkeypatch.setattr(supabase_settings, "get_site_settings", _boom)
    monkeypatch.setitem(growth.__dict__, "_mirror_coupon", lambda *a, **k: None)
    report = growth.record_code_use("JA-OWN2", "buyer@x.com", "JA-2")
    assert report["counted"] and not report["rewardIssued"]
    assert one("SELECT * FROM coupons WHERE kind='reward'") is None
    execute("DELETE FROM referral_codes")
    execute("DELETE FROM coupons")


# ------------------------------------------------ product delete (strict)
def test_admin_product_delete_503_when_supabase_delete_fails(client, monkeypatch):
    """A failed Supabase tombstone must never be reported as a successful
    delete: the portal answers 503 and the product stays live."""
    monkeypatch.setattr(Config, "ENV", "production")
    monkeypatch.setattr(Config, "SUPABASE_URL", FAKE_ORIGIN)
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    import supabase_store
    monkeypatch.setattr(supabase_store, "delete_products_strict", lambda ids: False)
    tok = _login(client)
    r = client.delete("/api/admin/products/jau-nope",
                      headers={"X-CSRF-Token": tok})
    assert r.status_code == 503
    body = r.get_json()
    assert body["ok"] is False and "Supabase" in body["error"]


def test_admin_product_delete_ok_when_supabase_confirms(client, monkeypatch):
    monkeypatch.setattr(Config, "ENV", "production")
    monkeypatch.setattr(Config, "SUPABASE_URL", FAKE_ORIGIN)
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    import supabase_store
    monkeypatch.setattr(supabase_store, "delete_products_strict", lambda ids: True)
    # The durable tombstone write is now honest (not swallowed): the delete
    # only reports success when BOTH Supabase writes landed.
    monkeypatch.setattr(supabase_store, "add_deleted_id", lambda pid: True)
    tok = _login(client)
    r = client.delete("/api/admin/products/jau-001",
                      headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def _login(client):
    """Sign in as the test admin (rate-limit rows cleared first)."""
    execute("DELETE FROM rate_limits")
    r = client.post("/api/admin/login", json={"email": EMAIL,
                                              "password": PW,
                                              "recaptcha": ""})
    assert r.status_code == 200, r.data
    return r.get_json().get("csrf") or r.get_json().get("token") or ""
