"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "163"
ASSETS = {
    "css/style.css": "576bd48f3b54b0bd04af5cf66f8644a8b8b42f4b0c6353a203be4f22eab41530",
    "css/fonts.css": "377c6561da56f99cf44296e4006947daf680075b4a64ff64e0db5dea548c7282",
    "js/store.js": "8102aebfffc251496e008754bdf09235b8b80d932d2b7d9c92d56477ce877186",
    "js/app.js": "19212155a34befc02c3fe322a40d524e0846b926ef629858f1c573309ac6901f",
    "js/admin.js": "31974afdf88c331ee6d211710591566bc618416d45134f67ef872c01d8f83eb7",
    "js/i18n.js": "c23d56cf53b5ec01a096697aea2f08f1bfc31039e30bb8cb75062688ca329c22",
    "js/net.js": "d5de1bfd2f068d96c91332fdeee422dfff88b641d6c19e07bf903b374510f840",
    "sw.js": "a53d7d9ee883dd864265f33ab27312151adc3bb642cb00af881a3d32023a5f59",
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
