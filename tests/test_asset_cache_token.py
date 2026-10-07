"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "198"
ASSETS = {
    "css/style.css": "58498264e7db4a67e07fcee3bda49016ce1a82a048b6f045effcf64adc840c2f",
    "css/fonts.css": "f9c7b74744d500cd31210a3b1535fd6fb2bb94b163792346e398172ff06b45ec",
    "js/store.js": "2325380f45b3db9e6dd7125bfaefb94e7b47413a35afa01d4e5399a22299379a",
    "js/app.js": "41081896bd8852537845fee1ebe6635afd66c9cc154debf30c169559580868a8",
    "js/admin.js": "bcb206edcd611c2f52c49d39835bff5cb33ac83349f77b85ec7d8b1f7cd06db6",
    "js/i18n.js": "2590d9d64ad9293fc9de6849b08995478cb939535956d586d24de2a1d4727d9d",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "5b1166729209d0cf58bf8e9d71d164b45c7e39d2c6eaab4474df18edd01a3798",
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
