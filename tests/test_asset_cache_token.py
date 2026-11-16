"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "245"
ASSETS = {
    "css/style.css": "ea5785efc3c8f3bd95f8f5dc0ec416f972634fcb33e9d1c6cd101d3d70633aa4",
    "css/fonts.css": "1c839b3184b81accb413326714da7944989712545b32079216d8a57b51adb21d",
    "js/store.js": "a8de1fdd26dc7b299f20514352f8898678099a1af7b73a431a4fb7ccafc0595c",
    "js/app.js": "f40dfea920b6e2daf5a25e9f929563c12adf32f2518eee2f996ed515f6f1da9d",
    "js/admin.js": "ffcaab825d89515261451cd15d2e5d2d973f24e5458b3c1ab535d7a37e068454",
    "js/i18n.js": "ef3a36539e1c850341e8a13824748cf6193f85f9e3122175d4307b4ec24432aa",
    "js/net.js": "4789deba96beb84abac450096adf8a2df61a5c5b83bf31951b11b7af40109779",
    "js/accounting.js": "126107e5a755933e2f3455999ba4d97e1e45c7f47e7d2a08c2a7b9f90e3bd809",
    "sw.js": "351fd65d2efaaa68f4cdcb5ed8ec33215ee6e7ef07b5d7990fccf09d4d9e67f3",
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
