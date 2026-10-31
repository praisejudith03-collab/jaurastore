"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "239"
ASSETS = {
    "css/style.css": "3ad23f87a5c5545484f8838608c5269b09ace410bda616084ccaa528e648b949",
    "css/fonts.css": "eec8dfa714f464909b315cd829ec34f933fb8c10ac72dfdc024b85114f15889e",
    "js/store.js": "3c33e808136565aa8a85de9f9174c793c19dbcf9f11fe8234cbcf2f02998f7b4",
    "js/app.js": "c12d774ae535aac5063729bee12fc79e2d0d27a0682aa92f2cbf485e39d93670",
    "js/admin.js": "d05a940bc0f499478cc2661dd78c9b213ea85148fe26d7f0b40c0f9c3942ece4",
    "js/i18n.js": "d949cb4596b736ecd91669c4b8af81079c6475e624caf547d3948cba3cc72583",
    "js/net.js": "4a10f7227672ea67292130d63f487fc322b08a1ae00959d10ab1fa0ca07957f1",
    "js/accounting.js": "8da86bfd33a113e1f54e27f81db821154c84b7c39fdd64a7d630d5aa543fc1ef",
    "sw.js": "0bf873255fc48512b04131321cfff619b20739d76a7bdaac204fe2c200f4cd30",
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
