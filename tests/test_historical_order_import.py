"""Historical-order import: currency separation, column mapping, Location,
converted supplier costs, and staging-queue compatibility.

The importer (``import_historical_orders.py``) reads a hand-made old
spreadsheet and stages the rows on the accounting desk. These tests pin the
behaviour that matters:

  * rows are routed by currency - Naira to the NGN queue, FCFA to the CFA
    queue - with the decision taken from the currency cell, then from any
    currency marker in the row, then from an explicit default;
  * every column of the source is mapped onto the canonical 12-column ledger
    layout, tolerating real-world header spelling;
  * the Location / Destination column is filled from country / city / zone and
    is never invented when the source has nothing;
  * supplier costs are stored in NGN (the snapshot invariant) and shown
    converted to FCFA on the CFA ledger;
  * a built row is a real order: ``accounting.entry_from_order`` accepts it and
    produces the same entry shape a live confirmation produces.

Run with:  python3 -m pytest tests/test_historical_order_import.py -q
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import accounting  # noqa: E402
import google_sheets  # noqa: E402
import import_historical_orders as imp  # noqa: E402

RATE = accounting.LEGACY_RATE


# --------------------------------------------------------------------- helpers
def _import(headers, rows, **kwargs):
    return imp.import_rows(headers, rows, rate=kwargs.pop("rate", RATE), **kwargs)


def _first(group):
    order, info = group[0]
    return order, info


def _payload(order):
    return json.loads(order["payload"])


# ------------------------------------------------------------ header mapping
def test_headers_are_mapped_despite_spelling_and_punctuation():
    """Real sheets say 'Order NO', 'order_id', 'Selling Price' - all must map."""
    mapping = imp.map_headers(
        ["Order Date", "Order NO", "Customer Name", "Items Ordered",
         "Currency", "Selling Price", "Cost Price (NGN)", "Delivery Fee",
         "Profit", "City", "Country", "Phone", "Remark"])
    assert mapping["date"] == 0
    assert mapping["order_id"] == 1
    assert mapping["customer"] == 2
    assert mapping["items"] == 3
    assert mapping["currency"] == 4
    assert mapping["revenue"] == 5
    assert mapping["supplier_cost"] == 6
    assert mapping["transport"] == 7
    assert mapping["city"] == 9
    assert mapping["country"] == 10


def test_longest_alias_wins_so_net_profit_is_not_swallowed_by_notes():
    """'Net Profit' must not fall through to a generic 'note'/'id' match."""
    mapping = imp.map_headers(["Notes", "Net Profit", "Order ID"])
    assert mapping["net_profit"] == 1
    assert mapping["notes"] == 0
    assert mapping["order_id"] == 2


def test_unknown_headers_produce_no_mapping_but_never_raise():
    assert imp.map_headers(["foo", "bar"]) == {}
    assert imp.map_headers([]) == {}


# -------------------------------------------------------------- money parsing
def test_naira_sign_is_stripped_not_fatal():
    """A leading U+20A6 used to poison Decimal() and zero the whole figure."""
    assert imp.parse_number("₦45,000") == 45000
    assert imp.parse_number("₦2,500") == 2500
    assert imp.parse_number("₦45 000") == 45000


def test_group_separators_and_french_decimal_comma():
    assert imp.parse_number("1,200") == 1200          # thousands
    assert imp.parse_number("12,50") == 12.50         # decimal comma
    assert imp.parse_number("35 000") == 35000        # space group
    assert imp.parse_number("62,000 FCFA") == 62000
    assert imp.parse_number("24,000 CFA") == 24000
    assert imp.parse_number("N12,000") == 12000


def test_unparseable_cells_become_zero_without_raising():
    assert imp.parse_number("") is None
    assert imp.parse_number("N/A") is None
    assert imp.parse_number("ankara gown") is None
    assert imp.parse_amount("N/A") == 0
    assert imp.parse_amount("(1,500)") == 0           # negative -> 0, never -ve


# ---------------------------------------------------------- currency detection
def test_currency_read_from_the_currency_cell():
    headers = ["Order Date", "Currency", "Selling Price"]
    result = _import(headers, [["2024-03-14", "NGN", "10,000"],
                               ["2024-03-15", "CFA", "10,000"]])
    assert [i["currency"] for _, i in result["ngn"]] == ["NGN"]
    assert [i["currency"] for _, i in result["cfa"]] == ["CFA"]
    assert all(i["currencyHow"] == "explicit" for _, i in
               result["ngn"] + result["cfa"])


def test_currency_inferred_from_markers_in_the_row():
    headers = ["Date", "Customer", "Selling Price"]
    result = _import(headers, [["2024-03-14", "Ada", "₦45,000"],
                               ["2024-03-15", "Marie", "62,000 FCFA"]])
    assert [i["currency"] for _, i in result["ngn"]] == ["NGN"]
    assert [i["currency"] for _, i in result["cfa"]] == ["CFA"]
    assert all(i["currencyHow"] == "inferred" for _, i in
               result["ngn"] + result["cfa"])


def test_rows_with_no_marker_use_the_default_and_are_flagged():
    headers = ["Date", "Customer", "Selling Price"]
    result = _import(headers, [["2024-03-14", "Ada", "45,000"]],
                     default_currency="CFA")
    order, info = _first(result["cfa"])
    assert info["currency"] == "CFA"
    assert info["currencyHow"] == "default"
    assert any("assumed CFA" in w for _, w in result["warnings"])


def test_mixed_sheet_routes_each_row_to_its_own_ledger():
    headers = ["Date", "Order ID", "Customer", "Currency", "Selling Price"]
    rows = [
        ["2024-03-14", "A-1", "Ada", "NGN", "45,000"],
        ["2024-03-15", "A-2", "Marie", "CFA", "45,000"],
        ["2024-03-16", "A-3", "Kofi", "NGN", "30,000"],
    ]
    result = _import(headers, rows)
    assert [o["id"] for o, _ in result["ngn"]] == ["A-1", "A-3"]
    assert [o["id"] for o, _ in result["cfa"]] == ["A-2"]


# ------------------------------------------------------------------- conversion
def test_conversion_moves_both_ways_at_the_given_rate():
    assert imp.convert(10000, "NGN", "CFA", RATE) == 4400
    assert imp.convert(4400, "CFA", "NGN", RATE) == 10000
    assert imp.convert(10000, "NGN", "NGN", RATE) == 10000


def test_cfa_supplier_cost_is_stored_in_ngn_and_displayed_in_cfa():
    """The snapshot stores NGN; the CFA ledger shows the converted figure."""
    headers = ["Date", "Currency", "Selling Price", "Supplier costs · FCFA"]
    result = _import(headers, [["2024-03-16", "CFA", "62,000", "24,000 FCFA"]])
    order, info = _first(result["cfa"])
    # 24,000 FCFA at 0.44 -> 54,545 NGN, and back to 24,000 FCFA for display.
    assert info["supplierCostNgn"] == 54545
    assert info["supplierCostCfa"] == 24000
    assert _payload(order)["accounting"]["supplierCostNgn"] == 54545


def test_a_cell_marker_beats_a_header_that_names_another_currency():
    """'24,000 CFA' under a 'Cost Price (NGN)' header is CFA, not NGN."""
    headers = ["Date", "Currency", "Cost Price (NGN)"]
    row = ["2024-03-20", "CFA", "24,000 CFA"]
    mapping = imp.map_headers(headers)
    assert imp.column_currency(headers, mapping, "supplier_cost", "CFA",
                               row[2]) == "CFA"
    # ... and a bare figure under the same header is NGN, as the header says.
    assert imp.column_currency(headers, mapping, "supplier_cost", "CFA",
                               "35 000") == "NGN"


def test_ngn_supplier_cost_stays_in_ngn_on_the_ngn_ledger():
    headers = ["Date", "Currency", "Selling Price", "Supplier Cost"]
    result = _import(headers, [["2024-03-14", "NGN", "45,000", "28,000"]])
    _, info = _first(result["ngn"])
    assert info["supplierCostNgn"] == 28000
    assert info["supplierCostCfa"] == 12320       # shown converted on CFA view


def test_revenue_and_transport_land_in_the_rows_own_currency():
    headers = ["Date", "Currency", "Selling Price", "Delivery Fee"]
    result = _import(headers, [["2024-03-16", "CFA", "62,000", "4,500"]])
    order, info = _first(result["cfa"])
    assert info["revenue"] == 62000
    assert order["total"] == 62000
    assert _payload(order)["accounting"]["deliveryExpense"] == 4500


# --------------------------------------------------------------------- location
def test_location_prefers_country_then_city_then_zone():
    headers = ["Date", "Country", "City", "Zone"]
    result = _import(headers, [["2024-03-14", "Nigeria", "Lagos", "Ikeja"],
                               ["2024-03-15", "", "Cotonou", ""],
                               ["2024-03-16", "", "", "Zone 4"]])
    assert [i["location"] for _, i in result["ngn"]] == \
        ["Nigeria", "Cotonou", "Zone 4"]


def test_location_is_never_invented():
    headers = ["Date", "Customer", "Selling Price"]
    result = _import(headers, [["2024-03-14", "Ada", "45,000"]])
    _, info = _first(result["ngn"])
    assert info["location"] == ""
    order, _ = _first(result["ngn"])
    assert order["country"] == ""
    assert any("no location" in w for _, w in result["warnings"])


def test_country_names_are_canonicalised():
    assert imp.canonical_country("Nigeria") == "Nigeria"
    assert imp.canonical_country("NG") == "Nigeria"
    assert imp.canonical_country("benin republic") == "Benin Republic"
    assert imp.canonical_country("Bénin") == "Benin Republic"
    assert imp.canonical_country("Cotonou") == ""       # a city, not a country


def test_a_city_that_names_a_country_is_promoted_to_country():
    headers = ["Date", "City"]
    result = _import(headers, [["2024-03-14", "Togo"]])
    order, info = _first(result["ngn"])
    assert info["location"] == "Togo"
    assert order["country"] == "Togo"


def test_a_bare_destination_column_reaches_the_ledger_location():
    """Regression: a "Town"/"Destination" column resolved fine but was never
    written onto the order, so accounting's order_location() saw no country,
    city or zone and the ledger's Location cell arrived empty."""
    headers = ["Date", "Selling Price", "Town"]
    result = _import(headers, [["2024-03-14", "45,000", "Cotonou"]])
    order, info = _first(result["ngn"])
    assert info["location"] == "Cotonou"
    assert order["city"] == "Cotonou"
    assert accounting.entry_from_order(order)["location"] == "Cotonou"


def test_a_destination_that_names_a_country_is_not_also_used_as_the_city():
    headers = ["Date", "Selling Price", "Destination"]
    result = _import(headers, [["2024-03-14", "45,000", "Togo"]])
    order, info = _first(result["ngn"])
    assert info["location"] == "Togo"
    assert order["country"] == "Togo"
    assert order["city"] == ""


def test_location_is_truncated_to_the_ledger_width():
    headers = ["Date", "City"]
    result = _import(headers, [["2024-03-14", "X" * 200]])
    _, info = _first(result["ngn"])
    assert len(info["location"]) == imp.LOCATION_MAX


# ------------------------------------------------------------------ items/dates
def test_items_are_split_and_quantities_parsed():
    items = imp.parse_items("2x Ankara Gown, 1x Head Wrap, 3 bags")
    assert [i["qty"] for i in items] == [2, 1, 3]
    assert items[0]["name"] == "Ankara Gown"
    assert imp.total_quantity(items) == 6


def test_dates_normalise_to_iso_and_unparsed_text_is_kept():
    headers = ["Date", "Selling Price"]
    result = _import(headers, [["14/03/2024", "10,000"],
                               ["2024-03-15", "10,000"],
                               ["sometime", "10,000"]])
    dates = [i["date"] for _, i in result["ngn"]]
    assert dates[0] == "2024-03-14"
    assert dates[1] == "2024-03-15"
    assert dates[2] == "sometime"
    assert any("unparsed date" in w for _, w in result["warnings"])


def test_excel_date_serials_become_real_dates():
    assert imp.normalize_date("45000") == "2023-03-15"
    assert imp.normalize_date("2024-03-14") == "2024-03-14"


# ------------------------------------------------------------------ order shape
def test_built_order_is_a_confirmed_order_the_desk_can_read():
    """The whole point: entry_from_order must accept an imported row."""
    headers = ["Date", "Order NO", "Customer", "Currency", "Selling Price",
               "Supplier Cost", "Delivery Fee", "Country", "Items"]
    result = _import(headers, [
        ["2024-03-14", "ORD-1", "Ada", "NGN", "45,000", "28,000", "2,500",
         "Nigeria", "2x Ankara Gown"]])
    order, _ = _first(result["ngn"])
    entry = accounting.entry_from_order(order)
    assert entry["id"] == "ORD-1"
    assert entry["currency"] == "NGN"
    assert entry["customer"] == "Ada"
    assert entry["location"] == "Nigeria"
    assert entry["saleAmount"] == 45000
    assert entry["supplierCostNgn"] == 28000
    assert entry["deliveryExpense"] == 2500
    assert entry["netProfit"] == 45000 - 28000
    assert entry["archived"] is False
    assert entry["deleted"] is False


def test_cfa_imported_row_shows_converted_cost_through_the_desk():
    headers = ["Date", "Currency", "Selling Price", "Supplier costs · FCFA",
               "Country"]
    result = _import(headers, [
        ["2024-03-16", "CFA", "62,000", "24,000 FCFA", "Benin Republic"]])
    order, _ = _first(result["cfa"])
    entry = accounting.entry_from_order(order)
    assert entry["currency"] == "CFA"
    assert entry["supplierCostCfa"] == 24000
    assert entry["supplierCostInCurrency"] == 24000
    assert entry["location"] == "Benin Republic"
    assert entry["netProfit"] == 62000 - 24000


def test_imported_rows_are_marked_as_historical_snapshots():
    headers = ["Date", "Selling Price"]
    result = _import(headers, [["2024-03-14", "10,000"]])
    order, _ = _first(result["ngn"])
    snapshot = _payload(order)["accounting"]
    assert snapshot["snapshotSource"] == "legacy-estimate"
    assert snapshot["exchangeRate"] == float(RATE)
    assert order["status"] == "confirmed"
    assert order["source"] == "historical-import"
    assert _payload(order)["importSource"] == "historical-import"


def test_missing_order_ids_are_generated_and_duplicates_are_not_lost():
    headers = ["Date", "Order NO", "Selling Price"]
    result = _import(headers, [["2024-03-14", "", "10,000"],
                               ["2024-03-15", "ORD-1", "10,000"],
                               ["2024-03-16", "ORD-1", "10,000"]])
    ids = [o["id"] for o, _ in result["ngn"]]
    assert ids[0].startswith(imp.ID_PREFIX + "-")
    assert ids[1] == "ORD-1"
    assert ids[2] == "ORD-1-2"          # the second row is kept, not dropped
    assert len(set(ids)) == 3


def test_blank_rows_are_ignored_without_failing():
    headers = ["Date", "Order NO", "Selling Price"]
    result = _import(headers, [["2024-03-14", "ORD-1", "10,000"],
                               ["", "", ""],
                               ["", "", ""],
                               ["2024-03-15", "ORD-2", "10,000"]])
    assert len(result["ngn"]) == 2


# ----------------------------------------------------------------------- output
def test_review_csvs_use_the_exact_ledger_headers(tmp_path):
    headers = ["Date", "Order NO", "Customer", "Currency", "Selling Price",
               "Supplier Cost", "Country"]
    result = _import(headers, [
        ["2024-03-14", "ORD-1", "Ada", "NGN", "45,000", "28,000", "Nigeria"],
        ["2024-03-15", "ORD-2", "Marie", "CFA", "62,000", "24,000 FCFA",
         "Benin Republic"]])
    paths = imp.write_csvs(result, str(tmp_path), RATE)
    for currency, path, count in paths:
        assert count == 1
        with open(path, encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        assert rows[0] == google_sheets._headers(currency)
        assert len(rows[1]) == 12
        assert rows[1][11]                      # Location / Destination filled
        assert rows[1][2] in ("Ada", "Marie")   # customer
    assert [c for c, _, _ in paths] == ["NGN", "CFA"]


def test_the_ledger_row_layout_matches_the_sheets_writer():
    """Column count must match google_sheets' own header, or data is lost."""
    headers = ["Date", "Order NO", "Customer", "Currency", "Selling Price",
               "Country"]
    result = _import(headers, [
        ["2024-03-14", "ORD-1", "Ada", "NGN", "45,000", "Nigeria"]])
    rows = imp.ledger_rows(result, RATE)
    assert len(rows["NGN"][0]) == len(google_sheets._headers("NGN"))
    assert len(rows["NGN"][0]) == 12


def test_google_sheets_writes_all_twelve_columns():
    """Regression: the append/update ranges were A:K while the row builder
    emits A:L, so the Location column was silently dropped on every push."""
    source = open(os.path.join(ROOT, "google_sheets.py"),
                  encoding="utf-8").read()
    assert "!A:K" not in source, "an 11-column order range survived - " \
                                 "the Location column would not be written"
    assert 'range_name = f"\'{tab}\'!A:L"' in source
    assert 'range_name = f"\'{tab}\'!A{position}:L{position}"' in source
    assert len(google_sheets._headers("NGN")) == 12
    assert google_sheets._headers("NGN")[11] == "Location / Destination"


# ----------------------------------------------------------------- end-to-end
def test_end_to_end_dry_run_writes_nothing(tmp_path, monkeypatch):
    """--confirm is required; a plain run must never touch the store."""
    path = tmp_path / "old.csv"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Order Date", "Order NO", "Customer Name", "Currency",
                         "Selling Price", "Cost Price", "Country"])
        writer.writerow(["2024-03-14", "ORD-1", "Ada", "NGN", "45,000",
                         "28,000", "Nigeria"])
        writer.writerow(["2024-03-15", "ORD-2", "Marie", "CFA", "62,000",
                         "24,000 FCFA", "Benin Republic"])

    def explode(*args, **kwargs):
        raise AssertionError("the dry run reached the writer")

    monkeypatch.setattr(imp, "stage_orders", explode)
    monkeypatch.setattr(imp, "existing_ids", explode)
    assert imp.main(["--csv", str(path), "--out-dir",
                     str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "historical-ngn-ledger.csv").exists()
    assert (tmp_path / "out" / "historical-cfa-ledger.csv").exists()


def test_end_to_end_reads_a_real_export_file(tmp_path):
    """Reads back through the CSV reader so the parser is exercised too."""
    path = tmp_path / "old.csv"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Date", "Ref", "Client", "Cur.", "Amount", "Cost",
                         "Town"])
        writer.writerow(["14/03/2024", "X-1", "Ada", "NGN", "₦45,000",
                         "₦28,000", "Lagos"])
        writer.writerow(["15/03/2024", "X-2", "Marie", "CFA", "62,000 FCFA",
                         "35 000", "Cotonou"])
    headers, rows = imp.read_table(str(path))
    result = _import(headers, rows)
    assert [o["id"] for o, _ in result["ngn"]] == ["X-1"]
    assert [o["id"] for o, _ in result["cfa"]] == ["X-2"]
    ngn_order, ngn_info = _first(result["ngn"])
    assert ngn_info["revenue"] == 45000
    assert ngn_info["location"] == "Lagos"
    cfa_order, cfa_info = _first(result["cfa"])
    assert cfa_info["revenue"] == 62000
    # A silent "Cost" header on a CFA row means the figure is already CFA, so
    # it round-trips: 35,000 FCFA -> 79,545 NGN stored -> 35,000 FCFA shown.
    assert cfa_info["supplierCostCfa"] == 35000
    assert cfa_info["supplierCostNgn"] == 79545
    assert accounting.entry_from_order(cfa_order)["location"] == "Cotonou"


def test_a_header_naming_the_currency_overrides_the_round_trip():
    """'Cost (NGN)' on a CFA row really is Naira, so it converts down."""
    headers = ["Date", "Currency", "Selling Price", "Cost (NGN)"]
    result = _import(headers, [["15/03/2024", "CFA", "62,000", "35 000"]])
    _, info = _first(result["cfa"])
    assert info["supplierCostNgn"] == 35000
    assert info["supplierCostCfa"] == 15400          # 35,000 NGN @ 0.44
