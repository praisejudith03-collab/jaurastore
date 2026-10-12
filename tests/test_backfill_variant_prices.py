"""The single-run supplier-cost backfill (scripts/backfill_variant_prices.py).

Spec section 5: supplier costs are filled from the supplier pages by ONE
administrative run — no background loops on Render, dry run by default, and
never over a value the owner already typed. Selling prices are never touched.
"""
import importlib.util
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

_spec = importlib.util.spec_from_file_location(
    "backfill_variant_prices",
    os.path.join(ROOT, "scripts", "backfill_variant_prices.py"))
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)


# ------------------------------------------------------------------ parsing
def test_extract_naira_price_reads_the_common_supplier_spellings():
    assert backfill.extract_naira_price("<p>Price: ₦2,500</p>") == 2500
    assert backfill.extract_naira_price("NGN 2 500 only") == 2500
    assert backfill.extract_naira_price("naira 3200") == 3200
    assert backfill.extract_naira_price("₦ 12,500.00 (was ₦15,000)") == 12500
    # The lowest marked amount wins: suppliers list crossed-out prices above
    # the real one, and a cost estimate must never overshoot.
    assert backfill.extract_naira_price("₦9,000 ₦7,500") == 7500


def test_extract_ignores_unmarked_and_script_content():
    # A bare "3 for 2" promo line is not a Naira cost.
    assert backfill.extract_naira_price("<p>3 for 2 promo</p>") is None
    assert backfill.extract_naira_price("") is None
    # Prices inside scripts/styles are page logic, not the visible price.
    assert backfill.extract_naira_price(
        "<script>var p = '₦1';</script><div>₦4,000</div>") == 4000


def test_variant_keys_accept_the_stored_shapes():
    product = {"optionSupplierSku": {
        "Size: Small": "https://splendall.example/small",
        "Size: Big": ["https://splendall.example/big"],
        "Size: None": "not a url",
    }}
    keys = backfill._variant_keys(product)
    assert keys == {"Size: Small": "https://splendall.example/small",
                    "Size: Big": "https://splendall.example/big"}
    product["optionSupplierSku"] = json.dumps({"Colour: Pink": "https://x.example/p"})
    assert backfill._variant_keys(product) == {"Colour: Pink": "https://x.example/p"}


# --------------------------------------------------------------- the plan
def _fake_fetch(responses):
    calls = []

    def fetch(url):
        calls.append(url)
        return responses.get(url, ("error", "unexpected url " + url))

    fetch.calls = calls
    return fetch


def test_collect_plan_fills_only_the_missing_costs():
    products = [
        {"id": "a", "name": "Whole-product piece", "supplierSku": "https://sup.example/a",
         "supplierCost": None},
        {"id": "b", "name": "Owner already costed", "supplierSku": "https://sup.example/b",
         "supplierCost": 2100},
        {"id": "c", "name": "Variant piece",
         "optionSupplierSku": {"Size: Small": "https://sup.example/s",
                               "Size: Big": "https://sup.example/b2"},
         "optionSupplierCost": {"Size: Small": 1200, "Size: Big": None}},
        {"id": "d", "name": "No links at all"},
    ]
    fetch = _fake_fetch({
        "https://sup.example/a": 4300,
        "https://sup.example/b": 9999,     # must never be fetched: cost exists
        "https://sup.example/s": 1800,     # must never be fetched: cost exists
        "https://sup.example/b2": 2600,
    })
    scanned, fills, failures = backfill.collect_plan(products, fetch)
    assert scanned == 4
    assert [entry["id"] for entry in fills] == ["a", "c"]
    assert fills[0]["fills"] == {"supplierCost": 4300}
    assert fills[1]["fills"] == {"optionSupplierCost": {"Size: Big": 2600}}
    # The owner-entered figures were never re-fetched, so they can never be
    # overwritten by this run.
    assert "https://sup.example/b" not in fetch.calls
    assert "https://sup.example/s" not in fetch.calls
    assert failures == []


def test_collect_plan_reports_fetch_errors_without_aborting_the_run():
    products = [{"id": "a", "name": "Broken link", "supplierSku": "https://sup.example/dead"}]
    fetch = _fake_fetch({"https://sup.example/dead": ("error", "HTTP 404")})
    scanned, fills, failures = backfill.collect_plan(products, fetch)
    assert scanned == 1 and fills == []
    assert len(failures) == 1
    assert "HTTP 404" in failures[0]["errors"][0]


def test_collect_plan_refuses_non_http_links():
    products = [{"id": "a", "name": "Not a url", "supplierSku": "ftp://sup.example/a",
                 "supplierCost": None}]
    fetch = _fake_fetch({})
    _scanned, fills, failures = backfill.collect_plan(products, fetch)
    assert fills == [] and failures == []
    assert fetch.calls == []


def test_ssrf_guard_blocks_private_targets_even_if_configured():
    """The script fetches through security.safe_fetch — a private/internal
    supplier URL is refused before any request leaves the process."""
    products = [{"id": "a", "name": "Internal link",
                 "supplierSku": "http://169.254.169.254/latest/meta-data",
                 "supplierCost": None}]
    fetch = _fake_fetch({})
    # Route the plan through the REAL guarded fetcher for this row.
    import security
    def guarded(url):
        try:
            return backfill.extract_naira_price(security.safe_fetch_text(url))
        except Exception as exc:
            return ("error", str(exc)[:160])
    _scanned, fills, failures = backfill.collect_plan(products, guarded)
    assert fills == []
    assert failures and "https" in failures[0]["errors"][0]


# ------------------------------------------------------------------ writing
def test_apply_writes_through_catalog_upsert_and_merges_maps():
    products = [
        {"id": "a", "name": "Piece", "supplierCost": None},
        {"id": "c", "name": "Variant piece",
         "optionSupplierCost": {"Size: Small": 1200, "Size: Big": None}},
    ]
    plan = [
        {"id": "a", "fills": {"supplierCost": 4300}, "name": "", "errors": []},
        {"id": "c", "fills": {"optionSupplierCost": {"Size: Big": 2600}},
         "name": "", "errors": []},
    ]
    saved_payloads = []
    fake_catalog = type(sys)("fake_catalog")
    fake_catalog.upsert = lambda payload, actor=None: (
        saved_payloads.append(dict(payload)) or (payload, "updated", True))
    with patch.dict(sys.modules, {"catalog": fake_catalog}):
        written = backfill._apply(products, plan)
    assert written == 2
    assert saved_payloads[0]["supplierCost"] == 4300
    assert saved_payloads[1]["optionSupplierCost"] == {
        "Size: Small": 1200, "Size: Big": 2600}


def test_cli_is_a_dry_run_by_default_and_applies_only_with_the_flag(tmp_path, capsys):
    products = [{"id": "a", "name": "Piece", "supplierSku": "https://sup.example/a",
                 "supplierCost": None}]
    fake_catalog = type(sys)("fake_catalog")
    saved = []
    fake_catalog.upsert = lambda payload, actor=None: (
        saved.append(dict(payload)) or (payload, "updated", True))
    fake_catalog.merged = lambda include_hidden=False: list(products)
    with patch.dict(sys.modules, {"catalog": fake_catalog}):
        with patch("security.safe_fetch_text", return_value="<div>₦4,300</div>"):
            rc_dry = backfill.main([])
            assert saved == []                  # dry run: nothing written
            rc_apply = backfill.main(["--apply"])
    assert rc_dry == 0 and rc_apply == 0
    assert len(saved) == 1 and saved[0]["supplierCost"] == 4300
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "--apply" in out


def test_cli_limit_processes_only_the_first_rows(tmp_path, capsys):
    products = [
        {"id": f"row{i}", "name": f"Piece {i}",
         "supplierSku": f"https://sup.example/{i}", "supplierCost": None}
        for i in range(5)
    ]
    fake_catalog = type(sys)("fake_catalog")
    saved = []
    fake_catalog.upsert = lambda payload, actor=None: (
        saved.append(dict(payload)) or (payload, "updated", True))
    fake_catalog.merged = lambda include_hidden=False: list(products)
    with patch.dict(sys.modules, {"catalog": fake_catalog}):
        with patch("security.safe_fetch_text",
                   side_effect=lambda url: f"<div>₦{1000 + len(url)}</div>"):
            rc = backfill.main(["--limit", "2", "--apply"])
    assert rc == 0
    assert len(saved) == 2
    out = capsys.readouterr().out
    assert "3 row(s) were NOT processed" in out


def test_cli_status_reports_coverage_without_fetching(capsys):
    fake_catalog = type(sys)("fake_catalog")
    fake_catalog.merged = lambda include_hidden=False: [
        {"id": "a", "supplierCost": 100},
        {"id": "b", "optionSupplierCost": {"Size: Small": 50}},
        {"id": "c"},
    ]
    with patch.dict(sys.modules, {"catalog": fake_catalog}):
        rc = backfill.main(["--status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "catalogue rows            : 3" in out
    assert "product-level cost set    : 1" in out
    assert "rows with any variant cost: 1" in out


def test_script_never_touches_selling_prices():
    """Cost backfill and retail pricing are separated by construction: the
    script only writes supplierCost / optionSupplierCost keys."""
    src = open(os.path.join(ROOT, "scripts", "backfill_variant_prices.py"),
               encoding="utf-8").read()
    apply_start = src.index("def _apply(")
    apply_fn = src[apply_start:src.index("\ndef ", apply_start + 1)]
    for key in ("priceNgn", "optionPrices", "priceCfa"):
        assert key not in apply_fn
    assert "supplierCost" in apply_fn and "optionSupplierCost" in apply_fn
