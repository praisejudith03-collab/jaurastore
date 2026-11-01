"""Permanent backend safety fallback for missing optional products columns.

Covers the production 'optionSupplierCost' / 'optionsSupplierCost' save failure:
  1. Product saves succeed (HTTP 200, live on the storefront) when optional
     columns such as optionSupplierCost, supplierCost, is_deleted, is_active,
     supplier_status, enableCustomNote, customNotePrompt, dimensions, bulkQty
     or bulkPercent are absent from the live Supabase products table.
  2. Non-default values entered for missing optional columns are durably stored
     in growth_settings (product_optional_fields_json), merged back on every
     read, cleared cleanly when emptied, and stripped from public endpoints.
  3. Typo/alias spellings (including optionsSupplierCost) map cleanly to
     optionSupplierCost across normalize, upsert, field-patch and /variants,
     never reaching PostgREST as an unknown column name.
  4. When a DDL backend is available, auto_migrate.ensure_product_columns
     provisions the missing column on the spot and reloads PostgREST's schema.
  5. Clicking Retry Save / re-saving a product purges superseded queued POSTs
     for that product id from the client outbox.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

from _pw import PW  # noqa: E402
import app as appmod  # noqa: E402
import auto_migrate  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import supabase_store  # noqa: E402
from db import execute, init_db  # noqa: E402
from test_save_visibility import (  # noqa: E402
    MIGRATE_COLUMNS,
    _StrictSupabase,
)

EMAIL = "jaurastore@gmail.com"

# Simulate the exact narrower production table where optionSupplierCost,
# supplierCost, is_deleted, is_active and supplier_status have not been added.
LEGACY_PRODUCTION_COLUMNS = (
    MIGRATE_COLUMNS
    - {
        "optionSupplierCost",
        "supplierCost",
        "is_deleted",
        "is_active",
        "supplier_status",
        "enableCustomNote",
        "customNotePrompt",
    }
)


class _StrictUpdateAndUpsertTable:
    """Rejects unknown columns on BOTH .upsert() and .update(), like PostgREST."""

    def __init__(self, owner, name):
        self._o = owner
        self._name = name
        self._filter = None
        self._in = None
        self._update = None
        self._window = None

    def _check_keys(self, keys):
        if self._name != "products":
            return
        for key in sorted(keys):
            if key not in self._o.allowed:
                self._o.rejections.append(key)
                if self._o.style == "pg":
                    raise Exception(
                        f'column "{key}" of relation "products" does not exist')
                raise Exception(
                    f"Could not find the '{key}' column of 'products' "
                    "in the schema cache (PGRST204)")

    def upsert(self, rows):
        items = rows if isinstance(rows, list) else ([rows] if rows else [])
        if self._name == "products":
            self._o.upsert_calls += 1
        for r in items:
            self._check_keys((r or {}).keys())
        store = self._o.tables.setdefault(self._name, [])
        for r in items:
            r = dict(r or {})
            match = "key" if self._name == "growth_settings" else "id"
            store[:] = [x for x in store if x.get(match) != r.get(match)]
            store.append(r)
        return self

    def update(self, payload):
        self._check_keys((payload or {}).keys())
        if self._name == "products":
            self._o.update_calls += 1
        self._update = dict(payload or {})
        return self

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, _n=None):
        return self

    def range(self, lo, hi):
        self._window = (lo, hi)
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
        return {"data": [dict(r) for r in rows], "count": count}


class _LegacyProdSupabase(_StrictSupabase):
    def __init__(self, allowed=LEGACY_PRODUCTION_COLUMNS, style="postgrest"):
        super().__init__(allowed)
        self.style = style
        self.rejections = []
        self.upsert_calls = 0
        self.update_calls = 0

    def table(self, name):
        return _StrictUpdateAndUpsertTable(self, name)


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", "/tmp/jaura_test.db")
    init_db()
    execute("DELETE FROM rate_limits")
    cat_file = tmp_path / "opt-col-catalog.json"
    cat_file.write_text(json.dumps({"products": [], "deleted": []}))
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(cat_file))
    monkeypatch.setattr(catalog_mod, "_sync_repo_async", lambda: None)
    monkeypatch.setattr(supabase_store, "_OPTIONAL_COLUMN_NOTED", set())
    monkeypatch.setattr(supabase_store, "_MISSING_PRODUCT_COLUMNS", {})
    monkeypatch.setattr(supabase_store, "_PRODUCT_HEAL_ATTEMPTED", set())
    application = appmod.create_app()
    application.config.update(TESTING=True)
    with application.test_client() as c:
        r = c.post("/api/admin/login", json={"email": EMAIL, "password": PW})
        assert r.status_code == 200, r.data
        yield c


def _csrf(client):
    return client.get("/api/admin/session").get_json()["csrf"]


def test_full_product_save_succeeds_and_publishes_when_option_supplier_cost_is_missing(
        admin_client, monkeypatch):
    """Exact production scenario: products table lacks optionSupplierCost,
    supplierCost, is_deleted, enableCustomNote, customNotePrompt."""
    fake = _LegacyProdSupabase()
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(catalog_mod, "_prod_source", lambda: True)

    tok = _csrf(admin_client)
    res = admin_client.post(
        "/api/admin/products",
        headers={"X-CSRF-Token": tok},
        json={
            "product": {
                "id": "jau-prod-missing-cols-1",
                "name": "Velvet Clutch Bag",
                "category": "accessories",
                "priceNgn": 14000,
                "stock": 5,
                "online": True,
                "options": [{"title": "Size", "values": ["Small", "Big"]}],
                "optionPrices": {"Size: Small": 14000, "Size: Big": 17000},
                "optionStock": {"Size: Small": 2, "Size: Big": 3},
                "supplierCost": 6500,
                "optionSupplierCost": {"Size: Small": 5800, "Size: Big": 7200},
                "enableCustomNote": True,
                "customNotePrompt": "Preferred colour or monogram",
            }
        },
    )
    assert res.status_code == 200, res.data
    body = res.get_json()
    assert body["ok"] is True
    assert body["live"] is True
    assert supabase_store.last_write_blocker() == ""

    # Stored row in products has NO missing columns, yet sidecar preserves values.
    raw_stored = next(
        r for r in fake.tables["products"] if r["id"] == "jau-prod-missing-cols-1")
    assert "optionSupplierCost" not in raw_stored
    assert "supplierCost" not in raw_stored
    assert "is_deleted" not in raw_stored

    # Admin / internal read-back preserves every optional field without data loss.
    assert body["product"]["supplierCost"] == 6500
    assert body["product"]["optionSupplierCost"] == {
        "Size: Small": 5800, "Size: Big": 7200}
    assert body["product"]["enableCustomNote"] is True
    assert body["product"]["customNotePrompt"] == "Preferred colour or monogram"

    # Public storefront sees the product immediately and NEVER sees supplier costs.
    pub = admin_client.get("/api/catalog").get_json()
    shop_item = next(
        p for p in pub["products"] if p["id"] == "jau-prod-missing-cols-1")
    assert shop_item["name"] == "Velvet Clutch Bag"
    assert shop_item["enableCustomNote"] is True
    assert "supplierCost" not in shop_item
    assert "optionSupplierCost" not in shop_item
    assert "optionsSupplierCost" not in shop_item


def test_fast_field_patch_succeeds_when_only_option_supplier_cost_is_edited(
        admin_client, monkeypatch):
    """Editing ONLY optionSupplierCost / supplierCost on an existing product
    via mergeBase + mergeFields succeeds on a legacy table and persists via sidecar."""
    fake = _LegacyProdSupabase()
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(catalog_mod, "_prod_source", lambda: True)
    tok = _csrf(admin_client)

    # 1. Create the product first with no supplier costs.
    create_res = admin_client.post(
        "/api/admin/products",
        headers={"X-CSRF-Token": tok},
        json={
            "product": {
                "id": "jau-fast-cost-1",
                "name": "Satin Scarf",
                "category": "accessories",
                "priceNgn": 8000,
                "stock": 4,
                "online": True,
                "options": [{"title": "Colour", "values": ["Gold", "Plum"]}],
                "optionStock": {"Colour: Gold": 2, "Colour: Plum": 2},
            }
        },
    )
    assert create_res.status_code == 200, create_res.data
    base = create_res.get_json()["product"]

    # 2. Fast-patch ONLY supplierCost and optionSupplierCost.
    edited = dict(
        base,
        supplierCost=3200,
        optionSupplierCost={"Colour: Gold": 3000, "Colour: Plum": 3400},
        mergeBase=base,
        mergeFields=["supplierCost", "optionSupplierCost"],
    )
    patch_res = admin_client.post(
        "/api/admin/products",
        headers={"X-CSRF-Token": tok},
        json={"product": edited},
    )
    assert patch_res.status_code == 200, patch_res.data
    updated = patch_res.get_json()["product"]
    assert updated["supplierCost"] == 3200
    assert updated["optionSupplierCost"] == {
        "Colour: Gold": 3000, "Colour: Plum": 3400}

    # 3. Clearing supplier costs removes them from the sidecar cleanly.
    cleared = dict(
        updated,
        supplierCost=None,
        optionSupplierCost={},
        mergeBase=updated,
        mergeFields=["supplierCost", "optionSupplierCost"],
    )
    clear_res = admin_client.post(
        "/api/admin/products",
        headers={"X-CSRF-Token": tok},
        json={"product": cleared},
    )
    assert clear_res.status_code == 200, clear_res.data
    after_clear = clear_res.get_json()["product"]
    assert after_clear["supplierCost"] is None
    assert after_clear["optionSupplierCost"] == {}


def test_options_supplier_cost_plural_alias_maps_cleanly_everywhere(
        admin_client, monkeypatch):
    """'optionsSupplierCost' (plural) maps to 'optionSupplierCost' in normalize,
    direct resilient upsert, canonicalize, and /api/products/variants."""
    clean = catalog_mod.normalize({
        "name": "Alias Piece",
        "priceNgn": 5000,
        "options": [{"title": "Size", "values": ["S", "M"]}],
        "optionsSupplierCost": {"Size: S": 2100, "Size: M": 2400},
    })
    assert clean["optionSupplierCost"] == {"Size: S": 2100, "Size: M": 2400}
    assert "optionsSupplierCost" not in clean

    # Direct backend write carrying raw 'optionsSupplierCost' against Postgres 42703:
    fake = _LegacyProdSupabase(style="pg")
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(catalog_mod, "_prod_source", lambda: True)

    raw_row = {
        "id": "jau-plural-alias-1",
        "sku": "JAU-PA1",
        "slug": "jau-plural-alias-1",
        "name": "Plural Alias Item",
        "category": "beauty",
        "priceNgn": 6000,
        "priceCfa": 2640,
        "stock": 3,
        "stock_quantity": 3,
        "image": "",
        "image_url": "",
        "images": [],
        "online": True,
        "source": "admin",
        "optionsSupplierCost": {"Size: S": 1900},
    }
    assert supabase_store._upsert_products_resilient([raw_row]) is True
    assert "optionsSupplierCost" not in fake.rejections
    loaded = supabase_store.product_by_id("jau-plural-alias-1")
    assert loaded["optionSupplierCost"] == {"Size: S": 1900}

    # /api/products/variants also accepts optionsSupplierCost.
    tok = _csrf(admin_client)
    vres = admin_client.put(
        "/api/products/variants",
        headers={"X-CSRF-Token": tok},
        json={
            "productId": "jau-plural-alias-1",
            "optionsSupplierCost": {"Size: S": 2050},
            "supplierCost": 2050,
        },
    )
    assert vres.status_code == 200, vres.data
    assert vres.get_json()["product"]["optionSupplierCost"] == {"Size: S": 2050}
    assert vres.get_json()["product"]["supplierCost"] == 2050


def test_auto_provisioning_heals_missing_product_column_when_ddl_backend_exists(
        monkeypatch):
    """When auto_migrate has a DDL backend, missing optionSupplierCost is
    created automatically and the value lands directly in products."""
    fake = _LegacyProdSupabase(
        allowed=(MIGRATE_COLUMNS | {"is_active"}) - {"optionSupplierCost"}
    )
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(supabase_store, "_PRODUCT_HEAL_ATTEMPTED", set())
    monkeypatch.setattr(supabase_store, "_MISSING_PRODUCT_COLUMNS", {})

    healed_cols = []

    def fake_ensure_product_columns(columns=None, logger=None):
        cols = list(columns or [])
        healed_cols.extend(cols)
        fake.allowed.update(cols)
        return True

    monkeypatch.setattr(
        auto_migrate, "ensure_product_columns", fake_ensure_product_columns)

    stmts = auto_migrate.product_statements(["optionSupplierCost"])
    assert stmts[0] == (
        'alter table public.products add column if not exists '
        '"optionSupplierCost" jsonb')
    assert "notify pgrst, 'reload schema'" in stmts

    ok = supabase_store.upsert_products([{
        "id": "jau-healed-1",
        "sku": "JAU-H1",
        "slug": "jau-healed-1",
        "name": "Auto Healed Bag",
        "category": "beauty",
        "priceCfa": 4400,
        "compareCfa": None,
        "priceNgn": 10000,
        "compareNgn": None,
        "image": "",
        "images": [],
        "description": "",
        "stock": 5,
        "badge": "",
        "featured": False,
        "online": True,
        "colors": [],
        "options": [{"title": "Size", "values": ["One"]}],
        "optionStock": {"Size: One": 5},
        "optionPrices": {},
        "optionSupplierCost": {"Size: One": 4200},
    }])
    assert ok is True
    assert healed_cols == ["optionSupplierCost"]
    stored = fake.tables["products"][0]
    assert stored["optionSupplierCost"] == {"Size: One": 4200}


def test_production_shape_cache_avoids_repeated_missing_column_roundtrips(
        monkeypatch):
    """Once missing optional columns are learned in production, the next save
    omits them up front and lands in a single PostgREST upsert call."""
    fake = _LegacyProdSupabase()
    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(supabase_store, "_product_shape_cache_enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "_MISSING_PRODUCT_COLUMNS", {})
    monkeypatch.setattr(supabase_store, "_PRODUCT_HEAL_ATTEMPTED", set())

    row1 = catalog_mod.normalize({"id": "jau-cache-1", "name": "First", "priceNgn": 3000, "stock": 2})
    assert supabase_store.upsert_products([row1]) is True
    first_calls = fake.upsert_calls
    assert first_calls > 1  # learned the missing optional columns

    fake.upsert_calls = 0
    row2 = catalog_mod.normalize({"id": "jau-cache-2", "name": "Second", "priceNgn": 4000, "stock": 3})
    assert supabase_store.upsert_products([row2]) is True
    assert fake.upsert_calls == 1  # zero extra roundtrips on subsequent saves


def test_retry_save_discards_superseded_outbox_jobs_for_same_product():
    """Re-saving a product in js/store.js discards any older queued POST for
    the same product.id so Retry Save never stacks stale retries."""
    script = textwrap.dedent(r"""
        import { readFileSync } from "node:fs";
        import vm from "node:vm";

        const netSrc = readFileSync("js/net.js", "utf8");
        const storeSrc = readFileSync("js/store.js", "utf8");

        const storage = new Map();
        const localStorage = {
          getItem: (k) => (storage.has(k) ? storage.get(k) : null),
          setItem: (k, v) => storage.set(k, String(v)),
          removeItem: (k) => storage.delete(k),
          clear: () => storage.clear(),
        };
        const el = () => ({
          id: "", className: "", innerHTML: "", style: {}, dataset: {},
          classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
          addEventListener() {}, setAttribute() {}, removeAttribute() {},
          appendChild() {}, getAttribute: () => null, querySelectorAll: () => [],
          querySelector: () => null,
        });
        let shouldFail = true;
        const sandbox = {
          console, setTimeout, clearTimeout, setInterval, clearInterval,
          Date, Math, JSON, Number, String, Array, Object, Boolean, Set, Map,
          Promise, Error, AbortController, AbortSignal, FormData, RegExp,
          encodeURIComponent, decodeURIComponent, isFinite, parseFloat, parseInt,
          URL, URLSearchParams,
          CustomEvent: class CustomEvent { constructor(t, o) { this.type = t; this.detail = o && o.detail; } },
          Event: class Event { constructor(t) { this.type = t; } },
          indexedDB: undefined,
          localStorage, sessionStorage: localStorage,
          navigator: { onLine: true, language: "en-NG" },
          location: { href: "https://jaurastore.com.ng/admin.html", pathname: "/admin.html", search: "", hash: "", origin: "https://jaurastore.com.ng" },
          history: { replaceState() {} },
          document: {
            readyState: "complete", hidden: false,
            documentElement: Object.assign(el(), { lang: "en" }),
            head: el(),
            body: Object.assign(el(), { dataset: { page: "admin" } }),
            addEventListener() {}, dispatchEvent() {},
            querySelectorAll: () => [], querySelector: () => null,
            getElementById: () => null, createElement: () => el(),
          },
          fetch: (url) => {
            if (String(url).includes("/api/config")) {
              return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ csrf: "tok" }), text: () => Promise.resolve("{}") });
            }
            if (String(url).includes("api/admin/products")) {
              if (shouldFail) {
                return Promise.resolve({
                  ok: false, status: 503,
                  text: () => Promise.resolve(JSON.stringify({ ok: false, error: "Temporary outage" })),
                  json: () => Promise.resolve({ ok: false, error: "Temporary outage" }),
                });
              }
              return Promise.resolve({
                ok: true, status: 200,
                text: () => Promise.resolve(JSON.stringify({ ok: true, product: { id: "jau-retry-1", name: "Fresh", priceNgn: 9000, stock: 5, online: true }, live: true })),
                json: () => Promise.resolve({ ok: true, product: { id: "jau-retry-1", name: "Fresh", priceNgn: 9000, stock: 5, online: true }, live: true }),
              });
            }
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}") });
          },
        };
        sandbox.window = sandbox;
        sandbox.globalThis = sandbox;
        sandbox.window.addEventListener = () => {};
        sandbox.window.dispatchEvent = () => {};
        sandbox.window.matchMedia = () => ({ matches: false, addEventListener() {} });
        vm.createContext(sandbox);
        vm.runInContext(netSrc, sandbox);
        vm.runInContext(storeSrc + "\nwindow.JA = JA;", sandbox);

        const r1 = await sandbox.JA.upsertProduct({ id: "jau-retry-1", name: "Stale", priceNgn: 8000, stock: 5, online: true });
        if (!r1.queued || sandbox.JA_NET.pending() !== 1) {
          console.error("expected 1 queued job after outage, got", r1, sandbox.JA_NET.pending());
          process.exit(1);
        }
        shouldFail = false;
        const r2 = await sandbox.JA.upsertProduct({ id: "jau-retry-1", name: "Fresh", priceNgn: 9000, stock: 5, online: true });
        if (!r2.ok || sandbox.JA_NET.pending() !== 0) {
          console.error("expected confirmed retry save and 0 pending jobs, got", r2, sandbox.JA_NET.pending());
          process.exit(1);
        }
        console.log("RETRY_OUTBOX_CLEAN_OK");
        process.exit(0);
    """)
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=str(ROOT), text=True, capture_output=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RETRY_OUTBOX_CLEAN_OK" in proc.stdout
