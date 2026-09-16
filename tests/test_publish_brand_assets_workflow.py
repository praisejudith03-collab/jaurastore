"""Safety checks for the manual Supabase brand-asset publishing path.

Render's free tier provides no shell, so the committed six-file brand ladder
is published by .github/workflows/publish-brand-assets.yml. These tests keep
that housekeeping path manual, secret-backed, repeatable, and unable to write
back to the repository.
"""
import os
import re

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "publish-brand-assets.yml")
BUCKET_SQL = os.path.join(ROOT, "create_public_assets_bucket.sql")
URL_SQL = os.path.join(ROOT, "set_brand_asset_urls.sql")


def _workflow():
    with open(WORKFLOW, encoding="utf-8") as fh:
        text = fh.read()
    data = yaml.safe_load(text)
    # PyYAML 5/6 treats an unquoted YAML 1.1 `on` as True.
    trigger = data.get("on", data.get(True))
    return text, data, trigger


def _publish_job(data):
    return data["jobs"]["publish"]


def _code(text):
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_workflow_is_manual_only():
    _text, _data, trigger = _workflow()
    assert list(trigger) == ["workflow_dispatch"]


def test_dry_run_is_the_default_and_publish_needs_confirmation():
    _text, _data, trigger = _workflow()
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["default"] == "true"
    assert inputs["dry_run"]["required"] is True
    assert inputs["confirm_publish"]["required"] is False


def test_job_is_bounded_protected_and_single_flight():
    _text, data, _trigger = _workflow()
    job = _publish_job(data)
    assert job["timeout-minutes"] > 0
    assert job["environment"]["name"] == "publish-brand-assets"
    assert data["concurrency"] == {
        "group": "publish-brand-assets",
        "cancel-in-progress": False,
    }


def test_workflow_has_read_only_repository_permissions():
    _text, data, _trigger = _workflow()
    assert data["permissions"] == {"contents": "read"}


def test_supabase_credentials_only_come_from_repository_secrets():
    text, data, _trigger = _workflow()
    env = _publish_job(data)["env"]
    assert env["SUPABASE_URL"] == "${{ secrets.SUPABASE_URL }}"
    assert env["SUPABASE_SERVICE_ROLE_KEY"] == "${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}"
    assert "eyJ" not in text
    assert not re.search(r"https://[a-z0-9-]+\.supabase\.co", text)


def test_service_key_is_masked_before_the_publish_script_runs():
    text, data, _trigger = _workflow()
    steps = _publish_job(data)["steps"]
    mask = next(i for i, step in enumerate(steps)
                if step.get("name") == "Mask the service-role key")
    run = next(i for i, step in enumerate(steps)
               if "tools/upload_brand_assets.py" in step.get("run", ""))
    assert mask < run
    assert "::add-mask::$SUPABASE_SERVICE_ROLE_KEY" in steps[mask]["run"]
    assert "echo \"$SUPABASE_SERVICE_ROLE_KEY\"" not in text


def test_publish_uses_committed_assets_without_rebuilding_them():
    _text, data, _trigger = _workflow()
    steps = _publish_job(data)["steps"]
    publish = next(step for step in steps
                   if step.get("name") == "Publish the committed brand assets")
    command = publish["run"]
    assert "tools/upload_brand_assets.py" in command
    assert "--no-build" in command
    assert "--dry-run" in command


def test_real_publish_is_gated_in_the_workflow():
    _text, data, trigger = _workflow()
    steps = _publish_job(data)["steps"]
    validate = next(step for step in steps
                    if step.get("name") == "Validate credentials for a real publish")
    assert validate["if"] == "inputs.dry_run == 'false'"
    assert "confirm_publish" in validate["run"]
    assert "PUBLISH" in validate["run"]
    assert "exit 1" in validate["run"]
    assert trigger["workflow_dispatch"]["inputs"]["confirm_publish"]["description"]


def test_bucket_sql_is_public_and_targets_only_public_assets():
    sql = open(BUCKET_SQL, encoding="utf-8").read().lower()
    assert "storage.buckets" in sql
    assert "'public-assets'" in sql
    assert "public = true" in sql
    assert "on conflict (id)" in sql
    assert "receipts" not in _code(sql)
    assert "uploads" not in _code(sql)


def test_bucket_sql_is_idempotent_without_deleting_objects():
    sql = open(BUCKET_SQL, encoding="utf-8").read().lower()
    assert "insert into" in sql and "on conflict" in sql
    assert "delete" not in _code(sql)
    assert "drop" not in _code(sql)
    assert "create bucket" not in _code(sql)


def test_bucket_sql_accepts_the_six_png_brand_assets():
    sql = open(BUCKET_SQL, encoding="utf-8").read().lower()
    assert "image/png" in sql
    assert "file_size_limit" in sql
    upload = open(os.path.join(ROOT, "tools", "upload_brand_assets.py"),
                  encoding="utf-8").read()
    for name in ("logo-square.png", "favicon-16.png", "favicon-32.png",
                 "favicon-48.png", "apple-touch-180.png", "icon-192.png"):
        assert name in upload


def test_site_settings_sql_writes_exactly_the_five_recorded_urls():
    sql = open(URL_SQL, encoding="utf-8").read().lower()
    columns = ("brand_logo_url", "favicon_32_url", "favicon_48_url",
               "apple_touch_icon_url", "icon_192_url")
    assert "update public.site_settings" in sql
    assert "where id = 1" in sql
    for column in columns:
        assert column in sql
    assert "favicon_16" not in _code(sql)


def test_site_settings_urls_match_the_public_assets_object_keys():
    sql = open(URL_SQL, encoding="utf-8").read()
    host = "https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/public-assets/brand/"
    for name in ("logo-square.png", "favicon-32.png", "favicon-48.png",
                 "apple-touch-180.png", "icon-192.png"):
        assert host + name in sql
    assert "service_role" not in sql.lower()


def test_workflow_checks_out_the_repository_before_publishing():
    _text, data, _trigger = _workflow()
    steps = _publish_job(data)["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    publish = next(i for i, step in enumerate(steps)
                   if "tools/upload_brand_assets.py" in step.get("run", ""))
    assert steps.index(checkout) < publish
    assert checkout["uses"] == "actions/checkout@v4"


def test_workflow_uses_a_pinned_python_setup():
    _text, data, _trigger = _workflow()
    setup = next(step for step in _publish_job(data)["steps"]
                 if step.get("uses", "").startswith("actions/setup-python@"))
    assert setup["uses"] == "actions/setup-python@v5"
    assert setup["with"]["python-version"] == "3.12"


def test_workflow_installs_the_declared_production_dependencies():
    _text, data, _trigger = _workflow()
    steps = _publish_job(data)["steps"]
    install = next(step for step in steps if step.get("name") == "Install dependencies")
    assert "pip install -r requirements.txt" in install["run"]
    assert "--user" not in install["run"]


def test_real_publish_does_not_disable_the_database_update():
    text, data, _trigger = _workflow()
    steps = _publish_job(data)["steps"]
    publish = next(step for step in steps
                   if "tools/upload_brand_assets.py" in step.get("run", ""))
    assert "--no-db" not in publish["run"]
    assert "site_settings" in text


def test_workflow_never_commits_or_pushes_repository_changes():
    text, data, _trigger = _workflow()
    code = _code(text).lower()
    assert "git push" not in code
    assert "git commit" not in code
    assert "contents: write" not in code
    assert "contents" in data["permissions"]



def test_bucket_migration_sets_a_reasonable_upload_limit():
    sql = open(BUCKET_SQL, encoding="utf-8").read().lower()
    assert "10485760" in sql
    assert "allowed_mime_types" in sql


def test_site_settings_sql_only_assigns_brand_columns():
    sql = open(URL_SQL, encoding="utf-8").read().lower()
    assignments = sql.split("set", 1)[1].split("where", 1)[0]
    for column in ("brand_logo_url", "favicon_32_url", "favicon_48_url",
                   "apple_touch_icon_url", "icon_192_url"):
        assert assignments.count(column) == 1
    assert "bank_name" not in assignments
    assert "payment" not in assignments


def test_site_settings_sql_contains_no_local_or_secret_reference():
    sql = open(URL_SQL, encoding="utf-8").read().lower()
    assert "service_role" not in sql
    assert "/uploads/" not in sql
    assert "file://" not in sql
    assert "public-assets/brand/" in sql
