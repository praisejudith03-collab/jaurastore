"""supabase_schema.sql: the products REPAIR block must heal every critical column.

Tonight's production bug: the create-table block already has "priceCfa" and
"priceNgn", but the repair block - the one that heals an EXISTING hand-built
narrower table - did not. Re-running the schema therefore left a table that
cannot hold the two price columns the app writes on every product save.

The repair block must cover every name in
supabase_store._CRITICAL_PRODUCT_COLUMNS: those are the columns a product
cannot exist without, which supabase_store refuses to drop from a write.
("id" is the primary key - it cannot be added to an existing table, so it is
covered by the create-table definition instead.)

Run with:  python3 -m pytest tests/test_supabase_schema.py -q
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import supabase_store  # noqa: E402

SCHEMA_PATH = os.path.join(ROOT, "supabase_schema.sql")


def _schema_text():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return fh.read()


def _create_table_columns(table="products"):
    m = re.search(
        r"create table if not exists " + table + r"\s*\((.*?)\n\);",
        _schema_text(), re.IGNORECASE | re.DOTALL)
    assert m, f"create table for {table} not found"
    cols = set()
    for line in m.group(1).splitlines():
        line = line.strip().rstrip(",").strip()
        if not line or line.startswith("--"):
            continue
        if re.match(r"^(constraint|primary|unique|foreign|check)\b", line, re.IGNORECASE):
            continue
        cols.add(line.split()[0].strip('"'))
    return cols


def _repair_columns(table="products"):
    return set(re.findall(
        r"alter table " + table + r"\s+add column if not exists\s+\"?(\w+)\"?",
        _schema_text(), re.IGNORECASE))


def test_repair_block_covers_every_critical_product_column():
    create_cols = _create_table_columns()
    repair_cols = _repair_columns()
    for col in sorted(supabase_store._CRITICAL_PRODUCT_COLUMNS):
        assert col in create_cols, \
            f"critical column {col!r} is missing from the create-table block"
        if col == "id":
            # the primary key cannot be added to an existing table; it must
            # at least be the create-table's primary key
            m = re.search(r"^\s*id\s+text primary key", _schema_text(),
                          re.IGNORECASE | re.MULTILINE)
            assert m, "id must be the products primary key in the create-table block"
            continue
        assert col in repair_cols, \
            f"critical column {col!r} is missing from the products repair block - " \
            "a narrower existing table would be left unable to hold it"


def _statements_only(sql):
    """The schema with every comment line removed (comments may quote the
    words the guard below forbids in real statements)."""
    return "\n".join(line for line in sql.splitlines()
                     if not line.strip().startswith("--"))


def test_repair_block_only_adds_columns():
    """Rule: never drop/truncate/delete/rename - the SQL adds only."""
    sql = _statements_only(_schema_text())
    for bad in (r"\bdrop\b", r"\btruncate\b", r"\brename\b", r"\bdelete\b"):
        m = re.search(bad, sql, re.IGNORECASE)
        assert not m, f"the schema file must never {bad.strip(chr(92))}: found {m.group(0)!r}"


def test_dead_snake_case_columns_are_documented_never_drop():
    """The legacy snake_case price/name/option columns are dead leftovers:
    the schema file must say so explicitly (never drop, never rename)."""
    sql = _schema_text()
    for col in ("price_cfa", "price_ngn", "name_fr", "compare_cfa",
                "compare_ngn", "option_stock"):
        assert col in sql, f"dead legacy column {col!r} is not documented"
    m = re.search(r"--[^#]*(price_cfa.*?)$", sql, re.DOTALL)
    assert m, "no comment block documents the dead snake_case columns"
    block = m.group(1)
    assert re.search(r"never\s+drop", block, re.IGNORECASE), \
        "the comment must say the dead columns must never be dropped"
    assert re.search(r"never\s+rename", block, re.IGNORECASE), \
        "the comment must say the dead columns must never be renamed"
