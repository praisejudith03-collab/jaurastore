"""Regression pins for the 2026-10-28 disappearing-media / stuck-admin report.

Three separate defects were behind it, and each one is pinned here:

1. THE WRITER REJECTED THE SHOP'S OWN COVERS.
   `supabase_store._upsert_products_resilient` runs the public-URL contract on
   every production write. It accepted only an absolute
   `<project>/storage/v1/object/public/uploads/...` URL, so a product whose
   cover is a committed repository photo (`images/products/x.jpg` - what
   `catalog.resolve_image` serves, what `catalog_health` counts as valid and
   what the whole bundled catalogue uses) could never be saved. The Admin
   Hide/Show toggle posts the WHOLE product, so un-hiding one of those rows
   answered 503 "The product could not be saved". The same validation silently
   DROPPED every committed photo from the gallery on a successful save.

2. THE NIGHTLY STORAGE SWEEPER COULD NOT SEE TWO CLASSES OF REFERENCE.
   Its scan reads products through `catalog.merged()`, which hides Trash-Bin
   rows, and it read categories only from the local `data/categories.json`
   (shipped artwork) instead of the Supabase table production serves. Uploaded
   category covers and the photos of trashed (restorable) products therefore
   looked like orphans and were deleted after the two-day grace window.

3. THE BOOT MIGRATION OVERRODE THE OWNER.
   `auto_migrate.PRODUCT_SCHEMA_STATEMENT` ended with an UPDATE that set
   `is_active/online = true` and `is_deleted = false` for the whole catalogue
   on every boot, so a restart re-published hidden products and emptied the
   Trash behind the portal's back.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auto_migrate
import catalog
import db
import storage
import storage_cleanup
import supabase_settings
import supabase_store
from config import Config

ORIGIN = "https://regression.supabase.co"
REPO_COVER = "images/products/10in1-raf-sandwich-maker.jpg"   # a real shipped file
CATEGORY_COVER = f"{ORIGIN}/storage/v1/object/public/uploads/categories/2026/10/kitchen.jpg"
TRASH_COVER = f"{ORIGIN}/storage/v1/object/public/uploads/products/2026/10/trashed.jpg"


# --------------------------------------------------------------------- helpers
class _Row(dict):
    """A sqlite3-style row: `"key" in row` and `row["key"]` both work."""


class _Table:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def or_(self, *_a, **_k):
        return self

    def upsert(self, rows, **_k):
        self._rows = list(rows)
        return self

    def update(self, patch):
        self._rows = [dict(r, **patch) for r in self._rows]
        return self

    def execute(self):
        return {"data": list(self._rows)}


class _Client:
    def __init__(self, tables=None):
        self.tables = dict(tables or {})
        self.upserts = []

    def table(self, name):
        if name == "products":
            return _ProductsTable(self)
        return _Table(self.tables.get(name, []))

    def rpc(self, *_a, **_k):
        raise RuntimeError("function does not exist (PGRST202)")

    def from_(self, name):            # storage bucket surface
        return self


class _ProductsTable(_Table):
    def __init__(self, owner):
        super().__init__(owner.tables.setdefault("products", []))
        self.owner = owner

    def upsert(self, rows, **_k):
        self.owner.upserts.append([dict(r) for r in rows])
        return super().upsert(rows)

    def execute(self):
        self.owner.tables["products"] = list(self._rows)
        return {"data": list(self._rows)}


@pytest.fixture()
def production(monkeypatch):
    """A live-Supabase shaped deployment: production env + a fake client."""
    client = _Client()
    monkeypatch.setattr(Config, "ENV", "production")
    monkeypatch.setattr(Config, "SUPABASE_URL", ORIGIN)
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    monkeypatch.setattr(Config, "UPLOAD_MODE", "supabase")
    monkeypatch.setattr(supabase_store, "client", lambda: client)
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    return client


# ------------------------------------------------- 1. the production writer
def test_a_committed_repo_cover_is_a_durable_cover(production):
    """The bundled catalogue's own photos must pass the production contract."""
    assert supabase_store._public_image_url_error(REPO_COVER)       # not a bucket URL
    assert supabase_store._canonical_catalog_image_url(REPO_COVER) == (REPO_COVER, "")


def test_a_product_with_a_repo_cover_can_be_saved_on_the_live_shop(production):
    """This is the 503 on Hide/Show: the whole row is posted, cover included."""
    ok = supabase_store._upsert_products_resilient([{
        "id": "wix-003", "name": "Sandwich maker", "priceNgn": 69850,
        "image": REPO_COVER, "image_url": REPO_COVER, "online": True,
    }])
    assert ok is True
    assert production.upserts[-1][0]["image_url"] == REPO_COVER


def test_a_made_up_relative_path_is_still_refused(production):
    """The relaxation must not re-open the ephemeral-path hole."""
    assert supabase_store._canonical_catalog_image_url("images/categories/temporary.jpg")[1]
    assert supabase_store._canonical_catalog_image_url("/uploads/products/x.jpg")[1]
    assert supabase_store._upsert_products_resilient([{
        "id": "no-temp", "name": "x", "image": "/uploads/products/x.jpg",
        "image_url": "/uploads/products/x.jpg",
    }]) is False
    assert "cover" in supabase_store.last_write_blocker()


def test_a_successful_save_keeps_committed_photos_in_the_gallery(production):
    """Every gallery photo used to be dropped unless it was a bucket URL."""
    bucket = f"{ORIGIN}/storage/v1/object/public/uploads/products/wix-004.jpg"
    ok = supabase_store._upsert_products_resilient([{
        "id": "wix-004", "name": "Pot set",
        "image": bucket, "image_url": bucket,
        "images": [bucket, REPO_COVER, "https://someone-else.example/steal.jpg"],
    }])
    assert ok is True
    assert production.upserts[-1][0]["images"] == [bucket, REPO_COVER]


# ------------------------------------------- 2. the nightly reference scan
def test_an_uploaded_category_cover_is_not_an_orphan(production, monkeypatch):
    """Production serves the Supabase table, not the shipped JSON file."""
    production.tables["categories"] = [{
        "id": "household", "name": "Household", "image_url": CATEGORY_COVER,
    }]
    monkeypatch.setattr(catalog, "merged", lambda **_k: [])
    monkeypatch.setattr(db, "query", lambda *_a, **_k: [])
    monkeypatch.setattr(supabase_settings, "enabled", lambda: False)

    keys, sources = storage_cleanup._referenced_keys()
    assert "categories/2026/10/kitchen.jpg" in keys
    assert sources["categories"] >= 1


def test_a_trashed_product_keeps_its_photo(production, monkeypatch):
    """Restore must not hand back a product with a dead photo."""
    monkeypatch.setattr(supabase_store, "load_product_trash",
                        lambda: [{"id": "gone-1", "image": TRASH_COVER,
                                  "payload": {"images": [TRASH_COVER]}}])
    monkeypatch.setattr(catalog, "merged", lambda **_k: [])
    monkeypatch.setattr(db, "query", lambda *_a, **_k: [])
    monkeypatch.setattr(supabase_settings, "enabled", lambda: False)

    keys, sources = storage_cleanup._referenced_keys()
    assert "products/2026/10/trashed.jpg" in keys
    assert sources["trash"] >= 1

    plan = storage_cleanup.build_plan(
        min_age_days=0, referenced=keys,
        list_objects={"products/2026/10/trashed.jpg": {
            "key": "products/2026/10/trashed.jpg", "bytes": 1, "changed_at": None}})
    assert plan["delete_candidates"] == []


def test_an_unreadable_category_list_aborts_the_sweep(monkeypatch):
    """"I could not ask" must never mean "nobody shows it"."""
    monkeypatch.setattr(storage, "_live_category_rows", lambda: None)
    with pytest.raises(RuntimeError):
        storage_cleanup._category_rows()


def test_the_category_reader_unwraps_the_live_rows(monkeypatch):
    """_read_categories_file returns (data, path); the scan needs the rows."""
    monkeypatch.setattr(storage, "_live_category_rows",
                        lambda: [{"id": "bags", "image": CATEGORY_COVER}])
    assert storage_cleanup._category_rows() == [{"id": "bags", "image": CATEGORY_COVER}]


# --------------------------------------------- 3. the boot schema migration
def test_boot_migration_never_touches_availability():
    """A restart must not unhide a product or drain the Trash Bin."""
    sql = auto_migrate.PRODUCT_SCHEMA_STATEMENT.lower()
    assert "update public.products" not in sql
    assert "update products" not in sql
    assert "set is_active" not in sql
    assert "set is_deleted" not in sql
    assert "jaura.catalog_admin" not in sql
    # ...while the additive column work it exists for is still there.
    assert "alter table public.products add column if not exists" in sql


def test_the_full_boot_statement_list_contains_the_ddl_only_heal():
    statements = auto_migrate.statements()
    products_sql = "\n".join(s for s in statements if "public.products" in s)
    assert products_sql
    assert "update" not in products_sql.lower()
