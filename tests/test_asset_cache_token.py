"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "177"
ASSETS = {
    "css/style.css": "6585ec66268028ea5dd89acd2f0f5b808215b1b62e5c422ef54a6265d37b4b52",
    "css/fonts.css": "161f557ede96625567321c7f772db29e0c2daf71b9d4b64d09b186f414b65a90",
    "js/store.js": "8f6a91be83e0d215962bc245ae55d2ca2bb1b5ad8a14e0e1a48f06e7cc4bf6d4",
    "js/app.js": "355af571f11b7d1036d9aeeae55f0d5d92e573e7f5ddb365932b6688f8550630",
    "js/admin.js": "bc1b388993ad97f44c81ac6fed673ec09c1d7b95af6a72069c9b03136bb766c0",
    "js/i18n.js": "4e90108092b7840c9bafcbc2310da11bdd21385aadf05fa658680c4977093ced",
    "js/net.js": "ef9635c7280b8f9bc54fe3a653c5822d739195b7006c3fcd5e791612ccc6250e",
    "sw.js": "2bfbd5204ad3ebab9384cdc51ac174d7ec1923a353168157d312175104fa9c48",
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
