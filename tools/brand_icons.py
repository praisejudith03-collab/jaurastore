"""Derive legible brand icons from images/brand/logo.jpg (no new artwork).

favicon.png / apple-touch.png used to be the FULL brand board - illegible at
the 32-48px Google shows next to search results. This tool crops the centred
square around the cart + "Jaura" wordmark (no tagline, no category icons)
from the board and re-writes the three derived files:

  images/brand/favicon.png      512x512   centred mark, board background
  images/brand/apple-touch.png  512x512   same crop (opaque, iOS-ready)
  images/brand/og-cover.jpg     1200x630  the same mark on brand cream

The crop is found from the artwork itself: the board's topmost ink band is
the cart + wordmark (fused); the tagline and the category-icon row sit
further down as separate bands and are excluded. The mark is centred in a
square canvas padded with the board's own background colour, so nothing is
ever hand-positioned.

Run (from the repository root):

    python3 tools/brand_icons.py            # re-write the three files
    python3 tools/brand_icons.py --check    # print sizes only; never writes

Read-only with respect to the source: images/brand/logo.jpg is never
modified. Exit code 1 on any hard failure (unreadable source, no ink bands,
tagline fused into the mark).
"""
import os
import sys

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("Pillow is required: pip install pillow", file=sys.stderr)
    raise SystemExit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO = os.path.join(ROOT, "images", "brand", "logo.jpg")
FAVICON = os.path.join(ROOT, "images", "brand", "favicon.png")
APPLE = os.path.join(ROOT, "images", "brand", "apple-touch.png")
OG = os.path.join(ROOT, "images", "brand", "og-cover.jpg")

ICON_SIZE = (512, 512)
OG_SIZE = (1200, 630)
INK_DELTA = 30        # a pixel counts as ink when <30 lumens under the board bg
MIN_ROW_INK = 6       # rows with fewer ink pixels are noise / JPEG halo
MIN_BAND_H = 12       # ignore hairline bands
OG_MARGIN_X = 0.82    # the mark may use at most 82% of the og-cover width
OG_MARGIN_Y = 0.68    # and 68% of its height


def _ink_rows(lum, bg_lum, width):
    px = lum.load()
    out = []
    for y in range(lum.height):
        n = 0
        for x in range(0, width, 2):
            if px[x, y] < bg_lum - INK_DELTA:
                n += 1
        out.append(n)
    return out


def _bands(row_ink):
    bands, y = [], 0
    while y < len(row_ink):
        if row_ink[y] > MIN_ROW_INK:
            y0 = y
            while y < len(row_ink) and row_ink[y] > MIN_ROW_INK:
                y += 1
            if y - y0 >= MIN_BAND_H:
                bands.append((y0, y - 1))
        y += 1
    return bands


def _band_x_extent(lum, bg_lum, y0, y1):
    px = lum.load()
    x0, x1 = lum.width, -1
    for y in range(y0, y1 + 1):
        for x in range(lum.width):
            if px[x, y] < bg_lum - INK_DELTA:
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
    return x0, x1 + 1


def find_mark(logo_path):
    """Locate the cart + wordmark on the board. Returns (crop, square, info).

    ``crop`` is the tight mark (no padding); ``square`` is the mark centred
    in a square canvas padded with the board background. Raises ValueError
    when the board does not look like the brand board.
    """
    im = Image.open(logo_path).convert("RGB")
    lum = im.convert("L")
    bg_lum = lum.getpixel((8, 8))
    bands = _bands(_ink_rows(lum, bg_lum, im.width))
    if len(bands) < 2:
        raise ValueError(f"expected the mark + tagline bands, found {bands}")
    mark_y0, mark_y1 = bands[0]
    gap = bands[1][0] - mark_y1
    if gap < 2:
        raise ValueError(
            "tagline fused into the mark band (gap %d) - refusing to crop" % gap)
    x0, x1 = _band_x_extent(lum, bg_lum, mark_y0, mark_y1)
    if x1 - x0 < int(0.3 * im.width):
        raise ValueError(f"mark band too narrow ({x1 - x0}px) - not the brand board?")
    crop = im.crop((x0, mark_y0, x1, mark_y1 + 1))

    # board background is a soft cream gradient, so the padding must extend
    # it row by row (a flat colour leaves a visible seam): each canvas row
    # takes the board's own background colour from the matching source row,
    # interpolated across the side padding
    def _lerp(a, b, t):
        return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))

    W, H = im.size
    px = im.load()
    pad = max(32, int(0.055 * (x1 - x0)))
    side = (x1 - x0) + 2 * pad
    top_pad = (side - crop.height) // 2
    left_x, right_x = 8, W - 9           # outer background (always clear of ink)
    edge_l, edge_r = x0 - 2, x1 + 2      # background just outside the mark bbox
    square = Image.new("RGB", (side, side))
    spx = square.load()
    for y in range(side):
        if y < top_pad:
            src_row = mark_y0 - (top_pad - y)
        elif y < top_pad + crop.height:
            src_row = mark_y0 + (y - top_pad)
        else:
            src_row = mark_y1 + (y - top_pad - crop.height)
        src_row = max(0, min(H - 1, src_row))
        if y < top_pad or y >= top_pad + crop.height:
            # full padding row: extend the board background edge to edge
            lcol, rcol = px[left_x, src_row], px[right_x, src_row]
            for c in range(side):
                spx[c, y] = _lerp(lcol, rcol, c / (side - 1))
        else:
            # mark row: only the side padding (the crop supplies the middle)
            lcol, ecol = px[left_x, src_row], px[edge_l, src_row]
            rcol, ecr = px[right_x, src_row], px[edge_r, src_row]
            for c in range(pad):
                spx[c, y] = _lerp(lcol, ecol, c / pad)
                spx[side - 1 - c, y] = _lerp(rcol, ecr, 1.0 - c / pad)
    square.paste(crop, (pad, top_pad))
    info = {"bands": bands, "mark": (x0, mark_y0, x1, mark_y1 + 1)}
    return crop, square, info


def _sample_cream():
    """Brand cream: the current og-cover's background, else the known shade."""
    try:
        with Image.open(OG) as og:
            return og.convert("RGB").getpixel((6, 6))
    except Exception:
        return (247, 244, 239)


def _feathered_mask(size):
    """Full opacity over the mark, soft feathered edges: the board panel
    melts into the cream instead of showing a hard rectangle."""
    from PIL import ImageDraw, ImageFilter
    w, h = size
    feather = max(24, int(h * 0.09))
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    d.rectangle([feather, feather, w - 1 - feather, h - 1 - feather], fill=255)
    return mask.filter(ImageFilter.GaussianBlur(feather / 2.0))


def build_outputs(crop, square):
    icon = square.resize(ICON_SIZE, Image.LANCZOS).convert("RGB")  # opaque, no alpha
    og = Image.new("RGB", OG_SIZE, _sample_cream())
    max_w = int(OG_SIZE[0] * OG_MARGIN_X)
    max_h = int(OG_SIZE[1] * OG_MARGIN_Y)
    scale = min(max_w / crop.width, max_h / crop.height)
    mark = crop.resize((max(1, int(crop.width * scale)),
                        max(1, int(crop.height * scale))), Image.LANCZOS)
    og.paste(mark, ((OG_SIZE[0] - mark.width) // 2, (OG_SIZE[1] - mark.height) // 2),
             _feathered_mask(mark.size))
    return icon, og


def _print_sizes():
    rows = [(LOGO, "input logo.jpg"), (FAVICON, "favicon.png"),
            (APPLE, "apple-touch.png"), (OG, "og-cover.jpg")]
    for path, label in rows:
        try:
            with Image.open(path) as im:
                print(f"[brand] {label}: {os.path.getsize(path)} bytes, "
                      f"{im.size[0]}x{im.size[1]}")
        except OSError:
            print(f"[brand] {label}: MISSING ({path})")


def main(argv):
    check_only = "--check" in argv
    if check_only:
        _print_sizes()
        return 0
    try:
        crop, square, info = find_mark(LOGO)
        print(f"[brand] mark band y {info['mark'][1]}-{info['mark'][3]} "
              f"x {info['mark'][0]}-{info['mark'][2]} "
              f"({info['mark'][2] - info['mark'][0]}x{info['mark'][3] - info['mark'][1]})")
        icon, og = build_outputs(crop, square)
        icon.save(FAVICON, format="PNG", optimize=True, compress_level=9)
        icon.save(APPLE, format="PNG", optimize=True, compress_level=9)
        og.save(OG, format="JPEG", quality=88, optimize=True)
        for path in (FAVICON, APPLE, OG):
            print(f"[brand] wrote {path} ({os.path.getsize(path)} bytes)")
    except Exception as exc:
        print(f"[brand] failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
