"""Tests for the production catalog watchdog (tools/catalog_watchdog.py).

The watchdog is the "it must never happen again" layer: hourly, in CI, it
compares the live storefront against the Supabase products table read
directly over PostgREST. These tests pin its logic offline:

  * the healthy production shape (273 rows, 255 online wix-*) passes;
  * every defect class it exists to catch fails it: an online row missing
    from the public catalogue, a served row Supabase does not list, an
    approved wix row gone/offline in Supabase, a duplicated id, and a
    silent fallback to the bundled js/products-data.js snapshot;
  * the two fetch layers work end to end (local HTTP servers, including
    PostgREST Range pagination and the Content-Range count);
  * the workflow that schedules it stays safe (secrets only, bounded
    permissions, single-flight).

Run with:  python3 -m pytest tests/test_catalog_watchdog.py -q
"""
import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

_spec = importlib.util.spec_from_file_location(
    "catalog_watchdog", os.path.join(ROOT, "tools", "catalog_watchdog.py"))
wd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wd)

OFFLINE_NON_WIX = [
    ("jau-mirror-fail", "X"), ("jau-mirror-ok", "Y"), ("jau-mirror-post", "Mirror Post"),
    ("jau-stock-a", "Stock Test jau-stock-a"), ("jau-stock-b", "Stock Test jau-stock-b"),
    ("jau-stock-dcf", "Stock Test jau-stock-dcf"), ("jau-stock-dec", "Stock Test jau-stock-dec"),
    ("jau-stock-del", "Stock Test jau-stock-del"), ("jau-stock-dnp", "Stock Test jau-stock-dnp"),
    ("jau-stock-dpn", "Stock Test jau-stock-dpn"), ("jau-stock-em2", "Stock Test jau-stock-em2"),
    ("jau-stock-eml", "Stock Test jau-stock-eml"), ("jau-stock-idem", "Stock Test jau-stock-idem"),
    ("jau-stock-opr", "Stock Test jau-stock-opr"), ("jau-stock-opt", "Stock Test jau-stock-opt"),
    ("jau-stock-pay", "Stock Test jau-stock-pay"), ("jau-stock-rop", "Stock Test jau-stock-rop"),
    ("jau-mtot3318", "Tote bag"),
]


def _production_shape():
    """(db_rows, api_payload) exactly like production: 273 table rows
    (255 online wix-* + 18 offline non-wix), 255 served products.

    The owner deleted wix-006, wix-007 and wix-108 on purpose, so they are
    in neither measurement - the retired ids must stay silent (see
    test_watchdog_passes_on_the_healthy_production_shape)."""
    seed = json.load(open(os.path.join(ROOT, "data", "seed.json"), encoding="utf-8"))
    db_rows, api_products = [], []
    for p in seed:
        pid = str(p.get("id", ""))
        if pid.startswith("wix-") and pid not in wd.RETIRED_WIX_IDS:
            db_rows.append({"id": pid, "online": True, "source": "admin"})
            api_products.append({          # public shape, table columns included
                "id": pid, "sku": p.get("sku"), "slug": p.get("slug"),
                "name": p.get("name"), "online": True, "source": "admin",
                "name_fr": None, "legacyId": None,
                "priceNgn": p.get("priceNgn"), "priceCfa": p.get("priceCfa"),
            })
    for pid, _name in OFFLINE_NON_WIX:
        db_rows.append({"id": pid, "online": False, "source": "admin"})
    payload = {"ok": True, "products": api_products,
               "meta": {"count": 273, "updatedAt": "2026-09-10T12:00:00+00:00"}}
    return db_rows, payload


# ------------------------------------------------------------- check() logic
def test_watchdog_passes_on_the_healthy_production_shape():
    # The approved set is 255: wix-001..wix-258 minus the three rows the
    # owner deleted on purpose. Their absence from Supabase is expected and
    # must never raise the "products disappear" alarm (the scheduled run was
    # red until they were retired here).
    assert len(wd.EXPECTED_WIX_IDS) == 255, len(wd.EXPECTED_WIX_IDS)
    assert wd.RETIRED_WIX_IDS == frozenset({"wix-006", "wix-007", "wix-108"})
    assert not (set(wd.EXPECTED_WIX_IDS) & wd.RETIRED_WIX_IDS)
    db_rows, payload = _production_shape()
    assert len(payload["products"]) == 255
    assert len(db_rows) == 255 + len(OFFLINE_NON_WIX)
    failures, summary = wd.check(db_rows, payload)
    assert failures == [], "healthy production shape must pass: " + "; ".join(failures)
    assert any("273 rows" in s for s in summary)


def test_watchdog_fails_when_an_online_row_is_missing_from_the_api():
    """The original defect class: Supabase says 255, the storefront serves
    fewer. Even ONE missing online row must trip the watchdog."""
    db_rows, payload = _production_shape()
    payload["products"] = [p for p in payload["products"] if p["id"] != "wix-100"]
    failures, _summary = wd.check(db_rows, payload)
    assert any("wix-100" in f and "MISSING" in f for f in failures), failures


def test_watchdog_fails_when_the_api_serves_a_row_supabase_does_not_list():
    db_rows, payload = _production_shape()
    payload["products"].append({"id": "jau-ghost", "name": "Ghost",
                                "online": True, "source": "admin"})
    failures, _summary = wd.check(db_rows, payload)
    assert any("jau-ghost" in f and "does not list" in f for f in failures), failures


def test_watchdog_fails_when_an_approved_wix_row_disappears_from_supabase():
    # wix-009 is still approved (wix-006/007/108 were retired: their absence
    # is expected and covered by test_watchdog_passes_on_the_healthy_...).
    db_rows, payload = _production_shape()
    db_rows = [r for r in db_rows if r["id"] != "wix-009"]
    failures, _summary = wd.check(db_rows, payload)
    assert any("wix-009" in f and "no longer exist" in f for f in failures), failures
    # and the row is still being served by the API even though Supabase no
    # longer lists it (the set-equality side of the same alarm)
    assert any("wix-009" in f and "does not list" in f for f in failures), failures


def test_watchdog_fails_when_an_approved_wix_row_goes_offline():
    db_rows, payload = _production_shape()
    for r in db_rows:
        if r["id"] == "wix-042":
            r["online"] = False
    payload["products"] = [p for p in payload["products"] if p["id"] != "wix-042"]
    failures, _summary = wd.check(db_rows, payload)
    assert any("wix-042" in f and "not online=true" in f for f in failures), failures


def test_watchdog_fails_on_a_duplicated_product_id():
    db_rows, payload = _production_shape()
    payload["products"] = payload["products"] + [dict(payload["products"][0])]
    failures, _summary = wd.check(db_rows, payload)
    assert any("duplicate product ids" in f for f in failures), failures


def test_watchdog_detects_the_local_products_data_fallback():
    """The bundled snapshot carries the same wix ids but none of the
    products-table columns - the canary must flag it even at full count."""
    db_rows, payload = _production_shape()
    payload["products"] = [{k: v for k, v in p.items()
                            if k not in ("source", "name_fr", "legacyId")}
                           for p in payload["products"]]
    failures, _summary = wd.check(db_rows, payload)
    assert any("fallback" in f for f in failures), failures


def test_watchdog_ignores_tombstones_but_flags_a_visible_online_null():
    """source='deleted'/'replaced' rows are not products (the app filters
    them too), while online=null renders visible and must be compared."""
    db_rows, payload = _production_shape()
    db_rows.append({"id": "wix-259", "online": True, "source": "deleted"})
    failures, _summary = wd.check(db_rows, payload)   # tombstone: ignored
    assert failures == []
    db_rows.append({"id": "wix-260", "online": None, "source": "admin"})
    failures, _summary = wd.check(db_rows, payload)   # visible but unserved
    assert any("wix-260" in f and "MISSING" in f for f in failures), failures


# ---------------------------------------------------------- fetch layers (HTTP)
class _Handler(BaseHTTPRequestHandler):
    """Serves /api/catalog and a PostgREST-style /rest/v1/products with
    Range pagination + Content-Range counts."""

    def log_message(self, *_args):      # keep pytest output clean
        pass

    def do_GET(self):
        if self.path.startswith("/api/catalog"):
            body = json.dumps(self.server.api_payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/rest/v1/products"):
            rng = (self.headers.get("Range") or "0-499")
            start, end = (int(x) for x in rng.split("-"))
            rows = self.server.db_rows[start:end + 1]
            total = len(self.server.db_rows)
            body = json.dumps(rows).encode()
            self.send_response(206)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Range", f"{start}-{start + len(rows) - 1}/{total}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture()
def local_servers(monkeypatch):
    db_rows, payload = _production_shape()
    db_rows.append({"id": "zz-tomb", "online": True, "source": "replaced"})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.api_payload, srv.db_rows = payload, db_rows
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    # force multiple pages so the Range walk is really exercised
    monkeypatch.setattr(wd, "DB_PAGE", 100)
    yield base, db_rows, payload
    srv.shutdown()
    srv.server_close()


def test_fetch_layers_read_the_whole_table_and_catalog(local_servers):
    base, db_rows, payload = local_servers
    got_payload = wd.fetch_public_catalog(base, attempts=1)
    assert len(got_payload["products"]) == len(payload["products"])
    got_rows = wd.fetch_db_rows(base, "test-key")
    assert len(got_rows) == len(db_rows)           # every page, incl. tombstone
    assert got_rows == db_rows


def test_fetch_public_catalog_fails_cleanly_on_a_broken_site():
    """A site that does not answer /api/catalog must surface as one clean
    RuntimeError (which main() reports as 'could not measure')."""
    class _404Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _404Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    broken_base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with pytest.raises(RuntimeError):
            wd.fetch_public_catalog(broken_base, attempts=1)
    finally:
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------- workflow safety (pinned)
def test_watchdog_workflow_stays_safe():
    """Follows tests/test_image_migration_workflow.py: parse the YAML and pin
    the properties that keep the scheduled watchdog safe - secrets only via
    repository secrets, read-only repo access, bounded issue access, and a
    single-flight concurrency so two runs can never race."""
    yaml = pytest.importorskip("yaml")
    with open(os.path.join(ROOT, ".github", "workflows", "catalog-watchdog.yml"),
              encoding="utf-8") as fh:
        text = fh.read()
    data = yaml.safe_load(text)
    triggers = data.get("on", data.get(True))
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    perms = data["permissions"]
    assert perms == {"contents": "read", "issues": "write"}, perms
    assert data["concurrency"]["group"] == "catalog-watchdog"
    assert data["concurrency"]["cancel-in-progress"] is False
    job = data["jobs"]["watch"]
    assert job["timeout-minutes"], "the job must have a timeout"
    # the service key may only ever arrive from the repository secrets
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert "secrets.SUPABASE_URL" in code and "secrets.SUPABASE_SERVICE_ROLE_KEY" in code
    assert "SUPABASE_SERVICE_ROLE_KEY:" in code
    # no secret VALUE is ever inlined (the key reference is the only occurrence
    # on the right-hand side of an env assignment)
    for line in code.splitlines():
        if "SUPABASE_SERVICE_ROLE_KEY" in line and ":" in line:
            assert line.strip().endswith("${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}"), line
    # the alert step only uses the workflow's own token
    for m in ("GH_TOKEN: ${{ github.token }}",):
        assert m in code
