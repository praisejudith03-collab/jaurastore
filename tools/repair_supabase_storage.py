"""Move legacy payment proofs to the private bucket before exposing uploads.

Dry-run (default): python tools/repair_supabase_storage.py
Apply:            python tools/repair_supabase_storage.py --apply

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY. It copies every object
under uploads/proofs/ into the private receipts bucket, updates matching
orders.proof_url and receipts.file_url references to fresh signed URLs, removes
the old proof objects, then marks uploads public. Any failed step stops before
the visibility change; rerunning is safe because destination uploads use upsert.
"""
import argparse
import mimetypes
import sys


def _files(bucket, prefix="proofs"):
    """List object keys recursively, keeping directories out of the result."""
    found = []
    pending = [prefix]
    while pending:
        folder = pending.pop()
        offset = 0
        while True:
            rows = bucket.list(folder, {"limit": 500, "offset": offset}) or []
            for row in rows:
                name = str((row or {}).get("name") or "").strip()
                if not name or name in (".", ".."):
                    continue
                path = folder.rstrip("/") + "/" + name
                metadata = row.get("metadata")
                if row.get("id") or isinstance(metadata, dict):
                    found.append((path, metadata if isinstance(metadata, dict) else {}))
                else:
                    pending.append(path)
            if len(rows) < 500:
                break
            offset += len(rows)
    return sorted(found)


def _proof_path(value, store):
    raw = str(value or "").strip()
    if not raw:
        return ""
    bucket = store._bucket_from_url(raw)
    if bucket:
        path = store._storage_path_from_url(raw, bucket)
        return path if bucket == "uploads" and path.startswith("proofs/") else ""
    if raw.startswith("/uploads/proofs/"):
        return raw[len("/uploads/"):].split("?", 1)[0]
    if raw.startswith("proofs/"):
        return raw.split("?", 1)[0]
    return ""


def _rows(client, table, columns):
    rows, offset = [], 0
    while True:
        batch = (client.table(table).select(columns).order("id")
                 .range(offset, offset + 499).execute())
        data = getattr(batch, "data", None)
        if data is None and isinstance(batch, dict):
            data = batch.get("data")
        data = data or []
        rows.extend(data)
        if len(data) < 500:
            return rows
        offset += len(data)


def migrate(apply=False):
    import supabase_store
    from config import Config

    if not (Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY):
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    client = supabase_store.client()
    if client is None:
        raise RuntimeError("Supabase client could not be created")
    old_bucket = client.storage.from_("uploads")
    private_bucket = client.storage.from_("receipts")
    objects = _files(old_bucket)
    if not objects:
        if apply:
            client.storage.update_bucket("uploads", {"public": True})
        return {"objects": 0, "orders": 0, "receipts": 0}

    orders = _rows(client, "orders", "id,proof_url")
    receipt_rows = _rows(client, "receipts", "id,file_url")
    paths = {path for path, _meta in objects}
    referenced = {
        _proof_path(row.get("proof_url"), supabase_store)
        for row in orders
    } | {
        _proof_path(row.get("file_url"), supabase_store)
        for row in receipt_rows
    }
    missing = sorted(path for path in referenced if path and path not in paths)
    if missing:
        raise RuntimeError("Referenced legacy proof object is missing: " + missing[0])

    if not apply:
        return {"objects": len(objects),
                "orders": sum(_proof_path(r.get("proof_url"), supabase_store) in paths for r in orders),
                "receipts": sum(_proof_path(r.get("file_url"), supabase_store) in paths for r in receipt_rows)}

    signed = {}
    for path, metadata in objects:
        blob = old_bucket.download(path)
        mime = (metadata.get("mimetype") or metadata.get("content-type")
                or mimetypes.guess_type(path)[0] or "application/octet-stream")
        private_bucket.upload(path, blob, {"upsert": "true", "content-type": mime})
        response = private_bucket.create_signed_url(path, 604800)
        url = (response.get("signedUrl") if isinstance(response, dict)
               else getattr(response, "signedUrl", ""))
        if not url:
            raise RuntimeError("Could not create a signed private receipt URL for " + path)
        if url.startswith("/"):
            url = Config.SUPABASE_URL.rstrip("/") + url
        signed[path] = url

    changed_orders = 0
    for row in orders:
        path = _proof_path(row.get("proof_url"), supabase_store)
        if path in signed:
            client.table("orders").update({"proof_url": signed[path]}).eq("id", row["id"]).execute()
            changed_orders += 1
    changed_receipts = 0
    for row in receipt_rows:
        path = _proof_path(row.get("file_url"), supabase_store)
        if path in signed:
            client.table("receipts").update({"file_url": signed[path]}).eq("id", row["id"]).execute()
            changed_receipts += 1

    # REST UPDATE can succeed with zero affected rows if a concurrent edit
    # removed/changed a reference. Re-read before deleting any legacy object.
    remaining_orders = _rows(client, "orders", "id,proof_url")
    remaining_receipts = _rows(client, "receipts", "id,file_url")
    stale = [row.get("id") for row in remaining_orders
             if _proof_path(row.get("proof_url"), supabase_store) in signed]
    stale += [row.get("id") for row in remaining_receipts
              if _proof_path(row.get("file_url"), supabase_store) in signed]
    if stale:
        raise RuntimeError("Database proof URL update was not confirmed for row " + str(stale[0]))

    for start in range(0, len(objects), 100):
        batch_paths = [path for path, _meta in objects[start:start + 100]]
        old_bucket.remove(batch_paths)
    if _files(old_bucket):
        raise RuntimeError("Legacy proofs remain in uploads; public visibility was not changed")
    # The public flag changes only after the private copies and every database
    # reference are confirmed and the legacy proof objects are gone.
    client.storage.update_bucket("uploads", {"public": True})
    return {"objects": len(objects), "orders": changed_orders,
            "receipts": changed_receipts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="copy proofs, update database references, remove legacy objects, and enable public uploads")
    args = parser.parse_args(argv)
    try:
        report = migrate(apply=args.apply)
    except Exception as exc:
        print("Storage repair stopped safely: %s" % exc, file=sys.stderr)
        return 1
    mode = "APPLIED" if args.apply else "DRY RUN"
    print("%s: %d legacy proof object(s), %d order reference(s), %d receipt row(s)" % (
        mode, report["objects"], report["orders"], report["receipts"]))
    if not args.apply:
        print("Review the target Supabase project, then rerun with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
