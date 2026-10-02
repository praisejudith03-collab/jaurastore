"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "182"
ASSETS = {
    "css/style.css": "e642a5e032613abd46fe2ee82aeb63ec70b77bb63481e39f9bb4ad6b70b7aa0a",
    "css/fonts.css": "5f91055338606581f7ca24caf32e0491abbfd1f8443c16a21a7cb99fccac5243",
    "js/store.js": "5ac12dafc406b343350a39bc5942f22fc9121e8840d4a79d8cf5ce08dcd94f23",
    "js/app.js": "8892c63b2dcc3e8b00c37582083c82791c26b514f447430548e1a62af153d497",
    "js/admin.js": "e64ebf94460569d5ad76784c60228a52baede381d8bc84c9d974591b175d8be2",
    "js/i18n.js": "2590d9d64ad9293fc9de6849b08995478cb939535956d586d24de2a1d4727d9d",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "d3e6313be33d273b13f7faeaa924a4d6e6370dff8c69f32b34565e7cf22fad63",
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
