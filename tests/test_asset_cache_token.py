"""Shared asset cache tokens must change whenever shipped bytes change."""
import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOKEN = "209"
ASSETS = {
    "css/style.css": "49b9c8888b8ab90beff4a9d3b21574e734788839e6190051007f77566561adae",
    "css/fonts.css": "ddbe065ca0432535c283609570c1cf52eb88037426099d28494b9f0f146e2d7d",
    "js/store.js": "42c9cb1528c0462d1ca14b5d26198bac032ce29d2ee44ee0469c0f3081c5c6e9",
    "js/app.js": "dc24eb7babb231104820e424b71144953cb255a7e7c4457c872e807a86fd20af",
    "js/admin.js": "efc76940009cfbe5fa8f84abe5fafee719e7d87516afd39ef4138b6c325e95e8",
    "js/i18n.js": "1c009c70f37b2be91e14360ecde97d7a70997a85140e8ee770d62e92d8ebbc62",
    "js/net.js": "972876c0f8dadf54702dec9925a28f990fe48a907b99c22553e381fafe6eab90",
    "sw.js": "cfcaa458899ccaa5c4a59bbf4a5e912ac8f5da580c78f38ee28a50440ac19271",
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
