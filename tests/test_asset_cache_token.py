"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "204"
ASSETS = {
    "css/style.css": "49b9c8888b8ab90beff4a9d3b21574e734788839e6190051007f77566561adae",
    "css/fonts.css": "aa12fb745854bd78400a1011322496c2dc79bf9f3c87a3af44080d02ff30de56",
    "js/store.js": "4d8ba8c387de202cafecc0cdadbc734363ac732ea9f4f9b85fff7ed49f63272e",
    "js/app.js": "9e30c0eca16af56b4765c7dd6232dc301813b01841c64e444bc8809a5c61a328",
    "js/admin.js": "348fea52cfe5ca98d30466a549b462548fa32a79506655302f70045af4cd44b9",
    "js/i18n.js": "082065a70f0350a6ab6fd1a07d3b66666b68b333947b344c49946cecf5edee06",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "6c41c2df3f866de9ea32aa570e1b52320c8f7281401fa0e8a40840e4e83d3d05",
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
