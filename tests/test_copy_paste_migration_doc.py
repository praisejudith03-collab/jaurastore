"""MIGRATION_COPY_PASTE.md must be the repository's SQL, not a retyped copy.

The document is what the operator actually pastes into the Supabase SQL
editor, from a phone, against a project this build sandbox cannot reach. So
the only safe form for it is a generated one:

  * every SQL block comes from a file (or a constant) in this repository,
  * ``--check`` fails the moment the document and the files disagree,
  * the SQL is parsed by PostgreSQL's own grammar (pglast), not by a regex,
  * Query 4 - the RLS lock - is exactly the text the database tests execute.

``tests/test_hard_delete_sql.py`` runs the blocks against a real PostgreSQL;
this file checks the document's contents and its drift guard.
"""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DOC = ROOT / "MIGRATION_COPY_PASTE.md"


def _load(name, relative):
    """Import a tools/ script without leaving it on sys.modules."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return _load("make_copy_paste_doc", "tools/make_copy_paste_doc.py")


def _sql_blocks(text):
    """Every fenced ```sql block in the document, in order."""
    blocks = []
    current = None
    for line in text.splitlines():
        if current is None:
            if line.strip() == "```sql":
                current = []
            continue
        if line.strip() == "```":
            blocks.append("\n".join(current))
            current = None
            continue
        current.append(line)
    assert current is None, "unclosed ```sql fence in the document"
    return blocks


# --------------------------------------------------- generated, not written
def test_the_document_is_exactly_what_the_generator_makes(tool):
    assert DOC.exists(), "run tools/make_copy_paste_doc.py"
    assert DOC.read_text() == tool.render(), (
        "MIGRATION_COPY_PASTE.md is stale: run tools/make_copy_paste_doc.py "
        "(never hand-edit it)")


def test_it_carries_the_three_approved_files_verbatim(tool):
    doc = DOC.read_text()
    files = tool.approved_files()
    assert [name for name, _text in files] == [
        "schema_sections/01_products.sql",
        "schema_sections/09_product_compatibility.sql",
        "hard_delete_products.sql",
    ]
    for name, text in files:
        assert text in doc, f"{name} is not in the document verbatim"
        assert name in doc, f"{name} is not named in the document"


def test_the_approved_list_comes_from_the_tool_that_executes_it(tool):
    """One list, not two: the runner and the document cannot disagree."""
    apply_tool = _load("apply_staging_schema", "tools/apply_staging_schema.py")
    assert [p.name for p in apply_tool.APPROVED_FILES] == [
        name.split("/")[-1] for name, _text in tool.approved_files()]


# ------------------------------------------------------------- the drift guard
def test_check_mode_passes_on_the_repository_document(tool, capsys):
    assert tool.main(["--check"]) == 0
    assert "MIGRATION_COPY_PASTE.md matches the 3 approved SQL files" in capsys.readouterr().out


def test_check_mode_fails_when_a_file_changed_under_it(tool, tmp_path, capsys):
    """Simulates the repository moving on after the document was generated."""
    copy = tmp_path / "MIGRATION_COPY_PASTE.md"
    changed = tool.render().replace("product_variants", "product_variantz", 1)
    copy.write_text(changed)
    assert tool.main(["--check", "--doc", str(copy)]) == 1
    err = capsys.readouterr().err
    assert "does not match the 3 approved SQL files" in err
    assert "make_copy_paste_doc.py" in err


def test_check_mode_fails_on_a_hand_edited_document(tool, tmp_path, capsys):
    copy = tmp_path / "MIGRATION_COPY_PASTE.md"
    copy.write_text(tool.render() + "\nPasted from an old chat message.\n")
    assert tool.main(["--check", "--doc", str(copy)]) == 1
    assert "stale" in capsys.readouterr().err


def test_check_mode_reports_a_missing_document(tool, tmp_path, capsys):
    assert tool.main(["--check", "--doc", str(tmp_path / "nope.md")]) == 1
    assert "missing" in capsys.readouterr().err


# ------------------------------------------------------------- Query 4 itself
def test_query_4_is_the_text_the_document_publishes(tool):
    assert tool.QUERY_4_SQL.rstrip("\n") in DOC.read_text()


def test_query_4_locks_only_the_three_child_tables(tool):
    sql = tool.QUERY_4_SQL
    locked = [line for line in sql.splitlines()
              if line.strip().startswith("alter table")
              and "enable row level security" in line]
    assert len(locked) == 3, locked
    for table in ("product_variants", "product_prices", "product_options"):
        assert f"alter table public.{table} enable row level security;" in sql
    # The one table that must never be locked here.
    assert "alter table public.products" not in sql
    assert "public.products enable row level security" not in sql


def test_the_document_never_enables_rls_on_products(tool):
    """The storefront's live stock feeds on an anon-key Realtime subscription
    on public.products; RLS without a policy would stop those events."""
    doc = DOC.read_text()
    assert "public.products enable row level security" not in doc
    for block in _sql_blocks(doc):
        assert "public.products enable row level security" not in block


def test_every_sql_block_in_the_document_parses_as_postgres(tool):
    pglast = pytest.importorskip("pglast")
    for index, block in enumerate(_sql_blocks(DOC.read_text()), start=1):
        statements = pglast.parse_sql(block)
        assert statements, f"block {index} is empty"


def test_the_read_only_checks_do_not_write(tool):
    """The pre-checks and the acceptance check run on a project that may still
    hold real data: they may only read."""
    doc = DOC.read_text()
    for block in (tool.PREFLIGHT_PROJECT_SQL, tool.PREFLIGHT_TABLES_SQL,
                  tool.ACCEPTANCE_SQL, tool.RPC_PROBE_SQL):
        lowered = block.lower()
        for forbidden in ("insert ", "update ", "delete ", "drop ",
                          "alter ", "create ", "grant ", "revoke "):
            assert forbidden not in lowered, f"read-only block writes: {block[:80]}"
        assert block.rstrip("\n") in doc, "a read-only check is not in the document"
