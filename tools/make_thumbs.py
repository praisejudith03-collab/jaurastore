"""Generate lightweight WebP thumbnails for every product photo.

Walk images/; for every photo (jpg/jpeg/png/webp) not already a thumbnail,
write a sibling <name>.400w.webp at longest edge 400px, WebP quality ~78,
never upscaling. Skip when the thumbnail exists and is newer than the source
so re-runs are cheap and idempotent.
"""
import os
import re
import sys

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("Pillow is required: pip install pillow", file=sys.stderr)
    raise SystemExit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES_DIR = os.path.join(ROOT, "images")

THUMB_SUFFIX = ".400w.webp"
THUMB_RE = re.compile(r"\.\d+w\.webp$", re.IGNORECASE)
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

LONGEST_EDGE = 400
QUALITY = 78


def thumb_path_for(src_path):
    base, _ext = os.path.splitext(src_path)
    return base + THUMB_SUFFIX


def is_thumb_name(filename):
    return bool(THUMB_RE.search(filename))


def make_thumb(src_path, dst_path):
    with Image.open(src_path) as im:
        # Flatten palette / CMYK onto RGB; keep alpha for RGBA/LA/P-with-transparency
        if im.mode == "P":
            try:
                transparency = im.info.get("transparency")
            except Exception:
                transparency = None
            im = im.convert("RGBA" if transparency is not None else "RGB")
        elif im.mode == "CMYK":
            im = im.convert("RGB")
        elif im.mode not in ("RGB", "RGBA", "L", "LA"):
            try:
                im = im.convert("RGB")
            except Exception:
                pass

        w, h = im.size
        longest = max(w, h)
        if longest > LONGEST_EDGE:
            scale = LONGEST_EDGE / float(longest)
            new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
            im = im.resize(new_size, Image.LANCZOS)

        save_kwargs = {"format": "WEBP", "quality": QUALITY, "method": 6}
        im.save(dst_path, **save_kwargs)


def main():
    made = skipped = 0
    for dirpath, _dirnames, filenames in os.walk(IMAGES_DIR):
        for name in sorted(filenames):
            _base, ext = os.path.splitext(name)
            if ext.lower() not in PHOTO_EXTS:
                continue
            if is_thumb_name(name):
                continue
            src = os.path.join(dirpath, name)
            dst = thumb_path_for(src)
            try:
                if os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
                    skipped += 1
                    continue
                make_thumb(src, dst)
                made += 1
            except Exception as exc:  # never let one bad file stop the batch
                print(f"skip {os.path.relpath(src, ROOT)}: {exc}")
                skipped += 1
    print(f"thumbs: {made} written, {skipped} skipped")


if __name__ == "__main__":
    main()
