"""The evening broadcast generator and its out-of-stock guard.

The post is the shop's daily "what can I actually sell tonight" message, so
the rules that matter are the ones that keep it honest:

  * a variant is listed only when its own stock is above zero;
  * the inventory watchdog's durable reading overrides a stale row - a colour
    it watched hit zero never reappears, whatever the browser cached;
  * prices appear in Naira and F CFA, converted with the storefront's own
    ``currency.to_cfa`` so the post never disagrees with the product page;
  * every line links to a real storefront URL (``/products/<slug>``, plural -
    the route app.py actually serves).

Run with:  python3 -m pytest tests/test_evening_broadcast.py -q
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import broadcast_posts as bp  # noqa: E402
import currency  # noqa: E402

RATE = 0.44


def _product(pid="p1", name="Tote", slug="tote", category="Bags",
             price=18500, qty=4, options=None, online=True):
    product = {"id": pid, "name": name, "slug": slug, "category": category,
               "priceNgn": price, "online": online}
    if options is not None:
        product["optionStock"] = options
        product["stock_quantity"] = sum(options.values())
    else:
        product["stock_quantity"] = qty
    return product


# ------------------------------------------------------------- availability
def test_only_in_stock_variants_are_listed():
    product = _product(options={"Red": 3, "Blue": 1, "Green": 0})
    options = bp.available_options(product)
    assert [label for label, _ in options] == ["Red", "Blue"]
    assert dict(options) == {"Red": 3, "Blue": 1}


def test_a_product_whose_every_variant_is_zero_is_not_postable():
    product = _product(options={"Red": 0, "Blue": 0})
    assert bp.available_options(product) == []
    assert bp.is_postable(product) is False


def test_a_simple_product_follows_its_own_quantity():
    assert bp.available_options(_product(qty=2)) == [(bp.NO_OPTION_KEY, 2)]
    assert bp.available_options(_product(qty=0)) == []
    assert bp.is_postable(_product(qty=0)) is False


def test_hidden_products_are_never_postable():
    assert bp.is_postable(_product(qty=5, online=False)) is False


def test_deleted_products_are_never_postable():
    product = _product(qty=5)
    product["deleted"] = True
    assert bp.is_postable(product) is False


# ------------------------------------------------------------ watchdog guard
def test_the_watchdog_blocklist_overrides_a_positive_stock_row():
    """The whole point: stale frontend state must not resurrect a dead colour."""
    product = _product(options={"Red": 3, "Blue": 1})
    blocked = {bp.oos_key("p1", "Red")}
    options = bp.available_options(product, oos=blocked)
    assert [label for label, _ in options] == ["Blue"]


def test_blocking_every_variant_removes_the_whole_product():
    product = _product(options={"Red": 3})
    assert bp.is_postable(product, oos={bp.oos_key("p1", "Red")}) is False


def test_a_whole_product_can_be_blocked_with_an_empty_option_key():
    product = _product(qty=9)
    assert bp.is_postable(product, oos={"p1::"}) is False


def test_blocklist_matching_is_case_and_space_insensitive():
    product = _product(options={"Sky Blue": 2})
    assert bp.available_options(product, oos={"p1::  sky   blue  "}) == []


def test_blocking_one_product_does_not_touch_another():
    other = _product(pid="p2", name="Satchel", slug="satchel", qty=3)
    assert bp.available_options(other, oos={bp.oos_key("p1", "Red")}) != []


def test_an_empty_or_broken_blocklist_blocks_nothing():
    """The guard fails open to the database, never to a guess."""
    product = _product(options={"Red": 3})
    assert bp.available_options(product, oos=None) == [("Red", 3)]
    assert bp.available_options(product, oos=set()) == [("Red", 3)]
    assert bp.available_options(product, oos=["nonsense"]) == [("Red", 3)]


# -------------------------------------------------------------------- prices
def test_prices_appear_in_both_currencies_using_the_storefront_conversion():
    post = bp.build_post([_product(price=18500)], rate=RATE)
    expected_cfa = currency.to_cfa(18500, RATE)
    assert f"₦18,500 · {expected_cfa:,} FCFA" in post["text"]
    assert post["lines"][0]["priceCfa"] == expected_cfa


def test_an_option_price_override_beats_the_product_price():
    product = _product(price=18500, options={"Red": 3})
    product["optionPrices"] = {"Red": 21000}
    post = bp.build_post([product], rate=RATE)
    assert "₦21,000" in post["text"]


def test_a_live_rate_is_used_when_none_is_supplied():
    """No literal in the source: the rate comes from the admin setting."""
    post = bp.build_post([_product(price=10000)])
    assert 0 < float(post["rate"]) <= 100
    assert post["lines"][0]["priceCfa"] == currency.to_cfa(10000, post["rate"])


# ---------------------------------------------------------------------- links
def test_every_line_carries_a_real_storefront_deep_link():
    post = bp.build_post([_product(slug="ankara tote")], rate=RATE)
    assert "https://jaurastore.com.ng/products/ankara%20tote" in post["text"]


def test_the_product_route_is_plural_because_that_is_what_app_serves():
    """/product/<slug> does not resolve - app.py serves /products/<slug>."""
    assert bp.PRODUCT_PATH == "/products/"
    assert bp.product_url("tote") == "https://jaurastore.com.ng/products/tote"
    source = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert '@app.route("/products/<slug>")' in source


def test_a_product_without_a_slug_gets_no_link():
    post = bp.build_post([_product(slug="")], rate=RATE)
    assert "https://" not in post["text"]


# ----------------------------------------------------------------- the copy
def test_the_post_groups_by_category_in_order():
    post = bp.build_post([
        _product(pid="b", name="Zebra Bag", slug="z", category="Bags"),
        _product(pid="a", name="Apple Cream", slug="a", category="Skincare"),
        _product(pid="c", name="Another Bag", slug="c", category="Bags"),
    ], rate=RATE)
    assert post["categories"] == ["Bags", "Skincare"]
    assert post["text"].index("*Bags*") < post["text"].index("*Skincare*")


def test_the_plain_variant_has_no_markdown_markers():
    post = bp.build_post([_product()], rate=RATE)
    assert "*" not in post["plain"]
    assert "_" not in post["plain"]
    assert "Tote" in post["plain"]
    # The markdown version still carries the emphasis.
    assert "*Tote*" in post["text"]


def test_the_post_reports_what_it_left_out_and_why():
    post = bp.build_post([
        _product(pid="ok", name="Fine", slug="fine", qty=2),
        _product(pid="dead", name="Gone", slug="gone", qty=0),
        _product(pid="hid", name="Hidden", slug="hidden", qty=5, online=False),
    ], rate=RATE)
    reasons = {s["id"]: s["reason"] for s in post["skipped"]}
    assert reasons["dead"] == "out of stock"
    assert reasons["hid"] == "hidden"
    assert post["productCount"] == 1
    assert post["skippedCount"] == 2


def test_a_watchdog_blocked_product_is_reported_as_such():
    product = _product(options={"Red": 3})
    post = bp.build_post([product], oos={bp.oos_key("p1", "Red")}, rate=RATE)
    assert post["skipped"][0]["reason"] == "out of stock (watchdog)"


def test_counts_and_totals_are_reported():
    post = bp.build_post([
        _product(pid="a", options={"Red": 2, "Blue": 1}),
        _product(pid="b", name="Plain", slug="plain", qty=3),
    ], rate=RATE)
    assert post["productCount"] == 2
    assert post["optionCount"] == 3


def test_max_products_caps_the_post():
    many = [_product(pid=f"p{i}", name=f"Item {i}", slug=f"i{i}") for i in range(10)]
    post = bp.build_post(many, rate=RATE, max_products=4)
    assert post["productCount"] == 4


def test_a_footer_and_title_can_be_customised():
    post = bp.build_post([_product()], rate=RATE, title="Friday Drop",
                         footer="Order before 6pm.")
    assert "*Friday Drop*" in post["text"]
    assert "Order before 6pm." in post["text"]


# ---------------------------------------------------------------- robustness
def test_an_empty_catalogue_produces_an_empty_post_without_raising():
    for products in (None, [], [None], [{}]):
        post = bp.build_post(products, rate=RATE)
        assert post["productCount"] == 0


def test_build_post_from_products_never_raises():
    result = bp.build_post_from_products([{"id": "x", "optionStock": "not json"}],
                                         rate=RATE)
    assert result["productCount"] == 0


def test_no_hardcoded_exchange_rate_in_the_module():
    """The rate must come from the admin setting, never a baked-in number."""
    path = os.path.join(ROOT, "broadcast_posts.py")
    source = open(path, encoding="utf-8").read()
    body = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    assert "0.44" not in body, "a literal exchange rate crept into the module"


def test_junk_stock_quantities_read_as_zero():
    """An unreadable number must never be advertised as available stock."""
    product = {"id": "p1", "name": "Tote", "slug": "tote", "priceNgn": 18500,
               "online": True, "stock_quantity": 4,
               "optionStock": {"Red": "abc", "Blue": None}}
    assert bp.option_stock(product) == {"Red": 0, "Blue": 0}
    assert bp.is_postable(product) is False
