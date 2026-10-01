"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "176"
ASSETS = {
    "css/style.css": "6585ec66268028ea5dd89acd2f0f5b808215b1b62e5c422ef54a6265d37b4b52",
    "css/fonts.css": "108883887ce7869b2844e43634e05fa1c381a4fdcdf5c3027f64c18d0462ab6e",
    "js/store.js": "362eb4fd8fed5bf1348c63ae86c45ab94a5409f8e14961d2228fe4b9ef5c60e1",
    "js/app.js": "5597e1ef38441c58b95fefb03c043bf8f06d2af3bcc5bbe0c2c3b293311ff242",
    "js/admin.js": "406087be436c4f150def455361ee9bcb494061cd3a6f916e49b13e7e7fb0d61b",
    "js/i18n.js": "4e90108092b7840c9bafcbc2310da11bdd21385aadf05fa658680c4977093ced",
    "js/net.js": "ef9635c7280b8f9bc54fe3a653c5822d739195b7006c3fcd5e791612ccc6250e",
    "sw.js": "d5d78bd4a48d59fbe87046fd683f8430fcf12e526984b43e152eefd5c516b452",
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
