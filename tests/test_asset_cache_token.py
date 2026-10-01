"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "173"
ASSETS = {
    "css/style.css": "6585ec66268028ea5dd89acd2f0f5b808215b1b62e5c422ef54a6265d37b4b52",
    "css/fonts.css": "4938474e324b2357a3a44f59e320d27b7221276a64be61ea7d58e848c8c5a55e",
    "js/store.js": "45721408785131d54108ca93c4f11ba161075e791e2c4c6d3a5b6550724a0962",
    "js/app.js": "0aafd4d877d5b5f573e8ba60565bf9145eca06ae251757cb046fe69d5b4b6ec2",
    "js/admin.js": "6326472c2fc4fec5289341ae244e02fbc21d1ea9aaf4d9fd6052bf6f23bba60e",
    "js/i18n.js": "4e90108092b7840c9bafcbc2310da11bdd21385aadf05fa658680c4977093ced",
    "js/net.js": "ef9635c7280b8f9bc54fe3a653c5822d739195b7006c3fcd5e791612ccc6250e",
    "sw.js": "b184fb9474124753d82903e752c080effad5a3704e73d7969d0490a491053ba5",
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
