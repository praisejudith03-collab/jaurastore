"""Rate copy in a real DOM: never a stale number, never a raw token.

The footer line and the FAQ answers quote the exchange rate, and that rate is
admin-controlled. Static copy gets exactly one thing wrong: the page paints
BEFORE GET /api/site answers, so on a first visit (empty cache) there is no
rate yet.

The contract (proved by tests/_rate_copy_dom_check.mjs in jsdom against the
shipped js/i18n.js):

  * with no rate yet, copy shows a pending marker - not a hardcoded number
    and not the literal "{rate}" token;
  * when the live row lands (ja:site), the copy re-renders with the real rate,
    in English ("0.45") and French ("0,45");
  * changing the admin rate changes the copy - it is live, not frozen.

Run with:  python3 -m pytest tests/test_rate_copy_in_dom.py -q
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

SCRIPT = os.path.join(ROOT, "tests", "_rate_copy_dom_check.mjs")


def test_the_dictionary_renders_a_live_token_not_a_frozen_number():
    """The rate strings carry a {rate} placeholder the engine fills at render
    time, so a rate change cannot be missed by a stale translation."""
    i18n = open(os.path.join(ROOT, "js", "i18n.js"), encoding="utf-8").read()
    assert "{rate}" in i18n, "the rate strings must render from the live token"


def test_rate_copy_renders_from_the_live_rate_in_a_real_dom():
    """jsdom is a developer dependency (`npm install jsdom`, or
    ``JA_JSDOM_DIR``), so a laptop without it skips. CI sets
    ``JA_REQUIRE_BROWSER=1`` and installs jsdom, so there a missing install is
    a FAILURE - a green CI run must never mean "the copy was not exercised".
    """
    result = subprocess.run(["node", SCRIPT], cwd=ROOT, text=True,
                            capture_output=True, timeout=180)
    if result.returncode == 3:
        message = ("jsdom is not installed (npm install jsdom, or set "
                   "JA_JSDOM_DIR); the rate copy DOM check did not run")
        if os.environ.get("JA_REQUIRE_BROWSER") == "1":
            pytest.fail(message + " - CI installs it, so this is a broken build")
        pytest.skip(message)
    assert result.returncode == 0, (
        "rate copy DOM check failed:\n" + result.stdout + "\n" + result.stderr)
    assert "RATE COPY DOM CHECKS PASSED" in result.stdout
