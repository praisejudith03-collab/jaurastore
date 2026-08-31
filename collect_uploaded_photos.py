#!/usr/bin/env python3
"""Collect the owner's product photos that sit loose in the repo root.

The owner uploads photos through the GitHub web UI, which drops them into the
repository root. This script:

  1. moves every root-level ``.jpg``/``.jpeg``/``.png``/``.webp`` product photo
     into ``images/products/`` (``logo.png``, payment receipts and files whose
     name starts with ``2026`` are left alone);
  2. handles duplicates:
       - identical name + identical bytes  -> the root copy is deleted;
       - identical name + different bytes  -> the new copy is kept as
         ``images/products/alt-<name>``;
  3. rebuilds the repository data state (``js/products-data.js`` and
     ``data/catalog.json``) from the merged catalogue, so ``resolve_image`` /
     ``photo_repair_candidates`` can auto-wire the committed photos to products
     by slug/basename.

Usage:
  python3 collect_uploaded_photos.py [--no-rebuild]
"""
import os, shutil, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PRODUCT_DIR = os.path.join(ROOT, "images", "products")
EXTS = (".jpg", ".jpeg", ".png", ".webp")
EXCLUDE_NAMES = {"logo.png"}


def _is_product_image(name):
    if name.lower() in EXCLUDE_NAMES:
        return False
    if not name.lower().endswith(EXTS):
        return False
    if "receipt" in name.lower():
        return False
    if name.lower().startswith("2026"):
        return False
    return True


def _alt_path(dest):
    """``images/products/alt-<name>`` with a small counter on collision."""
    folder, name = os.path.dirname(dest), os.path.basename(dest)
    alt = os.path.join(folder, "alt-" + name)
    if not os.path.exists(alt):
        return alt
    base, dot, ext = name.rpartition(".")
    i = 2
    while os.path.exists(os.path.join(folder, f"alt-{base}-{i}.{ext if dot else ''}")):
        i += 1
    return os.path.join(folder, f"alt-{base}-{i}.{ext if dot else ''}")


def collect():
    """Move / dedupe the root photos. Returns a report dict."""
    os.makedirs(PRODUCT_DIR, exist_ok=True)
    moved, removed, alt = [], [], []
    try:
        names = sorted(os.listdir(ROOT))
    except OSError:
        names = []
    for name in names:
        src = os.path.join(ROOT, name)
        if not os.path.isfile(src) or not _is_product_image(name):
            continue
        dest = os.path.join(PRODUCT_DIR, name)
        if os.path.exists(dest):
            with open(src, "rb") as a, open(dest, "rb") as b:
                same = a.read() == b.read()
            if same:
                os.remove(src)
                removed.append(name)
            else:
                dest = _alt_path(dest)
                shutil.move(src, dest)
                alt.append(os.path.relpath(dest, ROOT))
        else:
            shutil.move(src, dest)
            moved.append(name)
    return {"moved": moved, "removed": removed, "alt": alt}


def rebuild_data_state():
    """Regenerate js/products-data.js and data/catalog.json from the catalogue."""
    try:
        import repo_sync
        ok, report = repo_sync.regenerate(commit=False, push=False)
    except Exception as exc:                      # pragma: no cover
        return False, {"error": str(exc)}
    return bool(ok), report


def main():
    no_rebuild = "--no-rebuild" in sys.argv[1:]
    report = collect()
    report["dataSync"] = None
    if not no_rebuild:
        ok, sync = rebuild_data_state()
        report["dataSync"] = sync
        report["dataSyncOk"] = ok
    print(__import__("json").dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
