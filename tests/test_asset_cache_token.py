"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "189"
ASSETS = {
    "css/style.css": "4db472b76d985b918907d3e8fb44a2880b42a1329e1cd8b207f59c451565698a",
    "css/fonts.css": "38f7a435b10fa00152606b1215baa3e7b3f964aba07606fe9684f962d914b07f",
    "js/store.js": "bd8ccd3d4992dce22975586767ab7007d1c691f3e5669b9facf3e5741c22d9db",
    "js/app.js": "2baa1e95388ef9739fb4743558634068ef6e0b2039e459298980e806a5fa3f93",
    "js/admin.js": "794c74f68ec11adde1e21e24a042cd0148ba810aeb141b19823b59fab08bcee7",
    "js/i18n.js": "ea588613ee0b2225f83be9a86364adfec811fdb1dfe215ab1068346ed49dcd83",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "bf0929f2b5d1ab3a63be533bc61ce0b8534aa10fa49a22e8a29262cb87a1850f",
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
