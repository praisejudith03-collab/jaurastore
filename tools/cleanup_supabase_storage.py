#!/usr/bin/env python3
"""Audit and safely remove orphaned Supabase Storage objects.

Dry-run is the default. Apply requires BOTH --apply and --confirm-project with
exact project ref. Objects referenced by any scanned row are never removed.
Use the JSON report as the review/audit record.
"""
import argparse, hashlib, json, os, re, sys
from urllib.parse import unquote, urlparse

from supabase import create_client

TABLES = ("products", "featured_products", "categories", "hero_banners",
          "site_settings", "orders", "receipts")
MEDIA_EXT = {"jpg","jpeg","png","webp","gif","avif","heic","mp4","webm","mov","pdf","doc","docx"}
VIDEO_EXT = {"mp4","webm","mov"}
URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values(): yield from strings(item)
    elif isinstance(value, list):
        for item in value: yield from strings(item)


def storage_ref(value, project_url):
    if not isinstance(value, str): return None
    marker = "/storage/v1/object/"
    if marker not in value: return None
    tail = unquote(urlparse(value).path.split(marker, 1)[1])
    for prefix in ("public/", "sign/", "authenticated/"):
        if tail.startswith(prefix): tail = tail[len(prefix):]
    bits = tail.split("/", 1)
    return tuple(bits) if len(bits) == 2 else None


def list_objects(bucket, prefix=""):
    out = []
    offset = 0
    while True:
        rows = bucket.list(prefix, {"limit": 1000, "offset": offset, "sortBy": {"column":"name","order":"asc"}}) or []
        if not rows: break
        for row in rows:
            name = row.get("name", "")
            path = f"{prefix}/{name}".strip("/")
            # Supabase folders have no object id/metadata.
            if row.get("id") is None and not row.get("metadata"):
                out.extend(list_objects(bucket, path))
            else: out.append({**row, "path": path})
        if len(rows) < 1000: break
        offset += len(rows)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm-project", default="")
    ap.add_argument("--report", default="storage-cleanup-report.json")
    args = ap.parse_args()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key: sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    project = urlparse(url).hostname.split(".")[0]
    if args.apply and args.confirm_project != project:
        sys.exit(f"Refusing apply: pass --confirm-project {project}")
    sb = create_client(url, key)
    refs, table_results = set(), {}
    for table in TABLES:
        try:
            rows = sb.table(table).select("*").execute().data or []
            table_results[table] = len(rows)
            for row in rows:
                for text in strings(row):
                    candidates = [text] + URL_RE.findall(text)
                    for candidate in candidates:
                        ref = storage_ref(candidate, url)
                        if ref: refs.add(ref)
        except Exception as exc:
            if "PGRST205" in str(exc) or "Could not find the table" in str(exc):
                table_results[table] = "not present"
                continue
            # Safety invariant: an unreadable reference table aborts, rather
            # than turning all its active assets into apparent orphans.
            sys.exit(f"ABORT: could not scan reference table {table}: {exc}")
    buckets = sb.storage.list_buckets() or []
    report = {"project": project, "mode": "apply" if args.apply else "dry-run",
              "tables": table_results, "referenced": len(refs), "buckets": {}, "deleted": []}
    for item in buckets:
        name = item.name if hasattr(item, "name") else item.get("name")
        bucket = sb.storage.from_(name)
        objects = list_objects(bucket)
        orphaned, broken, hashes = [], [], {}
        for obj in objects:
            path = obj["path"]
            if (name, path) in refs: continue
            ext = path.rsplit(".", 1)[-1].lower()
            # Never classify unknown extension/system files as disposable.
            if ext not in MEDIA_EXT: continue
            orphaned.append(path)
            try:
                data = bucket.download(path)
                digest = hashlib.sha256(data).hexdigest()
                hashes.setdefault(digest, []).append(path)
                if ext in VIDEO_EXT and not (data[4:8] == b"ftyp" or data[:4] == b"\x1aE\xdf\xa3"):
                    broken.append(path)
            except Exception:
                if ext in VIDEO_EXT: broken.append(path)
        duplicates = [v for v in hashes.values() if len(v) > 1]
        candidates = sorted(set(orphaned) | set(broken))
        report["buckets"][name] = {"objects": len(objects), "orphaned": orphaned,
                                    "broken_videos": broken, "duplicate_groups": duplicates,
                                    "delete_candidates": candidates}
        if args.apply:
            for start in range(0, len(candidates), 100):
                batch = candidates[start:start+100]
                if batch: bucket.remove(batch); report["deleted"].extend(f"{name}/{p}" for p in batch)
    with open(args.report, "w", encoding="utf-8") as fh: json.dump(report, fh, indent=2)
    print(json.dumps({"mode": report["mode"], "referenced": len(refs),
                      "candidates": sum(len(x["delete_candidates"]) for x in report["buckets"].values()),
                      "deleted": len(report["deleted"]), "report": args.report}, indent=2))

if __name__ == "__main__": main()
