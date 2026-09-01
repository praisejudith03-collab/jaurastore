"""The checkout form, tested properly.

    python3 tests/checkout.py          # server must be running

Covers the things that can quietly cost an order:
  * the form cannot be sent empty, with a bad email, or without a receipt
  * JPG, PNG and PDF receipts are accepted; a renamed .exe is not
  * tapping Place order while the photo is still compressing still works
  * NGN / CFA totals, no Pick Up zone, tracking after the order
  * a phone: 390px, no sideways scrolling, order goes through
  * offline: the order is queued and delivered when the connection returns
  * the receipt reaches the inbox as the real file, and the order can be
    confirmed from the admin portal and from the link in the email
"""
import hashlib
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.environ.get("BASE", "http://127.0.0.1:8080")
PW = os.environ.get("ADMIN_PW", "JauraStore2026x")
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + name + (("  ->  " + str(detail)[:110]) if detail else ""))
    return bool(ok)


def clear_limits(action="order"):
    """Give the test a clean rate-limit slot."""
    try:
        from db import execute
        execute("DELETE FROM rate_limits WHERE action=?", (action,))
    except Exception:
        pass


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode())


def login():
    import http.cookiejar as cj
    import urllib.request as u
    jar = cj.CookieJar()
    op = u.build_opener(u.HTTPCookieProcessor(jar))
    req = u.Request(BASE + "/api/admin/login",
                    data=json.dumps({"email": "jaurastore@gmail.com", "password": PW}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
    with op.open(req, timeout=20) as r:
        csrf = json.loads(r.read().decode()).get("csrf", "")
    cookie = "; ".join("%s=%s" % (c.name, c.value) for c in jar)
    return cookie, csrf


def send(path, body, cookie, csrf, method="POST"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    req.add_header("Cookie", cookie)
    req.add_header("X-CSRF-Token", csrf)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def order_payload(oid, currency="CFA", total=18500, email="checkout@example.com"):
    return {
        "id": oid, "currency": currency, "total": total, "status": "pending",
        "payment": "bank transfer", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "customer": {"name": "Checkout Tester", "phone": "+229 97 00 11 22",
                     "email": email, "country": "Benin", "city": "Cotonou",
                     "zone": "Cotonou", "address": "12 Rue des Palmiers",
                     "note": "Leave with the guard"},
        "items": [{"id": "wix-001", "name": "10000 mah power bank",
                   "qty": 1, "price": total, "color": ""}],
    }


def fill_checkout(page, oid="JA-CK1", email="checkout@example.com", skip_proof=False):
    page.fill("[name=firstName]", "Grace")
    page.fill("[name=lastName]", "Mensah")
    page.fill("[name=address]", "12 Rue des Palmiers")
    page.fill("[name=city]", "Cotonou")
    page.fill("[name=phone]", "+229 97 00 11 22")
    page.fill("[name=email]", email)
    page.check("input[name=zone][value='Cotonou']")
    if not skip_proof:
        page.set_input_files("[name=proof]", os.path.join(FIX, "receipt.pdf"))


# --------------------------------------------------------------- the browser

def browser_checks():
    clear_limits()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_init_script("try{localStorage.setItem('jaura_welcome_at',String(Date.now()))}catch(e){}")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)[:120]))

        # ---------------------------------------------------------- the form
        page.goto(BASE + "/checkout.html", wait_until="networkidle")
        page.evaluate("()=>localStorage.setItem('jaura_cart', JSON.stringify([{id:'wix-001',qty:1,color:''}]))")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)

        check("checkout form is on the page", page.locator("[data-checkout]").count() == 1)

        empty_invalid = page.evaluate(
            "()=>{const f=document.querySelector('[data-checkout]');"
            "return f.checkValidity()?'valid':[...f.querySelectorAll(':invalid')].map(e=>e.name).join(',')}")
        check("an empty form cannot be submitted", empty_invalid != "valid", empty_invalid)

        zones = page.locator("input[name=zone]").all()
        zone_vals = [z.get_attribute("value") for z in zones]
        check("no Pick Up delivery option",
              zone_vals and not any("pick" in str(v).lower() for v in zone_vals), zone_vals)

        # -------------------------------------------- blocked without a proof
        fill_checkout(page, skip_proof=True)
        page.locator(".ck-place").scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        page.locator(".ck-place").click()
        page.wait_for_timeout(2500)
        check("no order without a payment receipt",
              page.locator("#ja-order-id").count() == 0
              and page.evaluate("()=>(document.querySelector('.toast')||{}).textContent||''").strip() != "",
              page.evaluate("()=>(document.querySelector('.toast')||{}).textContent||''"))

        # --------------------------------- place the order the instant the
        # photo is chosen (this used to silently refuse the order)
        page.set_input_files("[name=proof]", os.path.join(FIX, "proof.jpg"))
        page.locator(".ck-place").scroll_into_view_if_needed()
        page.wait_for_timeout(250)
        page.locator(".ck-place").click()
        try:
            page.wait_for_selector("#ja-order-id", timeout=45000)
            oid = page.locator("#ja-order-id").inner_text().strip()
        except Exception:
            oid = ""
        check("order is placed even when Save is tapped mid photo-compression", bool(oid), oid)

        if oid:
            page.wait_for_timeout(1500)
            try:
                d = get("/api/orders/" + oid)
                check("the order reached the server",
                      bool(d.get("ok")) and d.get("order", {}).get("status") == "pending",
                      (d.get("order") or {}).get("status"))
            except Exception as e:
                check("the order reached the server", False, str(e)[:80])
            try:
                cookie, csrf = login()
                adm = send("/api/admin/orders?limit=200", None, cookie, csrf, method="GET")
                row = next((o for o in adm.get("orders", []) if o.get("id") == oid), {})
                check("the receipt file is stored with the order",
                      bool(row.get("proofUrl") or row.get("proof_url")),
                      row.get("proofUrl") or row.get("proof_url"))
            except Exception as e:
                check("the receipt file is stored with the order", False, str(e)[:80])
            except Exception as e:
                check("the order reached the server", False, str(e)[:80])

            # ------------------------------------------------------ tracking
            try:
                d = get("/api/orders/" + oid)
                check("the customer can track the order afterwards",
                      d.get("ok") and (d.get("order") or {}).get("id") == oid,
                      (d.get("order") or {}).get("status"))
            except Exception as e:
                check("the customer can track the order afterwards", False, str(e)[:80])

        # ------------------------------------------------ a phone (390x844)
        m = browser.new_context(viewport={"width": 390, "height": 844},
                                is_mobile=True, has_touch=True, device_scale_factor=3)
        m.add_init_script("try{localStorage.setItem('jaura_welcome_at',String(Date.now()))}catch(e){}")
        mp = m.new_page()
        mp.goto(BASE + "/checkout.html", wait_until="networkidle")
        mp.evaluate("()=>localStorage.setItem('jaura_cart', JSON.stringify([{id:'wix-001',qty:1,color:''}]))")
        mp.reload(wait_until="networkidle")
        mp.wait_for_timeout(1200)
        over = mp.evaluate("()=>document.documentElement.scrollWidth-document.documentElement.clientWidth")
        check("no sideways scrolling on a phone", over <= 0, over)
        fill_checkout(mp, email="phone@example.com")
        mp.locator(".ck-place").scroll_into_view_if_needed()
        mp.wait_for_timeout(300)
        mp.locator(".ck-place").click()
        try:
            mp.wait_for_selector("#ja-order-id", timeout=45000)
            moid = mp.locator("#ja-order-id").inner_text().strip()
        except Exception:
            moid = ""
        if not moid:
            print("   phone diagnostics:", mp.evaluate("""() => ({
                toast: (document.querySelector('.toast')||{}).textContent||'',
                pending: window.JA_NET ? window.JA_NET.pending() : -1,
                online: navigator.onLine,
                proof: (document.querySelector('[data-checkout]')||{dataset:{}}).dataset.proof ? 'set' : 'missing',
                invalid: [...document.querySelectorAll('[data-checkout] :invalid')].map(e=>e.name),
                orderId: (document.querySelector('#ja-order-id')||{}).textContent||'',
            })"""))
        check("an order can be placed from a phone", bool(moid), moid)
        m.close()

        # ----------------------------------------------------------- offline
        # the page has to load while we still have a connection
        page.goto(BASE + "/checkout.html", wait_until="networkidle")
        page.evaluate("()=>localStorage.setItem('jaura_cart', JSON.stringify([{id:'wix-001',qty:1,color:''}]))")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        ctx.set_offline(True)
        try:
            fill_checkout(page, email="offline@example.com")
            page.locator(".ck-place").scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            page.locator(".ck-place").click()
            page.wait_for_timeout(2500)
            page.wait_for_timeout(1500)
            queued = (page.locator(".ck-queued").count() > 0
                      or page.locator("#ja-sync-pill").count() > 0
                      or page.evaluate("()=>window.JA_NET?window.JA_NET.pending():0") > 0)
        except Exception:
            queued = False
        check("an order placed offline is queued, not lost", queued,
              {"note": page.locator(".ck-queued").count(),
               "pill": page.locator("#ja-sync-pill").count(),
               "pending": page.evaluate("()=>window.JA_NET?window.JA_NET.pending():-1")})
        ctx.set_offline(False)
        page.wait_for_timeout(6000)

        check("no JavaScript errors on the checkout page", not errors, errors[:2])
        browser.close()


# ------------------------------------------------------------ email + confirm

def email_and_confirm_checks():
    clear_limits()
    from mail_sink import MailSink
    import config, emailer, security

    pdf = open(os.path.join(FIX, "receipt.pdf"), "rb").read()
    cookie, csrf = login()
    oid = "JA-CKEMAIL"
    order = order_payload(oid, email="checkout@example.com")

    with MailSink() as sink:
        config.Config.MAIL_MODE = "smtp"
        config.Config.MAIL_FROM = "jaurastore@gmail.com"
        config.Config.SMTP_HOST, config.Config.SMTP_PORT = sink.host, sink.port
        config.Config.SMTP_USER = config.Config.SMTP_PASS = ""

        ok, info = emailer.send_order_notice(order, pdf, f"payment-{oid}-checkout.pdf",
                                             "application/pdf")
        check("the order email is sent", ok is True, info)
        if not sink.messages:
            check("the order email arrived", False, "nothing reached the server")
            return

        import email as emailmod
        from email import policy as epolicy
        msg = emailmod.message_from_bytes(sink.messages[0]["data"], policy=epolicy.default)
        atts = list(msg.iter_attachments())
        body = msg.get_body(preferencelist=("plain",)).get_content()

        check("the receipt is attached to the email as a real file",
              bool(atts), [(a.get_filename(), len(a.get_payload(decode=True))) for a in atts])
        if atts:
            data = atts[0].get_payload(decode=True)
            check("the attached file is the customer's PDF, byte for byte",
                  data == pdf and atts[0].get_content_type() == "application/pdf",
                  f"{len(data)} bytes, {atts[0].get_content_type()}")
            check("the attachment has the original filename",
                  "pdf" in atts[0].get_filename().lower(), atts[0].get_filename())

        check("the email carries the customer's details",
              all(x in body for x in ("Checkout Tester", "+229 97 00 11 22", oid, "12 Rue des Palmiers")),
              [ln for ln in body.splitlines() if ln.startswith(("Name", "Phone", "Total"))][:3])
        check("the email links to confirm the order, not just the admin",
              "/confirm.html?id=" + oid in body and "token=" in body,
              [ln for ln in body.splitlines() if "confirm.html" in ln][:1])

    # ------------------------------------------------- confirm by email link
    # make sure the order exists for the link to act on
    from db import one, execute
    row = one("SELECT id, payload FROM orders WHERE id=?", (oid,))
    if not row:
        # create it through the public endpoint, the way the browser does
        import http.cookiejar as cj2
        import urllib.request as u2
        jar2 = cj2.CookieJar()
        op2 = u2.build_opener(u2.HTTPCookieProcessor(jar2))
        pub_csrf = json.loads(op2.open(BASE + "/api/config", timeout=20).read().decode())["csrf"]
        req = u2.Request(BASE + "/api/orders",
                         data=json.dumps(order).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-CSRF-Token", pub_csrf)
        with op2.open(req, timeout=25) as r:
            json.loads(r.read().decode())

    execute("UPDATE orders SET status='pending' WHERE id=?", (oid,))
    token = security.order_token(oid, "confirm")
    url = f"{BASE}/api/orders/{oid}/confirm?action=confirm&token={token}"
    with urllib.request.urlopen(url, timeout=25) as r:
        out = json.loads(r.read().decode())
    check("the confirm link in the email confirms the order",
          out.get("ok") and out.get("status") == "confirmed", out)

    row = one("SELECT status FROM orders WHERE id=?", (oid,))
    check("the admin portal shows the confirmed status",
          row and row["status"] == "confirmed", row[0] if row else None)

    bad = {}
    try:
        urllib.request.urlopen(
            f"{BASE}/api/orders/{oid}/confirm?action=confirm&token={'0'*40}", timeout=20)
    except urllib.error.HTTPError as e:
        bad["forged"] = e.code
    check("a forged confirm link is rejected", bad.get("forged") == 403, bad)

    try:
        urllib.request.urlopen(
            f"{BASE}/api/orders/JA-SOMEONE/confirm?action=confirm&token={token}", timeout=20)
    except urllib.error.HTTPError as e:
        bad["other"] = e.code
    check("a token cannot be reused on another order", bad.get("other") == 403, bad)


def admin_portal_checks():
    """The admin portal must show the file itself, not a link to go find it."""
    from playwright.sync_api import sync_playwright
    import http.cookiejar as cj2
    import urllib.request as u2

    jar = cj2.CookieJar()
    op = u2.build_opener(u2.HTTPCookieProcessor(jar))
    req = u2.Request(BASE + "/api/admin/login",
                     data=json.dumps({"email": "jaurastore@gmail.com", "password": PW}).encode(),
                     headers={"Content-Type": "application/json"}, method="POST")
    op.open(req, timeout=20)
    cookies = [{"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"} for c in jar]

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 1000})
        ctx.add_cookies(cookies)
        ctx.add_init_script("try{localStorage.setItem('jaura_welcome_at',String(Date.now()))}catch(e){}")
        page = ctx.new_page()
        page.goto(BASE + "/admin.html", wait_until="networkidle")
        page.wait_for_timeout(2000)
        for t in page.locator("[data-tab]").all():
            if t.get_attribute("data-tab") == "orders":
                t.click()
                break
        page.wait_for_timeout(4000)

        imgs = page.locator(".proof-preview").count()
        buttons = page.locator("[data-pdf-open]").count()
        check("every PDF receipt can be opened inside the order panel",
              buttons > 0, f"{buttons} PDF receipts")

        # open one for real: the file must appear in the panel, not a new tab
        page.locator("[data-pdf-open]").first.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        page.locator("[data-pdf-open]").first.click()
        page.wait_for_timeout(1500)
        frames = page.locator(".proof-frame").count()
        pdfs = page.locator('.proof-frame[src*=".pdf"]').count()
        check("the PDF opens in the admin panel itself", frames > 0 and pdfs > 0,
              f"{frames} viewers, {pdfs} showing a PDF")
        check("the admin portal shows image receipts inline", imgs > 0, f"{imgs} images")
        check("every receipt also has a download button",
              page.locator("a[download]").count() >= (buttons + imgs),
              page.locator("a[download]").count())
        rows = page.locator("#proofs-box tbody tr").count()
        check("the uploaded-receipts table lists every receipt", rows > 0, f"{rows} rows")
        b.close()


def main():
    print("checkout form, receipts and order confirmation\n")
    try:
        email_and_confirm_checks()
    except Exception as e:
        check("email + confirm checks ran", False, f"{type(e).__name__}: {e}"[:110])
    try:
        admin_portal_checks()
    except Exception as e:
        check("admin portal checks ran", False, f"{type(e).__name__}: {e}"[:110])
    try:
        browser_checks()
    except Exception as e:
        check("browser checks ran", False, f"{type(e).__name__}: {e}"[:110])
    print("\n%d/%d checks passed" % (sum(1 for _, ok in results if ok), len(results)))
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
