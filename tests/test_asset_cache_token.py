"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "202"
ASSETS = {
    "css/style.css": "794ff5b9c7d8172441ad1cbac47c8d3b87f8dc5674cf7905304451eabcaa3499",
    "css/fonts.css": "d7b7dab36e5935fccc2bdca23803bdad5d7666d500d32375fb63ace78a0ed11c",
    "js/store.js": "8aa1d56ab58fb32b7d3c87a35cc516d8cd2432e069c317b9f7e67a63d1849879",
    "js/app.js": "d5c2cfb3b7e561f415e12e8c5dfe0d77e9bc9fda994461862b76636285569b5c",
    "js/admin.js": "b65f22ae611c90ce6cb58a66391a563cf5ab710a323f31b70de071868e0c8717",
    "js/i18n.js": "082065a70f0350a6ab6fd1a07d3b66666b68b333947b344c49946cecf5edee06",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "355b281d74f89a262746b0d4b74343472d03e39e68cf52f8df561c80c81bc0f4",
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
