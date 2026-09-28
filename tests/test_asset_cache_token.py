"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "157"
ASSETS = {
    "css/style.css": "c49440a0f411d884125790e4abb8ad69fcde900f2ec56874674486ee591ff1c2",
    "css/fonts.css": "4fda7fa47c8a3b80bc5d1753e57c903b60dbcc20d9a3b6cf6e3c2e243589a5a9",
    "js/store.js": "67ec9a68d64fdf4d34a761874189d6aaa6980ea9fceef8982df70a4703e170e4",
    "js/app.js": "1dd7baf94579b27990e247a024cb455d400fb50bf49bb74e21faf373f2197a9d",
    "js/admin.js": "ca469259fda211ea1d21146902a3721bc2ce91bbff1f8449e5abbcf4aad3affa",
    "js/i18n.js": "0f4501dbb5f084d6251012146d6fca88ad91997c950f78598c501f84a6e8938b",
    "js/net.js": "d5de1bfd2f068d96c91332fdeee422dfff88b641d6c19e07bf903b374510f840",
    "sw.js": "1f4aa96f8ea2c37ab3dc6ed9bfa836d6f4ff4470a165faf94752fb2ffb9162d6",
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
