"""Product photos are resized and compressed IN THE BROWSER before upload.

Owner request (2026-09-27). A photo picked from the phone gallery is a 3-6 MB,
4000px camera original; the shop paints it a few hundred pixels wide. Sending
the original over mobile data is slow, times out on a weak signal, and fills
Supabase Storage with pixels nobody sees - and the admin's 6 MB gate used to
REJECT those photos outright ("That photo is 7.4 MB. The limit is 6 MB.")
before anything had a chance to shrink them.

js/admin.js now squeezes every picked picture first:

  * longest side capped at 1200px (never upscaled),
  * re-encoded to WebP where the canvas can encode it, JPEG otherwise,
  * EXIF orientation applied so portraits are not stored sideways,
  * the original kept whenever the squeeze cannot help (GIF, SVG, video,
    undecodable file, or a re-encode that is not actually smaller),
  * the 6 MB ceiling checked on what will really be uploaded.

The behaviour is proved by tests/_image_compression_sim.mjs, which boots the
real js/admin.js against a stubbed canvas; the wiring is pinned below so a
later edit cannot quietly send camera originals again. The server-side
storage.optimize_image_bytes() pass is unchanged and still runs.

Run with:  python3 -m pytest tests/test_image_compression.py -q
"""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(ROOT, "tests", "_image_compression_sim.mjs")


def _admin_js():
    with open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8") as fh:
        return fh.read()


def test_image_compression_simulation_passes():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (frontend sim needs Node >= 18)")
    proc = subprocess.run([node, SIM], cwd=ROOT, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, (
        "image compression simulation failed:\n" + proc.stdout + proc.stderr)
    assert "all image-compression checks passed" in proc.stdout


# ------------------------------------------------------------ the settings

def test_the_compression_budget_is_the_requested_one():
    src = _admin_js()
    assert re.search(r"const PHOTO_MAX_DIMENSION = 1200\b", src), (
        "the optimal max width the owner asked for is 1200px")
    quality = re.search(r"const PHOTO_QUALITY = ([\d.]+)", src)
    assert quality and 0.7 <= float(quality.group(1)) <= 0.9, (
        "quality has to shrink the file without visibly hurting the photo")
    assert "function compressImageFile(" in src, (
        "the shared browser-side compressor is missing")
    assert "function canEncodeWebp(" in src, (
        "WebP is the preferred output; the encoder has to be feature-detected")
    body = src.split("async function compressImageFile(", 1)[1].split("\n}", 1)[0]
    assert 'canEncodeWebp() ? "image/webp" : "image/jpeg"' in body, (
        "WebP when the browser can encode it, JPEG as the fallback")
    assert "Math.min(1, max / Math.max(w, h))" in src, (
        "the longest side drives the scale, and a photo is never upscaled")


def test_orientation_and_safety_rails():
    src = _admin_js()
    assert 'imageOrientation: "from-image"' in src, (
        "EXIF rotation must be applied or portrait photos upload sideways")
    body = src.split("async function compressImageFile(", 1)[1].split("\n}", 1)[0]
    assert "blob.size >= out.originalSize" in body, (
        "a re-encode that does not shrink must keep the original bytes")
    assert "isCompressibleImage(file)" in body, (
        "videos, GIFs, SVGs and documents must pass through untouched")
    keep = re.search(r"const PHOTO_KEEP_AS_IS = /(.+?)/i", src)
    assert keep and "gif" in keep.group(1) and "svg" in keep.group(1), (
        "animations and vectors must never be redrawn on a canvas")
    assert "canvasToBlob" in src and "toDataURL" in src, (
        "keep the data-URL fallback for engines without canvas.toBlob")


# --------------------------------------------------------- the call sites

def test_the_product_upload_compresses_before_it_measures_or_sends():
    src = _admin_js()
    upload = src.split("async function uploadProductImage(", 1)[1]
    upload = upload.split("\n  box.addEventListener(\"click\"", 1)[0]
    squeeze = upload.index("compressImageFile(file)")
    ceiling = upload.index("payload.size > maxPhoto")
    send = upload.index("window.JA_NET.api(endpoint")
    assert squeeze < ceiling < send, (
        "the photo must be compressed BEFORE the 6 MB gate and before the "
        "upload, so a camera original is shrunk instead of rejected")
    assert "blob: payload" in upload and "filename," in upload, (
        "the compressed bytes AND the new .webp/.jpg name must be uploaded")
    assert "PHOTO_DECODE_MAX_BYTES" in upload, (
        "only a file too large to even decode is turned away up front")
    assert "readableBytes(squeezed.originalSize)" in upload, (
        "tell the owner how much was saved")


def test_the_other_owner_uploads_are_squeezed_too():
    src = _admin_js()
    category = src.split('list.addEventListener("change"', 1)[1][:2000]
    assert "compressImageFile(f)" in category, (
        "category covers go through the same squeeze")
    assert "squeezed.blob || f" in category and "squeezed.filename" in category

    branding = src.split("function bindSiteBranding(", 1)[1]
    assert "compressImageFile(f, { maxSize: 1200 })" in branding, (
        "the logo and the shop banner are squeezed as well")
    assert branding.count("compressImageFile(") >= 2
    assert "blob: logoImg.blob || f" in branding
    assert "blob: bannerImg.blob || f" in branding


def test_the_legacy_helpers_still_exist_for_their_callers():
    src = _admin_js()
    assert "function fileToBlob(" in src and "function fileToData(" in src, (
        "older call sites (offline previews) still use these wrappers")
    data = src.split("function fileToData(", 1)[1][:800]
    assert "compressImageFile(" in data, (
        "the data-URL helper must share the same squeeze")


def test_the_owner_is_told_what_happens_to_the_photos():
    src = _admin_js()
    assert "resized to 1200px and compressed on your phone" in src, (
        "the media hint must describe the new behaviour, not the old "
        '"your photos stay as they are"')
    assert "stay as they are" not in src
