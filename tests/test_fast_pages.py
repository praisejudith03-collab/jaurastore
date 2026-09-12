"""Guard the fast-pages / image-optimisation release.

Two halves, both aimed at a 4G phone:

  * upload-time optimisation (storage.optimize_image_bytes): a multi-megabyte
    phone original is downscaled to 1600px and re-encoded before it is stored,
    so the shop never serves a 6 MB photo. Proofs (payment evidence) are never
    touched. Transparency becomes WebP; opaque photos become progressive JPEG.
  * page speed hints: the bucket preconnect, the banner preload, and
    loading="lazy" on the card renderers.

The bucket re-encoder (tools/optimize_bucket_images.py) is guarded by source
inspection: it must stay a dry-run-by-default, --apply-gated tool that never
touches the proofs folder.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import storage  # noqa: E402
from config import Config  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PHOTO_PAGES = ("index.html", "shop.html", "product.html", "categories.html",
               "wishlist.html", "cart.html", "checkout.html", "account.html")
BUCKET_HOST = "rvkweyipqgsggcnimhxf.supabase.co"


# --------------------------------------------------------------- image builders
def _big_jpeg():
    """A 3240x4320 random RGB JPEG, far over the 300KB optimisation floor."""
    from PIL import Image
    img = Image.frombytes("RGB", (3240, 4320), os.urandom(3240 * 4320 * 3))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _uploadable_jpeg():
    """A >300KB JPEG that stays under MAX_BYTES (6MB) for save_image()."""
    from PIL import Image
    img = Image.frombytes("RGB", (2400, 1800), os.urandom(2400 * 1800 * 3))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def _noisy_rgba_png():
    """A genuinely noisy RGBA image padded past the 300KB floor.

    The pixel content stays small (a 128x128 random RGBA block) so the WebP
    re-encode is quick; the trailing padding only pushes the byte length over
    OPTIMIZE_MIN_BYTES, which is what arms the optimiser. The alpha channel is
    genuinely random, so the transparency branch is exercised for real.
    """
    from PIL import Image
    img = Image.frombytes("RGBA", (128, 128), os.urandom(128 * 128 * 4))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue() + b"\x00" * (320 * 1024)


# ------------------------------------------------------------------ the tests
def test_large_jpeg_is_downscaled_and_reencoded_smaller():
    data = _big_jpeg()
    assert len(data) > storage.OPTIMIZE_MIN_BYTES
    out, ext = storage.optimize_image_bytes(data, "jpg")
    from PIL import Image
    img = Image.open(io.BytesIO(out))
    assert ext == "jpg"
    assert img.format == "JPEG"
    assert max(img.size) <= storage.OPTIMIZE_MAX_DIMENSION
    assert len(out) < len(data)


def test_small_jpeg_gif_and_garbage_stay_byte_identical():
    from PIL import Image

    small = io.BytesIO()
    Image.new("RGB", (120, 80), (200, 60, 90)).save(small, "JPEG")
    small_jpeg = small.getvalue()
    assert len(small_jpeg) < storage.OPTIMIZE_MIN_BYTES
    out, ext = storage.optimize_image_bytes(small_jpeg, "jpg")
    assert out == small_jpeg and ext == "jpg"

    # A GIF (animation container) over the size floor still passes through
    # untouched - the gif guard fires before any decode.
    big_gif = io.BytesIO()
    Image.frombytes("P", (1000, 1000), os.urandom(1000 * 1000)).save(
        big_gif, "GIF")
    gif_bytes = big_gif.getvalue()
    assert len(gif_bytes) > storage.OPTIMIZE_MIN_BYTES
    out, ext = storage.optimize_image_bytes(gif_bytes, "gif")
    assert out == gif_bytes and ext == "gif"

    garbage = b"\x00" * (400 * 1024)          # over the floor, not an image
    out, ext = storage.optimize_image_bytes(garbage, "jpg")
    assert out == garbage and ext == "jpg"


def test_noisy_rgba_png_reencodes_to_webp_rgba():
    data = _noisy_rgba_png()
    assert len(data) > storage.OPTIMIZE_MIN_BYTES
    out, ext = storage.optimize_image_bytes(data, "png")
    from PIL import Image
    img = Image.open(io.BytesIO(out))
    assert ext == "webp"
    assert img.format == "WEBP"
    assert img.mode == "RGBA"
    assert max(img.size) <= storage.OPTIMIZE_MAX_DIMENSION
    assert len(out) < len(data)


def test_save_image_optimizes_products_but_never_proofs(monkeypatch, tmp_path):
    from PIL import Image
    raw = _uploadable_jpeg()

    monkeypatch.setattr(Config, "UPLOAD_MODE", "local")
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(tmp_path))

    ok, _msg, url = storage.save_image(raw, "products", filename="x.jpg")
    assert ok
    key = url[len("/uploads/"):]
    stored = open(os.path.join(str(tmp_path), key), "rb").read()
    assert stored != raw, "product photos must be optimised at upload time"
    img = Image.open(io.BytesIO(stored))
    assert max(img.size) <= storage.OPTIMIZE_MAX_DIMENSION

    ok, _msg, url = storage.save_image(raw, "proofs", filename="x.jpg")
    assert ok
    key = url[len("/uploads/"):]
    stored = open(os.path.join(str(tmp_path), key), "rb").read()
    assert stored == raw, "payment proofs must be stored byte-for-byte"


def test_card_renderers_lazy_load():
    app_js = open(os.path.join(ROOT, "js", "app.js"), encoding="utf-8").read()
    store_js = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    assert 'loading="lazy"' in store_js
    assert 'loading="lazy"' in app_js


def test_photo_pages_preconnect_bucket_and_shop_preloads_banner():
    for name in PHOTO_PAGES:
        html = open(os.path.join(ROOT, name), encoding="utf-8").read()
        assert BUCKET_HOST in html, f"{name} missing the bucket preconnect"
        assert 'rel="preconnect"' in html, f"{name} preconnect link malformed"
    shop = open(os.path.join(ROOT, "shop.html"), encoding="utf-8").read()
    assert '<link rel="preload" as="image" href="images/banners/wordmark-bg.jpg" fetchpriority="high" />' in shop
    assert 'fetchpriority="high" decoding="async"' in shop


def test_requirements_pin_pillow_for_production():
    req = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read()
    assert "pillow" in req.lower()
    test_req = open(os.path.join(ROOT, "requirements-test.txt"),
                    encoding="utf-8").read()
    assert "pillow" not in test_req.lower()


def test_bucket_optimizer_is_dry_run_by_default_and_proofs_safe():
    src = open(os.path.join(ROOT, "tools", "optimize_bucket_images.py"),
               encoding="utf-8").read()
    assert "_COMPAT_EXT" in src
    assert "SENSITIVE_FOLDERS" in src
    # dry run by default: --apply is opt-in and the write is gated on it
    assert '--apply"' in src or "--apply'" in src or 'parser.add_argument("--apply"' in src
    assert "store_true" in src
    assert "if args.apply:" in src
    assert "SENSITIVE_FOLDERS" in src
