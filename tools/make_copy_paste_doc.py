#!/usr/bin/env python3
"""Generate MIGRATION_COPY_PASTE.md - the phone-sized migration runbook.

The operator runs this migration from a phone, pasting each step into the
Supabase SQL editor. That makes every paste a chance to drift: a retyped
`alter table`, a section edited in the repository after the document was
written, or a file copied from an old chat message. So the document is
GENERATED from the repository, never hand-written:

  * the three approved files ``tools/apply_staging_schema.py`` is allowed to
    execute are imported from that tool, so the two lists cannot disagree,
  * Query 4 - the RLS lock that clears the three "RLS Disabled in Public"
    advisor warnings ``hard_delete_products.sql`` leaves behind,
  * the read-only acceptance checks, including the empty-array RPC probe.

Usage::

    python3 tools/make_copy_paste_doc.py            # (re)write the document
    python3 tools/make_copy_paste_doc.py --check    # verify it, exit 1 on drift
    python3 tools/make_copy_paste_doc.py --print    # write it to stdout

The document is deliberately kept to one SQL statement per block where the
step is a check, because the operator reads it on a phone.
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC_NAME = "MIGRATION_COPY_PASTE.md"

# Raw links let the operator open one file directly on a phone, without
# cloning anything. They are only valid once this branch is merged to main,
# which the document says next to each one.
RAW_BASE = ("https://github.com/praisejudith03-collab/jaurastore/raw/main")

# The three files this document may contain, in dependency order. Imported
# from the tool that executes them, so a change to APPROVED_FILES (or to a
# section file) makes --check fail until the document is regenerated.
def _approved_tool():
    spec = importlib.util.spec_from_file_location(
        "apply_staging_schema", ROOT / "tools" / "apply_staging_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def approved_files():
    """``[(relative name, exact text), ...]`` - the only files allowed here."""
    out = []
    for path in _approved_tool().APPROVED_FILES:
        out.append((path.relative_to(ROOT).as_posix(),
                    path.read_text().rstrip("\n")))
    return out


# --------------------------------------------------------------- Query 4 ----
# The one statement this document does NOT take from a file: the RLS lock for
# the three child tables hard_delete_products.sql creates. It is kept as a
# constant so tests/test_hard_delete_sql.py can execute exactly the text the
# operator pastes, and tests/test_copy_paste_migration_doc.py can prove the
# document carries it unchanged.
QUERY_4_SQL = """\
-- Query 4: clear the three "RLS Disabled in Public" advisor warnings that
-- hard_delete_products.sql leaves behind, by locking the three child tables
-- that file creates:
--
--     public.product_variants, public.product_prices, public.product_options
--
-- Row level security with NO policy means the API roles (anon, authenticated)
-- lose every read and write; the server's service_role bypasses RLS, so the
-- admin delete path keeps working.
--
-- public.products is deliberately NOT touched. The storefront keeps an
-- anon-key Realtime subscription on it for live stock, and enabling RLS
-- without a policy silently stops those events. The advisor listing
-- "RLS Disabled in Public - public.products" is expected and stays.
--
-- Run this with "Run without RLS" - never the editor's green "Run and enable
-- RLS" button, which changes what is being enabled.

alter table public.product_variants enable row level security;
alter table public.product_prices enable row level security;
alter table public.product_options enable row level security;

revoke all on table public.product_variants from public;
revoke all on table public.product_prices from public;
revoke all on table public.product_options from public;

do $$
declare
  r text;
  t text;
begin
  foreach t in array array['product_variants', 'product_prices', 'product_options'] loop
    foreach r in array array['anon', 'authenticated', 'authenticator'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('revoke all on table public.%I from %I', t, r);
      end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
      execute format('grant select, insert, update, delete on table public.%I to service_role', t);
    end if;
  end loop;
end $$;
"""

# --------------------------------------------------------------- checks -----
# Read-only, one statement per block, safe to run before anything else.
PREFLIGHT_PROJECT_SQL = """\
select current_database() as db,
       current_user as role,
       to_regclass('public.products') as products_table,
       to_regclass('public.deleted_products') as tombstone_ledger;
"""

PREFLIGHT_TABLES_SQL = """\
select table_name
  from information_schema.tables
 where table_schema = 'public'
 order by table_name;
"""

# The acceptance check. One row: the table, the function with its exact
# signature, and the execute privileges the migration promises
# (service_role yes, anon no). Read-only.
ACCEPTANCE_SQL = """\
select to_regclass('public.products') as products,
       to_regprocedure('public.hard_delete_products(text[])') as delete_function,
       (select bool_or(has_function_privilege(r, 'public.hard_delete_products(text[])', 'EXECUTE'))
          from unnest(array['service_role']) as r
         where exists (select 1 from pg_roles where rolname = r)) as service_can,
       (select bool_or(has_function_privilege(r, 'public.hard_delete_products(text[])', 'EXECUTE'))
          from unnest(array['anon']) as r
         where exists (select 1 from pg_roles where rolname = r)) as anon_can;
"""

# Asking a deleting function to delete nothing: returns {} and proves the
# object exists without writing a row. The empty array is not a special case
# the function added for tests - it is the documented no-op input.
RPC_PROBE_SQL = "select public.hard_delete_products(array[]::text[]);"


def render():
    """The whole document, deterministic from the repository's files."""
    lines = []
    add = lines.append
    add("# Migration copy-paste: products + hard delete")
    add("")
    add("<!-- GENERATED by tools/make_copy_paste_doc.py - do not edit by hand.")
    add("     Regenerate it, or run the tool with --check, after any SQL change. -->")
    add("")
    add("Paste each step into a **new** Supabase SQL editor query, in order, and wait")
    add("for it to succeed before the next one. Every block here is idempotent: a")
    add("retried paste is safe.")
    add("")
    add("**Rules that never change**")
    add("")
    add("1. Never run `supabase_schema.sql` as a whole. Sections 15 (storage) and")
    add("   16 (stock) and the image migration stay on hold.")
    add("2. Never enable RLS on `public.products` - the storefront's live stock")
    add("   subscription reads it with the anon key. Query 4 locks only the three")
    add("   child tables, and that is deliberate.")
    add("3. Use the editor's **Run without RLS** action. Never the green")
    add("   \"Run and enable RLS\" button: it decides for you what to enable.")
    add("4. Never reuse a product id that has been deleted: the tombstone trigger")
    add("   rejects it, by design.")
    add("")
    add("---")
    add("")
    add("## Query 1 - pre-check: is this the right project? (read-only)")
    add("")
    add("Compare `db` with the project you mean to change, and expect")
    add("`products_table` to be NULL on a project that has never had the shop")
    add("schema. If `tombstone_ledger` is not NULL this migration already ran.")
    add("")
    add(_fence(PREFLIGHT_PROJECT_SQL))
    add("")
    add("## Query 2 - pre-check: what is already there? (read-only)")
    add("")
    add("Read the list. `public.products` may exist while everything else is")
    add("disposable; if it holds rows, stop and confirm they are disposable before")
    add("continuing.")
    add("")
    add(_fence(PREFLIGHT_TABLES_SQL))
    add("")
    add("---")
    add("")

    files = approved_files()
    why = {
        "schema_sections/01_products.sql":
            "Creates `public.products` (canonical `id text` primary key) and repairs "
            "any older hand-built table add-only.",
        "schema_sections/09_product_compatibility.sql":
            "Products-only repair: `legacyId`, `image_url`, `stock_quantity` and the "
            "non-negative stock check.",
        "hard_delete_products.sql":
            "The delete migration: `ON DELETE CASCADE` on every per-product table, "
            "the tombstone ledger and `hard_delete_products(text[])`.",
    }
    for index, (name, text) in enumerate(files, start=1):
        add(f"## File {index} of {len(files)} - `{name}`")
        add("")
        add(why.get(name, "Approved section."))
        add("")
        add(f"Copy the **whole block** into one new query ({len(text.splitlines())} "
            f"lines). Raw link once this is merged: {RAW_BASE}/{name}")
        add("")
        add(_fence(text))
        add("")
        if index == 2:
            add("After this second file, `to_regclass('public.products')` must be")
            add("`public.products` and `id` must be `text`. If `id` is anything else,")
            add("stop: the delete migration's foreign keys are declared `text`.")
            add("")
        if index == len(files):
            add("---")
            add("")

    add("## Query 4 - clear the three RLS warnings (lock the child tables)")
    add("")
    add("Run this once after the delete migration. It clears the three")
    add("\"RLS Disabled in Public\" advisor warnings for the child tables the")
    add("migration created, and leaves `public.products` exactly as it was.")
    add("")
    add("One statement batch; paste the whole block.")
    add("")
    add(_fence(QUERY_4_SQL.rstrip("\n")))
    add("")
    add("---")
    add("")
    add("## Acceptance check (read-only)")
    add("")
    add("Expect one row:")
    add("")
    add("```text")
    add("products          | delete_function                | service_can | anon_can")
    add("public.products   | hard_delete_products(text[])   | t           | f")
    add("```")
    add("")
    add(_fence(ACCEPTANCE_SQL))
    add("")
    add("Then ask the delete function to delete nothing. `{}` means the object")
    add("exists and the empty input is a no-op; an error means it is missing.")
    add("")
    add(_fence(RPC_PROBE_SQL))
    add("")
    add("## What this document does not do")
    add("")
    add("* It never prints or stores a key, a password or a database URL.")
    add("* It does not create a test product. When you want the live delete proof,")
    add("  use `tools/staging_delete_check.py` (or the workflow) instead - never a")
    add("  real catalogue item.")
    add("* Nothing here has to be run twice; every block is safe to re-run if a")
    add("  paste was interrupted.")
    add("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _fence(sql):
    return "```sql\n" + sql.rstrip("\n") + "\n```"


def check(doc_path):
    """``(ok, message)`` - the document must be exactly what render() makes."""
    expected = render()
    name = pathlib.Path(doc_path).name
    try:
        on_disk = pathlib.Path(doc_path).read_text()
    except OSError as exc:
        return False, f"{name} is missing ({exc.strerror}). Run tools/make_copy_paste_doc.py."
    files = approved_files()
    if on_disk != expected:
        for rel, text in files:
            if text not in on_disk:
                return False, (f"{name} does not match the 3 approved SQL files: "
                               f"{rel} is stale or missing. Run "
                               f"tools/make_copy_paste_doc.py.")
        return False, (f"{name} is stale (it differs from the generated document). "
                       f"Run tools/make_copy_paste_doc.py.")
    return True, f"{name} matches the 3 approved SQL files"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="verify the document instead of writing it")
    parser.add_argument("--doc", default=str(ROOT / DOC_NAME),
                        help="document path (used with --check)")
    parser.add_argument("--print", dest="to_stdout", action="store_true",
                        help="write the generated document to stdout")
    args = parser.parse_args(argv)

    if args.to_stdout:
        sys.stdout.write(render())
        return 0
    if args.check:
        ok, message = check(args.doc)
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    path = pathlib.Path(args.doc)
    path.write_text(render())
    print(f"wrote {path.name} ({len(render().splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
