"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "164"
ASSETS = {
    "css/style.css": "b343a4a6c651446c0e04c9d282625234e93ca508e1225e59acf17cb0e7f73ada",
    "css/fonts.css": "ac001290b53fed1f95a3f78da84b578b0512d45cefce334ba9d4c79e12fe79a7",
    "js/store.js": "855db306634af2c957e60167318475910a0d26f1bf15a44ce005cb69ad70a61b",
    "js/app.js": "e9f784ea840f944207abd06bf263478d097abaae31a481ebe55f253eaaa4077f",
    "js/admin.js": "9a991416aa3205ececa1174e7a80edf571c15817f698152c519a19689a52f072",
    "js/i18n.js": "0c718910647618eebdadba3ff6f5d9b09dac3373e6d067d2a0f2684008f9a711",
    "js/net.js": "ef9635c7280b8f9bc54fe3a653c5822d739195b7006c3fcd5e791612ccc6250e",
    "sw.js": "3a069c2e09d259d0a150bc1e0faaf0ad910b0469e2054d38664a3736e43df26e",
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
