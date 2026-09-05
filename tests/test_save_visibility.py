"""A product save must survive a narrower Supabase products table.

The incident: the hand-built products table has fewer columns than the row the
app writes, so PostgREST answered PGRST204 ("Could not find the 'compareCfa'
column of 'products' in the schema cache") and *every* product save was
rejected. Reads kept working, so the shop looked fine while the owner's save
sat in the server's local override file - invisible to the other phone and
wiped by the next deploy.

These tests hold the two halves of the fix:

* supabase_store writes whatever row the table actually accepts (drop the
  rejected column, retry), and reports the write as failed - mirrored=False -
  when the table rejects a column the product cannot exist without, or when it
  fails for any other reason.
* the admin save then really goes live: the product reaches the fake Supabase
  table and /api/catalog-level reads see it.

Run with:  python3 -m pytest tests -q
No real Supabase is needed: supabase_store.client() is monkeypatched to an
in-memory fake that enforces a column allowlist, exactly like a table with a
subset of the columns.
"""
import os
import re
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import supabase_store  # noqa: E402
from config import Config  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    authmod.ensure_seed_admins()
    authmod.set_password(EMAIL, PW)
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


@pytest.fixture(autouse=True)
def _supabase_configured(monkeypatch):
    """Pretend Supabase is configured, so a save genuinely has to mirror.

    Without this the test suite runs with no SUPABASE_URL / service key, and
    upsert_products() short-circuits to "nothing to mirror" - which would make
    every assertion below pass for the wrong reason.
    """
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)


@pytest.fixture()
def iso_catalog():
    """Run against an empty catalogue override file, then put it back.

    Every test file shares /tmp/jaura_test_catalog.json. A row left there by an
    earlier test would change what merged() returns (and a stray .bak would be
    restored instead), so this file starts from a clean, isolated catalogue.
    """
    path = catalog_mod._norm_filename(catalog_mod.CATALOG_FILE)
    bak = path + ".bak"
    saved = {p: (open(p, "rb").read() if os.path.isfile(p) else None)
             for p in (path, bak)}
    catalog_mod._write_overrides(
        {"products": [], "deleted": [], "updatedAt": "", "updatedBy": ""}, path)
    try:
        yield path
    finally:
        for p, raw in saved.items():
            try:
                if raw is None:
                    if os.path.isfile(p):
                        os.remove(p)
                else:
                    with open(p, "wb") as fh:
                        fh.write(raw)
            except OSError:
                pass


# ------------------------------------------------------------------ fakes
def _as_list(rows):
    """PostgREST accepts a single object as well as an array; normalise both."""
    if not rows:
        return []
    return rows if isinstance(rows, list) else [rows]


class _FakeTable:
    """In-memory stand-in for one PostgREST table (upsert / select / update).

    The rows live on the client that owns the table (self._o.tables[name]) so a
    subclass can consult the client's own settings while validating a write -
    see _StrictTable.
    """

    def __init__(self, owner, name):
        self._o = owner
        self._name = name
        self._filter = None
        self._in = None
        self._update = None
        self._window = None

    def upsert(self, rows):
        store = self._o.tables.setdefault(self._name, [])
        for r in _as_list(rows):
            r = dict(r or {})
            match = "key" if self._name == "growth_settings" else "id"
            store[:] = [x for x in store if x.get(match) != r.get(match)]
            store.append(r)
        return self

    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n=None):
        return self

    def range(self, lo, hi):
        self._window = (lo, hi)
        return self

    def update(self, payload):
        self._update = payload or {}
        return self

    def eq(self, key, val):
        self._filter = (key, val)
        return self

    def in_(self, key, vals):
        self._in = (key, [str(v) for v in (vals or [])])
        return self

    def execute(self):
        store = self._o.tables.setdefault(self._name, [])
        rows = list(store)
        if self._filter:
            k, v = self._filter
            rows = [r for r in rows if r.get(k) == v]
        if self._in:
            k, vals = self._in
            rows = [r for r in rows if str(r.get(k)) in vals]
        if self._update is not None:
            for r in rows:
                r.update(self._update)
        count = len(rows)
        if self._window:
            lo, hi = self._window
            rows = rows[lo:hi + 1]
        return {"data": rows, "count": count}


class _FakeSupabase:
    """A reachable Supabase whose tables are plain in-memory lists."""

    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _FakeTable(self, name)


class _BoomSupabase:
    """A configured but broken Supabase: every table access raises."""

    def table(self, name):
        raise RuntimeError(f"connection reset while writing {name}")


# The exact shape migrate_supabase.py creates in Supabase. The app writes more
# keys than this (images, optionStock, usesPlaceholder, ...) and production
# historically lacked compareCfa - that mismatch is what killed every save.
MIGRATE_COLUMNS = {"id", "sku", "slug", "name", "nameFr", "category",
                   "priceCfa", "compareCfa", "priceNgn", "compareNgn",
                   "image", "placeholderImage", "description", "stock",
                   "badge", "featured", "online", "colors", "options",
                   "source", "updated_at"}


class _StrictTable(_FakeTable):
    """Reject a row carrying a column the table does not have, like PostgREST.

    The error text is the production one, word for word, including the
    PGRST204 code: the resilient writer keys off it.
    """

    def upsert(self, rows):
        for r in _as_list(rows):
            for key in (r or {}):
                if key not in self._o.allowed:
                    raise Exception(
                        f"Could not find the '{key}' column of 'products' "
                        "in the schema cache (PGRST204)")
        return super().upsert(rows)


class _StrictSupabase(_FakeSupabase):
    """A products table narrowed to `allowed`; every other table is permissive."""

    def __init__(self, allowed):
        super().__init__()
        self.allowed = set(allowed)

    def table(self, name):
        if name == "products":
            return _StrictTable(self, name)
        return super().table(name)


def _full_row(pid="jau-narrow-1"):
    return {"id": pid, "sku": "JAU-1", "slug": pid, "name": f"Vis {pid}",
            "nameFr": "", "category": "beauty", "priceCfa": 4400,
            "compareCfa": None, "priceNgn": 10000, "compareNgn": None,
            "image": "", "images": [], "description": "", "stock": 7,
            "badge": "", "featured": False, "online": True,
            "colors": [], "options": [], "optionStock": {}}


def _row(pid="jau-visible-1", **over):
    """A minimal, valid admin payload - what the admin portal posts for a new
    product. catalog.normalize() expands it into the full row the mirror sends."""
    row = {"id": pid, "sku": "JAU-VIS", "slug": pid, "name": f"Vis {pid}",
           "category": "beauty", "priceCfa": 4400, "priceNgn": 10000,
           "stock": 7, "online": True}
    row.update(over)
    return row


# ------------------------------------------------------------------- writes
def test_upsert_adapts_to_narrow_table(monkeypatch):
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    assert supabase_store.upsert_products([_full_row()]) is True
    stored = [x for x in fake.tables["products"]
              if x.get("id") == "jau-narrow-1"][0]
    assert stored["stock"] == 7 and stored["priceNgn"] == 10000
    assert stored["name"] == "Vis jau-narrow-1"
    assert "compareCfa" not in stored
    assert "images" not in stored and "optionStock" not in stored


def test_upsert_refuses_critical_column_drop(monkeypatch):
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa", "stock"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    assert supabase_store.upsert_products([_full_row()]) is False
    assert fake.tables.get("products", []) == []


def test_upsert_false_on_other_errors(monkeypatch):
    monkeypatch.setattr(supabase_store, "client", lambda: _BoomSupabase())
    assert supabase_store.upsert_products([_full_row()]) is False


def test_bulk_replace_adapts_to_narrow_table(monkeypatch):
    """The CSV / bulk import mirrors through the same resilient path.

    A rejected column must not cost the whole catalogue: the tombstone pass
    still runs and every row lands with the columns the table has. The caller's
    own dicts stay untouched (a retry edits a copy, not the product the
    request will hand back).
    """
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    first = _full_row("jau-narrow-3")
    supabase_store.replace_all_products([first, _full_row("jau-narrow-4")])
    stored = fake.tables["products"]
    assert {"jau-narrow-3", "jau-narrow-4"} <= {x.get("id") for x in stored}
    assert all("compareCfa" not in x and x["stock"] == 7 for x in stored)
    assert all(x["source"] == "admin" for x in stored)
    assert "compareCfa" in first and first["stock"] == 7


# ---------------------------------------------------------------- end to end
def test_save_goes_live_on_narrow_table(client, iso_catalog, monkeypatch):
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    tok = login(client)
    r = client.post("/api/admin/products",
                    json={"product": _row("jau-narrow-2")},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert r.get_json()["mirrored"] is True
    ids = {str(p.get("id")) for p in catalog_mod.merged(include_hidden=True)}
    assert "jau-narrow-2" in ids


# ------------------------------------------------- each kind in its own table
def _order_body(oid="JA-VIS01"):
    """A shopper's completed checkout, as js/store.js posts it."""
    return {"id": oid, "currency": "CFA", "total": 15000,
            "customer": {"name": "Ama", "email": "ama@example.com",
                         "phone": "+229 90 00 00 00", "city": "Cotonou",
                         "zone": "Cotonou", "address": "Rue 12"},
            "items": [{"id": "wix-001", "name": "Bag", "qty": 1,
                       "price": 15000}]}


def _fresh_order(*oids):
    """Drop these order ids from the shared SQLite file first.

    The suite shares /tmp/jaura_test.db across runs, and a checkout whose id
    is already stored short-circuits as a duplicate - which would skip the
    mirror this test is about and read as a false failure.
    """
    for oid in oids:
        execute("DELETE FROM orders WHERE id=?", (oid,))


def test_each_kind_of_data_lands_in_its_own_table(client, iso_catalog, monkeypatch):
    """Products -> products, an order -> orders, categories -> growth_settings."""
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(Config, "SUPABASE_ENABLED", True)   # mirrors on
    # the category table lives in the repo; point the PUT at /tmp so the
    # suite never rewrites a shipped data file
    import api as api_mod
    cats_file = "/tmp/jaura_test_categories.json"
    for f in (cats_file, cats_file + ".bak"):
        if os.path.isfile(f):
            os.remove(f)
    monkeypatch.setattr(api_mod, "CATEGORIES_FILE", cats_file)
    _fresh_order("JA-VIS01")

    tok = login(client)
    r = client.post("/api/admin/products", json={"product": _row(
        "jau-tbl-products", category="skincare")}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert r.get_json()["mirrored"] is True

    client.put("/api/admin/categories", json={"categories": [
        {"id": "beauty", "name": "Beauty"},
        {"id": "gift-set", "name": "Gift Sets & Packaging"}]},
        headers={"X-CSRF-Token": tok})
    csrf = client.get("/api/csrf").get_json()["token"]
    o = client.post("/api/orders", json=_order_body(),
                    headers={"X-CSRF-Token": csrf})
    assert o.status_code == 200, o.data

    stored = [x for x in fake.tables["products"]
              if x.get("id") == "jau-tbl-products"][0]
    served = {str(p["id"]): p for p in catalog_mod.merged(include_hidden=True)}
    assert served["jau-tbl-products"]["category"] == "beauty"
    assert stored["name"] and stored["priceNgn"] and stored["source"] == "admin"

    orders = fake.tables.get("orders", [])
    assert [x["id"] for x in orders] == ["JA-VIS01"], "the sale never reached orders"
    assert orders[0]["customer_name"] == "Ama" and orders[0]["total"] == 15000
    assert orders[0]["status"] == "pending"
    assert json.loads(orders[0]["payload"])["items"][0]["id"] == "wix-001"

    settings = {x.get("key"): x.get("value")
                for x in fake.tables.get("growth_settings", [])}
    assert "categories_json" in settings, "the category table was not mirrored"
    assert {c["id"] for c in json.loads(settings["categories_json"])} == {
        "beauty", "gift-set"}
    assert not [x for x in fake.tables["products"] if x.get("id") == "JA-VIS01"]


def test_a_broken_mirror_never_loses_a_sale(client, iso_catalog, monkeypatch):
    """client() blowing up must not turn a paid order into a 500."""
    def _angry_client():
        raise RuntimeError("supabase is not answering")

    monkeypatch.setattr(supabase_store, "client", _angry_client)
    monkeypatch.setattr(Config, "SUPABASE_ENABLED", True)
    _fresh_order("JA-VIS02")

    csrf = client.get("/api/csrf").get_json()["token"]
    r = client.post("/api/orders", json=_order_body("JA-VIS02"),
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.data[:200]
    assert r.get_json()["ok"] is True
    assert one("SELECT id FROM orders WHERE id='JA-VIS02'")["id"] == "JA-VIS02"


def test_create_order_accepts_the_row_alone(monkeypatch):
    """The mirror is called with the order row and nothing else."""
    fake = _FakeSupabase()
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    assert supabase_store.create_order({"id": "JA-SIG1", "total": 1}) is None
    assert fake.tables["orders"][0]["id"] == "JA-SIG1"
    # no payload key on the row: the mirror stores the whole order
    assert json.loads(fake.tables["orders"][0]["payload"]) == {
        "id": "JA-SIG1", "total": 1}
    assert supabase_store.create_order({"id": "JA-SIG2"}, None) is None


def test_products_ddl_covers_every_column_the_app_writes():
    """supabase_schema.sql must list every key the products upsert sends."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sql = open(os.path.join(root, "supabase_schema.sql"), encoding="utf-8").read()
    m = re.search(r"create table if not exists products \((.*?)\n\);", sql, re.S)
    assert m, "no products table in supabase_schema.sql"
    cols = set()
    for line in m.group(1).splitlines():
        line = line.strip().rstrip(",")
        if not line or line.lower().startswith(
                ("primary", "foreign", "unique", "constraint", "--")):
            continue
        cols.add(line.split()[0].strip('"'))
    written = catalog_mod.resolve_image(catalog_mod.normalize(
        {"id": "jau-ddl", "name": "Ddl", "priceNgn": 1000}))
    written.setdefault("source", "admin")
    written.setdefault("updated_at", supabase_store._now())
    missing = sorted(set(written) - cols)
    assert not missing, f"products DDL is missing column(s): {missing}"


def test_no_supabase_store_call_site_is_out_of_date():
    """Every supabase_store call in the app must match the function's signature.

    The checkout mirror was a TypeError for exactly this reason: the route
    and the helper disagreed, and the path only runs when Supabase is
    configured - which the suite never is.
    """
    import ast
    import inspect
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad = []
    for fname in ("api.py", "catalog.py", "db.py", "app.py", "scheduler.py",
                  "repo_sync.py", "storage.py", "analytics.py", "growth.py",
                  "emailer.py", "auth.py", "migrate_supabase.py",
                  "backfill_supabase_orders.py"):
        path = os.path.join(root, fname)
        if not os.path.isfile(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=fname)
        names, module_alias = {}, set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "supabase_store":
                for al in node.names:
                    names[al.asname or al.name] = al.name
            elif isinstance(node, ast.Import):
                for al in node.names:
                    if al.name == "supabase_store":
                        module_alias.add(al.asname or al.name)
        if not names and not module_alias:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Name):
                local, attr = f.id, None
            elif (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id in module_alias):
                local, attr = f.attr, f.attr
            else:
                continue
            target_name = attr if attr is not None else names.get(local)
            if target_name is None:
                continue
            fn = getattr(supabase_store, target_name, None)
            if not callable(fn):
                continue
            try:
                sig = inspect.signature(fn)
            except (TypeError, ValueError):
                continue
            params = list(sig.parameters.values())
            if any(p.kind == p.VAR_POSITIONAL for p in params):
                continue
            positional = [p for p in params
                          if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            required = [p.name for p in positional if p.default is p.empty]
            given = len(node.args) + len([k for k in node.keywords if k.arg is None])
            kwnames = {k.arg for k in node.keywords if k.arg}
            missing = [n for n in required[given:] if n not in kwnames]
            extra_kw = set()
            if not any(p.kind == p.VAR_KEYWORD for p in params):
                extra_kw = kwnames - {p.name for p in params}
            if missing or extra_kw:
                bad.append(f"{fname}:{node.lineno} {target_name}() "
                           f"missing={missing or '-'} "
                           f"unknown_kw={sorted(extra_kw) or '-'} [sig {sig}]")
    assert not bad, "out-of-date supabase_store call site(s):\n  " + "\n  ".join(bad)


# ------------------------------------------------------------ TASK 3: catalog
def test_narrow_row_shadows_no_fields(client, iso_catalog, monkeypatch):
    """A dropped column must not hide what this server still holds."""
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    tok = login(client)
    r = client.post("/api/admin/products", json={"product": _row(
        "jau-fill", images=["images/brand/logo.jpg", "images/products/shoes.jpg"],
        optionStock={"Small": 2, "Large": 3})}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    stored = [x for x in fake.tables["products"] if x.get("id") == "jau-fill"][0]
    assert "images" not in stored and "optionStock" not in stored   # table lacks them
    p = {str(x["id"]): x for x in catalog_mod.merged(include_hidden=True)}["jau-fill"]
    assert len(p.get("images") or []) == 2, "the gallery was shadowed"
    assert (p.get("optionStock") or {}).get("Large") == 3, "variant stock was shadowed"


def test_same_named_products_from_two_phones_both_show(client, iso_catalog, monkeypatch):
    """Two different pieces called the same thing are not one product."""
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    tok = login(client)
    # explicit slugs: the _row helper defaults slug to the id, and the whole
    # point here is two products asking for the SAME slug
    a = client.post("/api/admin/products", json={"product": _row(
        "jau-name-a", name="Facial Soap", slug="facial-soap", sku="JAU-A",
        priceNgn=2000)}, headers={"X-CSRF-Token": tok})
    b = client.post("/api/admin/products", json={"product": _row(
        "jau-name-b", name="Facial Soap", slug="facial-soap", sku="JAU-B",
        priceNgn=9000)}, headers={"X-CSRF-Token": tok})
    assert a.status_code == b.status_code == 200
    assert a.get_json()["mirrored"] and b.get_json()["mirrored"]
    served = [p for p in catalog_mod.merged(include_hidden=True)
              if str(p.get("name", "")) == "Facial Soap"]
    assert {p["id"] for p in served} == {"jau-name-a", "jau-name-b"}
    slugs = {p["slug"] for p in served}
    assert len(slugs) == 2, "both kept the same slug and one will hide"
    assert "facial-soap-2" in slugs
    assert b.get_json()["product"]["slug"] == "facial-soap-2"
    # a re-save of an existing product must not keep appending suffixes
    again = client.post("/api/admin/products", json={"product": _row(
        "jau-name-b", name="Facial Soap", slug="facial-soap-2", sku="JAU-B",
        priceNgn=9500)}, headers={"X-CSRF-Token": tok})
    assert again.get_json()["product"]["slug"] == "facial-soap-2"
    assert again.get_json()["mirrored"] is True


def test_reimporting_the_same_batch_does_not_rename_slugs(client, iso_catalog,
                                                          monkeypatch):
    """PUTting the same CSV twice must not churn every slug to -2, -3, ..."""
    fake = _StrictSupabase(MIGRATE_COLUMNS - {"compareCfa"})
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    tok = login(client)
    batch = [{"id": "jau-shea-a", "name": "Shea Butter", "slug": "shea-butter",
              "sku": "SHEA-A", "priceNgn": 3000},
             {"id": "jau-shea-b", "name": "Shea Butter", "slug": "shea-butter-2",
              "sku": "SHEA-B", "priceNgn": 4000}]
    r1 = client.put("/api/admin/products", json={"products": batch},
                    headers={"X-CSRF-Token": tok})
    assert r1.status_code == 200, r1.data
    r2 = client.put("/api/admin/products", json={"products": batch},
                    headers={"X-CSRF-Token": tok})
    assert r2.status_code == 200, r2.data
    slugs = {p["id"]: p["slug"] for p in catalog_mod.merged(include_hidden=True)
             if p["id"] in ("jau-shea-a", "jau-shea-b")}
    assert slugs == {"jau-shea-a": "shea-butter",
                     "jau-shea-b": "shea-butter-2"}, slugs
