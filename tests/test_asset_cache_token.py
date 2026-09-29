"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "162"
ASSETS = {
    "css/style.css": "b902e8c8d8db0d062251322b80b000af79cfa05f7bbc637fffadcbce2fc79460",
    "css/fonts.css": "85c5916308b7705d972c0721c74d83665020a043c490021562dcddebf45796d6",
    "js/store.js": "ef9671e42a90a6b1e6972d5f46bdae89d9e2e7172c88d2fba2c93959ce1be2e8",
    "js/app.js": "ec278f45f164167db5a35195e6659f7c31653f9627718c2a73d33aa49ae44d1a",
    "js/admin.js": "19947a09b8881af0f6f5bc87f8229f2f276e8e60885c5e04647fca3aa8d72cca",
    "js/i18n.js": "c23d56cf53b5ec01a096697aea2f08f1bfc31039e30bb8cb75062688ca329c22",
    "js/net.js": "d5de1bfd2f068d96c91332fdeee422dfff88b641d6c19e07bf903b374510f840",
    "sw.js": "21f15cd756d86497a6f8db0336281473c95700c27fed9c6560a5fea4b3818cb6",
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
