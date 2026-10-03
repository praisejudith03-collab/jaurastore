"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "186"
ASSETS = {
    "css/style.css": "fe19e1c8589d3b8f3d19aafb8d6332dfd8f90dfa305308aa8fed71cd12d02c22",
    "css/fonts.css": "e1838d41493be03ee6ab89940855cc1a1a4926385a69749652a42db8b188613c",
    "js/store.js": "4bd1b6127dd0512a8f2b0190899f20694af8d87d1d8c8ebb046c321d0ab9c070",
    "js/app.js": "9838462b4c44b2374a07351bd64c0b6dd850ffe833330023df039b174090c976",
    "js/admin.js": "854ce7d1a21940665948cc961460a366f9456fffcc6739d775b6ef979754309b",
    "js/i18n.js": "ea588613ee0b2225f83be9a86364adfec811fdb1dfe215ab1068346ed49dcd83",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "23bc0dec02ba1ae183e9b6136c7c924284a4f8df6c8c3f5e76b3fc2f4a4adfb8",
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
