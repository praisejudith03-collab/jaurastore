"""Master recovery + catalogue write-lock.

Three layers are pinned here, because each one failed on its own in production:

  1. the SQL recovery brings soft-deleted / unavailable rows back, and never
     resurrects a permanently deleted product;
  2. the guard reverts a background availability / cover / category write but
     still lets the admin editor save a full row and still lets the guarded
     RPCs do their job;
  3. the HTTP surface (health, recover, cover repair, supplier alerts,
     walk-in profit) reports what it did and requires an admin + CSRF.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

from _pw import PW  # noqa: E402

import app  # noqa: E402
import analytics as analytics_mod  # noqa: E402
import catalog  # noqa: E402
from config import Config  # noqa: E402
import supabase_store  # noqa: E402

RECOVERY_SQL = ROOT / "recover_unavailable_products.sql"
APPROVED_SECTIONS = ("01_products.sql", "09_product_compatibility.sql",
                     "06_growth_settings.sql", "08_categories.sql")


# --------------------------------------------------------------------------
# 1. static contract
# --------------------------------------------------------------------------
def test_the_recovery_sql_is_additive_and_guards_every_dangerous_column():
    sql = RECOVERY_SQL.read_text()
    code = "\n".join(line for line in sql.splitlines()
                     if not line.strip().startswith("--")).lower()
    assert "create temp table" in code
    for required in (
        "recover_unavailable_products",
        "admin_trash_products",
        "admin_restore_products",
        "admin_set_product_online",
        "products_catalog_guard_write",
        "products_catalog_guard_delete",
        "jaura.catalog_admin",
        "blocked_unavailable",
        "blocked_null",
    ):
        assert required in code, required
    # The one thing this script must never do to existing data.
    assert "drop table public.products" not in code
    assert "truncate" not in code
    assert "delete from public.products" not in code
    # `is_active` is restored AND locked.
    assert "add column if not exists is_active boolean not null default true" in code


def test_a_targeted_background_update_cannot_pass_as_a_full_admin_save():
    sql = RECOVERY_SQL.read_text()
    # The discriminator is a bumped updated_at, not just "the row looks full".
    assert "p_updated_at > p_previous_updated_at" in sql
    assert "old.updated_at" in sql
    # Tombstones are never written by a full-row save, only by the guarded RPC.
    assert "if old.is_deleted is not true and new.is_deleted is true then" in sql


# --------------------------------------------------------------------------
# 2. the SQL itself, on a disposable PostgreSQL
# --------------------------------------------------------------------------
class _Pg:
    def __init__(self):
        import pgserver
        from pgserver._commands import POSTGRES_BIN_PATH
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="jaura-recovery-pg-"))
        self.srv = pgserver.get_server(str(self.dir), cleanup_mode=None)
        self.psql = str(POSTGRES_BIN_PATH / "psql")
        self.uri = self.srv.get_uri()

    def close(self):
        try:
            self.srv.cleanup()
        finally:
            import shutil
            shutil.rmtree(self.dir, ignore_errors=True)

    def raw(self, sql):
        done = subprocess.run(
            [self.psql, self.uri, "-At", "-v", "ON_ERROR_STOP=1", "-f", "-"],
            input=sql, capture_output=True, text=True, timeout=180)
        return done.returncode, done.stdout.strip(), done.stderr.strip()

    def sql(self, sql):
        rc, out, err = self.raw(sql)
        if rc != 0:
            raise AssertionError(f"psql exited {rc}\n{err}")
        return out


@pytest.fixture(scope="module")
def pg():
    try:
        import pgserver  # noqa: F401
    except Exception:
        pytest.skip("pgserver is not installed")
    database = _Pg()
    try:
        for name in APPROVED_SECTIONS:
            database.sql((ROOT / "schema_sections" / name).read_text())
        database.sql((ROOT / "hard_delete_products.sql").read_text())
        database.sql(RECOVERY_SQL.read_text())
        database.sql("insert into categories (id,name) values ('beauty','Beauty');")
        yield database
    finally:
        database.close()


def _seed(pg, rows):
    values = ",\n".join(
        "('{id}','{name}',{ngn},{cfa},{stock},{stock},{online},'{source}',{deleted},"
        "{image},{image},'beauty','beauty')".format(
            id=r["id"], name=r["name"], ngn=r["ngn"], cfa=r["cfa"],
            stock=r["stock"], online="true" if r.get("online", True) else "false",
            source=r.get("source", "admin"),
            deleted="true" if r.get("deleted") else "false",
            image="NULL" if r.get("image") is None else f"'{r['image']}'")
        for r in rows)
    pg.sql("insert into public.products (id,name,\"priceNgn\",\"priceCfa\",stock_quantity,"
           "stock,online,source,is_deleted,image_url,image,category,category_id) values "
           + values + ";")


def _reset(pg, rows):
    """Wipe the fixture rows. The guard blocks a bare DELETE by design, so the
    harness signs in to the catalogue-admin context the RPCs use."""
    pg.sql("select set_config('jaura.catalog_admin', 'on', false);"
           "delete from public.products;"
           "delete from public.deleted_products;"
           "delete from public.product_guard_events;")
    _seed(pg, rows)


def test_recovery_restores_unavailable_rows_and_keeps_permanent_deletes(pg):
    _reset(pg, [
        {"id": "p-live", "name": "Live", "ngn": 1000, "cfa": 500, "stock": 5},
        {"id": "p-soft", "name": "Soft", "ngn": 2000, "cfa": 900, "stock": 3,
         "online": False, "source": "deleted", "deleted": True, "image": None},
        {"id": "p-src", "name": "Src", "ngn": 3000, "cfa": 1500, "stock": 2,
         "online": False, "source": "deleted", "deleted": True, "image": ""},
    ])
    pg.sql("select public.hard_delete_products(array['p-gone']);")
    report = json.loads(pg.sql(
        "select public.recover_unavailable_products('test')::text;"))
    assert report["ok"] is True
    assert report["restored"] == 2
    assert set(report["restoredIds"]) == {"p-soft", "p-src"}
    rows = {pid: flags.split("|") for pid, flags in
            (line.split("|", 1) for line in pg.sql(
                "select id, is_deleted, is_active, online, source from public.products;").splitlines())}
    assert rows["p-soft"] == ["f", "t", "t", "admin"]
    assert rows["p-src"] == ["f", "t", "t", "admin"]
    assert rows["p-live"] == ["f", "t", "t", "admin"]
    # The audit ledger explains what happened, field by field.
    assert int(pg.sql("select count(*) from public.product_guard_events where event='recovered';")) == 2


def test_recovery_repairs_an_empty_cover_from_the_gallery(pg):
    _reset(pg, [{"id": "p-gallery", "name": "Gallery", "ngn": 1500, "cfa": 700,
                 "stock": 1, "online": False, "deleted": True, "image": None}])
    pg.sql("update public.products set images = '[\"https://x.supabase.co/storage/v1/object/public/uploads/g.jpg\"]'::jsonb "
           "where id='p-gallery';")
    report = json.loads(pg.sql("select public.recover_unavailable_products('gallery')::text;"))
    assert report["repairedImages"] == 1
    assert pg.sql("select image_url from public.products where id='p-gallery';") \
        == "https://x.supabase.co/storage/v1/object/public/uploads/g.jpg"


def test_guard_reverts_a_background_flag_write_but_allows_the_full_admin_save(pg):
    _reset(pg, [{"id": "p-guard", "name": "Guard", "ngn": 4000, "cfa": 1800, "stock": 4}])
    cover = "https://x.supabase.co/storage/v1/object/public/uploads/g.jpg"
    pg.sql(f"update public.products set image_url='{cover}', image='{cover}' where id='p-guard';")

    # 1. A one-column ping: silently reverted, and logged.
    pg.sql("update public.products set online = false where id='p-guard';")
    assert pg.sql("select online from public.products where id='p-guard';") == "t"
    assert int(pg.sql("select count(*) from public.product_guard_events "
                      "where event='blocked_unavailable' and field='online';")) == 1

    # 2. The admin editor posts the whole row with a fresh timestamp: allowed.
    pg.sql("update public.products set online=false, source='admin', name='Guard', "
           "\"priceNgn\"=4000, updated_at=now(), image_url='%s' where id='p-guard';" % cover)
    assert pg.sql("select online from public.products where id='p-guard';") == "f"

    # 3. Blanking the cover is never allowed silently.
    pg.sql("update public.products set image_url = NULL where id='p-guard';")
    assert pg.sql("select image_url from public.products where id='p-guard';") == cover

    # 4. The guarded RPCs can do it, and say so in the ledger.
    pg.sql("select public.admin_set_product_online(array['p-guard'], true);")
    assert pg.sql("select online from public.products where id='p-guard';") == "t"
    pg.sql("select public.admin_trash_products(array['p-guard']);")
    assert pg.sql("select is_deleted from public.products where id='p-guard';") == "t"
    pg.sql("select public.admin_restore_products(array['p-guard']);")
    assert pg.sql("select is_deleted, online from public.products where id='p-guard';") == "f|t"


def test_guard_refuses_a_hand_delete_but_the_hard_delete_rpc_still_works(pg):
    _reset(pg, [{"id": "p-del", "name": "Del", "ngn": 900, "cfa": 400, "stock": 1}])
    rc, _out, err = pg.raw("delete from public.products where id='p-del';")
    assert rc != 0 and "catalogue admin functions" in err
    assert pg.sql("select count(*) from public.products where id='p-del';") == "1"
    assert pg.sql("select public.hard_delete_products(array['p-del']);") == "{p-del}"
    assert pg.sql("select count(*) from public.products;") == "0"


# --------------------------------------------------------------------------
# 3. HTTP surface
# --------------------------------------------------------------------------
@pytest.fixture()
def client():
    application = app.create_app()
    application.config.update(TESTING=True)
    with application.test_client() as http:
        yield http


def _csrf(client):
    return client.get("/api/admin/session").get_json()["csrf"]


def _login(client):
    token = _csrf(client)
    response = client.post("/api/admin/login",
                           json={"email": "jaurastore@gmail.com", "password": PW},
                           headers={"X-CSRF-Token": token})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["csrf"]


def test_recovery_endpoints_require_an_admin_and_csrf(client):
    assert client.get("/api/admin/catalog/health").status_code in (401, 403)
    assert client.post("/api/admin/catalog/recover", json={}).status_code in (401, 403)
    token = _login(client)
    # Signed in, but a cross-site POST without the token is refused.
    assert client.post("/api/admin/catalog/recover", json={}).status_code == 403
    assert client.post("/api/admin/catalog/recover", json={},
                       headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert token


def test_recovery_endpoint_reports_what_it_restored(client, monkeypatch):
    _login(client)
    calls = []

    def fake_recover(ids=None, reason="owner recovery"):
        calls.append((ids, reason))
        return {"ok": True, "restored": 2, "restoredIds": ["a", "b"],
                "skippedPermanent": 1, "repairedImages": 1,
                "prunedDeletedIds": 2, "method": "rpc"}

    monkeypatch.setattr(catalog, "recover_unavailable_products", fake_recover)
    monkeypatch.setattr(catalog, "catalog_health", lambda: {"ok": True, "unavailable": 0})
    token = client.get("/api/admin/session").get_json()["csrf"]
    response = client.post("/api/admin/catalog/recover", json={"ids": ["a", "b"]},
                           headers={"X-CSRF-Token": token})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["ok"] is True and body["report"]["restored"] == 2
    assert "2 products restored" in body["message"]
    assert calls and calls[0][0] == ["a", "b"]

    # A failed recovery is an error, never a silent "done".
    monkeypatch.setattr(catalog, "recover_unavailable_products",
                        lambda **kw: {"ok": False, "error": "database unreachable"})
    failed = client.post("/api/admin/catalog/recover", json={},
                         headers={"X-CSRF-Token": token})
    assert failed.status_code == 503
    assert "database unreachable" in failed.get_json()["error"]


def test_health_endpoint_explains_the_write_lock(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(catalog, "catalog_health", lambda: {
        "ok": True, "products": 258, "unavailable": 12, "softDeleted": 9,
        "inactive": 0, "hidden": 3, "missingCover": 4, "relativeCover": 7,
        "nonAbsoluteCover": 0, "missingCategory": 2, "writeLock": True})
    data = client.get("/api/admin/catalog/health").get_json()
    assert data["ok"] and data["health"]["unavailable"] == 12
    assert data["health"]["writeLock"] is True


def test_cover_repair_endpoint_rewrites_relative_urls(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(supabase_store, "repair_relative_image_urls", lambda: 3)
    token = client.get("/api/admin/session").get_json()["csrf"]
    body = client.post("/api/admin/catalog/repair-image-urls", json={},
                       headers={"X-CSRF-Token": token}).get_json()
    assert body["ok"] and body["fixed"] == 3
    assert "3 cover URLs" in body["message"]


def test_supplier_alerts_carry_everything_the_tab_renders(client, monkeypatch):
    _login(client)
    rows = [
        {"id": "p-out", "name": "Sold Out Bag", "image_url": "https://x/y.jpg",
         "supplierSku": "https://supplier.example/out", "supplier_status": "out_of_stock",
         "priceNgn": 12000, "priceCfa": 5300, "stock_quantity": 0, "online": True},
        {"id": "p-ok", "name": "Fine", "supplier_status": "ok", "priceNgn": 500},
        {"id": "p-low", "name": "Low", "supplier_status": "low_stock", "priceNgn": 800,
         "priceCfa": 350, "stock_quantity": 2},
    ]
    monkeypatch.setattr(supabase_store, "products_table_rows", lambda: rows)
    monkeypatch.setattr(catalog, "is_stock_locked", lambda pid: pid == "p-low")
    body = client.get("/api/admin/supplier-alerts").get_json()
    assert body["ok"] is True
    alerts = {a["id"]: a for a in body["alerts"]}
    assert set(alerts) == {"p-out", "p-low"}          # 'ok' rows are not alerts
    assert alerts["p-out"]["priceNgn"] == 12000
    assert alerts["p-out"]["supplierUrl"].startswith("https://supplier.example/")
    assert alerts["p-low"]["storeOutOfStock"] is True
    assert body["counts"] == {"total": 2, "outOfStock": 1, "lowStock": 1, "hidden": 1}


def test_walkin_sale_returns_revenue_and_net_profit(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(Config, "SUPABASE_ENABLED", False)
    monkeypatch.setattr(catalog, "merged", lambda **kw: [{
        "id": "wi-1", "name": "Walk Item", "online": True, "stock": 9,
        "stock_quantity": 9, "priceNgn": 5000, "priceCfa": 2200,
        "supplierCost": 2000, "supplierSku": "https://supplier.example/wi"}])
    monkeypatch.setattr(catalog, "reserve_stock", lambda pid, qty, **kw: True)
    monkeypatch.setattr(catalog, "release_stock", lambda *a, **k: True)
    import google_sheets
    monkeypatch.setattr(google_sheets, "push_orders", lambda orders: {"NGN": {"count": 1}})
    token = client.get("/api/admin/session").get_json()["csrf"]
    body = client.post("/api/admin/walk-in-sales", json={
        "customerName": "Walk In", "productId": "wi-1", "quantity": 2,
        "salePrice": 4500, "currency": "NGN", "paymentMethod": "cash",
    }, headers={"X-CSRF-Token": token}).get_json()
    import db
    try:
        assert body["ok"] is True
        assert body["revenue"] == 9000
        assert body["netProfit"] == 5000          # (4500 - 2000) * 2
        assert body["costMissing"] is False
        assert body["sheets"]["ok"] is True
    finally:
        db.execute("DELETE FROM orders WHERE id=?", (body.get("orderId"),))


def test_walkin_costing_uses_the_fractional_accounting_rate(client, monkeypatch):
    """The shop's rate is "NGN value of one FCFA" (0.44), not a whole number.

    Reading it as an int turned 0.44 into 0 -> 1, so a ₦2,000 supplier cost was
    reported as FCFA 2,000 instead of FCFA 4,545, and a CFA walk-in sale showed
    the wrong profit."""
    import accounting as accounting_mod
    _login(client)
    monkeypatch.setattr(Config, "SUPABASE_ENABLED", False)
    monkeypatch.setattr(accounting_mod, "current_exchange_rate", lambda: 0.44)
    monkeypatch.setattr(catalog, "merged", lambda **kw: [{
        "id": "wi-cfa", "name": "CFA Item", "online": True, "stock": 5,
        "stock_quantity": 5, "priceNgn": 9000, "priceCfa": 5000,
        "supplierCost": 2000}])
    monkeypatch.setattr(catalog, "reserve_stock", lambda *a, **k: True)
    monkeypatch.setattr(catalog, "release_stock", lambda *a, **k: True)
    import google_sheets
    monkeypatch.setattr(google_sheets, "push_orders", lambda orders: {"CFA": {"count": 1}})
    token = client.get("/api/admin/session").get_json()["csrf"]

    listed = client.get("/api/admin/walk-in/products").get_json()
    assert listed["exchangeRate"] == 0.44
    assert listed["products"][0]["supplierCost"] == 2000
    assert listed["products"][0]["supplierCostCfa"] == 4545     # 2000 / 0.44

    sold = client.post("/api/admin/walk-in-sales", json={
        "customerName": "CFA Buyer", "productId": "wi-cfa", "quantity": 2,
        "salePrice": 5000, "currency": "CFA", "paymentMethod": "cash",
    }, headers={"X-CSRF-Token": token}).get_json()
    assert sold["ok"] is True
    assert sold["currency"] == "CFA"
    assert sold["revenue"] == 10000
    # (5000 - 2000/0.44) per unit = 454.55, rounded once for the sale:
    # 909 in total. The bug would have produced 6000 (5000 - 2000 each).
    assert sold["netProfit"] == 909
    import db
    try:
        assert db.one("SELECT payload FROM orders WHERE id=?",
                      (sold["orderId"],)) is not None
    finally:
        db.execute("DELETE FROM orders WHERE id=?", (sold["orderId"],))


def test_sales_report_counts_net_profit_and_flags_missing_cost(client, monkeypatch):
    """Net profit = selling price - cost price - delivery fee, and a legacy
    order with no cost recorded is counted *and* flagged, never silently
    treated as pure profit. Scoped with `q=` so the shared orders table (which
    every other module also writes to) cannot skew the totals."""
    _login(client)
    import db

    def wipe():
        db.execute("DELETE FROM orders WHERE id IN ('JA-RCV-WI','JA-RCV-OLD')")

    wipe()
    columns = ("id,payload,email,customer_name,phone,country,city,zone,address,note,"
               "payment,proof_url,items_count,total,currency,source,status,at,updated_at")
    try:
        db.execute(f"INSERT INTO orders ({columns}) VALUES "
                   "('JA-RCV-WI',?,'','RecoveryCase Walk In','','NG','','','','','cash','',"
                   "2,9000,'NGN','walk-in','confirmed','2026-10-22T10:00:00','2026-10-22T10:00:00')",
                   (json.dumps({
                       "id": "JA-RCV-WI", "currency": "NGN", "total": 9000,
                       "customer": {"name": "RecoveryCase Walk In"}, "source": "walk-in",
                       "items": [{"id": "wi-1", "name": "RecoveryCase Item",
                                  "qty": 2, "price": 4500}],
                       "accounting": {"currency": "NGN", "exchangeRate": 0.45,
                                      "saleAmount": 9000, "supplierCostNgn": 4000,
                                      "supplierQty": 2, "supplierUnitPriceNgn": 2000,
                                      "deliveryExpense": 500, "discount": 0,
                                      "lineItems": [{"lineId": "line-01",
                                                     "productId": "wi-1",
                                                     "name": "RecoveryCase Item", "qty": 2,
                                                     "saleAmount": 9000,
                                                     "supplierCostNgn": 4000}]}}),))
        db.execute(f"INSERT INTO orders ({columns}) VALUES "
                   "('JA-RCV-OLD',?,'','RecoveryCase Old Buyer','','NG','','','','','transfer','',"
                   "1,3000,'NGN','checkout','confirmed','2026-10-20T10:00:00','2026-10-20T10:00:00')",
                   (json.dumps({"id": "JA-RCV-OLD", "currency": "NGN", "total": 3000,
                                "customer": {"name": "RecoveryCase Old Buyer"},
                                "items": [{"id": "x", "name": "RecoveryCase Legacy",
                                           "qty": 1, "price": 3000}]}),))
        data = client.get("/api/admin/sales?days=all&q=recoverycase").get_json()
        assert data["ok"] is True
        assert data["orders"] == 2
        ngn = next(b for b in data["profitByCurrency"] if b["currency"] == "NGN")
        assert ngn["revenue"] == 12000
        assert ngn["supplierCost"] == 4000
        assert ngn["delivery"] == 500
        # 9000 - 4000 - 500, plus the 3000 legacy sale whose cost is unknown.
        assert ngn["netProfit"] == 7500
        assert ngn["missingCost"] == 1
        assert data["netProfit"] == 7500
        assert data["ordersMissingCost"] == 1
        assert data["costComplete"] is False
        # A fully costed range says so, so the UI can hide the warning.
        clean = analytics_mod.sales_report("all", search="JA-RCV-WI")
        assert clean["ordersMissingCost"] == 0 and clean["costComplete"] is True
    finally:
        wipe()


def test_a_failed_catalogue_read_is_not_reported_as_a_missing_product(client, monkeypatch):
    """The exact toast Praise reported:
    "Photo update could not be saved. The previous saved photo is safe. That
    product is no longer available."  The row was still there - the database
    read had failed. A read failure must answer 503 "retry", never 404."""
    _login(client)

    def boom(*_a, **_k):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(catalog, "product_for_save", boom)
    token = client.get("/api/admin/session").get_json()["csrf"]
    response = client.put("/api/admin/products/jau-1/media",
                          json={"images": ["https://x.supabase.co/uploads/a.jpg"]},
                          headers={"X-CSRF-Token": token})
    assert response.status_code == 503, response.get_json()
    error = response.get_json()["error"]
    assert "no longer available" not in error.lower()
    assert "safe" in error.lower()

    # A clean read that genuinely finds nothing is still a 404 - and the media
    # route asks for the strict read, so the two cases cannot be confused.
    asked = []

    def missing_row(pid, strict=False):
        asked.append((pid, strict))
        return None

    monkeypatch.setattr(catalog, "product_for_save", missing_row)
    missing = client.put("/api/admin/products/jau-gone/media",
                         json={"images": ["https://x.supabase.co/uploads/a.jpg"]},
                         headers={"X-CSRF-Token": token})
    assert missing.status_code == 404
    assert "no longer available" in missing.get_json()["error"]
    assert asked == [("jau-gone", True)]


def test_recovery_prefers_the_sql_function_even_without_ids(monkeypatch):
    """The one-click "recover everything" button must run inside the guarded
    SQL transaction, not a series of unguarded REST updates."""
    class _Rpc:
        def __init__(self):
            self.calls = []

        def rpc(self, name, params):
            self.calls.append((name, dict(params)))
            calls = self.calls

            class _Result:
                data = {"ok": True, "restored": 3, "restoredIds": ["a", "b", "c"],
                        "skippedPermanent": 1, "repairedImages": 2,
                        "prunedDeletedIds": 3, "reason": "owner recovery"}

                def execute(self):
                    return self

            assert calls
            return _Result()

    fake = _Rpc()
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(supabase_store, "invalidate_read_cache", lambda *a, **k: None)
    report = supabase_store.recover_unavailable_products()
    assert report["ok"] is True and report["method"] == "rpc"
    assert report["restored"] == 3 and report["repairedImages"] == 2
    assert fake.calls == [("recover_unavailable_products",
                           {"p_reason": "owner recovery"})]


def test_product_by_id_strict_raises_while_default_stays_quiet(monkeypatch):
    from test_save_visibility import _BoomSupabase
    monkeypatch.setattr(supabase_store, "client", lambda: _BoomSupabase())
    assert supabase_store.product_by_id("jau-1") is None      # checkout stays safe
    with pytest.raises(RuntimeError):
        supabase_store.product_by_id("jau-1", strict=True)


# --------------------------------------------------------------------------
# 4. the browser behaviour that makes the recovery usable
# --------------------------------------------------------------------------
def test_admin_pages_always_fall_back_to_the_branded_placeholder():
    admin = (ROOT / "js" / "admin.js").read_text()
    # The catalogue grid card is the one that used to show a broken-image icon.
    assert 'class="adx-card-pic"><img src="${JA.asset(p.image || "images/products/_placeholder.jpg")}"' in admin
    assert 'onerror="fallbackImg(event)"' in admin
    # Supplier alert cards and the marketing picker do too.
    assert "supplierAlertCardHTML" in admin and "onerror=\"fallbackImg(event)\"" in admin
    assert (ROOT / "images" / "products" / "_placeholder.jpg").is_file()
    assert (ROOT / "images" / "products" / "_placeholder.400w.webp").is_file()
    store = (ROOT / "js" / "store.js").read_text()
    assert 'const ph = el.getAttribute("data-ph") || "images/products/_placeholder.jpg";' in store


def test_supplier_alerts_tab_and_walkin_modal_render_the_required_controls():
    admin = (ROOT / "js" / "admin.js").read_text()
    assert "Supplier Alerts" in admin
    assert "Toggle Out of Stock" in admin
    assert "Open supplier listing" in admin
    # Walk-in sale: the five required fields, no email/phone requirement.
    for field in ('name="customerName"', 'name="productId"', 'name="quantity"',
                  'name="salePrice"', 'name="currency"', 'name="paymentMethod"'):
        assert field in admin, field
    dialog = admin.split('id="walkin-dialog"')[1].split("</dialog>")[0].lower()
    for banned in ('type="email"', 'type="tel"', 'name="email"', 'name="phone"'):
        assert banned not in dialog, banned
    assert "Cash" in admin and "Bank Transfer" in admin and "POS" in admin


def test_walkin_and_sheet_feedback_use_green_and_red_toasts():
    admin = (ROOT / "js" / "admin.js").read_text()
    accounting = (ROOT / "js" / "accounting.js").read_text()
    # Red toast when the ledger push fails, green when it lands.
    assert 'JA.toast(d.message || "Sale recorded, but the Google Sheets sync failed.", { kind: "error" })' in admin
    assert "JA.success(d.message" in admin
    assert 'data-action="refresh-google"' in accounting
    assert 'toast(data.message || "Google Sheets connection refreshed.", "ok")' in accounting
    assert '"Could not refresh the Google Sheets connection.", "error"' in accounting
    # ...and the CSS actually paints them: green for ok/success, red for error.
    css = (ROOT / "css" / "style.css").read_text()
    assert '#aa-toast[data-kind="ok"], #aa-toast[data-kind="success"] { background: #1f7a4d; }' in css
    assert '#aa-toast[data-kind="error"] { background: var(--aa-error); }' in css
    assert '.toast[data-kind="success"]' in css and 'background: #1f7a4d' in css
    assert '.toast[data-kind="error"]' in css and '#8e3e30' in css


def test_google_refresh_refuses_cleanly_without_credentials(monkeypatch):
    import google_sheets
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    report = google_sheets.refresh_connection()
    assert report["ok"] is False
    assert "GOOGLE_CLIENT_ID" in report["error"]
