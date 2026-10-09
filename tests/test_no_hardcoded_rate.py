"""No hardcoded exchange rate may survive in a calculation path.

The store has ONE exchange rate: the admin-set `cfaRate` growth setting. A
literal `0.44` in a conversion is a silent bug - the shop converts at a rate
nobody set the moment the owner changes it - so this module pins the rule at
the source level as well as the behaviour level:

  * no module-level rate constant exists any more;
  * the shipped JS bundles carry no rate literal (the live rate arrives on
    GET /api/site);
  * every conversion follows the admin setting, in both directions;
  * the only surviving 0.44 is accounting.LEGACY_RATE, which exists purely to
    label pre-feature snapshots whose rate was never captured.

Run with:  python3 -m pytest tests/test_no_hardcoded_rate.py -q
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import currency  # noqa: E402
import growth  # noqa: E402


# ------------------------------------------------- no constant, no literal
def test_no_module_level_rate_constant_survives():
    """`NGN_TO_CFA` was the hardcoded fallback every path quietly used."""
    assert not hasattr(currency, "NGN_TO_CFA"), \
        "currency.NGN_TO_CFA is gone; conversions read currency.live_rate()"
    assert not hasattr(growth, "NGN_TO_CFA"), \
        "growth.NGN_TO_CFA is gone; the rate is the cfaRate growth setting"


def test_the_rate_comes_from_the_admin_setting(monkeypatch):
    """Change the admin setting and every conversion follows."""
    monkeypatch.setattr(growth, "settings",
                        lambda: {"cfaRate": 0.5})
    assert currency.live_rate() == 0.5
    # 4,800 NGN x 0.5 = 2,400, already a clean step.
    assert currency.to_cfa(4_800) == 2_400
    assert currency.to_ngn(2_400) == 4_800


def test_an_explicit_rate_wins_so_history_is_never_repriced(monkeypatch):
    monkeypatch.setattr(growth, "settings", lambda: {"cfaRate": 2.0})
    # A snapshot locked at 0.44 keeps converting at 0.44.
    assert currency.to_cfa(4_800, 0.44) == 2_150
    assert currency.to_ngn(2_200, 0.44) == 5_000


def test_a_blank_setting_falls_back_to_the_configured_default_never_a_literal(monkeypatch):
    """Even the fallback is configuration data (growth.DEFAULTS), not a
    number copied into the code."""
    monkeypatch.setattr(growth, "settings", lambda: {"cfaRate": 0})
    assert currency.live_rate() == growth.DEFAULTS["cfaRate"]


# ------------------------------------------------------------- shipped bytes
FRONTEND = ("js/store.js", "js/app.js", "js/admin.js", "js/accounting.js",
            "css/style.css")


def test_no_frontend_bundle_carries_a_rate_literal():
    """The storefront converts from the live /api/site row, so a bundle must
    not ship a rate of its own - a stale bundle would silently reprice the
    whole shop."""
    offenders = []
    for name in FRONTEND:
        text = (ROOT / name).read_text(encoding="utf-8")
        # "0.44" used as a conversion factor (not, say, inside a version
        # string or an unrelated decimal) is what we are hunting.
        for match in re.finditer(r"[^\w.]0\.44\b", text):
            line = text[:match.start()].count("\n") + 1
            offenders.append(f"{name}:{line}")
    assert not offenders, \
        "hardcoded 0.44 rate in the shipped frontend: " + ", ".join(offenders)


def test_the_only_surviving_044_is_the_marked_historical_baseline():
    """accounting.LEGACY_RATE is deliberately kept: pre-feature snapshots have
    no captured rate and must never drift. It is referenced, never a fallback
    for live calculations."""
    import accounting
    assert str(accounting.LEGACY_RATE) == "0.44"
    source = (ROOT / "accounting.py").read_text(encoding="utf-8")
    # current_exchange_rate() must NOT fall back to it.
    body = re.search(r"def current_exchange_rate\(\):.*?\n\n\n", source,
                     re.S).group(0)
    assert "LEGACY_RATE" not in body.split("except")[-1], \
        "the live rate must fall back to growth.DEFAULTS, never LEGACY_RATE"


def test_the_site_row_serves_the_live_rate():
    """/api/site carries cfaRate so the storefront never has to guess."""
    source = (ROOT / "api.py").read_text(encoding="utf-8")
    assert 'site["cfaRate"]' in source
