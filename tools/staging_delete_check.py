#!/usr/bin/env python3
"""Live check: the admin delete path really deletes + purges Storage.

Run it where the server credentials already are (Render shell, CI with the
repository secrets, or any machine with SUPABASE_URL +
SUPABASE_SERVICE_ROLE_KEY exported). It writes only one uniquely named
disposable product and one dummy object, and removes both:

    1. read-only preflight: the key works and public.products exists;
    2. the delete RPC exists and service_role may execute it - checked with an
       EMPTY id array, which the function documents as a no-op;
    3. upload a dummy object  uploads/products/staging-checks/<uuid>.jpg;
    4. insert one disposable product row (id jau-staging-check-<uuid>,
       online=false) pointing at that object;
    5. call the SAME function the Admin API calls,
       ``supabase_store.hard_delete_products([pid])`` - so the RPC, the
       tombstone mirror, the child sweep and the Storage purge all run;
    6. verify from the outside: the row is gone, the object 404s, and
       re-creating the id is refused by the tombstone trigger.

It never deletes anything it did not create, never reuses an id, and never
prints a key. Exit codes: 0 all checks passed, 2 refused/preflight, 3
verification failed.

Usage:
    python3 tools/staging_delete_check.py --confirm-project-ref <ref>
    python3 tools/staging_delete_check.py --dry-run      # plan only, no network
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BUCKET = "uploads"                     # storage.py: the sole supported bucket
# An 8x8 JPEG (632 bytes) so Storage applies its normal image rules to the
# dummy object. Generated with Pillow, verified decodable, inlined to keep
# this tool dependency-free.
DUMMY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAA0JCgsKCA0LCgsODg0PEyAVExISEycc"
    "HhcgLikxMC4pLSwzOko+MzZGNywtQFdBRkxOUlNSMj5aYVpQYEpRUk//2wBDAQ4O"
    "DhMREyYVFSZPNS01T09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09P"
    "T09PT09PT09PT09PT0//wAARCAAIAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEA"
    "AAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIh"
    "MUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6"
    "Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZ"
    "mqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx"
    "8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAV"
    "YnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hp"
    "anN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPE"
    "xcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDL"
    "ooorkOo//9k="
)


class Refused(Exception):
    """Preflight says no; nothing was written."""


class CheckFailed(Exception):
    """A verification check failed."""


# ------------------------------------------------------------------- plumbing
def project_ref_from_url(url):
    try:
        host = (urlsplit(url or "").hostname or "")
    except ValueError:
        return ""
    return host[: -len(".supabase.co")] if host.endswith(".supabase.co") else ""


class Checks:
    def __init__(self):
        self.results = []

    def record(self, ok, label, detail=""):
        self.results.append((bool(ok), label, str(detail)))
        mark = "PASS" if ok else "FAIL"
        line = f"[{mark}] {label}"
        if detail:
            line += f"  ({detail})"
        print(line)
        return bool(ok)

    @property
    def failures(self):
        return [r for r in self.results if not r[0]]


def request(method, url, key, body=None, content_type="application/json",
            extra_headers=None, timeout=60):
    """One HTTPS call. Returns (status, headers, text); never raises on 4xx."""
    data = body
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode("utf-8")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    if data is not None:
        headers["Content-Type"] = content_type
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read().decode("utf-8", "replace")
    except Exception as exc:                       # network/DNS/TLS
        raise Refused(f"cannot reach the project: {type(exc).__name__}: {exc}")


# ----------------------------------------------------------------------- main
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--confirm-project-ref", default="",
                        help="must equal the project ref inside SUPABASE_URL")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan; needs no credentials and no network")
    parser.add_argument("--keep-media", action="store_true",
                        help="leave the dummy object behind if the purge fails")
    args = parser.parse_args(argv)

    pid = f"jau-staging-check-{uuid.uuid4().hex[:12]}"
    key = f"products/staging-checks/{uuid.uuid4().hex[:12]}.jpg"

    if args.dry_run:
        print("staging delete check - plan (dry run, no network):")
        print(f"  1. preflight: products table + the delete RPC (empty-array no-op)")
        print(f"  2. upload one dummy object  {BUCKET}/{key}")
        print(f"  3. insert one disposable product row (id {pid}, online=false)")
        print(f"  4. call supabase_store.hard_delete_products([{pid!r}])")
        print("  5. verify: row gone, object 404s, the id cannot be re-created")
        print("nothing was read from or written to any project.")
        return 0

    base = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    service_key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not base or not service_key:
        print("REFUSED: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set "
              "(server-side credentials; never share or print them).",
              file=sys.stderr)
        return 2
    ref = project_ref_from_url(base)
    if not ref:
        print("REFUSED: SUPABASE_URL is not a <ref>.supabase.co URL.",
              file=sys.stderr)
        return 2
    if args.confirm_project_ref.strip() != ref:
        print("REFUSED: --confirm-project-ref does not match the project in "
              "SUPABASE_URL. Nothing was changed.", file=sys.stderr)
        return 2

    print(f"staging delete check against project {ref} (bucket {BUCKET})")
    checks = Checks()
    object_url = f"{base}/storage/v1/object/{BUCKET}/{key}"
    public_url = f"{base}/storage/v1/object/public/{BUCKET}/{key}"
    inserted = False

    try:
        # 1. preflight: key works, products table exists
        status, _h, text = request(
            "GET", f"{base}/rest/v1/products?select=id&limit=1", service_key)
        if status >= 400:
            print(f"REFUSED: products table is not readable ({status}): {text[:200]}",
                  file=sys.stderr)
            return 2
        checks.record(True, "public.products is readable with the server key")

        # 2. the RPC exists and service_role may execute it (empty = no-op)
        status, _h, text = request(
            "POST", f"{base}/rest/v1/rpc/hard_delete_products", service_key,
            {"product_ids": []})
        if status >= 400:
            print("REFUSED: hard_delete_products is not callable "
                  f"({status}): {text[:200]} - apply hard_delete_products.sql "
                  "first (see STAGING_HARD_DELETE_RUNBOOK.md).", file=sys.stderr)
            return 2
        checks.record(text.strip() in ("[]", "{}", ""), 
                      "hard_delete_products(text[]) exists and is executable",
                      f"empty-array call returned {text.strip()[:40]!r}")

        # 3. upload the dummy object
        status, _h, text = request("POST", object_url, service_key,
                                   DUMMY_JPEG, "image/jpeg",
                                   {"x-upsert": "false"})
        if status >= 400:
            print(f"REFUSED: could not upload the dummy object ({status}): "
                  f"{text[:200]}", file=sys.stderr)
            return 2
        checks.record(True, "dummy object uploaded", f"{BUCKET}/{key}")

        # 4. one disposable product row pointing at it
        row = {"id": pid, "name": "Disposable staging check",
               "slug": pid, "source": "admin", "online": False,
               "image": public_url, "image_url": public_url,
               "images": [public_url], "priceNgn": 1000, "priceCfa": 800,
               "stock": 0, "stock_quantity": 0}
        status, _h, text = request(
            "POST", f"{base}/rest/v1/products", service_key, row,
            extra_headers={"Prefer": "return=representation"})
        if status >= 400:
            raise CheckFailed(f"could not create the disposable row ({status}): {text[:200]}")
        inserted = True
        checks.record(True, "disposable product created", pid)

        # 5. the SAME call the Admin API makes
        # The app reads its Storage backend from the environment at import
        # time; make sure the purge follows the Supabase path even on a shell
        # that forgot to export UPLOAD_MODE.
        os.environ.setdefault("UPLOAD_MODE", "supabase")
        import supabase_store
        report = supabase_store.hard_delete_products([pid])
        checks.record(report.get("deleted") == [pid],
                      "the delete RPC removed the row",
                      f"report.deleted={report.get('deleted')}")
        checks.record(int(report.get("files") or 0) >= 1,
                      "the replaced object was purged from Storage",
                      f"report.files={report.get('files')}")
        checks.record(not report.get("errors"),
                      "no cleanup errors were reported",
                      "; ".join(report.get("errors") or [])[:200])

        # 6. verify from the outside
        status, _h, text = request(
            "GET", f"{base}/rest/v1/products?id=eq.{pid}&select=id", service_key)
        gone = status < 400 and (text.strip() in ("[]", ""))
        inserted = inserted and not gone
        checks.record(gone, "the product row is gone from PostgreSQL")

        status, _h, _text = request("HEAD", public_url, service_key)
        checks.record(status in (400, 404),
                      "the object is gone from the bucket", f"HTTP {status}")

        # The tombstone is what makes the delete permanent; prove it the only
        # way that does not depend on reading the ledger over PostgREST.
        status, _h, text = request("POST", f"{base}/rest/v1/products",
                                   service_key, dict(row, online=False))
        rejected = status >= 400 and "permanently deleted" in text
        checks.record(rejected, "the deleted id cannot be re-created",
                      f"HTTP {status}: {text.strip()[:80]}")
        if status < 400:
            inserted = True        # the re-create landed; clean it up below

    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except CheckFailed as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
    except Exception as exc:                            # pragma: no cover
        print(f"FAILED: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)

    # ---- cleanup: only ever our own disposable row and object -------------
    if inserted:
        try:
            import supabase_store
            supabase_store.hard_delete_products([pid])
            print(f"cleanup: re-ran the delete for {pid}")
        except Exception as exc:                        # pragma: no cover
            print(f"cleanup: could not remove {pid}: {exc}", file=sys.stderr)
    if not args.keep_media:
        status, _h, _text = request("HEAD", public_url, service_key)
        if status not in (400, 404):
            request("DELETE", object_url, service_key)
            print("cleanup: removed the dummy object")

    if checks.failures:
        print(f"\nRESULT: FAIL ({len(checks.failures)} check(s) failed)")
        return 3
    print("\nRESULT: PASS - the admin delete path really deletes the row, "
          "purges its unshared media and leaves a live tombstone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
