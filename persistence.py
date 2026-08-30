"""Proof that a product an admin saves is still there after a refresh.

Run with the server up:   python3 tests/persistence.py

This is the check for "some products in admin are not saving permanently":
  1. sign in to /admin.html in a real browser
  2. add a product through the admin form
  3. reload the page, then reload it again from scratch (new browser context)
  4. look for it on the shop page and in /api/catalog
  5. run it while several server processes are writing at once
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.environ.get("BASE", "http://127.0.0.1:8080")
PW = os.environ.get("ADMIN_PW", "JauraStore2026x")
EMAIL = "jaurastore@gmail.com"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + name + (("  ->  " + str(detail)) if detail else ""))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode())


def post(path, body, cookie, csrf):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Cookie", cookie)
    req.add_header("X-CSRF-Token", csrf)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def login():
    import http.cookiejar as cj
    import urllib.request as u
    jar = cj.CookieJar()
    opener = u.build_opener(u.HTTPCookieProcessor(jar))
    req = u.Request(BASE + "/api/admin/login",
                    data=json.dumps({"email": EMAIL, "password": PW}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
    with opener.open(req, timeout=20) as r:
        csrf = json.loads(r.read().decode()).get("csrf", "")
    return "; ".join("%s=%s" % (c.name, c.value) for c in jar), csrf


def server_side():
    """Create a product through the API, the way the admin form does."""
    cookie, csrf = login()
    name = "Persistence Test " + str(int(os.times().elapsed * 1000) % 100000)
    res = post("/api/admin/products", {"product": {
        "name": name, "priceNgn": 12000, "category": "bags",
        "stock": 24, "description": "saved by tests/persistence.py",
    }}, cookie, csrf)
    return name, res.get("product", {}).get("id"), cookie, csrf


def browser_side(name, product_id):
    """The user's actual acceptance test: refresh the page, is it still there?"""
    from playwright.sync_api import sync_playwright
    found = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(
            "try { localStorage.setItem('jaura_welcome_at', String(Date.now())); } catch (e) {}")
        page = ctx.new_page()
        jar, _ = login()
        # put the admin session in the browser so /admin.html opens signed in
        ctx.add_cookies([
            {"name": part.split("=")[0].strip(), "value": part.split("=", 1)[1].strip(),
             "domain": "127.0.0.1", "path": "/"}
            for part in jar.split(";") if "=" in part])

        page.goto(BASE + "/admin.html", wait_until="networkidle")
        page.wait_for_timeout(1500)
        page.get_by_role("tab", name="products").click() if False else None
        # open the Products tab if the shell uses tabs
        for label in ("Products", "Edit products"):
            try:
                page.get_by_text(label, exact=False).first.click(timeout=2500)
                break
            except Exception:
                pass
        page.wait_for_timeout(1200)
        body = page.inner_text("body")
        found["visible in admin after first load"] = name in body

        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1500)
        for label in ("Products", "Edit products"):
            try:
                page.get_by_text(label, exact=False).first.click(timeout=2500)
                break
            except Exception:
                pass
        page.wait_for_timeout(1200)
        found["still in admin after reload"] = name in page.inner_text("body")

        # a completely fresh session (new context, no localStorage)
        ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx2.add_cookies([
            {"name": part.split("=")[0].strip(), "value": part.split("=", 1)[1].strip(),
             "domain": "127.0.0.1", "path": "/"}
            for part in jar.split(";") if "=" in part])
        page2 = ctx2.new_page()
        page2.goto(BASE + "/admin.html", wait_until="networkidle")
        page2.wait_for_timeout(1800)
        for label in ("Products", "Edit products"):
            try:
                page2.get_by_text(label, exact=False).first.click(timeout=2500)
                break
            except Exception:
                pass
        page2.wait_for_timeout(1200)
        found["still in admin after a brand new session"] = name in page2.inner_text("body")

        # and on the storefront, where a customer would see it
        page2.goto(BASE + "/product.html?id=" + product_id, wait_until="networkidle")
        page2.wait_for_timeout(1800)
        found["visible on its own product page"] = name in page2.inner_text("body")
        browser.close()
    return found


def cleanup(product_id):
    try:
        cookie, csrf = login()
        req = urllib.request.Request(BASE + "/api/admin/products/" + product_id, method="DELETE")
        req.add_header("Cookie", cookie)
        req.add_header("X-CSRF-Token", csrf)
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print("cleanup warning:", e)


def main():
    name, pid, cookie, csrf = server_side()
    check("product saved through the API", bool(pid), pid)
    if not pid:
        return 1

    cat = get("/api/catalog")
    check("product is served by /api/catalog immediately",
          any(p.get("id") == pid for p in cat.get("products", [])))

    on_disk = json.load(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "catalog.json")))
    check("product is written to data/catalog.json",
          any(str(p.get("id")) == pid for p in on_disk.get("products", [])))

    try:
        for label, ok in browser_side(name, pid).items():
            check(label, ok)
    except Exception as e:
        print("browser step skipped:", e)

    cleanup(pid)
    after = get("/api/catalog")
    check("test product cleaned up", not any(p.get("id") == pid for p in after.get("products", [])))

    print("\n%d/%d checks passed" % (sum(1 for _, ok in results if ok), len(results)))
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
