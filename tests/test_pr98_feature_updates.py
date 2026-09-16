"""Regression coverage for configurable discounts, variants and health jobs."""
import datetime
from pathlib import Path

import abandoned
import api
import catalog
import growth


ROOT = Path(__file__).resolve().parents[1]


def test_no_static_bulk_discount_and_tiers_choose_highest_threshold():
    assert growth.DEFAULTS["bulkDiscountTiers"] == []
    assert growth.bulk_discount_percent(100, []) == 0
    tiers = growth._cap({**growth.DEFAULTS, "bulkDiscountTiers": [
        {"minQuantity": 30, "percent": 20},
        {"minQuantity": 10, "percent": 5},
        {"minQuantity": 15, "percent": 10},
    ]})["bulkDiscountTiers"]
    assert [growth.bulk_discount_percent(qty, tiers) for qty in (9, 10, 15, 30)] == [0, 5, 10, 20]


def test_variant_prices_are_cleaned_and_inherit_when_missing():
    product = catalog.normalize({
        "id": "jau-priced-options", "name": "Bag", "priceNgn": 1000,
        "priceCfa": 440, "stock": 5,
        "options": [{"title": "Colour", "values": ["Blue", "Brown"]}],
        "optionPrices": {"Colour: Brown": 1250, "bad": "not-a-price"},
    })
    assert product["optionPrices"] == {"Colour: Brown": 1250}
    assert api._server_unit_price(product, "NGN", "Colour: Brown") == 1250
    assert api._server_unit_price(product, "NGN", "Colour: Blue") == 1000
    assert api._server_unit_price(product, "CFA", "Colour: Brown") == 550


def test_abandoned_cart_threshold_is_twenty_minutes():
    assert abandoned.REMINDER_AFTER_MINUTES == 20
    cutoff = datetime.datetime.fromisoformat(abandoned._cutoff())
    age = datetime.datetime.utcnow() - cutoff
    assert datetime.timedelta(minutes=19, seconds=55) <= age <= datetime.timedelta(minutes=20, seconds=5)


def test_admin_exposes_discount_and_merchandising_controls():
    source = (ROOT / "js/admin.js").read_text(encoding="utf-8")
    for label in ("Flexible bulk / volume discounts", "Minimum quantity",
                  "Option price overrides", "Promo Discount", "Best Seller",
                  "New Product Arrival", "Price reduction / strikethrough"):
        assert label in source
    assert "data-opt-price" in source and "bulkDiscountTiers" in source


def test_watchdog_runs_every_twenty_minutes_and_audits_service_health():
    workflow = (ROOT / ".github/workflows/catalog-watchdog.yml").read_text(encoding="utf-8")
    watchdog = (ROOT / "tools/catalog_watchdog.py").read_text(encoding="utf-8")
    assert 'cron: "*/20 * * * *"' in workflow
    assert "fetch_service_health(base)" in watchdog
    assert '"/healthz"' in watchdog
