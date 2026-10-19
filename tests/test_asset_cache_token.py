"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "210"
ASSETS = {
    "css/style.css": "af945dc734236a9534c539e2f6fd489d94ea7e1fa968fd5102ab0589ff2fb9aa",
    "css/fonts.css": "fca3334a51a6ba59a34f1ff1afc760635a587eec7ab82ff53fa1e374e2206e22",
    "js/store.js": "9f06e7e5c87206b95be065182738d9aac6e7d6a34eb7ba547d51dba85203311e",
    "js/app.js": "7b7433263654809f73ec77bb8483d7220acd0f0ec465246c78e47697cb739cfe",
    "js/admin.js": "951722693ff1d41cb8e4503f04ae09635d72aac8d73ec140d42d529e9b1ae946",
    "js/i18n.js": "1c009c70f37b2be91e14360ecde97d7a70997a85140e8ee770d62e92d8ebbc62",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "9dcbaa78d449ce89992e388e3741e20ad7aeb2b6a2f863e77dc0a095d600a138",
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
