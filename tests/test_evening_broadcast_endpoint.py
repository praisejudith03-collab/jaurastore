"""The two new admin endpoints: evening broadcast and historical import.

Both are admin-only and CSRF-protected, and both are read-only by default -
the broadcast only returns copy, and the import is a dry run until ``confirm``
is sent. These tests pin that: the guards are the feature.

Run with:  python3 -m pytest tests/test_evening_broadcast_endpoint.py -q
"""
import io
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

import api as apimod  # noqa: E402
import app as appmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
from db import init_db  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tests"))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"

PRODUCTS = [
    {"id": "b1", "name": "Ankara Tote", "slug": "ankara-tote", "category": "Bags",
     "priceNgn": 18500, "online": True, "stock_quantity": 4,
     "optionStock": {"Red": 3, "Green": 0}},
    {"id": "b2", "name": "Sold Out Bag", "slug": "sold-out", "category": "Bags",
     "priceNgn": 9000, "online": True, "stock_quantity": 0},
    {"id": "b3", "name": "Hidden Bag", "slug": "hidden", "category": "Bags",
     "priceNgn": 7000, "online": False, "stock_quantity": 9},
]


@pytest.fixture()
def client():
    init_db()
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def login(client):
    response = client.post("/api/admin/login",
                           json={"email": EMAIL, "password": PW})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["csrf"]


@pytest.fixture()
def products(monkeypatch):
    """Pin the catalogue the endpoint sees."""
    monkeypatch.setattr(catalog_mod, "merged",
                        lambda *a, **k: [dict(p) for p in PRODUCTS])
    return PRODUCTS


# ------------------------------------------------------------ access control
def test_evening_post_requires_an_admin_session(client, products):
    assert client.post("/api/admin/broadcast/evening-post",
                       json={}).status_code in (401, 403)


def test_evening_post_requires_csrf(client, products):
    login(client)
    response = client.post("/api/admin/broadcast/evening-post", json={})
    assert response.status_code == 403


def test_import_requires_an_admin_session(client):
    assert client.post("/api/admin/accounting/import-historical",
                       data={}).status_code in (401, 403)


def test_import_requires_csrf(client):
    login(client)
    response = client.post("/api/admin/accounting/import-historical", data={})
    assert response.status_code == 403


# ------------------------------------------------------------ evening post
def test_evening_post_lists_only_verified_in_stock_lines(client, products):
    csrf = login(client)
    response = client.post("/api/admin/broadcast/evening-post", json={},
                           headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["ok"] is True
    assert body["productCount"] == 1
    text = body["text"]
    assert "Ankara Tote" in text
    assert "Red (3)" in text                       # in stock
    assert "Green" not in text                     # sold out, never posted
    assert "Sold Out Bag" not in text
    assert "Hidden Bag" not in text


def test_evening_post_reports_what_it_skipped(client, products):
    csrf = login(client)
    body = client.post("/api/admin/broadcast/evening-post", json={},
                       headers={"X-CSRF-Token": csrf}).get_json()
    reasons = {s["id"]: s["reason"] for s in body["skipped"]}
    assert reasons["b2"] == "out of stock"
    assert reasons["b3"] == "hidden"


def test_evening_post_defers_to_the_watchdog_blocklist(client, products,
                                                        monkeypatch):
    """A stale row still claiming stock must lose to the watchdog."""
    import broadcast_posts
    monkeypatch.setattr(broadcast_posts, "watchdog_out_of_stock",
                        lambda: ["b1::red"])
    csrf = login(client)
    body = client.post("/api/admin/broadcast/evening-post", json={},
                       headers={"X-CSRF-Token": csrf}).get_json()
    assert body["watchdogBlocked"] == 1
    # Every variant of b1 is now blocked, so nothing is safe to post.
    assert body["productCount"] == 0


def test_evening_post_uses_the_real_storefront_route(client, products):
    csrf = login(client)
    body = client.post("/api/admin/broadcast/evening-post", json={},
                       headers={"X-CSRF-Token": csrf}).get_json()
    assert "/products/ankara-tote" in body["text"]


def test_evening_post_accepts_a_custom_title_and_footer(client, products):
    csrf = login(client)
    body = client.post("/api/admin/broadcast/evening-post",
                       json={"title": "Friday Drop", "footer": "Order by 6pm."},
                       headers={"X-CSRF-Token": csrf}).get_json()
    assert "*Friday Drop*" in body["text"]
    assert "Order by 6pm." in body["text"]


def test_evening_post_reports_the_rate_it_used(client, products):
    csrf = login(client)
    body = client.post("/api/admin/broadcast/evening-post", json={},
                       headers={"X-CSRF-Token": csrf}).get_json()
    assert 0 < float(body["rate"]) <= 100


# --------------------------------------------------------- historical import
CSV = ("Order Date,Order NO,Customer Name,Currency,Selling Price,Cost Price,"
       "Country\n"
       "2024-03-14,ORD-1,Ada,NGN,45000,28000,Nigeria\n"
       "2024-03-15,ORD-2,Marie,CFA,62000,24000,Benin Republic\n")


def test_import_is_a_dry_run_by_default(client, monkeypatch):
    """Nothing may be written until confirm is sent - that is the guard."""
    import import_historical_orders as importer

    def explode(*args, **kwargs):
        raise AssertionError("a dry run reached the writer")

    monkeypatch.setattr(importer, "stage_orders", explode)
    monkeypatch.setattr(importer, "existing_ids", explode)
    csrf = login(client)
    body = client.post("/api/admin/accounting/import-historical",
                       json={"csv": CSV},
                       headers={"X-CSRF-Token": csrf}).get_json()
    assert body["ok"] is True
    assert body["dryRun"] is True
    assert body["ngnCount"] == 1
    assert body["cfaCount"] == 1
    assert "Dry run" in body["message"]


def test_import_maps_currency_location_and_costs(client, monkeypatch):
    csrf = login(client)
    body = client.post("/api/admin/accounting/import-historical",
                       json={"csv": CSV},
                       headers={"X-CSRF-Token": csrf}).get_json()
    preview = {row["id"]: row for row in body["preview"]}
    assert preview["ORD-1"]["currency"] == "NGN"
    assert preview["ORD-1"]["location"] == "Nigeria"
    assert preview["ORD-1"]["supplierCostNgn"] == 28000
    assert preview["ORD-2"]["currency"] == "CFA"
    assert preview["ORD-2"]["location"] == "Benin Republic"
    # 24,000 read as CFA (the row's own currency) -> stored in NGN, shown CFA.
    assert preview["ORD-2"]["supplierCostCfa"] == 24000
    assert preview["ORD-2"]["supplierCostNgn"] == 54545


def test_import_accepts_an_uploaded_file(client, monkeypatch):
    csrf = login(client)
    response = client.post(
        "/api/admin/accounting/import-historical",
        data={"file": (io.BytesIO(CSV.encode("utf-8")), "old-orders.csv")},
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["ok"] is True
    assert body["ngnCount"] == 1 and body["cfaCount"] == 1


def test_import_accepts_an_xlsx_upload(client, monkeypatch, tmp_path):
    """The .xlsx branch writes to a temp file and cleans it up."""
    import zipfile

    path = tmp_path / "old.xlsx"
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rows = [["Order Date", "Order NO", "Customer Name", "Currency",
             "Selling Price", "Cost Price", "Country"],
            ["2024-03-14", "XLS-1", "Ada", "NGN", "45000", "28000", "Nigeria"]]
    xml_rows = []
    for index, row in enumerate(rows, start=1):
        cells = "".join(
            f'<c r="{chr(65 + col)}{index}"><v>{value}</v></c>'
            for col, value in enumerate(row))
        xml_rows.append(f'<row r="{index}">{cells}</row>')
    body = (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<worksheet xmlns="{ns[1:-1]}"><sheetData>'
            + "".join(xml_rows) + "</sheetData></worksheet>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", body)

    csrf = login(client)
    response = client.post(
        "/api/admin/accounting/import-historical",
        data={"file": (open(path, "rb"), "old.xlsx")},
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["ok"] is True
    assert body["ngnCount"] == 1


def test_import_without_a_file_or_csv_text_is_rejected(client):
    csrf = login(client)
    response = client.post("/api/admin/accounting/import-historical",
                           json={}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 400


def test_import_rejects_a_file_with_no_data_rows(client):
    csrf = login(client)
    response = client.post(
        "/api/admin/accounting/import-historical",
        data={"file": (io.BytesIO(b"a,b,c\n"), "empty.csv")},
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf})
    assert response.status_code == 400


def test_import_refuses_an_oversized_upload(client):
    csrf = login(client)
    big = b"a,b\n" + (b"1,2\n" * (9 * 1024 * 1024 // 4))
    response = client.post(
        "/api/admin/accounting/import-historical",
        data={"file": (io.BytesIO(big), "huge.csv")},
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf})
    assert response.status_code == 400


def test_import_confirm_without_supabase_reports_503_not_a_crash(
        client, monkeypatch):
    """Asking to write when no store is configured is an honest failure."""
    from config import Config
    monkeypatch.setattr(Config, "SUPABASE_URL", "")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "")
    csrf = login(client)
    response = client.post("/api/admin/accounting/import-historical",
                           json={"csv": CSV, "confirm": True},
                           headers={"X-CSRF-Token": csrf})
    assert response.status_code == 503
