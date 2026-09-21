"""F CFA conversion rounding: Naira is exact, CFA is rounded UP to 50 / 100.

Naira (₦) is the BASE currency and is never modified. F CFA is CONVERTED from
Naira for display, cart totals and checkout, and every converted amount must
land on a clean 50 / 100 step:

    24 CFA -> 50 CFA     (anything below 50 rounds UP to 50)
    64 CFA -> 100 CFA    (anything above 50 rounds UP to the next 100)

Run with:  python3 -m pytest tests/test_cfa_rounding.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest  # noqa: E402

import currency  # noqa: E402
import catalog as catalog_mod  # noqa: E402


# --------------------------------------------------------------- round_cfa
@pytest.mark.parametrize("raw,expected", [
    (0, 0),
    (1, 50),
    (24, 50),          # the reported case: below 50 rounds UP to 50
    (49, 50),
    (50, 50),          # already a clean step - untouched
    (51, 100),
    (64, 100),         # the reported case: above 50 rounds UP to 100
    (99, 100),
    (100, 100),
    (101, 150),
    (149, 150),
    (150, 150),
    (1013, 1050),
    (4400, 4400),
])
def test_cfa_amounts_always_land_on_a_50_or_100_step(raw, expected):
    assert currency.round_cfa(raw) == expected


def test_round_cfa_never_raises_and_never_goes_negative():
    assert currency.round_cfa(None) == 0
    assert currency.round_cfa("not a number") == 0
    assert currency.round_cfa(-500) == 0


def test_every_rounded_amount_is_a_multiple_of_the_step():
    for naira in range(0, 5000, 7):
        amount = currency.to_cfa(naira)
        assert amount % currency.CFA_STEP == 0, naira


def test_conversion_only_ever_rounds_up():
    for naira in range(1, 3000, 13):
        exact = naira * currency.NGN_TO_CFA
        assert currency.to_cfa(naira) >= exact
        assert currency.to_cfa(naira) - exact < currency.CFA_STEP


# --------------------------------------------------------- Naira is exact
def test_naira_is_the_base_currency_and_is_never_rounded():
    """A Naira price is stored and served EXACTLY as the admin typed it."""
    for odd in (1, 37, 999, 1013, 24999):
        product = catalog_mod.normalize({"id": "cfa-test", "name": "Test",
                                         "priceNgn": odd})
        assert product["priceNgn"] == odd


def test_cfa_derived_from_naira_is_a_clean_step():
    product = catalog_mod.normalize({"id": "cfa-test", "name": "Test",
                                     "priceNgn": 1013})
    assert product["priceCfa"] % currency.CFA_STEP == 0
    assert product["priceCfa"] == currency.to_cfa(1013)


def test_an_explicit_odd_cfa_price_is_snapped_to_a_clean_step():
    product = catalog_mod.normalize({"id": "cfa-test", "name": "Test",
                                     "priceCfa": 24})
    assert product["priceCfa"] == 50


def test_compare_at_cfa_price_is_also_rounded():
    product = catalog_mod.normalize({"id": "cfa-test", "name": "Test",
                                     "priceNgn": 1013, "compareNgn": 2027})
    assert product["compareNgn"] == 2027            # Naira untouched
    assert product["compareCfa"] % currency.CFA_STEP == 0


# ------------------------------------------- checkout / cart server pricing
def test_server_unit_price_in_cfa_is_always_a_clean_step():
    import api

    product = {"id": "p1", "priceNgn": 1013}
    price = api._server_unit_price(product, "CFA")
    assert price % currency.CFA_STEP == 0
    assert price == currency.to_cfa(1013)


def test_server_unit_price_in_naira_is_the_exact_base_amount():
    import api

    assert api._server_unit_price({"id": "p1", "priceNgn": 1013}, "NGN") == 1013


def test_option_variation_prices_round_in_cfa_too():
    import api

    product = {"id": "p1", "priceNgn": 1000,
               "optionPrices": {"Large": 1013}}
    price = api._server_unit_price(product, "CFA", variant="Large")
    assert price % currency.CFA_STEP == 0
    assert price == currency.to_cfa(1013)


def test_the_browser_rounding_matches_the_server():
    """js/store.js must implement the identical 50/100 ceiling."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source = open(os.path.join(root, "js", "store.js"), encoding="utf-8").read()
    assert "function roundCfa" in source
    assert "Math.ceil(v / CFA_STEP) * CFA_STEP" in source
    assert "const CFA_STEP = 50;" in source
