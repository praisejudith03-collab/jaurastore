"""TEMPORARY diagnostic (remove before merging): render the storefront at
1440px in real Chromium inside CI and report the actual header geometry back
as ::error:: annotations (the sandbox cannot download Actions logs)."""
import http.server
import os
import threading

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8123


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, *a):
        pass


def serve():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def emit(label, data):
    print(f"::error::{label} = {data}", flush=True)


httpd = serve()
base = f"http://127.0.0.1:{PORT}"
with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    for mobile_emu in (True, False):
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            is_mobile=mobile_emu, has_touch=mobile_emu)
        context.add_init_script("sessionStorage.setItem('jaura_welcome_seen', '1')")
        page = context.new_page()
        page.goto(base + "/index.html", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        emit(f"clientWidth mobile={mobile_emu}",
             page.evaluate("document.documentElement.clientWidth"))
        for name, sel in (
                ("logo_img", "#site-header .logo img"),
                ("nav_right", "#site-header .nav-right"),
                ("lang_EN", '#site-header .lang-switch [data-lang="en"]'),
                ("cur_NGN", '#site-header .currency-switch [data-cur="NGN"]'),
                ("cur_CFA", '#site-header .currency-switch [data-cur="CFA"]'),
        ):
            info = page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return "MISSING";
                    const cs = getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return JSON.stringify({x: Math.round(r.x), y: Math.round(r.y),
                                           w: Math.round(r.width), h: Math.round(r.height),
                                           display: cs.display, visibility: cs.visibility,
                                           opacity: cs.opacity});
                }""", sel)
            emit(f"{name} mobile={mobile_emu}", info)
        emit(f"header_html_len mobile={mobile_emu}",
             page.evaluate("document.getElementById('site-header').innerHTML.length"))
        context.close()
    browser.close()
httpd.shutdown()
print("probe done", flush=True)
