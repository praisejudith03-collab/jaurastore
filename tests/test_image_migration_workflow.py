"""Blocker 9 - the image migration runs only from a protected manual workflow.

This sandbox has no SUPABASE_URL and no SUPABASE_SERVICE_ROLE_KEY (`gh secret
list` returns HTTP 403), so the migration cannot be run here and must not be
faked. The sanctioned path is .github/workflows/image-migration.yml, which
holds the key only as a repository secret.

These tests pin the properties that keep that file safe. They parse the YAML
rather than grepping it, so a change that quietly weakens a gate fails here.

Run with:  python3 -m pytest tests/test_image_migration_workflow.py -q
"""
import os
import re
import subprocess
import sys

import pytest

# The distribution is "pyyaml" but the import name is "yaml" - importorskip
# takes the import name, so passing the distribution name skipped every test.
yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github", "workflows", "image-migration.yml")


@pytest.fixture(scope="module")
def wf():
    with open(WF, encoding="utf-8") as fh:
        text = fh.read()
    data = yaml.safe_load(text)
    # PyYAML parses a bare `on:` as the boolean True.
    return text, data, data.get("on", data.get(True))


def _job(wf):
    return wf[1]["jobs"]["migrate"]


# --------------------------------------------------------------------------
# it is manual and protected
# --------------------------------------------------------------------------

def test_the_workflow_is_manual_only(wf):
    triggers = list(wf[2])
    assert triggers == ["workflow_dispatch"], triggers


def test_dry_run_defaults_to_true(wf):
    dry = wf[2]["workflow_dispatch"]["inputs"]["dry_run"]
    assert dry["default"] == "true"
    assert dry["required"] is True


def test_the_job_requires_a_protected_environment(wf):
    """Reviewers are configured on this environment in the repo settings."""
    assert _job(wf)["environment"]["name"] == "image-migration"


def test_two_runs_cannot_race(wf):
    conc = wf[1]["concurrency"]
    assert conc["group"] == "image-migration"
    # Cancelling in progress could kill a run mid-upload.
    assert conc["cancel-in-progress"] is False


def test_the_job_cannot_write_to_the_repo(wf):
    """It updates Supabase, not Git - so contents must stay read-only."""
    perms = wf[1]["permissions"]
    assert perms["contents"] == "read"
    assert "write" not in str(perms)


def test_only_the_uploads_bucket_is_ever_used(wf):
    assert _job(wf)["env"]["SUPABASE_BUCKET"] == "uploads"
    # Strip comments first: prose explaining the rule legitimately mentions the
    # other bucket. What matters is that no EXECUTABLE line selects it.
    code = "\n".join(ln.split("#", 1)[0] for ln in wf[0].splitlines())
    assert "receipts" not in code, (
        "an executable line selects the 'receipts' bucket")
    assert "--bucket receipts" not in wf[0]


# --------------------------------------------------------------------------
# no credential can reach the file, the log, or an artifact
# --------------------------------------------------------------------------

def test_no_credential_value_is_hardcoded(wf):
    text = wf[0]
    # A Supabase service-role key is a JWT; its payload carries service_role.
    assert "eyJ" not in text, "a JWT literal appears in the workflow"
    assert '"service_role"' not in text and "'service_role'" not in text
    assert not re.search(r"supabase\.co", text.replace(
        "https://<project-ref>.supabase.co", "")), "a real project URL is in the file"


def test_credentials_arrive_only_through_secrets(wf):
    env = _job(wf)["env"]
    assert env["SUPABASE_URL"] == "${{ secrets.SUPABASE_URL }}"
    assert env["SUPABASE_SERVICE_ROLE_KEY"] == "${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}"


def test_the_key_is_masked_before_any_step_can_log_it(wf):
    steps = _job(wf)["steps"]
    mask_idx = next(i for i, s in enumerate(steps)
                    if s.get("name") == "Mask the service-role key")
    run_idx = next(i for i, s in enumerate(steps)
                   if "migrate_images.py" in str(s.get("run", "")))
    assert mask_idx < run_idx, "the key must be masked before the script runs"
    assert "::add-mask::$SUPABASE_SERVICE_ROLE_KEY" in steps[mask_idx]["run"]


def test_the_credential_step_never_prints_the_key(wf):
    steps = _job(wf)["steps"]
    check = next(s for s in steps
                 if s.get("name") == "Fail fast if credentials are missing")
    run = check["run"]
    # It reports the URL host and the key LENGTH only.
    assert "${#SUPABASE_SERVICE_ROLE_KEY}" in run
    forbidden = [
        'echo "$SUPABASE_SERVICE_ROLE_KEY"',
        "echo $SUPABASE_SERVICE_ROLE_KEY",
        'printenv SUPABASE_SERVICE_ROLE_KEY',
        "cat $SUPABASE_SERVICE_ROLE_KEY",
    ]
    for f in forbidden:
        assert f not in run, f"the credential step prints the key: {f}"


# --------------------------------------------------------------------------
# the apply gate
# --------------------------------------------------------------------------

def test_a_real_run_needs_both_flags(wf):
    steps = _job(wf)["steps"]
    gate = next(s for s in steps
                if s.get("name") == "Refuse a real run that was not explicitly confirmed")
    assert gate["if"] == "inputs.dry_run == 'false' && inputs.confirm_apply != 'APPLY'"
    assert "exit 1" in gate["run"]


def test_the_apply_step_repeats_both_conditions(wf):
    steps = _job(wf)["steps"]
    apply_step = next(s for s in steps
                      if s.get("name") == "Apply (uploads + image_url writes)")
    assert apply_step["if"] == (
        "inputs.dry_run == 'false' && inputs.confirm_apply == 'APPLY'")


def test_a_dry_run_always_happens(wf):
    """Even when applying, the pre-flight plan must be produced."""
    steps = _job(wf)["steps"]
    dry = next(s for s in steps if s.get("name") == "Dry-run plan (always, zero writes)")
    assert "--dry-run" in dry["run"]
    assert not dry.get("if"), "the dry-run step must not be conditional"


def test_the_apply_is_blocked_when_the_plan_is_unsafe(wf):
    steps = _job(wf)["steps"]
    names = [s.get("name") for s in steps]
    block_i = names.index("Block the apply when the plan is not safe")
    apply_i = names.index("Apply (uploads + image_url writes)")
    assert block_i < apply_i
    block = steps[block_i]
    assert block["if"] == "inputs.dry_run == 'false'"
    assert "safe_to_apply" in block["run"]
    assert "sys.exit(1)" in block["run"]


def test_the_report_is_published_even_when_the_job_fails(wf):
    steps = _job(wf)["steps"]
    pub = next(s for s in steps if s.get("name") == "Publish the dry-run report")
    assert pub["if"] == "always()"
    assert pub["with"]["path"] == "dry-run-report.json"
    assert pub["with"]["if-no-files-found"] == "error"


def test_the_apply_verifies_the_saved_public_url(wf):
    steps = _job(wf)["steps"]
    apply_step = next(s for s in steps
                      if s.get("name") == "Apply (uploads + image_url writes)")
    assert "--http-check" in apply_step["run"], (
        "the apply must HEAD the saved URL to confirm it resolves")


def test_the_workflow_never_deletes_local_images(wf):
    text = wf[0]
    for bad in ("rm -rf images", "rm -f images", "shutil.rmtree", "git rm"):
        assert bad not in text, f"the workflow deletes local files: {bad}"


# --------------------------------------------------------------------------
# the script it calls actually supports every flag used
# --------------------------------------------------------------------------

def test_every_flag_the_workflow_passes_exists():
    help_text = subprocess.run(
        [sys.executable, os.path.join(ROOT, "migrate_images.py"), "--help"],
        capture_output=True, text=True, check=True, cwd=ROOT).stdout
    used = {"--dry-run", "--report", "--bucket", "--limit", "--live-only",
            "--http-check"}
    missing = [f for f in used if f not in help_text]
    assert not missing, f"migrate_images.py does not accept: {missing}"


def test_the_workflow_file_is_valid_yaml(wf):
    data = wf[1]
    assert data["name"]
    assert set(data["jobs"]) == {"migrate"}
    assert data["jobs"]["migrate"]["runs-on"] == "ubuntu-latest"
