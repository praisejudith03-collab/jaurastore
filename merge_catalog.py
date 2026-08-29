#!/usr/bin/env python3
"""Merge Wix scrape + slug prices + old seed into a live catalog."""
import json, re
from pathlib import Path

ROOT = Path("/home/user/jaura-store")
wix = json.loads((ROOT / "data/wix_products.json").read_text())
old = json.loads((ROOT / "data/seed.json").read_text())

def norm(s):
    s = s.lower()
    s = re.sub(r"[₦n,.\-_/]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def slug_ngn(slug):
    m = re.search(r"-(\d{3,6})$", slug)
    if not m:
        return None
    n = int(m.group(1))
    if 200 <= n <= 250000:
        return n
    return None

old_by_slug = {p.get("slug"): p for p in old}
old_by_name = {norm(p["name"]): p for p in old}

# extra known prices from live Wix pages we already parsed
KNOWN = {
    "2-in-1-lipstick-lipgloss-2400": (2400, 1000, 1500),
    "6-in-1-lipgloss-set": (4900, 2000, 3500),
    "100l-storage-bag": (10300, 4200, 6000),
    "mini-mirror": (950, 400, 800),
    "hair-scrunchies-4-in-1": (3000, 1250, 1800),
    "beard-balm": (2950, 1200, 2000),
    "blue-idea-rechargeable-clipper": (11750, 4800, 6000),
    "lip-gel": (1150, 450, 600),
    "lip-scrub": (2450, 1000, 1500),
    "lip-gloss-g": (None, 900, 1200),
    "lipgloss-f": (None, 900, 1200),
    "tomi-wrist-watch-02-23000": (23000, 9600, None),
    "i18pro-flip-phone-26500": (26500, 10900, 15000),
    "matturi-wristwatch-22000": (22000, 9000, None),
    "tomi-wrist-watch-16000": (16000, 6500, 12000),
    "wrist-watch-box-2200": (2200, 900, None),
    "naidu-pearl-watch-9800": (9800, 4000, None),
    "deblve-unisex-set-12750": (12750, 5200, None),
    "fashion-bracelet-5600-1": (5600, 2300, None),
    "choker-set-9500": (9500, 4000, None),
    "mouth-spray-1400": (1400, 600, 1000),
    "mini-fan-3850": (3850, 1600, 3000),
    "electric-steam-iron-16000": (16000, 6800, None),
    "2-layers-plate-rack-28000": (28000, 11500, 14000),
    "24-pcs-of-gold-cutlery-set-18500": (18500, 7600, 9500),
    "wipes-5-packs-2500": (2500, 1100, 2000),
    "electronic-personal-scale-9000": (9000, 3700, 5000),
    "kitchen-tissue-3000": (3000, 1250, 2800),
    "fur-cap": (3700, 1500, 2000),
    "q8-wireless-mircophone-15700": (15700, 6500, 15000),
    "selfie-stick-5000": (5000, 2100, 3500),
    "vacuum-phone-holder-4900": (4900, 2000, 3500),
    "m10-earpod-9500": (9500, 3900, 5000),
    "wrist-bp-monitor-12500": (12500, 5200, 7000),
    "ac-design-fan-13500": (13500, 5600, None),
    "insulated-mugs-10500": (10500, 4350, None),
    "f15-wireless-mic-single-13000": (13000, 5350, 7500),
    "f15-wireless-mic-double-18000": (18000, 7400, 9500),
    "car-diffuser": (None, 500, 950),
}

out = []
for i, p in enumerate(wix, 1):
    slug = p["slug"]
    ngn = p.get("priceNgn") or 0
    cfa = p.get("priceCfa") or 0
    cmp = p.get("compareCfa")
    if slug in KNOWN:
        kn, kc, kcmp = KNOWN[slug]
        if kn: ngn = kn
        if kc: cfa = kc
        if kcmp: cmp = kcmp
    if not ngn:
        ngn = slug_ngn(slug) or 0
    # match old seed
    hit = old_by_slug.get(slug) or old_by_name.get(norm(p["name"]))
    if hit:
        if not ngn:
            ngn = hit.get("priceNgn") or 0
        if not cfa:
            cfa = hit.get("priceCfa") or 0
        if not cmp:
            cmp = hit.get("compareCfa")
        if hit.get("category") and p.get("category") == "household":
            p["category"] = hit["category"]
    if ngn and not cfa:
        cfa = max(1, round(ngn * 0.41))
    if cfa and not ngn:
        ngn = max(1, round(cfa * 2.45))
    name = p["name"]
    name = re.sub(r"\s*₦\s*[\d,]+", "", name).strip(" .")
    out.append({
        "id": f"wix-{i:03d}",
        "sku": f"WIX{i:03d}",
        "slug": slug,
        "name": name,
        "category": p.get("category") or "household",
        "priceCfa": int(cfa or 0),
        "compareCfa": int(cmp) if cmp else None,
        "priceNgn": int(ngn or 0),
        "compareNgn": None,
        "image": p["image"],
        "description": name,
        "stock": 0 if (not ngn and cfa) else 24,
        "badge": "sale" if cmp else "",
        "featured": i <= 16,
        "colors": [],
    })

(ROOT / "data/seed.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
js = "window.JA_SEED = " + json.dumps(out, ensure_ascii=False) + ";\n"
(ROOT / "js/products-data.js").write_text(js)
print("catalog", len(out), "with ngn", sum(1 for p in out if p["priceNgn"]), "with cfa", sum(1 for p in out if p["priceCfa"]))
print("sample", out[7])
