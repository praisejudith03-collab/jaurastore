"""Re-encode original photos in place: longest edge capped at 1000px.

- Never upscale; quality 78.
- Skip thumbnail files matching \\.[0-9]+w\\.webp$.
- Never resize the five brand files whose exact dimensions the app and the
  service worker depend on (they are still re-encoded at their own size when
  that saves at least 8%).
- Replace a file only when the result is at least 8% smaller, so a second
  run changes nothing.
"""
import io
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

THUMB_RE = re.compile(r"\.[0-9]+w\.webp$", re.IGNORECASE)
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

LONGEST_EDGE = 1000
QUALITY = 78
MIN_SAVING = 0.08  # replace only when >=8% smaller

# Exact dimensions the app + service worker depend on — never resize these.
NO_RESIZE = {
    os.path.normpath("images/brand/logo.jpg"),
    os.path.normpath("images/brand/logo-flyer.jpg"),
    os.path.normpath("images/brand/og-cover.jpg"),
    os.path.normpath("images/brand/favicon.png"),
    os.path.normpath("images/brand/apple-touch.png"),
}


def _flatten_for_jpeg(im):
    if im.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        alpha = im.split()[-1]
        rgb = im.convert("RGB")
        bg.paste(rgb, mask=alpha)
        return bg
    if im.mode == "P":
        try:
            if im.info.get("transparency") is not None:
                return _flatten_for_jpeg(im.convert("RGBA"))
        except Exception:
            pass
        return im.convert("RGB")
    if im.mode != "RGB":
        try:
            return im.convert("RGB")
        except Exception:
            return im
    return im


def _encode(im, ext):
    buf = io.BytesIO()
    if ext in (".jpg", ".jpeg"):
        rgb = _flatten_for_jpeg(im)
        rgb.save(buf, format="JPEG", quality=QUALITY, optimize=True)
    elif ext == ".png":
        # Preserve mode (RGB stays RGB so opaque icons stay opaque).
        save_im = im
        if save_im.mode == "CMYK":
            save_im = save_im.convert("RGB")
        save_im.save(buf, format="PNG", optimize=True, compress_level=9)
    elif ext == ".webp":
        save_im = im
        if save_im.mode == "P":
            try:
                save_im = save_im.convert(
                    "RGBA" if save_im.info.get("transparency") is not None else "RGB"
                )
            except Exception:
                save_im = save_im.convert("RGB")
        elif save_im.mode == "CMYK":
            save_im = save_im.convert("RGB")
        save_im.save(buf, format="WEBP", quality=QUALITY, method=6)
    else:  # pragma: no cover
        return None
    return buf.getvalue()


def optimize_one(path):
    rel = os.path.normpath(os.path.relpath(path, ROOT))
    with Image.open(path) as src:
        src.load()
        # Work on a copy so the context manager can close the file.
        im = src.copy()

    may_resize = rel not in NO_RESIZE
    w, h = im.size
    longest = max(w, h)
    if may_resize and longest > LONGEST_EDGE:
        scale = LONGEST_EDGE / float(longest)
        im = im.resize(
            (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS
        )

    _base, ext = os.path.splitext(path)
    data = _encode(im, ext.lower())
    if not data:
        return False
    try:
        old_size = os.path.getsize(path)
    except OSError:
        return False
    if len(data) <= old_size * (1 - MIN_SAVING):
        with open(path, "wb") as fh:
            fh.write(data)
        return True
    return False


def main():
    optimized = skipped = 0
    for dirpath, _dirnames, filenames in os.walk(IMAGES_DIR):
        for name in sorted(filenames):
            _base, ext = os.path.splitext(name)
            if ext.lower() not in PHOTO_EXTS:
                continue
            if THUMB_RE.search(name):
                continue
            path = os.path.join(dirpath, name)
            try:
                if optimize_one(path):
                    optimized += 1
                else:
                    skipped += 1
            except Exception as exc:  # never let one bad file stop the batch
                print(f"skip {os.path.relpath(path, ROOT)}: {exc}")
                skipped += 1
    print(f"optimize: {optimized} rewritten, {skipped} skipped")


if __name__ == "__main__":
    main()
