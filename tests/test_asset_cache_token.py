"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "155"
ASSETS = {
    "css/style.css": "62bb5a7f58f443892602318ec91422cbf9245113d373ac7be6045ea8a24376f0",
    "css/fonts.css": "c3904294a8971ac7b90ef62f3847ffedd9e7ca01ed3acf51b12ee4cd1aae8b65",
    "js/store.js": "626573ce566d2b7bf54dd59aaeb54e8cf6324f80a0b77d01fa6c8ccd34f0a60e",
    "js/app.js": "1603160b07ae4449b0205488faac7bd52664d7b30967d6d0e5735737f696ffe4",
    "js/admin.js": "7505f8878f12cb8c42bc886a528b024c30efec902a10ade30a80f2ae8fc7a856",
    "js/i18n.js": "ca54f1fbbf1b4532fc35df4bd8f3eea30991c8a8e0eea5629b49aca25c7ef1af",
    "js/net.js": "d5de1bfd2f068d96c91332fdeee422dfff88b641d6c19e07bf903b374510f840",
    "sw.js": "3c1b84b2214ee184c463dfdd620ad802884ce604dfaf0b61732137d01c2b8557",
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
