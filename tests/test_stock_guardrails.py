"""Background stock guardrails: the stored row must agree with what is sold.

The shop already DERIVES availability on every read (api._public_product turns
a zero quantity into stock_status "out", catalog.stock_of sums the variant
map), and every write path guards the rows it touches (a checkout reservation,
a supplier mirror, an admin save). What was missing is the sweep: a row whose
variants all reached zero while its own total still said something else, or a
total that drifted away from its variant sum, stayed wrong until an admin
happened to open that product. This module pins the sweep that repairs those
rows on the regular maintenance tick, and - just as important - pins the ONE
thing it must never do: guess which of two disagreeing stock columns the shop
means. On this catalogue that is usually a legacy `stock` value with real
numbers next to a `stock_quantity` that still holds its table default, and
writing either over the other destroys the only copy of the truth.

Run with:  python3 -m pytest tests/test_stock_guardrails.py -q
"""
import json
import os
import pathlib
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def catalogue(tmp_path, monkeypatch):
    """A catalog module pointed at a scratch overrides file (no live data)."""
    import catalog
    monkeypatch.setenv("FLASK_ENV", "testing")
    path = tmp_path / "catalog.json"
    monkeypatch.setattr(catalog, "CATALOG_FILE", str(path), raising=False)
    monkeypatch.setattr(catalog.Config, "CATALOG_PATH", str(path), raising=False)
    # Supabase must not answer: this sweep is tested against local rows only.
    monkeypatch.setattr(catalog, "_supabase_products", lambda: None, raising=False)
    monkeypatch.setattr(catalog, "_durable_deleted_ids", lambda: set(), raising=False)
    monkeypatch.setattr(catalog, "_supabase_dead_ids", lambda: set(), raising=False)
    return catalog


def _write(catalog, rows):
    catalog._write_overrides({"products": rows}, catalog.CATALOG_FILE)
    return rows


def _read_rows(catalog):
    with open(catalog.CATALOG_FILE, encoding="utf-8") as fh:
        return json.load(fh).get("products") or []


# ------------------------------------------------------------------ derivation
def test_derived_status_is_out_when_the_row_has_no_units(catalogue):
    assert catalogue.derived_stock_status({"stock": 0}) == "out"
    assert catalogue.derived_stock_status({"stock": 4}) == "in"
    assert catalogue.derived_stock_status({"stock": "0"}) == "out"
    # a broken value is not "in": an unreadable quantity cannot be sold
    assert catalogue.derived_stock_status({"stock": "abc"}) == "out"
    assert catalogue.derived_stock_status({}) == "out"


def test_variant_statuses_follow_the_map_not_the_total(catalogue):
    row = {"stock": 9, "optionStock": {"Red": 0, "Blue": 2}}
    assert catalogue.derived_variant_statuses(row) == {"Red": "out", "Blue": "in"}
    # every variant at zero makes the whole product unavailable even though the
    # stored total still claims otherwise - this is the drift the sweep repairs
    row = {"stock": 9, "optionStock": {"Red": 0, "Blue": "0"}}
    assert catalogue.derived_stock_status(row) == "out"


# ------------------------------------------------------------------- the repair
def test_fix_repairs_junk_variant_values_and_the_total(catalogue):
    row = {"id": "p1", "stock": 5, "optionStock": {"Red": "abc", "Blue": "3"}}
    fixed, changed = catalogue.stock_guardrail_fix(row)
    assert fixed["optionStock"] == {"Red": 0, "Blue": 3}, fixed
    assert fixed["stock"] == 3, "the total must equal the variant sum"
    assert "stock" in changed and "stock_quantity" in changed


def test_fix_zeroes_a_total_whose_variants_all_sold_out(catalogue):
    row = {"id": "p2", "stock": 4, "stock_quantity": 4,
           "optionStock": {"Red": 0, "Blue": 0}}
    fixed, changed = catalogue.stock_guardrail_fix(row)
    assert fixed["stock"] == 0 and fixed["stock_quantity"] == 0, fixed
    assert catalogue.derived_stock_status(fixed) == "out"
    assert set(changed) >= {"stock", "stock_quantity"}


def test_an_explicitly_switched_off_row_keeps_the_numbers_the_owner_typed(catalogue):
    """The same data-loss rule as the column clash.

    `stockStatus: "out"` already makes the shop serve "out" on every read, so
    rewriting the stored quantities would only destroy the numbers the owner
    needs the moment she switches the product back on.
    """
    row = {"id": "p3", "stockStatus": "out", "stock": 7, "stock_quantity": 7,
           "optionStock": {"Red": 7}}
    fixed, changed = catalogue.stock_guardrail_fix(row)
    assert fixed.get("stock", 7) == 7 and fixed["optionStock"] == {"Red": 7}, fixed
    assert not changed, "a switched-off row is decided by the owner, not the sweep"


def test_fix_leaves_a_healthy_row_untouched(catalogue):
    row = {"id": "p4", "stock": 3, "stock_quantity": 3,
           "optionStock": {"Red": 1, "Blue": 2}}
    fixed, changed = catalogue.stock_guardrail_fix(row)
    assert fixed == row, "a healthy row must come back byte-identical"
    assert not changed


# -------------------------------------------------------------- the sweep
def test_sweep_repairs_drift_and_is_idempotent(catalogue):
    _write(catalogue, [
        {"id": "drift", "name": "Drift", "priceNgn": 100, "online": True,
         "options": [{"title": "Colour", "values": ["Red", "Blue"]}],
         "optionStock": {"Red": 0, "Blue": 0}, "stock": 4, "stock_quantity": 4},
        {"id": "junk", "name": "Junk", "priceNgn": 100, "online": True,
         "options": [{"title": "Colour", "values": ["Red"]}],
         "optionStock": {"Red": "abc"}, "stock": 4, "stock_quantity": 4},
    ])
    first = catalogue.stock_guardrail_sweep()
    assert first["fixed"] == 2, first
    assert set(first["ids"]) == {"drift", "junk"}
    assert first["checked"] > 2, "the sweep walks the whole live catalogue"
    rows = {r["id"]: r for r in _read_rows(catalogue)}
    assert rows["drift"]["stock"] == 0 and rows["junk"]["stock"] == 0
    assert rows["junk"]["optionStock"] == {"Red": 0}
    second = catalogue.stock_guardrail_sweep()
    assert second["fixed"] == 0, "a repaired catalogue must not be rewritten again"
    assert second["checked"] == first["checked"]


def test_sweep_never_writes_a_healthy_catalogue(catalogue):
    _write(catalogue, [
        {"id": "ok1", "name": "One", "priceNgn": 10, "online": True,
         "stock": 5, "stock_quantity": 5},
        {"id": "ok2", "name": "Two", "priceNgn": 10, "online": True,
         "options": [{"title": "Colour", "values": ["Red"]}],
         "optionStock": {"Red": 2}, "stock": 2, "stock_quantity": 2},
    ])
    before = pathlib.Path(catalogue.CATALOG_FILE).read_text()
    result = catalogue.stock_guardrail_sweep()
    assert result["checked"] >= 2 and result["fixed"] == 0, result
    assert pathlib.Path(catalogue.CATALOG_FILE).read_text() == before, \
        "a no-op sweep must not touch the file (updated_at churn across the catalogue)"


def test_sweep_reports_a_stock_disagreement_and_never_rewrites_it(catalogue):
    """The destructive guess: `stock` says 24, `stock_quantity` says 0.

    On this shop that is the normal shape of an imported row (the table's
    default landed in one column, the real number in the other), so the sweep
    must flag it for the owner and leave BOTH values exactly as they were.
    """
    _write(catalogue, [
        {"id": "clash", "name": "Clash", "priceNgn": 100, "online": True,
         "stock": 24, "stock_quantity": 0},
    ])
    result = catalogue.stock_guardrail_sweep()
    assert result["fixed"] == 0, "a disagreement is a decision, not a repair"
    assert result["disagreements"] == 1, result
    assert result["disagreementIds"] == ["clash"]
    row = _read_rows(catalogue)[0]
    assert row["stock"] == 24 and row["stock_quantity"] == 0, row


def test_report_lists_disagreements_with_both_numbers(catalogue):
    _write(catalogue, [
        {"id": "clash", "name": "Clash", "priceNgn": 100, "online": True,
         "stock": 24, "stock_quantity": 0},
        {"id": "agree", "name": "Agree", "priceNgn": 100, "online": True,
         "stock": 4, "stock_quantity": 4},
        {"id": "variant", "name": "Variant", "priceNgn": 100, "online": True,
         "options": [{"title": "Colour", "values": ["Red"]}],
         "optionStock": {"Red": 1}, "stock": 1, "stock_quantity": 0},
    ])
    report = catalogue.stock_guardrail_report()
    ids = [d["id"] for d in report["disagreements"]]
    assert ids == ["clash"], \
        "an agreeing row and a variant row (its sum is the truth) are not clashes"
    entry = report["disagreements"][0]
    assert entry["stock"] == 24 and entry["stock_quantity"] == 0
    assert entry["status"] == "out"


def test_a_missing_second_spelling_is_not_a_disagreement(catalogue):
    _write(catalogue, [{"id": "only", "name": "Only", "priceNgn": 5, "online": True,
                        "stock": 3}])
    report = catalogue.stock_guardrail_report()
    assert report["disagreements"] == [], \
        "a row with no stock_quantity column at all is not a clash"


# ------------------------------------------------------- the scheduler wiring
def test_the_maintenance_tick_runs_the_guardrail_every_tick():
    src = (pathlib.Path(ROOT) / "scheduler.py").read_text()
    tick = src[src.index("def _maintenance_tick"):]
    tick = tick[:tick.index("\ndef ")]
    assert "stock.guardrail" in tick, \
        "a product that sells out must not wait for the nightly pass"
    assert "stock_guardrail_sweep" in src
    assert "stockGuardrailLastRun" in src


def test_the_health_snapshot_publishes_the_guardrail_numbers(monkeypatch):
    import scheduler
    monkeypatch.setattr(scheduler, "_alive", lambda *a, **k: True, raising=False)
    snap = scheduler.health_snapshot(repair=False)
    guard = snap["stockGuardrail"]
    assert guard["schedule"]
    for key in ("checked", "fixed", "disagreements"):
        assert key in guard
    assert "stockGuardrailDisagreements" in snap


def test_the_guardrail_failure_is_recorded_on_health_not_swallowed():
    src = (pathlib.Path(ROOT) / "scheduler.py").read_text()
    body = src[src.index("def _stock_guardrail"):]
    body = body[:body.index("\ndef ")]
    assert "skipped" in body and "lastError" in body, \
        "a skipped sweep must be visible on the health screen"
