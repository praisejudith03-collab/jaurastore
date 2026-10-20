"""Every storefront page, on a French device, renders zero English.

Directive 2026-10-19: "100 % French localization fallback / zero English". The
dictionary covers markup, the phrase table covers copy built by JavaScript and
the server fills nameFr/descriptionFr for catalogue rows — but nothing ran all
three together on each page, so a policy paragraph or a footer default could
stay English while every other test stayed green.

This test boots each shipped page in jsdom with a French device, with the API
deliberately silent (the cold first visit a slow phone actually sees), and
fails if any rendered text node still holds a sentence made of English
function words. See tests/_french_pages_check.mjs for the full contract.

Run with: python3 -m pytest tests/test_french_storefront_pages.py -q
"""
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tests", "_french_pages_check.mjs")


def test_no_storefront_page_renders_english_for_a_french_device():
    """jsdom is a developer dependency (`npm install jsdom`, or JA_JSDOM_DIR),
    so a laptop without it skips. CI installs it and sets JA_REQUIRE_BROWSER,
    where a missing install is a FAILURE — a green run must never mean the
    copy was not exercised."""
    result = subprocess.run(["node", SCRIPT], cwd=ROOT, text=True,
                            capture_output=True, timeout=600)
    if result.returncode == 3:
        message = ("jsdom is not installed (npm install jsdom, or set "
                   "JA_JSDOM_DIR); the French page check did not run")
        if os.environ.get("JA_REQUIRE_BROWSER") == "1":
            pytest.fail(message + " - CI installs it, so this is a broken build")
        pytest.skip(message)
    assert result.returncode == 0, (
        "a storefront page still renders English for a French device:\n"
        + result.stdout + "\n" + result.stderr)
    assert "FRENCH PAGES CHECKS PASSED" in result.stdout


def test_the_check_covers_every_shopper_facing_page():
    """A page that is not in the list is a page nobody is proving French on.
    Adding a storefront page therefore has to add it here too."""
    script = open(SCRIPT, encoding="utf-8").read()
    pages = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    assert pages  # keep the read honest if the repo moves
    for page in (
        "index.html", "shop.html", "product.html", "cart.html", "checkout.html",
        "categories.html", "wishlist.html", "account.html", "order-complete.html",
        "faq.html", "about.html", "contact.html", "delivery.html",
        "shipping.html", "returns.html", "privacy.html", "terms.html", "404.html",
    ):
        assert f'"{page}"' in script, f"{page} is missing from the French page check"
        assert os.path.exists(os.path.join(ROOT, page)), page


def test_the_owner_tools_are_not_swept_and_stay_english():
    """The admin and accounting desks are the owner's own tools: they are not
    part of the shopper experience, and sweeping them would fight the
    owner's data entry."""
    script = open(SCRIPT, encoding="utf-8").read()
    assert '"admin.html"' not in script
    assert '"accounting.html"' not in script
