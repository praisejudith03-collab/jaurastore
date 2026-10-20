"""Supplier stock sync does not consume a Render worker or web CPU.

Owner directive 2026-10-19 moved the schedule to GitHub Actions: live updates
every 20 minutes and a 02:00 deep audit. This test supersedes the previous
"in-process only" contract, but keeps its real intent: no separate Render
worker service, no duplicated instance-hours, and the admin supplier fields
stay intact for the watchdog to read.

Run with: python3 -m pytest tests/test_no_background_supplier_sync.py -q
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_render_worker_and_no_legacy_worker_script():
    assert not os.path.exists(os.path.join(ROOT, "tools", "supplier_stock_sync.py"))
    assert not os.path.exists(os.path.join(
        ROOT, ".github", "workflows", "supplier-stock-sync.yml"))
    render = open(os.path.join(ROOT, "render.yaml"), encoding="utf-8").read()
    assert render.count("type: web") == 1
    assert "type: worker" not in render


def test_supplier_polling_is_disabled_on_render_and_hosted_on_actions():
    render = open(os.path.join(ROOT, "render.yaml"), encoding="utf-8").read()
    block = render.split("key: SUPPLIER_WATCHDOG_ENABLED", 1)[1][:80]
    assert 'value: "0"' in block
    workflow = open(os.path.join(
        ROOT, ".github", "workflows", "supplier-watchdog.yml"), encoding="utf-8").read()
    assert "tools/supplier_sync.py" in workflow
    assert "5,25,45 * * * *" in workflow
    assert "0 1 * * *" in workflow


def test_scheduler_keeps_the_optional_in_process_call():
    scheduler = open(os.path.join(ROOT, "scheduler.py"), encoding="utf-8").read()
    assert "supplier_watchdog" in scheduler
    assert "jaurastore-background" in scheduler


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
    assert "supplier_watchdog" in open(os.path.join(
        ROOT, "supplier_watchdog.py"), encoding="utf-8").read()
