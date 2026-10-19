"""Locale selection / defaulting (owner request 2026-09-27, revised 2026-10-19).

HISTORY. On 2026-09-27 the live storefront was opening in French for visitors
who should see English (₦ Naira, English navigation, English product cards,
English checkout and delivery-zone copy). The interface language was being
decided from the visitor's device/browser locale, and — because the header and
slide-out-menu switches had just been removed and the stored choice was written
but never read back — a shopper whose phone reported French had no way back.
The response that day was to make French strictly opt-in.

On 2026-10-19 the owner asked for the browser language listener to be
RESTORED ("renders all UI text … in French without manual toggling"). The
restore is safe now precisely because the two missing halves exist: the stored
choice is read back (readStored → storedChoice), and there is a visible
one-tap control (the floating EN|FR pill in js/store.js) whose tap persists
through I18N.setLang(). The rules pinned here:

  * PRECEDENCE: explicit choice (?lang= link, or a pill tap) > device locale >
    English. The device read decides a FIRST visit only and is never written to
    storage, so it can never outlive — or masquerade as — a real choice.
  * A stale/garbage value under the storage key (anything that is not
    exactly "en" or "fr") is purged on load so it can never leak French/F
    CFA onto a fresh English visitor.
  * The currency still follows the active language: French locks FCFA (and a
    CFA price comes from the live catalogue row, never as 0 because the FX
    rate has not landed); English opens in Naira first and the floating pill
    still switches it.
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


def test_device_locale_decides_only_when_nothing_was_chosen():
    """Owner directive 2026-10-19, reversing the opt-in-only rule of 2026-09-27.

    The requirement now is "a visitor whose device locale is French gets the
    French storefront without manual toggling". What must NOT come back is the
    thing that made the 2026-09-27 attempt fail: the device deciding a page
    AFTER the shopper had chosen, or a device-derived value being written to
    storage as if it were a choice. Both halves are pinned here.
    """
    src = _read(os.path.join("js", "i18n.js"))
    body = src.split("function readStored()", 1)[1].split("\n  }", 1)[0]
    # ...an explicit stored choice first, then the ?lang= override, then device.
    assert "storedChoice()" in body, "an explicit stored choice must still win"
    assert 'get("lang")' in body, "the ?lang= override must still be read"
    assert "detectBrowserLang()" in body, (
        "a first-time visitor with no explicit choice must follow the device "
        "locale (restore of the browser language listener, 2026-10-19)")
    assert 'return "en";' not in body, (
        "readStored() must not hard-default to English ahead of the device read")
    # The device value is an observation, so it is never persisted; only a URL
    # override or setLang() writes storage.
    assert "persist(detectBrowserLang" not in body and "persist(device" not in body, (
        "a device-inferred language must never be stored as an explicit choice")
    # The device is read in exactly ONE place — detectBrowserLang() — so no
    # render path can quietly start deciding language from the browser again.
    detector = src.split("function detectBrowserLang()", 1)[1].split("\n  }", 1)[0]
    assert "navigator.languages" in detector and "navigator.language" in detector, (
        "the detector is the single reader of the device locale")
    everywhere_else = src.replace("function detectBrowserLang()" + detector, "", 1)
    assert "navigator.languages" not in everywhere_else and "navigator.language" not in everywhere_else, (
        "only detectBrowserLang() may touch navigator.language(s)")


def test_the_way_back_to_english_exists_and_persists():
    """The escape hatch is the point of the 2026-10-19 restore.

    Without a visible control, a French-tagged device is stuck in French — the
    exact defect that made auto-detection get switched off on 2026-09-27 — and
    it is not enough for the pill to exist: its tap must go through
    I18N.setLang(), which is what persists the reversal.
    """
    store = _read(os.path.join("js", "store.js"))
    start = store.index('<div class="cur-float lang-float" data-lang-float')
    pill = store[start:start + 400]
    assert 'data-lang="en"' in pill and 'data-lang="fr"' in pill, (
        "the floating language pill must offer both EN and FR")
    # The shared [data-lang] handler in bindChrome() persists and repaints.
    binder = store.split("function bindChrome()", 1)[1]
    lang_handler = binder.split('querySelectorAll("[data-lang]")', 1)[1][:900]
    assert 'localStorage.setItem("jaura_lang"' in lang_handler, (
        "tapping the pill must persist the choice")
    assert 'window.I18N.setLang(next)' in lang_handler, (
        "tapping the pill must go through I18N.setLang so the shop repaints")
    # And it stays out of the header/menu, which remain switch-free by owner
    # request 2026-09-27 (see tests/test_header_layout_lock.py).
    header = store.split('<header class="header" data-header>', 1)[1].split("</header>", 1)[0]
    assert "data-lang" not in header, "the header stays free of language switches"


def test_html_documents_never_carry_a_stale_language_to_the_wrong_visitor():
    """Accept-Language is a per-visitor input, so the response must Vary on it.

    Without the Vary, one shared cache entry could hand the French <html lang>
    to an English visitor (or the reverse). The header policy itself is pinned
    in tests/test_regression_headers.py and tests/test_zero_cache_policy.py.
    """
    src = _read("app.py")
    assert 'sec.force_no_store(resp, vary=rel.endswith((".html", ".htm")))' in src, (
        "the language rewrite must only ship alongside a no-store + Vary policy")


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
