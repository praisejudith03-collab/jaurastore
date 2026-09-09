"""Regression tests for older Supabase table shapes.

Each section that creates a table must heal an existing narrower table additively
before any index, constraint, function or seed that references the new columns.
The production bug was that CREATE TABLE IF NOT EXISTS alone does not add columns
to an existing table, so a hand-built or earlier table left the deployment
failing section-by-section with \"column does not exist\".

These tests pin the repair blocks so a future edit cannot drop them and reintroduce
the one-section-at-a-time failure mode. They check the SQL text, not a live
database, so they run in CI without credentials.

Run with: python3 -m pytest tests/test_legacy_repairs.py -q
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "supabase_schema.sql")

def _schema_text():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return fh.read()

def _section(name):
    text = _schema_text()
    # split at banners, return the section's raw SQL (including its banner)
    pattern = re.compile(r"^-- SECTION: ([a-z][a-z0-9_]*)\r?$", re.MULTILINE)
    banners = list(pattern.finditer(text))
    names = [m.group(1) for m in banners]
    starts = [0] + [m.start() for m in banners[1:]]
    ends = starts[1:] + [len(text)]
    for n, s, e in zip(names, starts, ends):
        if n == name:
            return text[s:e]
    raise AssertionError(f"section {name!r} not found")

def _create_columns(table):
    m = re.search(r"create table if not exists " + re.escape(table) + r"\s*\((.*?)\)\s*;", _schema_text(), re.IGNORECASE | re.DOTALL)
    assert m, f"create table for {table} not found"
    cols = set()
    for line in m.group(1).splitlines():
        line = line.strip().rstrip(",").strip()
        if not line or line.startswith("--"):
            continue
        if re.match(r"^(constraint|primary|unique|foreign|check)\b", line, re.IGNORECASE):
            continue
        # first token is column name, possibly quoted
        cols.add(line.split()[0].strip('"' ))
    return cols

def _repair_columns(table):
    return set(re.findall(r"alter table " + re.escape(table) + r"\s+add column if not exists\s+\"?(\w+)\"?", _schema_text(), re.IGNORECASE))

def _repair_columns_in_section(table, section_name):
    sec = _section(section_name)
    return set(re.findall(r"alter table " + re.escape(table) + r"\s+add column if not exists\s+\"?(\w+)\"?", sec, re.IGNORECASE))

# ---------------------------------------------------------------------------
# Section 12 coupon_uses - the recovered fix
# ---------------------------------------------------------------------------
def test_coupon_uses_repairs_an_older_table_before_indexes():
    sec = _section("coupon_redemptions")
    # required columns per task
    for col in ("code", "email", "order_id", "percent", "used_at"):
        assert re.search(r"alter table coupon_uses add column if not exists\s+\"?" + col + r"\"?\b", sec, re.IGNORECASE), \
            f"coupon_uses repair missing {col!r} in section 12"
    # code and order_id must be added nullable (no \"not null\") so legacy rows survive without invented values
    for col in ("code", "order_id"):
        m = re.search(r"alter table coupon_uses add column if not exists\s+\"?" + col + r"\"?\s+([^;]*);", sec, re.IGNORECASE)
        assert m, f"alter for {col} not found"
        assert "not null" not in m.group(1).lower(), f"{col} must stay nullable for legacy rows"
    # order: ALTERs before the indexes that use those columns
    low = sec.lower()
    # ensure each alter appears before the index creation
    code_pos = low.index("alter table coupon_uses add column if not exists code")
    order_pos = low.index("alter table coupon_uses add column if not exists order_id")
    used_at_pos = low.index("alter table coupon_uses add column if not exists used_at")
    idx_pos = low.index("create index if not exists coupon_uses_code on coupon_uses")
    uniq_pos = low.index("create unique index if not exists coupon_uses_code_order")
    assert code_pos < idx_pos and order_pos < idx_pos and used_at_pos < idx_pos, "repair must be before indexes"
    assert max(code_pos, order_pos, used_at_pos) < uniq_pos, "repair must be before unique index"
    # idempotency via unique index on (code, order_id)
    assert "create unique index if not exists coupon_uses_code_order" in low
    assert "on coupon_uses(code, order_id)" in low

def test_coupon_uses_repair_never_destroys_rows():
    sec = _section("coupon_redemptions")
    code = re.sub(r"--[^\n]*", "", sec).lower()
    for bad in ("drop", "truncate", "delete", "update ", "rename", "create table coupon_uses"):
        # allow the initial create table, but not a second one in this section
        if bad == "create table coupon_uses":
            # exactly one create table for coupon_uses in this section
            assert code.count("create table if not exists coupon_uses") == 1
            continue
        assert bad not in code, f"coupon_redemptions section must not contain {bad!r}"
    for stmt in re.findall(r"alter table coupon_uses[^;]*;", code):
        assert "add column if not exists" in stmt, f"non-additive alter on coupon_uses: {stmt!r}"

# ---------------------------------------------------------------------------
# Section 13 product_reviews - full column contract and legacy renames
# ---------------------------------------------------------------------------
def test_product_reviews_repairs_all_required_columns():
    sec = _section("product_reviews")
    # the agreed contract plus order_id
    for col in ("product_id", "order_id", "email", "name", "rating", "title", "body", "hidden", "created_at", "updated_at"):
        # either the CREATE defines it or the repair adds it
        has_create = col in _create_columns("product_reviews")
        has_repair = re.search(r"alter table product_reviews add column.*\b" + col + r"\b", sec, re.IGNORECASE) or \
                     re.search(r"column_name = '" + col + r"'", sec, re.IGNORECASE)
        assert has_create or has_repair, f"product_reviews missing {col!r} in section 13"
    # rating, title, body, hidden, created_at, updated_at must be supported
    low = sec.lower()
    # guarded renames must exist for legacy stars/note/at
    assert "rename column stars to rating" in low
    assert "rename column note to body" in low
    assert "rename column at to created_at" in low
    # repair DO block must be guarded by information_schema existence checks
    for old in ("stars", "note", "at"):
        assert re.search(r"if exists\s*\(.*column_name = '" + old + r"'", sec, re.IGNORECASE | re.DOTALL), \
            f"rename of {old!r} not guarded"

def test_product_reviews_repairs_before_indexes_and_preserves_unique_rule():
    sec = _section("product_reviews")
    low = sec.lower()
    # repair adds hidden/created_at etc before the product_reviews_pid index
    # find the last repair alter/do before indexes
    # the section has two DO blocks: renames then adds; indexes are at end
    pid_idx = low.index("create index if not exists product_reviews_pid")
    uniq_idx = low.index("create unique index if not exists product_reviews_product_email")
    # ensure at least one alter for hidden appears before pid index
    assert "add column hidden" in low
    assert low.index("add column hidden") < pid_idx
    assert low.index("add column hidden") < uniq_idx
    # preserve one-review-per-product/email rule
    assert "unique (product_id, email)" in low or "product_reviews_product_email" in low

def test_product_reviews_repair_never_deletes_blob():
    sec = _section("product_reviews")
    code = re.sub(r"--[^\n]*", "", sec).lower()
    assert "drop" not in code
    assert "truncate" not in code
    # delete is not used in this section (but is allowed elsewhere? check none here)
    assert "delete" not in code

# ---------------------------------------------------------------------------
# Section 14 delivery_seeds - idempotent, never overwrites Admin edits
# ---------------------------------------------------------------------------
def test_delivery_seeds_is_idempotent_and_preserves_admin_edits():
    sec = _section("delivery_seeds")
    low = sec.lower()
    assert "insert into delivery_zones" in low
    assert "on conflict do nothing" in low
    assert "do update" not in low, "seed must not overwrite Admin-edited fares"
    code = re.sub(r"--[^\n]*", "", sec).lower()
    # Match SQL keywords, not the pg_attribute field `attisdropped`.
    for bad in ("drop", "truncate", "delete", "update", "rename", "alter"):
        assert not re.search(r"\b" + bad + r"\b", code), \
            f"delivery_seeds must not contain {bad!r}"

# ---------------------------------------------------------------------------
# Section 15 storage - exactly one bucket
# ---------------------------------------------------------------------------
def test_storage_uses_exactly_one_bucket_and_keeps_policies_idempotent():
    src = _schema_text()
    # exactly one storage.buckets insert, for uploads (comments stripped for env var check)
    code_no_comments = re.sub(r"--[^\n]*", "", src).lower()
    inserts = re.findall(r"insert into storage\.buckets.*?;", src, re.S | re.I)
    assert len(inserts) == 1
    assert "values ('uploads', 'uploads', true)" in inserts[0].lower()
    assert "bucket_id = 'receipts'" not in code_no_comments
    assert "receipts" not in inserts[0].lower() or "uploads" in inserts[0].lower()
    # bucket insert must be ON CONFLICT DO NOTHING (not changing visibility)
    assert "on conflict (id) do nothing" in inserts[0].lower()
    # policies are idempotent via DO exception when duplicate_object
    assert 'create policy "public read uploads"' in src
    assert 'create policy "service role writes uploads"' in src
    # no SUPABASE_PRIVATE_BUCKET handling in actual statements (comments may mention it)
    assert "supabase_private_bucket" not in code_no_comments
    # does not delete or modify remote buckets/objects
    sec = _section("storage")
    code = re.sub(r"--[^\n]*", "", sec).lower()
    assert "drop" not in code
    assert "delete" not in code

def test_receipts_table_still_exists():
    assert "create table if not exists receipts (" in _schema_text().lower()
    assert "create table if not exists receipts (".lower() in _schema_text().lower()

# ---------------------------------------------------------------------------
# Section 16 stock - functions safely with products columns
# ---------------------------------------------------------------------------
def test_stock_functions_are_safe_and_columns_exist_before_them():
    sec = _section("stock")
    low = sec.lower()
    # required products columns are ensured before functions
    assert "alter table products add column if not exists stock_quantity" in low
    assert "alter table products add column if not exists stock" in low
    assert "alter table products add column if not exists online" in low
    # order: alters before functions
    stock_add = low.index("alter table products add column if not exists stock_quantity")
    func_pos = low.index("create or replace function reserve_product_stock")
    assert stock_add < func_pos
    # functions are atomic and idempotent: single UPDATE with guard, return boolean, security definer
    assert "create or replace function reserve_product_stock" in low
    assert "create or replace function release_product_stock" in low
    assert "security definer" in low
    # no stock overwrite during setup
    code = re.sub(r"--[^\n]*", "", sec).lower()
    assert "drop" not in code
    assert "truncate" not in code
    assert "delete" not in code

# ---------------------------------------------------------------------------
# Re-audit sections 01-12 - every section adds missing non-key columns before indexes
# ---------------------------------------------------------------------------
def test_orders_repairs_before_indexes():
    sec = _section("orders")
    for col in ("email", "customer_name", "phone", "zone", "proof_url", "payload", "status"):
        assert re.search(r"alter table orders add column if not exists\s+" + col + r"\b", sec, re.IGNORECASE), f"orders repair missing {col}"
    low = sec.lower()
    assert low.index("alter table orders add column if not exists email") < low.index("create index if not exists idx_orders_at")

def test_receipts_repairs_before_indexes():
    sec = _section("receipts")
    for col in ("order_id", "file_url", "email", "file_type", "created_at"):
        assert re.search(r"alter table receipts add column if not exists\s+" + col + r"\b", sec, re.IGNORECASE), f"receipts repair missing {col}"
    low = sec.lower()
    assert low.index("alter table receipts add column if not exists order_id") < low.index("create index if not exists idx_receipts_order")

def test_referrals_repairs_before_indexes():
    sec = _section("referrals")
    assert re.search(r"alter table referral_codes add column if not exists\s+email\b", sec, re.IGNORECASE)
    assert re.search(r"alter table referral_uses add column if not exists\s+code\b", sec, re.IGNORECASE)
    low = sec.lower()
    assert low.index("alter table referral_codes add column if not exists email") < low.index("create index if not exists idx_referral_email")
    assert low.index("alter table referral_uses add column if not exists code") < low.index("create index if not exists idx_referral_uses_code")

def test_coupons_repairs_missing_columns():
    sec = _section("coupons")
    for col in ("percent", "kind", "active", "max_uses", "uses"):
        assert re.search(r"alter table coupons add column if not exists\s+" + col + r"\b", sec, re.IGNORECASE), f"coupons repair missing {col}"

def test_categories_repairs_before_anything():
    sec = _section("categories")
    for col in ("name", "name_fr", "image_url", "hidden", "updated_at"):
        assert re.search(r"alter table categories add column if not exists\s+" + col + r"\b", sec, re.IGNORECASE), f"categories repair missing {col}"

def test_site_settings_repairs_all_core_columns():
    sec = _section("site_settings")
    for col in ("bank_name", "account_number", "referral_commission_percentage", "hero_banner_title", "contact_email", "site_logo_url", "updated_at"):
        assert re.search(r"alter table site_settings add column if not exists\s+" + col + r"\b", sec, re.IGNORECASE), f"site_settings repair missing {col}"


def test_growth_settings_repairs_value():
    sec = _section("growth_settings")
    assert re.search(r"alter table growth_settings add column if not exists\s+value\b", sec, re.IGNORECASE)

def test_all_sections_are_add_only():
    src = _schema_text()
    # strip comments then check for destructive keywords
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("--")).lower()
    # rename is allowed only for product_reviews stars/note/at - already pinned elsewhere
    # we allow those three renames, but no other rename should exist
    renames = re.findall(r"rename column (\w+) to (\w+)", code, re.IGNORECASE)
    allowed = {("stars","rating"), ("note","body"), ("at","created_at")}
    for r in renames:
        assert tuple(map(str.lower, r)) in {(a.lower(), b.lower()) for a,b in allowed}, f"unexpected rename {r}"
    # ensure no drop/truncate/delete in statements
    for bad in (r"\bdrop\b", r"\btruncate\b", r"\bdelete\b"):
        assert not re.search(bad, code), f"destructive {bad} found in schema"
