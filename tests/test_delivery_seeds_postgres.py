"""Execute sections 11/14 on disposable PostgreSQL, never on Supabase.

Install the test-only bundled PostgreSQL binaries with pip install pgserver==0.1.4.
No database URL or credentials are read. Each case runs in a rolled-back
transaction; the Unix-socket-only cluster is stopped after the tests.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pgserver
import pytest

ROOT = Path(__file__).resolve().parents[1]
REPAIR = (ROOT / "schema_sections/11_delivery_zones.sql").read_text()
SEEDS = (ROOT / "schema_sections/14_delivery_seeds.sql").read_text()
SNAPSHOT = """
select coalesce(jsonb_agg(z order by id), '[]'::jsonb) from delivery_zones z;
"""
EXPECTED = [
    ("lagos-mainland", "Lagos Mainland", "NGN", 2000, 5000, "delivery", 1),
    ("lagos-island", "Lagos Island", "NGN", 3500, 6000, "delivery", 2),
    ("ng-other", "Other Nigeria", "NGN", 0, 0, "quote", 3),
    ("cotonou", "Cotonou", "CFA", 1000, 3000, "delivery", 4),
    ("calavi", "Calavi", "CFA", 1500, 3500, "delivery", 5),
    ("porto-novo", "Porto-Novo", "CFA", 1500, 3500, "delivery", 6),
    ("bj-other", "Other Benin", "CFA", 0, 0, "quote", 7),
    ("lome", "Lomé", "CFA", 2500, 3500, "delivery", 8),
    ("tg-other", "Other Togo", "CFA", 0, 0, "quote", 9),
    ("pickup-cotonou", "Pickup in Cotonou is free for lighter products",
     "CFA", 0, 0, "pickup", 10),
]
SEED_FIELDS = ("id", "name", "currency", "fare_min", "fare_max", "kind", "sort_order")


@pytest.fixture(scope="module")
def postgres():
    # Short socket path avoids PostgreSQL's Unix socket path length limit.
    # Ignore PG* environment variables; connections can reach only this cluster.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    with tempfile.TemporaryDirectory(prefix="jaura-pg-") as directory:
        base = Path(directory)
        data = base / "data"
        pgserver.initdb(["--auth=trust", "--encoding=UTF8", "--no-locale",
                         "-U", "schema_test"], pgdata=data, env=env, timeout=30)
        pgserver.pg_ctl(["-w", "-l", str(base / "postgres.log"),
                         "-o", f"-F -k {base} -c listen_addresses=''", "start"],
                        pgdata=data, env=env, timeout=30)
        try:
            def run(sql, *, error=False):
                executable = Path(pgserver.__file__).parent / "pginstall/bin/psql"
                result = subprocess.run(
                    [str(executable), "-X", "-qAt", "-v", "ON_ERROR_STOP=1",
                     "-h", str(base), "-p", "5432", "-U", "schema_test",
                     "-d", "postgres"],
                    input="begin; create schema seed_test; "
                          "set local search_path = seed_test, public;\n"
                          + sql + "\nrollback;",
                    text=True, capture_output=True, env=env, timeout=30,
                )
                if error:
                    assert result.returncode != 0, "old seed unexpectedly succeeded"
                    return result.stderr
                assert result.returncode == 0, result.stderr
                return [json.loads(line) for line in result.stdout.splitlines()]
            yield run
        finally:
            pgserver.pg_ctl(["-w", "-m", "fast", "stop"], pgdata=data,
                            env=env, timeout=30)


def legacy_table(*, with_name=True, required=True):
    return f"""
create table delivery_zones (
  id text primary key,
  zone_name text {'not null' if required else ''} unique,
  {'name text unique,' if with_name else ''}
  fare_min integer not null,
  fare_max integer not null,
  active boolean not null default true,
  sort_order integer not null
);
"""


@pytest.mark.parametrize("conflicting_id", [False, True])
def test_old_name_only_seed_reproduces_zone_name_not_null(postgres, conflicting_id):
    # NOT NULL is checked even before ON CONFLICT can skip an existing ID.
    existing = """
insert into delivery_zones (id, zone_name, fare_min, fare_max, active, sort_order)
values ('cotonou', 'Admin name', 8123, 9456, false, 77);
""" if conflicting_id else ""
    error = postgres(legacy_table(with_name=False) + existing + REPAIR + """
insert into delivery_zones (id, name, currency, fare_min, fare_max, kind, sort_order)
values ('cotonou', 'Cotonou', 'CFA', 1000, 3000, 'delivery', 4)
on conflict (id) do nothing;
""", error=True)
    assert 'null value in column "zone_name"' in error
    assert 'violates not-null constraint' in error


def assert_defaults(rows, *, legacy=False, excluded=()):
    by_id = {row["id"]: row for row in rows}
    for expected in EXPECTED:
        if expected[0] in excluded:
            continue
        row = by_id[expected[0]]
        assert tuple(row[field] for field in SEED_FIELDS) == expected
        assert row["active"] is True
        if legacy:
            assert row["zone_name"] == row["name"]
        else:
            assert "zone_name" not in row


def test_fresh_name_only_table_seeds_all_defaults_idempotently(postgres):
    # An unrelated relation must not trigger legacy handling on this table.
    shadow = "create table public.delivery_zones (zone_name text not null);"
    first, second = postgres(shadow + REPAIR + SEEDS + SNAPSHOT + SEEDS + SNAPSHOT)
    assert len(first) == 10
    assert_defaults(first)
    assert second == first


@pytest.mark.parametrize("with_name", [False, True])
@pytest.mark.parametrize("required", [False, True])
def test_legacy_rows_and_admin_edits_survive_repair_and_repeated_seed(
        postgres, with_name, required):
    admin_name = "'Admin current name'," if with_name else ""
    existing = f"""
insert into delivery_zones
  (id, zone_name, {'name,' if with_name else ''} fare_min, fare_max, active, sort_order)
values
  ('cotonou', 'Admin legacy name', {admin_name}
   8123, 9456, false, 77),
  ('custom-zone', 'Custom legacy name', {'null,' if with_name else ''}
   321, 654, true, -9);
"""
    original, repaired, first, second = postgres(
        legacy_table(with_name=with_name, required=required) + existing + SNAPSHOT
        + REPAIR + SNAPSHOT + SEEDS + SNAPSHOT + SEEDS + SNAPSHOT)
    repaired_by_id = {row["id"]: row for row in repaired}
    first_by_id = {row["id"]: row for row in first}
    for row in original:
        assert {key: repaired_by_id[row["id"]][key] for key in row} == row
    for row in repaired:
        assert first_by_id[row["id"]] == row  # every column, including timestamps
    assert len(first) == 11
    assert_defaults(first, legacy=True, excluded=("cotonou",))
    assert second == first


@pytest.mark.parametrize("legacy", [False, True])
def test_unique_name_conflicts_skip_inserts_without_changing_admin_rows(postgres, legacy):
    setup = legacy_table() if legacy else ""
    setup += REPAIR
    # Different IDs with a default name must not make the batch fail. On legacy
    # tables also exercise a zone_name collision independent of current name.
    setup += """
insert into delivery_zones
  (id, name, fare_min, fare_max, active, sort_order%s)
values ('custom-cotonou', 'Cotonou', 8001, 9002, false, 81%s);
""" % (", zone_name" if legacy else "", ", 'Lagos Mainland'" if legacy else "")
    original, first, second = postgres(setup + SNAPSHOT + SEEDS + SNAPSHOT + SEEDS + SNAPSHOT)
    excluded = ("cotonou", "lagos-mainland") if legacy else ("cotonou",)
    assert len(first) == 11 - len(excluded)
    assert all(row in first for row in original)
    assert not set(excluded) & {row["id"] for row in first}
    assert_defaults(first, legacy=legacy, excluded=excluded)
    assert second == first
