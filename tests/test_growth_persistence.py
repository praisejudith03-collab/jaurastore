"""Persistence tests for the growth module (referral, coupons, reviews,
stock-panel settings).

The growth data keeps its working copy in SQLite on the Render disk, which a
redeploy wipes. Every write is mirrored into Supabase; these tests cover the
other half - the boot restore in create_app() that writes the mirrored rows
back - plus the product-review mirror (reviews have no dedicated Supabase
table and ride in growth_settings as a JSON row, like the categories).

Run with:  python3 -m pytest tests -q
No real Supabase needed: supabase_store.client() is monkeypatched to an
in-memory fake.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

from config import Config  # noqa: E402
import app as appmod  # noqa: E402
import growth  # noqa: E402
import supabase_store  # noqa: E402
from db import execute, init_db, one, query, restore_growth_settings  # noqa: E402

FAKE_ORIGIN = "https://fake.supabase.co"

# Same in-memory fakes as tests/test_storage_supabase.py
class FakeTable:
    def __init__(self, owner, name):
        self._owner = owner
        self._name = name
        self._filter = None

    def upsert(self, rows, on_conflict=None):
        """Upsert, honouring on_conflict the way PostgREST does.

        Without this the fake de-duplicated only on `id`, so coupon_uses and
        product_reviews - which conflict on (code, order_id) and
        (product_id, email) - appended a duplicate row on every retry and the
        idempotency being tested could never be observed.
        """
        store = self._owner.tables.setdefault(self._name, [])
        rows = rows if isinstance(rows, list) else [rows]
        for r in rows:
            if on_conflict:
                cols = [c.strip() for c in on_conflict.split(",")]
                key = tuple(str(r.get(c)) for c in cols)
                store[:] = [x for x in store
                            if tuple(str(x.get(c)) for c in cols) != key]
            elif self._name == "growth_settings":
                store[:] = [x for x in store if x.get("key") != r.get("key")]
            else:
                store[:] = [x for x in store if x.get("id") != r.get("id")]
            store.append(dict(r))
        return self

    def select(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n=None):
        return self

    def eq(self, key, val):
        self._filter = (key, val)
        return self

    def execute(self):
        rows = list(self._owner.tables.get(self._name, []))
        if self._filter:
            k, v = self._filter
            rows = [r for r in rows if r.get(k) == v]
        return {"data": rows}


class FakeSupabaseClient:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return FakeTable(self, name)


@pytest.fixture()
def sb(monkeypatch):
    """Supabase configured + reachable, with a fresh in-memory database."""
    client = FakeSupabaseClient()
    monkeypatch.setattr(Config, "SUPABASE_URL", FAKE_ORIGIN)
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    monkeypatch.setattr(supabase_store, "client", lambda: client)
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


def _wipe_growth_tables():
    """Simulate the wiped Render disk: growth data gone from SQLite."""
    execute("DELETE FROM growth_settings")
    execute("DELETE FROM coupons")
    execute("DELETE FROM referral_codes")
    execute("DELETE FROM product_reviews")
    execute("DELETE FROM variant_stock")


def _growth_value(sb, key):
    for row in sb.tables.get("growth_settings", []):
        if row.get("key") == key:
            return row.get("value")
    return None


# ------------------------------------------------------------- boot restore
def test_owner_referral_settings_survive_a_wiped_disk(sb):
    """The exact scenario: the owner set their referral percentage and
    thresholds in the admin panel, a deploy wiped the disk, the shop boots."""
    sb.tables["growth_settings"] = [
        {"key": "referralEnabled", "value": "1"},
        {"key": "minSpendNgn", "value": "25000"},
        {"key": "buyerPercent", "value": "7"},
        {"key": "referrerPercent", "value": "10"},
        {"key": "milestone", "value": "2"},
        {"key": "abandonedHours", "value": "3"},
    ]
    _wipe_growth_tables()
    assert growth.settings()["minSpendNgn"] == growth.DEFAULTS["minSpendNgn"]  # wiped
    appmod.create_app()
    s = growth.settings()
    assert s["minSpendNgn"] == 25000
    assert s["buyerPercent"] == 7
    assert s["referrerPercent"] == 10
    assert s["milestone"] == 2
    assert s["abandonedHours"] == 3
    assert s["referralEnabled"] == 1


def test_coupons_and_referral_codes_survive_a_wiped_disk(sb):
    sb.tables["coupons"] = [
        {"code": "WELCOME10", "percent": 10, "kind": "manual", "email": None,
         "note": "launch", "active": 1, "max_uses": 5, "uses": 2,
         "expires_at": None, "created_at": "2026-08-01T00:00:00"},
        {"code": "THANKS-AB12", "percent": 10, "kind": "reward", "email": "a@x.com",
         "note": "Automatic referrer reward for JA-XY", "active": 1, "max_uses": 1,
         "uses": 0, "expires_at": None, "created_at": "2026-08-02T00:00:00"},
    ]
    sb.tables["referral_codes"] = [
        {"code": "JA-XYZ12", "email": "owner@x.com", "name": "Owner", "uses": 2,
         "reward_issued": 1, "reward_coupon": "THANKS-AB12",
         "created_at": "2026-08-01T00:00:00"},
    ]
    _wipe_growth_tables()
    appmod.create_app()

    c = one("SELECT * FROM coupons WHERE code='WELCOME10'")
    assert c and c["uses"] == 2 and c["max_uses"] == 5 and c["active"] == 1
    r = one("SELECT * FROM referral_codes WHERE code='JA-XYZ12'")
    assert r and r["uses"] == 2 and r["reward_issued"] == 1
    assert r["reward_coupon"] == "THANKS-AB12"
    # the restored code still validates at checkout with the restored rate
    chk = growth.check_code("ja-xyz12")
    assert chk["ok"] and chk["kind"] == "referral" and chk["percent"] == growth.settings()["buyerPercent"]
    execute("DELETE FROM coupons"); execute("DELETE FROM referral_codes")


def test_product_reviews_survive_a_wiped_disk(sb):
    sb.tables["growth_settings"] = [{
        "key": supabase_store.PRODUCT_REVIEWS_KEY,
        "value": json.dumps([
            {"product_id": "wix-001", "order_id": "JA-TEST1", "email": "buyer@x.com",
             "name": "Buyer", "stars": 5, "note": "Lovely ankara, fits perfectly.",
             "at": "2026-08-10T00:00:00"},
        ]),
    }]
    _wipe_growth_tables()
    appmod.create_app()
    row = one("SELECT * FROM product_reviews WHERE product_id='wix-001' AND email='buyer@x.com'")
    assert row and row["stars"] == 5
    assert row["note"] == "Lovely ankara, fits perfectly."
    execute("DELETE FROM product_reviews")


def test_restore_never_deletes_local_only_keys():
    """Upsert-only restore: markers that live only in SQLite (bootstrap,
    category-merge) must survive a restore whose Supabase map lacks them."""
    execute("DELETE FROM growth_settings")
    execute("INSERT INTO growth_settings (key, value) VALUES ('admin_bootstrap_applied','1')")
    restore_growth_settings({"minSpendNgn": "30000", "buyerPercent": "5"})
    assert one("SELECT value FROM growth_settings WHERE key='admin_bootstrap_applied'")
    assert one("SELECT value FROM growth_settings WHERE key='minSpendNgn'")["value"] == "30000"
    execute("DELETE FROM growth_settings")


# ------------------------------------------------------------- review mirror
def test_review_create_writes_to_the_product_reviews_table(client, sb):
    """A customer review must land in the real Supabase product_reviews table.

    It used to ride in growth_settings as one JSON blob, which was
    last-write-wins: two reviews arriving together lost one. The table is now
    the source of truth, so that is what this asserts.
    """
    execute("DELETE FROM product_reviews")
    execute("DELETE FROM orders WHERE id='JA-REV1'")
    execute("INSERT INTO orders (id, payload, email, status, at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("JA-REV1", json.dumps({"items": [{"id": "wix-001", "name": "A", "qty": 1}]}),
             "rev@x.com", "confirmed", "2026-09-01T00:00:00", "2026-09-01T00:00:00"))
    tok = client.get("/api/config").get_json()["csrf"]
    r = client.post("/api/reviews",
                    json={"productId": "wix-001", "email": "rev@x.com", "name": "Rev",
                          "stars": 4, "note": "Great quality."},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    rows = sb.tables.get("product_reviews", [])
    assert rows, "the review was not written to the product_reviews table"
    assert any(m["product_id"] == "wix-001" and m["email"] == "rev@x.com"
               and m["stars"] == 4 for m in rows)
    # the read path must come from the same table, not from SQLite
    got = client.get("/api/reviews/wix-001").get_json()
    assert got["source"] == "supabase:product_reviews"
    assert got["count"] == 1 and got["average"] == 4
    execute("DELETE FROM product_reviews"); execute("DELETE FROM orders WHERE id='JA-REV1'")


def test_two_reviews_from_different_customers_both_survive(client, sb):
    """The exact bug the blob had: the second write replaced the first."""
    execute("DELETE FROM product_reviews")
    execute("DELETE FROM orders WHERE id IN ('JA-REV2','JA-REV3')")
    for oid, email in (("JA-REV2", "a@x.com"), ("JA-REV3", "b@x.com")):
        execute("INSERT INTO orders (id, payload, email, status, at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (oid, json.dumps({"items": [{"id": "wix-002", "name": "B", "qty": 1}]}),
                 email, "confirmed", "2026-09-01T00:00:00", "2026-09-01T00:00:00"))
    tok = client.get("/api/config").get_json()["csrf"]
    for email, stars in (("a@x.com", 5), ("b@x.com", 3)):
        r = client.post("/api/reviews",
                        json={"productId": "wix-002", "email": email, "name": "R",
                              "stars": stars, "note": "Fine."},
                        headers={"X-CSRF-Token": tok})
        assert r.status_code == 200, r.data
    rows = sb.tables.get("product_reviews", [])
    assert len(rows) == 2, f"expected both reviews, got {len(rows)}: {rows}"
    got = client.get("/api/reviews/wix-002").get_json()
    assert got["count"] == 2 and got["average"] == 4.0
    execute("DELETE FROM product_reviews")
    execute("DELETE FROM orders WHERE id IN ('JA-REV2','JA-REV3')")


def test_resubmitting_the_same_review_updates_instead_of_duplicating(client, sb):
    execute("DELETE FROM product_reviews")
    execute("DELETE FROM orders WHERE id='JA-REV4'")
    execute("INSERT INTO orders (id, payload, email, status, at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("JA-REV4", json.dumps({"items": [{"id": "wix-003", "name": "C", "qty": 1}]}),
             "c@x.com", "confirmed", "2026-09-01T00:00:00", "2026-09-01T00:00:00"))
    tok = client.get("/api/config").get_json()["csrf"]
    for stars in (2, 5):
        r = client.post("/api/reviews",
                        json={"productId": "wix-003", "email": "c@x.com", "name": "C",
                              "stars": stars, "note": "Changed my mind."},
                        headers={"X-CSRF-Token": tok})
        assert r.status_code == 200, r.data
    rows = sb.tables.get("product_reviews", [])
    assert len(rows) == 1, f"the unique key did not hold: {rows}"
    assert rows[0]["stars"] == 5
    execute("DELETE FROM product_reviews"); execute("DELETE FROM orders WHERE id='JA-REV4'")


def test_product_reviews_roundtrip(sb):
    rows = [{"product_id": "wix-002", "order_id": "JA-T2", "email": "b@x.com",
             "name": "B", "stars": 3, "note": "ok", "at": "2026-09-02T00:00:00"}]
    assert supabase_store.save_product_reviews(rows) is True
    assert supabase_store.load_product_reviews() == rows
    assert supabase_store.load_product_reviews() is not None
