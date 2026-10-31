"""Regression coverage for the admin -> Supabase -> public storefront sync pipeline."""
import os
import shutil
import subprocess
import sys
import threading

import pytest

import api as api_mod
import app as appmod
import catalog as catalog_mod
from db import execute, init_db
import product_merge
import supabase_store

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
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def _login(client):
    res = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert res.status_code == 200, res.data
    return res.get_json()["csrf"]


def test_inflight_read_cannot_repopulate_cache_after_write(monkeypatch):
    monkeypatch.setattr(supabase_store, "_read_cache_enabled", lambda: True)
    supabase_store.invalidate_read_cache()
    started = threading.Event()
    release = threading.Event()

    def old_read():
        started.set()
        assert release.wait(5)
        return [{"id": "old"}]

    thread = threading.Thread(
        target=lambda: supabase_store.cached_read("products_rows", old_read)
    )
    thread.start()
    try:
        assert started.wait(5)
        supabase_store.invalidate_read_cache("products_rows")
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert supabase_store.cached_read(
            "products_rows", lambda: [{"id": "new"}]
        ) == [{"id": "new"}]
    finally:
        release.set()
        thread.join(5)
        supabase_store.invalidate_read_cache()


def test_category_id_is_merged_with_concurrent_category_changes():
    base = {"id": "a", "category": "beauty", "category_id": "beauty", "name": "Item"}
    stored = dict(base, category="bags", category_id="bags")
    edited = dict(base, name="New name")
    merged, _ = product_merge.merge_product_edit(base, edited, stored, dirty=["name"])
    assert merged["category"] == merged["category_id"] == "bags"
    edited = dict(base, category="shoes", category_id="shoes")
    merged, _ = product_merge.merge_product_edit(
        base, edited, stored, dirty=["category", "category_id"]
    )
    assert merged["category"] == merged["category_id"] == "shoes"


def test_normalize_and_upsert_populate_active_status_and_category_fk_fallback(monkeypatch):
    normalized = catalog_mod.normalize({
        "id": "jau-yesterday-bag",
        "name": "Yesterday Tote Bag",
        "category": "skincare",
        "priceNgn": 18500,
        "stock": 5,
    })
    assert normalized["category"] == "beauty"
    assert normalized["category_id"] == "beauty"
    assert normalized["online"] is True
    assert normalized["stock"] == normalized["stock_quantity"] == 5

    saved_batches = []

    class _FkGuardedTable:
        def upsert(self, rows):
            batch = [dict(r) for r in rows]
            for r in batch:
                if r.get("category_id") == "unseeded-cat":
                    raise RuntimeError(
                        'insert or update on table "products" violates foreign key '
                        'constraint "products_category_id_fkey" (23503)'
                    )
            saved_batches.append(batch)
            return self

        def execute(self):
            return {"data": saved_batches[-1] if saved_batches else []}

    class _FkSupabase:
        def table(self, name):
            assert name == "products"
            return _FkGuardedTable()

    monkeypatch.setattr(supabase_store, "enabled", lambda: True)
    monkeypatch.setattr(supabase_store, "client", lambda: _FkSupabase())
    monkeypatch.setattr(supabase_store, "_enforce_public_catalog_images", lambda: False)

    row = catalog_mod.normalize({
        "id": "jau-today-item",
        "name": "Today Fragrance",
        "category": "unseeded-cat",
        "priceNgn": 24000,
        "stock": 3,
        "source": "draft",
    })
    assert supabase_store.upsert_products([row]) is True
    assert len(saved_batches) == 1
    persisted = saved_batches[0][0]
    assert persisted["category"] == "unseeded-cat"
    assert persisted["category_id"] is None
    assert persisted["is_active"] is True
    assert persisted["is_deleted"] is False
    assert persisted["source"] == "admin"


def test_canonicalize_and_fold_expose_category_id_only_rows_to_storefront(
    client, monkeypatch, tmp_path
):
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(tmp_path / "catalog.json"))
    raw_row = supabase_store._canonicalize_product({
        "id": "jau-catid-only",
        "name": "Category ID Only Piece",
        "category": "",
        "category_id": "bags",
        "priceNgn": 15000,
        "priceCfa": 6600,
        "stock_quantity": 4,
        "online": None,
        "is_active": True,
        "is_deleted": False,
        "source": "admin",
        "image_url": "images/products/_placeholder.jpg",
    })
    assert raw_row["category"] == "bags"
    assert raw_row["online"] is True

    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: [raw_row])
    monkeypatch.setattr(catalog_mod, "_durable_deleted_ids", lambda: set())
    monkeypatch.setattr(catalog_mod, "_supabase_dead_ids", lambda: set())
    api_mod._invalidate_all_catalog_caches()

    data = client.get("/api/catalog").get_json()
    found = next((p for p in data["products"] if p["id"] == "jau-catid-only"), None)
    assert found is not None
    assert found["category"] == "bags"
    assert found["stock_available"] == 4
    assert found["stock_status"] == "in"


def test_new_product_save_skips_unnecessary_pre_read_lookup(
    client, monkeypatch, tmp_path
):
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(tmp_path / "catalog.json"))
    api_mod._invalidate_all_catalog_caches()
    lookups = []

    def _spy(pid, strict=False):
        lookups.append(pid)
        return None

    monkeypatch.setattr(catalog_mod, "product_for_save", _spy)
    tok = _login(client)
    res = client.post(
        "/api/admin/products",
        headers={"X-CSRF-Token": tok},
        json={
            "product": {
                "id": "jau-fast-create",
                "name": "Fast Create Dress",
                "category": "ankara",
                "priceNgn": 30000,
                "stock": 7,
                "images": ["images/products/_placeholder.jpg"],
            }
        },
    )
    assert res.status_code == 200, res.data
    assert lookups == []


def test_outbox_preserves_ondone_and_syncs_reloaded_product_saves():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const netSrc = readFileSync("js/net.js", "utf8");
const ls = new Map();
let failNext = true;
const applied = [];
const onDoneCalls = [];

function bootEnv() {
  const sandbox = {
    console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
    AbortController, AbortSignal, Date, Math, JSON, Object, Array, String, Number, Boolean, Promise, Set,
    navigator: { onLine: true },
    sessionStorage: { getItem: () => "tok-1", setItem: () => {}, removeItem: () => {} },
    localStorage: {
      getItem: (k) => (ls.has(k) ? ls.get(k) : null),
      setItem: (k, v) => ls.set(k, String(v)),
      removeItem: (k) => ls.delete(k),
    },
    indexedDB: {
      open: () => { throw new Error("no idb in sim"); },
    },
    document: {
      hidden: false,
      readyState: "complete",
      body: { appendChild: () => {} },
      head: { appendChild: () => {} },
      querySelectorAll: () => [],
      getElementById: () => null,
      createElement: () => ({ style: {}, classList: { add: () => {}, remove: () => {}, contains: () => false }, addEventListener: () => {} }),
      addEventListener: () => {},
    },
    addEventListener: () => {},
    JA: {
      toast: () => {},
      applyServerProduct: (p) => applied.push(p.id),
    },
    fetch: async (url) => {
      if (String(url).includes("/api/config")) {
        return { ok: true, status: 200, json: async () => ({ csrf: "tok-1" }), text: async () => '{"csrf":"tok-1"}' };
      }
      if (failNext) {
        return { ok: false, status: 503, text: async () => '{"ok":false,"error":"temporary"}' };
      }
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ ok: true, product: { id: "jau-queued-1", name: "Queued Piece" } }),
      };
    },
  };
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(netSrc, sandbox, { filename: "js/net.js" });
  return sandbox;
}

// 1. In-memory queued job keeps onDone when flushed in the same tab.
const tab1 = bootEnv();
const queued = await tab1.JA_NET.api("api/admin/products", {
  method: "POST",
  json: { product: { id: "jau-queued-1", name: "Queued Piece" } },
  queue: true,
  label: "Product",
  onDone: (d) => onDoneCalls.push(d.product.id),
});
if (!queued || !queued.queued) throw new Error("expected queued response");
failNext = false;
await tab1.JA_NET.flush(true);
if (onDoneCalls.join(",") !== "jau-queued-1") {
  throw new Error("expected in-memory onDone callback, got: " + onDoneCalls.join(","));
}

// 2. Reloaded outbox job (no serialized onDone) still calls JA.applyServerProduct.
failNext = true;
await tab1.JA_NET.api("api/admin/products", {
  method: "POST",
  json: { product: { id: "jau-queued-1", name: "Queued Piece" } },
  queue: true,
  label: "Product",
});
const tab2 = bootEnv();
await tab2.JA_NET.boot();
failNext = false;
await tab2.JA_NET.flush(true);
if (!applied.includes("jau-queued-1")) {
  throw new Error("expected reloaded flush to call JA.applyServerProduct, got: " + applied.join(","));
}
"""
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
