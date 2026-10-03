"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "185"
ASSETS = {
    "css/style.css": "fe19e1c8589d3b8f3d19aafb8d6332dfd8f90dfa305308aa8fed71cd12d02c22",
    "css/fonts.css": "f1b6ecc06de9eb03708bb95d5159edcb9d2ec9b5bdc2473953e00313e61f4e7f",
    "js/store.js": "3d04c38905eba4d47f831014101ac0971c000a371e28e2a2b2d7c1397568629d",
    "js/app.js": "13b86b525f50baba7c700d0d2b9a6e29474f53c7537b81d9abd0ac1831651635",
    "js/admin.js": "99d788c25b942d19415d24dea444d23d1474181e0f5ddfc7067ace6f4d473590",
    "js/i18n.js": "ea588613ee0b2225f83be9a86364adfec811fdb1dfe215ab1068346ed49dcd83",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "1d44c0e88285b39d37dfd2bff19a382fe413cad300158857a9493560613b49e8",
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
