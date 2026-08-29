#!/usr/bin/env python3
"""Scrape J Aura Wix catalog: names, prices, photos."""
import json, re, time, urllib.request
from pathlib import Path

ROOT = Path("/home/user/jaura-store")
IMG_DIR = ROOT / "images" / "products"
IMG_DIR.mkdir(parents=True, exist_ok=True)

CATS = [
    ("clothing", "clothing"),
    ("household", "household-items"),
    ("ankara", "ankara-ready-to-wear"),
    ("accessories", "accessories"),
    ("beauty", "beauty"),
    ("shoes", "shoes"),
    ("gadgets", "gadgets"),
    ("packaging", "packaging"),
    ("skincare", "skincare"),
    ("bags", "bags"),
    ("hair-care", "hair-care"),
    ("nails", "nails"),
    ("gift-set", "gift-set"),
    ("children", "children-items"),
    ("decor", "decor"),
]

UA = "Mozilla/5.0 (compatible; JauraBot/1.0)"

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()

def slugify(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "item"

def parse_money(txt):
    if not txt:
        return None
    n = re.sub(r"[^\d]", "", txt)
    return int(n) if n else None

def fetch_category(slug):
    url = f"https://jaurastore.wixsite.com/j-aura-store/category/{slug}"
    html = get(url).decode("utf-8", "ignore")
    return html

def extract_products(html, cat_id):
    # product links
    items = []
    seen = set()
    # Find product-page blocks
    for m in re.finditer(
        r'href="(https://jaurastore\.wixsite\.com/j-aura-store/product-page/([^"?]+))"',
        html,
    ):
        slug = m.group(2)
        if slug in seen:
            continue
        seen.add(slug)
        items.append({"slug": slug, "url": m.group(1), "category": cat_id})

    # Images near product pages
    img_map = {}
    for m in re.finditer(
        r'(https://static\.wixstatic\.com/media/0a7193_[a-f0-9]+~mv2\.(?:jpg|jpeg|png|webp))[^"]*".{0,400}?product-page/([a-z0-9\-]+)',
        html,
        re.I | re.S,
    ):
        img_map.setdefault(m.group(2), m.group(1))
    for m in re.finditer(
        r'product-page/([a-z0-9\-]+)[^"]*".{0,200}?(https://static\.wixstatic\.com/media/0a7193_[a-f0-9]+~mv2\.(?:jpg|jpeg|png|webp))',
        html,
        re.I | re.S,
    ):
        img_map.setdefault(m.group(1), m.group(2))

    # Simpler: all wix images in order with product slugs in order
    slugs = [i["slug"] for i in items]
    imgs = re.findall(
        r'https://static\.wixstatic\.com/media/0a7193_[a-f0-9]+~mv2\.(?:jpg|jpeg|png)',
        html,
    )
    # first image is often a banner
    product_imgs = [u for u in imgs if "fill/w_12" not in u]
    return items, img_map, imgs

def parse_listing_text(html):
    """Extract name + naira + cfa from markdown-like or raw html listings."""
    text = re.sub(r"<[^>]+>", "\n", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&#[0-9]+;", " ", text)
    # Naira prices
    blocks = []
    # Pattern: Name ₦price ... F CFA sale/regular
    for m in re.finditer(
        r"([A-Za-z0-9][^\n]{1,80}?)\s*[₦N]\s*([\d,\.]+)",
        text,
    ):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" -·.|")
        ngn = parse_money(m.group(2))
        if name and ngn and len(name) > 1:
            blocks.append({"name": name, "priceNgn": ngn, "pos": m.start()})
    return blocks, text

def main():
    catalog = []
    by_slug = {}

    # 1) sitemap for slugs + primary images
    sm = get("https://jaurastore.wixsite.com/j-aura-store/store-products-sitemap.xml").decode()
    for loc, slug, img in re.findall(
        r"<loc>(https://jaurastore\.wixsite\.com/j-aura-store/product-page/([^<]+))</loc>.*?<image:loc>([^<]+)</image:loc>",
        sm,
        re.S,
    ):
        by_slug[slug] = {
            "slug": slug,
            "url": loc,
            "image_url": img.split("/v1/")[0],
            "name": None,
            "priceNgn": None,
            "priceCfa": None,
            "compareCfa": None,
            "category": "household",
        }

    print("sitemap products", len(by_slug))

    # 2) category pages for names, prices, category assignment
    for cat_id, slug in CATS:
        try:
            html = fetch_category(slug)
        except Exception as e:
            print("fail cat", slug, e)
            continue
        # assign category from product-page links in this page
        slugs = re.findall(r"product-page/([a-z0-9\-]+)", html)
        for s in slugs:
            if s in by_slug:
                by_slug[s]["category"] = cat_id
        # names + naira from visible text
        # Wix often: >Name ₦1234< or title then price
        pairs = re.findall(
            r'product-page/([a-z0-9\-]+)[^>]*>[\s\S]{0,80}?',
            html,
        )
        # Extract structured: slug then nearby ₦
        for m in re.finditer(
            r'product-page/([a-z0-9\-]+)"[\s\S]{0,2500}?(?:₦|Naira|NGN)?\s*([\d,]{2,7})?[\s\S]{0,400}?F\s*CFA[^\d]*([\d\s,\.]+)(?:[\s\S]{0,120}?F\s*CFA[^\d]*([\d\s,\.]+))?',
            html,
            re.I,
        ):
            s = m.group(1)
            if s not in by_slug:
                continue
            if m.group(2):
                by_slug[s]["priceNgn"] = parse_money(m.group(2))
            c1 = parse_money(m.group(3))
            c2 = parse_money(m.group(4)) if m.group(4) else None
            if c2 and c1 and c2 < c1:
                by_slug[s]["compareCfa"] = c1
                by_slug[s]["priceCfa"] = c2
            elif c1:
                by_slug[s]["priceCfa"] = c1
        print("cat", slug, "links", len(set(slugs)))
        time.sleep(0.4)

    # 3) fill names from slug, fetch missing prices from product pages (sample first, then all missing)
    missing = [s for s, p in by_slug.items() if not p["priceNgn"] or not p["priceCfa"]]
    print("need product pages", len(missing), "of", len(by_slug))

    for i, slug in enumerate(by_slug.keys()):
        p = by_slug[slug]
        if p["name"] and p["priceNgn"] and p["priceCfa"] and i > 0:
            # still try name from slug always
            pass
        # name from slug
        if not p["name"]:
            p["name"] = slug.replace("-", " ").strip()
            p["name"] = re.sub(r"\s+\d{3,5}$", "", p["name"]).strip()
            p["name"] = p["name"][:1].upper() + p["name"][1:]

    # Fetch product pages for accurate name + prices (batched)
    slugs = list(by_slug.keys())
    for i, slug in enumerate(slugs):
        p = by_slug[slug]
        try:
            html = get(p["url"]).decode("utf-8", "ignore")
        except Exception as e:
            print("fail product", slug, e)
            continue
        title = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.I)
        if not title:
            title = re.search(r"<title>([^|<]+)", html, re.I)
        if title:
            name = re.sub(r"\s+", " ", title.group(1)).strip()
            name = re.sub(r"\s*\|\s*J Aura.*", "", name, flags=re.I).strip()
            if name:
                p["name"] = name
        # prices
        ngn = re.search(r"₦\s*([\d,\s]+)", html)
        if ngn:
            p["priceNgn"] = parse_money(ngn.group(1))
        cfas = re.findall(r"F\s*CFA[^\d]{0,8}([\d\s,\.]+)", html, re.I)
        nums = [parse_money(x) for x in cfas if parse_money(x)]
        nums = [n for n in nums if n and n < 500000]
        if len(nums) >= 2:
            p["compareCfa"] = max(nums[0], nums[1])
            p["priceCfa"] = min(nums[0], nums[1])
        elif nums:
            p["priceCfa"] = nums[0]
        if i % 20 == 0:
            print("product", i, "/", len(slugs), p["name"], p["priceNgn"], p["priceCfa"])
        time.sleep(0.25)

    # 4) download images
    products = []
    for i, (slug, p) in enumerate(sorted(by_slug.items()), 1):
        ext = ".jpg"
        if p.get("image_url", "").endswith(".png"):
            ext = ".png"
        fname = f"{slug[:60]}{ext}"
        dest = IMG_DIR / fname
        if p.get("image_url") and not dest.exists():
            try:
                # request a reasonable jpeg
                src = p["image_url"]
                if "wixstatic.com" in src and "/v1/" not in src:
                    src = src + "/v1/fill/w_600,h_600,al_c,q_80,usm_0.66_1.00_0.01,enc_auto/" + src.split("/")[-1]
                data = get(src)
                dest.write_bytes(data)
            except Exception as e:
                print("img fail", slug, e)
        local = f"images/products/{fname}" if dest.exists() else "images/products/mouth-spray.jpg"
        ngn = p["priceNgn"] or 0
        cfa = p["priceCfa"] or max(1, round(ngn * 0.41))
        products.append({
            "id": f"wix-{i:03d}",
            "sku": f"WIX{i:03d}",
            "slug": slug,
            "name": p["name"] or slug,
            "category": p["category"],
            "priceCfa": cfa,
            "compareCfa": p["compareCfa"],
            "priceNgn": ngn,
            "compareNgn": None,
            "image": local,
            "imageUrl": p.get("image_url") or "",
            "description": p["name"] or slug,
            "stock": 20 if ngn else 0,
            "badge": "sale" if p.get("compareCfa") else "",
            "featured": i <= 12,
            "colors": [],
        })

    out = ROOT / "data" / "wix_products.json"
    out.write_text(json.dumps(products, ensure_ascii=False, indent=2))
    print("wrote", len(products), "to", out)
    with_ngn = sum(1 for p in products if p["priceNgn"])
    print("with naira", with_ngn)

if __name__ == "__main__":
    main()
