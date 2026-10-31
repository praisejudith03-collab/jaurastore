"""Two linked changes, pinned together.

1. ``public.products.is_deleted`` (the Trash Bin flag) is added by a
   migration, and the product save handler survives a table that does not
   have it yet, without ever reviving a trashed piece.

2. The Supplier Alerts list and its owner emails cover only items that are
   ACTIVE on the website and IN STOCK there. Anything the owner has already
   hidden, trashed or set to zero is left out, and an old pending alert for
   such an item is closed without an email.

Run with:  python3 -m pytest tests/test_is_deleted_migration_and_supplier_alert_scope.py -q
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

from _pw import PW  # noqa: E402
import app  # noqa: E402
import catalog  # noqa: E402
import supabase_store  # noqa: E402
import supplier_alerts  # noqa: E402
import supplier_watchdog  # noqa: E402

MIGRATION = ROOT / "migrations" / "20261031_products_is_deleted.sql"


# ------------------------------------------------------------ fake Supabase
class _Res:
    def __init__(self, data):
        self.data = data


class _Products:
    """One products table. Rejects any column listed in ``missing``, the way
    PostgREST does: a PGRST204 schema-cache message, or the raw Postgres 42703."""

    def __init__(self, fake):
        self.fake = fake
        self._mode = None
        self._patch = None
        self._id = None

    def _reject(self, keys):
        for key in keys:
            if key in self.fake.missing:
                if self.fake.style == "pg":
                    raise Exception(f'column "{key}" of relation "products" does not exist')
                raise Exception(f"Could not find the '{key}' column of 'products' "
                                "in the schema cache (PGRST204)")

    def upsert(self, rows):
        rows = rows if isinstance(rows, list) else [rows]
        for row in rows:
            self._reject(row.keys())
        for row in rows:
            self.fake.rows[str(row["id"])] = dict(row)
        self._mode = "upsert"
        return self

    def update(self, patch):
        self._reject(patch.keys())
        self._mode, self._patch = "update", dict(patch)
        return self

    def eq(self, _key, value):
        self._id = str(value)
        return self

    def select(self, *_a, **_k):
        return self

    def execute(self):
        if self._mode == "update":
            row = self.fake.rows.get(self._id)
            if row is None:
                return _Res([])
            row.update(self._patch)
            return _Res([dict(row)])
        return _Res([])


class _Client:
    def __init__(self, missing=(), style="postgrest"):
        self.missing = set(missing)
        self.style = style
        self.rows = {}

    def table(self, name):
        assert name == "products", name
        return _Products(self)


def _product_row(pid="p-1", **over):
    row = {"id": pid, "name": f"Piece {pid}", "priceNgn": 10000, "priceCfa": 4400,
           "stock": 7, "stock_quantity": 7, "image": "", "image_url": "",
           "category": "beauty", "online": True, "source": "admin"}
    row.update(over)
    return row


@pytest.fixture()
def live_supabase(monkeypatch):
    """upsert_products is a no-op unless Supabase is enabled; turn it on."""
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)


@pytest.fixture()
def schema_warnings(monkeypatch):
    """Capture the durable products-schema warnings instead of writing them."""
    saved = []
    monkeypatch.setattr(supabase_store, "load_supplier_sync_warnings", lambda: list(saved))
    monkeypatch.setattr(supabase_store, "save_supplier_sync_warnings",
                        lambda w: saved.__setitem__(slice(None), list(w)) or True)
    monkeypatch.setattr(supabase_store, "_OPTIONAL_COLUMN_NOTED", set())
    return saved


# ============================================================ 1. migration
def test_migration_file_carries_the_requested_statement():
    text = MIGRATION.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines()
                     if not line.strip().startswith("--"))
    normalised = " ".join(code.split()).lower()
    assert ("alter table public.products add column if not exists "
            "is_deleted boolean default false;") in normalised


def test_migration_is_additive_only_and_parses_as_postgres_sql():
    pglast = pytest.importorskip("pglast")
    text = MIGRATION.read_text(encoding="utf-8")
    pglast.parse_sql(text)                  # raises on a syntax error
    code = " ".join(line for line in text.splitlines()
                    if not line.strip().startswith("--")).lower()
    assert "if not exists" in code           # re-running is a no-op
    for forbidden in (" update ", " delete ", " drop ", " insert "):
        assert forbidden not in f" {code} ", forbidden   # no row is touched


# ====================================================== 1b. save handler
def test_save_goes_through_when_is_deleted_column_is_absent(
        monkeypatch, schema_warnings, live_supabase):
    fake = _Client(missing={"is_deleted"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    assert supabase_store.upsert_products([_product_row("p-save")]) is True
    stored = fake.rows["p-save"]
    assert stored["name"] == "Piece p-save" and stored["stock"] == 7
    assert "is_deleted" not in stored
    assert supabase_store.last_write_blocker() == ""
    assert schema_warnings and schema_warnings[-1]["code"] == "products_table_optional_columns_missing"
    assert "is_deleted" in schema_warnings[-1]["reason"]


def test_the_optional_warning_is_recorded_once_per_process(
        monkeypatch, schema_warnings, live_supabase):
    fake = _Client(missing={"is_deleted"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    calls = []
    monkeypatch.setattr(supabase_store, "save_supplier_sync_warnings",
                        lambda w: calls.append(list(w)) or True)
    for pid in ("p-a", "p-b", "p-c"):
        assert supabase_store.upsert_products([_product_row(pid)]) is True
    assert len(calls) == 1


def test_the_postgres_42703_spelling_is_handled_the_same_way(
        monkeypatch, schema_warnings, live_supabase):
    fake = _Client(missing={"is_deleted"}, style="pg")
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    assert supabase_store.upsert_products([_product_row("p-pg")]) is True
    assert "is_deleted" not in fake.rows["p-pg"]


def test_a_truthy_is_deleted_is_never_silently_dropped(monkeypatch, schema_warnings):
    """A tombstone with no column to hold it must refuse, never revive."""
    fake = _Client(missing={"is_deleted"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    row = _product_row("p-trash", is_deleted=True)
    assert supabase_store._upsert_products_resilient([row]) is False
    assert fake.rows == {}
    assert "is_deleted" in supabase_store.last_write_blocker()


def test_any_other_missing_column_still_refuses_the_save(monkeypatch, schema_warnings):
    fake = _Client(missing={"is_deleted", "compareCfa"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    row = _product_row("p-narrow", compareCfa=5000, is_deleted=False)
    assert supabase_store._upsert_products_resilient([row]) is False
    assert fake.rows == {}


def test_a_field_update_survives_a_missing_is_deleted_column(monkeypatch, schema_warnings):
    fake = _Client(missing={"is_deleted"})
    fake.rows["p-upd"] = {"id": "p-upd", "name": "Old", "online": False,
                          "is_active": False, "source": "admin", "stock": 3}
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    outcome, row = supabase_store.update_product_fields(
        {"id": "p-upd", "name": "Old", "online": True, "stock": 3}, ["online"])
    assert outcome == "updated"
    assert row["online"] is True and row["is_active"] is True
    assert "is_deleted" not in fake.rows["p-upd"]


def test_missing_column_name_reads_both_spellings():
    assert supabase_store._missing_column_name(
        Exception("Could not find the 'is_deleted' column of 'products' (PGRST204)")) == "is_deleted"
    assert supabase_store._missing_column_name(
        Exception('column "is_deleted" of relation "products" does not exist')) == "is_deleted"
    assert supabase_store._missing_column_name(
        Exception("column is_deleted does not exist")) == "is_deleted"
    assert supabase_store._missing_column_name(
        Exception("duplicate key value violates unique constraint")) == ""
    assert supabase_store._missing_column_name(None) == ""


# =========================================== 2. sellable (active + in stock)
@pytest.mark.parametrize("row, option, expected", [
    ({"id": "a", "stock_quantity": 3, "online": True}, "", True),
    ({"id": "a", "stock_quantity": 0, "online": True}, "", False),       # zeroed in shop
    ({"id": "a", "stock_quantity": 3, "online": False}, "", False),      # hidden
    ({"id": "a", "stock_quantity": 3, "online": True, "is_deleted": True}, "", False),
    ({"id": "a", "stock_quantity": 3, "online": True, "source": "deleted"}, "", False),
    ({"id": "a", "stock_quantity": 3, "online": True, "active": False}, "", False),
    ({"id": "", "stock_quantity": 3, "online": True}, "", False),       # no id
    (None, "", False),                                                  # no row
    ({"id": "a", "stock": 5, "online": True}, "", True),                # legacy alias
    ({"id": "a", "stock_quantity": 6, "online": True,
      "optionStock": {"Red": 0, "Blue": 6}}, "Red", False),             # variant zeroed
    ({"id": "a", "stock_quantity": 6, "online": True,
      "optionStock": {"Red": 0, "Blue": 6}}, "Blue", True),
    ({"id": "a", "stock_quantity": 6, "online": True,
      "optionStock": {"Red": 0, "Blue": 6}}, "", True),
    ({"id": "a", "stock_quantity": 0, "online": True,
      "optionStock": {"Red": 4}}, "Red", False),                        # total is zero
])
def test_sellable_means_active_and_in_stock(row, option, expected):
    assert catalog.supplier_alert_sellable(row, option) is expected


# ======================================================= 2b. the list
@pytest.fixture()
def admin_client():
    application = app.create_app()
    application.config.update(TESTING=True)
    with application.test_client() as http:
        yield http


def _admin_login(client):
    token = client.get("/api/admin/session").get_json()["csrf"]
    response = client.post("/api/admin/login",
                           json={"email": "jaurastore@gmail.com", "password": PW},
                           headers={"X-CSRF-Token": token})
    assert response.status_code == 200, response.get_json()


def test_the_list_shows_only_live_in_stock_items_and_counts_the_rest(
        admin_client, monkeypatch):
    _admin_login(admin_client)
    rows = [
        {"id": "keep-out", "name": "Keep out", "supplier_status": "out_of_stock",
         "stock_quantity": 3, "online": True, "priceNgn": 1000},
        {"id": "keep-low", "name": "Keep low", "supplier_status": "low_stock",
         "stock_quantity": 8, "online": True, "priceNgn": 1000,
         "optionStock": {"Red": 0, "Blue": 8}},
        {"id": "zero", "name": "Zeroed", "supplier_status": "low_stock",
         "stock_quantity": 0, "online": True},
        {"id": "hidden", "name": "Hidden", "supplier_status": "out_of_stock",
         "stock_quantity": 4, "online": False},
        {"id": "trashed", "name": "Trashed", "supplier_status": "out_of_stock",
         "stock_quantity": 4, "online": True, "is_deleted": True},
        {"id": "gone-soft", "name": "Soft gone", "supplier_status": "low_stock",
         "stock_quantity": 4, "online": True, "source": "deleted"},
        {"id": "fine", "name": "Fine", "supplier_status": "ok", "stock_quantity": 9},
    ]
    monkeypatch.setattr(supabase_store, "products_table_rows", lambda: rows)
    body = admin_client.get("/api/admin/supplier-alerts").get_json()
    assert body["ok"] is True
    assert {a["id"] for a in body["alerts"]} == {"keep-out", "keep-low"}
    assert body["counts"] == {"total": 2, "outOfStock": 1, "lowStock": 1, "excluded": 4}
    assert all(a["stock"] > 0 for a in body["alerts"])


# ======================================================= 2c. the drain
def _store(state=None):
    holder = {"state": state if state is not None else {}}
    return holder, (lambda: holder["state"]), (lambda s: holder.update(state=s) or True)


def _obs(pid, qty, name="Piece"):
    return {"product_id": pid, "product_name": name, "sku": "", "option": "",
            "supplier_qty": qty, "flagged": False, "shop_stock": 7}


def _listing(pid, stock, online=True, **over):
    row = {"id": pid, "name": f"Name {pid}", "stock_quantity": stock, "stock": stock,
           "online": online}
    row.update(over)
    return row


def test_drain_emails_only_items_still_selling(monkeypatch):
    holder, load, save = _store()
    supplier_alerts.record_observations(
        [_obs("jau-live", 0, "Live piece"), _obs("jau-zero", 0, "Zeroed piece"),
         _obs("jau-hidden", 0, "Hidden piece")], load, save)
    monkeypatch.setattr(catalog, "supplier_alert_rows", lambda: [
        _listing("jau-live", 7), _listing("jau-zero", 0),
        _listing("jau-hidden", 7, online=False)])
    sent = []
    out = supplier_alerts.drain_pending_alerts(
        send=lambda alerts: (sent.extend(alerts), (True, "ok"))[1], load=load, save=save)
    assert [a["product_id"] for a in sent] == ["jau-live"]
    assert out["sent"] is True and out["count"] == 1
    assert supplier_alerts.pending_alerts(holder["state"]) == []
    closed = holder["state"]["entries"]["jau-zero::"]
    assert closed["pending"] is False and closed["emailedLevel"] == "out"
    assert "handledAt" in closed


def test_an_already_handled_item_is_not_mailed_again_after_a_restock(monkeypatch):
    holder, load, save = _store()
    supplier_alerts.record_observations([_obs("jau-z", 0)], load, save)
    monkeypatch.setattr(catalog, "supplier_alert_rows", lambda: [_listing("jau-z", 0)])
    sent = []
    send = lambda alerts: (sent.extend(alerts), (True, "ok"))[1]  # noqa: E731
    supplier_alerts.drain_pending_alerts(send=send, load=load, save=save)
    assert sent == []

    # Owner restocks the piece; the supplier is still out. That is not a new
    # change, so nothing is queued.
    monkeypatch.setattr(catalog, "supplier_alert_rows", lambda: [_listing("jau-z", 5)])
    assert supplier_alerts.record_observations([_obs("jau-z", 0)], load, save)["pending"] == 0
    # The supplier easing from out to low is not news either.
    assert supplier_alerts.record_observations([_obs("jau-z", 2)], load, save)["pending"] == 0


def test_a_drop_on_a_live_item_still_alerts_after_a_handled_one(monkeypatch):
    holder, load, save = _store()
    supplier_alerts.record_observations([_obs("jau-x", 0)], load, save)
    monkeypatch.setattr(catalog, "supplier_alert_rows", lambda: [_listing("jau-x", 0)])
    supplier_alerts.drain_pending_alerts(send=lambda a: (True, "ok"), load=load, save=save)
    # The supplier comes back into stock: the watchdog reads it as ok, which
    # clears the old entry. The owner restocks the piece on the site.
    supplier_alerts.record_observations([_obs("jau-x", 40)], load, save)
    monkeypatch.setattr(catalog, "supplier_alert_rows", lambda: [_listing("jau-x", 5)])
    # Then the supplier drops to 2 units: a fresh low change on an active item.
    supplier_alerts.record_observations([_obs("jau-x", 2)], load, save)
    assert [a["level"] for a in supplier_alerts.pending_alerts(holder["state"])] == ["low"]


def test_an_unreadable_catalogue_sends_nothing(monkeypatch):
    holder, load, save = _store()
    supplier_alerts.record_observations([_obs("jau-u", 0)], load, save)

    def broken():
        raise RuntimeError("catalogue offline")
    monkeypatch.setattr(catalog, "supplier_alert_rows", broken)
    sent = []
    out = supplier_alerts.drain_pending_alerts(
        send=lambda alerts: (sent.extend(alerts), (True, "ok"))[1], load=load, save=save)
    assert sent == [] and out["sent"] is False
    assert len(supplier_alerts.pending_alerts(holder["state"])) == 1


# ================================================= 2d. the watchdog
def _patch_fresh_listing(monkeypatch, rows):
    monkeypatch.setattr(catalog, "_prod_source", lambda: False)
    monkeypatch.setattr(catalog, "merged", lambda include_hidden=False: rows)


def _capture_observations(monkeypatch):
    seen = []
    monkeypatch.setattr(supplier_alerts, "record_observations",
                        lambda obs, *a, **k: seen.extend(obs) or {"recorded": len(obs)})
    return seen


def test_watchdog_uses_the_fresh_listing_not_the_stale_candidate(monkeypatch):
    """The candidate was picked minutes ago with stock 5. The owner has since
    zeroed it, so the reading must not be queued for email."""
    stale = {"id": "jau-w", "name": "Bag", "stock": 5, "stock_quantity": 5}
    _patch_fresh_listing(monkeypatch, [_listing("jau-w", 0, name="Bag")])
    seen = _capture_observations(monkeypatch)
    supplier_watchdog._record_stock_alerts(stale, [("", 0, False)])
    assert seen == []


def test_watchdog_queues_a_reading_for_a_live_in_stock_item(monkeypatch):
    stale = {"id": "jau-w2", "name": "Bag", "stock": 1, "stock_quantity": 1}
    _patch_fresh_listing(monkeypatch, [_listing("jau-w2", 9, name="Bag")])
    seen = _capture_observations(monkeypatch)
    supplier_watchdog._record_stock_alerts(stale, [("", 0, False)])
    assert len(seen) == 1
    assert seen[0]["product_id"] == "jau-w2" and seen[0]["shop_stock"] == 9


def test_watchdog_skips_only_the_zeroed_variant(monkeypatch):
    listing = _listing("jau-v", 6, optionStock={"Red": 0, "Blue": 6})
    _patch_fresh_listing(monkeypatch, [listing])
    seen = _capture_observations(monkeypatch)
    supplier_watchdog._record_stock_alerts(
        {"id": "jau-v", "name": "Scarf"},
        [("Red", 0, False), ("Blue", 1, False)])
    assert [(o["option"], o["supplier_qty"]) for o in seen] == [("Blue", 1)]


def test_watchdog_queues_nothing_for_a_deleted_product(monkeypatch):
    _patch_fresh_listing(monkeypatch, [])
    seen = _capture_observations(monkeypatch)
    supplier_watchdog._record_stock_alerts({"id": "jau-gone", "name": "Gone"},
                                           [("", 0, False)])
    assert seen == []


# ======================================= 2e. dashboard supplier queues
def test_dashboard_supplier_queues_match_the_alerts_page_scope(admin_client, monkeypatch):
    """The dashboard's supplier queues use the same rule as Supplier Alerts:
    a supplier low/out only counts for a variant or product the shop is still
    selling. Handled (zeroed) variants and items are left out."""
    import api as api_mod
    _admin_login(admin_client)
    products = {
        "a": {"id": "a", "name": "Live low", "supplier_status": "low_stock",
              "stock_quantity": 3, "online": True},
        "b": {"id": "b", "name": "Zeroed out", "supplier_status": "out_of_stock",
              "stock_quantity": 0, "online": True},
        "c": {"id": "c", "name": "Colour split", "supplier_status": "out_of_stock",
              "stock_quantity": 6, "online": True,
              "optionStock": {"Red": 0, "Blue": 6}},
        "d": {"id": "d", "name": "Fine", "supplier_status": "ok",
              "stock_quantity": 2, "online": True},
    }
    stock_rows = [
        {"product_id": "a", "variant_key": "__default__", "variant_label": "Default",
         "qty": 3, "low_threshold": 5, "manual_out": False},
        {"product_id": "b", "variant_key": "__default__", "variant_label": "Default",
         "qty": 0, "low_threshold": 5, "manual_out": True},
        {"product_id": "c", "variant_key": "Red", "variant_label": "Red",
         "qty": 0, "low_threshold": 5, "manual_out": True},
        {"product_id": "c", "variant_key": "Blue", "variant_label": "Blue",
         "qty": 6, "low_threshold": 5, "manual_out": False},
        {"product_id": "d", "variant_key": "__default__", "variant_label": "Default",
         "qty": 2, "low_threshold": 5, "manual_out": False},
    ]
    monkeypatch.setattr(api_mod, "_stock_rows_for_read", lambda: stock_rows)
    monkeypatch.setattr(api_mod, "_merged_products_cached", lambda: list(products.values()))
    monkeypatch.setattr(api_mod, "query", lambda *a, **k: [])
    monkeypatch.setattr(supabase_store, "load_supplier_sync_warnings", lambda: [])
    body = admin_client.get("/api/admin/needs-attention").get_json()
    assert body["ok"] is True
    low = [(r["product_id"], r["variant_key"]) for r in body["supplierLowStock"]]
    out = [(r["product_id"], r["variant_key"]) for r in body["supplierOutOfStock"]]
    assert low == [("a", "__default__")]
    assert out == [("c", "Blue")]          # the zeroed Red variant is excluded
    assert body["counts"]["supplierLowStock"] == 1
    assert body["counts"]["supplierOutOfStock"] == 1
