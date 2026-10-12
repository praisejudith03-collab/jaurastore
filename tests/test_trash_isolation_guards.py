"""Trash Bin isolation: an is_deleted tombstone hides a product everywhere.

Spec section 3: deleting in /admin sets the deleted flag so the piece leaves
the storefront, search, public APIs and broadcast generators at once, while
the dedicated Trash Bin keeps the only copy until the owner restores or
permanently purges it. Nothing re-ingests a trashed id in the background.
"""
import json
import os
import sys
from unittest.mock import patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import app as appmod  # noqa: E402
import broadcast_posts  # noqa: E402
import catalog as catmod  # noqa: E402
import supabase_store  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tests"))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"
PID = "jau-isolation-1"


class _FakeClient:
    """The smallest supabase-py surface delete_products_strict needs."""

    def __init__(self, store, fail_first_with=None):
        self._store = store
        self._fail = fail_first_with

    def table(self, _name):
        # One-shot: the first request hits the legacy schema error, the
        # retry (the fallback path under test) succeeds.
        fail, self._fail = self._fail, None
        return _FakeTable(self._store, fail)


class _FakeTable:
    """Chainable update().in_().execute() recorder."""

    def __init__(self, store, fail_first_with=None):
        self._store = store
        self._fail = fail_first_with
        self._patch = None

    def update(self, patch):
        self._patch = patch
        return self

    def in_(self, _col, ids):
        if isinstance(self._fail, Exception):
            err, self._fail = self._fail, None
            raise err
        for pid in ids:
            self._store.setdefault(pid, {}).update(self._patch or {})
        return self

    def execute(self):
        return self


def test_tombstone_sets_both_the_explicit_flag_and_the_legacy_marker():
    store = {}
    with patch_client(_FakeClient(store)):
        ok = supabase_store.delete_products_strict([PID])
    assert ok is True
    assert store[PID]["is_deleted"] is True
    assert store[PID]["source"] == "deleted"


def test_tombstone_falls_back_to_the_legacy_marker_on_a_legacy_table():
    store = {}
    legacy = _FakeClient(store, fail_first_with=Exception(
        "Could not find the 'is_deleted' column of products in the schema cache"))
    with patch_client(legacy):
        ok = supabase_store.delete_products_strict([PID])
    assert ok is True
    assert store[PID]["source"] == "deleted"


class patch_client:
    """Point supabase_store.client() at a fake for the duration."""

    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        self.p = pytest.MonkeyPatch()
        self.p.setattr(supabase_store, "client", lambda: self.fake)
        return self

    def __exit__(self, *exc):
        self.p.undo()
        return False


def test_live_catalogue_save_clears_the_tombstone_markers():
    """Restoring runs through upsert_products: the revived row must not stay
    flagged is_deleted/source=deleted or it would still read as trashed."""
    rows = []
    with patch("supabase_store._upsert_products_resilient",
               lambda r: rows.extend(r) or True):
        with patch("supabase_store.client", lambda: object()):
            with patch("supabase_store.enabled", lambda: True):
                ok = supabase_store.upsert_products([{
                    "id": PID, "name": "Back again", "priceNgn": 1000,
                    "image": "images/products/_placeholder.jpg",
                    "source": "deleted", "is_deleted": True,
                }])
    assert ok is True
    assert rows[0]["is_deleted"] is False
    assert rows[0]["source"] == "admin"


def test_broadcast_generator_refuses_deleted_rows():
    postable = {"id": "x", "name": "Live piece", "priceNgn": 100,
                "stock_quantity": 3, "online": True}
    assert broadcast_posts.is_postable(postable)
    for tombstoned in (
        {"is_deleted": True},
        {"isDeleted": True},
        {"source": "deleted"},
        {"deleted": True},
    ):
        row = {**postable, **tombstoned}
        assert not broadcast_posts.is_postable(row), tombstoned


@pytest.fixture()
def client(tmp_path, monkeypatch):
    init_db()
    execute("DELETE FROM rate_limits")
    execute("DELETE FROM growth_settings WHERE key=?",
            (supabase_store.PRODUCT_TRASH_KEY,))
    monkeypatch.setattr(catmod, "_prod_source", lambda: False)
    monkeypatch.setattr(catmod, "_sync_repo_async", lambda: None)
    cat_file = tmp_path / "isolation-catalog.json"
    cat_file.write_text(json.dumps({"products": [], "deleted": []}))
    monkeypatch.setattr(catmod, "CATALOG_FILE", str(cat_file))
    application = appmod.create_app()
    application.config.update(TESTING=True)
    with application.test_client() as c:
        r = c.post("/api/admin/login", json={"email": EMAIL, "password": PW})
        assert r.status_code == 200, r.data
        yield c


def test_trashed_product_cannot_be_broadcast_even_by_a_stale_tab(client):
    csrf = client.get("/api/admin/session").get_json()["csrf"]
    saved = client.post("/api/admin/products", headers={"X-CSRF-Token": csrf}, json={
        "product": {"id": PID, "name": "Broadcast Isolation", "sku": "ISO-1",
                    "category": "accessories", "priceNgn": 5000,
                    "stock_quantity": 2, "online": True,
                    "image": "images/products/_placeholder.jpg"},
    })
    assert saved.status_code == 200, saved.data
    trashed = client.delete(f"/api/admin/products/{PID}",
                            headers={"X-CSRF-Token": csrf})
    assert trashed.status_code == 200, trashed.data
    assert trashed.get_json()["deleteMode"] == "trash"

    # The broadcast queue resolves products through the LIVE catalogue only:
    # a trashed id is unavailable even to an admin tab opened before the
    # delete, so a removed piece can never be re-advertised.
    refused = client.post("/api/admin/marketing/broadcast",
                          headers={"X-CSRF-Token": csrf},
                          json={"kind": "promo", "subject": "Test",
                                "content": "Hello",
                                "productIds": [PID]})
    assert refused.status_code == 400, refused.data
    assert "unavailable" in refused.get_json()["error"]

    # ...and the trash bin itself still holds the record for the owner.
    listing = client.get("/api/admin/products/trash").get_json()
    assert PID in {row["id"] for row in listing["items"]}


def test_normalized_rows_never_carry_a_stale_deleted_flag_into_a_save():
    """A trash payload re-saved after a restore is a LIVE product: normalize()
    has no is_deleted output key, so the full-save path cannot re-tombstone
    the row it is writing."""
    clean = catmod.normalize({"name": "Restored piece", "priceNgn": 900,
                              "is_deleted": True, "deleted": True})
    assert "is_deleted" not in clean
    assert "deleted" not in clean
    assert not catmod.is_deleted_product(clean)
