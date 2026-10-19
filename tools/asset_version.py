#!/usr/bin/env python3
"""Automated build asset versioning — the fingerprint half of the cache policy.

WHY THIS EXISTS. A phone that has already visited the shop holds the old
`js/admin.js?v=205` for a year, on purpose: every versioned asset URL is served
with `public, max-age=31536000, immutable` (see app.py / security.IMMUTABLE_VERSIONED)
which is what makes a repeat visit on 3G instant instead of five seconds of
blank screen. The flip side is that a shipped change is INVISIBLE to every
returning visitor until the URL itself changes. Before this tool, that meant an
agent or the owner editing a JS file, watching
`tests/test_asset_cache_token.py` fail, and then hand-editing the token in 20
HTML pages, sw.js, css/fonts.css, three JS bundles and three test constants —
and the moment somebody forgot a step, phones kept rendering the previous build.
Owners described that as "the mobile page is stale, I have to add ?v=live" and
cleared browser caches by hand.

WHAT IT DOES.
  --check   Compare the shipped bytes of every fingerprinted asset against the
            hashes recorded in tests/test_asset_cache_token.py. Exit 1 if any
            differ, i.e. an asset changed without a token bump. CI runs this.
  --write   If (and only if) the bytes changed: increment the shared numeric
            token everywhere it appears, and refresh the fingerprint hashes.
            When nothing changed it is a no-op, so it is safe to run on every
            build — a deploy that touched no asset does NOT invalidate a
            year's worth of phone caches for nothing.

The token is content-derived in the sense that matters: one token, one build of
each file, and a new token the moment any byte moves. Run it from a pre-deploy
step (`python3 tools/asset_version.py --write`) and commit the result.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "tests" / "test_asset_cache_token.py"
SW = ROOT / "sw.js"


def set_root(root: Path) -> None:
    """Point the whole tool at another tree.

    Exists for the test suite: `--write` REWRITES shipped files, and a pytest
    that ran it against the live repo bumped the build token across 28 files as
    a side effect of merely running the tests. Anything that exercises the
    writer must do it on a copy, so the flag is part of the tool's interface
    rather than something each caller reimplements.
    """
    global ROOT, GUARD, SW
    ROOT = Path(root).resolve()
    GUARD = ROOT / "tests" / "test_asset_cache_token.py"
    SW = ROOT / "sw.js"
    if not SW.exists() or not GUARD.exists():
        raise SystemExit(f"asset_version: {ROOT} is not a jaurastore tree "
                         f"(needs sw.js and tests/test_asset_cache_token.py)")

# Files that may CARRY a `?v=<token>` reference. The list is a allowlist on
# purpose: `tools/`, the markdown release notes and the schema sections quote
# old tokens as history and must never be rewritten.
REF_GLOBS = ("*.html", "sw.js", "js/*.js", "css/*.css")

# Constants that pin the token itself.
TOKEN_CONSTANTS = (
    (Path("tests/test_asset_cache_token.py"), 'TOKEN = "{old}"'),
    (Path("tools/sync_favicon_head.py"), 'TOKEN = "{old}"'),
    (Path("tests/test_brand_icons.py"), 'SHARED_TOKEN = "{old}"'),
)

VERSION_RE = re.compile(r'const VERSION = "jaura-v(\d+)";')


def current_token() -> str:
    """The token the service worker declares — it is the build's version."""
    match = VERSION_RE.search(SW.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("asset_version: sw.js has no `const VERSION = \"jaura-v<digits>\";`")
    return match.group(1)


def fingerprinted() -> list[tuple[Path, str]]:
    """(path, recorded sha256) for every asset the guard pins."""
    text = GUARD.read_text(encoding="utf-8")
    block = text.split("ASSETS = {", 1)[1].split("}", 1)[0]
    out = []
    for rel, digest in re.findall(r'"([^"]+)": "([0-9a-f]{64})"', block):
        out.append((ROOT / rel, digest))
    if not out:
        raise SystemExit(f"asset_version: could not read the ASSETS map from {GUARD}")
    return out


def drifted() -> list[str]:
    out = []
    for path, digest in fingerprinted():
        if not path.exists():
            out.append(f"{path.relative_to(ROOT)}: missing from the tree")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            out.append(f"{path.relative_to(ROOT)}: bytes changed "
                       f"({actual[:12]}… vs recorded {digest[:12]}…)")
    return out


def ref_files(token: str) -> list[Path]:
    hits = []
    for pattern in REF_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if f"v={token}" in path.read_text(encoding="utf-8", errors="replace"):
                hits.append(path)
    return hits


def refresh_hashes() -> int:
    """Re-record every fingerprint from the bytes that now ship."""
    text = GUARD.read_text(encoding="utf-8")
    digests = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path, _ in fingerprinted() if path.exists()}

    def sub(match: re.Match) -> str:
        line = match.string[match.start():match.end()]
        rel = re.search(r'"([^"]+)": ', line)
        if rel and rel.group(1) in digests:
            return f'{rel.group(1)}": "{digests[rel.group(1)]}"'
        return match.group(0)

    new = text
    for rel, digest in digests.items():
        new = re.sub(rf'"{re.escape(rel)}": "[0-9a-f]{{64}}"', f'"{rel}": "{digest}"', new)
    if new != text:
        GUARD.write_text(new, encoding="utf-8")
    return len(digests)


def write(token: str) -> bool:
    nxt = str(int(token) + 1)
    touched = 0
    for path in ref_files(token):
        text = path.read_text(encoding="utf-8")
        bumped = (text.replace(f"v={token}", f"v={nxt}")
                      .replace(f"jaura-v{token}", f"jaura-v{nxt}"))
        if bumped != text:
            path.write_text(bumped, encoding="utf-8")
            touched += 1
    for rel, template in TOKEN_CONSTANTS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        old, new = template.format(old=token), template.format(old=nxt)
        if old in text:
            path.write_text(text.replace(old, new), encoding="utf-8")
            touched += 1
    refresh_hashes()
    print(f"asset_version: token {token} -> {nxt} across {touched} file(s); "
          "fingerprints refreshed")
    return True


def check(token: str) -> bool:
    bad = drifted()
    if not bad:
        print(f"asset_version: ok — every shipped asset matches token v{token}")
        return True
    print("asset_version: the build's assets do not match their recorded "
          "fingerprints:")
    for line in bad:
        print(f"  - {line}")
    print(f"  Fix: python3 tools/asset_version.py --write   "
          f"(bumps v{token} -> v{int(token) + 1} everywhere and re-records the hashes)")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true",
                      help="fail if an asset changed without a token bump (CI)")
    mode.add_argument("--write", action="store_true",
                      help="bump the token and refresh hashes, only if bytes changed")
    parser.add_argument("--root", default=str(ROOT),
                        help="tree to operate on (default: this repo). Tests must "
                             "point this at a COPY: --write rewrites files.")
    args = parser.parse_args()
    set_root(Path(args.root))
    token = current_token()
    if args.check:
        return 0 if check(token) else 1
    if not drifted():
        print(f"asset_version: nothing changed — the build stays on v{token} "
              "(no phone cache is invalidated for free)")
        return 0
    return 0 if write(token) else 1


if __name__ == "__main__":
    sys.exit(main())
