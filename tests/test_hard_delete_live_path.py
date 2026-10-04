"""The real delete path against a real PostgreSQL - no stubbed report.

Every other test around absolute deletion replaces
``supabase_store.hard_delete_products()`` with a lambda and then asserts what
the app does with the dict that lambda returns. That leaves the interesting
half untested: the store's own RPC call, its tombstone bookkeeping, the child
sweep, the media purge, and the SQL that only exists once the migration has
been applied.

These tests remove the lambda. ``supabase_store.client`` is pointed at a small
PostgREST-shaped bridge (table/rpc/storage calls piped to psql) backed by the
pgserver PostgreSQL that ``hard_delete_products.sql`` was applied to, so the
row-level truth is the database's answer:

  * the products row is gone, the children cascaded, the tombstone is in
    ``deleted_products``, and the trigger refuses the id afterwards;
  * the store reports (and purges) only what the database really removed;
  * a database WITHOUT the migration fails closed - the row survives and the
    report says so, which is the state the staging project is in today;
  * the Admin DELETE endpoint answers 200 + ``deleteMode="supabase-hard"`` only
    when the database really removed the row, and 503 without touching it when
    the function is missing;
  * replacing a photo saves the new gallery, purges only the replaced,
    unreferenced object (the shared-reference guard reads the live product
    rows), and never calls the delete RPC.

Everything on the PostgreSQL side is real, and the "bucket" is a set in the
bridge, so ``storage.delete_upload`` - including its shared-reference guard -
runs unpatched.

Skips cleanly when pgserver is unavailable, like the rest of the PostgreSQL
tests.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

pgserver = pytest.importorskip("pgserver")

import app as appmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import storage  # noqa: E402
import supabase_store  # noqa: E402
from config import Config  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

ROOT = pathlib.Path(__file__).resolve().parent.parent
SQL_PATH = ROOT / "hard_delete_products.sql"
APPROVED_SECTIONS = ("01_products.sql", "09_product_compatibility.sql")
EMAIL = "jaurastore@gmail.com"


# ------------------------------------------------------------------ PostgreSQL
class _PgError(Exception):
    """A database error, shaped like the PostgREST failure the app sees."""


class _PG:
    """A disposable PostgreSQL cluster plus the psql plumbing."""

    def __init__(self):
        from pgserver._commands import POSTGRES_BIN_PATH
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="jaura-live-pg-"))
        self.srv = pgserver.get_server(str(self.dir), cleanup_mode=None)
        self.psql = str(POSTGRES_BIN_PATH / "psql")
        self.uri = self.srv.get_uri()

    def close(self):
        try:
            self.srv.cleanup()
        finally:
            shutil.rmtree(self.dir, ignore_errors=True)

    def raw(self, sql):
        """(returncode, stdout, stderr) - never raises on a SQL error."""
        done = subprocess.run(
            [self.psql, self.uri, "-At", "-v", "ON_ERROR_STOP=1", "-f", "-"],
            input=sql, capture_output=True, text=True, timeout=180)
        return done.returncode, done.stdout.strip(), done.stderr.strip()

    def sql(self, sql):
        rc, out, err = self.raw(sql)
        if rc != 0:
            raise AssertionError(f"psql exited {rc}\n{err}")
        return out

    def apply(self, path):
        self.sql(pathlib.Path(path).read_text())

    def scalar(self, sql):
        return self.sql(sql).strip()

    def json_rows(self, sql):
        raw = self.sql(sql)
        return json.loads(raw) if raw else []

    def count(self, table, where="true"):
        return int(self.scalar(
            f'select count(*) from public."{table}" where {where}'))


def _start(apply_migration):
    pg = _PG()
    for name in APPROVED_SECTIONS:
        pg.apply(ROOT / "schema_sections" / name)
    if apply_migration:
        pg.apply(SQL_PATH)
    return pg


@pytest.fixture(scope="module")
def migrated():
    """Sections 01 + 09 (approved) and then the delete migration."""
    pg = _start(True)
    try:
        yield pg
    finally:
        pg.close()


@pytest.fixture(scope="module")
def bare():
    """The same approved sections with NO delete migration (staging today)."""
    pg = _start(False)
    try:
        yield pg
    finally:
        pg.close()


@pytest.fixture()
def client(app):
    init_db()
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


# ----------------------------------------------------------- PostgREST bridge
def _ident(name):
    return '"' + str(name).replace('"', '""') + '"'


def _lit(value, dtype):
    """A SQL literal for one JSON value, typed as the column really is."""
    if value is None:
        return "null"
    if isinstance(dtype, str) and dtype.startswith("json"):
        return "'" + json.dumps(value).replace("'", "''") + "'::" + dtype
    if dtype == "boolean":
        if isinstance(value, str):
            return ("true" if value.strip().lower() in ("1", "true", "t", "yes")
                    else "false")
        return "true" if value else "false"
    if dtype in ("integer", "bigint", "smallint", "numeric", "real",
                 "double precision"):
        if isinstance(value, bool):
            return "1" if value else "0"
        text = str(value).strip()
        try:
            float(text)
        except ValueError:
            raise _PgError(f'invalid input syntax for type {dtype}: "{text}"')
        return text
    if isinstance(dtype, str) and dtype.startswith("timestamp"):
        return "'" + str(value).replace("'", "''") + "'::" + dtype
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value).replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"


class _Res:
    def __init__(self, data, count=None):
        self.data = list(data or [])
        self.count = len(self.data) if count is None else int(count)


class _Bridge:
    """A PostgREST-shaped client over psql: table(), rpc(req) and storage."""

    def __init__(self, pg):
        self.pg = pg
        self._meta = {}
        self.objects = set()          # the "bucket": storage paths
        self.removed = []             # every remove() call, as (bucket, paths)
        self.rpc_calls = []           # every rpc name + ids the app asked for
        self.storage = _BridgeStorage(self)

    # -- metadata ---------------------------------------------------------
    def meta(self, table):
        if table in self._meta:
            return self._meta[table]
        exists = self.pg.scalar(
            f"select coalesce(to_regclass('public.{_ident(table)}')::text, '')")
        if not exists:
            raise _PgError(
                f'relation "public.{table}" does not exist (PGRST205)')
        rows = self.pg.json_rows(
            "select coalesce(json_agg(row_to_json(c)), '[]')::text from ("
            "  select column_name, data_type from information_schema.columns"
            f"  where table_schema = 'public' and table_name = '{table}'"
            ") c")
        cols = {r["column_name"]: r["data_type"] for r in rows}
        pk_rows = self.pg.json_rows(
            "select coalesce(json_agg(row_to_json(k)), '[]')::text from ("
            "  select a.attname as name from pg_index i"
            "  join pg_class c on c.oid = i.indrelid"
            "  join pg_namespace n on n.oid = c.relnamespace"
            "  join pg_attribute a on a.attrelid = c.oid and a.attnum = any(i.indkey)"
            "  where n.nspname = 'public' and i.indisprimary"
            f"    and c.relname = '{table}'"
            ") k")
        pk = pk_rows[0]["name"] if pk_rows else "id"
        self._meta[table] = (cols, pk)
        return self._meta[table]

    def table(self, name):
        return _BridgeTable(self, name)

    def rpc(self, name, params=None):
        return _BridgeRpc(self, name, params or {})

    def _q(self, sql):
        rc, out, err = self.pg.raw(sql)
        if rc != 0:
            raise _PgError(err.splitlines()[-1] if err else "query failed")
        return json.loads(out) if out else []


class _BridgeTable:
    def __init__(self, bridge, name):
        self._b = bridge
        self._name = name
        self._select = "*"
        self._count_req = None
        self._eq = None
        self._in = None
        self._limit = None
        self._order = None
        self._window = None
        self._payload = None
        self._delete = False
        self._rows = None

    # -- builders (all return self, like postgrest-py) ---------------------
    def select(self, columns="*", count=None):
        self._select = columns
        self._count_req = count
        return self

    def order(self, column, desc=False):
        self._order = (column, bool(desc))
        return self

    def range(self, lo, hi):
        self._window = (int(lo), int(hi))
        return self

    def limit(self, n=None):
        self._limit = None if n is None else int(n)
        return self

    def eq(self, column, value):
        self._eq = (column, value)
        return self

    def in_(self, column, values):
        self._in = (column, list(values or []))
        return self

    def update(self, payload):
        self._payload = dict(payload or {})
        return self

    def delete(self):
        self._delete = True
        return self

    def upsert(self, rows):
        self._rows = rows if isinstance(rows, list) else [rows]
        return self

    # -- execution --------------------------------------------------------
    def _where(self, cols):
        parts = []
        if self._eq:
            key, value = self._eq
            if key not in cols:
                raise _PgError(
                    f"column products.{key} does not exist (42703)")
            parts.append(f'{_ident(key)} = {_lit(value, cols.get(key))}')
        if self._in:
            key, values = self._in
            if key not in cols:
                raise _PgError(f"column {key} does not exist (42703)")
            if values:
                literals = ", ".join(_lit(v, cols.get(key)) for v in values)
                parts.append(f"{_ident(key)} in ({literals})")
            else:
                parts.append("false")
        return " and ".join(parts) if parts else "true"

    def execute(self):
        cols, pk = self._b.meta(self._name)
        where = self._where(cols)
        table = f"public.{_ident(self._name)}"

        if self._rows is not None:
            return _Res(self._upsert(cols, pk))
        if self._payload is not None:
            unknown = [k for k in self._payload if k not in cols]
            if unknown:
                raise _PgError(
                    f"Could not find the '{unknown[0]}' column of "
                    f"'{self._name}' in the schema cache (PGRST204)")
            sets = ", ".join(f"{_ident(k)} = {_lit(v, cols.get(k))}"
                             for k, v in self._payload.items())
            if not self._where(cols):
                raise _PgError("bridge refuses an unfiltered update")
            return _Res(self._b._q(
                f"with u as (update {table} set {sets} where {where} returning *)"
                " select coalesce(json_agg(u), '[]')::text from u"))
        if self._delete:
            if not self._where(cols):
                raise _PgError("bridge refuses an unfiltered delete")
            return _Res(self._b._q(
                f"with d as (delete from {table} where {where} returning *)"
                " select coalesce(json_agg(d), '[]')::text from d"))

        selection = ("*" if self._select.strip() == "*"
                     else ", ".join(_ident(c.strip())
                                    for c in self._select.split(",")))
        tail = ""
        if self._order:
            column, desc = self._order
            tail += f' order by {_ident(column)}{" desc" if desc else ""}'
        if self._window:
            lo, hi = self._window
            tail += f" limit {max(0, hi - lo + 1)} offset {lo}"
        elif self._limit is not None:
            tail += f" limit {self._limit}"
        rows = self._b._q(
            f"select coalesce(json_agg(r), '[]')::text from ("
            f"  select {selection} from {table} where {where}{tail}) r")
        total = None
        if self._count_req == "exact":
            total = int(self._b.pg.scalar(
                f"select count(*) from {table} where {where}"))
        return _Res(rows, total)

    def _upsert(self, cols, pk):
        for row in self._rows:
            unknown = [k for k in (row or {}) if k not in cols]
            if unknown:
                raise _PgError(
                    f"Could not find the '{unknown[0]}' column of "
                    f"'{self._name}' in the schema cache (PGRST204)")
        names, values = [], []
        for row in self._rows:
            row = dict(row or {})
            if not names:
                names = list(row.keys())
            values.append("(" + ", ".join(
                _lit(row.get(n), cols.get(n)) for n in names) + ")")
        updates = ", ".join(f"{_ident(n)} = excluded.{_ident(n)}"
                            for n in names if n != pk)
        conflict = f"on conflict ({_ident(pk)}) do update set {updates}" \
            if updates else f"on conflict ({_ident(pk)}) do nothing"
        return self._b._q(
            f"with w as (insert into public.{_ident(self._name)} "
            f"({', '.join(_ident(n) for n in names)}) values "
            f"{', '.join(values)} {conflict} returning *)"
            " select coalesce(json_agg(w), '[]')::text from w")


class _BridgeRpc:
    def __init__(self, bridge, name, params):
        self._b = bridge
        self._name = name
        self._params = params

    def execute(self):
        ids = list(self._params.get("product_ids") or [])
        self._b.rpc_calls.append((self._name, [str(i) for i in ids]))
        arguments = []
        for value in self._params.values():
            if isinstance(value, (list, tuple)):
                items = ", ".join(_lit(v, "text") for v in value)
                arguments.append(f"array[{items}]::text[]")
            else:
                arguments.append(_lit(value, "text"))
        return _Res(self._b._q(
            "select coalesce(json_agg(x), '[]')::text from ("
            f"  select unnest(public.{_ident(self._name)}"
            f"({', '.join(arguments)})) as x) t"))


class _BridgeStorage:
    def __init__(self, bridge):
        self._b = bridge

    def from_(self, bucket):
        return _BridgeBucket(self._b, bucket)


class _BridgeBucket:
    def __init__(self, bridge, bucket):
        self._b = bridge
        self._bucket = bucket

    def remove(self, paths):
        removed = []
        for path in paths or []:
            if path in self._b.objects:
                self._b.objects.discard(path)
                removed.append(path)
        self._b.removed.append((self._bucket, list(removed)))
        return removed


def wire(monkeypatch, bridge, env="production"):
    """Point the whole app at the bridge, exactly like a deployed instance."""
    monkeypatch.setattr(Config, "ENV", env)
    monkeypatch.setattr(Config, "SUPABASE_URL", "https://bridge.local")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "bridge-key")
    monkeypatch.setattr(Config, "UPLOAD_MODE", "supabase")
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: bridge)
    monkeypatch.setattr(catalog_mod, "_sync_repo_async", lambda: None)
    return bridge


def _login(client):
    r = client.post("/api/admin/login",
                    json={"email": EMAIL, "password": PW, "recaptcha": ""})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def _save(client, tok, product):
    r = client.post("/api/admin/products", json={"product": product},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body.get("mirrored") is not False, body
    return body["product"]


def _truncate(pg):
    pg.sql("truncate public.products, public.deleted_products, "
           "public.product_variants, public.product_prices, "
           "public.product_options cascade;")


@pytest.fixture()
def live(migrated, monkeypatch):
    _truncate(migrated)
    bridge = _Bridge(migrated)
    wire(monkeypatch, bridge)
    return bridge


@pytest.fixture()
def bare_live(bare, monkeypatch):
    bare.sql("drop table if exists public.products cascade; "
             "drop table if exists public.product_variants cascade;")
    for name in APPROVED_SECTIONS:
        bare.apply(ROOT / "schema_sections" / name)
    bridge = _Bridge(bare)
    wire(monkeypatch, bridge)
    return bridge


def _url(name):
    return f"/uploads/products/2026/{name}"


def _key(url):
    return url[len("/uploads/"):]


# --------------------------------------------------------- the delete itself
def test_the_real_rpc_removes_row_children_media_and_writes_the_tombstone(
        migrated, live):
    """One call through the real store: row gone, children cascaded, tombstone
    written, the unreferenced object purged, and the id un-reusable."""
    pid = "jau-live-1"
    url = _url("live-1.jpg")
    live.objects.add(_key(url))
    migrated.sql(f"""
        insert into public.products (id, name, image, images, stock_quantity, source)
        values ('{pid}', 'Live One', '{url}', '{json.dumps([url])}'::jsonb, 3, 'admin');
        insert into public.product_variants (product_id, title) values ('{pid}', 'Red');
        insert into public.product_prices (product_id, price_ngn) values ('{pid}', 1000);
    """)

    report = supabase_store.hard_delete_products([pid])

    assert report["deleted"] == [pid], report
    assert report["files"] == 1, report
    assert report["errors"] == [], report
    assert migrated.count("products", f"id = '{pid}'") == 0
    assert migrated.count("product_variants", f"product_id = '{pid}'") == 0
    assert migrated.count("product_prices", f"product_id = '{pid}'") == 0
    assert migrated.count("deleted_products", f"product_id = '{pid}'") == 1
    assert _key(url) not in live.objects, "the unshared object was not purged"

    rc, _out, err = migrated.raw(
        f"insert into public.products (id, name) values ('{pid}', 'Ghost')")
    assert rc != 0 and "permanently deleted" in err, err


def test_the_real_rpc_reports_only_ids_that_were_there(migrated, live):
    """The report is the database's answer, not the caller's hope."""
    real, ghost = "jau-live-2", "jau-live-2-never-existed"
    migrated.sql(f"insert into public.products (id, name) "
                 f"values ('{real}', 'Live Two')")

    report = supabase_store.hard_delete_products([real, ghost])

    assert report["deleted"] == [real], report
    assert report["errors"] == [], report
    assert migrated.count("products", f"id = '{real}'") == 0
    # both are tombstoned: an id that never existed must not be creatable later
    assert migrated.count("deleted_products",
                          f"product_id in ('{real}', '{ghost}')") == 2


def test_the_empty_array_call_is_a_true_no_op(migrated, live):
    """tools/staging_delete_check.py proves the RPC exists with an EMPTY call.

    That probe must not delete, tombstone or otherwise touch anything - it is
    what makes "is the migration applied?" answerable without writing.
    """
    migrated.sql("insert into public.products (id, name) "
                 "values ('jau-live-7', 'Live Seven')")
    before = migrated.count("deleted_products")

    status = migrated.sql(
        "select public.hard_delete_products(array[]::text[]);")

    assert status.strip() == "{}"
    assert migrated.count("products") == 1
    assert migrated.count("deleted_products") == before, "a probe wrote a tombstone"


def test_a_database_without_the_migration_fails_closed(bare_live, bare):
    """Staging today: no hard_delete_products(text[]). The row must survive.

    This is the guarantee that matters most before the migration is applied -
    the store may not fall back to a row-by-row REST delete, which is not
    race-safe and could report success while a stale write recreates the row.
    """
    pid = "jau-live-3"
    bare.sql(f"insert into public.products (id, name) values ('{pid}', 'Live Three')")

    report = supabase_store.hard_delete_products([pid])

    assert report["deleted"] == []
    assert report["errors"], "a missing migration must be reported"
    assert "hard_delete_products.sql" in report["errors"][0]
    assert bare.count("products", f"id = '{pid}'") == 1, "the row was touched"
    assert bare.scalar("select coalesce("
                       "to_regclass('public.deleted_products')::text, '')") == ""


# ---------------------------------------------------------- the Admin button
@pytest.fixture()
def manual_queue():
    """Queue work from the test thread: no worker races the DB assertions."""
    import task_queue
    task_queue.reset()
    task_queue.set_manual(True)
    yield task_queue
    task_queue.set_manual(False)
    task_queue.reset()


def test_admin_delete_answers_at_once_and_the_worker_hard_deletes(
        client, migrated, live, manual_queue):
    """Save a product, delete it through the portal, and check the database.

    The request must NOT run the multi-second RPC (that is what timed the
    browser out): it writes the tombstone, answers 200 + ``deleteMode:
    "queued"`` and a job id, with the row still present. The queued worker then
    performs the real delete against the real database, and every promised
    effect is checked there - row gone, children cascaded, tombstone row
    written, media purged.
    """
    tok = _login(client)
    url = _url("admin-del.jpg")
    live.objects.add(_key(url))
    pid = "jau-live-4"
    saved = _save(client, tok, {"id": pid, "name": "Live Four", "priceNgn": 5000,
                                "stock": 2, "image": url, "images": [url]})
    assert saved["image"] == url
    assert migrated.count("products", f"id = '{pid}'") == 1

    r = client.delete(f"/api/admin/products/{pid}", headers={"X-CSRF-Token": tok})

    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert body["deleteMode"] == "queued"
    assert body["queued"] is True and str(body["jobId"]).startswith("JOB-")
    # The durable tombstone is already written, so the product can never come
    # back; the row itself is still there because the RPC has not run yet.
    assert migrated.count("deleted_products", f"product_id = '{pid}'") == 1
    assert migrated.count("products", f"id = '{pid}'") == 1, \
        "the request ran the heavy half inline"

    manual_queue.wait_idle(timeout=30)
    job = manual_queue.get(body["jobId"])
    assert job["state"] == "done", job
    assert migrated.count("products", f"id = '{pid}'") == 0
    assert migrated.count("deleted_products", f"product_id = '{pid}'") == 1
    assert _key(url) not in live.objects
    assert job["result"]["filesRemoved"] == 1


def test_the_sync_hatch_still_blocks_and_still_hard_deletes(
        client, migrated, live):
    """``?sync=1`` keeps the old one-request contract for the runbook."""
    tok = _login(client)
    url = _url("admin-sync.jpg")
    live.objects.add(_key(url))
    pid = "jau-live-4b"
    _save(client, tok, {"id": pid, "name": "Live Four B", "priceNgn": 5000,
                        "stock": 1, "image": url, "images": [url]})

    r = client.delete(f"/api/admin/products/{pid}",
                      headers={"X-CSRF-Token": tok},
                      query_string={"sync": "1"})

    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert body["deleteMode"] == "supabase-hard"
    assert body["filesRemoved"] == 1
    assert migrated.count("products", f"id = '{pid}'") == 0
    assert migrated.count("deleted_products", f"product_id = '{pid}'") == 1
    assert _key(url) not in live.objects


def test_admin_delete_is_503_and_keeps_the_row_without_the_migration(
        client, bare, bare_live):
    """The portal must never claim a Supabase delete the database did not do."""
    tok = _login(client)
    pid = "jau-live-5"
    bare.sql(f"insert into public.products (id, name) "
             f"values ('{pid}', 'Live Five')")

    r = client.delete(f"/api/admin/products/{pid}",
                      headers={"X-CSRF-Token": tok},
                      query_string={"sync": "1"})

    assert r.status_code == 503, r.data
    body = r.get_json()
    assert body["ok"] is False
    assert "Supabase" in body["error"]
    assert body["report"]["deleted"] == []
    assert bare.count("products", f"id = '{pid}'") == 1, "the row was touched"


def test_a_database_without_any_ledger_fails_closed_even_async(
        client, bare, bare_live):
    """No tombstone table AND no ledger table: nothing is queued, nothing is
    claimed.

    The async path is still fail-closed where it matters. Without a durable
    place to record the delete the request answers 503 - it must never hide a
    product on the strength of a tombstone write that did not land, and it must
    never queue a purge for a row that is still live.
    """
    import task_queue
    task_queue.reset()
    tok = _login(client)
    pid = "jau-live-6"
    bare.sql(f"insert into public.products (id, name) "
             f"values ('{pid}', 'Live Six')")

    r = client.delete(f"/api/admin/products/{pid}", headers={"X-CSRF-Token": tok})

    assert r.status_code == 503, r.data
    body = r.get_json()
    assert body["ok"] is False
    assert "tombstone" in body["error"].lower()
    assert task_queue.pending() == 0, "work was queued for a live row"
    assert bare.count("products", f"id = '{pid}'") == 1, "the row must survive"


def test_a_queued_delete_that_keeps_failing_is_reported_not_hidden(
        client, migrated, live, manual_queue, monkeypatch):
    """A queued job that cannot finish parks in the panel with its reason.

    The operator's fear is a product that still sells after a delete; the
    second-worst outcome is a purge that quietly never happened. This pins the
    job panel as the place where that becomes visible, with the row untouched
    and the tombstone keeping the id out of the shop.
    """
    tok = _login(client)
    pid = "jau-live-7"
    _save(client, tok, {"id": pid, "name": "Live Seven", "priceNgn": 5000,
                        "stock": 1})

    real_rpc = supabase_store.hard_delete_products

    def _boom(ids):
        raise RuntimeError("storage: bucket unreachable")

    monkeypatch.setattr(supabase_store, "hard_delete_products", _boom)
    r = client.delete(f"/api/admin/products/{pid}", headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["deleteMode"] == "queued"

    manual_queue.wait_idle(timeout=30)
    job = manual_queue.get(body["jobId"])
    assert job["state"] == "failed", job
    assert "bucket unreachable" in job["lastError"], job
    assert job["attempts"] == manual_queue.MAX_ATTEMPTS
    # The tombstone keeps it out of the shop; the row is still there for the
    # admin to retry against.
    assert migrated.count("deleted_products", f"product_id = '{pid}'") == 1
    assert migrated.count("products", f"id = '{pid}'") == 1

    # Retrying from the panel re-queues it, and the real RPC (through the
    # bridge, against the real database) then completes it.
    monkeypatch.setattr(supabase_store, "hard_delete_products", real_rpc)
    retry = client.post(f"/api/admin/tasks/jobs/{body['jobId']}/retry",
                        headers={"X-CSRF-Token": tok})
    assert retry.status_code == 200, retry.data
    manual_queue.wait_idle(timeout=30)
    assert manual_queue.get(body["jobId"])["state"] == "done"
    assert migrated.count("products", f"id = '{pid}'") == 0


# ------------------------------------------------------------- image replace
def test_a_photo_replacement_saves_first_and_purges_only_unshared_media(
        client, migrated, live):
    """Replacement is a save: new gallery persisted, old object purged only
    when nothing else shows it, and the delete RPC is never called."""
    tok = _login(client)
    shared, own_a, own_b, new_a = (_url("shared.jpg"), _url("own-a.jpg"),
                                   _url("own-b.jpg"), _url("new-a.jpg"))
    for url in (shared, own_a, own_b, new_a):
        live.objects.add(_key(url))

    _save(client, tok, {"id": "jau-live-6a", "name": "Live Six A",
                        "priceNgn": 4000, "stock": 1,
                        "image": shared, "images": [shared, own_a]})
    _save(client, tok, {"id": "jau-live-6b", "name": "Live Six B",
                        "priceNgn": 4000, "stock": 1,
                        "image": shared, "images": [shared, own_b]})
    live.removed.clear()
    live.rpc_calls.clear()

    r = client.put("/api/admin/products/jau-live-6a/media",
                   json={"images": [new_a]}, headers={"X-CSRF-Token": tok})

    assert r.status_code == 200, r.data
    saved = r.get_json()["product"]
    assert saved["image"] == saved["image_url"] == new_a
    assert saved["images"] == [new_a]
    # the row really moved in PostgreSQL
    row = migrated.json_rows(
        "select coalesce(json_agg(r), '[]')::text from (select image, images "
        "from public.products where id = 'jau-live-6a') r")[0]
    assert row["image"] == new_a
    assert row["images"] == [new_a]
    # the replaced, now-unreferenced object is gone; the shared one is NOT
    assert _key(own_a) not in live.objects, "the replaced object was kept"
    assert _key(shared) in live.objects, "a photo another product still shows was deleted"
    assert _key(new_a) in live.objects
    assert live.rpc_calls == [], "replacement must never call the delete RPC"

    # ...and once the last reference goes, the shared object may be purged.
    _save(client, tok, {"id": "jau-live-6b", "name": "Live Six B",
                        "priceNgn": 4000, "stock": 1,
                        "image": own_b, "images": [own_b]})
    assert _key(shared) not in live.objects
    assert _key(own_b) in live.objects
    assert live.rpc_calls == []
