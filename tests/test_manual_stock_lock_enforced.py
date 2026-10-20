"""A manual Out-of-stock lock survives every automated writer.

Directive 2026-10-19: a product the owner switches to "Out of stock" in the
admin must stay locked until the owner puts stock back - across supplier
ticks, catalogue re-indexes, mirror restores and redeploys. The lock is the
union of the catalogue-file mirror and the durable growth_settings row, and
every read/write surface has to honour it.

Run with: python3 -m pytest tests/test_manual_stock_lock_enforced.py -q
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_lock_test.db")


@pytest.fixture()
def isolated_catalog(tmp_path, monkeypatch):
    import catalog
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"products": [], "deleted": [], "updatedAt": "",
                                "updatedBy": ""}), encoding="utf-8")
    monkeypatch.setattr(catalog, "CATALOG_FILE", str(path))
    monkeypatch.setattr(catalog, "_stock_lock_cache", {"at": 0.0, "locks": None})
    # The durable Supabase half is a no-op in the test environment.
    yield catalog


def test_the_storefront_never_publishes_a_locked_product_as_buyable(monkeypatch):
    import api
    import catalog
    monkeypatch.setattr(catalog, "is_stock_locked", lambda pid: str(pid) == "wix-001")
    row = {"id": "wix-001", "stock": 50, "optionStock": {"Red": 20, "Blue": 30}}
    out = api._public_product(row)
    assert out["stock_available"] == 0
    assert out["stock_status"] == "out"
    assert out["option_stock_available"] == {"Red": 0, "Blue": 0}
    assert out["option_stock_status"] == {"Red": "out", "Blue": "out"}


def test_checkout_availability_is_zero_while_locked(monkeypatch):
    import api
    import catalog
    monkeypatch.setattr(catalog, "is_stock_locked", lambda pid: str(pid) == "wix-001")
    assert api._stock_available({"id": "wix-001", "stock": 50}, "__default__") == 0
    assert api._stock_available(
        {"id": "wix-001", "stock": 50, "optionStock": {"Red": 20}}, "Color: Red") == 0


def test_the_supplier_write_path_refuses_a_locked_product(monkeypatch):
    import catalog
    monkeypatch.setattr(catalog, "is_stock_locked", lambda pid: True)
    row, action, mirrored = catalog.apply_supplier_stock("wix-001", 50)
    assert row is None
    assert action == "locked-out"
    assert mirrored is True


def test_the_lock_round_trips_and_only_an_owner_release_clears_it(isolated_catalog):
    catalog = isolated_catalog
    assert catalog.remember_manual_stock_lock("wix-001", actor="owner",
                                              why="admin set Out of stock") is True
    catalog._stock_lock_cache = {"at": 0.0, "locks": None}
    assert catalog.is_stock_locked("wix-001") is True
    # Re-reading the file after a simulated redeploy still sees the lock.
    catalog._stock_lock_cache = {"at": 0.0, "locks": None}
    assert catalog.manual_stock_locks(refresh=True)["wix-001"]["why"] == \
        "admin set Out of stock"
    assert catalog.forget_manual_stock_lock("wix-001", actor="owner") is True
    catalog._stock_lock_cache = {"at": 0.0, "locks": None}
    assert catalog.is_stock_locked("wix-001") is False


def test_a_price_or_photo_save_does_not_change_the_lock(isolated_catalog, monkeypatch):
    catalog = isolated_catalog
    catalog.remember_manual_stock_lock("wix-001", actor="owner")
    calls = []
    monkeypatch.setattr(catalog, "remember_manual_stock_lock",
                        lambda pid, **kw: calls.append(("lock", pid)) or True)
    monkeypatch.setattr(catalog, "forget_manual_stock_lock",
                        lambda pid, **kw: calls.append(("unlock", pid)) or True)
    # No availability opinion: a price/photo save carries the already-zero
    # shelf, with no explicit "out" flag and no positive quantity.
    catalog._settle_manual_stock_lock({"id": "wix-001", "stock": 0},
                                      {"id": "wix-001", "stock": 0})
    assert calls == []
