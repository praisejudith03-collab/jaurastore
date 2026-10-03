"""The staging migration/verification tools refuse what they must refuse.

`tools/apply_staging_schema.py` and `tools/staging_delete_check.py` are the
only code in the repository that can run DDL against a live Supabase project,
so their guard rails are tested here rather than trusted:

  * the approved file list is exactly the three files the runbook names, and
    nothing on hold (supabase_schema.sql, sections 15/16,
    inventory_guardrails.sql, the image migration) can be reached through it;
  * a project ref that does not match SUPABASE_URL is refused before any
    connection is made;
  * a project whose public.products already holds rows is refused unless the
    operator says otherwise;
  * a run with only the service-role key (no DDL credential) is refused with
    the explanation that PostgREST does not expose DDL;
  * the workflow that drives them applies the approved tool and nothing else.

No network and no credentials are used: every path exercised here returns
before a connection would be opened.
"""
import importlib.util
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load(name, relative):
    """Import a tools/ script without leaving it on sys.modules."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def apply_tool():
    return _load("apply_staging_schema", "tools/apply_staging_schema.py")


@pytest.fixture(scope="module")
def delete_tool():
    return _load("staging_delete_check", "tools/staging_delete_check.py")


def _clear_credentials(monkeypatch):
    for name in ("SUPABASE_URL", "SUPABASE_DB_URL", "SUPABASE_ACCESS_TOKEN",
                 "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------- the approved file list
def test_only_the_approved_files_can_be_applied(apply_tool):
    names = [p.name for p in apply_tool.APPROVED_FILES]
    assert names == ["01_products.sql", "09_product_compatibility.sql",
                     "hard_delete_products.sql"], names
    for path in apply_tool.APPROVED_FILES:
        assert path.exists(), path


def test_the_tool_refuses_a_file_that_is_not_approved(apply_tool):
    """Even a caller that passes the whole schema file is refused."""
    for held in ("supabase_schema.sql", "inventory_guardrails.sql",
                 "schema_sections/15_storage.sql", "schema_sections/16_stock.sql"):
        with pytest.raises(apply_tool.ApplyFailed, match="not in APPROVED_FILES"):
            apply_tool.apply_files(object(), [ROOT / held], log=lambda *_a: None)


def test_project_ref_is_read_from_the_supabase_url(apply_tool):
    assert apply_tool.project_ref_from_url(
        "https://rvkweyipqgsggcnimhxf.supabase.co") == "rvkweyipqgsggcnimhxf"
    assert apply_tool.project_ref_from_url("https://example.com") == ""
    assert apply_tool.project_ref_from_url("") == ""


# ------------------------------------------------------------ refusals first
def test_dry_run_needs_no_credentials_and_touches_nothing(apply_tool, monkeypatch, capsys):
    _clear_credentials(monkeypatch)
    assert apply_tool.main(["--confirm-project-ref", "x", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run: nothing was read from or written" in out
    for name in ("01_products.sql", "09_product_compatibility.sql",
                 "hard_delete_products.sql"):
        assert name in out
    assert "supabase_schema.sql" in out          # named as NOT applied


def test_a_mismatched_project_ref_is_refused(apply_tool, monkeypatch, capsys):
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", "https://staging-ref.supabase.co")
    rc = apply_tool.main(["--confirm-project-ref", "some-other-ref",
                          "--apply"])
    assert rc == 2
    assert "does not match the project in SUPABASE_URL" in capsys.readouterr().err


def test_service_role_key_alone_cannot_apply_ddl(apply_tool, monkeypatch, capsys):
    """PostgREST has no DDL: without a DB URL or Management token, refuse."""
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", "https://staging-ref.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "not-a-ddl-credential")
    rc = apply_tool.main(["--confirm-project-ref", "staging-ref", "--apply"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "SUPABASE_DB_URL" in err and "SUPABASE_ACCESS_TOKEN" in err
    assert "PostgREST" in err


# ------------------------------------------------------- preflight behaviour
class _FakeTransport:
    """Canned answers, no connection; records the SQL it was asked."""

    def __init__(self, products=True, rows=3, function=None, tables=()):
        self.calls = []
        self.products = products
        self.rows = rows
        self.function = function
        self.tables = list(tables)

    def query(self, sql):
        self.calls.append(sql)
        if "to_regclass('public.products')" in sql:
            return ["public.products"] if self.products else [None]
        if "to_regprocedure" in sql:
            return [self.function]
        if "information_schema.tables" in sql:
            return [json.dumps(self.tables)]
        if "count(*)" in sql:
            return [str(self.rows)]
        return []


def test_a_populated_products_table_is_refused_by_default(apply_tool):
    transport = _FakeTransport(rows=258)
    with pytest.raises(apply_tool.Refused, match="already holds 258 row"):
        apply_tool.preflight(transport, allow_existing_rows=False)
    # ...and it only ever read
    for sql in transport.calls:
        assert sql.strip().lower().startswith("select"), sql


def test_allow_existing_rows_lets_an_operator_proceed(apply_tool):
    transport = _FakeTransport(rows=258)
    report = apply_tool.preflight(transport, allow_existing_rows=True)
    assert report["products_rows"] == 258 and report["products_table"] == "public.products"


def test_preflight_on_an_empty_project_reports_missing_products(apply_tool):
    transport = _FakeTransport(products=False, tables=("orders", "receipts"))
    report = apply_tool.preflight(transport, allow_existing_rows=False)
    assert report["products_table"] is None
    assert report["products_rows"] == 0
    assert report["table_names"] == ["orders", "receipts"]


# --------------------------------------------------- the live delete checker
def test_delete_check_dry_run_is_offline_and_names_its_own_footprint(
        delete_tool, monkeypatch, capsys):
    _clear_credentials(monkeypatch)
    assert delete_tool.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "jau-staging-check-" in out            # a disposable id, never a real one
    assert "products/staging-checks/" in out
    assert "nothing was read from or written" in out


def test_delete_check_refuses_without_server_credentials(delete_tool, monkeypatch, capsys):
    _clear_credentials(monkeypatch)
    rc = delete_tool.main(["--confirm-project-ref", "staging-ref"])
    assert rc == 2
    assert "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY" in capsys.readouterr().err


def test_delete_check_refuses_a_mismatched_project_ref(delete_tool, monkeypatch, capsys):
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", "https://staging-ref.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "not-a-real-key")
    rc = delete_tool.main(["--confirm-project-ref", "production-ref"])
    assert rc == 2
    assert "does not match the project in SUPABASE_URL" in capsys.readouterr().err


def test_delete_check_only_targets_its_own_named_objects(delete_tool):
    """The tool's footprint is one uuid-named row and one uuid-named object."""
    source = (ROOT / "tools" / "staging_delete_check.py").read_text()
    assert 'pid = f"jau-staging-check-{uuid.uuid4().hex[:12]}"' in source
    assert 'products/staging-checks/' in source
    # It deletes through the app's own function, so the purge semantics are the
    # shipped ones rather than a second implementation.
    assert "supabase_store.hard_delete_products([pid])" in source


# ------------------------------------------------------------- the workflow
def test_the_workflow_runs_the_approved_tool_and_nothing_held():
    import yaml

    path = (ROOT / ".github" / "workflows" / "staging-schema-migration.yml")
    workflow = yaml.safe_load(path.read_text())
    # PyYAML reads the bare `on:` key as the boolean True (YAML 1.1).
    triggers = workflow.get("on") or workflow.get(True) or {}
    assert "workflow_dispatch" in triggers
    assert "push" not in triggers, "this must never run on a push"

    runs = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            runs.append(step.get("run", ""))
    joined = "\n".join(runs)
    assert "tools/apply_staging_schema.py" in joined
    assert "tools/staging_delete_check.py" in joined
    assert "tools/split_schema.py --check" in joined
    # Nothing held may be executed by the workflow.
    for forbidden in ("supabase_schema.sql", "inventory_guardrails.sql",
                      "migrate_images.py", "15_storage.sql", "16_stock.sql"):
        assert forbidden not in joined, forbidden
    # The live check is opt-in and gated on an actual apply.
    gate = [s.get("if", "") for job in workflow["jobs"].values()
            for s in job.get("steps", []) if "staging_delete_check" in s.get("run", "")]
    assert gate and "inputs.apply == 'true'" in gate[0]
    assert "inputs.live_purge_check == 'true'" in gate[0]
