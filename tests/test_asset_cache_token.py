"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "179"
ASSETS = {
    "css/style.css": "6585ec66268028ea5dd89acd2f0f5b808215b1b62e5c422ef54a6265d37b4b52",
    "css/fonts.css": "258d2cb606d145d616036c61c569554b0a9e3634b2ee287411c0fcb0b2895f99",
    "js/store.js": "ebc6cfaf4d028ede1b1105fd4ddd00996595348d0515a0111763334926a09b3d",
    "js/app.js": "99314f471e1cc8305c7748abc029999b458693c78d37e9ef323efaab8cbbe997",
    "js/admin.js": "285ae150f656a22bbccfe2686df72c1eac2b88f79bbfa646a2fde9f4e7c40499",
    "js/i18n.js": "4e90108092b7840c9bafcbc2310da11bdd21385aadf05fa658680c4977093ced",
    "js/net.js": "fb5f99efc36c80f0061342a4c405fa0b443e12308c59b020ce14e799707c6301",
    "sw.js": "74c446dd91d1b5191122ecc43d9a6374875ae721b3ca6df3271c055cac237046",
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
