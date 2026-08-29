#!/usr/bin/env python3
"""Fill missing NGN/CFA from Wix slugs and the house rate 1 CFA = NGN 2.45."""
import json
import re
from pathlib import Path

ROOT = Path("/home/user/jaura-store")
RATE = 2.45  # Wix converter: 1 CFA = NGN 2.45

KNOWN = {
    "mouth-spray-1400": (1400, 600, 1000),
    "bag-k-7500": (7500, 3200, None),
    "mini-fan-3850": (3850, 1600, 3000),
    "key-holder-750": (750, 325, None),
    "q8-wireless-mircophone-15700": (15700, 6500, 15000),
    "electric-steam-iron-16000": (16000, 6800, None),
    "enny-big-bag-comes-with-a-scarf-22000": (22000, 9000, None),
    "2-in-1-lipstick-lipgloss-2400": (2400, 1000, 1500),
    "heels-27000": (27000, 11000, None),
    "min-min-bag-10000": (10000, 4200, None),
    "luki-2-in-1-bag-20000": (20000, 8500, None),
    "selfie-stick-5000": (5000, 2100, 3500),
    "leather-crocs-bag-19000": (19000, 7800, 10000),
    "tomi-wrist-watch-02-23000": (23000, 9600, None),
    "acne-removal-face-mask-550": (550, 250, 500),
    "vacuum-phone-holder-4900": (4900, 2000, 3500),
    "m10-earpod-9500": (9500, 3900, 5000),
    "2-layers-plate-rack-28000": (28000, 11500, 14000),
    "24-pcs-of-gold-cutlery-set-18500": (18500, 7600, 9500),
    "wipes-5-packs-2500": (2500, 1100, 2000),
    "electronic-personal-scale-9000": (9000, 3700, 5000),
    "kitchen-tissue-3000": (3000, 1250, 2800),
    "wrist-bp-monitor-12500": (12500, 5200, 7000),
    "ac-design-fan-13500": (13500, 5600, None),
    "shoe-wipes-1600": (1600, 680, None),
    "insulated-mugs-10500": (10500, 4350, None),
    "stanley-cup-13000": (13000, 5400, None),
    "f15-wireless-mic-single-13000": (13000, 5350, 7500),
    "f15-wireless-mic-double-18000": (18000, 7400, 9500),
    "q13-ai-smart-tracking-tripod-with-light-40000": (40000, 16350, 20000),
    "menstrual-relief-belt-12000": (12000, 4900, 6000),
}

ps = json.loads((ROOT / "data" / "seed.json").read_text())


def slug_ngn(slug):
    nums = [int(n) for n in re.findall(r"(\d{3,6})", slug or "") if 200 <= int(n) <= 500000]
    return nums[-1] if nums else 0


fixed = 0
for p in ps:
    slug = p.get("slug") or ""
    ngn = int(p.get("priceNgn") or 0)
    cfa = int(p.get("priceCfa") or 0)
    cmp_cfa = p.get("compareCfa")
    if slug in KNOWN:
        ngn, cfa, cmp_cfa = KNOWN[slug]
    if ngn < 200:
        ngn = slug_ngn(slug) or ngn
    if ngn < 200 and cfa >= 100:
        ngn = round(cfa * RATE)
    if cfa < 100 and ngn >= 200:
        cfa = max(100, round(ngn / RATE))
    if ngn < 200:
        from_name = slug_ngn(re.sub(r"\s+", "-", (p.get("name") or "").lower()))
        if from_name:
            ngn = from_name
    if cfa < 100 and ngn >= 200:
        cfa = max(100, round(ngn / RATE))
    if cmp_cfa and int(cmp_cfa) <= cfa:
        cmp_cfa = None
    p["priceNgn"] = ngn
    p["priceCfa"] = cfa
    p["compareCfa"] = cmp_cfa
    p["compareNgn"] = round(cmp_cfa * RATE) if cmp_cfa else None
    if p["badge"] == "" and cmp_cfa and cmp_cfa > cfa:
        p["badge"] = "sale"
    fixed += 1

out = ROOT / "data" / "seed.json"
out.write_text(json.dumps(ps, ensure_ascii=False, indent=2), encoding="utf-8")
(ROOT / "js" / "products-data.js").write_text(
    "window.JA_SEED = " + json.dumps(ps, ensure_ascii=False) + ";\n", encoding="utf-8"
)
bad = [p for p in ps if p["priceNgn"] < 200 or p["priceCfa"] < 50]
print("updated", len(ps), "still-bad", len(bad))
