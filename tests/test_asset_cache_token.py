"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "199"
ASSETS = {
    "css/style.css": "58498264e7db4a67e07fcee3bda49016ce1a82a048b6f045effcf64adc840c2f",
    "css/fonts.css": "874dad30c195d3c7f4eea7937102fc2be1149a9e4bbcc941669a895ec07df271",
    "js/store.js": "8ba47857c74a4f3c9223259a2648e72ce594031a31df89ae8565f7bf82ff3296",
    "js/app.js": "36b9084a2f06a36422975a089b274efc25d526b4271fd5706d7a336cd400e2a3",
    "js/admin.js": "664f835df36e4fee6fe17f629a48e67e767767484d8517313fd2016ba3e1534d",
    "js/i18n.js": "eb9e0c09adfdc5c8b28d5445cc494620537b03c4cbf085b43b065ad7279c3883",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "cd872e35f7dd2d8d08a459f3c511c2fc25b7a2addf71d1ab850f9d1eb0e0abff",
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
