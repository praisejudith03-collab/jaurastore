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


def test_product_editor_excludes_redundant_discount_and_merchandising_fields():
    """Discount, merchandising and translation controls stay out of the
    product editor. Per-option SELLING PRICE and SUPPLIER COST inputs
    (data-opt-price / data-opt-cost) are approved editor controls since the
    variant-pricing spec (2026-10): they are pricing essentials, not
    merchandising extras — the old "Option price overrides" panel they
    replaced is still banned."""
    source = (ROOT / "js/admin.js").read_text(encoding="utf-8")
    form = source[source.index("function productForm(p = {})"):source.index("async function handleProductSubmit")]
    for removed in ("Option price overrides", "Promo Discount", "Best Seller",
                    "New Product Arrival", "Price reduction / strikethrough",
                    "bulkQty", "bulkPercent"):
        assert removed not in form, removed
    # Shop-wide volume tiers remain available outside the product editor.
    assert "bulkDiscountTiers" in source
    # The approved per-option pricing inputs exist (spec 2026-10) and the
    # deprecated overrides panel is still gone everywhere in the file.
    assert "data-opt-price" in source
    assert "data-opt-cost" in source
    assert "Option price overrides" not in source


def test_watchdog_runs_nightly_and_audits_service_health():
    """Nightly since 2026-10-19 (owner directive): the full catalogue deep
    audit runs once a day at 02:00 local server time (01:00 UTC). The
    watchdog's own two-run missing tolerance still survives, and supplier
    live updates are handled by the separate 20-minute workflow.

    The old "hourly" pin is intentionally superseded, not deleted: the
    schedule moved, the health audit it guards did not.
    """
    workflow = (ROOT / ".github/workflows/catalog-watchdog.yml").read_text(encoding="utf-8")
    watchdog = (ROOT / "tools/catalog_watchdog.py").read_text(encoding="utf-8")
    assert 'cron: "0 1 * * *"' in workflow
    assert 'cron: "0 * * * *"' not in workflow
    assert "fetch_service_health(base)" in watchdog
    assert '"/healthz"' in watchdog
