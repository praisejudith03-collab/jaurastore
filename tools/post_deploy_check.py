#!/usr/bin/env python3
"""Read-only post-deploy verification for a running JauraStore instance.

The owner's directive for 2026-10-20 ended with "verify and deploy", and the
only honest verification of a deploy is against the deployed service. This
tool does exactly that, with no credentials and no writes:

  healthz   ``GET /healthz`` answers 200 with ``ok``, and the server's own
            ``latencyMs`` stays inside the owner's 50 ms budget for at least
            80% of samples (single-run noise on a free instance is real, so the
            check is a share, not "every sample"). Wall-clock time is reported
            too, capped at 2 s so a stalled TCP path still fails.

  catalog   ``GET /api/catalog`` publishes every row it counts:
            ``meta.publishedCount == len(products)``, ``meta.count >=
            publishedCount``, ``hiddenCount == count - publishedCount``, and no
            duplicated id. This is the invariant behind "I saved a product and
            the shop does not show it" - if it holds, the grid received
            everything and any missing card is a client-side state
            (category/price/photo), not a lost row.

  site      ``GET /api/site`` carries a live exchange rate (``cfaRate > 0``).
            A zero rate is what turned 259 products into one card for French
            visitors before the 2026-10-20 fix.

  shop      ``GET /shop.html`` with ``Accept-Language: fr`` is stamped
            ``lang="fr"`` (first paint in French, before any JavaScript), every
            ``?v=`` asset token is the same one (no half-bumped build), and the
            grid container is present.

  audit     ``GET /api/admin/catalog/audit`` is NOT public: it must answer 401
            or 403. A 200 here is an information leak, and a 404 means the
            deployed build predates the release.

Usage::

    python3 tools/post_deploy_check.py
    python3 tools/post_deploy_check.py --base https://jaurastore.com.ng --samples 9
    python3 tools/post_deploy_check.py --checks healthz,catalog --json

Exit codes: 0 every selected check passed, 1 at least one check failed,
2 the service was unreachable at every selected endpoint (DNS, TLS or
connection failures - nothing answered, so there is nothing to verify).
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.request
from typing import NamedTuple
from urllib.parse import urljoin

DEFAULT_BASE = "https://jaurastore.com.ng"
DEFAULT_SAMPLES = 5
DEFAULT_TIMEOUT = 15.0

#: Owner directive: the liveness probe answers in under 50 ms.
HEALTHZ_BUDGET_MS = 50.0
#: Share of samples that must meet the budget (a single cold sample may not).
HEALTHZ_BUDGET_SHARE = 0.8
#: Wall-clock ceiling for one probe, so a black-holed connection still fails.
HEALTHZ_WALL_CEILING_MS = 2000.0
#: How much of the body to read for the shop page (the file is ~40 KB).
SHOP_BODY_LIMIT = 2 * 1024 * 1024

CHECKS = ("healthz", "catalog", "site", "shop", "audit")


class RequestFailed(RuntimeError):
    """The HTTP round trip itself failed (DNS, TLS, connection, timeout)."""


class Response(NamedTuple):
    """One HTTP answer. ``url`` is the address that finally answered."""
    status: int
    headers: dict
    body: bytes
    elapsed_ms: float
    url: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface 3xx answers instead of chasing them.

    The admin checks must see the redirect itself: a 302 to the login page is
    a gated endpoint, while chasing it and reading the 200 of that page would
    look exactly like a leak.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def _get(base, path, *, headers=None, timeout=DEFAULT_TIMEOUT, limit=None, follow=True):
    """GET one URL and return a :class:`Response`.

    Redirects are followed by default (the site may answer http -> https); an
    HTTP error status is returned rather than raised, because several checks
    *expect* a 401/403 and must be able to see it.
    """
    url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    request = urllib.request.Request(url, headers=dict(headers or {}))
    open_url = urllib.request.urlopen if follow else _NO_REDIRECT_OPENER.open
    started = time.perf_counter()
    try:
        with open_url(request, timeout=timeout) as response:
            body = response.read(limit) if limit else response.read()
            return Response(response.status, dict(response.headers), body,
                            (time.perf_counter() - started) * 1000, response.geturl())
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(limit) if limit else exc.read()
        except Exception:
            pass
        return Response(exc.code, dict(exc.headers or {}), body,
                        (time.perf_counter() - started) * 1000, url)
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as exc:
        raise RequestFailed(str(getattr(exc, "reason", exc))[:200]) from exc


def _json(body):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _result(name, ok, detail):
    return {"check": name, "ok": bool(ok), "detail": str(detail)}


# --------------------------------------------------------------------- checks
def check_healthz(base, samples=DEFAULT_SAMPLES, timeout=DEFAULT_TIMEOUT):
    server_ms, wall_ms = [], []
    last = None
    for _ in range(max(1, int(samples))):
        answer = _get(base, "/healthz", timeout=timeout)
        status, body = answer.status, answer.body
        last = _json(body)
        wall_ms.append(answer.elapsed_ms)
        if not isinstance(last, dict):
            return _result("healthz", False,
                            f"HTTP {status}, body is not a health document")
        if status != 200 or not last.get("ok"):
            try:
                reason = (last.get("database") or {}).get("error") or "unhealthy"
            except AttributeError:
                reason = "unhealthy"
            return _result("healthz", False, f"HTTP {status} ok=false ({reason})")
        try:
            server_ms.append(float(last.get("latencyMs")))
        except (TypeError, ValueError):
            return _result("healthz", False, "the health document has no latencyMs")
    inside = [ms for ms in server_ms if ms < HEALTHZ_BUDGET_MS]
    share = len(inside) / len(server_ms)
    wall_p95 = sorted(wall_ms)[max(0, int(len(wall_ms) * 0.95) - 1)]
    detail = (f"server p50 {statistics.median(server_ms):.2f} ms, "
              f"max {max(server_ms):.2f} ms, {share:.0%} under {HEALTHZ_BUDGET_MS:.0f} ms; "
              f"wall p95 {wall_p95:.0f} ms")
    ok = share >= HEALTHZ_BUDGET_SHARE and wall_p95 < HEALTHZ_WALL_CEILING_MS
    return _result("healthz", ok, detail)


def check_catalog(base, timeout=DEFAULT_TIMEOUT):
    answer = _get(base, "/api/catalog", timeout=timeout)
    status, body, elapsed = answer.status, answer.body, answer.elapsed_ms
    payload = _json(body)
    if status != 200 or not isinstance(payload, dict) or not payload.get("ok"):
        return _result("catalog", False, f"HTTP {status}, the catalogue is not ok")
    products = payload.get("products")
    meta = payload.get("meta") or {}
    if not isinstance(products, list):
        return _result("catalog", False, "the payload carries no products list")
    if "publishedCount" not in meta:
        return _result("catalog", False,
                       "meta.publishedCount is missing - the deployed build predates the visibility contract")
    published, total, hidden = meta.get("publishedCount"), meta.get("count"), meta.get("hiddenCount")
    if published != len(products):
        dropped = (published - len(products)) if isinstance(published, int) else None
        tail = (f" - {dropped} row(s) were counted and not served "
                f"(the \"saved product is missing\" signature)") if dropped and dropped > 0 else ""
        return _result("catalog", False,
                       f"publishedCount={published} but {len(products)} products were served{tail}")
    if not isinstance(total, int) or total < published:
        return _result("catalog", False, f"count={total} is smaller than publishedCount={published}")
    if hidden != total - published:
        return _result("catalog", False,
                       f"hiddenCount={hidden} but count-publishedCount={total - published}")
    ids = [str(p.get("id") or "") for p in products if isinstance(p, dict)]
    if len(ids) != len(products):
        return _result("catalog", False, "a product row is not an object")
    if any(not pid for pid in ids):
        return _result("catalog", False, "a product row has no id")
    if len(set(ids)) != len(ids):
        duplicates = sorted({pid for pid in ids if ids.count(pid) > 1})[:5]
        return _result("catalog", False, f"duplicated product id(s): {', '.join(duplicates)}")
    return _result("catalog", True,
                   f"{published} published of {total} live ({hidden} hidden), no duplicates, "
                   f"{len(body) / 1024:.0f} KB in {elapsed:.0f} ms")


def check_site(base, timeout=DEFAULT_TIMEOUT):
    answer = _get(base, "/api/site", timeout=timeout)
    status, payload = answer.status, _json(answer.body)
    if status != 200 or not isinstance(payload, dict) or not payload.get("ok"):
        # The endpoint names the reason it could not read the settings; repeat
        # it, because "HTTP 503" alone sends the reader to the wrong log.
        try:
            reason = payload.get("error") or "the site row is not ok"
        except AttributeError:
            reason = "the site row is not ok"
        return _result("site", False, f"HTTP {status}, {reason}")
    site = payload.get("site")
    if not isinstance(site, dict):
        return _result("site", False, "the payload carries no site row")
    try:
        rate = float(site.get("cfaRate") or 0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate <= 0:
        return _result("site", False,
                       "cfaRate is 0 - every Naira price would read 0 in CFA on a French device")
    detail = f"cfaRate {rate:g}"
    if "store_active" in site:
        detail += f", store_active={site['store_active']}"
    return _result("site", True, detail)


def check_shop(base, timeout=DEFAULT_TIMEOUT):
    answer = _get(base, "/shop.html", headers={"Accept-Language": "fr-FR,fr;q=0.9"},
                  timeout=timeout, limit=SHOP_BODY_LIMIT)
    if answer.status != 200:
        return _result("shop", False, f"HTTP {answer.status}")
    if not answer.url.rstrip("/").endswith("/shop.html"):
        # The shop usually redirects to / when the storefront is paused; saying
        # so beats "the grid container is missing" for a page nobody served.
        return _result("shop", False, f"/shop.html redirected to {answer.url}")
    html = answer.body.decode("utf-8", "replace")
    lang = re.search(r"<html\b[^>]*\blang=\"([^\"]+)\"", html, re.I)
    if not lang or lang.group(1).lower() != "fr":
        seen = lang.group(1) if lang else "missing"
        return _result("shop", False,
                       f'lang={seen} for a French device (the French first paint regressed)')
    tokens = sorted(set(re.findall(r"\?v=(\d+)", html)))
    if len(tokens) != 1:
        return _result("shop", False, f"mixed asset tokens: {', '.join(tokens) or 'none'}")
    if "data-shop-grid" not in html:
        return _result("shop", False, "the grid container is missing")
    return _result("shop", True,
                   f'lang="fr" before JS, asset token v{tokens[0]}, grid present')


def check_audit(base, timeout=DEFAULT_TIMEOUT):
    answer = _get(base, "/api/admin/catalog/audit", timeout=timeout, follow=False)
    status = answer.status
    if status in (401, 403):
        return _result("audit", True, f"admin-only (HTTP {status})")
    if 300 <= status < 400:
        target = answer.headers.get("Location") or "a login page"
        return _result("audit", True, f"admin-only (HTTP {status} to {target})")
    if status == 404:
        return _result("audit", False, "the audit route is missing - the deployed build predates the release")
    if status == 200:
        return _result("audit", False,
                       "the catalogue audit answers 200 without a session - it leaks the catalogue")
    return _result("audit", False, f"unexpected HTTP {status}")


_RUNNERS = {
    "healthz": lambda base, args: check_healthz(base, args.samples, args.timeout),
    "catalog": lambda base, args: check_catalog(base, args.timeout),
    "site": lambda base, args: check_site(base, args.timeout),
    "shop": lambda base, args: check_shop(base, args.timeout),
    "audit": lambda base, args: check_audit(base, args.timeout),
}


# ------------------------------------------------------------------------ CLI
def _parser():
    parser = argparse.ArgumentParser(
        description="Read-only post-deploy verification of a running JauraStore instance.")
    parser.add_argument("--base", default=DEFAULT_BASE,
                        help=f"site origin to verify (default {DEFAULT_BASE})")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES,
                        help=f"how many /healthz probes to time (default {DEFAULT_SAMPLES})")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"per-request timeout in seconds (default {DEFAULT_TIMEOUT:g})")
    parser.add_argument("--checks", default=",".join(CHECKS),
                        help="comma-separated subset of: " + ", ".join(CHECKS))
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    return parser


def run(base, checks, args):
    """Run the selected checks and return the report (a list of results)."""
    report = []
    for name in checks:
        try:
            report.append(_RUNNERS[name](base, args))
        except RequestFailed as exc:
            report.append(_result(name, False, f"request failed: {exc}"))
        except Exception as exc:                      # never crash the verifier
            report.append(_result(name, False, f"{exc.__class__.__name__}: {exc}"))
    return report


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    # De-duplicate while keeping the order asked for: a repeated name is a
    # typo, not a reason to hit production twice.
    checks = list(dict.fromkeys(
        c.strip().lower() for c in str(args.checks).split(",") if c.strip()))
    unknown = [c for c in checks if c not in _RUNNERS]
    if not checks or unknown:
        parser.error("unknown check(s): " + ", ".join(unknown or ["(none selected)"]))
    if args.samples < 1:
        parser.error("--samples must be at least 1")
    base = str(args.base or "").strip()
    if not base.startswith(("http://", "https://")):
        parser.error("--base must start with http:// or https://")

    started = time.perf_counter()
    report = run(base, checks, args)
    failed = [r for r in report if not r["ok"]]
    summary = {
        "ok": not failed,
        "base": base,
        "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
        "checks": report,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        width = max(len(r["check"]) for r in report)
        for result in report:
            status = "PASS" if result["ok"] else "FAIL"
            print(f"{status}  {result['check'].ljust(width)}  {result['detail']}")
        print()
        print(("POST-DEPLOY CHECKS PASSED" if not failed
               else f"{len(failed)} post-deploy check(s) FAILED")
              + f"  ({summary['elapsedMs']:.0f} ms, {base})")
    if failed and all(r["detail"].startswith("request failed:") for r in failed):
        return 2
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
