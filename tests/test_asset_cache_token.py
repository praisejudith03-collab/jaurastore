"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "156"
ASSETS = {
    "css/style.css": "9041b657d4d8b5385b899e62b96dac84bec484d4fef32505381897578e341525",
    "css/fonts.css": "7ef835f13f74916a10a1360720e740bfe54394e37e7b93c89c5b5fdfc6df54a4",
    "js/store.js": "b628e11f5ac79554cb7887f80a37e6c988445555242a3a0ef22b7e333c5e5d8e",
    "js/app.js": "9425783fe37d1a51902b6df06a7430f6b16de5bde8b9e6dc3120843943eaf78b",
    "js/admin.js": "3949e5cfde514482003dbd74842da2f6f1eee6990d9b00f7dba100efc758f3f4",
    "js/i18n.js": "ca54f1fbbf1b4532fc35df4bd8f3eea30991c8a8e0eea5629b49aca25c7ef1af",
    "js/net.js": "d5de1bfd2f068d96c91332fdeee422dfff88b641d6c19e07bf903b374510f840",
    "sw.js": "6812fa1fc23c4531e9324465db7e3bfd4f2483dbce8df6c67b135972c6597e21",
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
