"""Pytest wrapper for the storefront browser simulation (tests/_store_sim.mjs).

The Node harness boots the REAL js/store.js in a stubbed browser and proves
the client-side catalogue pipeline keeps every online product the server
serves - the layer pytest cannot reach on its own. CI (ubuntu-latest) ships
Node, so this runs everywhere the suite runs; it skips with a clear reason
only where no Node runtime exists.

Run with:  python3 -m pytest tests/test_storefront_catalog.py -q
"""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(ROOT, "tests", "_store_sim.mjs")


def test_storefront_keeps_every_online_catalog_product():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (frontend sim needs Node >= 18)")
    proc = subprocess.run([node, SIM], cwd=ROOT, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, (
        "storefront simulation failed:\n" + proc.stdout + proc.stderr)
    assert "all storefront checks passed" in proc.stdout
