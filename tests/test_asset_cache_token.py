"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "154"
ASSETS = {
    "css/style.css": "39bb80288a61f19bf4c0337c0858e04de86b0617339903875d8f13128e3d9bf4",
    "css/fonts.css": "c0a427c04098b75476d9d88bdd785a12d799a0a571aed775fadfb90399472111",
    "js/store.js": "e83f3be40557252615051d69ccaf5483d7b90b4f234dd65eb4b135a416bc8cc2",
    "js/app.js": "8b25d0c33a45cd1bbad707d2d259f97ba7a110e2cb7d80d26f869cbea7b6ab1c",
    "js/admin.js": "76caa8a668c36bc98090564983916db13c685680dcfa1dfbf920626645afea12",
    "js/i18n.js": "815067a9c9a9e906e24748963526729aa9acf3cd4584f919df4919a06d180b90",
    "js/net.js": "d5de1bfd2f068d96c91332fdeee422dfff88b641d6c19e07bf903b374510f840",
    "sw.js": "527efb0bc141236ad726532939068e8525af051674d0a2f310b9bd4aedb164a8",
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
