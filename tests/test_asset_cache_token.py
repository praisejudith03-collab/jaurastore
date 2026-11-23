"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "247"
ASSETS = {
    "css/style.css": "ba18706522bb65711e8c3bcc84f1efda113f0b9586af214e8639be2fcaf5166c",
    "css/fonts.css": "3bad73812fedc30475634b7ccbce1e5d2b6c27b83f804307bca4b2d7ba7b934c",
    "js/store.js": "dfb03ff61953b523da4aad105b69f49b5b02b472c6baab00dc0386345bfdfa6f",
    "js/app.js": "494e0c0f6c149f6c7e4110bbf3a7b1e3e9e55e999b620fd64afa29d6729aaed1",
    "js/admin.js": "f4ab89f4878e31cce838d2c625137475fa43ad00bd3582bb9bbe4a5ca254d050",
    "js/i18n.js": "ef3a36539e1c850341e8a13824748cf6193f85f9e3122175d4307b4ec24432aa",
    "js/net.js": "4789deba96beb84abac450096adf8a2df61a5c5b83bf31951b11b7af40109779",
    "js/accounting.js": "126107e5a755933e2f3455999ba4d97e1e45c7f47e7d2a08c2a7b9f90e3bd809",
    "sw.js": "8443e591f4d567045bb265fcf76b011953a87031a9aa09acab047ba986be0133",
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
