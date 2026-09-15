"""Derive the square brand logo and the standard favicon set from the AI logo.

The source artwork (``images/brand/logo.jpg``) is a 1:1 brand *board*: the
cart + "Jaura" wordmark on top, then the tagline, then a row of category
icons and a "SHOP MORE, WORRY LESS." pill. Shrunk whole to 32px that board is
an illegible smudge, which is why Google showed a grey blob instead of the
shop's logo next to its search result.

This tool crops the board to the centred cart + wordmark (``tools/brand_icons``
finds that band from the artwork itself - no hand-placed coordinates), pads it
into a 1:1 square with the board's own cream background, and writes the
standard favicon ladder from that single square master:

  images/brand/favicon-16.png     16x16
  images/brand/favicon-32.png     32x32    <link rel="icon" sizes="32x32">
  images/brand/favicon-48.png     48x48    <link rel="icon" sizes="48x48">
  images/brand/apple-touch-180.png 180x180 <link rel="apple-touch-icon">
  images/brand/icon-192.png       192x192  Android home screen / PWA
  images/brand/logo-square.png    1024x1024 the square brand logo master

Every output is opaque RGB: iOS draws no transparency behind a home-screen
icon, and Google's site-icon crawler prefers a solid background.

Run (from the repository root):

    python3 tools/favicon_assets.py            # write the files
    python3 tools/favicon_assets.py --check    # print sizes only, never writes

Read-only with respect to the source: images/brand/logo.jpg is never
modified. The files this writes are the ones tools/upload_brand_assets.py
pushes to the Supabase `public-assets` bucket.
"""
import os
import sys

try:
    from PIL import Image
except ImportError:  # pragma: no cover - the tool cannot run without Pillow
    print("Pillow is required: pip install pillow", file=sys.stderr)
    raise SystemExit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brand_icons  # noqa: E402  (same folder; supplies the mark finder)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND = os.path.join(ROOT, "images", "brand")
SOURCE = os.path.join(BRAND, "logo.jpg")

# name -> pixel size. The square master comes first so the ladder can be
# resampled from it rather than from the (much larger) padded crop.
MASTER = ("logo-square.png", 1024)
ICONS = (
    ("favicon-16.png", 16),
    ("favicon-32.png", 32),
    ("favicon-48.png", 48),
    ("apple-touch-180.png", 180),
    ("icon-192.png", 192),
)


def build_square(source=SOURCE):
    """The 1:1, centre-framed brand mark as an opaque RGB image.

    Raises ValueError when the source does not look like the brand board.
    """
    _crop, square, _info = brand_icons.find_mark(source)
    return square.convert("RGB")


# Anything the shop ships stays under this, the repo's standing limit for a
# brand file (tests/test_api.py enforces it for the icons it knows about).
MAX_BYTES = 200 * 1024


def _save(img, path):
    img.save(path, format="PNG", optimize=True, compress_level=9)
    if os.path.getsize(path) > MAX_BYTES:
        # The artwork is a flat cream/gold/black mark, so a 256-colour palette
        # is visually identical at any size and roughly halves the file. Only
        # the 1024px master ever needs it; the icons are already tiny.
        img.quantize(colors=256, method=Image.MEDIANCUT).save(
            path, format="PNG", optimize=True, compress_level=9)
    return os.path.getsize(path)


def write_assets(source=SOURCE, out_dir=BRAND):
    """Write the square master + the favicon ladder. Returns {path: (w, h)}."""
    os.makedirs(out_dir, exist_ok=True)
    square = build_square(source)
    written = {}

    master_name, master_px = MASTER
    master = square.resize((master_px, master_px), Image.LANCZOS)
    master_path = os.path.join(out_dir, master_name)
    _save(master, master_path)
    written[master_path] = master.size

    for name, px in ICONS:
        icon = master.resize((px, px), Image.LANCZOS).convert("RGB")
        path = os.path.join(out_dir, name)
        _save(icon, path)
        written[path] = icon.size
    return written


def _check(out_dir=BRAND):
    rows = [MASTER] + list(ICONS)
    missing = 0
    for name, px in rows:
        path = os.path.join(out_dir, name)
        try:
            with Image.open(path) as im:
                ok = im.size == (px, px)
                print(f"[favicon] {name}: {im.size[0]}x{im.size[1]}, "
                      f"{os.path.getsize(path)} bytes{'' if ok else '  <-- WRONG SIZE'}")
                if not ok:
                    missing += 1
        except OSError:
            print(f"[favicon] {name}: MISSING ({path})")
            missing += 1
    return 1 if missing else 0


def main(argv):
    if "--check" in argv:
        return _check()
    try:
        written = write_assets()
    except Exception as exc:
        print(f"[favicon] failed: {exc}", file=sys.stderr)
        return 1
    for path, size in sorted(written.items()):
        print(f"[favicon] wrote {os.path.relpath(path, ROOT)} "
              f"({size[0]}x{size[1]}, {os.path.getsize(path)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
