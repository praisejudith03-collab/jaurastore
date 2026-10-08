"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "200"
ASSETS = {
    "css/style.css": "405e90ae0a8db73a64d49fbfc3e7c70158169b6d678010c22bb8d5301abb4d26",
    "css/fonts.css": "9ef95c5ef58ca9905bd4dc22b99168b8a4a44af0ea039535c83d7baf09578735",
    "js/store.js": "e96a36285c05267c63e07b8571041ccace42fe5721dd98badd35ed23b2b24617",
    "js/app.js": "fb3af04e56bcafbb0c974f8cdca24aed1fb8100e12a37bf1fd7a1d603ead334d",
    "js/admin.js": "29006c826aee9b37c02031057ba2a243459e40eb6dbb2f2a9ce8f32a0556dc28",
    "js/i18n.js": "eb9e0c09adfdc5c8b28d5445cc494620537b03c4cbf085b43b065ad7279c3883",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "b399e6cf271eb8d1297cc66fbfe2b51565bdf4a24ffc0f01c55695c0c4588a17",
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
