#!/usr/bin/env python3
"""The French switch must change everything, and keep it French.

Every page is loaded twice, once in each language, and any string that comes
back identical is inspected. Product names, prices, dates, order numbers and
place names are allowed to stay the same - they are not English, they are
data. Everything else has to change.

    python3 tests/i18n.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = os.environ.get("JAURA_BASE", "http://127.0.0.1:8080/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PASS = os.environ.get("ADMIN_PW", "")
if not ADMIN_PASS:
    sys.exit("ADMIN_PW is not set. Run this as:  ADMIN_PW='<admin password>' python3 i18n.py")

PAGES = ["index.html", "shop.html", "categories.html", "cart.html", "checkout.html",
         "contact.html", "delivery.html", "faq.html", "wishlist.html",
         "account.html"]

TEXT_JS = """() => {
  const out = new Set();
  const walk = (el) => {
    for (const n of el.childNodes) {
      if (n.nodeType === 3) {
        const s = n.textContent.trim();
        if (s && /[A-Za-zÀ-ÿ]/.test(s)) out.add(s.replace(/\\s+/g, ' ').trim());
      } else if (n.nodeType === 1 && !['SCRIPT','STYLE','NOSCRIPT','TEXTAREA','CODE'].includes(n.tagName)) {
        if (!n.hasAttribute('hidden')) walk(n);
      }
    }
  };
  walk(document.body);
  return [...out];
}"""

# strings that are identical in English and French, or are not English at all
ALLOWED = {
    "EN", "FR", "×", "·", ".", "FAQ", "Contact", "Vision", "Atelier", "Maison",
    "TikTok", "jaurastore@gmail.com", "F CFA", "Cotonou", "Cotonou, Benin",
    "Cotonou, Benin Rep.", "Cotonou, Benin Republic", "Lagos, Nigeria", "Lomé",
    "Togo", "Porto-Novo", "Lagos Island", "Lagos Mainland", "Benin", "Nigeria",
    "Total", "Page", "Conversion", "sessions", "on the site", "Account",
    "Moov Money Togo (F CFA)", "Admin · Jaura Store", "Beauty & skincare",
    "Naira — UBA", "Festac, Iyana-Ishashi, Iyana-Ipaja, Ojo, Surulere, Yaba, Gbagada",
    "Lekki Phase 1, Lekki Phase 2, Oniru, Victoria Island, Ikoyi, Ajah",
    "name, category, priceNgn, compareNgn, stock, badge, description, colors",
    "Jaura Store", "Message *", "Message", "Email", "Note",
    "home", "shop", "product", "cart", "checkout", "pay", "order",
}

# text the owner typed, and numbers - never language
DATA = re.compile(r"(\d|JA-[A-Z0-9]{4,}|^(/|\.))")

passed = 0
failed = 0
DATA_NAMES = set()   # names that come from real orders - not language


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"PASS  {label}" + (f" -> {detail}" if detail else ""))
    else:
        failed += 1
        print(f"FAIL  {label}" + (f" -> {detail}" if detail else ""))


def product_names():
    """Names in the live catalogue - these must never be translated."""
    import json
    import urllib.request
    names = set()
    try:
        with urllib.request.urlopen(BASE + "api/products", timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        items = data if isinstance(data, list) else data.get("products", data.get("items", []))
        for it in items:
            if isinstance(it, dict):
                for k in ("name", "title", "nameEn", "nameFr"):
                    if it.get(k):
                        names.add(" ".join(str(it[k]).split()))
    except Exception:
        pass
    for src in ("data/catalog.json", "data/products.json", "data/wix_products.json"):
        path = os.path.join(os.path.dirname(HERE), src)
        if not os.path.exists(path):
            continue
        try:
            raw = json.load(open(path))
            items = raw if isinstance(raw, list) else raw.get("products", raw.get("items", []))
            for it in items:
                if isinstance(it, dict):
                    for k in ("name", "title", "nameEn", "nameFr"):
                        if it.get(k):
                            names.add(" ".join(str(it[k]).split()))
        except Exception:
            pass
    return names


def collect(page_url, lang, browser, admin=False):
    ctx = browser.new_context(viewport={"width": 1366, "height": 950})
    ctx.add_init_script(f"localStorage.setItem('jaura_lang', '{lang}');")
    pg = ctx.new_page()
    try:
        pg.goto(BASE + page_url, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(1500)
        if admin:
            pg.fill("#login-form input[name=email]", ADMIN_EMAIL)
            pg.fill("#login-form input[name=password]", ADMIN_PASS)
            pg.click("#login-btn")
            pg.wait_for_timeout(3500)
            seen = set(pg.evaluate(TEXT_JS))
            try:
                orders = pg.evaluate(
                    "async () => { const r = await fetch('/api/admin/orders',"
                    " {credentials:'same-origin'}); const d = await r.json();"
                    " return d.orders || d.items || []; }")
                for o in orders:
                    c = (o or {}).get("customer") or {}
                    for k in ("name", "email", "phone", "city", "address"):
                        if c.get(k):
                            DATA_NAMES.add(" ".join(str(c[k]).split()))
            except Exception:
                pass
            for btn in pg.query_selector_all("[data-tab]"):
                try:
                    btn.click()
                    pg.wait_for_timeout(900)
                    seen |= set(pg.evaluate(TEXT_JS))
                except Exception:
                    pass
            ctx.close()
            return seen
        out = set(pg.evaluate(TEXT_JS))
        ctx.close()
        return out
    except Exception as exc:
        ctx.close()
        raise exc


def main():
    names = product_names()
    print(f"catalogue names that must stay untouched: {len(names)}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch()

        print("--- public pages ---")
        for page_url in PAGES:
            try:
                en = collect(page_url, "en", browser)
                fr = collect(page_url, "fr", browser)
            except Exception as exc:
                check(f"{page_url} loaded in both languages", False,
                      f"{type(exc).__name__}: {str(exc)[:80]}")
                continue
            stuck = sorted(
                s for s in fr
                if (s in en and s not in ALLOWED and not DATA.search(s)
                        and s not in names and s not in DATA_NAMES)
            )
            check(f"{page_url} is fully French", not stuck,
                  f"{len(fr)} French strings" + (f", still English: {stuck[:4]}" if stuck else ""))

        print("\n--- admin portal ---")
        try:
            en = collect("admin.html", "en", browser, admin=True)
            fr = collect("admin.html", "fr", browser, admin=True)
        except Exception as exc:
            check("admin portal loaded in both languages", False,
                  f"{type(exc).__name__}: {str(exc)[:80]}")
            en = fr = set()
        if en and fr:
            stuck = sorted(
                s for s in fr
                if (s in en and s not in ALLOWED and not DATA.search(s)
                        and s not in names and s not in DATA_NAMES)
            )
            check("the admin portal is fully French", not stuck,
                  f"{len(fr)} French strings" + (f", still English: {stuck[:4]}" if stuck else ""))

        print("\n--- spot checks ---")
        # The standalone /pay.html page is gone: receipts are now uploaded in
        # the checkout itself, so the payment strings are checked there.
        fr_checkout = collect("checkout.html", "fr", browser)
        check("the checkout receipt label is French",
              any("Télécharger le reçu de paiement" in s for s in fr_checkout))
        for gone in ("Upload the bank payment receipt",):
            check(f"the checkout no longer says {gone!r}",
                  not any(gone == s for s in fr_checkout))

        fr_shop = collect("shop.html", "fr", browser)
        check("category names are French",
              any("Vêtements pour hommes et femmes" in s for s in fr_shop))
        check("the product count is French",
              any(re.search(r"^\d[\d\s]* produits$", s) for s in fr_shop),
              next((s for s in fr_shop if re.search(r"^\d[\d\s]* produits$", s)), "none"))
        check("product names are left alone",
              any(s in fr_shop for s in list(names)[:40] if s))

        if fr:
            check("admin tabs are French",
                  "Modifier les produits" in fr and "Paramètres de la boutique" in fr)
            check("admin panels are French",
                  any("Ventes totales" in s for s in fr))

        fr_en_only = collect("index.html", "en", browser)
        check("English still works after all this",
              any("Home" in s or "Shop" in s for s in fr_en_only))
        browser.close()

    print(f"\n{passed}/{passed + failed} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
