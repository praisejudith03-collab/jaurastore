"""CI pipeline contract (owner request 2026-10-05).

The build itself is now under test. Three separate incidents motivated this
module:

  * a commit was force-pushed after a failure, but the superseded run's
    "All jobs have failed" mail kept arriving, because every push started TWO
    builds (a `push` build and a `pull_request` build of the same SHA);
  * the four mobile price-wrapping measurements skipped silently wherever a
    browser was missing, so a run could be green while measuring nothing;
  * the command CI ran and the command a developer ran before pushing were
    two similar-looking strings in two files.

So the workflow must: trigger once per commit, cancel superseded builds, put
a hard timeout on the job, run `tools/ci_check.sh` (the same script a human
runs), demand a real browser, keep the failure report as an artefact, and
never contain an escape hatch that turns a red build green.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT_PATH = ROOT / "tools" / "ci_check.sh"


@pytest.fixture(scope="module")
def ci_text():
    return CI_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ci(ci_text):
    # PyYAML reads `on:` as the boolean True (YAML 1.1), hence the fallback.
    return yaml.safe_load(ci_text)


def _steps(ci):
    return {step.get("name"): step for step in ci["jobs"]["tests"]["steps"]}


def _on(ci):
    return ci.get(True) or ci["on"]


# --------------------------------------------------------------------------
# a) One commit, one build
# --------------------------------------------------------------------------
def test_a_commit_starts_exactly_one_build(ci):
    """`push` is limited to main, so a branch commit is built once (by its
    pull_request run) instead of twice. The duplicate build was what kept a
    stale failure notification alive after the fix had already been pushed."""
    on = _on(ci)
    assert "pull_request" in on
    assert on["push"] == {"branches": ["main"]}, (
        "push must be limited to main; an unrestricted push: double-builds "
        "every branch commit alongside its pull_request run")


def test_the_superseded_build_is_cancelled(ci):
    concurrency = ci["concurrency"]
    assert concurrency["cancel-in-progress"] is True, (
        "a force-push must cancel the run it replaced instead of leaving a "
        "doomed build (and its failure mail) behind")
    group = concurrency["group"]
    assert "github.ref" in group and "pull_request.number" in group, (
        "the concurrency group must separate pull requests from the main run")
    assert "github.workflow" in group, "the group must be namespaced per workflow"


def test_no_second_test_running_workflow():
    """ci.yml is the only workflow that may run pytest on push/PR: a second
    one is how a repo gets two contradictory notifications per commit."""
    runners = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if "pytest" not in text:
            continue
        data = yaml.safe_load(text)
        on = data.get(True) or data["on"]
        triggers = set(on) if isinstance(on, dict) else set(on)
        if triggers & {"push", "pull_request"}:
            runners.append(path.name)
    assert runners == ["ci.yml"], f"unexpected test-running workflows: {runners}"


# --------------------------------------------------------------------------
# b) The job cannot hang, cannot be skipped, cannot lie
# --------------------------------------------------------------------------
def test_the_job_has_a_hard_timeout(ci):
    timeout = ci["jobs"]["tests"].get("timeout-minutes")
    assert isinstance(timeout, int) and 0 < timeout <= 30, (
        "a hung test must fail the job in minutes, not burn the 6-hour default "
        "(which is itself reported as a failure)")


def test_the_workflow_uses_the_one_local_script(ci):
    """CI and the pre-push check must be the same command, not two strings."""
    step = _steps(ci).get("Run test suite")
    assert step, "the workflow has no 'Run test suite' step"
    assert step["run"].strip() == "bash tools/ci_check.sh --ci"
    assert step.get("env", {}).get("JA_REQUIRE_BROWSER") == "1", (
        "CI must demand a real browser, or the mobile measurements skip "
        "silently and the run goes green having measured nothing")


def test_the_local_script_runs_the_whole_suite():
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    assert SCRIPT_PATH.stat().st_mode & 0o111, "tools/ci_check.sh must be executable"
    assert "-m pytest tests/" in script, "the script must run the whole tests/ tree"
    assert "--junitxml=pytest-results.xml" in script, (
        "the script must write the same report the workflow uploads")
    # Inspect the pytest invocation itself: ` -x` also occurs in ordinary
    # shell like `[ -x "$ROOT/.venv-test/bin/python" ]`.
    invocation = script.split("python -m pytest tests/", 1)[1].split("\n", 1)[0]
    for flag in ("--maxfail", " -x", "--exitfirst"):
        assert flag not in invocation, (
            f"{flag} would stop at the first failure and hide the rest")
    # A missing interpreter must be a hard error, never a silent pass.
    assert "exit 2" in script
    subprocess.run(["bash", "-n", str(SCRIPT_PATH)], check=True)


def test_the_browser_requirement_cannot_be_quietly_dropped():
    """Both browser modules must fail (not skip) when CI demands a browser."""
    for name in ("tests/test_autopilot_stability.py", "tests/test_browser_smoke.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "JA_REQUIRE_BROWSER" in text, f"{name} ignores JA_REQUIRE_BROWSER"
        assert "pytest.skip" in text, f"{name} lost its local skip path"
        assert "pytest.fail" in text, (
            f"{name} would skip even when a browser is mandatory in CI")


@pytest.mark.parametrize("forbidden", [
    "continue-on-error",
    "|| true",
    "set +e",
    "--maxfail",
    " -x ",
    "|| exit 0",
])
def test_no_escape_hatch_turns_a_red_build_green(ci_text, forbidden):
    assert forbidden not in ci_text, (
        f"{forbidden!r} in ci.yml would let a failing suite report success")


# --------------------------------------------------------------------------
# c) Everything a green run needs is installed and reported
# --------------------------------------------------------------------------
def test_all_declared_dependencies_are_installed(ci):
    install = next(step for name, step in _steps(ci).items()
                   if name and name.startswith("Install dependencies"))
    run = install["run"]
    assert "requirements.txt" in run and "requirements-test.txt" in run, (
        "requirements-test.txt carries playwright/PyYAML/pgserver; without it "
        "modules silently skip")


def test_the_browser_is_installed_for_the_measurements(ci):
    assert any("playwright install" in (step.get("run") or "")
               and "chromium" in (step.get("run") or "")
               for step in ci["jobs"]["tests"]["steps"]), (
        "the Chromium measurements need `playwright install --with-deps chromium`")


def test_failures_are_readable_and_kept(ci):
    steps = _steps(ci)
    upload = next((step for name, step in steps.items()
                   if (name or "").startswith("Upload pytest report")), None)
    assert upload, "the junit report must be uploaded"
    assert upload.get("if") == "always()", "the report is most needed on failure"
    assert "pytest-results.xml" == upload.get("with", {}).get("path")
    reporter = next((step for name, step in steps.items()
                     if (name or "").startswith("Report pytest failures")), None)
    assert reporter, "pytest failures must be republished as annotations"
    assert reporter.get("if") == "failure()"
    assert "::error" in reporter["run"]


def test_the_workflow_is_read_only(ci):
    assert ci["permissions"] == {"contents": "read"}, (
        "CI only reads the repo; a test run must never be able to push")


def test_a_skipped_test_fails_the_ci_build(tmp_path):
    """The teeth behind 'green means everything ran'."""
    report = tmp_path / "report.xml"
    report.write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_x" name="test_ok" time="0.1"/>'
        '<testcase classname="tests.test_x" name="test_slow" time="0.1">'
        '<skipped message="Playwright chromium not available"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8")
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "ci_report.py"),
                           str(report), "--fail-on-skips"],
                          capture_output=True, text=True)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "tests.test_x::test_slow" in proc.stdout
    assert "1 skipped" in proc.stdout
    # ...and the same report is informational (exit 0) when skips are allowed,
    # which is how a developer with no browser installed still gets a verdict.
    ok = subprocess.run([sys.executable, str(ROOT / "tools" / "ci_report.py"), str(report)],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_a_failing_report_exits_nonzero(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_x" name="test_bad" time="0.1">'
        '<failure message="boom">assert 1 == 2</failure></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8")
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "ci_report.py"), str(report)],
                          capture_output=True, text=True)
    assert proc.returncode == 1


def test_the_script_audits_skips_when_dependencies_are_guaranteed():
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "tools/ci_report.py" in script, (
        "the run must be summarised and its skips audited")
    assert "--fail-on-skips" in script, "CI must refuse to call a skipped run green"
    assert 'if [ "$REQUIRE_BROWSER" = "1" ]' in script, (
        "the skip audit belongs to the environment where every dependency is "
        "installed; a browser-less laptop still gets a verdict")


def test_the_documented_pre_push_ritual_is_this_script():
    """The instruction the owner follows must name the command CI runs."""
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "bash tools/ci_check.sh" in script, "the script documents its own use"
    assert re.search(r"Exit status is pytest's", script), (
        "the script must state that only a fully green suite exits 0")
