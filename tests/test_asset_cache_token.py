"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "153"
ASSETS = {
    "css/style.css": "b2992c2a8fa551cde18e4c7bb6ddfc30fa0afbec777dcd98dd68e0a93744fd88",
    "css/fonts.css": "7d2f4652e3b8997bbc2787b8f2b0ebc74746d9255bb67ddfd142d02c43774a99",
    "js/store.js": "16382375f15026929c0bc010a2534723bc98ef93b2c03bc506bbfa67d36a6762",
    "js/app.js": "a1769bfab34b1fe5ec150c9d8131a17e9595874186c4a5891357b9a536efae85",
    "js/admin.js": "0de2d0212ce69a619fb1d9b16c8885d074c15714058bb4018a64fd7f98110367",
    "js/i18n.js": "46525ff501ca2aab70fef93dc6be6f40806ddbf3472b1ec74233d57b0d0f0c30",
    "js/net.js": "d5de1bfd2f068d96c91332fdeee422dfff88b641d6c19e07bf903b374510f840",
    "sw.js": "90178c4ac603678b55166cf41f9c722190ef234e88e38716f3cd255f5d7e4735",
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
