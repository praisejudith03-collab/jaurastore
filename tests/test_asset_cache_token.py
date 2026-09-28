"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "161"
ASSETS = {
    "css/style.css": "13575162e0a5e976fbba935cd47a293d64255f51497ec35264709a113aa0e106",
    "css/fonts.css": "6bec82b8fdc5b6f93292a7ef04d4792f6ecda2b488262ad06b5dbd5184718233",
    "js/store.js": "63600412da23b833460f0179410405770ab4ad1f22c8997027da607c9d684f4e",
    "js/app.js": "fda5abbc97576275c1ffe7d7a49c46247f1c6d905d3e04775924798ea3880957",
    "js/admin.js": "1e46dcd58bbda59cf1009be74130229b8b10df23dfc06f78dd4704017c5fa935",
    "js/i18n.js": "c23d56cf53b5ec01a096697aea2f08f1bfc31039e30bb8cb75062688ca329c22",
    "js/net.js": "d5de1bfd2f068d96c91332fdeee422dfff88b641d6c19e07bf903b374510f840",
    "sw.js": "97bc1d888e1e2420daed8e7973cb2fd5e73eb8a55474a50a2b3fe58ab55b6b6c",
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
