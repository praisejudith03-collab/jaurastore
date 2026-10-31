"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "235"
ASSETS = {
    "css/style.css": "3ad23f87a5c5545484f8838608c5269b09ace410bda616084ccaa528e648b949",
    "css/fonts.css": "bd850b2755db5c3068be8ddffcca49b6a56f35fb208780789a30c212863e9cc1",
    "js/store.js": "d871623910a0103542b517def459e0c3319484f5d867eb081e6b39a77f3c64be",
    "js/app.js": "3c198feb5891562ceedc4f154ba44c9dc1b207256dd2412fa7041e6425d784be",
    "js/admin.js": "e37df34dc8b766eac80dcd33ef39934093f4ce5e9c5c31b99b7b879a9bde3900",
    "js/i18n.js": "d949cb4596b736ecd91669c4b8af81079c6475e624caf547d3948cba3cc72583",
    "js/net.js": "653b2fc748bb525f9f6da42e44658da170a3d054b7fd05cb5ec318bcdec0af81",
    "js/accounting.js": "8da86bfd33a113e1f54e27f81db821154c84b7c39fdd64a7d630d5aa543fc1ef",
    "sw.js": "ce8c5a8a95cbdc79d4197ae116efebe080bcec138625cc553815e93e98778eab",
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
