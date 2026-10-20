"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "215"
ASSETS = {
    "css/style.css": "d8e16b0fc27087a133d0526b9ab2a9cb3ba20e46db35c553e61666679ecb5b40",
    "css/fonts.css": "0162053eeff35483bddbca93a39ae98c13e8b11ce7ce016687f0038dbb911655",
    "js/store.js": "7f9199bd614753ea900fd620eec09314de7ae86a8dbf7df40e336a27eeb7ca00",
    "js/app.js": "f300c63aaefa8789e85e88497da1bb0bd773144b1f20855ddbbc33941e9c49e2",
    "js/admin.js": "9bba4d7035c9006e591cdd6fe23418037f7976d1d0f6f9621ba61cacb147bb84",
    "js/i18n.js": "1c4e75126587ebd6993ae492e8881975913d886181371a4942466d5cf4ec2eca",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "js/accounting.js": "5ba1e19451b52f04b72102d316ca6ffb37c72d05d770c9819af54e3bee3a4268",
    "sw.js": "4c6157be13450b925c6565ded61c96d4a33f811051d6dcd1f2f8f371b3499a52",
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
