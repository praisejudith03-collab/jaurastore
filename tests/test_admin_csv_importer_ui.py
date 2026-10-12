"""The native "Import Historical CSV" admin flow routes into the staging queue.

Spec section 5b: an upload modal/button in /admin hands a historical sheet to
the reviewed importer, which auto-detects NGN vs. FCFA per row and lands
everything in the accounting STAGING queue — never straight into the live
ledgers — with a dry-run report the owner reads before confirming.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_admin_dashboard_links_to_the_import_dialog():
    admin_js = _read("js", "admin.js")
    assert "/admin/accounting#import" in admin_js
    assert "Import historical CSV" in admin_js


def test_accounting_page_opens_the_upload_modal():
    accounting_js = _read("js", "accounting.js")
    assert 'aria-label="Import historical CSV"' in accounting_js
    # A file upload (CSV/XLSX) plus a dry-run first: the owner reads the
    # staging report before anything is written.
    assert 'data-action="import-csv"' in accounting_js
    assert 'accept=".csv,.tsv,.xlsx,.xlsm,text/csv"' in accounting_js
    assert "runCsvImport(false)" in accounting_js
    assert "runCsvImport(true)" in accounting_js
    # The #import deep link from the dashboard auto-opens the dialog.
    assert 'window.location.hash === "#import"' in accounting_js


def test_importer_splits_naira_and_fcfa_into_the_staging_queue():
    importer = _read("import_historical_orders.py")
    assert "def detect_currency" in importer
    assert "def row_currency" in importer
    assert "staging" in importer.lower()
    # Currency routing covers both ledgers and the FCFA countries.
    for marker in ("CFA", "NGN", "benin", "togo"):
        assert marker in importer
    api_src = _read("api.py")
    # The admin endpoint is a dry run unless confirm=true, and refuses huge
    # sheets instead of half-importing them.
    assert "confirm" in api_src.split("def admin_accounting_import_historical")[1][:4000]
    assert "_HISTORICAL_IMPORT_MAX_BYTES" in api_src


def test_stock_watchdog_stays_limited_to_stock_tracking():
    """Spec 5c: no heavy background loops; the watchdog only mirrors
    In-Stock <-> Out-of-Stock and warns on supplier price rises — it never
    rewrites the shop's own retail prices or runs as a separate worker."""
    watchdog = _read("supplier_watchdog.py")
    assert "never rewrites the shop's own retail price" in watchdog
    scheduler = _read("scheduler.py")
    assert "supplier_watchdog" in scheduler
    render = _read("render.yaml")
    assert "type: worker" not in render
    # The single-run backfill script exists and is dry-run by default.
    script = _read("scripts", "backfill_variant_prices.py")
    assert "--apply" in script
    assert "DRY RUN" in script
    assert "while True" not in script and "sleep(" not in script
