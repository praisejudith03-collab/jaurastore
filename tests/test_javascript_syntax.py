"""Parse all tracked shipped JS, including service workers and static assets."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = [p for p in subprocess.check_output(
    ["git", "ls-files", "-z", "--", "*.js"], cwd=ROOT, text=True).split("\0")
    if p and not p.startswith(("tests/", "tools/"))]


@pytest.mark.parametrize("path", SHIPPED)
def test_shipped_javascript_parses(path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed (CI explicitly installs Node)")
    result = subprocess.run([node, "--check", str(ROOT / path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_shipped_editor_backups():
    for folder in ("js", "css"):
        assert not list((ROOT / folder).rglob("*.bak"))


# Shared admin helpers must sit at top level (brace depth 0).
# node --check verifies syntax but does not resolve identifiers, so a helper
# declared inside another function (e.g. paintDesk) parses cleanly but throws
# a ReferenceError at runtime when invoked from outside.
SHARED_ADMIN_HELPERS = [
    "siteFieldPatch",
    "loadedSiteValue",
    "loadedSiteRow",
    "PAYMENT_FIELDS",
    "bindBanner",
]


def _brace_depth_at(src: str, identifier: str) -> int:
    m = re.search(r"\b(function|const|let|var)\s+" + re.escape(identifier) + r"\b", src)
    assert m, f"declaration for {identifier} not found in source"
    target_pos = m.start()

    depth = 0
    in_str = None
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(src)

    while i < target_pos:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
        elif in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 1
        elif in_str:
            if ch == "\\":
                i += 1
            elif ch == in_str:
                in_str = None
        else:
            if ch == "/" and nxt == "/":
                in_line_comment = True
                i += 1
            elif ch == "/" and nxt == "*":
                in_block_comment = True
                i += 1
            elif ch in ('"', "'", "`"):
                in_str = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        i += 1
    return depth


@pytest.mark.parametrize("helper", SHARED_ADMIN_HELPERS)
def test_shared_admin_helpers_are_at_brace_depth_zero(helper):
    admin_js = (ROOT / "js" / "admin.js").read_text(encoding="utf-8")
    depth = _brace_depth_at(admin_js, helper)
    assert depth == 0, f"{helper} must be declared at top-level scope (brace depth 0), found depth {depth}"

