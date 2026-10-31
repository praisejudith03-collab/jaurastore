"""A product the owner adds in the admin portal must be on the storefront.

The owner's report: "products I added yesterday and today save in the admin
portal but do not show on jaurastore.com.ng".  This module follows ONE new
product through the exact production write path -

    POST /api/admin/products
      -> catalog.upsert            (production: Supabase is the source of truth)
        -> supabase_store.upsert_products
      -> GET /api/catalog          (what a shopper's browser receives)

and asserts the row is in the public answer.  It runs with Supabase configured
and FLASK_ENV=production so the Supabase branch is the one under test (the test
suite's own default, FLASK_ENV=testing, takes the local override path and would
never exercise it).

Run with:  python3 -m pytest tests/test_new_product_publish.py -q
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("FLASK_ENV", "testing")     # app boot stays local

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import supabase_store  # noqa: E402
from config import Config  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW
EMAIL = "jaurastore@gmail.com"

# The products-table columns the live database has (see supabase_schema.sql +
# schema_sections/01_products.sql).  A PostgREST table rejects any other key.
PRODUCT_COLUMNS = {
    "id", "legacyId", "sku", "slug", "name", "nameFr", "descriptionFr",
    "category", "category_id", "priceCfa", "compareCfa", "priceNgn",
    "compareNgn", "image", "image_url", "images", "description", "stock",
    "stock_quantity", "badge", "featured", "online", "colors", "options",
    "optionStock", "optionPrices", "optionCompareAt", "optionSupplierSku",
    "optionSku", "reviews", "enableCustomNote", "customNotePrompt",
    "dimensions", "bulkQty", "bulkPercent", "placeholderImage",
    "usesPlaceholder", "source", "supplierId", "supplierSku", "supplierCost",
    "optionSupplierCost", "supplier_status", "is_deleted", "updated_at",
    "is_active", "price_cfa", "price_ngn", "compare_cfa", "compare_ngn",
    "name_fr", "option_prices", "option_compare_at", "option_stock",
    "option_stock_available", "option_stock_status", "stock_available",
    "stock_status", "supplierTracked",
}


class _T:
    def __init__(self, owner, name):
        self._o = owner
        self._name = name
        self._eq = []
        self._update = None
        self._window = None

    # -- query building
    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n=None):
        return self

    def range(self, lo, hi):
        self._window = (lo, hi)
        return self

    def eq(self, key, val):
        self._eq.append((key, val))
        return self

    def in_(self, key, vals):
        self._eq.append((key, ("__in__", list(vals or []))))
        return self

    # -- writes
    def upsert(self, rows):
        rows = rows if isinstance(rows, list) else [rows]
        store = self._o.tables.setdefault(self._name, [])
        for r in rows:
            r = dict(r or {})
            if self._name == "products":
                for key in r:
                    if key not in PRODUCT_COLUMNS:
                        raise Exception(
                            f"Could not find the '{key}' column of 'products' "
                            "in the schema cache (PGRST204)")
            pk = "key" if self._name == "growth_settings" else (
                "product_id" if self._name == "deleted_products" else "id")
            store[:] = [x for x in store if x.get(pk) != r.get(pk)]
            store.append(r)
        return self

    def update(self, payload):
        self._update = payload or {}
        return self

    def delete(self):
        store = self._o.tables.setdefault(self._name, [])
        store[:] = [x for x in store if not self._matches(x)]
        return self

    def _matches(self, row):
        for key, val in self._eq:
            if isinstance(val, tuple) and val and val[0] == "__in__":
                if str(row.get(key)) not in {str(v) for v in val[1]}:
                    return False
            elif row.get(key) != val:
                return False
        return True

    def execute(self):
        store = self._o.tables.setdefault(self._name, [])
        rows = [x for x in store if self._matches(x)]
        if self._update is not None:
            for r in rows:
                r.update(self._update)
        total = len(rows)
        if self._window:
            lo, hi = self._window
            rows = rows[lo:hi + 1]
        return {"data": [dict(r) for r in rows], "count": total}


class _Fake:
    def __init__(self, rows=None):
        self.tables = {"products": [dict(r) for r in (rows or [])],
                       "growth_settings": [], "deleted_products": []}

    def table(self, name):
        return _T(self, name)


def _existing(pid, name, slug=None):
    return {"id": pid, "name": name, "slug": slug or pid, "sku": "JAU-" + pid,
            "priceNgn": 10000, "priceCfa": 4400, "stock": 3,
            "stock_quantity": 3, "online": True, "is_deleted": False,
            "is_active": True, "source": "admin", "images": [],
            "updated_at": "2026-09-01T10:00:00+00:00"}


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


def _login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


@pytest.fixture()
def production(app, monkeypatch):
    """Supabase is the source of truth, exactly like the live deployment."""
    fake = _Fake([_existing("jau-mtwtw75i", "Kente set"),
                  _existing("wix-004", "12 pcs dessini pot set")])
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(catalog_mod, "_sync_repo_async", lambda *a, **k: None)
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda *a, **k: {"products": [], "deleted": [],
                                         "updatedAt": "", "updatedBy": ""})
    monkeypatch.setattr(catalog_mod, "_seed_products", lambda *a, **k: [])
    monkeypatch.setattr(Config, "ENV", "production")
    supabase_store.invalidate_read_cache()
    yield fake
    supabase_store.invalidate_read_cache()


def _payload(pid="jau-mvvnew001", name="Wireless Earbuds Pro"):
    """Exactly what js/admin.js handleProductSubmit posts for a new product."""
    return {
        "id": pid, "sku": "JAU-NEW001", "slug": "wireless-earbuds-pro",
        "name": name, "nameFr": "", "category": "gadgets",
        "priceNgn": 15000, "priceCfa": 6000,
        "image": "https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/"
                 "public/uploads/products/2026/10/a.jpg",
        "images": ["https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/"
                   "public/uploads/products/2026/10/a.jpg"],
        "image_url": "https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/"
                     "object/public/uploads/products/2026/10/a.jpg",
        "description": "Noise cancelling", "stock": 6, "stock_quantity": 6,
        "stockStatus": "in", "colors": [], "options": [], "optionStock": {},
        "optionPrices": {}, "optionCompareAt": {}, "enableCustomNote": False,
        "customNotePrompt": "", "featured": False, "online": True,
        "badge": "", "supplierSku": "", "optionSku": {}, "optionSupplierSku": {},
    }


def _public_ids(client):
    r = client.get("/api/catalog")
    assert r.status_code == 200
    return [p["id"] for p in (r.get_json() or {}).get("products") or []]


def test_new_product_is_publicly_visible_after_save(client, production):
    tok = _login(client)
    assert _public_ids(client) == ["jau-mtwtw75i", "wix-004"]

    r = client.post("/api/admin/products", json={"product": _payload()},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True and body["action"] == "created", body

    ids = _public_ids(client)
    assert "jau-mvvnew001" in ids, ids
    assert len(ids) == 3


def _add_legacy_tombstone(fake, pid):
    """The durable growth_settings list an earlier delete left behind."""
    fake.tables.setdefault("growth_settings", []).append(
        {"key": supabase_store.DELETED_IDS_KEY, "value": json.dumps([pid])})
    supabase_store.invalidate_read_cache()


def test_resaving_a_previously_deleted_product_publishes_it(client, production):
    """Re-creating a piece from the Trash must land AND clear the tombstone."""
    _add_legacy_tombstone(production, "jau-mvvnew002")
    tok = _login(client)
    payload = _payload("jau-mvvnew002", "Silk Headband")
    payload["slug"] = "silk-headband"

    r = client.post("/api/admin/products", json={"product": payload},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True and body["action"] == "created", body
    # The save reports the storefront's own answer, and it must be live.
    assert body.get("live") is True, body
    assert "jau-mvvnew002" in _public_ids(client)


def test_two_quick_creates_with_the_same_name_both_publish(client, production):
    """Back-to-back saves may not steal each other's slug and vanish.

    catalog.upsert allocates the slug against a cached catalogue read, and
    _dedupe_products folds two rows that share slug+name. The second product of
    a batch used to be saved and never appear.
    """
    tok = _login(client)
    first = _payload("jau-mvvdup01", "Pearl Clutch")
    first["slug"] = "pearl-clutch"
    first["sku"] = "JAU-PEARL1"
    second = _payload("jau-mvvdup02", "Pearl Clutch")
    second["slug"] = "pearl-clutch"
    second["sku"] = "JAU-PEARL2"          # the editor generates a fresh code

    r1 = client.post("/api/admin/products", json={"product": first},
                     headers={"X-CSRF-Token": tok})
    r2 = client.post("/api/admin/products", json={"product": second},
                     headers={"X-CSRF-Token": tok})
    assert r1.status_code == 200 and r2.status_code == 200, (r1.data, r2.data)
    assert r1.get_json().get("live") is True, r1.get_json()
    assert r2.get_json().get("live") is True, r2.get_json()
    ids = _public_ids(client)
    assert "jau-mvvdup01" in ids, ids
    assert "jau-mvvdup02" in ids, ids


def test_readding_a_product_keeps_the_new_copy_not_the_stale_one(
        client, production):
    """A re-saved piece must be the copy the shop shows, not the old one.

    Two rows, one product (same name + slug + sku): the shop folds them into
    one card, which is right. It used to keep the OLDER row, so the save the
    owner had just made was the one that disappeared - "I saved it and it is
    still not there".
    """
    tok = _login(client)
    old = _payload("jau-mvvold1", "Velvet Scrunchie")
    old["slug"] = "velvet-scrunchie"
    old["sku"] = "JAU-VELVET"
    r_old = client.post("/api/admin/products", json={"product": old},
                        headers={"X-CSRF-Token": tok})
    assert r_old.status_code == 200, r_old.data

    new = _payload("jau-mvvnew3", "Velvet Scrunchie")
    new["slug"] = "velvet-scrunchie"
    new["sku"] = "JAU-VELVET"
    r_new = client.post("/api/admin/products", json={"product": new},
                        headers={"X-CSRF-Token": tok})
    assert r_new.status_code == 200, r_new.data
    body = r_new.get_json()
    assert body.get("live") is True, body

    ids = _public_ids(client)
    assert "jau-mvvnew3" in ids, ids
    assert "jau-mvvold1" not in ids, ids


def test_save_reports_not_live_instead_of_claiming_the_shop_shows_it(
        client, production):
    """A confirmed save that the shop hides must say so, with the reason."""
    tok = _login(client)
    payload = _payload("jau-mvvhidden", "Draft Tote")
    payload["slug"] = "draft-tote"
    payload["online"] = False

    r = client.post("/api/admin/products", json={"product": payload},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert body["live"] is False, body
    assert body["publishStage"] == "hidden", body
    assert "hidden" in body["publishReason"]
    assert "jau-mvvhidden" not in _public_ids(client)


def test_catalog_audit_names_why_a_saved_row_is_not_published(client, production):
    """The admin audit must name the filter, not just count rows."""
    tok = _login(client)
    payload = _payload("jau-mvvaudit", "Audit Scarf")
    payload["slug"] = "audit-scarf"
    r = client.post("/api/admin/products", json={"product": payload},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert "jau-mvvaudit" in _public_ids(client)

    # An extra delete ledger entry (an earlier delete of the same id) is the
    # usual reason a saved row never reaches the grid.
    _add_legacy_tombstone(production, "jau-mvvaudit")

    audit = client.get("/api/admin/catalog/audit").get_json()
    reasons = {row["id"]: row["reason"] for row in audit.get("notPublished") or []}
    assert "jau-mvvaudit" in reasons, audit
    assert "deleted" in reasons["jau-mvvaudit"]
