"""Tests for the production catalog watchdog (tools/catalog_watchdog.py).

The watchdog is the "it must never happen again" layer: every hour, it
compares the live storefront against the Supabase products table read
directly over PostgREST. These tests pin its logic offline:

  * the healthy production shape (272 rows, 254 online wix-*) passes;
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
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.parse
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

# Products the owner retired before these fixtures were written. The watchdog
# no longer has an opinion about specific ids; this only keeps the fixture
# shaped like the real catalogue.
_RETIRED = frozenset({"wix-002", "wix-006", "wix-007", "wix-108"})

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
    (254 online wix-* + 18 offline non-wix), 254 served products.

    Products the owner retired are absent from both measurements, which is
    normal shopkeeping and must stay silent."""
    seed = json.load(open(os.path.join(ROOT, "data", "seed.json"), encoding="utf-8"))
    db_rows, api_products = [], []
    for p in seed:
        pid = str(p.get("id", ""))
        if pid.startswith("wix-") and pid not in _RETIRED:
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
    db_rows, payload = _production_shape()
    assert len(payload["products"]) == 254
    assert len(db_rows) == 254 + len(OFFLINE_NON_WIX)
    failures, summary = wd.check(db_rows, payload)
    assert failures == [], "healthy production shape must pass: " + "; ".join(failures)
    assert any("272 rows" in s for s in summary)


def test_a_newly_missing_product_warns_first_then_fails_when_it_persists():
    """The original defect class: Supabase says 254, the storefront serves
    fewer. One run of missing only WARNs (a deploy in flight, a cache edge,
    replication lag - the owner asked for tolerance); the SAME product still
    missing on the next run is a defect and must page."""
    db_rows, payload = _production_shape()
    payload["products"] = [p for p in payload["products"] if p["id"] != "wix-100"]

    # first observation: a warning, not a page
    state = {}
    failures, summary = wd.check(db_rows, payload, state=state)
    assert failures == [], failures
    assert any("wix-100" in s and s.startswith("WARN") for s in summary), summary
    assert state["missingStreaks"] == {"wix-100": 1}

    # still missing on the next run: a page
    failures, _summary = wd.check(db_rows, payload, previous=state)
    assert any("wix-100" in f and "MISSING" in f for f in failures), failures

    # and back on the storefront: the streak resets
    payload["products"].append({"id": "wix-100", "name": "Back", "online": True,
                                "source": "admin"})
    state = {}
    failures, _summary = wd.check(db_rows, payload, previous={"missingStreaks": {"wix-100": 1}},
                                  state=state)
    assert failures == [], failures
    assert state["missingStreaks"] == {}


def test_intentionally_deleted_products_are_never_missing():
    """wix-229 ("Thank you sticker 2") was deleted from the storefront ON
    PURPOSE (owner confirmed): its Supabase row stays online while the app's
    durable deleted-ids list suppresses it. Reporting that as "missing" was
    the false alarm that kept #75 open - it is shopkeeping, not data loss."""
    db_rows, payload = _production_shape()
    payload["products"] = [p for p in payload["products"] if p["id"] != "wix-229"]
    state = {}
    failures, summary = wd.check(db_rows, payload,
                                 deleted_ids={"wix-229"}, state=state)
    assert failures == [], failures
    assert not any("wix-229" in s and "WARN" in s for s in summary), summary
    assert any("intentionally deleted" in s for s in summary), summary
    # and it stays silent even across runs (no streak grows)
    failures, _summary = wd.check(db_rows, payload, previous=state,
                                  deleted_ids={"wix-229"})
    assert failures == [], failures


def test_a_missing_deleted_ids_read_degrades_to_streak_tolerance():
    """If growth_settings cannot be read the watchdog must neither guess a
    full list (everything excused) nor fail the run - it just loses the
    intentional-deletion layer and keeps the two-run tolerance."""
    db_rows, payload = _production_shape()
    payload["products"] = [p for p in payload["products"] if p["id"] != "wix-229"]
    state = {}
    failures, summary = wd.check(db_rows, payload, deleted_ids=None, state=state)
    assert failures == [], failures                       # first run: WARN only
    assert any("wix-229" in s and s.startswith("WARN") for s in summary), summary
    failures, _summary = wd.check(db_rows, payload, previous=state,
                                  deleted_ids=None)
    assert any("wix-229" in f and "MISSING" in f for f in failures), failures


def test_watchdog_does_not_call_a_filtered_test_product_missing():
    """The app never serves a test-suite product, so an online fixture row in
    Supabase is the app's policy - not the "products disappear" defect. It is
    reported in the summary (it still needs its tombstone), not as a failure.
    """
    db_rows, payload = _production_shape()
    db_rows.append({"id": "jau-stock-live", "online": True, "source": "admin",
                    "sku": "JAUSTOCKLIVE", "name": "Stock Test jau-stock-live"})
    failures, summary = wd.check(db_rows, payload)
    assert failures == [], failures
    assert any("test-suite row" in s for s in summary), summary


def test_watchdog_fails_when_a_test_product_is_live_on_the_storefront():
    """"I deleted the stock test products and they are back" - a fixture that
    reaches the public catalogue must trip the alarm."""
    db_rows, payload = _production_shape()
    fixture = {"id": "jau-stock-em2", "online": True, "source": "admin",
               "sku": "JAUSTOCKEM2", "name": "Stock Test jau-stock-em2"}
    db_rows.append(fixture)
    payload["products"].append({"id": "jau-stock-em2", "name": fixture["name"],
                                "online": True, "source": "admin"})
    failures, _summary = wd.check(db_rows, payload)
    assert any("jau-stock-em2" in f and "test-suite" in f for f in failures), failures


def test_watchdog_fails_when_the_api_serves_a_row_supabase_does_not_list():
    db_rows, payload = _production_shape()
    payload["products"].append({"id": "jau-ghost", "name": "Ghost",
                                "online": True, "source": "admin"})
    failures, _summary = wd.check(db_rows, payload)
    assert any("jau-ghost" in f and "does not list" in f for f in failures), failures


def test_retiring_a_product_is_not_an_alert():
    """The owner's own curation must never page anyone.

    The previous version kept a hardcoded list of approved wix-* ids, so
    taking a product off sale - or deleting it - was reported as data loss.
    Worse, an auto-repair step put offline products back online every 20
    minutes, so deleting the row was the only way to make a retirement stick.
    Both directions are now silent.
    """
    db_rows, payload = _production_shape()
    # taken offline in Supabase and correspondingly absent from the storefront
    for row in db_rows:
        if row["id"] == "wix-042":
            row["online"] = False
    payload["products"] = [p for p in payload["products"] if p["id"] != "wix-042"]
    # and a second one deleted outright
    db_rows = [r for r in db_rows if r["id"] != "wix-009"]
    payload["products"] = [p for p in payload["products"] if p["id"] != "wix-009"]
    failures, _summary = wd.check(db_rows, payload)
    assert failures == [], failures


def test_watchdog_fails_when_the_catalogue_collapses_between_runs():
    """Curation is fine; losing a fifth of the shop at once is not."""
    db_rows, payload = _production_shape()
    keep = {p["id"] for p in payload["products"][:100]}
    db_rows = [r for r in db_rows if r["id"] in keep or not r["id"].startswith("wix-")]
    payload["products"] = [p for p in payload["products"] if p["id"] in keep]
    failures, _summary = wd.check(db_rows, payload, {"liveCount": 254})
    assert any("fell from 254" in f for f in failures), failures
    # ...and once that is the new normal, it stops alerting
    failures, _summary = wd.check(db_rows, payload, {"liveCount": 100})
    assert failures == [], failures


def test_a_small_retirement_does_not_trip_the_shrink_guard():
    db_rows, payload = _production_shape()
    dropped = {p["id"] for p in payload["products"][:12]}
    db_rows = [r for r in db_rows if r["id"] not in dropped]
    payload["products"] = [p for p in payload["products"] if p["id"] not in dropped]
    failures, _summary = wd.check(db_rows, payload, {"liveCount": 254})
    assert failures == [], failures


WRITE_VERBS = ('"PATCH"', '"POST"', '"PUT"', '"DELETE"')


def _functions_that_write(source):
    """Top-level function bodies that issue a non-read request."""
    return [block for block in re.split(r"\ndef ", source)
            if any(verb in block for verb in WRITE_VERBS)]


def test_the_watchdog_never_writes_to_production():
    """A monitor that repairs its own subject cannot be trusted to report on
    it - and this one was overruling the owner.

    The watchdog used to PATCH sold-out products back online, overriding the
    owner's own catalogue decisions. So: it may read anything, and it may
    persist its OWN measurements, but it must never touch the catalogue -
    no repairing, no un-hiding, no re-pricing, no write to /rest/v1/products.

    The one write it is allowed is the stock-state upsert, which targets
    growth_settings (the same key/value map the app reads) so the broadcast
    out-of-stock guard has yesterday's reading to work from.
    """
    source = open(os.path.join(ROOT, "tools", "catalog_watchdog.py"),
                  encoding="utf-8").read()
    assert "activate_complete_products" not in source

    writers = _functions_that_write(source)
    for block in writers:
        name = block.split("(", 1)[0].strip()
        assert "/rest/v1/products" not in block, (
            f"{name}() writes to the products table - the watchdog measures "
            f"the catalogue, it never repairs it")
        assert "growth_settings" in block, (
            f"{name}() issues a write that is not aimed at growth_settings; "
            f"the watchdog may only persist its own state")
        assert "STOCK_STATE_KEY" in block, (
            f"{name}() writes a growth_settings key that is not the watchdog's "
            f"own stock state")

    # The permission is not open-ended: exactly one writer exists today, and
    # it is the documented stock-state upsert. Adding a second one has to be a
    # deliberate act that re-reads this test.
    assert len(writers) == 1, (
        f"expected exactly one writing function, found "
        f"{[b.split('(', 1)[0].strip() for b in writers]}")


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
    # visible but unserved; carried over one run so the tolerance is satisfied
    failures, _summary = wd.check(db_rows, payload,
                                  previous={"missingStreaks": {"wix-260": 1}})
    assert any("wix-260" in f and "MISSING" in f for f in failures), failures


def test_storefront_refresh_is_an_uncached_automatic_request(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=120):
        seen.update(url=url, headers=headers)
        return 200, {}, b"{}"

    monkeypatch.setattr(wd, "_get", fake_get)
    wd.refresh_storefront_cache("https://shop.test")
    assert "/api/catalog?watchdog_refresh=" in seen["url"]
    assert "no-store" in seen["headers"]["Cache-Control"]


# ---------------------------------------------------------- fetch layers (HTTP)
class _Handler(BaseHTTPRequestHandler):
    """Serves /api/catalog and a PostgREST-style /rest/v1/products with
    Range pagination + Content-Range counts."""

    def log_message(self, *_args):      # keep pytest output clean
        pass

    def do_GET(self):
        if self.path.startswith("/api/catalog"):
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            payload = dict(self.server.api_payload)
            if query.get("watchdog_page") == ["1"]:
                limit = int(query.get("limit", ["50"])[0])
                offset = int(query.get("offset", ["0"])[0])
                all_products = payload["products"]
                page = all_products[offset:offset + limit]
                next_offset = offset + len(page)
                payload["products"] = page
                payload["pagination"] = {
                    "limit": limit, "offset": offset, "total": len(all_products),
                    "nextOffset": next_offset if next_offset < len(all_products) else None,
                    "hasMore": next_offset < len(all_products),
                }
                self.server.catalog_offsets.append(offset)
                self.server.catalog_page_sizes.append(len(page))
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/rest/v1/growth_settings"):
            body = json.dumps([{"value": json.dumps(
                list(self.server.deleted_ids))}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/rest/v1/products"):
            rng = (self.headers.get("Range") or "0-49")
            start, end = (int(x) for x in rng.split("-"))
            self.server.product_ranges.append((start, end))
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


class _LocalServerBundle:
    """Keep existing three-value fixture unpacking and expose request logs."""
    def __init__(self, base, db_rows, payload, server):
        self.base, self.db_rows, self.payload, self.server = base, db_rows, payload, server

    def __iter__(self):
        yield self.base
        yield self.db_rows
        yield self.payload


@pytest.fixture()
def local_servers(monkeypatch):
    db_rows, payload = _production_shape()
    db_rows.append({"id": "zz-tomb", "online": True, "source": "replaced"})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.api_payload, srv.db_rows = payload, db_rows
    srv.deleted_ids = {"wix-229"}
    srv.catalog_offsets, srv.catalog_page_sizes, srv.product_ranges = [], [], []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    # Force the implementation's 50-row pages to be exercised.
    monkeypatch.setattr(wd, "DB_PAGE", 50)
    monkeypatch.setattr(wd, "CATALOG_PAGE", 50)
    yield _LocalServerBundle(base, db_rows, payload, srv)
    srv.shutdown()
    srv.server_close()


def test_fetch_layers_read_the_whole_table_and_catalog(local_servers):
    base, db_rows, payload = local_servers
    assert wd.DB_PAGE == wd.CATALOG_PAGE == 50
    got_payload = wd.fetch_public_catalog(base, attempts=1)
    assert len(got_payload["products"]) == len(payload["products"])
    assert "pagination" not in got_payload
    assert local_servers.server.catalog_offsets == [0, 50, 100, 150, 200, 250]
    assert max(local_servers.server.catalog_page_sizes) <= 50

    got_rows = wd.fetch_db_rows(base, "test-key")
    assert len(got_rows) == len(db_rows)           # every page, incl. tombstone
    assert got_rows == db_rows
    assert [start for start, _end in local_servers.server.product_ranges] == [
        0, 50, 100, 150, 200, 250]
    assert all(end - start + 1 == 50
               for start, end in local_servers.server.product_ranges)


def test_public_catalog_supports_old_unpaginated_render_during_rollout(monkeypatch):
    """A watchdog deploy should keep running while Render is rolling its API
    change out; an older response without pagination is still accepted."""
    _db_rows, payload = _production_shape()
    seen = {}

    def fake_get(url, headers=None, timeout=120):
        seen["url"] = url
        return 200, {}, json.dumps(payload).encode()

    monkeypatch.setattr(wd, "_get", fake_get)
    result = wd.fetch_public_catalog("https://shop.test", attempts=1)
    assert result == payload
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(seen["url"]).query)
    assert query == {"watchdog_page": ["1"], "limit": ["50"], "offset": ["0"]}


def test_transient_500s_recover_to_a_clean_watchdog_pass(monkeypatch, tmp_path):
    """A temporary Render and PostgREST 500 is retried per page, so main()
    returns a clean pass (and Actions will not run the issue-opening step).
    """
    db_rows, payload = _production_shape()
    calls = {"catalog": 0, "products": 0}
    catalog_offsets, product_ranges, delays = [], [], []

    def transient_500(url):
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, None)

    def fake_get(url, headers=None, timeout=120):
        parsed = urllib.parse.urlsplit(url)
        if parsed.path == "/api/catalog":
            query = urllib.parse.parse_qs(parsed.query)
            assert query.get("watchdog_page") == ["1"]
            limit = int(query["limit"][0])
            offset = int(query["offset"][0])
            catalog_offsets.append(offset)
            if offset == 0 and calls["catalog"] == 0:
                calls["catalog"] += 1
                transient_500(url)
            page = payload["products"][offset:offset + limit]
            next_offset = offset + len(page)
            response = dict(payload)
            response["products"] = page
            response["pagination"] = {
                "limit": limit, "offset": offset,
                "total": len(payload["products"]),
                "nextOffset": next_offset if next_offset < len(payload["products"]) else None,
                "hasMore": next_offset < len(payload["products"]),
            }
            return 200, {}, json.dumps(response).encode()

        if parsed.path == "/rest/v1/products":
            start, end = (int(x) for x in headers["Range"].split("-"))
            product_ranges.append((start, end))
            if start == 0 and calls["products"] == 0:
                calls["products"] += 1
                transient_500(url)
            page = db_rows[start:end + 1]
            last = start + len(page) - 1
            return (206, {"content-range": f"{start}-{last}/{len(db_rows)}"},
                    json.dumps(page).encode())

        if parsed.path == "/rest/v1/growth_settings":
            body = json.dumps([{"value": json.dumps(["wix-229"])}]).encode()
            return 200, {}, body
        raise AssertionError(f"unexpected watchdog request: {parsed.path}")

    monkeypatch.setattr(wd, "_get", fake_get)
    monkeypatch.setattr(wd.time, "sleep", delays.append)
    monkeypatch.setattr(wd, "fetch_service_health", lambda *_a, **_k: None)
    monkeypatch.setattr(wd, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("WATCHDOG_BASE_URL", "https://shop.test")
    monkeypatch.setenv("SUPABASE_URL", "https://db.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = wd.main()
    text = out.getvalue()

    assert rc == 0, text
    assert "PASS  every online Supabase product is on the public storefront" in text
    assert "FAIL" not in text
    assert text.count("transient HTTP 500") == 2
    assert delays == [2, 2]                     # first exponential retry delay
    assert catalog_offsets[:2] == [0, 0]         # failed page retried, not skipped
    assert catalog_offsets[2:] == [50, 100, 150, 200, 250]
    assert product_ranges[:2] == [(0, 49), (0, 49)]
    assert [start for start, _end in product_ranges[2:]] == [50, 100, 150, 200, 250]
    assert json.loads((tmp_path / "state.json").read_text())["liveCount"] == sum(
        row.get("online") is not False for row in db_rows)


def test_get_with_retries_uses_exponential_backoff_for_server_errors(monkeypatch):
    assert wd.DB_PAGE == wd.CATALOG_PAGE == 50
    assert wd.REQUEST_TIMEOUT >= 60
    assert wd.RETRY_DELAYS == (2, 4, 8, 16)
    calls, delays = [], []

    def fake_get(url, headers=None, timeout=120):
        calls.append((url, timeout))
        if len(calls) <= 3:
            raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, None)
        return 200, {}, b"ok"

    monkeypatch.setattr(wd, "_get", fake_get)
    monkeypatch.setattr(wd.time, "sleep", delays.append)
    assert wd._get_with_retries("https://db.test/rest/v1/products") == (200, {}, b"ok")
    assert [delay for delay in delays] == [2, 4, 8]
    assert len(calls) == 4
    assert all(timeout == wd.REQUEST_TIMEOUT for _url, timeout in calls)


def test_fetch_deleted_ids_reads_the_durable_list(local_servers):
    """The intentional-deletion layer reads the same growth_settings key the
    app writes, over PostgREST, with the service key - read-only."""
    base, _db_rows, _payload = local_servers
    ids = wd.fetch_deleted_ids(base, "test-key")
    assert ids == {"wix-229"}


def test_fetch_deleted_ids_returns_none_on_a_broken_read(monkeypatch):
    class _500Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(500)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _500Handler)
    monkeypatch.setattr(wd.time, "sleep", lambda _delay: None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    broken_base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert wd.fetch_deleted_ids(broken_base, "test-key") is None
    finally:
        srv.shutdown()
        srv.server_close()


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
    assert triggers["schedule"] == [{"cron": "0 * * * *"}]
    perms = data["permissions"]
    assert perms == {"contents": "read", "issues": "write"}, perms
    assert data["concurrency"]["group"] == "catalog-watchdog"
    assert data["concurrency"]["cancel-in-progress"] is False
    job = data["jobs"]["watch"]
    assert job["timeout-minutes"] == 25
    steps = job["steps"]
    watchdog_step = next(step for step in steps if step.get("id") == "watchdog")
    assert "timeout" in watchdog_step["run"] and "20m" in watchdog_step["run"]
    failure_step = next(step for step in steps if step.get("name") == "Open or update the alert issue")
    assert "steps.watchdog.outcome == 'failure'" in failure_step["if"]
    recovery_step = next(step for step in steps if step.get("name") == "Close the alert issue on recovery")
    assert "steps.watchdog.outcome == 'success'" in recovery_step["if"]
    assert "gh issue close" in recovery_step["run"]
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


def test_unhealthy_workers_no_longer_blind_the_catalogue_check(monkeypatch, tmp_path):
    """A sick scheduler must not stop the catalogue from being measured.

    fetch_service_health() used to run first and raise, so every run ended at
    "could not measure production: background scheduler workers are not
    healthy" - for four days the shop could have been losing products and no
    alert would have said so. Health is now reported alongside the catalogue
    result, not instead of it.
    """
    db_rows, payload = _production_shape()
    db_rows = [r for r in db_rows if r["id"] != "wix-100"]      # a real defect

    def unhealthy(_base):
        raise RuntimeError("background scheduler workers are not healthy: "
                           "stopped worker(s): reminders")

    monkeypatch.setattr(wd, "fetch_service_health", unhealthy)
    monkeypatch.setattr(wd, "fetch_public_catalog", lambda *a, **k: payload)
    monkeypatch.setattr(wd, "fetch_db_rows", lambda *a, **k: db_rows)
    monkeypatch.setattr(wd, "refresh_storefront_cache", lambda *a, **k: None)
    monkeypatch.setattr(wd, "fetch_deleted_ids", lambda *a, **k: set())
    monkeypatch.setattr(wd, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("SUPABASE_URL", "https://db.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")

    # This defect is the EXTRA direction (the storefront serves a row the
    # table no longer lists): it pages immediately - the two-run tolerance
    # only softens the missing direction - and the sick workers are reported
    # alongside it instead of hiding the catalogue defect.
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = wd.main()
    text = out.getvalue()
    assert rc == 1, text                      # an invariant broke, not "could not measure"
    assert "could not measure" not in text
    assert "workers are not healthy" in text  # still reported
    assert "wix-100" in text                  # and the catalogue defect is visible


def test_a_missing_product_pages_on_the_second_run_not_the_first(monkeypatch, tmp_path):
    """End to end through main(): the missing direction is softened by the
    two-run tolerance (the extra direction is not - see the workers test),
    and the streak memory survives in the state file between runs."""
    db_rows, payload = _production_shape()
    payload["products"] = [p for p in payload["products"] if p["id"] != "wix-100"]
    state = tmp_path / "state.json"
    monkeypatch.setattr(wd, "fetch_service_health", lambda *a, **k: None)
    monkeypatch.setattr(wd, "fetch_public_catalog", lambda *a, **k: payload)
    monkeypatch.setattr(wd, "fetch_db_rows", lambda *a, **k: db_rows)
    monkeypatch.setattr(wd, "refresh_storefront_cache", lambda *a, **k: None)
    monkeypatch.setattr(wd, "fetch_deleted_ids", lambda *a, **k: set())
    monkeypatch.setattr(wd, "STATE_PATH", str(state))
    monkeypatch.setenv("SUPABASE_URL", "https://db.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = wd.main()
    assert rc == 0, out.getvalue()              # WARN only, no page
    assert any("wix-100" in line and line.startswith("WARN")
               for line in out.getvalue().splitlines())
    assert json.loads(state.read_text())["missingStreaks"] == {"wix-100": 1}

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = wd.main()
    assert rc == 1, out.getvalue()              # still missing: page
    assert any("wix-100" in line and "consecutive" in line
               for line in out.getvalue().splitlines())


def test_the_run_records_its_size_so_the_next_run_has_a_baseline(monkeypatch, tmp_path):
    db_rows, payload = _production_shape()
    state = tmp_path / "state.json"
    monkeypatch.setattr(wd, "fetch_service_health", lambda *a, **k: None)
    monkeypatch.setattr(wd, "fetch_public_catalog", lambda *a, **k: payload)
    monkeypatch.setattr(wd, "fetch_db_rows", lambda *a, **k: db_rows)
    monkeypatch.setattr(wd, "fetch_deleted_ids", lambda *a, **k: set())
    monkeypatch.setattr(wd, "STATE_PATH", str(state))
    monkeypatch.setenv("SUPABASE_URL", "https://db.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")
    with contextlib.redirect_stdout(io.StringIO()):
        rc = wd.main()
    assert rc == 0
    saved = json.loads(state.read_text())
    assert saved["liveCount"] == len(payload["products"])
    # the tolerance memory rides along in the same state file
    assert saved["missingStreaks"] == {}
