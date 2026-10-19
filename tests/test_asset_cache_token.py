"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "211"
ASSETS = {
    "css/style.css": "af945dc734236a9534c539e2f6fd489d94ea7e1fa968fd5102ab0589ff2fb9aa",
    "css/fonts.css": "0a040fcc4f76898bd2bd8474b99b0e801290037c012e50181d9d59808d525080",
    "js/store.js": "8669d64c0a3e7bab6c8f71fe646b64859269acfa92e50da52a2bdaa26f68ae46",
    "js/app.js": "d8ec81a571419fa24cffbffabf26f6e2487a907e9e94a13c534844ab00de1dc8",
    "js/admin.js": "7bbaa8002ef372010aa3d91957853b6e2e6d6c21189efb706604612d64054f21",
    "js/i18n.js": "1c009c70f37b2be91e14360ecde97d7a70997a85140e8ee770d62e92d8ebbc62",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "js/accounting.js": "5ba1e19451b52f04b72102d316ca6ffb37c72d05d770c9819af54e3bee3a4268",
    "sw.js": "19af44a466ef50e27aed59e7246bd88d1329f6be6cba3dd78f184220c00ec463",
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
