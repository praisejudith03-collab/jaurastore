"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "192"
ASSETS = {
    "css/style.css": "01d202293ab55b2f889f1640866a0e835a4dc0193ba17fab16aaaff4588fb74f",
    "css/fonts.css": "25876f8dfa09f6d57f3b9ac0897e54b48862aed3ca3d766373c208d2126e9237",
    "js/store.js": "0eb6be6d09195f0bf2439f460067a2a451eb67a96b4fa5eece99491a290cfda4",
    "js/app.js": "7ef47082c497fcf12d2c549737109604ffc34891290e616fc0d52d15e8f70af5",
    "js/admin.js": "a489c92fdab362320f5fdce2892aa843657e9fb3e09e6e33863b5b68215d4035",
    "js/i18n.js": "2590d9d64ad9293fc9de6849b08995478cb939535956d586d24de2a1d4727d9d",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "ee7738fb969c945468a7ab9ea03ebcf93b0534153acda01198550f0ce141340c",
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
