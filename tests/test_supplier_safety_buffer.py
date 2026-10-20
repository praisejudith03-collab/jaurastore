"""The owner's 2026-10-19 stock buffer (supersedes the 1:1 rule).

Supplier 0 / Sold Out -> out immediately. High supplier stock -> publish
60-80% of it (a 20-40% safety buffer, default 30%: supplier 50 -> 35). Small
counts stay exact so a single available unit is never rounded to zero.

Run with: python3 -m pytest tests/test_supplier_safety_buffer.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")

import supplier_watchdog as sw  # noqa: E402


def test_default_buffer_is_thirty_percent_and_inside_the_owner_band():
    assert sw.DEFAULT_SAFETY_BUFFER_PERCENT == 30
    assert sw.MIN_SAFETY_BUFFER_PERCENT == 20
    assert sw.MAX_SAFETY_BUFFER_PERCENT == 40
    assert sw.safety_buffer_percent() == 30


def test_supplier_fifty_shows_thirty_to_thirty_five():
    """Owner example: supplier 50 -> 30-35 shown, never the full 50."""
    shown = sw._apply_stock_rule(50, 0)
    assert 30 <= shown <= 35, shown
    assert shown <= 50


def test_high_stock_is_buffered_but_never_exceeds_the_supplier():
    for qty, expected in ((10, 7), (25, 17), (40, 28), (100, 70), (1000, 700)):
        got = sw._apply_stock_rule(qty, 0)
        assert got == expected, (qty, got, expected)
        assert 0 < got <= qty
        # 60-80% of the supplier count = a 20-40% buffer.
        assert qty * 0.60 <= got <= qty * 0.80 + 1


def test_small_counts_are_exact_so_one_unit_never_becomes_zero():
    for qty in (1, 2, 3, 4, 5, 6, 7, 8, 9):
        assert sw._apply_stock_rule(qty, 0) == qty


def test_supplier_zero_or_sold_out_is_immediate_out_of_stock():
    assert sw._apply_stock_rule(0, 9) == 0
    assert sw._apply_stock_rule(0, 0, flagged=False) == 0


def test_option_rule_uses_the_same_buffer():
    assert sw._apply_option_stock_rule(40, 0) == 28
    assert sw._apply_option_stock_rule(3, 0) == 3
    assert sw._apply_option_stock_rule(0, 7) == 0


def test_buffer_percentage_is_clamped_to_twenty_forty(monkeypatch):
    monkeypatch.setenv("SUPPLIER_SAFETY_BUFFER_PERCENT", "5")
    assert sw.safety_buffer_percent() == 20
    assert sw._apply_stock_rule(50, 0) == 40
    monkeypatch.setenv("SUPPLIER_SAFETY_BUFFER_PERCENT", "99")
    assert sw.safety_buffer_percent() == 40
    assert sw._apply_stock_rule(50, 0) == 30
    monkeypatch.setenv("SUPPLIER_SAFETY_BUFFER_PERCENT", "35")
    assert sw.safety_buffer_percent() == 35
    assert sw._apply_stock_rule(50, 0) == 32


def test_a_bare_in_stock_flag_has_no_count_to_buffer(monkeypatch):
    monkeypatch.setattr(sw, "SUPPLIER_IN_STOCK_UNITS", 10)
    assert sw._apply_stock_rule(1, 0, flagged=True) == 10
    assert sw._apply_stock_rule(1, 25, flagged=True) == 25
