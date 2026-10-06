"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "197"
ASSETS = {
    "css/style.css": "c8a5d95e8bd3576419afb21ad9d63b5d96cbaecd0ed3fc24f6e6855e83096d17",
    "css/fonts.css": "0a1094a59e51dc83d2ebc146e18ae6680795820cce6190d51e8658f820c2ac35",
    "js/store.js": "773e3cf76b2eb37bd5c294479d6e889f37893487979d84fc0a88db5ac2687054",
    "js/app.js": "a22c8ceae981d3b39a1b2af86e7d22297eeded1c48b0bcc5eef3561857424e7d",
    "js/admin.js": "3d065c784966a53eec52045d1ec48b64ebfaccd514aec5ed3039a6bd99805bfa",
    "js/i18n.js": "2590d9d64ad9293fc9de6849b08995478cb939535956d586d24de2a1d4727d9d",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "c1efcbca06a559e8940ea2110354a8962607cda95e0ba38cf2e106ef60a5e7b1",
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
