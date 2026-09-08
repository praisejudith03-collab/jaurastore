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

import pytest  # noqa: E402

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
    """Rule: never drop/truncate/delete - the SQL adds only.

    `rename` is the one exception, and a narrow one: product_reviews shipped
    first as stars/note/at and the agreed contract is rating/body/created_at.
    A column rename rewrites no data and drops no row, so it upgrades an
    existing database instead of rebuilding it. Every other rename stays
    banned, and each allowed one is pinned below so a fourth cannot slip in.
    """
    sql = _statements_only(_schema_text())
    for bad in (r"\bdrop\b", r"\btruncate\b", r"\bdelete\b"):
        m = re.search(bad, sql, re.IGNORECASE)
        assert not m, f"the schema file must never {bad.strip(chr(92))}: found {m.group(0)!r}"


def test_the_only_renames_are_the_guarded_review_column_moves():
    """Pins the carve-out above: exactly three renames, all on
    product_reviews, all guarded by an information_schema existence check so
    re-running the file cannot fail or double-apply."""
    sql = _schema_text()
    found = re.findall(r"rename column (\w+) to (\w+)", sql, re.IGNORECASE)
    assert sorted(found) == sorted([("stars", "rating"), ("note", "body"),
                                    ("at", "created_at")]), \
        f"unexpected column renames in the schema: {found}"
    for old_col, _new_col in found:
        guard = re.search(
            r"if exists\s*\(\s*select 1 from information_schema\.columns[^)]*"
            r"column_name = '" + old_col + r"'\s*\)", sql, re.IGNORECASE | re.DOTALL)
        assert guard, f"rename of {old_col!r} is not guarded by an existence check"


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


# ---------------------------------------------------------------------------
# The full table and column inventory the deployment depends on. Applying the
# schema is an acceptance condition, so "does the file define it" is testable
# here even though no database is reachable from the suite.
# ---------------------------------------------------------------------------

REQUIRED_TABLES = (
    "site_settings", "products", "categories", "orders", "receipts",
    "admin_users", "admin_reset_tokens", "coupons", "coupon_uses",
    "referral_codes", "referral_uses", "delivery_zones", "product_reviews",
)

REQUIRED_PRODUCT_COLUMNS = (
    "id", "legacyId", "name", "category", "priceNgn", "priceCfa",
    "compareNgn", "compareCfa", "image_url", "images", "stock_quantity",
    "description", "featured", "online", "updated_at",
)


@pytest.mark.parametrize("table", REQUIRED_TABLES)
def test_required_table_is_defined(table):
    m = re.search(r"create table (?:if not exists )?\"?" + re.escape(table) +
                  r"\"?\s*\(", _schema_text(), re.IGNORECASE)
    assert m, f"supabase_schema.sql does not create table {table!r}"


def test_products_supports_every_column_the_app_writes():
    create_cols = _create_table_columns("products")
    repair_cols = _repair_columns("products")
    have = create_cols | repair_cols
    missing = [c for c in REQUIRED_PRODUCT_COLUMNS if c not in have]
    assert not missing, (
        f"products is missing required column(s): {missing}. A narrower "
        "hand-built table must still be healable by re-running the schema.")


def test_legacy_id_is_a_real_column_not_just_an_alias():
    """Legacy wix-* links resolve through this, so it must be a column with a
    uniqueness rule - not something reconstructed at read time."""
    create_cols = _create_table_columns("products")
    assert "legacyId" in create_cols
    # The index is a PARTIAL one (`where "legacyId" is not null`) and spans two
    # lines, so match across the newline - a single-line pattern misses it.
    m = re.search(r"create\s+unique\s+index[\s\S]{0,120}?on\s+products\s*\(\s*\"legacyId\"\s*\)"
                  r"[\s\S]{0,80}?where\s+\"legacyId\"\s+is\s+not\s+null",
                  _schema_text(), re.IGNORECASE)
    assert m, ("no partial unique index on products.\"legacyId\" - without it "
               "two products could claim the same legacy wix-* alias")


def test_coupon_uses_makes_a_redemption_idempotent():
    """A retried order must not count the same coupon twice, so the log needs
    a unique (code, order_id) pair rather than a bare insert."""
    m = re.search(r"create table (?:if not exists )?coupon_uses\s*\((.*?)\n\);",
                  _schema_text(), re.IGNORECASE | re.DOTALL)
    assert m, "coupon_uses is not defined"
    body = m.group(1).lower()
    assert "unique (code, order_id)" in body.replace("  ", " ")


def test_product_reviews_enforces_one_per_customer_per_product():
    m = re.search(r"create table (?:if not exists )?product_reviews\s*\((.*?)\n\);",
                  _schema_text(), re.IGNORECASE | re.DOTALL)
    assert m, "product_reviews is not defined"
    body = m.group(1).lower()
    assert "unique (product_id, email)" in body.replace("  ", " ")
    assert "check (rating between 1 and 5)" in body.replace("  ", " ")


def test_product_reviews_has_the_agreed_column_contract():
    """The columns the migration and the API contract both depend on. Checked
    against the CREATE block so a missing column fails here, in CI, rather
    than as a 422 from PostgREST during the production dry-run."""
    m = re.search(r"create table (?:if not exists )?product_reviews\s*\((.*?)\n\);",
                  _schema_text(), re.IGNORECASE | re.DOTALL)
    assert m, "product_reviews is not defined"
    body = m.group(1)
    for col in ("product_id", "email", "name", "rating", "title", "body",
                "hidden", "created_at", "updated_at"):
        assert re.search(r"^\s*" + col + r"\b", body, re.MULTILINE), \
            f"product_reviews is missing the required column {col!r}"
    # the superseded names must be gone from the definition, or the table
    # carries two vocabularies and every reader has to guess
    for gone in ("stars", "note"):
        assert not re.search(r"^\s*" + gone + r"\b", body, re.MULTILINE), \
            f"product_reviews still defines the superseded column {gone!r}"


def test_coupon_uses_cannot_double_count_an_order():
    """The constraint the coupon idempotency depends on."""
    m = re.search(r"create table (?:if not exists )?coupon_uses\s*\((.*?)\n\);",
                  _schema_text(), re.IGNORECASE | re.DOTALL)
    assert m, "coupon_uses is not defined"
    assert "unique (code, order_id)" in m.group(1).lower().replace("  ", " ")


def test_delivery_zones_constrains_its_enums():
    """A bad currency or kind would price a delivery wrongly, so the database
    rejects it rather than trusting the caller."""
    m = re.search(r"create table (?:if not exists )?delivery_zones\s*\((.*?)\n\);",
                  _schema_text(), re.IGNORECASE | re.DOTALL)
    assert m, "delivery_zones is not defined"
    body = m.group(1).lower()
    assert "check (currency in ('cfa','ngn'))" in body.replace("  ", " ")
    assert "check (kind in ('delivery','pickup','quote'))" in body.replace("  ", " ")
    assert "check (fare_min >= 0)" in body.replace("  ", " ")


def test_the_schema_seeds_the_delivery_zones_idempotently():
    """Re-running the schema must not overwrite an admin's edited fares."""
    m = re.search(r"insert into delivery_zones[\s\S]*?on conflict \(id\) do nothing;",
                  _schema_text(), re.IGNORECASE)
    assert m, "delivery_zones seed is missing or is not idempotent"


def _delivery_zone_repair_columns():
    return set(re.findall(
        r"alter table delivery_zones\s+add column if not exists\s+\"?(\w+)\"?",
        _schema_text(), re.IGNORECASE))


def test_delivery_zones_repairs_an_older_table_before_using_new_columns():
    """Production failure: an existing delivery_zones table predating `active`
    made the index (and the seed's sort_order) fail with "column does not
    exist". The repair block must add every non-key column, add-only, and must
    come BEFORE anything that references those columns."""
    sql = _schema_text()
    repair = _delivery_zone_repair_columns()
    create_cols = _create_table_columns("delivery_zones")
    for col in sorted(create_cols - {"id"}):
        assert col in repair, \
            f"delivery_zones repair block is missing {col!r}"

    active_add = sql.lower().index(
        "alter table delivery_zones add column if not exists active")
    sort_add = sql.lower().index(
        "alter table delivery_zones add column if not exists sort_order")
    index_at = sql.lower().index("on delivery_zones(active, sort_order)")
    seed_at = sql.lower().index("insert into delivery_zones")
    assert active_add < index_at, "`active` is added after the index that uses it"
    assert sort_add < index_at, "`sort_order` is added after the index that uses it"
    assert max(active_add, sort_add) < seed_at, \
        "the seed runs before the repair block that adds its columns"


def test_delivery_zones_repair_never_destroys_existing_rows():
    """Existing ids, names, fares, active and sort_order values survive: the
    repair is ADD COLUMN only - no drop/truncate/delete/update/rename."""
    block = _schema_text().split("-- SECTION: delivery_zones", 1)[1]
    block = block.split("-- SECTION:", 1)[0]
    code = re.sub(r"--[^\n]*", "", block).lower()
    for bad in ("drop", "truncate", "delete", "update ", "rename",
                "create table delivery_zones"):
        assert bad not in code, \
            f"delivery_zones section must not contain {bad!r}"
    for statement in re.findall(r"alter table delivery_zones[^;]*;", code):
        assert "add column if not exists" in statement, \
            f"non additive alter on delivery_zones: {statement!r}"
