"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "243"
ASSETS = {
    "css/style.css": "e8e9f5781bcf18a9137b65bdf129ad3e48ef3f783e2c5aaf3d34d2c2459bf894",
    "css/fonts.css": "030a978e7ccb33177f315ad52b179c35dd606b8c97da877f33ad5027cdd6aa16",
    "js/store.js": "080213d5475b97aa74dd732d3a5f031d76ca7aa6888cf9495d1254156091a0ef",
    "js/app.js": "8ed60b2f06e0b0dab70d5fd9627b677a89d6f28b7e0169fae705c107d3ed53e2",
    "js/admin.js": "4117ce0e057f703ff50a41ed9342a4dc5445a7bd6c7d351d2f15e90e74a5b0a5",
    "js/i18n.js": "ef3a36539e1c850341e8a13824748cf6193f85f9e3122175d4307b4ec24432aa",
    "js/net.js": "4789deba96beb84abac450096adf8a2df61a5c5b83bf31951b11b7af40109779",
    "js/accounting.js": "8da86bfd33a113e1f54e27f81db821154c84b7c39fdd64a7d630d5aa543fc1ef",
    "sw.js": "c80a026f9c8ddb2cbe1dd8f884a366ff0abb6a3ea88823d3898df5388a7fa895",
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
