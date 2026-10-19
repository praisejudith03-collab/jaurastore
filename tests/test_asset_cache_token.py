"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "205"
ASSETS = {
    "css/style.css": "af945dc734236a9534c539e2f6fd489d94ea7e1fa968fd5102ab0589ff2fb9aa",
    "css/fonts.css": "f2c4d6248b2aca7573505ee2bdf629e831fde68c5e1a3fb048aba29eab78e0a6",
    "js/store.js": "7b00f3f90ebd9111e8cb5b13cfee475b80d335209334a4ec2508f6111d38b349",
    "js/app.js": "2840e18f4571ad83620c242a275896cd0862c08db18c99ffca69a975bb2b2857",
    "js/admin.js": "dcce3186e89edb308a9ad09ab7b502259b596b6d7118e0ebb6ecf90d55f46d54",
    "js/i18n.js": "082065a70f0350a6ab6fd1a07d3b66666b68b333947b344c49946cecf5edee06",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "d290ac6b949647c59011e12a96133dc4f66f79144a2ac20dacca05174283eb5c",
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
