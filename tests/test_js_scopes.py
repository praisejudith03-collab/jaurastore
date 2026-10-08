"""Runtime/scope guards for file-local JavaScript helpers.

These intentionally use Acorn rather than `node --check`: syntax validation
cannot see an identifier that is declared in a sibling function. Developer
machines may skip without Node/Acorn; CI installs both and fails on a missing
runtime so a green build always runs the guards.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQUIRE = os.environ.get("JA_REQUIRE_BROWSER") == "1"


def _run(script: str, *args: str) -> str:
    node = shutil.which("node")
    if not node:
        message = "Node is not installed; JavaScript helper-scope guard did not run"
        if REQUIRE:
            pytest.fail(message + " - CI installs Node, so this is a broken build")
        pytest.skip(message)
    result = subprocess.run(
        [node, str(ROOT / "tests" / script), *args], cwd=ROOT,
        text=True, capture_output=True, timeout=120,
    )
    if result.returncode == 3:
        message = ("Acorn is not installed (npm install --no-save --prefix "
                   "/tmp/uidom jsdom acorn); the JavaScript helper-scope guard did not run")
        if REQUIRE:
            pytest.fail(message + " - CI installs it, so this is a broken build")
        pytest.skip(message)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_shipped_javascript_has_no_cross_function_helper_calls():
    output = _run("_js_scope_check.mjs")
    assert "JS HELPER SCOPE CHECK PASSED" in output


def test_exchange_rate_setting_load_and_save_run_without_reference_errors():
    output = _run("_exchange_rate_check.mjs")
    assert "EXCHANGE RATE BIND CHECKS PASSED" in output
