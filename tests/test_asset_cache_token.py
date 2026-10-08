"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "201"
ASSETS = {
    "css/style.css": "405e90ae0a8db73a64d49fbfc3e7c70158169b6d678010c22bb8d5301abb4d26",
    "css/fonts.css": "e67fa5810c52b2ab0019efc0c46574255ef8023a5ad4c7a13f24f2318a4221ac",
    "js/store.js": "47523cb029238e13e22670cc184dbbf3f6de85c840cebb76e4eb8e502c27df60",
    "js/app.js": "e515f1c7a23fdfeb091ebe7b3a4361d9d8f2b66ac9ac6f743e74ded074770748",
    "js/admin.js": "fd798e99809c1e3b30f92a9086b78eec6b1f16e13293b47392b9296c026d3069",
    "js/i18n.js": "eb9e0c09adfdc5c8b28d5445cc494620537b03c4cbf085b43b065ad7279c3883",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "5f7433a87d39ff35a356f22d5e8eee715c6ef3e806f4c5f5b67a346e13692722",
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
