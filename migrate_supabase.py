#!/usr/bin/env python3
"""Seed / migrate the J Aura Store catalogue and legacy records into Supabase.

Run once (with SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY set) to push the
existing products into Supabase so it can become the source of truth:

    python3 migrate_supabase.py            # import seed.json + catalog.json
    python3 migrate_supabase.py --reset    # drop rows first (DANGEROUS)
    python3 migrate_supabase.py --receipts # legacy receipts -> private bucket
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
  Pre-cutover payment receipts were uploaded into the PUBLIC `uploads` bucket
  (URLs like .../storage/v1/object/sign/uploads/proofs/...) or written to the
  local uploads dir (/uploads/proofs/...). Those rows are re-pointed at a
  fresh SIGNED URL in the PRIVATE `receipts` bucket:
    - a public-bucket object is copied into the private bucket (the old
      object is NEVER deleted - the report lists both URLs),
    - a local file is uploaded into the private bucket (the local file is
      NEVER deleted - the report lists its path),
    - the receipts row's file_url is updated to the new signed URL,
    - rows whose object is already private are skipped,
    - rows whose object cannot be found are listed as errors, left untouched.
  A JSON report (printed, and written when --report is given) records every
  action so the cutover can be reviewed before the old bucket/disk copy is
  cleaned up by hand.

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


def _signed_url_from(c, private_bucket, path):
    res = c.storage.from_(private_bucket).create_signed_url(path, 604800)
    if isinstance(res, dict):
        return res.get("signedUrl") or ""
    return getattr(res, "signedUrl", "") or ""


def _migrate_receipts(sb):
    """Re-point legacy receipt rows at fresh signed URLs in the PRIVATE bucket."""
    from supabase_store import (client, _bucket, _private_bucket,
                                _bucket_from_url, _storage_path_from_url)
    c = client()
    if c is None:
        print("Could not initialise the Supabase client.")
        sys.exit(1)
    public = _bucket()
    private = _private_bucket()
    try:
        res = sb.table("receipts").select(
            "id, order_id, file_url, proof_url, file_name").execute()
    except Exception as exc:
        print(f"receipts read failed: {exc}")
        sys.exit(1)
    rows = (res.data or []) if isinstance(res, dict) else getattr(res, "data", []) or []
    report = {"private_bucket": private, "public_bucket": public,
              "migrated": [], "skipped": [], "errors": [], "summary": {}}
    for row in rows:
        rid = str(row.get("id") or "")
        old = str(row.get("file_url") or row.get("proof_url") or "").strip()
        if not old:
            report["skipped"].append({"id": rid, "reason": "no file_url"})
            continue
        if "/storage/v1/object/" in old:
            b = _bucket_from_url(old)
            path = _storage_path_from_url(old, b)
        else:
            b = ""
            path = (old.removeprefix("/uploads/").strip("/")
                    if old.startswith("/uploads/") else old.lstrip("/"))
        if b == private:
            # already points at the private bucket; leave it untouched
            report["skipped"].append({"id": rid, "reason": "already private",
                                      "url": old})
            continue
        if not path:
            report["skipped"].append({"id": rid, "reason": "unrecognised url",
                                      "url": old})
            continue
        # Only receipt objects (*proofs/*) get migrated
        if path.lstrip("/").split("/", 1)[0].lower() not in ("proofs", "receipts"):
            report["skipped"].append({"id": rid, "reason": "not a proof object",
                                      "url": old})
            continue
        try:
            try:
                c.storage.from_(private).info(path)
                note = "private copy exists; URL refreshed"
            except Exception:
                if b and b != private:
                    # legacy object in the public bucket: copy, never delete
                    data = c.storage.from_(b).download(path)
                    c.storage.from_(private).upload(
                        path, data, {"content-type": "application/octet-stream",
                                     "upsert": "true"})
                    note = ("copied to private bucket from "
                            f"{b} (old object left in place)")
                else:
                    # local-disk legacy file: upload, never delete the file
                    import storage as storage_mod
                    root = storage_mod.local_root()
                    full = os.path.abspath(os.path.join(
                        root, path.replace("/", os.sep)))
                    if not full.startswith(root + os.sep) or not os.path.isfile(full):
                        raise FileNotFoundError(f"local file missing: {full}")
                    with open(full, "rb") as fh:
                        data = fh.read()
                    c.storage.from_(private).upload(
                        path, data, {"content-type": "application/octet-stream",
                                     "upsert": "true"})
                    note = f"uploaded from local disk ({full}; file left in place)"
            signed = _signed_url_from(c, private, path)
            if not signed:
                raise RuntimeError("no signed URL returned")
            sb.table("receipts").update({"file_url": signed}).eq("id", rid).execute()
            report["migrated"].append({
                "id": rid, "order_id": row.get("order_id") or "",
                "object": path, "from": old, "to": signed, "note": note})
        except Exception as exc:
            report["errors"].append({"id": rid, "url": old,
                                     "error": f"{exc.__class__.__name__}: {exc}"})
    report["summary"] = {
        "total": len(rows),
        "migrated": len(report["migrated"]),
        "skipped": len(report["skipped"]),
        "errors": len(report["errors"]),
    }
    print(f"\nReceipt migration: {report['summary']['migrated']} migrated, "
          f"{report['summary']['skipped']} skipped, "
          f"{report['summary']['errors']} errors "
          f"({report['summary']['total']} rows).")
    for m in report["migrated"]:
        print(f"  [migrated] {m['id']} {m['object']} -> {m['to']}")
    for e in report["errors"]:
        print(f"  [error]    {e['id']} {e['url']}: {e['error']}")
    _write_report(report)


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
