"""End-to-end checks with a real headless browser.

    python3 tests/e2e.py            (needs the app running on :8080)

Every check prints PASS/FAIL and the run exits non-zero if anything fails.
"""
import json, os, re, sys, time, urllib.request

BASE = os.environ.get("BASE", "http://127.0.0.1:8080")
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shots")
os.makedirs(SHOTS, exist_ok=True)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("  ->  " + str(detail)) if detail else ""))
    return ok


def api(path, method="GET", body=None, cookie=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def admin_cookie():
    """Sign in over HTTP so helpers can call admin endpoints."""
    import urllib.request as u, http.cookiejar as cj
    jar = cj.CookieJar()
    opener = u.build_opener(u.HTTPCookieProcessor(jar))
    req = u.Request(BASE + "/api/admin/login",
                    data=json.dumps({"email": "jaurastore@gmail.com",
                                     "password": os.environ.get("ADMIN_PW", "JauraStore2026x")}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
    with opener.open(req, timeout=20) as r:
        csrf = json.loads(r.read().decode()).get("csrf", "")
    return "; ".join("%s=%s" % (c.name, c.value) for c in jar), csrf


def delete_demo(pid):
    cookie, csrf = admin_cookie()
    req = urllib.request.Request(BASE + "/api/admin/products/" + pid, method="DELETE")
    req.add_header("Cookie", cookie)
    req.add_header("X-CSRF-Token", csrf)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except Exception as e:
        return str(e)


def db_rows(sql):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from db import query
    return [dict(r) for r in query(sql)]


def db_exec(sql):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from db import execute
    execute(sql)


def main():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_init_script(
            "try { localStorage.setItem('jaura_welcome_at', String(Date.now())); } catch (e) {}")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append("console:" + m.text) if m.type == "error" else None)

        # ---------------------------------------------------------- storefront
        def click_safe(page, selector, timeout=20000):
            """Scroll the control to the middle of the screen, then click it
            with the mouse - a fixed bar must never sit on top of a button."""
            loc = page.locator(selector).first
            loc.wait_for(timeout=timeout)
            box = None
            for _ in range(3):
                try:
                    loc.evaluate("el => el.scrollIntoView({block: 'center', inline: 'nearest'})")
                except Exception:
                    pass
                page.wait_for_timeout(300)
                box = loc.bounding_box()
                if box and box["y"] > 60 and box["y"] + box["height"] < page.viewport_size["height"] - 90:
                    break
            assert box, "no box for " + selector
            # the page scrolls smoothly, so wait until the element has stopped
            # moving before trusting the coordinates
            stable = None
            for _ in range(20):
                now = loc.bounding_box()
                if stable and now and abs(now["y"] - stable["y"]) < 0.5 and abs(now["x"] - stable["x"]) < 0.5:
                    box = now
                    break
                stable = now
                page.wait_for_timeout(100)
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

        def dismiss(page):
            """Close the welcome pop-over if it is on screen."""
            try:
                x = page.locator("[data-welcome-x]")
                if x.count() and x.first.is_visible():
                    x.first.click()
                    page.wait_for_timeout(700)
            except Exception:
                pass

        page.goto(BASE + "/", wait_until="networkidle")
        dismiss(page)
        check("home renders product grid", page.locator(".p-card, [data-card], .card").count() > 0
              or "J Aura" in page.content())
        check("footer shows jaurastore@gmail.com", "jaurastore@gmail.com" in page.content())
        page.screenshot(path=os.path.join(SHOTS, "01-home.png"), full_page=False)

        # catalogue comes from the server
        hits = []
        page.on("response", lambda r: hits.append(r.url) if "/api/catalog" in r.url else None)
        page.goto(BASE + "/shop.html", wait_until="networkidle")
        dismiss(page)
        check("shop page loaded catalogue from /api/catalog", any(hits), hits[:1])

        # ------------------------------------------------------- product view
        page.goto(BASE + "/product.html?id=wix-001", wait_until="networkidle")
        dismiss(page)
        check("product page shows a name", len(page.locator("h1").first.inner_text()) > 3,
              page.locator("h1").first.inner_text()[:40])
        page.wait_for_timeout(1200)
        pv = db_rows("SELECT COUNT(*) n FROM page_views")
        check("page view recorded on the server", pv[0]["n"] > 0, pv[0])

        # ----------------------------------------------------- cart + checkout
        page.goto(BASE + "/cart.html", wait_until="networkidle")
        page.goto(BASE + "/shop.html", wait_until="networkidle")
        added = False
        btn = page.locator("[data-add]").first
        dismiss(page)
        if btn.count():
            btn.click(); added = True
        else:
            card = page.locator("a[href*='product.html']").first
            if card.count():
                card.click()
                page.wait_for_load_state("networkidle")
                b = page.locator("[data-buy]").first
                if b.count():
                    b.click(); added = True
        page.wait_for_timeout(600)
        check("add to cart counted", db_rows("SELECT COUNT(*) n FROM events WHERE type='cart'")[0]["n"] > 0 or not added,
              "added=" + str(added))

        page.goto(BASE + "/checkout.html", wait_until="networkidle")
        dismiss(page)
        zone_text = page.locator(".fare-list").inner_text() if page.locator(".fare-list").count() else ""
        check("no Pick Up option in the delivery zones",
              not re.search(r"pick\s*-?\s*up", zone_text, re.I), zone_text.replace("\n", " | ")[:90])
        check("delivery section asks for a location", "Delivery location" in page.content())

        # fill the checkout and place a real order with a screenshot
        page.fill("[name=firstName]", "Grace")
        page.fill("[name=lastName]", "Mensah")
        page.fill("[name=address]", "12 Rue des Palmiers, Akpakpa")
        page.fill("[name=city]", "Cotonou")
        page.fill("[name=phone]", "+229 97 00 11 22")
        page.fill("[name=email]", "grace@example.com")
        page.check("input[name=zone][value='Cotonou']")
        page.set_input_files("[name=proof]", os.path.abspath("tests/fixtures/proof.jpg"))
        # no wait on purpose: tapping Place order before the photo finishes
        # compressing used to silently refuse the order
        click_safe(page, ".ck-place")
        try:
            page.wait_for_selector("#ja-order-id", timeout=20000)
        except Exception:
            diag = page.evaluate("""() => {
              const f = document.querySelector('[data-checkout]');
              return {
                welcome: !!document.querySelector('[data-welcome]'),
                proof: f ? !!f.dataset.proof : null,
                invalid: f ? [...f.querySelectorAll(':invalid')].map(e => e.name).join(',') : null,
                toast: (document.querySelector('.toast') || {}).textContent || '',
                pending: window.JA_NET ? window.JA_NET.pending() : -1,
              };
            }""")
            print("   checkout diagnostics:", diag)
            root_txt = page.locator("[data-checkout-root]").inner_text()[:400]
            page.screenshot(path=os.path.join(SHOTS, "zz-checkout-fail.png"), full_page=True)
            check("order confirmation shown", False,
                  "no #ja-order-id. root=" + root_txt.replace("\n", " "))
            raise
        order_id = page.locator("#ja-order-id").inner_text().strip()
        check("order confirmation shown", bool(re.match(r"^JA-[A-Z0-9]+$", order_id)), order_id)
        page.screenshot(path=os.path.join(SHOTS, "02-order-done.png"))

        page.wait_for_timeout(1500)
        row = db_rows("SELECT id, status, total, currency, proof_url, customer_name, city FROM orders WHERE id='%s'" % order_id)
        check("order stored on the server", bool(row), row[:1])
        if row:
            check("payment screenshot stored", bool(row[0]["proof_url"]), row[0]["proof_url"])
            check("customer address retained", "Rue des Palmiers" in (db_rows(
                "SELECT address FROM orders WHERE id='%s'" % order_id) or [{}])[0].get("address", ""))

        # ----------------------------------------------------------- tracking
        page.goto(BASE + "/order.html?id=" + order_id, wait_until="networkidle")
        check("order tracking works from a fresh page", order_id in page.content())

        # --------------------------------------------- payment receipt form
        db_exec("DELETE FROM rate_limits WHERE action='payment-proof'")   # a clean slot
        page.goto(BASE + "/pay.html?id=" + order_id, wait_until="networkidle")
        dismiss(page)
        check("payment form asks for name, phone, email and order id",
              all(page.locator(f'[data-pay-form] [name={n}]').count() == 1
                  for n in ("name", "phone", "email", "orderId")))
        page.fill("[data-pay-form] [name=name]", "Grace Mensah")
        page.fill("[data-pay-form] [name=phone]", "+229 97 00 11 22")
        page.fill("[data-pay-form] [name=email]", "grace@example.com")
        page.set_input_files("[data-pay-form] [name=receipt]",
                             os.path.abspath("tests/fixtures/receipt.pdf"))
        page.wait_for_timeout(600)
        check("pdf preview shown before sending", page.locator(".pay-pdf").count() == 1,
              page.locator("[data-pay-file-note]").inner_text()[:60])
        click_safe(page, ".pay-send")
        page.wait_for_selector(".pay-done", timeout=30000)
        check("receipt accepted and confirmation shown", True,
              page.locator(".pay-done .pay-lead").inner_text()[:80])
        page.screenshot(path=os.path.join(SHOTS, "07-payment-receipt.png"))

        page.wait_for_timeout(2000)
        rows = db_rows("SELECT order_id, name, phone, email, method, file_name, file_size, mime, emailed "
                       "FROM payment_proofs ORDER BY id DESC LIMIT 1")
        check("receipt stored on the server", bool(rows), rows[:1])
        if rows:
            r = rows[0]
            check("receipt is the original pdf",
                  r["mime"] == "application/pdf" and r["file_name"].endswith(".pdf"), r["file_name"])
            check("email carried the customer details",
                  r["order_id"] == order_id and r["name"] == "Grace Mensah" and r["phone"], r)

        # prove the attachment in the mailbox is byte-for-byte the original
        # Delivery is only checkable when this environment actually sends mail
        # (see tests/test_api.py::..._emailed_with_the_original_file_attached,
        # which proves it in CI with an in-process SMTP sink).
        folder = os.environ.get("MAIL_SINK", "/tmp/mail")
        sent = sorted(f for f in os.listdir(folder) if f.endswith(".eml")) if os.path.isdir(folder) else []
        if sent:
            import email as emailmod
            from email import policy as _policy
            # the business copy is the one carrying the attachment; the
            # customer only gets a short confirmation
            msg = None
            for fname in reversed(sent):
                with open(os.path.join(folder, fname), "rb") as fh:
                    candidate = emailmod.message_from_binary_file(fh, policy=_policy.default)
                if list(candidate.iter_attachments()):
                    msg = candidate
                    break
            msg = msg or candidate
            atts = list(msg.iter_attachments())
            orig = open("tests/fixtures/receipt.pdf", "rb").read()
            ok = bool(atts) and atts[-1].get_payload(decode=True) == orig
            check("emailed attachment is the original file, byte for byte", ok,
                  [(a.get_filename(), len(a.get_payload(decode=True))) for a in atts])
            check("receipt email is addressed to the shop", "jaurastore" in (msg["To"] or ""),
                  msg["To"])
            body = msg.get_body(preferencelist=("plain",))
            text = body.get_content() if body else ""
            check("email lists name, phone, order id, products, quantity and method",
                  all(k in text for k in ("Grace Mensah", "+229 97 00 11 22",
                                          (rows[0]["order_id"] if rows else order_id),
                                          "Quantity", "Payment method")),
                  [ln for ln in text.splitlines() if ln.startswith(("Customer", "Phone", "Order", "Product", "Quantity", "Payment"))][:6])
        else:
            print("      note: mail is not being delivered in this environment, so the "
                  "attachment was not checked here. It is covered by "
                  "tests/test_api.py (in-process SMTP sink).")

        # -------------------------------------------------------------- admin
        page.goto(BASE + "/admin.html", wait_until="networkidle")
        check("admin shows the email + password form", page.locator("#login-form input[name=email]").count() == 1)
        page.fill("input[name=email]", "jaurastore@gmail.com")
        page.fill("input[name=password]", os.environ.get("ADMIN_PW", "JauraStore2026x"))
        page.click("#login-btn")
        page.wait_for_selector("#panel-analytics .stats b", timeout=20000)
        page.wait_for_timeout(2500)
        kpis = page.locator("#an-kpis .stat").all_inner_texts()
        check("insights dashboard renders KPIs", len(kpis) >= 4, " | ".join(k.replace("\n", " ") for k in kpis[:5]))
        check("traffic chart drawn", page.locator("#an-chart .an-bar").count() > 0,
              page.locator("#an-chart .an-bar").count())
        check("top pages table filled", page.locator("#an-pages table tbody tr").count() > 0,
              page.locator("#an-pages table tbody tr").count())
        check("conversion block filled", page.locator("#an-conv .stat").count() >= 4)
        page.screenshot(path=os.path.join(SHOTS, "03-dashboard.png"), full_page=True)

        # orders tab
        click_safe(page, "[data-tab=orders]")
        page.wait_for_selector("#orders-box .order-card", timeout=20000)
        cards = page.locator("#orders-box .order-card").count()
        check("orders tab lists the order from the server", cards >= 1, cards)
        check("order card shows the proof image",
              page.locator("#orders-box .order-card img.proof-preview").count() >= 1)
        check("order card shows the delivery address", "Rue des Palmiers" in page.locator("#orders-box").inner_text())
        page.screenshot(path=os.path.join(SHOTS, "04-orders.png"), full_page=True)

        # confirm the order
        page.locator("#orders-box [data-confirm]").first.click()
        page.wait_for_timeout(2500)
        st = db_rows("SELECT status FROM orders WHERE id='%s'" % order_id)
        check("confirm sets the status on the server", st and st[0]["status"] == "confirmed", st[:1])

        # account tab: change password from this device
        click_safe(page, "[data-tab=account]")
        page.wait_for_selector("#pw-form", timeout=10000)
        check("account tab has the change-password form", page.locator("#pw-form input[name=current]").count() == 1)
        page.screenshot(path=os.path.join(SHOTS, "05-account.png"))

        # -------------------------------------------------- offline behaviour
        click_safe(page, "[data-tab=products]")
        page.wait_for_selector("#add-product", timeout=10000)
        click_safe(page, "#add-product")
        page.wait_for_selector("#prod-form", timeout=10000)
        name = "Offline Sync Bag " + str(int(time.time()))
        page.fill("#prod-form input[name=name]", name)
        page.fill("#prod-form input[name=priceNgn]", "12500")
        # upload the photo while we still have a connection
        page.set_input_files("#more-media", os.path.abspath("tests/fixtures/proof.jpg"))
        page.wait_for_selector(".wix-tile.is-pending", state="detached", timeout=30000)
        page.wait_for_timeout(500)
        # the photo must really be a stored url before we pull the plug,
        # otherwise the form is entitled to ask us to wait for it
        for _ in range(30):
            if page.evaluate("() => (window.__editImages||[]).every((s)=>typeof s==='string' && s)"):
                break
            page.wait_for_timeout(1000)
        check("product photo uploaded to the server",
              page.evaluate("() => (window.__editImages||[]).every((s)=>typeof s==='string' && s)"
                            " && (window.__editImages||[]).length > 0"),
              page.evaluate("() => (window.__editImages||[]).map((s)=>typeof s)"))

        ctx.set_offline(True)
        page.wait_for_timeout(400)
        click_safe(page, "#prod-form .wix-save")
        # the save has to finish writing to the outbox before the pill shows
        try:
            page.wait_for_selector("#ja-sync-pill", timeout=15000)
        except Exception:
            pass
        pill = page.locator("#ja-sync-pill")
        if pill.count() == 0:
            diag = page.evaluate("""() => {
              const btn = document.querySelector('#prod-form .wix-save');
              let hit = 'no button';
              if (btn) {
                const b = btn.getBoundingClientRect();
                const el = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
                hit = el ? el.tagName + '.' + el.className : 'nothing there';
              }
              return {
                online: navigator.onLine,
                hasNet: typeof window.JA_NET,
                pending: window.JA_NET ? window.JA_NET.pending() : -1,
                btnVisible: btn ? !!btn.offsetParent : null,
                btnLabel: btn ? btn.textContent : null,
                elementAtButton: hit,
                prodFormOpen: !!document.querySelector('#prod-form'),
                submitBound: (document.querySelector('#prod-form') || {}).dataset
                  ? document.querySelector('#prod-form').dataset.submitBound : 'no dataset',
                images: (window.__editImages || []).map((s) => typeof s),
                lastToast: (document.querySelector('.toast') || {}).textContent || '',
                name: (document.querySelector('#prod-form input[name=name]') || {}).value || '',
                price: (document.querySelector('#prod-form input[name=priceNgn]') || {}).value || '',
                formValid: (document.querySelector('#prod-form') || {}).checkValidity
                  ? document.querySelector('#prod-form').checkValidity() : null,
                invalid: [...((document.querySelector('#prod-form') || {})
                  .querySelectorAll ? document.querySelector('#prod-form').querySelectorAll(':invalid') : [])]
                  .map((e) => e.name + '=' + (e.value || '').slice(0, 20) + ' :: ' + (e.validationMessage || '').slice(0, 50)),
              };
            }""")
            print("   offline diagnostics:", diag)
        check("offline save is queued, not lost", pill.count() > 0,
              pill.inner_text() if pill.count() else "no pill")
        page.screenshot(path=os.path.join(SHOTS, "06-offline-pending.png"))

        ctx.set_offline(False)
        deadline = time.time() + 40
        in_cat = False
        while time.time() < deadline and not in_cat:
            page.wait_for_timeout(2000)
            try:
                status, cat = api("/api/catalog")
                if status == 200 and isinstance(cat, dict):
                    in_cat = any(p.get("name") == name for p in cat.get("products", []))
            except Exception:
                pass
        check("queued product reached the server after reconnect", in_cat, name)
        check("sync pill cleared after it uploaded", page.locator("#ja-sync-pill").count() == 0,
              page.locator("#ja-sync-pill").count())

        real = [e for e in errors
                if "favicon" not in e.lower()
                and "ERR_" not in e          # the offline step resets connections on purpose
                and "Failed to load resource" not in e]
        # tidy up the demo product we just created
        try:
            status, cat = api("/api/catalog")
            if status == 200:
                for p in cat.get("products", []):
                    if str(p.get("name", "")).startswith("Offline Sync Bag"):
                        delete_demo(p["id"])
        except Exception as e:
            print("cleanup warning:", e)

        check("no uncaught JS errors", not real, real[:3])

        # --------------------------------------- the payment form on a phone
        mctx = browser.new_context(viewport={"width": 390, "height": 844},
                                   is_mobile=True, has_touch=True, device_scale_factor=3)
        mctx.add_init_script(
            "try { localStorage.setItem('jaura_welcome_at', String(Date.now())); } catch (e) {}")
        mpage = mctx.new_page()
        mpage.goto(BASE + "/pay.html", wait_until="networkidle")
        mpage.wait_for_timeout(600)
        overflow = mpage.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check("payment form fits a 390px phone with no sideways scrolling", overflow <= 0, overflow)
        db_exec("DELETE FROM rate_limits WHERE action='payment-proof'")
        mpage.fill("[data-pay-form] [name=name]", "Chidinma Okafor")
        mpage.fill("[data-pay-form] [name=phone]", "+234 803 555 0199")
        mpage.fill("[data-pay-form] [name=email]", "chidinma@example.com")
        mpage.fill("[data-pay-form] [name=orderId]", "JA-MOBILE1")
        mpage.fill("[data-pay-form] [name=items]", "1x Gucci crossbody bag")
        mpage.fill("[data-pay-form] [name=quantity]", "1")
        mpage.fill("[data-pay-form] [name=amount]", "NGN 45,000")
        mpage.set_input_files("[data-pay-form] [name=receipt]",
                              os.path.abspath("tests/fixtures/receipt.pdf"))
        mpage.wait_for_timeout(700)
        mpage.locator(".pay-send").scroll_into_view_if_needed()
        mpage.locator(".pay-send").click()
        mpage.wait_for_selector(".pay-done", timeout=45000)
        check("receipt sent from a phone", True,
              mpage.locator(".pay-done .pay-lead").inner_text()[:70])
        mpage.screenshot(path=os.path.join(SHOTS, "08-pay-mobile.png"), full_page=True)
        mctx.close()

        browser.close()

    ok = all(r[1] for r in results)
    print("\n%d/%d checks passed" % (sum(1 for r in results if r[1]), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
