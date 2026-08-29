#!/usr/bin/env python3
"""Pull live Wix product options (colour, size, metal…), descriptions and prices."""
import html as htmlmod
import json, re, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path("/home/user/jaura-store")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
CACHE = ROOT / "data" / "wix_detail_cache.json"
NGN_RE = re.compile(r"(?:₦|&#8358;|\u20a6)\s*([\d.,]{2,8})")


def get(url, tries=6):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    wait = 6
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=50) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503, 502) and i < tries - 1:
                time.sleep(wait)
                wait = min(wait * 1.6, 25)
                continue
            raise
        except Exception:
            if i < tries - 1:
                time.sleep(wait)
                continue
            raise


def money(txt, lo=50, hi=400000):
    if not txt:
        return None
    n = re.sub(r"[^\d]", "", str(txt))
    if not n:
        return None
    v = int(n)
    return v if lo <= v <= hi else None


def extract_balanced(html, start):
    if start < 0 or start >= len(html) or html[start] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]
    return None


def rich_text(val):
    if not val:
        return ""
    if isinstance(val, dict):
        parts = []
        if val.get("text"):
            parts.append(str(val["text"]))
        for n in val.get("nodes") or []:
            parts.append(rich_text(n))
        return " ".join(p for p in parts if p).strip()
    s = str(val).strip()
    if not s or s in ("{}", "null"):
        return ""
    if s.startswith("{") and "nodes" in s:
        try:
            return rich_text(json.loads(s))
        except Exception:
            pass
    s = htmlmod.unescape(s)
    s = s.replace("&#010;", "\n").replace("\r", "\n")
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    if s in ("", "{}", '{"nodes":[]}'):
        return ""
    return s


def clean_name(name):
    if not name:
        return None
    name = htmlmod.unescape(name)
    name = re.sub(r"<[^>]+>", " ", name)
    name = NGN_RE.sub("", name)
    name = re.sub(r"\s*\|\s*J Aura.*", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    if 2 < len(name) < 90:
        return name
    return None


def parse_ngn(*texts):
    for t in texts:
        if not t:
            continue
        m = NGN_RE.search(t)
        if m:
            v = money(m.group(1))
            if v:
                return v
    return None


def parse_page(html, slug):
    out = {
        "slug": slug,
        "name": None,
        "description": "",
        "priceNgn": None,
        "priceCfa": None,
        "compareCfa": None,
        "options": [],
        "additionalInfo": [],
        "sku": "",
    }
    title = re.search(r"<title>([^<]+)</title>", html, re.I)
    h1 = re.search(r"<h1[^>]*>([\s\S]{0,240}?)</h1>", html, re.I)
    title_txt = title.group(1) if title else ""
    h1_txt = re.sub(r"<[^>]+>", " ", h1.group(1)) if h1 else ""
    out["name"] = clean_name(h1_txt) or clean_name(title_txt)
    out["priceNgn"] = parse_ngn(h1_txt, title_txt)

    needle = '"catalog":{"product":'
    idx = html.rfind(needle)
    prod = None
    if idx >= 0:
        start = html.find("{", idx + len(needle) - 1)
        raw = extract_balanced(html, start)
        if raw:
            try:
                prod = json.loads(raw)
            except Exception:
                prod = None
    if prod:
        nm = clean_name(prod.get("name") or "")
        if nm and (not out["name"] or len(nm) >= len(out["name"])):
            out["name"] = nm
        if not out["priceNgn"]:
            out["priceNgn"] = parse_ngn(prod.get("name") or "")
        out["description"] = rich_text(prod.get("description"))
        out["sku"] = str(prod.get("sku") or "")
        disc = money(prod.get("discountedPrice") or prod.get("price"))
        cmpc = money(prod.get("comparePrice"))
        if disc:
            out["priceCfa"] = disc
        if cmpc and disc and cmpc > disc:
            out["compareCfa"] = cmpc
        opts = []
        for opt in prod.get("options") or []:
            title_o = str(opt.get("title") or opt.get("key") or "").strip()
            vals = []
            seen = set()
            otype = opt.get("optionType") or "DROP_DOWN"
            for sel in opt.get("selections") or []:
                raw_v = str(sel.get("value") or "").strip()
                desc_v = str(sel.get("description") or sel.get("key") or "").strip()
                hexv = raw_v if re.match(r"^#[0-9A-Fa-f]{3,8}$", raw_v) else (
                    desc_v if re.match(r"^#[0-9A-Fa-f]{3,8}$", desc_v) else ""
                )
                label = desc_v if desc_v and not re.match(r"^#[0-9A-Fa-f]{3,8}$", desc_v) else (raw_v or desc_v)
                if hexv and (not label or label.startswith("#")):
                    label = hexv
                v = label
                if v and v.lower() not in seen:
                    seen.add(v.lower())
                    vals.append(hexv if (otype == "COLOR" and hexv) else v)
            if title_o and vals:
                opts.append({"title": title_o, "type": otype, "values": vals})
        out["options"] = opts
        info = []
        for sec in prod.get("additionalInfo") or []:
            t = str(sec.get("title") or "").strip()
            d = rich_text(sec.get("description"))
            if t or d:
                info.append({"title": t, "description": d})
        out["additionalInfo"] = info

    cfas = [money(x) for x in re.findall(r"F[\s\u00a0\u202f]*CFA[^\d]{0,12}([\d\s,\.]+)", html, re.I)]
    cfas = [c for c in cfas if c]
    uniq = []
    for c in cfas:
        if c not in uniq:
            uniq.append(c)
    if len(uniq) >= 2:
        out["compareCfa"] = max(uniq[0], uniq[1])
        out["priceCfa"] = min(uniq[0], uniq[1])
        if out["compareCfa"] == out["priceCfa"]:
            out["compareCfa"] = None
    elif uniq:
        if not out["priceCfa"] or uniq[0] < out["priceCfa"]:
            out["priceCfa"] = uniq[0]

    if not out["priceNgn"]:
        m = re.search(r"-(\d{3,6})$", slug)
        if m:
            out["priceNgn"] = money(m.group(1))
    if not out["description"]:
        ld = re.search(r'<script type="application/ld\+json">([\s\S]*?)</script>', html)
        if ld:
            try:
                data = json.loads(ld.group(1))
                out["description"] = rich_text(data.get("description"))
            except Exception:
                pass
    return out


def load_catalog():
    p = ROOT / "js" / "products-data.js"
    text = p.read_text()
    data = json.loads(text[text.find("[") : text.rfind("]") + 1])
    return data, p


def fetch_one(slug):
    url = f"https://jaurastore.wixsite.com/j-aura-store/product-page/{slug}"
    html = get(url)
    return parse_page(html, slug)


def suspicious(parsed):
    if not parsed or not parsed.get("ok"):
        return True
    n = parsed.get("priceNgn")
    slug = parsed.get("slug") or ""
    name = parsed.get("name") or ""
    if name and len(name) < 5:
        return True
    m = re.search(r"-(\d{3,6})$", slug)
    if n and m:
        slugp = int(m.group(1))
        if n != slugp and str(n).startswith(str(slugp)):
            return True
    return False


def main():
    data, js_path = load_catalog()
    orig_names = {p["slug"]: p.get("name") for p in data}
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text())
        except Exception:
            cache = {}
    need = []
    for p in data:
        slug = p.get("slug") or ""
        cached = cache.get(slug)
        # always refresh sandwich; otherwise skip solid cache
        if slug == "10in1-raf-sandwich-maker" or not cached or suspicious(cached):
            need.append(slug)
    print("need", len(need), "cached-good", len(data) - len(need), "total", len(data), flush=True)
    fail = []
    for i, slug in enumerate(need, 1):
        try:
            parsed = fetch_one(slug)
            parsed["ok"] = True
            cache[slug] = parsed
            if i % 10 == 0 or parsed.get("options") or "sandwich" in slug:
                print(i, "/", len(need), slug, "ngn", parsed.get("priceNgn"), "cfa", parsed.get("priceCfa"),
                      "cmp", parsed.get("compareCfa"),
                      "opts", [o["title"] for o in parsed.get("options") or []],
                      "name", parsed.get("name"), flush=True)
        except Exception as e:
            fail.append((slug, str(e)))
            print("fail", slug, e, flush=True)
        time.sleep(0.22)
        if i % 15 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))

    updated = 0
    with_opts = 0
    for p in data:
        slug = p.get("slug") or ""
        parsed = cache.get(slug) or {}
        ngn = parsed.get("priceNgn")
        cfa = parsed.get("priceCfa")
        cmpc = parsed.get("compareCfa")
        if ngn and not suspicious({**parsed, "ok": True}):
            p["priceNgn"] = ngn
            p["priceCfa"] = cfa if cfa else max(1, round(ngn / 2.45))
            if cmpc and cmpc > p["priceCfa"]:
                p["compareCfa"] = cmpc
                p["badge"] = "sale"
            else:
                p["compareCfa"] = None
                if p.get("badge") == "sale" and not cmpc:
                    p["badge"] = ""
            p["compareNgn"] = None
            updated += 1
        elif cfa:
            m = re.search(r"-(\d{3,6})$", slug)
            if m and (not p.get("priceNgn") or suspicious(parsed)):
                p["priceNgn"] = int(m.group(1))
            p["priceCfa"] = cfa
            if cmpc and cmpc > cfa:
                p["compareCfa"] = cmpc
                p["badge"] = "sale"
            updated += 1
        desc = (parsed.get("description") or "").strip()
        if desc:
            p["description"] = desc[:900]
        elif p.get("description") == p.get("name"):
            p["description"] = ""
        opts = parsed.get("options") or []
        p["options"] = opts
        if opts:
            with_opts += 1
            color_opt = next((o for o in opts if re.search(r"colou?r", o["title"], re.I)), None)
            p["colors"] = [v for v in (color_opt["values"] if color_opt else []) if not str(v).startswith("#")]
        else:
            p["colors"] = p.get("colors") or []
        info = parsed.get("additionalInfo") or []
        if info:
            p["additionalInfo"] = info
        scraped = parsed.get("name")
        current = orig_names.get(slug) or p.get("name") or ""
        if scraped and len(scraped) >= max(8, len(current) - 4):
            p["name"] = scraped
        else:
            p["name"] = current
    (ROOT / "data" / "seed.json").write_text(json.dumps(data, ensure_ascii=False))
    js_path.write_text("window.JA_SEED = " + json.dumps(data, ensure_ascii=False) + ";\n")
    print("updated prices", updated, "with options", with_opts, "fails", len(fail), "of", len(data), flush=True)
    sm = next((p for p in data if "sandwich" in (p.get("slug") or "")), None)
    if sm:
        print("sandwich", sm.get("name"), sm.get("priceNgn"), sm.get("priceCfa"), sm.get("compareCfa"), sm.get("description"), flush=True)
    from collections import Counter
    c = Counter()
    for p in data:
        for o in p.get("options") or []:
            c[o["title"]] += 1
    print("option titles", c, flush=True)


if __name__ == "__main__":
    main()
