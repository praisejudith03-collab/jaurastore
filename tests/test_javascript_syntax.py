"""Parse all tracked shipped JS, including service workers and static assets."""
from pathlib import Path
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
