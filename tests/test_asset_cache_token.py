"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "170"
ASSETS = {
    "css/style.css": "a20ca1e963f7229ffeb5774a0699405431be8638d92f941ecfbf4dda7e81e06a",
    "css/fonts.css": "2aaedb7c21e257a12264764adde0a47035d34ac16907fb83314fed7f2c0d65ca",
    "js/store.js": "7791e1a54b1db0982c82c2dca56aab8ed17a3337e05f254d6b591235427dd140",
    "js/app.js": "38974eee70fb61f09b99ad10f6c3b875106f043e4099526d9088001ea7a65d58",
    "js/admin.js": "1458b3915462f71353deebdcb332ba7bc59a55bbb59edb3bf49e7c25b503d3ba",
    "js/i18n.js": "4e90108092b7840c9bafcbc2310da11bdd21385aadf05fa658680c4977093ced",
    "js/net.js": "ef9635c7280b8f9bc54fe3a653c5822d739195b7006c3fcd5e791612ccc6250e",
    "sw.js": "2f94d99ab64150a98f70e61a23058bdb725256d7a63ed515a63ec119c0f3528f",
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
