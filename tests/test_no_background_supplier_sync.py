"""Supplier stock sync is consolidated into the web service.

There must be no separate Render/GitHub background supplier worker consuming its
own instance hours. Supplier URLs remain owner-entered fields, and the only
stock automation is the bounded in-process supplier_watchdog called by
scheduler.py.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_old_external_worker_script_and_workflow_are_gone():
    assert not os.path.exists(os.path.join(ROOT, "tools", "supplier_stock_sync.py"))
    assert not os.path.exists(os.path.join(ROOT, ".github", "workflows", "supplier-stock-sync.yml"))


def test_supplier_watchdog_is_in_process_not_a_render_worker():
    scheduler = open(os.path.join(ROOT, "scheduler.py"), encoding="utf-8").read()
    assert "supplier_watchdog" in scheduler
    assert "jaurastore-background" in scheduler
    render = open(os.path.join(ROOT, "render.yaml"), encoding="utf-8").read()
    assert render.count("type: web") == 1
    assert "type: worker" not in render


def test_admin_product_form_keeps_supplier_url_fields():
    admin_js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert 'select name="supplierId"' not in admin_js
    assert "Not supplier-synced" not in admin_js
    assert 'name="supplierSku"' in admin_js
    assert "Supplier URL" in admin_js
    assert "data-opt-supplier" in admin_js


def test_catalog_still_stores_supplier_fields_for_watchdog():
    catalog = open(os.path.join(ROOT, "catalog.py"), encoding="utf-8").read()
    assert "supplierSku" in catalog
    assert "optionSupplierSku" in catalog
    assert "supplier_watchdog" in open(os.path.join(ROOT, "supplier_watchdog.py"), encoding="utf-8").read()
