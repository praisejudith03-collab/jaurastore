"""Automatic language/currency detection, checkout cleanup, floating pill
and the finer hero (owner request 2026-09-27).

The shopping-facing rules pinned here:

  * the device language is detected on every page load (navigator.languages /
    navigator.language) - French renders the French interface and LOCKS the
    currency to FCFA; English and everything else keeps English with prices
    in Naira first;
  * the "Benin/Togo delivery detected - choose your currency" confirm()
    popup was removed from the checkout (fares stay silent, in the active
    currency);
  * the floating white currency pill (English storefront only) sits pinned
    above the WhatsApp bubble;
  * the hero got the polite greeting, the finer serif headline and the
    CTA pop-in + gentle pulse.

The runtime half boots the real js/i18n.js, js/store.js and js/app.js in a
stubbed browser: tests/_auto_locale_sim.mjs. The static half is asserted
here directly, so a future edit cannot silently remove the wiring.

Run with:  python3 -m pytest tests/test_auto_locale_rules.py -q
"""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(ROOT, "tests", "_auto_locale_sim.mjs")


def test_automatic_locale_simulation_passes():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (frontend sim needs Node >= 18)")
    proc = subprocess.run([node, SIM], cwd=ROOT, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, (
        "auto-locale simulation failed:\n" + proc.stdout + proc.stderr)
    assert "all auto-locale checks passed" in proc.stdout


# ----------------------------------------------------------------- i18n.js

def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_i18n_detects_the_device_language_on_load():
    src = _read(os.path.join("js", "i18n.js"))
    assert "detectBrowserLang" in src, "the detector is missing"
    assert "navigator.languages" in src and "navigator.language" in src, (
        "detection must read the device's preferred languages")
    # readStored: URL override, then detection - never a stale stored choice
    body = src.split("function readStored()", 1)[1][:400]
    assert "detectBrowserLang()" in body, (
        "every page load must fall back to the device language")
    assert "sessionStorage.getItem(KEY)" not in body and "localStorage.getItem(KEY)" not in body, (
        "a stale stored language must NOT win over the device language")


def test_i18n_carries_the_hero_greeting_and_verbatim_headline():
    src = _read(os.path.join("js", "i18n.js"))
    assert '"home.kicker": "Welcome. Ready to shop?"' in src
    assert '"home.kicker": "Bienvenue. Prêt à faire vos achats ?"' in src
    assert '"home.heroLine": "Experience effortless elegance and curated essentials"' in src, (
        "the main hero headline must stay verbatim")


# ---------------------------------------------------------------- store.js

def test_store_locks_french_to_fcfa_and_defaults_english_to_naira():
    src = _read(os.path.join("js", "store.js"))
    assert "function currencyLocked()" in src, "the FCFA lock helper is missing"
    body = src.split("function currency()", 1)[1][:200]
    assert 'currencyLocked()) return "CFA"' in body, (
        "a French interface must always answer FCFA")
    assert 'localStorage.getItem(KEYS.currency) || "NGN"' in body, (
        "the English default must stay Naira FIRST on page load")
    settle = src.split("function setCurrency(", 1)[1][:240]
    assert 'currencyLocked() ? "CFA"' in settle, (
        "setCurrency must not talk a French storefront out of FCFA")


def test_the_old_confirm_popup_is_gone_from_checkout():
    src = _read(os.path.join("js", "app.js"))
    assert "promptCurrencyForBeninTogo" not in src
    assert "delivery detected. Choose your currency" not in src
    assert "Togo delivery detected" not in src and "Benin delivery detected" not in src


def test_checkout_surfaces_the_gateway_for_the_active_currency():
    src = _read(os.path.join("js", "app.js"))
    body = src.split("function paintCheckoutTotals(", 1)[1]
    body = body.split("/** One zone ->", 1)[0]
    assert "JA.currencyLocked()" in body, (
        "the French FCFA lock must be enforced where the gateways are painted")
    assert r'[name=currency][value=\"CFA\"]' in body, (
        "the FCFA gateway must be pre-selected in French mode")
    assert re.search(r'value\s*===\s*"NGN"\)\s*card\.hidden\s*=\s*true', body), (
        "the Naira pay-card must be hidden from a FCFA-locked checkout")
    assert 'data-bank-ngn]' in body and 'data-bank-cfa]' in body, (
        "the per-currency bank sheets must keep driving the visible gateway")


# ------------------------------------------------------------------- hero

def test_hero_got_the_finer_serif_and_the_entry_motions():
    css = _read(os.path.join("css", "style.css"))
    hero = css.split(".home-hero-static h1,", 1)
    assert len(hero) == 2, "the refined hero headline rule is missing"
    block = hero[1][:600]
    assert '"Cormorant Garamond"' in block and '"Playfair Display"' in block, (
        "the headline must use the luxurious fine serif stack")
    assert "font-weight: 400" in block, "the finer look keeps a light 400 weight"
    assert "line-height: 1.14" in block, "open line-height requested"
    assert "letter-spacing" in block and "0.015em" in block, (
        "elegant letter-spacing requested")
    assert "@keyframes jaHeroSlideIn" in css, "soft entry slide-in missing"
    assert "@keyframes jaHeroPopIn" in css, "CTA pop-in zoom missing"
    assert "@keyframes jaHeroPulse" in css, "CTA gentle pulse missing"
    assert ".home-hero-static__cta .btn-purple" in css, (
        "the CTA animations must target the hero's purple button")
    assert "prefers-reduced-motion" in css, (
        "the hero animations must respect reduced-motion users")


def test_the_moving_banner_was_not_touched():
    """The ticker stays exactly as it was: same builder, same paint hooks."""
    src = _read(os.path.join("js", "store.js"))
    assert "function convBannerHTML()" in src
    assert "function paintConvBanner()" in src
    assert '<div class="conv-bar" role="status" data-no-i18n>' in src
    assert 'class="conv-track"' in src
