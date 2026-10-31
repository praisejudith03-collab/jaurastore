"""The transport under an admin save must not lose it or hide its reason.

An admin save is one POST. Three transport rules decide whether it reaches
the shop:

  * it must be allowed longer than the five-second JSON default (a photo
    check + database write + read-back on a free instance behind mobile
    data), or it is aborted mid-flight, queued and re-sent from scratch;
  * a server that ANSWERED and refused must hand its reason back, so the
    editor can print "the products table is missing the column 'x'";
  * a dropped connection stays quiet - the durable outbox retries it.

tests/_net_save_check.mjs boots the real js/net.js and proves all three.
Developer machines may skip without Node; CI installs it and fails on a
missing runtime.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQUIRE = os.environ.get("JA_REQUIRE_BROWSER") == "1"


def test_admin_save_transport_checks():
    node = shutil.which("node")
    if not node:
        message = "Node is not installed; the admin-save transport check did not run"
        if REQUIRE:
            pytest.fail(message + " - CI installs Node, so this is a broken build")
        pytest.skip(message)
    result = subprocess.run(
        [node, str(ROOT / "tests" / "_net_save_check.mjs")], cwd=ROOT,
        text=True, capture_output=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ADMIN SAVE TRANSPORT CHECKS PASSED" in result.stdout
