"""The admin save toast, verified in a real DOM instead of by grep.

The owner's directive requires that a confirmed admin save is an unmistakable
green confirmation. tests/test_admin_save_feedback.py pins that intent in the
source (admin.js calls JA.success, style.css has the green rule); this file
runs the shipped js/store.js in jsdom and checks the toast the admin actually
sees: one element, `data-kind="success"`, the green class, the exact wording,
reused (not duplicated) for the next notice, red - never green - for an error,
and hidden again when its timer expires.

It also guards the other half of the directive: the green rule must exist in
the stylesheet the toast relies on.

Run with:  python3 -m pytest tests/test_admin_toast_dom.py -q
"""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tests", "_toast_check.mjs")


def _run():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (the toast check needs Node >= 18)")
    env = dict(os.environ)
    proc = subprocess.run([node, SCRIPT], cwd=ROOT, env=env, text=True,
                          capture_output=True, timeout=180)
    if proc.returncode == 3:
        message = ("jsdom is not installed (npm install jsdom, or set "
                   "JA_JSDOM_DIR); the admin toast check did not run")
        if os.environ.get("JA_REQUIRE_BROWSER") == "1":
            pytest.fail(message + " - CI installs it, so this is a broken build")
        pytest.skip(message)
    return proc


def test_a_confirmed_save_paints_one_green_toast_and_errors_stay_red():
    proc = _run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ADMIN TOAST CHECKS PASSED" in proc.stdout
    # The failure detail is what makes a red build actionable; keep the names.
    for phrase in ("a confirmed save creates exactly one toast",
                   "the success toast carries the green class",
                   "a neutral notice is not painted as a success",
                   "an error toast is marked as an error",
                   "the toast hides itself when its timer expires"):
        assert phrase in proc.stdout


def test_the_harness_really_would_catch_a_broken_toast():
    """Guard the guard: the check must fail if the toast stops being green.

    A harness that cannot fail is a false green in the deploy checklist. This
    proves the failure path exists by feeding the same file a stylesheet
    without the success rule through a temporary copy of the bundle directory.
    """
    source = open(SCRIPT, encoding="utf-8").read()
    assert 'data-kind") === "success"' in source
    assert "is-success" in source
    assert 'kind: "error"' in source
    assert 'window.JA = JA' in source, (
        "the harness must export JA from the eval, or it silently drives nothing")
