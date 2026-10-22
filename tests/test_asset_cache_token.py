"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "222"
ASSETS = {
    "css/style.css": "52fab2dd614fa3305434c9ea74d18fed049d8b9c88d416522342dadc516c6535",
    "css/fonts.css": "00c6d37f9a2154e3c7cec852671f5040e06e6efd0e67b392fc1d70dd45dfede9",
    "js/store.js": "340d23c136a5f516a89613d7f1c1691c59a5b0ca5c47354306e28743f703bc68",
    "js/app.js": "12dffb24dccdd48ac86a9dc50895dfb5be8517f404f1b6010bbf8ac8f1ad73c8",
    "js/admin.js": "760ac3b6011288c8b12088e318ccbae54e4a8401f721ac6f6a504a8a0d980ff1",
    "js/i18n.js": "d949cb4596b736ecd91669c4b8af81079c6475e624caf547d3948cba3cc72583",
    "js/net.js": "653b2fc748bb525f9f6da42e44658da170a3d054b7fd05cb5ec318bcdec0af81",
    "js/accounting.js": "8da86bfd33a113e1f54e27f81db821154c84b7c39fdd64a7d630d5aa543fc1ef",
    "sw.js": "1e76b638c6c14afb9aaffd0826d803a090d30df1f325ad25a194548949c676c2",
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
        assert actual == expected, (
            f"{name} changed without a token bump - every returning phone keeps "
            f"the previous build of it for a year (immutable URL). Fix with: "
            f"python3 tools/asset_version.py --write"
        )

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
