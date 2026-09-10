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
                page.goto(base + path, wait_until="domcontentloaded", timeout=90000)
                logo = page.locator("#site-header .logo img")
                expect(logo).to_be_visible(timeout=90000)
                # The header logo is LOCKED to the centre of the page on
                # desktop (nav links | logo | controls). Fail the deploy check
                # if any stylesheet ever drags it back to the edge again.
                if width >= 1024:
                    layout_centre = page.evaluate(
                        "document.documentElement.clientWidth / 2")
                    box = logo.bounding_box()
                    centre = box["x"] + box["width"] / 2
                    assert abs(centre - layout_centre) <= 4, (
                        f"header logo not centred on {base + path}: "
                        f"centre {centre} != {layout_centre}")
                    cfa = page.locator('#site-header .header .currency-switch [data-cur="CFA"]')
                    cbox = cfa.bounding_box()
                    assert cbox and 40 <= cbox["width"] <= 80, (
                        f"currency pill wrong width: {cbox}")
                # The two golden butterflies stay in the header, untouched.
                assert page.locator("#site-header .header-flies .hfly").count() == 2
                page.locator(f'#site-header .nav-right [data-lang="{language}"]').click()
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
