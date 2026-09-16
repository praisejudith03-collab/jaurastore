#!/usr/bin/env python3
"""Production catalog watchdog: the storefront may never lose a product.

Runs autonomously every 20 minutes from .github/workflows/catalog-watchdog.yml
(and can also run on demand for diagnostics). It takes two INDEPENDENT
measurements and compares them:

  1. the public storefront answer - GET {WATCHDOG_BASE_URL}/api/catalog
  2. the source of truth - the Supabase products table, read directly over
     PostgREST (NOT through the app's own supabase_store client, so a bug in
     the app's read path can never hide itself)

and fails loudly when any of these invariants break:

  * every online (online is not false) non-tombstone Supabase row appears in
    the public catalogue exactly once - in either direction (missing rows are
    the "storefront shows fewer products than Supabase" defect; extra rows
    are rows that should have been filtered out);
  * the live catalogue does not collapse between runs (see MAX_SHRINK_RATIO).
    Retiring products is normal shopkeeping and never alerts; losing a fifth
    of the shop at once is a bug and does;
  * the served wix-* rows carry products-table columns (source / name_fr /
    legacyId) - a silent fallback to the bundled js/products-data.js snapshot
    is caught even though that snapshot also holds the same ids.

READ-ONLY against production. An earlier version kept a hardcoded list of 254
approved wix-* ids and PATCHed any of them that were offline back to
online=true on every run. That fought the shop owner: taking a product off
sale was silently undone within 20 minutes, so the only way to retire one was
to delete the row - which then tripped the "approved row no longer exists"
alarm. Deciding what to sell belongs to the owner; this tool only reports
what it sees. The one exception is a cache-busting re-read of the storefront,
which writes nothing.

Background-worker health is reported but no longer aborts the run, so a sick
scheduler cannot hide a catalogue defect behind "could not measure".

It never prints secrets: the URL and the service key stay in the
environment, and failures are reported as counts and product ids only.

Exit codes: 0 = healthy, 1 = invariant broken, 2 = could not measure.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://jaurastore.com.ng"
REQUEST_TIMEOUT = 45
RETRY_DELAYS = (2, 5)

# How far the live catalogue may shrink between two runs before this is
# treated as data loss rather than curation. Retiring a handful of products
# is normal shopkeeping; losing a fifth of the shop in twenty minutes is a
# bug. The baseline is the previous run's count, carried in the state file.
MAX_SHRINK_RATIO = 0.20
MIN_LIVE_PRODUCTS = 50
STATE_PATH = os.environ.get("WATCHDOG_STATE", ".watchdog-state.json")

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


def _request(url, headers=None, timeout=REQUEST_TIMEOUT, method="GET", data=None):
    """Make an HTTP request and return (status, lower-case headers, body)."""
    req = urllib.request.Request(url, headers=headers or {}, method=method, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
        return resp.status, hdrs, resp.read()


def _get(url, headers=None, timeout=REQUEST_TIMEOUT):
    return _request(url, headers=headers, timeout=timeout)


def _get_with_retries(url, headers=None, attempts=3):
    """Bounded GET retries for transient storefront/PostgREST failures."""
    last = None
    for attempt in range(max(1, attempts)):
        try:
            return _get(url, headers=headers)
        except Exception as exc:                    # noqa: BLE001 - summarized
            last = exc
            if attempt < min(len(RETRY_DELAYS), attempts - 1):
                time.sleep(RETRY_DELAYS[attempt])
    raise RuntimeError(f"request failed after {max(1, attempts)} attempt(s): {last}")


def describe_worker_failure(background):
    """Explain WHY the workers are unhealthy, using the health payload.

    /healthz now carries the last recorded crash (job, exception type,
    message, payload id and memory) alongside the liveness flags, so the
    alert names the actual defect instead of only its symptom.
    """
    background = background or {}
    dead = [name for name, alive in (
        ("maintenance", background.get("maintenanceAlive")),
        ("reminders", background.get("remindersAlive"))) if not alive]
    bits = []
    if not background.get("started"):
        bits.append("scheduler never started")
    if dead:
        bits.append("stopped worker(s): " + ", ".join(dead))
    if background.get("lastErrorJob") or background.get("lastError"):
        bits.append("last error in %s: %s (%s)" % (
            background.get("lastErrorJob") or "unknown",
            str(background.get("lastError") or "")[:200],
            background.get("lastErrorAt") or "time unknown"))
    for report in (background.get("recentFailures") or [])[:3]:
        if not isinstance(report, dict):
            continue
        bits.append("  - %s %s: %s%s (rss %s MB, %s)" % (
            report.get("job") or "job",
            report.get("error") or "Error",
            str(report.get("message") or "")[:160],
            " payload=" + str(report.get("payloadId")) if report.get("payloadId") else "",
            report.get("rssMb"), report.get("at")))
    if int(background.get("failures") or 0):
        bits.append("%s failure(s) recorded since boot" % background["failures"])
    if int(background.get("restarts") or 0):
        bits.append("%s worker restart(s)" % background["restarts"])
    return "; ".join(bits) if bits else "no detail reported by /healthz"


def fetch_service_health(base):
    """Audit HTTP and in-process background workers before catalog checks."""
    url = base.rstrip("/") + "/healthz"
    _status, _headers, body = _get_with_retries(url)
    payload = json.loads(body.decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError("service health endpoint did not report ok")
    background = payload.get("background")
    if isinstance(background, dict) and not (
            background.get("started") and background.get("maintenanceAlive") and
            background.get("remindersAlive")):
        raise RuntimeError("background scheduler workers are not healthy: "
                           + describe_worker_failure(background))
    return payload


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
           "/rest/v1/products?select=id,online,source,sku,name,category,priceCfa,priceNgn,image,image_url,images&order=id.asc")
    base_headers = {
        "apikey": service_key,
        "Authorization": "Bearer " + service_key,
        "Range-Unit": "items",
    }
    rows, start = [], 0
    while True:
        headers = dict(base_headers)
        headers["Range"] = f"{start}-{start + DB_PAGE - 1}"
        status, hdrs, body = _get_with_retries(url, headers=headers)
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


def refresh_storefront_cache(base):
    """Force an uncached catalogue read so the storefront rebuilds immediately."""
    url = base.rstrip("/") + "/api/catalog?watchdog_refresh=" + str(int(time.time()))
    headers = {"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"}
    status, _headers, _body = _get_with_retries(url, headers=headers)
    if status != 200:
        raise RuntimeError(f"storefront cache refresh answered HTTP {status}")


def load_state(path=None):
    """Previous run's measurements. Missing/corrupt state is not an error."""
    try:
        with open(path or STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(live_count, path=None):
    try:
        with open(path or STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"liveCount": int(live_count),
                       "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                      fh)
    except OSError:                                # pragma: no cover
        pass


def check(db_rows, payload, previous=None):
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

    # 3. the shop must not lose a large slice of its catalogue at once.
    #
    # This replaces a hardcoded list of approved wix-* ids. That list made
    # every deliberate retirement look like data loss, and - worse - it fed
    # an auto-repair step that set the owner's offline products back online
    # every 20 minutes, so the only way to retire a product was to delete the
    # row. Curation is the shopkeeper's call; this guard only asks that the
    # catalogue never collapses without someone noticing.
    previous_count = None
    if isinstance(previous, dict):
        try:
            previous_count = int(previous.get("liveCount"))
        except (TypeError, ValueError):
            previous_count = None
    if previous_count and previous_count >= MIN_LIVE_PRODUCTS:
        floor = int(previous_count * (1 - MAX_SHRINK_RATIO))
        if len(visible) < floor:
            failures.append(
                f"the live catalogue fell from {previous_count} to "
                f"{len(visible)} products since the last run (more than "
                f"{int(MAX_SHRINK_RATIO * 100)}%) - if this was a deliberate "
                f"bulk retirement, the next run adopts the new figure")
    if db_rows and not visible:
        failures.append("the Supabase products table lists no online products")

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
    # Worker health is reported, but it no longer aborts the run. It used to
    # be checked first and raise, so a sick scheduler meant the catalogue was
    # never compared at all - the shop could quietly lose products while every
    # alert talked about threads. The two concerns are now independent.
    health_failure = None
    try:
        fetch_service_health(base)
    except Exception as exc:                        # noqa: BLE001 - reported below
        health_failure = str(exc)

    try:
        payload = fetch_public_catalog(base)
        db_rows = fetch_db_rows(supabase_url, service_key)
    except Exception as exc:                        # noqa: BLE001 - reported below
        print(f"FAIL  could not measure production: {exc}")
        if health_failure:
            print(f"FAIL  {health_failure}")
        return 2

    # A stale CDN copy is the one benign cause of "the storefront is missing a
    # product", so re-read it uncached before believing the mismatch. This is
    # the only remediation left here: the watchdog reads production and never
    # writes to it. (It used to PATCH offline products back online every 20
    # minutes, which overrode the owner's own catalogue decisions.)
    previous = load_state()
    initial_failures, _initial_summary = check(db_rows, payload, previous)
    if any("MISSING from the public catalogue" in f for f in initial_failures):
        try:
            refresh_storefront_cache(base)
            db_rows = fetch_db_rows(supabase_url, service_key)
            payload = fetch_public_catalog(base)
            print("REPAIR  storefront catalogue cache refreshed")
        except Exception as exc:                   # noqa: BLE001 - reported below
            print(f"FAIL  storefront cache refresh failed: {exc}")
            return 2

    failures, summary = check(db_rows, payload, previous)
    for line in summary:
        print("INFO  " + line)
    if health_failure:
        failures = failures + [health_failure]

    # Record this run's figure either way: the shrink guard compares against
    # the last observation, so a deliberate bulk retirement alerts once and
    # then becomes the new normal instead of alerting forever.
    live_now = sum(1 for row in (db_rows or [])
                   if str((row or {}).get("source") or "").strip().lower()
                   not in TOMBSTONE_SOURCES
                   and (row or {}).get("online") is not False)
    save_state(live_now)

    if failures:
        for line in failures:
            print("FAIL  " + line)
        return 1
    print("PASS  every online Supabase product is on the public storefront")
    return 0


if __name__ == "__main__":
    sys.exit(main())
