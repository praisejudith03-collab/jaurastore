"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "223"
ASSETS = {
    "css/style.css": "eee9dab0919c4aea2e24c82bb20f747470388af767b895b8ed3734cbf3189297",
    "css/fonts.css": "ef55df132677ac34a878400f97a980ea141cf9bda0119be0fd7bf2ca2ecd06d3",
    "js/store.js": "b8652fa56e9e080282012f177b6e8e5f14f7f525bebf8e0b1dffcf06c7f8fa24",
    "js/app.js": "ca2abed6fda5bab10dbdfd304f26cf440f8f69c6baf3bb590cfa9e1dc8b24d60",
    "js/admin.js": "f8610cc35da02408e7f09cc30ddd74018ac369df44ba27bbcc6e2f84b4934d8e",
    "js/i18n.js": "d949cb4596b736ecd91669c4b8af81079c6475e624caf547d3948cba3cc72583",
    "js/net.js": "653b2fc748bb525f9f6da42e44658da170a3d054b7fd05cb5ec318bcdec0af81",
    "js/accounting.js": "8da86bfd33a113e1f54e27f81db821154c84b7c39fdd64a7d630d5aa543fc1ef",
    "sw.js": "e2a2975a4fa72c565eaec410a3142a9a6a672d3a24e102601e28c8dbbdb8e11b",
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
