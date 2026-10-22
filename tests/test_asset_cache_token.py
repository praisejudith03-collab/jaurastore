"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "221"
ASSETS = {
    "css/style.css": "52fab2dd614fa3305434c9ea74d18fed049d8b9c88d416522342dadc516c6535",
    "css/fonts.css": "483d9e00ff76e7154485a1a54b279f12ddcda9822fa5d2fbd25fa758554f3607",
    "js/store.js": "29ef77db0f435ea04bcf0c143a400513d9de88c6f2257c2dad2ca539bf91d8ef",
    "js/app.js": "3a052567f62455e577d95ebb5115593a16a15bb3b9f4e524698038ae5aa3bd47",
    "js/admin.js": "095dc5d84cefaee921acf2deecd563f474b96adb974f099f4888acfd8f930551",
    "js/i18n.js": "d949cb4596b736ecd91669c4b8af81079c6475e624caf547d3948cba3cc72583",
    "js/net.js": "653b2fc748bb525f9f6da42e44658da170a3d054b7fd05cb5ec318bcdec0af81",
    "js/accounting.js": "8da86bfd33a113e1f54e27f81db821154c84b7c39fdd64a7d630d5aa543fc1ef",
    "sw.js": "4bbf634483b6a987d20672d4d82e4fb9daa7ec46d87356034a568242767fdac9",
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
