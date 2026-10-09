"""The catalog watchdog's stock guard.

The watchdog used to compare Supabase rows against the public catalogue and
alert when a product went missing - but it never looked at quantity, so a
colour that sold out in the morning was still being posted to WhatsApp in the
evening. These tests pin the per-SKU / per-variant tracking that closes that
gap, and the durable out-of-stock blocklist the broadcast generator reads so a
stale frontend can never resurrect a dead variant.

Run with:  python3 -m pytest tests/test_watchdog_stock_guard.py -q
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import catalog_watchdog as cw  # noqa: E402


def _row(pid="p1", sku="SKU1", name="Tote", qty=4, options=None):
    row = {"id": pid, "sku": sku, "name": name}
    if options is not None:
        row["optionStock"] = options
        row["stock_quantity"] = sum(options.values())
    else:
        row["stock_quantity"] = qty
    return row


# -------------------------------------------------------------- option parsing
def test_option_map_reads_a_jsonb_object():
    assert cw.option_map({"optionStock": {"Red": 3, "Blue": 1}}) == \
        {"Red": 3, "Blue": 1}


def test_option_map_reads_a_legacy_json_string():
    assert cw.option_map({"optionStock": '{"Red": 5}'}) == {"Red": 5}


def test_option_map_survives_junk():
    assert cw.option_map({"optionStock": "not json"}) == {}
    assert cw.option_map({"optionStock": None}) == {}
    assert cw.option_map({}) == {}
    assert cw.option_map(None) == {}


def test_unreadable_quantities_read_as_zero():
    """Safe direction: an unreadable number is never treated as stock."""
    assert cw.option_map({"optionStock": {"Red": "abc", "Blue": None}}) == \
        {"Red": 0, "Blue": 0}


def test_negative_quantities_clamp_to_zero():
    assert cw.option_map({"optionStock": {"Red": -3}}) == {"Red": 0}


# ------------------------------------------------------------------ snapshots
def test_snapshot_records_per_variant_and_total_stock():
    snap = cw.stock_snapshot([_row(options={"Red": 3, "Blue": 1})])
    entry = snap["products"]["p1"]
    assert entry["options"] == {"Red": 3, "Blue": 1}
    assert entry["total"] == 4
    assert entry["sku"] == "SKU1"


def test_a_product_with_no_variants_uses_its_own_quantity():
    snap = cw.stock_snapshot([_row(qty=7)])
    assert snap["products"]["p1"]["total"] == 7
    assert snap["products"]["p1"]["options"] == {}


def test_zero_variants_land_on_the_out_of_stock_blocklist():
    snap = cw.stock_snapshot([_row(options={"Red": 3, "Blue": 0})])
    assert snap["outOfStock"] == ["p1::blue"]


def test_a_fully_sold_out_product_blocks_the_whole_product():
    snap = cw.stock_snapshot([_row(qty=0)])
    assert snap["outOfStock"] == ["p1::"]


def test_a_product_with_stock_is_not_blocked():
    snap = cw.stock_snapshot([_row(options={"Red": 2})])
    assert snap["outOfStock"] == []


def test_rows_without_an_id_are_ignored():
    snap = cw.stock_snapshot([{"sku": "X", "stock_quantity": 3}, None, {}])
    assert snap["products"] == {}


# ---------------------------------------------------------------- transitions
def test_a_variant_dropping_to_zero_is_reported():
    before = cw.stock_snapshot([_row(options={"Red": 3, "Green": 2})])
    after = cw.stock_snapshot([_row(options={"Red": 3, "Green": 0})])
    moved = cw.diff_stock(before, after)
    assert len(moved) == 1
    assert moved[0]["kind"] == "out_of_stock"
    assert moved[0]["option"] == "Green"
    assert moved[0]["from"] == 2 and moved[0]["to"] == 0


def test_a_restock_is_reported_too():
    before = cw.stock_snapshot([_row(options={"Red": 0})])
    after = cw.stock_snapshot([_row(options={"Red": 5})])
    moved = cw.diff_stock(before, after)
    assert [m["kind"] for m in moved] == ["restocked"]
    assert moved[0]["to"] == 5


def test_a_whole_product_dropping_to_zero_is_reported():
    before = cw.stock_snapshot([_row(qty=5)])
    after = cw.stock_snapshot([_row(qty=0)])
    moved = cw.diff_stock(before, after)
    assert [(m["kind"], m["option"], m["from"], m["to"]) for m in moved] == \
        [("out_of_stock", "", 5, 0)]


def test_a_brand_new_variant_is_not_a_transition():
    """An option the previous run never saw cannot have 'gone' out of stock."""
    before = cw.stock_snapshot([_row(options={"Red": 3})])
    after = cw.stock_snapshot([_row(options={"Red": 3, "New": 0})])
    assert cw.diff_stock(before, after) == []


def test_the_first_run_reports_no_transitions():
    """Nothing to compare against means nothing has changed yet."""
    assert cw.diff_stock({}, cw.stock_snapshot([_row(qty=0)])) == []


def test_unchanged_stock_reports_nothing():
    snap = cw.stock_snapshot([_row(options={"Red": 3})])
    assert cw.diff_stock(snap, snap) == []


def test_transitions_carry_sku_and_name_for_the_alert():
    before = cw.stock_snapshot([_row(options={"Red": 1})])
    after = cw.stock_snapshot([_row(options={"Red": 0})])
    moved = cw.diff_stock(before, after)
    assert moved[0]["sku"] == "SKU1"
    assert moved[0]["name"] == "Tote"
    assert moved[0]["id"] == "p1"


# ---------------------------------------------------------------- persistency
def test_state_round_trips_through_disk(tmp_path):
    path = str(tmp_path / "stock.json")
    snap = cw.stock_snapshot([_row(options={"Red": 2, "Blue": 0})])
    assert cw.save_stock_state(snap, path) is True
    loaded = cw.load_stock_state(path)
    assert loaded["products"]["p1"]["options"] == {"Red": 2, "Blue": 0}
    assert loaded["outOfStock"] == ["p1::blue"]


def test_a_missing_state_file_is_an_empty_state_not_an_error(tmp_path):
    assert cw.load_stock_state(str(tmp_path / "nope.json")) == {}


def test_a_corrupt_state_file_is_ignored(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert cw.load_stock_state(str(path)) == {}


def test_the_state_is_json_serialisable_for_growth_settings():
    """It is stored as a JSON string in a key/value table, so it must dump."""
    snap = cw.stock_snapshot([_row(options={"Red": 2, "Blue": 0})])
    raw = json.dumps(snap, ensure_ascii=False)
    assert json.loads(raw)["outOfStock"] == ["p1::blue"]


# ------------------------------------------------------- broadcast integration
def test_the_watchdog_blocklist_feeds_the_broadcast_guard():
    """End to end: what the watchdog measures is what the post excludes."""
    import broadcast_posts as bp

    snap = cw.stock_snapshot([_row(options={"Red": 0, "Blue": 2})])
    product = {"id": "p1", "name": "Tote", "slug": "tote", "category": "Bags",
               "priceNgn": 18500, "online": True,
               # A STALE row: it still claims Red is in stock.
               "optionStock": {"Red": 4, "Blue": 2}}
    options = bp.available_options(product, oos=snap["outOfStock"])
    assert [label for label, _ in options] == ["Blue"]


def test_the_stock_columns_are_actually_selected():
    """The guard is useless if the query never asks for stock."""
    source = open(os.path.join(ROOT, "tools", "catalog_watchdog.py"),
                  encoding="utf-8").read()
    assert '"optionStock"' in source          # quoted: Postgres folds camelCase
    assert "stock_quantity" in source
