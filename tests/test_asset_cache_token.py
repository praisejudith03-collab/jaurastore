"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "166"
ASSETS = {
    "css/style.css": "a20ca1e963f7229ffeb5774a0699405431be8638d92f941ecfbf4dda7e81e06a",
    "css/fonts.css": "36ccc090db802d9291cabf494d7ba94eceeeae1ae6261bda2eae891028222d32",
    "js/store.js": "14d70c052ab599ba3ab15fbcf583a31968782fbab8c6de06132313cb0f5eb02d",
    "js/app.js": "76155291c35b3a3a656814337fb57c5c5aedbae1b42e2a02360f47aecec69417",
    "js/admin.js": "2f0f9eb9e09c40cd5fa74cb37f33a60424bfad020fb702ae6aac1b35203f8db8",
    "js/i18n.js": "0c718910647618eebdadba3ff6f5d9b09dac3373e6d067d2a0f2684008f9a711",
    "js/net.js": "ef9635c7280b8f9bc54fe3a653c5822d739195b7006c3fcd5e791612ccc6250e",
    "sw.js": "6537004c6dda89c8a590f256d454b83d37ca45a55e25bbb0f5d4dc0bf34eb3f3",
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
