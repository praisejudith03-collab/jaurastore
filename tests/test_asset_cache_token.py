"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "165"
ASSETS = {
    "css/style.css": "e05a1b3023a8a97709e7b65606ee3b23e1bcd1e6a9bb0dd9f3a10a56707dd59f",
    "css/fonts.css": "628aa799bb2f4ebdbe1ad348a7af4c6d9a52df5287e7bedea6c66cb40d390174",
    "js/store.js": "ce33d5ce892183970c38db012bacab44e3eec4b7872942e1ba90ac4e3e5c4283",
    "js/app.js": "a4c3a23248ff4d60dd2c975d8f3c28509edc10b47ece0c7ccd5a5e9d07f4ef5f",
    "js/admin.js": "247528f45eb9c6ffc8617f1e9910a7f0d430cfec8eb3658e6e6d12485f26a021",
    "js/i18n.js": "0c718910647618eebdadba3ff6f5d9b09dac3373e6d067d2a0f2684008f9a711",
    "js/net.js": "ef9635c7280b8f9bc54fe3a653c5822d739195b7006c3fcd5e791612ccc6250e",
    "sw.js": "213fb2a07c1a26166950637a58bc038776d3d6bdb9d74a028721e30908ef069f",
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
