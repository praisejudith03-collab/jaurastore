"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "168"
ASSETS = {
    "css/style.css": "a20ca1e963f7229ffeb5774a0699405431be8638d92f941ecfbf4dda7e81e06a",
    "css/fonts.css": "7ffeff8413ff9955831bfd67a399a0b276e6842b48645398759b5cce014d4619",
    "js/store.js": "4b40cad5edaa3853af7c9051d903e4ab3980c21e350a7f2617980e62da5721a7",
    "js/app.js": "f4607dc69a04a29860d05663f2e83b5124827b53da1b912f799bfaccf476c255",
    "js/admin.js": "a874cdbb04d95b2963c8828ce07961c7556b6111636946e2acad0cca9c4b86f6",
    "js/i18n.js": "b2b045d6824749cda9bf28a57e89be4b7c12be9fdb697d90162eddb43b20f746",
    "js/net.js": "ef9635c7280b8f9bc54fe3a653c5822d739195b7006c3fcd5e791612ccc6250e",
    "sw.js": "aa1bccaaa5337afea654b1176b93eb4e30930467bfa93f55d64e80cb0a165bbc",
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
