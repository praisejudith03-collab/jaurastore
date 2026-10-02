"""Locale selection / defaulting fix (owner request 2026-09-27).

The live storefront was opening in French for visitors who should see
English (₦ Naira, English navigation, English product cards, English
checkout and delivery-zone copy). Root cause: the interface language was
being decided from the visitor's device/browser locale
(`navigator.languages` / `navigator.language`), which a Nigerian/English
visitor cannot control and which does not reliably reflect their preferred
storefront language.

The rules pinned here:

  * ENGLISH is the default storefront language for EVERY visitor. It is
    NEVER inferred from the visitor's IP address, geography, browser region
    or device locale.
  * French only renders when the shopper EXPLICITLY asks for it - opening a
    link with `?lang=fr`, or a call to `I18N.setLang("fr")`. Either way the
    choice is persisted (localStorage `jaura_lang`, existing storage
    mechanism) and is honoured on every later page load and every page of
    the site, until the shopper explicitly changes it again.
  * A stale/garbage value under the storage key (anything that is not
    exactly "en" or "fr") is purged on load so it can never leak French/F
    CFA onto a fresh English visitor.
  * The currency still follows the active language: French locks FCFA;
    English opens in Naira first and the floating pill still switches it.
  * The "Benin/Togo delivery detected - choose your currency" confirm()
    popup stays removed from the checkout (fares stay silent, in the active
    currency).
  * The floating white currency pill (English storefront only) sits pinned
    above the WhatsApp bubble.

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


def test_english_is_the_default_never_inferred_from_the_device():
    src = _read(os.path.join("js", "i18n.js"))
    body = src.split("function readStored()", 1)[1].split("\n  }", 1)[0]
    assert 'return "en";' in body, (
        "a fresh visitor with no explicit choice must default to English")
    assert "detectBrowserLang()" not in body, (
        "the default language must never be decided by the device/browser "
        "locale (Nigerian/English visitors must not be forced into French)")
    assert "navigator.language" not in body and "navigator.languages" not in body, (
        "readStored() must not consult the device language at all")


def test_explicit_choice_is_persisted_and_read_back():
    src = _read(os.path.join("js", "i18n.js"))
    # setLang() must persist to the existing storage mechanism.
    set_lang = src.split("function setLang(", 1)[1][:400]
    assert "persist(value)" in set_lang or "localStorage.setItem(KEY" in set_lang, (
        "setLang must persist the explicit choice")
    # readStored() must actually read a previously persisted choice back —
    # this is the bug: the old build wrote the choice but never read it.
    body = src.split("function readStored()", 1)[1].split("\n  }", 1)[0]
    assert "storedChoice()" in body or "localStorage.getItem(KEY)" in body, (
        "an explicit stored choice must be honoured on later loads")
    # the ?lang= URL override remains, and it also persists so the choice
    # sticks past the single page it was opened on.
    assert 'get("lang")' in body
    assert "persist(picked)" in body or "localStorage.setItem(KEY" in body


def test_stale_locale_storage_is_migrated_away():
    src = _read(os.path.join("js", "i18n.js"))
    assert "function migrateStaleLocale()" in src, (
        "a migration guard must clear invalid stored locale values"
    )
    assert "migrateStaleLocale();" in src, "the migration must run on load"
    guard = src.split("function migrateStaleLocale()", 1)[1][:800]
    assert "removeItem(KEY)" in guard, (
        "a value that is not exactly \"en\"/\"fr\" must be purged")


def test_i18n_carries_the_hero_greeting_and_verbatim_headline():
    src = _read(os.path.join("js", "i18n.js"))
    assert '"home.kicker": "Welcome. Ready to shop?"' in src
    assert '"home.kicker": "Bienvenue. Prêt à faire vos achats ?"' in src
    assert '"home.heroLine": "Experience effortless elegance and curated essentials"' in src, (
        "the main hero headline must stay verbatim")


def test_most_viewed_heading_is_translatable():
    """'Most viewed right now' must follow the active locale like every
    other storefront string, not stay hard-coded in English."""
    i18n = _read(os.path.join("js", "i18n.js"))
    assert '"home.mostViewed": "Most viewed right now"' in i18n
    assert '"home.mostViewed": "Les plus consultés en ce moment"' in i18n
    app = _read(os.path.join("js", "app.js"))
    assert 't("home.mostViewed")' in app, (
        "paintMostViewed must render the heading through the i18n table")
    assert "Most viewed right now" not in app.split("function paintMostViewed", 1)[1][:600], (
        "the heading must not be hard-coded English inside paintMostViewed")


# ---------------------------------------------------------------- store.js

def _store_js():
    with open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8") as fh:
        return fh.read()


def test_store_locks_french_to_fcfa_and_defaults_english_to_naira():
    src = _store_js()
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


def test_checkout_keeps_three_payment_methods_and_derives_currency_from_the_choice():
    app = _read(os.path.join("js", "app.js"))
    checkout = _read("checkout.html")
    body = app.split("function paintCheckoutTotals(", 1)[1]
    body = body.split("/** One zone ->", 1)[0]
    assert 'function checkoutPaymentMethod(form)' in app
    assert 'function checkoutCurrency(form)' in app
    assert 'const cur = method === "naira" ? "NGN" : "CFA"' in body
    for method in ("naira", "benin_cfa", "togo_cfa"):
        assert f'value="{method}"' in checkout
    for sheet in ("data-bank-ngn", "data-bank-benin", "data-bank-togo"):
        assert sheet in body and sheet in checkout
    assert 'data-i18n="ck.togoFeeNotice"' in checkout
    assert "Moov Money Togo may charge a fee for cross-border transfers." in _read("js/i18n.js")


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
