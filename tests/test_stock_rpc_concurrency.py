"""The production stock guard, exercised on real PostgreSQL.

The storefront's local backend reserves under a cross-process file lock
(tests/test_stock_enforcement.py covers that). Production guards inside
PostgreSQL instead: reserve_product_stock() is a single guarded UPDATE that
covers the product total AND the chosen variant, so two concurrent checkouts
of the last unit cannot both land. This file proves it with genuinely
concurrent sessions on a disposable pgserver cluster - never on Supabase,
whose credentials are never read.

Run with:  python3 -m pytest tests/test_stock_rpc_concurrency.py -q
Install the test-only bundled PostgreSQL binaries with pip install
pgserver==0.1.4.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pgserver
import pytest

ROOT = Path(__file__).resolve().parents[1]

PRODUCTS_DDL = Path("schema_sections/01_products.sql").read_text()
STOCK_SQL = Path("schema_sections/16_stock.sql").read_text()

PRODUCTS = [
    # one unit left: the classic two-buyers race
    {"id": "rpc-last", "sku": "RPCLAST", "slug": "rpc-last", "name": "Last Unit",
     "priceNgn": 2000, "stock": 1, "stock_quantity": 1, "online": True,
     "optionStock": None, "category": "beauty"},
    # per-variant: 1 Red + 4 Black
    {"id": "rpc-var", "sku": "RPCVAR", "slug": "rpc-var", "name": "Variant Guard",
     "priceNgn": 3000, "stock": 5, "stock_quantity": 5, "online": True,
     "optionStock": {"Red": 1, "Black": 4}, "category": "beauty"},
    # 5 units, qty-2 orders: at most two fit
    {"id": "rpc-run", "sku": "RPCRUN", "slug": "rpc-run", "name": "Limited Run",
     "priceNgn": 1500, "stock": 5, "stock_quantity": 5, "online": True,
     "optionStock": None, "category": "beauty"},
]


def _seed_rows():
    values = []
    for p in PRODUCTS:
        opt = ("'" + json.dumps(p["optionStock"]) + "'") if p["optionStock"] else "null"
        values.append(
            "('{id}', '{sku}', '{slug}', '{name}', {price}, {stock}, "
            "{sq}, {opt}, true)".format(id=p["id"], sku=p["sku"], slug=p["slug"],
                                        name=p["name"], price=p["priceNgn"],
                                        stock=p["stock"], sq=p["stock_quantity"],
                                        opt=opt))
    # Only the columns the stock guard touches; the rest default.
    return ("insert into products (id, sku, slug, name, \"priceNgn\", stock, "
            "stock_quantity, \"optionStock\", online) values " + ",".join(values))


@pytest.fixture(scope="module")
def pg():
    # Short socket path avoids PostgreSQL's Unix socket path length limit.
    # Ignore PG* environment variables; connections reach only this cluster.
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
            def psql(sql, *, error=False):
                # Each call is its own committed session: the mutations the
                # tests assert on (release_product_stock) must persist for the
                # reads that follow, and the cluster is throwaway anyway.
                executable = Path(pgserver.__file__).parent / "pginstall/bin/psql"
                result = subprocess.run(
                    [str(executable), "-X", "-qAt", "-v", "ON_ERROR_STOP=1",
                     "-h", str(base), "-p", "5432", "-U", "schema_test",
                     "-d", "postgres"],
                    input="set search_path = stock_test, public;\n" + sql + "\n",
                    text=True, capture_output=True, timeout=60, env=env)
                if result.returncode != 0 and not error:
                    raise AssertionError(result.stderr)
                return result
            # The products table DDL + both stock RPCs, committed (the race
            # sessions must see the seed rows).
            executable = Path(pgserver.__file__).parent / "pginstall/bin/psql"
            setup = ("create schema stock_test; set search_path = stock_test, public;\n"
                     + PRODUCTS_DDL + "\n" + STOCK_SQL + "\n" + _seed_rows() + "\n")
            result = subprocess.run(
                [str(executable), "-X", "-qAt", "-v", "ON_ERROR_STOP=1",
                 "-h", str(base), "-p", "5432", "-U", "schema_test", "-d", "postgres"],
                input=setup, text=True, capture_output=True, timeout=120, env=env)
            if result.returncode != 0:
                raise AssertionError(result.stderr)
            yield {"run": psql, "base": base, "env": env}
        finally:
            pgserver.pg_ctl(["-m", "fast", "stop"], pgdata=data, env=env, timeout=30)


def _row(pg, pid, column):
    out = pg["run"](f"select coalesce({column}::text, '<null>') from products "
                    f"where id = '{pid}';").stdout.strip()
    return out


def _concurrent_calls(pg, calls):
    """Run `calls` (list of SQL strings) in parallel sessions; return their
    return-code / stdout pairs in order. Each call runs in its own psql."""
    procs = []
    executable = Path(pgserver.__file__).parent / "pginstall/bin/psql"
    script = ("set search_path = stock_test, public;\n"
              "select coalesce(reserve_product_stock('{pid}', {qty}, {opt}), false)::text;\n")
    for pid, qty, opt in calls:
        opt_sql = "null" if not opt else f"'{opt}'"
        procs.append(subprocess.Popen(
            [str(executable), "-X", "-qAt",
             "-h", str(pg["base"]), "-p", "5432", "-U", "schema_test", "-d", "postgres"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=pg["env"]))
        procs[-1]._sql = script.format(pid=pid, qty=qty, opt=opt_sql)
    outs = []
    for proc in procs:
        out, err = proc.communicate(proc._sql, timeout=60)
        outs.append((proc.returncode, out.strip(), err.strip()))
    return outs


def test_two_sessions_cannot_both_take_the_last_unit(pg):
    outs = _concurrent_calls(pg, [("rpc-last", 1, None)] * 8)
    for code, _out, err in outs:
        assert code == 0, err
    trues = [out for _c, out, _e in outs if out == "true"]
    assert len(trues) == 1, outs
    assert _row(pg, "rpc-last", "stock_quantity") == "0"
    assert _row(pg, "rpc-last", "stock") == "0"


def test_variant_guard_is_per_variant_and_atomic(pg):
    outs = _concurrent_calls(pg, [("rpc-var", 1, "Red")] * 6)
    trues = [out for _c, out, _e in outs if out == "true"]
    assert len(trues) == 1, outs
    assert json.loads(_row(pg, "rpc-var", '"optionStock"'))["Red"] == 0
    assert json.loads(_row(pg, "rpc-var", '"optionStock"'))["Black"] == 4
    assert _row(pg, "rpc-var", "stock_quantity") == "4"


def test_more_orders_than_units_never_oversell(pg):
    outs = _concurrent_calls(pg, [("rpc-run", 2, None)] * 8)
    trues = [out for _c, out, _e in outs if out == "true"]
    assert len(trues) <= 2, outs
    assert _row(pg, "rpc-run", "stock_quantity") in ("1", "2")
    assert int(_row(pg, "rpc-run", "stock_quantity")) >= 0


def test_over_order_is_rejected_and_changes_nothing(pg):
    out = pg["run"]("select reserve_product_stock('rpc-run', 99, null)::text;").stdout.strip()
    assert out == "false"
    assert _row(pg, "rpc-run", "stock_quantity") in ("1", "2")


def test_sold_out_variant_is_rejected_without_touching_others(pg):
    out = pg["run"]("select reserve_product_stock('rpc-var', 1, 'Red')::text;").stdout.strip()
    assert out == "false"
    assert json.loads(_row(pg, "rpc-var", '"optionStock"'))["Red"] == 0
    assert json.loads(_row(pg, "rpc-var", '"optionStock"'))["Black"] == 4


def test_release_restores_product_and_variant(pg):
    ok = pg["run"]("select release_product_stock('rpc-var', 1, 'Red')::text;").stdout.strip()
    assert ok == "true"
    assert json.loads(_row(pg, "rpc-var", '"optionStock"'))["Red"] == 1
    assert _row(pg, "rpc-var", "stock_quantity") == "5"
    assert _row(pg, "rpc-var", "stock") == "5"
