"""Execute hard_delete_products.sql against a real PostgreSQL.

The other tests around deletion exercise the Python. This one runs the SQL
itself, because that is the only part that cannot be checked by reading it: a
migration is only correct if the database accepts it, re-accepts it, and ends
up in the state it promised. It is driven through psql, the same way a human
pastes it into the Supabase SQL editor.

Runs on the pgserver embedded PostgreSQL (already a test dependency). If that
is unavailable the tests skip rather than pretend to have checked anything.
"""
import pathlib
import subprocess

import pytest

pgserver = pytest.importorskip("pgserver")

SQL_PATH = pathlib.Path(__file__).resolve().parent.parent / "hard_delete_products.sql"


@pytest.fixture(scope="module")
def db():
    """A real PostgreSQL, torn down afterwards."""
    import shutil
    import tempfile
    pgdata = pathlib.Path(tempfile.mkdtemp(prefix="jaura-pg-"))
    srv = pgserver.get_server(str(pgdata), cleanup_mode=None)
    try:
        yield srv
    finally:
        srv.cleanup()
        shutil.rmtree(pgdata, ignore_errors=True)


def _psql(db, *args, stdin=None):
    from pgserver._commands import POSTGRES_BIN_PATH
    done = subprocess.run(
        [str(POSTGRES_BIN_PATH / "psql"), db.get_uri(),
         "-v", "ON_ERROR_STOP=1", *args],
        input=stdin, capture_output=True, text=True, timeout=180)
    if done.returncode != 0:
        raise AssertionError(
            f"psql exited {done.returncode}\n{done.stderr.strip()}")
    return done.stdout.strip()


def run(db, sql):
    """Execute a script, raising on any error. Mirrors pasting into the editor."""
    return _psql(db, "-At", "-f", "-", stdin=sql)


def one(db, sql):
    """Run a scalar query, return the first cell."""
    return _psql(db, "-At", "-c", sql).strip()


SCHEMA = """
drop table if exists public.featured_products cascade;
drop table if exists public.product_views cascade;
drop table if exists public.product_reviews cascade;
drop table if exists public.variant_stock cascade;
drop table if exists public.product_options cascade;
drop table if exists public.product_prices cascade;
drop table if exists public.product_variants cascade;
drop table if exists public.products cascade;

create table public.products (
  id    text primary key,
  name  text not null,
  stock integer default 0
);

-- variant_stock ships WITH a foreign key, but a restrictive one: this is the
-- shape the migration has to upgrade in place.
create table public.variant_stock (
  id         uuid primary key default gen_random_uuid(),
  product_id text not null references public.products(id) on delete restrict
);
create index variant_stock_product_id_idx on public.variant_stock(product_id);

-- product_reviews has NO foreign key at all, which is the shape it has always
-- had in this shop.
create table public.product_reviews (
  id         bigint generated always as identity primary key,
  product_id text not null,
  rating     integer not null default 5
);
"""


def fresh(db, migrated=True):
    """A database in its pre-migration state, migration applied by default."""
    run(db, SCHEMA)
    if migrated:
        run(db, SQL_PATH.read_text())


def make_role(db, name):
    run(db, f"do $$ begin create role {name} nologin; "
            "exception when duplicate_object then null; end $$;")


def drop_role(db, name):
    # drop owned by first: a role holding a grant on the delete function would
    # otherwise refuse to be dropped.
    run(db, f"do $$ begin execute 'drop owned by {name}'; "
            f"exception when undefined_object then null; end $$;")
    run(db, f"do $$ begin execute 'drop role {name}'; "
            "exception when undefined_object then null; end $$;")


def supabase_roles_present(db):
    return one(db, "select count(*) from pg_roles "
                   "where rolname in ('anon', 'authenticated', "
                   "'authenticator', 'service_role')") != "0"


def test_the_migration_applies_and_reapplies(db):
    """Idempotent: pasting it twice must be safe, which is what the file claims."""
    run(db, SCHEMA)
    text = SQL_PATH.read_text()
    run(db, text)
    run(db, text)          # <- the claim under test
    assert one(db, "select count(*) from public.products") == "0"


def test_the_cascade_actually_takes_the_children(db):
    """Delete parents; every child row must go in the same statement."""
    fresh(db)
    run(db, """
        insert into public.products (id, name) values
          ('p1','One'), ('p2','Two'), ('p3','Three');
        insert into public.variant_stock (product_id) values ('p1');
        insert into public.product_reviews (product_id, rating) values
          ('p1', 5), ('p2', 4), ('p3', 3);
        insert into public.product_variants (product_id, title) values
          ('p1','Red');
        insert into public.product_options (product_id, title) values
          ('p1','Size');
    """)
    gone = one(db, "select public.hard_delete_products(array['p1','p2'])")
    assert sorted(gone.strip("{}").split(",")) == ["p1", "p2"], gone

    assert one(db, "select count(*) from public.products") == "1"
    # the child that had a RESTRICT foreign key - upgraded by the migration
    assert one(db, "select count(*) from public.variant_stock") == "0"
    # the child that had none
    assert one(db, "select count(*) from public.product_reviews "
                   "where product_id in ('p1','p2')") == "0"
    # the children the migration created
    assert one(db, "select count(*) from public.product_variants") == "0"
    assert one(db, "select count(*) from public.product_options") == "0"
    # and the product nobody deleted is untouched
    assert one(db, "select count(*) from public.product_reviews "
                   "where product_id = 'p3'") == "1"


def test_it_reports_only_ids_that_were_really_there(db):
    """A caller must never be told 'deleted' for a row that survived."""
    fresh(db)
    run(db, "insert into public.products (id, name) values "
            "('p1','One'), ('p2','Two')")

    gone = one(db, "select public.hard_delete_products("
                   "array['p1', 'does-not-exist'])")
    assert gone == "{p1}", gone            # a Postgres array literal
    assert one(db, "select count(*) from public.products") == "1"

    # empty and null input are a no-op, not an error and not a table wipe
    assert one(db, "select public.hard_delete_products(array[]::text[])") == "{}"
    assert one(db, "select public.hard_delete_products(null)") == "{}"
    assert one(db, "select count(*) from public.products") == "1"


def test_one_table_of_orphans_cannot_abort_the_whole_migration(db):
    """The failure this guards against is mundane and total. product_reviews
    has never had a foreign key, so it still holds reviews of products deleted
    before tombstones existed. Validating those rows must skip THAT table -
    not roll back the cascade every other table needed."""
    fresh(db, migrated=False)
    run(db, """
        insert into public.products (id, name) values ('p1','One');
        insert into public.variant_stock (product_id) values ('p1');
        insert into public.product_reviews (product_id, rating)
          values ('deleted-years-ago', 5);
    """)

    run(db, SQL_PATH.read_text())          # must not raise

    # the table that could not take the cascade is left exactly as it was...
    assert one(db, "select count(*) from public.product_reviews") == "1"
    assert one(db, """
        select count(*) from pg_constraint con
          join pg_class rel on rel.oid = con.conrelid
         where rel.relname = 'product_reviews' and con.contype = 'f'
           and con.confdeltype = 'c'""") == "0"

    # ...while the tables that could take it still got it, which was the entire
    # point of running the file.
    assert one(db, """
        select count(*) from pg_constraint con
          join pg_class rel on rel.oid = con.conrelid
         where rel.relname = 'variant_stock' and con.contype = 'f'
           and con.confdeltype = 'c'""") == "1"
    one(db, "select public.hard_delete_products(array['p1'])")
    assert one(db, "select count(*) from public.variant_stock") == "0"
    assert one(db, "select count(*) from public.products") == "0"


def test_the_orphaned_table_still_gets_swept_by_the_app(db):
    """A table with no cascade must not keep a deleted product's rows alive.
    The app sweeps these explicitly (supabase_store.PRODUCT_CHILD_TABLES) and
    that sweep runs even when the RPC succeeded - this is the case where
    relying on the schema alone would leak."""
    fresh(db)
    run(db, """
        insert into public.products (id, name) values ('p1','One');
        insert into public.product_reviews (product_id, rating) values ('p1', 5);
    """)
    one(db, "select public.hard_delete_products(array['p1'])")
    assert one(db, "select count(*) from public.product_reviews") == "0"


def test_the_function_is_not_callable_by_the_public(db):
    """SECURITY: CREATE FUNCTION grants EXECUTE to PUBLIC, and PostgREST
    exposes the public schema. Without the revoke, anyone who can reach the
    shop's API could post a list of product ids and empty the catalogue."""
    fresh(db)
    proacl = one(db, "select coalesce(proacl::text, 'NULL') from pg_proc "
                     "where proname = 'hard_delete_products'")
    assert proacl != "NULL", "function has no explicit privileges at all"
    # grantee 0 is PUBLIC. It must not appear.
    assert ",0=" not in proacl and not proacl.startswith("0="), proacl


def test_anon_and_authenticated_are_revoked_when_supabase_provides_them(db):
    """Those roles only exist on Supabase, so their absence must not be an
    error - and when they are there they must not be able to call this."""
    run(db, SCHEMA)
    for role in ("anon", "authenticated", "service_role"):
        make_role(db, role)
    try:
        run(db, SQL_PATH.read_text())     # must not raise even with the roles

        assert one(db, "select has_function_privilege('anon', "
                       "'public.hard_delete_products(text[])', 'EXECUTE')") == "f"
        assert one(db, "select has_function_privilege('authenticated', "
                       "'public.hard_delete_products(text[])', 'EXECUTE')") == "f"
        # and the app's own role still has it
        assert one(db, "select has_function_privilege('service_role', "
                       "'public.hard_delete_products(text[])', 'EXECUTE')") == "t"
    finally:
        for role in ("anon", "authenticated", "service_role"):
            drop_role(db, role)


def test_it_applies_on_a_database_with_no_supabase_roles(db):
    """A bare GRANT to a role that does not exist aborts the whole script, so
    this file cannot be run against staging at all unless that grant is
    guarded. Found by running it, not by reading it."""
    run(db, SCHEMA)
    assert not supabase_roles_present(db)
    run(db, SQL_PATH.read_text())     # must not raise
    # ...and the function is still created and still works
    assert one(db, "select count(*) from pg_proc "
                   "where proname = 'hard_delete_products'") == "1"


def test_the_app_calls_it_with_the_name_and_argument_in_the_file():
    """A renamed parameter here would make every delete fall back to the slow
    row-by-row path, silently. Keep the two in step."""
    import supabase_store
    sql = SQL_PATH.read_text().lower()
    src = pathlib.Path(supabase_store.__file__).read_text()
    assert 'rpc("hard_delete_products", {"product_ids": ids})' in src
    assert "hard_delete_products(product_ids text[])" in sql
    # every child table the app sweeps is mentioned in the migration
    for table, _column in supabase_store.PRODUCT_CHILD_TABLES:
        assert table in sql, table
    # and the three the shop may not have yet are created outright
    for table in ("product_variants", "product_prices", "product_options"):
        assert f"create table if not exists public.{table}" in sql, table


def test_the_sql_is_valid_postgres():
    """Parsed by PostgreSQL's own grammar, not by a regex."""
    pglast = pytest.importorskip("pglast")
    stmts = pglast.parse_sql(SQL_PATH.read_text())
    assert len(stmts) >= 9
