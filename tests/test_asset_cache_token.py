"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "190"
ASSETS = {
    "css/style.css": "503424cb0f3fe6d84f1a02e9fd333a6005d7a97ec2297082e9432dcbb9daee1d",
    "css/fonts.css": "6b4162eea1b25886ff8414d6bc8935643f5fca0c555c58fca89f42d2468a25b2",
    "js/store.js": "e159098651e962fa2f9823c843295ca432cde2a1d63b6117edaa19d121776324",
    "js/app.js": "e2e878263edf55e6d0f7a384e25fdaf1873802fc305cc31c3f5a081f80fb9c26",
    "js/admin.js": "9237e4e061880e417e7e7c41e289a2e4d2e4c00ee5e7e21da1e0e67d12fa6f82",
    "js/i18n.js": "ea588613ee0b2225f83be9a86364adfec811fdb1dfe215ab1068346ed49dcd83",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "e1177fec991806926b34c3ffaedb10c58425f014d0056fe04e698b279af4d06e",
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
