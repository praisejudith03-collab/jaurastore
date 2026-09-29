#!/usr/bin/env python3
"""One-time in-place compression of active Supabase product images.

URLs and database rows never change: optimized WebP bytes overwrite the same
bucket/key and are verified by downloading that exact key after each write.
Dry-run unless --apply and the exact project ref are both supplied.
"""
import argparse, io, json, os, sys
from urllib.parse import unquote, urlparse
from PIL import Image, ImageOps
from supabase import create_client

MAX_DIMENSION = 1200
QUALITY = 80
IMAGE_FIELDS = ("image", "image_url", "images", "media", "gallery")


def walk(v):
    if isinstance(v, str): yield v
    elif isinstance(v, list):
        for x in v: yield from walk(x)
    elif isinstance(v, dict):
        for x in v.values(): yield from walk(x)


def ref(url):
    if not isinstance(url, str) or "/storage/v1/object/" not in url: return None
    tail = unquote(urlparse(url).path.split("/storage/v1/object/", 1)[1])
    for prefix in ("public/", "sign/", "authenticated/"):
        if tail.startswith(prefix): tail = tail[len(prefix):]
    bits = tail.split("/", 1)
    return tuple(bits) if len(bits) == 2 else None


def optimize(raw):
    with Image.open(io.BytesIO(raw)) as src:
        src.load()
        image = ImageOps.exif_transpose(src)
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        out = io.BytesIO()
        image.save(out, "WEBP", quality=QUALITY, method=6)
        return out.getvalue(), image.size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm-project", default="")
    ap.add_argument("--report", default="batch-compression-report.json")
    args = ap.parse_args()
    url, key = os.getenv("SUPABASE_URL", "").rstrip("/"), os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key: sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    project = urlparse(url).hostname.split(".")[0]
    if args.apply and args.confirm_project != project: sys.exit(f"Refusing apply: pass --confirm-project {project}")
    sb = create_client(url, key)
    rows = sb.table("products").select("*").execute().data or []
    refs = {}
    for row in rows:
        # A false active flag or explicit hidden/deleted status is not active.
        if row.get("active") is False or row.get("is_active") is False or str(row.get("status", "")).lower() in ("deleted", "draft", "inactive"):
            continue
        for field in IMAGE_FIELDS:
            for value in walk(row.get(field)):
                r = ref(value)
                if r: refs[r] = value
    report = {"project": project, "mode": "apply" if args.apply else "dry-run", "active_products": len(rows),
              "referenced_objects": len(refs), "objects": [], "before_bytes": 0, "after_bytes": 0,
              "freed_bytes": 0, "valid_links": True}
    for (bucket_name, path), source_url in sorted(refs.items()):
        item = {"bucket": bucket_name, "path": path, "url": source_url}
        try:
            bucket = sb.storage.from_(bucket_name)
            raw = bucket.download(path)
            item["before"] = len(raw); report["before_bytes"] += len(raw)
            encoded, size = optimize(raw)
            item.update(width=size[0], height=size[1], after=len(encoded))
            # Never make an object larger merely to change its encoding.
            payload = encoded if len(encoded) < len(raw) else raw
            item["after"] = len(payload)
            if args.apply and payload != raw:
                bucket.update(path, payload, {"content-type": "image/webp", "upsert": "true"})
            check = bucket.download(path) if args.apply else raw
            if args.apply and check != payload: raise RuntimeError("post-write byte verification failed")
            item["verified"] = True
            report["after_bytes"] += len(payload)
        except Exception as exc:
            item.update(verified=False, error=str(exc)); report["valid_links"] = False
        report["objects"].append(item)
    report["freed_bytes"] = report["before_bytes"] - report["after_bytes"]
    report["freed_mb"] = round(report["freed_bytes"] / 1048576, 3)
    with open(args.report, "w") as fh: json.dump(report, fh, indent=2)
    print(json.dumps({k: report[k] for k in ("mode", "referenced_objects", "freed_mb", "valid_links")}, indent=2))
    return 0 if report["valid_links"] else 2

if __name__ == "__main__": raise SystemExit(main())
