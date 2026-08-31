#!/usr/bin/env python3
"""Audit and restore missing product photos from the Wix store sitemap.

The shop never serves third-party/Wix images in the browser, so this tool is
only used to *bring* photos into the repository. It:

  1. parses ``https://jaurastore.wixsite.com/j-aura-store/store-products-sitemap.xml``
     (and falls back to the product-page HTML when the sitemap omits an image)
     to build ``data/wix_image_map.json`` mapping product slugs -> committed
     ``static.wixstatic.com`` media URLs;
  2. audits products whose rendered image is the branded placeholder;
  3. with ``--download``, downloads each map hit (max 8 MB, magic-byte
     verified) into ``images/products/<slug>.<ext>`` and rebuilds the
     repository data state so ``catalog.resolve_image()`` /
     ``photo_repair_candidates()`` auto-wire the photos to their products.

Usage:
  python3 fix_missing_photos.py            # just refresh the audit / map
  python3 fix_missing_photos.py --download # download missing photos
  python3 fix_missing_photos.py --download --force
"""
import os, sys, json, re, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
PRODUCT_DIR = os.path.join(ROOT, "images", "products")
MAP_PATH = os.path.join(ROOT, "data", "wix_image_map.json")
SITEMAP = "https://jaurastore.wixsite.com/j-aura-store/store-products-sitemap.xml"
BASE = "https://jaurastore.wixsite.com/j-aura-store"
MAX_BYTES = 8 * 1024 * 1024
UA = "Mozilla/5.0 (compatible; jauraphotos/1.0)"

_IMAGE_RE = re.compile(
    r"https://static\.wixstatic\.com/media/[^<\"'\s>]+~mv2\.(?:jpg|jpeg|png|webp)",
    re.I)


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(2 * 1024 * 1024)
        return data.decode("utf-8", "replace"), r.headers.get("Content-Type", "")


def parse_sitemap(text):
    """Return {slug: wix_image_url} from the sitemap XML text."""
    urls = re.findall(r"https://jaurastore\.wixsite\.com/j-aura-store/product-page/[^<>\s]+", text or "")
    images = _IMAGE_RE.findall(text or "")
    # The sitemap places each <loc> (product page) immediately before its
    # <image:loc>. Align them positionally; dedupe by slug (keep the first).
    out = {}
    for url, img in zip(urls, images):
        slug = url.rstrip("/").split("/product-page/")[-1].split("/")[0]
        if slug:
            out.setdefault(slug, img)
    return out


def parse_product_page(slug, text):
    """A page-level fallback: any static.wixstatic.com URL on the page."""
    images = _IMAGE_RE.findall(text or "")
    return images[0] if images else None


def build_map(store=None, quiet=False):
    """Refresh data/wix_image_map.json. Returns ({slug: url}, report)."""
    report = {"source": "sitemap", "ok": False}
    try:
        text, ctype = _fetch(SITEMAP)
    except Exception as exc:
        report["error"] = f"sitemap fetch failed: {exc}"
        return store or _read_json(MAP_PATH, {}), report
    mapping = parse_sitemap(text)
    if not mapping:
        report["error"] = "sitemap parsed but no product/image pairs found"
        return store or _read_json(MAP_PATH, {}), report
    report["ok"] = True
    report["count"] = len(mapping)
    report["source"] = ctype or "sitemap"
    _write_json(MAP_PATH, mapping)
    if store:
        merged = dict(store)
        merged.update(mapping)
        _write_json(MAP_PATH, merged)
        return merged, report
    return mapping, report


def _ext_from_url(url, ctype=""):
    ext = ""
    if ctype:
        ctype = ctype.split(";")[0].strip().lower()
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(ctype, "")
    if not ext:
        m = re.search(r"\.(jpg|jpeg|png|webp)(?:$|\?)", url or "", re.I)
        if m:
            ext = "." + m.group(1).lower()
            if ext == ".jpeg":
                ext = ".jpg"
    return ext or ".jpg"


def _magic_ok(data):
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return False


def _product_placeholder_slugs():
    """Slugs of products that still render the branded placeholder."""
    try:
        import catalog as catalog_mod
        products = catalog_mod.merged(include_hidden=True)
    except Exception:
        return set()
    out = set()
    for p in products:
        if (p.get("image") or "").endswith("_placeholder.jpg") or p.get("usesPlaceholder"):
            if p.get("slug"):
                out.add(p["slug"])
    return out


def download(mapping=None, force=False):
    """Download each map hit into images/products/<slug>.<ext>."""
    mapping = mapping if mapping is not None else _read_json(MAP_PATH, {})
    os.makedirs(PRODUCT_DIR, exist_ok=True)
    wanted = _product_placeholder_slugs()
    downloaded, skipped, failed = [], [], []
    slugs = sorted(mapping or {})
    for slug in slugs:
        if wanted and slug not in wanted:
            continue
        url = (mapping.get(slug) or {}).get("url") if isinstance(mapping.get(slug), dict) else mapping.get(slug)
        if not url or not str(url).startswith("http"):
            failed.append({"slug": slug, "reason": "no URL"})
            continue
        dest = os.path.join(PRODUCT_DIR, slug + _ext_from_url(str(url)))
        if os.path.exists(dest) and not force:
            skipped.append(slug)
            continue
        try:
            req = urllib.request.Request(str(url), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read(MAX_BYTES + 1)
                ctype = r.headers.get("Content-Type", "")
            if len(data) > MAX_BYTES:
                failed.append({"slug": slug, "reason": "over 8MB"})
                continue
            if not _magic_ok(data):
                failed.append({"slug": slug, "reason": "not an image"})
                continue
            dest = os.path.join(PRODUCT_DIR, slug + _ext_from_url(str(url), ctype))
            with open(dest, "wb") as fh:
                fh.write(data)
            downloaded.append(slug)
        except Exception as exc:
            failed.append({"slug": slug, "reason": str(exc)[:120]})
    return {"downloaded": downloaded, "skipped": skipped, "failed": failed}


def rebuild_data_state():
    try:
        import repo_sync
        ok, report = repo_sync.regenerate(commit=False, push=False)
    except Exception as exc:                      # pragma: no cover
        return False, {"error": str(exc)}
    return bool(ok), report


def main():
    args = sys.argv[1:]
    download_flag = "--download" in args
    force = "--force" in args
    mapping, map_report = build_map()
    report = {"map": map_report, "download": None, "dataSync": None}
    if map_report.get("ok"):
        report["mapURLs"] = len(mapping)
    if download_flag:
        report["download"] = download(mapping, force=force)
        ok, sync = rebuild_data_state()
        report["dataSync"] = sync
        report["dataSyncOk"] = ok
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
