#!/usr/bin/env python3
"""Fetch live Wix product prices and merge into catalog."""
import json, re, time, urllib.request
from pathlib import Path

ROOT = Path("/home/user/jaura-store")
UA = "Mozilla/5.0 (compatible; JauraPriceBot/1.0)"

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "ignore")

def money(txt):
    if not txt:
        return None
    n = re.sub(r"[^\d]", "", str(txt))
    if not n:
        return None
    v = int(n)
    return v if 50 <= v <= 2000000 else None

def parse_page(html, slug):
    out = {"priceNgn": None, "priceCfa": None, "compareCfa": None, "compareNgn": None, "name": None}
    title = re.search(r"<title>([^<]+)</title>", html, re.I)
    h1 = re.search(r"<h1[^>]*>([\s\S]{0,200}?)</h1>", html, re.I)
    blob = " ".join(x for x in [(h1.group(1) if h1 else ""), (title.group(1) if title else "")] if x)
    blob = re.sub(r"<[^>]+>", " ", blob)
    blob = re.sub(r"\s+", " ", blob).strip()
    name = re.sub(r"\s*[₦N]\s*[\d,.\s]+.*", "", blob, flags=re.I)
    name = re.sub(r"\s*\|\s*J Aura.*", "", name, flags=re.I).strip(" .-")
    if name and 2 < len(name) < 90:
        out["name"] = name
    ngn = re.search(r"₦\s*([\d,\s]+)", blob) or re.search(r"₦\s*([\d,\s]+)", html)
    if ngn:
        out["priceNgn"] = money(ngn.group(1))
    if not out["priceNgn"]:
        m = re.search(r"-(\d{3,6})$", slug)
        if m:
            out["priceNgn"] = money(m.group(1))
    cfas = [money(x) for x in re.findall(r"F\s*CFA[^\d]{0,12}([\d\s,\.]+)", html, re.I)]
    cfas = [c for c in cfas if c]
    # keep plausible CFA
    cfas = [c for c in cfas if c < 2000000]
    if len(cfas) >= 2:
        a, b = cfas[0], cfas[1]
        out["compareCfa"] = max(a, b)
        out["priceCfa"] = min(a, b)
        if out["compareCfa"] == out["priceCfa"]:
            out["compareCfa"] = None
    elif cfas:
        out["priceCfa"] = cfas[0]
    return out

def load_catalog():
    p = ROOT / "js" / "products-data.js"
    text = p.read_text()
    data = json.loads(text[text.find("["): text.rfind("]") + 1])
    return data, p, text

def main():
    data, js_path, _ = load_catalog()
    cache_path = ROOT / "data" / "wix_price_cache.json"
    cache = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
    ok = 0
    fail = []
    for i, p in enumerate(data):
        slug = p.get("slug") or ""
        url = f"https://jaurastore.wixsite.com/j-aura-store/product-page/{slug}"
        parsed = cache.get(slug)
        if not parsed or not parsed.get("priceNgn"):
            try:
                html = get(url)
                parsed = parse_page(html, slug)
                cache[slug] = parsed
            except Exception as e:
                fail.append((slug, str(e)))
                parsed = cache.get(slug) or {}
            time.sleep(0.18)
        ngn = parsed.get("priceNgn")
        cfa = parsed.get("priceCfa")
        cmpc = parsed.get("compareCfa")
        if ngn:
            p["priceNgn"] = ngn
            p["priceCfa"] = cfa if cfa else max(1, round(ngn / 2.45))
            if cmpc and cmpc > p["priceCfa"]:
                p["compareCfa"] = cmpc
                p["badge"] = "sale"
            else:
                p["compareCfa"] = None
                if p.get("badge") == "sale":
                    p["badge"] = ""
            p["compareNgn"] = None
            ok += 1
        if i % 25 == 0:
            print(i, "ok", ok, p.get("name"), p.get("priceNgn"), p.get("priceCfa"))
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    (ROOT / "data" / "seed.json").write_text(json.dumps(data, ensure_ascii=False))
    js_path.write_text("window.JA_SEED = " + json.dumps(data, ensure_ascii=False) + ";\n")
    print("updated", ok, "of", len(data), "fails", len(fail))
    sm = next((p for p in data if "sandwich" in (p.get("name") or "").lower()), None)
    print("sandwich", sm.get("name") if sm else None, sm.get("priceNgn") if sm else None, sm.get("priceCfa") if sm else None, sm.get("compareCfa") if sm else None)

if __name__ == "__main__":
    main()
