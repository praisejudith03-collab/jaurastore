"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "203"
ASSETS = {
    "css/style.css": "8a64cd3d33cd8693e8590850f367aadd1cc5761f50695f3e3fffa379ff09af94",
    "css/fonts.css": "1fde049830a6dc3756222596ce220257e84fadee9c9742ac4623a1cc1cff0167",
    "js/store.js": "111a3c084d28dd4205ff08571d6b36112df536581287b18675f2fb261382672e",
    "js/app.js": "a8c08c803ec6377cc0ab8181e43bac7bcbaa630252facd412e3dcdc83abbcd95",
    "js/admin.js": "87dc0df97dc3c413f0e3569e838e25d0a8f44eb0504112fadccd8f587b67c643",
    "js/i18n.js": "082065a70f0350a6ab6fd1a07d3b66666b68b333947b344c49946cecf5edee06",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "7b0923a4f82ea1f5f4bf0d5e399b9d25f132ebbf571b8ca87dca9c38d5a3e62b",
}

def _refs(text):
    return re.findall(r"[?&]v=(\d+)", text)

def test_html_refs_use_token():
    for page in ROOT.glob("*.html"):
        assert set(_refs(page.read_text())) <= {TOKEN}, page.name

def test_worker_version_and_core_use_token():
    text = (ROOT / "sw.js").read_text()
    assert f'const VERSION = "jaura-v{TOKEN}";' in text
    assert set(_refs(text)) <= {TOKEN}

def test_shared_sources_use_token():
    for name in ("js/store.js", "js/app.js", "js/admin.js", "css/fonts.css"):
        assert set(_refs((ROOT / name).read_text())) <= {TOKEN}, name

def test_favicon_sync_token_matches():
    text = (ROOT / "tools/sync_favicon_head.py").read_text()
    assert re.search(r'^TOKEN = "(\d+)"$', text, re.M).group(1) == TOKEN

def test_shared_asset_fingerprints():
    for name, expected in ASSETS.items():
        actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        assert actual == expected, f"{name} changed: bump the token everywhere and refresh the hashes"

def _bump():
    path = Path(__file__)
    text = path.read_text()
    for name in ASSETS:
        digest = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        text = re.sub(rf'("{re.escape(name)}": )"[0-9a-f]+"', rf'\1"{digest}"', text)
    path.write_text(text)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bump", action="store_true")
    args = parser.parse_args()
    if args.bump:
        _bump()
    else:
        parser.error("use --bump to refresh fingerprints")
