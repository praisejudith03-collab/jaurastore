#!/usr/bin/env python3
"""Apply the APPROVED products schema + the hard-delete migration to Supabase.

This is the automated form of STAGING_HARD_DELETE_RUNBOOK.md, meant to be run
from GitHub Actions (`.github/workflows/staging-schema-migration.yml`) or from
a server shell that already has the credentials in its environment. It never
prints a key, a token or a connection string.

What it applies, in dependency order, and nothing else:

    1. schema_sections/01_products.sql          -> public.products (id text)
    2. schema_sections/09_product_compatibility.sql -> products-only repair
    3. hard_delete_products.sql                 -> the delete RPC + cascade

Deliberately NOT applied: `supabase_schema.sql` (the whole file), sections
15 (storage) and 16 (stock), `inventory_guardrails.sql`, and the image
migration - all on hold per schema_sections/README.md. The approved files are
the minimum that creates `public.products` and the columns the application
contract requires; `--apply` refuses any other file, so a future edit to the
list cannot silently widen the blast radius.

Transport (one of these must be configured):

    SUPABASE_DB_URL        Postgres connection string -> psql, the same path
                           `python3 migrate_supabase.py --schema` documents.
    SUPABASE_ACCESS_TOKEN  Supabase Management API token -> HTTPS, no psql.

Either way the target project is taken from the host inside `SUPABASE_URL`,
and `--confirm-project-ref` must match it, so this cannot be pointed at a
project by accident.

Exit codes: 0 ok (or dry-run), 2 refused/preflight failure, 3 apply failure.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The only files this tool may ever execute, in order. See the module
# docstring: this list is the safety boundary, not a convenience.
APPROVED_FILES = (
    ROOT / "schema_sections" / "01_products.sql",
    ROOT / "schema_sections" / "09_product_compatibility.sql",
    ROOT / "hard_delete_products.sql",
)

MANAGEMENT_API = "https://api.supabase.com/v1/projects/{ref}/database/query"


class Refused(Exception):
    """A preflight failure: nothing was written."""


class ApplyFailed(Exception):
    """A file failed to apply; the tool stops at the first error."""


# --------------------------------------------------------------- environment
def project_ref_from_url(url):
    """The project ref inside https://<ref>.supabase.co, or ""."""
    try:
        host = (urlsplit(url or "").hostname or "")
    except ValueError:
        return ""
    if host.endswith(".supabase.co"):
        return host[: -len(".supabase.co")]
    return ""


def environment():
    """Read the configured credentials without ever returning their values."""
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    db_url = (os.environ.get("SUPABASE_DB_URL") or "").strip()
    token = (os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
    return {
        "url": url,
        "ref": project_ref_from_url(url),
        "has_db_url": bool(db_url),
        "db_url": db_url,
        "has_token": bool(token),
        "token": token,
        "bucket": (os.environ.get("SUPABASE_BUCKET") or "uploads").strip(),
    }


# ------------------------------------------------------------------ transport
class PsqlTransport:
    """DDL through psql + SUPABASE_DB_URL (the documented server path)."""

    name = "psql (SUPABASE_DB_URL)"

    def __init__(self, db_url):
        if not shutil.which("psql"):
            raise Refused("psql is not installed and SUPABASE_DB_URL is set; "
                          "install the postgresql client or use "
                          "SUPABASE_ACCESS_TOKEN instead.")
        self._db_url = db_url

    def query(self, sql):
        """Run one statement/script; return parsed rows (best effort)."""
        done = subprocess.run(
            ["psql", self._db_url, "-At", "-v", "ON_ERROR_STOP=1", "-f", "-"],
            input=sql, capture_output=True, text=True, timeout=300)
        if done.returncode != 0:
            raise ApplyFailed(_tidy(done.stderr) or "psql failed")
        out = (done.stdout or "").strip()
        if not out:
            return []
        rows = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("[") or line.startswith("{"):
                try:
                    rows.append(json.loads(line))
                    continue
                except ValueError:
                    pass
            rows.append(line)
        return rows


class ManagementApiTransport:
    """DDL through the Supabase Management API (no psql needed)."""

    name = "Supabase Management API (SUPABASE_ACCESS_TOKEN)"

    def __init__(self, ref, token):
        self._ref = ref
        self._token = token

    def query(self, sql):
        body = json.dumps({"query": sql}).encode("utf-8")
        request = urllib.request.Request(
            MANAGEMENT_API.format(ref=self._ref), data=body, method="POST",
            headers={"Authorization": f"Bearer {self._token}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise ApplyFailed(f"HTTP {exc.code}: {_tidy(detail)}")
        except Exception as exc:                     # network/DNS/TLS
            raise ApplyFailed(f"{type(exc).__name__}: {exc}")
        raw = (raw or "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except ValueError:
            return [raw]
        return parsed if isinstance(parsed, list) else [parsed]


def _tidy(text):
    """Collapse whitespace; the caller decides what is safe to print."""
    return " ".join(str(text or "").split())[:500]


def choose_transport(env):
    if env["has_db_url"]:
        return PsqlTransport(env["db_url"])
    if env["has_token"]:
        return ManagementApiTransport(env["ref"], env["token"])
    raise Refused(
        "no DDL credential is configured. Set SUPABASE_DB_URL (psql path) or "
        "SUPABASE_ACCESS_TOKEN (Management API) as a repository secret. The "
        "service-role key alone cannot create tables or functions - PostgREST "
        "does not expose DDL.")


# ----------------------------------------------------------------- preflight
def scalar(transport, sql):
    rows = transport.query(sql)
    if not rows:
        return None
    first = rows[0]
    if isinstance(first, dict):
        for value in first.values():
            return value
        return None
    if isinstance(first, list):
        return first[0] if first else None
    if isinstance(first, str):
        try:
            parsed = json.loads(first)
        except ValueError:
            return first
        if isinstance(parsed, dict):
            for value in parsed.values():
                return value
        if isinstance(parsed, list):
            return parsed[0] if parsed else None
    return first


def preflight(transport, allow_existing_rows):
    """Read-only checks. Raises Refused; never writes."""
    report = {}
    report["products_table"] = scalar(
        transport, "select to_regclass('public.products')::text;")
    report["delete_function"] = scalar(
        transport, "select to_regprocedure('public.hard_delete_products(text[])')::text;")
    report["tables"] = transport.query(
        "select coalesce(json_agg(table_name order by table_name), '[]')::text "
        "from information_schema.tables where table_schema = 'public';")
    tables = []
    if report["tables"]:
        raw = report["tables"][0]
        if isinstance(raw, str):
            try:
                tables = json.loads(raw)
            except ValueError:
                tables = []
        elif isinstance(raw, list):
            tables = raw
    report["table_names"] = tables

    if report["products_table"]:
        count = scalar(transport,
                       "select count(*)::text from public.products;")
        report["products_rows"] = int(count or 0)
        if report["products_rows"] > 0 and not allow_existing_rows:
            raise Refused(
                f"public.products already holds {report['products_rows']} row(s). "
                "This tool never touches product data, but applying the "
                "products sections to a populated database is an operator "
                "decision: confirm this project is disposable staging, then "
                "re-run with --allow-existing-rows.")
    else:
        report["products_rows"] = 0
    return report


# --------------------------------------------------------------------- apply
def apply_files(transport, files, log=print):
    """Run each approved file as its own batch; stop at the first failure."""
    for path in files:
        if path not in APPROVED_FILES:
            raise ApplyFailed(
                f"refusing to apply {path}: it is not in APPROVED_FILES "
                "(the approved minimum for public.products + hard delete)")
        if not path.exists():
            raise ApplyFailed(f"missing file: {path}")
        log(f"applying {path.relative_to(ROOT)} ...")
        transport.query(path.read_text())
        log(f"  ok: {path.name}")


def verify(transport, log=print):
    """Post-apply verification, mirroring the runbook's read-only checks."""
    checks = {}
    checks["products_table"] = scalar(
        transport, "select to_regclass('public.products')::text;")
    checks["delete_function"] = scalar(
        transport, "select to_regprocedure('public.hard_delete_products(text[])')::text;")
    checks["ledger"] = scalar(
        transport, "select to_regclass('public.deleted_products')::text;")
    roles = transport.query(
        "select coalesce(json_agg(json_build_object("
        "  'role', r, 'can', has_function_privilege(r, "
        "  'public.hard_delete_products(text[])', 'EXECUTE'))), '[]')::text "
        "from unnest(array['service_role','anon','authenticated']) r "
        "where exists (select 1 from pg_roles where rolname = r);")
    checks["privileges"] = []
    if roles and isinstance(roles[0], str):
        try:
            checks["privileges"] = json.loads(roles[0])
        except ValueError:
            checks["privileges"] = []

    log("verification:")
    log(f"  to_regclass('public.products')                    = {checks['products_table']}")
    log(f"  to_regprocedure('public.hard_delete_products(text[])') = {checks['delete_function']}")
    log(f"  to_regclass('public.deleted_products')            = {checks['ledger']}")
    for entry in checks["privileges"]:
        log(f"  execute({entry.get('role')}) = {entry.get('can')}")
    problems = []
    if not checks["products_table"]:
        problems.append("public.products is still missing")
    if not checks["delete_function"]:
        problems.append("public.hard_delete_products(text[]) is still missing")
    for entry in checks["privileges"]:
        if entry.get("role") == "service_role" and entry.get("can") is not True:
            problems.append("service_role cannot execute the delete function")
        if entry.get("role") in ("anon", "authenticated") and entry.get("can"):
            problems.append(f"{entry.get('role')} can execute the delete function")
    if problems:
        raise ApplyFailed("; ".join(problems))
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--confirm-project-ref", required=True,
                        help="must equal the project ref inside SUPABASE_URL")
    parser.add_argument("--apply", action="store_true",
                        help="actually apply; without it this is read-only")
    parser.add_argument("--allow-existing-rows", action="store_true",
                        help="proceed even when public.products already has rows")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan; needs no credentials and no network")
    args = parser.parse_args(argv)

    plan = [str(p.relative_to(ROOT)) for p in APPROVED_FILES]
    print("approved migration plan (in order):")
    for name in plan:
        print(f"  - {name}")
    print("not applied: supabase_schema.sql, sections 15/16, "
          "inventory_guardrails.sql, the image migration")

    if args.dry_run:
        print("\ndry run: nothing was read from or written to any project.")
        return 0

    env = environment()
    if not env["url"]:
        print("REFUSED: SUPABASE_URL is not set.", file=sys.stderr)
        return 2
    if not env["ref"]:
        print("REFUSED: SUPABASE_URL is not a <ref>.supabase.co URL.",
              file=sys.stderr)
        return 2
    print(f"\ntarget project ref: {env['ref']}")
    if (args.confirm_project_ref or "").strip() != env["ref"]:
        print("REFUSED: --confirm-project-ref does not match the project in "
              "SUPABASE_URL. Nothing was changed.", file=sys.stderr)
        return 2

    try:
        transport = choose_transport(env)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(f"transport: {transport.name}")

    try:
        report = preflight(transport, args.allow_existing_rows)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except ApplyFailed as exc:
        print(f"REFUSED: preflight could not read the project: {exc}",
              file=sys.stderr)
        return 2
    print(f"preflight: products table = {report['products_table'] or 'MISSING'}, "
          f"rows = {report['products_rows']}, "
          f"delete function = {report['delete_function'] or 'MISSING'}")
    print(f"existing public tables: {', '.join(report['table_names']) or '(none)'}")

    if not args.apply:
        print("\nread-only run: nothing was applied. Re-run with --apply to "
              "write the three approved files.")
        return 0

    try:
        apply_files(transport)
        verify(transport)
    except ApplyFailed as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 3
    print("\ndone: the approved schema and the delete migration are applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
