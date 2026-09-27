"""Read-only mobile verification of a deployed shop (never signs in or writes data).

Usage: python tools/browser_smoke.py https://jaurastore.com.ng --wait 900
Waits for this checkout's store.js before checking the actual browser DOM.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument("url")
parser.add_argument("--wait", type=int, default=0)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
expected = hashlib.sha256((root / "js/store.js").read_bytes()).hexdigest()
base = args.url.rstrip("/")
deadline = time.monotonic() + args.wait

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM_EXECUTABLE"),
                                args=["--no-sandbox", "--disable-dev-shm-usage"])
    context = browser.new_context(viewport={"width": 390, "height": 844},
                                  is_mobile=True, has_touch=True)
    while True:
        try:
            response = context.request.get(base + "/js/store.js?verify=" + str(time.time_ns()), timeout=90000)
            if response.ok and hashlib.sha256(response.body()).hexdigest() == expected:
                break
            reason = f"Expected store.js not deployed yet (HTTP {response.status})"
        except Exception as exc:
            reason = str(exc)
        if time.monotonic() >= deadline:
            raise RuntimeError(reason)
        print(reason, flush=True)
        time.sleep(20)
    context.add_init_script("sessionStorage.setItem('jaura_welcome_seen', '1')")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    import re
    for width in (360, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        for language in ("en", "fr"):
            for path in ("/shop.html", "/categories.html", "/faq.html"):
                # Language is auto-detected from the device (2026-09-27); the
                # ?lang= URL parameter is the explicit override harness.
                page.goto(base + path + "?lang=" + language,
                          wait_until="domcontentloaded", timeout=90000)
                logo = page.locator("#site-header .logo img")
                expect(logo).to_be_visible(timeout=90000)
                # The header logo is LOCKED to the CENTRE of the header row
                # ("Option A", owner request 2026-09-27): menu [=] left, logo
                # centred, search + cart right, one grid row, and NO language
                # or currency dropdowns in the header. Fail the deploy check
                # if any stylesheet ever drags it off-centre, scatters it
                # onto a second row, or the old header switches come back.
                box = logo.bounding_box()
                nav_right = page.locator("#site-header .header .nav-right")
                rbox = nav_right.bounding_box()
                assert box and rbox, "header logo/controls missing"
                logo_mid = box["y"] + box["height"] / 2
                right_mid = rbox["y"] + rbox["height"] / 2
                assert abs(logo_mid - right_mid) <= 2, (
                    f"logo and controls not on one row on {base + path}: "
                    f"logo mid {logo_mid} != controls mid {right_mid}")
                assert box["x"] + box["width"] <= rbox["x"] + 1, (
                    f"logo overlaps controls on {base + path}")
                assert page.locator("#site-header .header .lang-switch").count() == 0, (
                    f"the removed header language switch is back on {base + path}")
                assert page.locator("#site-header .header .currency-switch").count() == 0, (
                    f"the removed header currency switch is back on {base + path}")
                if width >= 1024:
                    row = page.evaluate(
                        "(() => { const r = document.querySelector("
                        "'#site-header .header .header-inner').getBoundingClientRect();"
                        " return { left: r.left, width: r.width }; })()")
                    logo_mid_x = box["x"] + box["width"] / 2
                    row_mid_x = row["left"] + row["width"] / 2
                    assert abs(logo_mid_x - row_mid_x) <= 4, (
                        f"header logo not centred on {base + path}: "
                        f"logo centre {logo_mid_x} != row centre {row_mid_x}")
                # The currency now lives in the floating pill: visible in
                # English, hidden while French locks FCFA (bottom:85px
                # right:20px, stacked above the WhatsApp bubble).
                pill = page.locator(".cur-float")
                assert pill.count() == 1, f"floating currency pill missing on {base + path}"
                if language == "en":
                    expect(pill).to_be_visible(timeout=90000)
                    pbox = pill.bounding_box()
                    bottom_gap = 900 - (pbox["y"] + pbox["height"])
                    assert 75 <= bottom_gap <= 95, (
                        f"currency pill not stacked at bottom:85px on {base + path}: {pbox}")
                    assert width - (pbox["x"] + pbox["width"]) <= 24, (
                        f"currency pill not pinned right:20px on {base + path}: {pbox}")
                else:
                    assert pill.evaluate("el => el.hidden"), (
                        f"French mode must hide the currency pill on {base + path}")
                    assert page.evaluate("JA.currency()") == "CFA", (
                        f"French mode must lock FCFA on {base + path}")
                # The two golden butterflies stay in the header, untouched.
                assert page.locator("#site-header .header-flies .hfly").count() == 2
                assert logo.evaluate("img => img.complete && img.naturalWidth > 0")
                selector = "[data-shop-grid] > *" if path == "/shop.html" else "[data-cat-list] > *"
                if path != "/faq.html":
                    expect(page.locator(selector).first).to_be_visible(timeout=90000)
                assert not re.search(r"admin[\s_-]*portal|portail\s+admin", page.locator("body").inner_text(), re.I)
                page.locator("#site-header [data-open-search]").click()
                expect(page.locator("[data-search-input]")).to_be_visible()
                print(json.dumps({"url": base + path, "width": width, "language": language,
                                  "products": page.evaluate("JA.products().length"), "jsErrors": errors}), flush=True)
    assert not errors, errors
    browser.close()
