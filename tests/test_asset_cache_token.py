"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "180"
ASSETS = {
    "css/style.css": "86be092c6e18d14af921c8c244e64bc1ce608572735798dfce36f574c781d2fd",
    "css/fonts.css": "7364e971d6f0752737c5479e6220550e9d15b7209e798a42b0d69cf5771fc752",
    "js/store.js": "285cf618839fd32d5613fda851005b4c99b4f67f5962c7d52ccbb80b2822b192",
    "js/app.js": "8ea980341abfdd04c488dede20a47c619667125a1add256d6159a9299323e8a5",
    "js/admin.js": "237b84c7980b1e6ffd119fbd9e8fd9183bd97d1355e7bfa92c204099de3cf6d6",
    "js/i18n.js": "4e90108092b7840c9bafcbc2310da11bdd21385aadf05fa658680c4977093ced",
    "js/net.js": "9ac0d5a09fc1e1ab3b32d3c5af26cb2efc3e8b3ce0c2fbaaeaa90bbf798f752b",
    "sw.js": "e98b0fd1a6c621208be61a30826b932d61baaa7ec1be6a905fbf351228f2b3e4",
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
