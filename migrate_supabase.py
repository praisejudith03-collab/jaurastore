#!/usr/bin/env python3
"""Seed / migrate the J Aura Store catalogue and legacy records into Supabase.

Run once (with SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY set) to push the
existing products into Supabase so it can become the source of truth:

    python3 migrate_supabase.py            # import seed.json + catalog.json
    python3 migrate_supabase.py --reset    # drop rows first (DANGEROUS)
    python3 migrate_supabase.py --receipts # disabled legacy receipt migration
    python3 migrate_supabase.py --schema   # apply supabase_schema.sql (needs
                                           # SUPABASE_DB_URL + psql)
    python3 migrate_supabase.py --report out.json

Product import:
  Reads the seed catalogue (data/seed.json -> the first 258 products) and the
  admin overrides (data/catalog.json, applied on top), then upserts them into
  the Supabase `products` table expected by catalog.py / supabase_store.py.
  Every row carries BOTH the canonical columns (image_url, images,
  stock_quantity) and the legacy aliases (image, stock) so old readers and
  the new code keep working. It never deletes existing Supabase rows unless
  --reset is passed; prices are preserved as-is.

Receipt migration (--receipts):
  Disabled: the old cross-bucket procedure is incompatible with the single
  public uploads bucket. Existing receipt records and objects must be reviewed
  before any explicit migration; this command makes no receipt/storage writes.

Run the product import BEFORE switching on SUPABASE_URL /
SUPABASE_SERVICE_ROLE_KEY so the shop has data to serve.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config              # noqa: E402


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _load_seed():
    """Return a dict of products from data/seed.json, keyed by id."""
    seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "seed.json")
    try:
        with open(seed_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = []
    return {p.get("id"): p for p in data if isinstance(p, dict) and p.get("id")}


def _load_overrides():
    """Return admin products from data/catalog.json, keyed by id."""
    cat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "catalog.json")
    try:
        with open(cat_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    return {p.get("id"): p for p in (data.get("products") or [])
            if isinstance(p, dict) and p.get("id")}


def _report_path():
    """Where the JSON migration report is written (--report overrides)."""
    for i, arg in enumerate(sys.argv):
        if arg == "--report" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "supabase_migration_report.json")


def _write_report(report):
    path = _report_path()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        print(f"\nReport written to {path}")
    except OSError as exc:
        print(f"\nCould not write the report file: {exc}")


def _migrate_products(sb, reset):
    if reset:
        try:
            sb.table("products").delete().gt("id", "").execute()
        except Exception as exc:
            print(f"(reset) delete failed — continuing: {exc}")

    merged = _load_seed()
    merged.update(_load_overrides())

    # Reconcile every product's image against the repository so the Supabase
    # rows link to a path the browser can actually show: a committed repo
    # image (images/products/x.jpg) when it exists, else the committed branded
    # placeholder. No third-party / Wix photo is ever referenced. See
    # catalog.resolve_image().
    import catalog as catalog_mod
    merged = {pid: catalog_mod.resolve_image(p) for pid, p in merged.items()}

    rows = []
    now = _now()
    for pid, p in merged.items():
        # CANONICAL columns (source of truth) plus the legacy aliases old
        # readers still use. image_url/stock_quantity are written from their
        # canonical key when present, else from the legacy key - prices are
        # preserved exactly as stored.
        image_url = p.get("image_url") or p.get("image") or ""
        try:
            stock_quantity = int(p.get("stock_quantity")
                                 if p.get("stock_quantity") is not None
                                 else p.get("stock") or 0)
        except (TypeError, ValueError):
            stock_quantity = 0
        row = {
            "id": pid,
            "sku": p.get("sku", ""),
            "slug": p.get("slug", ""),
            "name": p.get("name", ""),
            "nameFr": p.get("nameFr", ""),
            "category": p.get("category", ""),
            "priceCfa": p.get("priceCfa", 0),
            "compareCfa": p.get("compareCfa", None),
            "priceNgn": p.get("priceNgn", 0),
            "compareNgn": p.get("compareNgn", None),
            # canonical product asset fields
            "image_url": image_url,
            "images": p.get("images", [p.get("image")] if image_url else []),
            "stock_quantity": stock_quantity,
            # legacy aliases (compatibility; written from the same values)
            "image": image_url,
            "stock": stock_quantity,
            "placeholderImage": p.get("placeholderImage", ""),
            "description": p.get("description", ""),
            "badge": p.get("badge", ""),
            "featured": bool(p.get("featured", False)),
            "online": p.get("online", True) is not False,
            "colors": p.get("colors", []),
            "options": p.get("options", []),
            "source": "admin" if pid in _load_overrides() else "seed",
            "updated_at": now,
        }
        rows.append(row)

    if rows:
        try:
            sb.table("products").upsert(rows).execute()
        except Exception as exc:
            print(f"Upsert failed: {exc}")
            sys.exit(1)
    print(f"Imported {len(rows)} products into Supabase (source of truth ready).")
    print("Now set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY to switch the shop over.")


def _migrate_receipts(sb):
    """Fail safely instead of running the obsolete cross-bucket migration."""
    raise SystemExit("Receipt migration is disabled under the uploads-only policy. "
                     "Review existing objects and records before planning a migration.")


def _apply_schema():
    """Apply supabase_schema.sql through psql (idempotent, ON_ERROR_STOP)."""
    conn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not conn:
        print("--schema needs SUPABASE_DB_URL (the Postgres pooler connection"
              " string with the service role).")
        print("Alternatively paste supabase_schema.sql into the Supabase SQL"
              " editor (Dashboard -> SQL -> New query) and run it there.")
        sys.exit(1)
    if not shutil.which("psql"):
        print("psql is not installed. Install the postgresql client, or run"
              " supabase_schema.sql in the Supabase SQL editor.")
        sys.exit(1)
    schema = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "supabase_schema.sql")
    try:
        subprocess.run(["psql", conn, "-v", "ON_ERROR_STOP=1", "-f", schema],
                       check=True)
    except subprocess.CalledProcessError as exc:
        print(f"schema apply failed: {exc}")
        sys.exit(1)
    print("supabase_schema.sql applied (every statement is idempotent).")


def main():
    do_receipts = "--receipts" in sys.argv
    do_schema = "--schema" in sys.argv
    reset = "--reset" in sys.argv
    if not (Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY):
        print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (and .env) first.")
        sys.exit(1)

    if do_schema:
        _apply_schema()
        if not (do_receipts or reset):
            return

    from supabase_store import client
    sb = client()
    if sb is None:
        print("Could not initialise the Supabase client.")
        sys.exit(1)

    if do_receipts:
        _migrate_receipts(sb)
        return
    _migrate_products(sb, reset)


if __name__ == "__main__":
    main()
