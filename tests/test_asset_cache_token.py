"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "216"
ASSETS = {
    "css/style.css": "d8e16b0fc27087a133d0526b9ab2a9cb3ba20e46db35c553e61666679ecb5b40",
    "css/fonts.css": "eb064c530a813d08f270a377e9dbc0b042758e244136953b3442a97a972e1772",
    "js/store.js": "3ebb173e4e70894c5fe64077ccad2dcde17157c6ca76e7657dc29c39f1fda4e6",
    "js/app.js": "a79f4faaa4c1547a37c5ef12a4f8e6967da51f9c2e70581bb32ab609d98b20c1",
    "js/admin.js": "fd9f1e512a21a0b5ff802df362d5e386a1e72a2ccad1ac6916f984e5aacea2a5",
    "js/i18n.js": "1ce5a78f72b53822bf3041ad1e7a977b6f07d5b95109ab38a9c2e390992717a0",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "js/accounting.js": "5ba1e19451b52f04b72102d316ca6ffb37c72d05d770c9819af54e3bee3a4268",
    "sw.js": "ccf8fb20d9b2b0d3622dad67cf51d3e75926ee8feba24bf879d29d4f2d030c33",
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
