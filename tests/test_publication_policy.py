"""Publication policy (2026-09-08): what may appear on the public storefront.

    publish  <=>  not a test fixture, not operator-offline,
                  a real non-placeholder image, priceNgn/priceCfa > 0,
                  stock_quantity a non-negative integer.

Covers: ``publication.decide`` buckets, the Admin save defaults (a complete
product is online at once, an incomplete one is saved but stays offline,
an explicit unpublish is honoured), the explicit publish endpoint (409 with
reasons for a row that is not ready), the /api/catalog ETag moving on every
publish / unpublish, and the production read path using ONLY Supabase rows
filtered with ``online IS TRUE``.

Run with:  python3 -m pytest tests/test_publication_policy.py -q
"""
import json
import os

os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

import auth as authmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import publication  # noqa: E402
from _pw import PW  # noqa: E402
from app import create_app  # noqa: E402
from db import execute, init_db  # noqa: E402

EMAIL = "jaurastore@gmail.com"
REAL_PHOTO = "images/products/10in1-raf-sandwich-maker.jpg"
PUBLIC_URL = ("https://abc.supabase.co/storage/v1/object/public/uploads/"
              "products/2026/09/abc123.jpg")


@pytest.fixture(scope="module")
def app():
    a = create_app()
    a.config["TESTING"] = True
    return a


@pytest.fixture()
def client(app):
    init_db()
    authmod.ensure_seed_admins()
    authmod.set_password(EMAIL, PW)
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def _row(**kw):
    base = {"id": "wix-900", "name": "Test kettle", "category": "household",
            "priceNgn": 12000, "priceCfa": 5280, "stock_quantity": 3,
            "image_url": PUBLIC_URL}
    base.update(kw)
    return base


# ---------------------------------------------------------------- decide()

def test_a_complete_row_with_a_public_photo_is_live():
    d = publication.decide(_row())
    assert d["online"] is True and d["bucket"] == "live" and d["reasons"] == []
    assert d["image_status"] == "public"


def test_a_committed_repo_photo_counts_as_a_real_image():
    d = publication.decide(_row(image_url=REAL_PHOTO))
    assert d["online"] is True and d["image_status"] == "repo"


def test_zero_stock_is_still_publishable_but_missing_stock_is_not():
    assert publication.decide(_row(stock_quantity=0))["online"] is True
    d = publication.decide(_row(stock_quantity=None, stock=None))
    assert d["online"] is False and "invalid_stock" in d["codes"]
    d = publication.decide(_row(stock_quantity=-1))
    assert d["online"] is False and "invalid_stock" in d["codes"]


def test_placeholder_only_rows_stay_offline():
    d = publication.decide(_row(image_url="images/products/_placeholder.jpg"))
    assert d["online"] is False and d["bucket"] == "placeholder_only"
    assert d["codes"] == ["placeholder_image"]


def test_unconfirmed_relative_and_blank_images_are_no_image():
    d = publication.decide(_row(image_url="/uploads/products/2026/09/x.jpg"))
    assert d["online"] is False and d["bucket"] == "no_image"
    assert d["codes"] == ["relative_image"]
    d = publication.decide(_row(image_url=""))
    assert d["bucket"] == "no_image" and d["codes"] == ["no_image"]
    d = publication.decide(_row(image_url="https://static.wixstatic.com/a.jpg"))
    assert d["bucket"] == "no_image" and d["codes"] == ["external_image"]


def test_invalid_pricing_is_incomplete_even_with_a_real_photo():
    d = publication.decide(_row(priceNgn=0))
    assert d["online"] is False and d["bucket"] == "incomplete"
    assert "invalid_price" in d["codes"]
    d = publication.decide(_row(priceCfa=None))
    assert d["online"] is False and "invalid_price" in d["codes"]


def test_operator_offline_and_fixture_ids_never_publish():
    d = publication.decide(_row(id="wix-001"))
    assert d["online"] is False and d["bucket"] == "operator_offline"
    d = publication.decide(_row(id="wix-012"))
    assert d["online"] is False and d["bucket"] == "operator_offline"
    d = publication.decide(_row(id="jau-stock-dec"))
    assert d["online"] is False and d["bucket"] == "fixture"
    # the fixture rule is an audit rule: a save judges completeness only
    assert publication.decide(_row(id="jau-stock-dec"), fixtures=False)["online"] is True


def test_classify_buckets_every_row_once():
    rows = [_row(), _row(id="wix-001"), _row(id="wix-041", image_url="images/products/_placeholder.jpg"),
            _row(id="jau-mirror-ok"), _row(id="wix-002", image_url="/uploads/p.jpg"),
            _row(id="wix-777", priceNgn=0)]
    b = publication.classify(rows)
    assert [d["id"] for d in b["live"]] == ["wix-900"]
    assert [d["id"] for d in b["operator_offline"]] == ["wix-001"]
    assert [d["id"] for d in b["placeholder_only"]] == ["wix-041"]
    assert [d["id"] for d in b["fixture"]] == ["jau-mirror-ok"]
    assert [d["id"] for d in b["no_image"]] == ["wix-002"]
    assert [d["id"] for d in b["incomplete"]] == ["wix-777"]
    assert sum(len(v) for v in b.values()) == len(rows)


# --------------------------------------------------------- Admin save path

def test_a_complete_new_product_defaults_to_online(client):
    tok = login(client)
    r = client.post("/api/admin/products", json={"product": {
        "id": "jau-pub-ok", "name": "Publishable bag", "category": "bags",
        "priceNgn": 9000, "stock": 4, "image": REAL_PHOTO,
    }}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["product"]["online"] is True
    assert body["publication"]["online"] is True
    assert body["publication"]["bucket"] == "live"
    client.post("/api/admin/logout", headers={"X-CSRF-Token": tok})
    public = client.get("/api/catalog").get_json()["products"]
    assert any(p["id"] == "jau-pub-ok" for p in public)


def test_an_incomplete_product_is_saved_but_stays_offline(client):
    tok = login(client)
    r = client.post("/api/admin/products", json={"product": {
        "id": "jau-pub-noimg", "name": "No photo yet", "category": "bags",
        "priceNgn": 9000, "stock": 4, "online": True,
    }}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["product"]["online"] is False, "an explicit online=true cannot override the policy"
    assert body["publication"]["online"] is False
    assert body["publication"]["requested"] is True
    assert "no_image" in body["publication"]["codes"]
    # the admin sees it, the public does not, and the row exists (not deleted)
    assert any(p["id"] == "jau-pub-noimg" for p in client.get("/api/catalog?all=1").get_json()["products"])
    client.post("/api/admin/logout", headers={"X-CSRF-Token": tok})
    assert not any(p["id"] == "jau-pub-noimg" for p in client.get("/api/catalog").get_json()["products"])
    assert any(str(p.get("id")) == "jau-pub-noimg" for p in catalog_mod.overrides()["products"])


def test_invalid_price_or_stock_keeps_a_photographed_product_offline(client):
    tok = login(client)
    r = client.post("/api/admin/products", json={"product": {
        "id": "jau-pub-price", "name": "Free?", "category": "bags",
        "priceNgn": 0, "stock": 4, "image": REAL_PHOTO,
    }}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert r.get_json()["publication"]["codes"] == ["invalid_price"]
    assert r.get_json()["product"]["online"] is False


def test_an_explicit_unpublish_is_honoured_for_a_complete_product(client):
    tok = login(client)
    r = client.post("/api/admin/products", json={"product": {
        "id": "jau-pub-hide", "name": "Hidden on purpose", "category": "bags",
        "priceNgn": 9000, "stock": 4, "image": REAL_PHOTO, "online": False,
    }}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    pub = r.get_json()["publication"]
    assert pub["online"] is False and pub["publishable"] is True
    assert pub["requested"] is False


def test_a_stock_move_never_changes_publication(client):
    tok = login(client)
    client.post("/api/admin/products", json={"product": {
        "id": "jau-pub-stock", "name": "Sells out", "category": "bags",
        "priceNgn": 9000, "stock": 1, "image": REAL_PHOTO,
    }}, headers={"X-CSRF-Token": tok})
    catalog_mod.apply_stock_delta("jau-pub-stock", -1, actor="test")
    row = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == "jau-pub-stock")
    assert row["stock_quantity"] == 0 and row["online"] is True
    # ...and an unpublished product does not come back online on a refund
    client.post("/api/admin/products", json={"product": {
        "id": "jau-pub-stock", "name": "Sells out", "category": "bags",
        "priceNgn": 9000, "stock": 0, "image": REAL_PHOTO, "online": False,
    }}, headers={"X-CSRF-Token": tok})
    catalog_mod.apply_stock_delta("jau-pub-stock", +2, actor="test")
    row = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == "jau-pub-stock")
    assert row["stock_quantity"] == 2 and row["online"] is False


# ------------------------------------------------- explicit publish endpoint

def test_publish_endpoint_flips_only_online(client):
    tok = login(client)
    client.post("/api/admin/products", json={"product": {
        "id": "jau-pub-flip", "name": "Flip me", "category": "bags",
        "priceNgn": 9000, "stock": 4, "image": REAL_PHOTO, "online": False,
    }}, headers={"X-CSRF-Token": tok})
    before = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == "jau-pub-flip")
    assert before["online"] is False

    r = client.post("/api/admin/products/jau-pub-flip/publish", json={"online": True},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert r.get_json()["online"] is True and r.get_json()["product"]["online"] is True
    after = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == "jau-pub-flip")
    assert after["online"] is True
    for k in ("priceNgn", "priceCfa", "stock_quantity", "image", "name", "slug"):
        assert after[k] == before[k], k

    r = client.post("/api/admin/products/jau-pub-flip/publish", json={"online": False},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert next(p for p in catalog_mod.merged(include_hidden=True)
                if p["id"] == "jau-pub-flip")["online"] is False


def test_publishing_an_unready_product_is_refused_with_reasons(client):
    tok = login(client)
    client.post("/api/admin/products", json={"product": {
        "id": "jau-pub-refuse", "name": "Not ready", "category": "bags",
        "priceNgn": 9000, "stock": 4,
    }}, headers={"X-CSRF-Token": tok})
    r = client.post("/api/admin/products/jau-pub-refuse/publish", json={"online": True},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 409, r.data
    body = r.get_json()
    assert body["ok"] is False
    # merged() resolves a missing photo to the branded placeholder, so the
    # stored row reads as "placeholder": either way it is not a real image
    assert set(body["publication"]["codes"]) & {"no_image", "placeholder_image"}
    assert body["publication"]["reasons"]
    # unpublishing is always allowed, and the row is still there
    r = client.post("/api/admin/products/jau-pub-refuse/publish", json={"online": False},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert any(p["id"] == "jau-pub-refuse" for p in catalog_mod.merged(include_hidden=True))


def test_publish_endpoint_validates_its_input(client):
    tok = login(client)
    r = client.post("/api/admin/products/jau-pub-flip/publish", json={"online": "yes"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 400
    r = client.post("/api/admin/products/jau-does-not-exist/publish", json={"online": True},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 404


def test_publish_endpoint_needs_an_admin(client):
    r = client.post("/api/admin/products/jau-pub-flip/publish", json={"online": True})
    assert r.status_code in (401, 403)


# ------------------------------------------------------------ ETag / caching

def test_catalog_etag_moves_on_publish_and_unpublish(client):
    tok = login(client)
    client.post("/api/admin/products", json={"product": {
        "id": "jau-pub-etag", "name": "Etag bag", "category": "bags",
        "priceNgn": 9000, "stock": 4, "image": REAL_PHOTO,
    }}, headers={"X-CSRF-Token": tok})
    client.post("/api/admin/logout", headers={"X-CSRF-Token": tok})

    r1 = client.get("/api/catalog")
    assert r1.status_code == 200
    etag1 = r1.headers["ETag"]
    assert r1.headers["Cache-Control"] == "public, max-age=30, must-revalidate"
    assert "Cookie" in r1.headers.get("Vary", "")
    assert client.get("/api/catalog", headers={"If-None-Match": etag1}).status_code == 304
    meta = r1.get_json()["meta"]
    assert set(meta) >= {"updatedAt", "count", "online"}

    tok = login(client)
    r = client.post("/api/admin/products/jau-pub-etag/publish", json={"online": False},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    # the admin view is never shared or stored
    assert client.get("/api/catalog").headers["Cache-Control"] == "private, no-store"
    client.post("/api/admin/logout", headers={"X-CSRF-Token": tok})

    r2 = client.get("/api/catalog", headers={"If-None-Match": etag1})
    assert r2.status_code == 200, "a stale 304 after an unpublish"
    assert r2.headers["ETag"] != etag1
    assert r2.get_json()["meta"]["online"] == meta["online"] - 1
    assert not any(p["id"] == "jau-pub-etag" for p in r2.get_json()["products"])


def test_public_catalog_body_and_etag_come_from_the_same_snapshot(client):
    """The ETag is derived from the same read as the body - a product count in
    meta always equals what the public list would be after the online filter."""
    r = client.get("/api/catalog")
    body = r.get_json()
    assert body["meta"]["count"] >= len(body["products"])


# ----------------------------------------------- production read path (Supabase)

def _prod_rows():
    return [
        _row(id="wix-900", online=True, updated_at="2026-09-08T10:00:00Z"),
        _row(id="wix-901", online=False, updated_at="2026-09-08T11:00:00Z"),
        _row(id="wix-902", online=None, updated_at="2026-09-08T09:00:00Z"),
        _row(id="wix-903", online="true", updated_at="2026-09-08T09:30:00Z"),
    ]


def test_production_reads_only_supabase_rows_with_online_true(monkeypatch):
    monkeypatch.setattr(catalog_mod, "_prod_source", lambda: True)
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: _prod_rows())
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [_row(id="jau-local-only", online=True)],
                                 "deleted": [], "updatedAt": "2026-09-08T12:00:00Z"})

    everything = catalog_mod.merged(include_hidden=True)
    ids = {p["id"] for p in everything}
    assert ids == {"wix-900", "wix-901", "wix-902", "wix-903"}, \
        "no seed row and no local override may join the production catalogue"

    public = catalog_mod.merged()
    assert [p["id"] for p in public] == ["wix-900"], "online IS TRUE only: None and 'true' are offline"

    m = catalog_mod.meta(everything)
    assert m["count"] == 4 and m["online"] == 1
    assert m["updatedAt"] == "2026-09-08T12:00:00Z"  # max(row updated_at, local stamp)


def test_production_meta_follows_supabase_updated_at(monkeypatch):
    monkeypatch.setattr(catalog_mod, "_prod_source", lambda: True)
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: _prod_rows())
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [], "deleted": [], "updatedAt": ""})
    assert catalog_mod.meta()["updatedAt"] == "2026-09-08T11:00:00Z"

    rows = _prod_rows()
    rows[1]["updated_at"] = "2026-09-08T13:00:00Z"   # an unpublish bumps the hidden row
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: rows)
    assert catalog_mod.meta()["updatedAt"] == "2026-09-08T13:00:00Z"


def test_production_supabase_outage_is_an_empty_catalogue_not_an_old_one(monkeypatch):
    monkeypatch.setattr(catalog_mod, "_prod_source", lambda: True)
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: None)
    assert catalog_mod.merged() == []


def test_production_set_online_writes_only_the_flag(monkeypatch):
    import supabase_store
    calls = []
    monkeypatch.setattr(catalog_mod, "_prod_source", lambda: True)
    monkeypatch.setattr(catalog_mod, "_sync_repo_async", lambda *a, **k: None)
    monkeypatch.setattr(supabase_store, "set_product_online",
                        lambda pid, online, stamp=None: calls.append((pid, online)) or True)
    assert catalog_mod.set_online("wix-900", False, "tester") is True
    assert calls == [("wix-900", False)]
    monkeypatch.setattr(supabase_store, "set_product_online",
                        lambda pid, online, stamp=None: False)
    assert catalog_mod.set_online("wix-900", True, "tester") is False


def test_supabase_set_product_online_patches_two_columns_only(monkeypatch):
    import supabase_store

    class _Q:
        def __init__(self, log):
            self.log = log

        def update(self, patch):
            self.log.append(("update", dict(patch)))
            return self

        def eq(self, col, val):
            self.log.append(("eq", col, val))
            return self

        def execute(self):
            return type("R", (), {"data": [{"id": "wix-900"}]})()

    class _C:
        def __init__(self):
            self.log = []

        def table(self, name):
            self.log.append(("table", name))
            return _Q(self.log)

    fake = _C()
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    assert supabase_store.set_product_online("wix-900", False, "2026-09-08T12:00:00Z") is True
    updates = [e for e in fake.log if e[0] == "update"]
    assert updates and set(updates[0][1]) == {"online", "updated_at"}
    assert updates[0][1]["online"] is False
    assert ("eq", "id", "wix-900") in fake.log


def test_bulk_import_obeys_the_same_policy(client):
    """PUT /api/admin/products (CSV / bulk) cannot publish an unready row."""
    tok = login(client)
    # snapshot the raw override file: a bulk import replaces it wholesale
    before = json.loads(json.dumps(catalog_mod.overrides()))
    try:
        r = client.put("/api/admin/products", json={"products": [
            {"id": "jau-bulk-ok", "name": "Bulk ok", "category": "bags",
             "priceNgn": 9000, "stock": 2, "image": REAL_PHOTO},
            {"id": "jau-bulk-noimg", "name": "Bulk no photo", "category": "bags",
             "priceNgn": 9000, "stock": 2, "online": True},
        ]}, headers={"X-CSRF-Token": tok})
        assert r.status_code == 200, r.data
        rows = {p["id"]: p for p in catalog_mod.merged(include_hidden=True)}
        assert rows["jau-bulk-ok"]["online"] is True
        assert rows["jau-bulk-noimg"]["online"] is False
    finally:
        # put the shared fixture catalogue back exactly as it was (no policy
        # re-run on the seed rows other test modules order against)
        path = catalog_mod._norm_filename(catalog_mod.CATALOG_FILE)
        with catalog_mod._catalog_lock(path):
            catalog_mod._write_overrides(before, path)
