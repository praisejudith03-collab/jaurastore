"""The Trash / Recycler: deleted products leave the shop but not the records.

The default admin DELETE moves a product into a dedicated trash store instead
of destroying it. The piece is excluded from every active listing (storefront,
admin list, search) and from the catalogue count, but a full snapshot is kept
so Restore brings it back with photos, prices and stock intact. Permanently
Delete runs the original hard-delete machinery and purges everything.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import app as appmod  # noqa: E402
import catalog as catmod  # noqa: E402
import supabase_store  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tests"))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"
PID = "jau-trash-1"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    init_db()
    execute("DELETE FROM rate_limits")
    execute("DELETE FROM growth_settings WHERE key=?",
            (supabase_store.PRODUCT_TRASH_KEY,))
    monkeypatch.setattr(catmod, "_prod_source", lambda: False)
    monkeypatch.setattr(catmod, "_sync_repo_async", lambda: None)
    # A scratch catalogue file so the suite's shared override file is untouched.
    cat_file = tmp_path / "trash-catalog.json"
    cat_file.write_text(json.dumps({"products": [], "deleted": []}))
    monkeypatch.setattr(catmod, "CATALOG_FILE", str(cat_file))
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        r = c.post("/api/admin/login", json={"email": EMAIL, "password": PW})
        assert r.status_code == 200, r.data
        yield c
    execute("DELETE FROM growth_settings WHERE key=?",
            (supabase_store.PRODUCT_TRASH_KEY,))


def _csrf(client):
    r = client.get("/api/admin/session")
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def _save_product(client, csrf):
    r = client.post("/api/admin/products", headers={"X-CSRF-Token": csrf}, json={
        "product": {"id": PID, "name": "Trash Test Purse", "sku": "TRASH-1",
                    "category": "accessories", "priceNgn": 9000,
                    "stock_quantity": 4, "online": True,
                    "image": "images/products/_placeholder.jpg"},
    })
    assert r.status_code == 200, r.data
    return r.get_json()


def _live_ids():
    return {str(p["id"]) for p in catmod.merged(include_hidden=True)}


def test_delete_moves_to_trash_and_clears_the_catalogue(client):
    csrf = _csrf(client)
    _save_product(client, csrf)
    assert PID in _live_ids()

    r = client.delete(f"/api/admin/products/{PID}", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert body["deleteMode"] == "trash"
    assert body["trashed"] is True

    # Excluded from the live catalogue and the storefront view.
    assert PID not in _live_ids()
    public = client.get("/api/catalog")
    assert PID not in json.dumps(public.get_json())

    # The trash list carries the record with its inventory metrics.
    listing = client.get("/api/admin/products/trash")
    assert listing.status_code == 200, listing.data
    items = listing.get_json()["items"]
    assert [row["id"] for row in items] == [PID]
    assert items[0]["name"] == "Trash Test Purse"
    assert items[0]["stock"] == 4

    # A repeat delete of the same id is an idempotent success.
    again = client.delete(f"/api/admin/products/{PID}", headers={"X-CSRF-Token": csrf})
    assert again.status_code == 200, again.data
    assert again.get_json()["alreadyTrashed"] is True


def test_restore_returns_the_product_with_its_details(client):
    csrf = _csrf(client)
    _save_product(client, csrf)
    assert client.delete(f"/api/admin/products/{PID}",
                         headers={"X-CSRF-Token": csrf}).status_code == 200

    restored = client.post(f"/api/admin/products/trash/{PID}/restore",
                           headers={"X-CSRF-Token": csrf})
    assert restored.status_code == 200, restored.data
    body = restored.get_json()
    assert body["restored"] is True
    assert body["product"]["name"] == "Trash Test Purse"
    assert PID in _live_ids()
    # Restored clean out of the trash list.
    assert client.get("/api/admin/products/trash").get_json()["count"] == 0

    # A second restore of the same id is a 404, not a duplicate.
    twice = client.post(f"/api/admin/products/trash/{PID}/restore",
                        headers={"X-CSRF-Token": csrf})
    assert twice.status_code == 404


def test_purge_permanently_deletes_the_snapshot_and_the_piece(client):
    csrf = _csrf(client)
    _save_product(client, csrf)
    assert client.delete(f"/api/admin/products/{PID}",
                         headers={"X-CSRF-Token": csrf}).status_code == 200

    purged = client.delete(f"/api/admin/products/trash/{PID}",
                           headers={"X-CSRF-Token": csrf})
    assert purged.status_code == 200, purged.data
    assert PID not in _live_ids()
    assert client.get("/api/admin/products/trash").get_json()["count"] == 0
    # The local override file keeps the durable deleted marker, so the id
    # cannot resurface from a stale mirror or a redeploy.
    data = json.loads(open(catmod.CATALOG_FILE).read())
    assert PID in data.get("deleted", [])
    # Purging something that is not in the trash is a 404.
    assert client.delete("/api/admin/products/trash/not-there",
                         headers={"X-CSRF-Token": csrf}).status_code == 404


def test_trash_entries_never_reach_the_public_catalogue_or_counts(client, monkeypatch):
    """The inventory-count regression: deleted ids must not inflate the list."""
    csrf = _csrf(client)
    _save_product(client, csrf)
    before = len(_live_ids())
    assert client.delete(f"/api/admin/products/{PID}",
                         headers={"X-CSRF-Token": csrf}).status_code == 200
    assert len(_live_ids()) == before - 1
    # Even a trashed-and-hidden id stays out of the hidden read too.
    assert PID not in {str(p["id"]) for p in catmod.merged(include_hidden=True)}


def test_local_growth_settings_row_holds_the_trash(client):
    csrf = _csrf(client)
    _save_product(client, csrf)
    assert client.delete(f"/api/admin/products/{PID}",
                         headers={"X-CSRF-Token": csrf}).status_code == 200
    row = one("SELECT value FROM growth_settings WHERE key=?",
              (supabase_store.PRODUCT_TRASH_KEY,))
    assert row is not None
    stored = json.loads(row["value"])
    assert isinstance(stored, list) and stored[0]["id"] == PID
    assert stored[0]["payload"]["priceNgn"] == 9000
