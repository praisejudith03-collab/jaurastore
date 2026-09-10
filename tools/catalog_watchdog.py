#!/usr/bin/env python3
"""Production catalog watchdog: the storefront may never lose a product.

Runs hourly from .github/workflows/catalog-watchdog.yml (and on demand via
workflow_dispatch). It takes two INDEPENDENT measurements and compares them:

  1. the public storefront answer - GET {WATCHDOG_BASE_URL}/api/catalog
  2. the source of truth - the Supabase products table, read directly over
     PostgREST (NOT through the app's own supabase_store client, so a bug in
     the app's read path can never hide itself)

and fails loudly when any of these invariants break:

  * every online (online is not false) non-tombstone Supabase row appears in
    the public catalogue exactly once - in either direction (missing rows are
    the "storefront shows fewer products than Supabase" defect; extra rows
    are rows that should have been filtered out);
  * the 255 approved customer rows (wix-001..wix-258 minus the three the
    owner deleted on purpose: wix-006, wix-007, wix-108) still exist in
    Supabase and are online=true - the "products disappear" guard. If you
    INTENTIONALLY unpublish or delete one of them, change EXPECTED_WIX_IDS
    (or disable the watchdog) in the same change - otherwise this alert is
    doing its job;
  * the served wix-* rows carry products-table columns (source / name_fr /
    legacyId) - a silent fallback to the bundled js/products-data.js snapshot
    is caught even though that snapshot also holds the same 258 ids.

It never prints secrets: the URL and the service key stay in the
environment, and failures are reported as counts and product ids only.

Exit codes: 0 = healthy, 1 = invariant broken, 2 = could not measure.
"""
import json
import os
import sys
import time
import urllib.request

DEFAULT_BASE = "https://jaurastore.com.ng"

# The approved customer catalogue: 255 wix-* rows - ids wix-001..wix-258
# (CUSTOMER_CATALOG_IMPORT_REVIEW.md) minus the three the owner deleted on
# purpose (2026-09-10), which must never trip this guard again.
# Zero-padded to three digits.
RETIRED_WIX_IDS = frozenset({"wix-006", "wix-007", "wix-108"})
EXPECTED_WIX_IDS = tuple(f"wix-{i:03d}" for i in range(1, 259)
                          if f"wix-{i:03d}" not in RETIRED_WIX_IDS)

# Rows whose `source` marks a tombstone (a soft delete or a superseded bulk
# import) are not live products - the app filters them too (supabase_store).
TOMBSTONE_SOURCES = ("deleted", "replaced")

# Test-suite products are never shop pieces. The app refuses to serve or save
# one (catalog.is_test_fixture / merged()) and tombstones what it finds on
# boot; this guard mirrors that rule so the watchdog does not report the
# app's own policy as a defect - and so a fixture that DOES reach the public
# catalogue is caught. Keep the two implementations in step.
FIXTURE_ID_PREFIXES = ("jau-stock", "jau-mirror")
FIXTURE_SKU_PREFIX = "JAUSTOCK"
FIXTURE_NAME_PREFIX = "stock test"


def is_test_fixture(row):
    """True for a product the test suite created for itself."""
    row = row or {}
    pid = str(row.get("id") or "").strip().lower()
    if any(pid.startswith(prefix) for prefix in FIXTURE_ID_PREFIXES):
        return True
    sku = str(row.get("sku") or "").strip().upper()
    if sku.startswith(FIXTURE_SKU_PREFIX):
        return True
    return str(row.get("name") or "").strip().lower().startswith(FIXTURE_NAME_PREFIX)

# PostgREST page size for the independent table read.
DB_PAGE = 500
DB_ROW_CEILING = 100_000        # a sane stop against a runaway pagination loop

# Columns that only the products table carries. The bundled local snapshot
# has none of them, so their presence proves the answer came from Supabase.
TABLE_COLUMN_CANARIES = ("source", "name_fr", "legacyId")


def _get(url, headers=None, timeout=120):
    """GET a URL, returning (status, {lowercased header: value}, body bytes)."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
        return resp.status, hdrs, resp.read()


def fetch_public_catalog(base, attempts=3):
    """GET /api/catalog with retries (a free dyno's cold start is ~50s)."""
    url = base.rstrip("/") + "/api/catalog"
    last = None
    for attempt in range(attempts):
        try:
            _status, _hdrs, body = _get(url)
            payload = json.loads(body.decode("utf-8"))
            if not payload.get("ok") or not isinstance(payload.get("products"), list):
                raise ValueError("the catalog payload is not ok / not product-shaped")
            return payload
        except Exception as exc:                    # noqa: BLE001 - reported below
            last = exc
            if attempt + 1 < attempts:
                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"could not read {url}: {last}")


def fetch_db_rows(supabase_url, service_key):
    """Every products-table row over PostgREST, paged by Range headers.

    Independent of the app's supabase_store client on purpose: if the app's
    read path ever loses rows, this measurement still sees the whole table.
    """
    url = (supabase_url.rstrip("/") +
           "/rest/v1/products?select=id,online,source,sku,name&order=id.asc")
    base_headers = {
        "apikey": service_key,
        "Authorization": "Bearer " + service_key,
        "Range-Unit": "items",
    }
    rows, start = [], 0
    while True:
        headers = dict(base_headers)
        headers["Range"] = f"{start}-{start + DB_PAGE - 1}"
        status, hdrs, body = _get(url, headers=headers)
        if status not in (200, 206):
            raise RuntimeError(f"PostgREST answered HTTP {status}")
        try:
            page = json.loads(body.decode("utf-8"))
        except ValueError:
            raise RuntimeError("PostgREST returned a body that is not JSON")
        if not isinstance(page, list):
            raise RuntimeError("PostgREST returned an unexpected payload shape")
        rows.extend(page)
        # Content-Range: "0-499/276" (or "*/276" for an empty page)
        total = None
        content_range = hdrs.get("content-range") or ""
        if "/" in content_range:
            try:
                total = int(content_range.rsplit("/", 1)[1])
            except ValueError:
                total = None
        if total is None:
            # No count header: a short page means the table ended.
            if len(page) < DB_PAGE:
                break
        elif len(rows) >= total:
            break
        if not page:
            break
        start += len(page)
        if len(rows) > DB_ROW_CEILING:
            raise RuntimeError("pagination did not converge - aborting")
    return rows


def check(db_rows, payload, expected_ids=EXPECTED_WIX_IDS):
    """Compare the two measurements. Returns (failures, summary_lines)."""
    failures, summary = [], []

    products = [p for p in (payload.get("products") or []) if p and p.get("id")]
    api_ids = [str(p["id"]) for p in products]

    # 1. no product may render twice
    seen, dupes = set(), set()
    for pid in api_ids:
        if pid in seen:
            dupes.add(pid)
        seen.add(pid)
    if dupes:
        failures.append("duplicate product ids in /api/catalog: " +
                        ", ".join(sorted(dupes)[:15]))

    # 2. the live table: what Supabase considers visible right now
    live = {}
    for row in db_rows or []:
        src = str((row or {}).get("source") or "").strip().lower()
        if src in TOMBSTONE_SOURCES:
            continue                             # a tombstone, not a product
        pid = str((row or {}).get("id") or "")
        if pid:
            live[pid] = row
    visible = {pid for pid, row in live.items() if row.get("online") is not False}

    # 2b. test-suite products: the app never serves one, so a live fixture row
    # must not be reported as "missing from the storefront" - and a fixture
    # that IS served is the defect the owner reported ("they are back").
    fixture_ids = {pid for pid, row in live.items() if is_test_fixture(row)}
    visible = {pid for pid in visible if pid not in fixture_ids}
    served_fixtures = sorted(pid for pid in seen if pid in fixture_ids)
    if served_fixtures:
        failures.append(
            f"{len(served_fixtures)} test-suite product(s) are LIVE on the "
            f"storefront: {', '.join(served_fixtures[:15])}"
            + (" …" if len(served_fixtures) > 15 else ""))

    missing = sorted(visible - seen)
    extra = sorted(pid for pid in (seen - visible) if pid not in fixture_ids)
    if missing:
        failures.append(
            f"{len(missing)} online Supabase product(s) are MISSING from the "
            f"public catalogue: {', '.join(missing[:15])}"
            + (" …" if len(missing) > 15 else ""))
    if extra:
        failures.append(
            f"{len(extra)} product(s) are served that Supabase does not list "
            f"online: {', '.join(extra[:15])}")

    # 3. the approved 255 customer rows never disappear and never go offline
    gone = [pid for pid in expected_ids if pid not in live]
    offline = [pid for pid in expected_ids if pid in live
               and live[pid].get("online") is not True]
    if gone:
        failures.append(
            f"{len(gone)} of the {len(expected_ids)} approved wix-* rows no "
            f"longer exist in the Supabase products table: "
            f"{', '.join(gone[:15])}" + (" …" if len(gone) > 15 else ""))
    if offline:
        failures.append(
            f"{len(offline)} of the {len(expected_ids)} approved wix-* rows "
            f"are not online=true in Supabase: {', '.join(offline[:15])}"
            + (" …" if len(offline) > 15 else ""))

    # 4. the answer must come from the products table, not the local snapshot
    wix_rows = [p for p in products if str(p["id"]).startswith("wix-")]
    table_shaped = sum(1 for p in wix_rows
                       if any(k in p for k in TABLE_COLUMN_CANARIES))
    if wix_rows and table_shaped < len(wix_rows):
        failures.append(
            f"only {table_shaped}/{len(wix_rows)} served wix-* rows carry "
            f"products-table columns - the storefront may be serving the "
            f"bundled js/products-data.js fallback instead of Supabase")

    # 5. light sanity on the response metadata
    meta = payload.get("meta") or {}
    try:
        if int(meta.get("count")) < len(products):
            failures.append(
                f"meta.count ({meta.get('count')}) is smaller than the served "
                f"product list ({len(products)})")
    except (TypeError, ValueError):
        failures.append("meta.count is missing or not a number")

    summary.append(
        f"public catalogue: {len(products)} products "
        f"({len(wix_rows)} wix-*) · Supabase: {len(db_rows or [])} rows, "
        f"{len(live)} live, {len(visible)} online"
        + (f", {len(fixture_ids)} test-suite row(s) still untombstoned"
           if fixture_ids else ""))
    return failures, summary


def main():
    base = os.environ.get("WATCHDOG_BASE_URL", DEFAULT_BASE)
    supabase_url = os.environ.get("SUPABASE_URL", "")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not supabase_url or not service_key:
        print("FAIL  SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set "
              "(GitHub Actions secrets: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)")
        return 2
    try:
        payload = fetch_public_catalog(base)
        db_rows = fetch_db_rows(supabase_url, service_key)
    except Exception as exc:                        # noqa: BLE001 - reported below
        print(f"FAIL  could not measure production: {exc}")
        return 2

    failures, summary = check(db_rows, payload)
    for line in summary:
        print("INFO  " + line)
    if failures:
        for line in failures:
            print("FAIL  " + line)
        return 1
    print("PASS  every online Supabase product is on the public storefront, "
          "including all approved wix-* rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
