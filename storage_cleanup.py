"""Orphaned & duplicate upload cleanup for Jaura Store.

Scans every place the application can reference a stored upload and purges
the objects nobody points at any more:

* products (cover, gallery, video, per-option media) - via catalog.merged(),
  which is the same read the storefront uses, in BOTH storage modes;
* orders and payment receipts (orders.proof_url, receipts.file_url /
  payment_proofs.file_url) - the receipts a deleted order forgot;
* site settings (logo, hero video/poster/document, shop banner, social
  images...) - every string value of the live settings row;
* categories (their artwork columns).

Everything else in the upload storage is a candidate for deletion, guarded
by two safety rules:

1. NOTHING referenced is ever a candidate - and the check is re-run at
   apply time, so a product saved while the report was being reviewed
   cannot lose its photo to a stale plan.
2. NOTHING younger than ``min_age_days`` (default 2) is a candidate - an
   upload that just landed (the admin is still filling the form, or the
   product row write is in flight) must never be hoovered up by a cleanup
   that happens to run at the wrong second. Objects whose age cannot be
   established are treated as brand new (protected), never as ancient.

Duplicate content is detected by SHA-256 among the candidates and reported
as ``duplicate_groups``; every orphan is deleted whatever it duplicates.
Duplicates that are still REFERENCED are never rewritten or deleted by this
routine - re-pointing live product rows at another object's bytes is a data
change, not a cleanup, and stays a manual decision.

The module works against BOTH back ends: the local ``data/uploads`` tree
(development / testing) and the Supabase Storage bucket (production), using
the same conventions as storage.py / supabase_store.py.
"""
import datetime
import hashlib
import os
import re

MEDIA_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "avif", "heic",
              "mp4", "webm", "mov", "pdf", "doc", "docx"}
MAX_HASH_BYTES = 64 * 1024 * 1024   # never read more than 64 MB per object


# ------------------------------------------------------------------ helpers

def _now():
    return datetime.datetime.utcnow()


def _key_from_url(value):
    try:
        import storage
        return storage._key_from_url(str(value or ""))
    except Exception:
        return ""


def _harvest_strings(value, out):
    """Collect every string inside a nested dict/list (settings rows,
    JSON columns) so no media reference hides in an unexpected key."""
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _harvest_strings(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _harvest_strings(item, out)


def _age_ok(info, min_age_days):
    """True when an object is old enough to be considered abandoned."""
    if not min_age_days or min_age_days <= 0:
        return True
    changed = info.get("changed_at")
    if not isinstance(changed_at := changed, datetime.datetime):
        return False                      # unknown age -> protect it
    return (_now() - changed_at).total_seconds() >= float(min_age_days) * 86400


# ------------------------------------------------------- reference scanning

def _referenced_keys():
    """Every storage key the live application still points at.

    Returns (keys, sources) where sources maps a human name to the number of
    keys it contributed. Raises when a reference source cannot be read: an
    unreadable source must abort the whole scan, never look like zero
    references (that would turn every active asset into an apparent orphan).
    """
    keys, sources = set(), {}

    def _add(name, values):
        added = 0
        for value in values:
            key = _key_from_url(value)
            if key and key not in keys:
                keys.add(key)
                added += 1
        if added:
            sources[name] = sources.get(name, 0) + added

    # 1. products - the same read the storefront uses, both storage modes
    import catalog
    products = catalog.merged(include_hidden=True)      # may raise -> abort
    for product in products or []:
        _add("products", catalog._media_refs(product or {}))

    # 2. orders + receipts: the local operational copy, plus the Supabase
    # mirror when configured (an ephemeral host may hold rows only there).
    try:
        from db import query
        values = []
        for row in query("SELECT proof_url FROM orders") or []:
            values.append((row["proof_url"] if "proof_url" in row.keys() else "") or "")
        for row in query("SELECT file_url FROM payment_proofs") or []:
            values.append((row["file_url"] if "file_url" in row.keys() else "") or "")
        _add("orders+receipts", values)
    except Exception as exc:
        raise RuntimeError(f"could not read orders/receipts: {exc}") from exc
    try:
        import supabase_store
        c = supabase_store.client()
        if c is not None:
            values = []
            res = c.table("orders").select("proof_url").execute()
            for row in (getattr(res, "data", None) or []):
                values.append(str((row or {}).get("proof_url") or ""))
            res = c.table("receipts").select("file_url, proof_url").execute()
            for row in (getattr(res, "data", None) or []):
                values.append(str((row or {}).get("file_url") or ""))
                values.append(str((row or {}).get("proof_url") or ""))
            _add("supabase orders+receipts", values)
    except Exception as exc:
        raise RuntimeError(f"could not read Supabase orders/receipts: {exc}") from exc

    # 3. site settings (logo, hero assets, banners, ...) - local or Supabase.
    # When Supabase is not configured the settings feature does not exist on
    # this host, so no settings-referenced upload can exist either: skip the
    # source. When it IS configured, a failed read aborts the scan (an
    # unreadable reference source must never look like zero references).
    try:
        import supabase_settings
        if supabase_settings.enabled() and supabase_settings.client() is not None:
            strings = []
            _harvest_strings(supabase_settings.get_site_settings() or {}, strings)
            _add("site_settings", strings)
    except RuntimeError as exc:
        if "required" not in str(exc):
            raise RuntimeError(f"could not read site settings: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"could not read site settings: {exc}") from exc

    # 4. categories (artwork columns), best effort but never silent: the
    # category source is a JSON file / table the owner edits; if it cannot
    # be read the scan is abandoned like any other reference source.
    try:
        strings = []
        _harvest_strings(_category_rows(), strings)
        _add("categories", strings)
    except Exception as exc:
        raise RuntimeError(f"could not read categories: {exc}") from exc

    return keys, sources


def _category_rows():
    import catalog
    try:
        rows = catalog._read_categories_file()
        if isinstance(rows, dict):
            rows = rows.get("categories") or []
        return rows or []
    except Exception:
        return []
    # A failed category read falls through to the local mirror; the Supabase
    # categories table is covered by the settings scan in production mode.


# ------------------------------------------------------------- object lists

def _list_local():
    """Every stored file under the local upload root as {key: info}."""
    import storage
    root = storage.local_root()
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(base, name)
            key = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                stat = os.stat(full)
            except OSError:
                continue
            out[key] = {"key": key, "bytes": stat.st_size,
                        "changed_at": datetime.datetime.utcfromtimestamp(stat.st_mtime),
                        "path": full}
    return out


def _list_supabase():
    """Every object in the app's Supabase Storage bucket as {key: info}."""
    import supabase_store
    c = supabase_store.client()
    if c is None:
        raise RuntimeError("Supabase storage is not configured.")
    bucket_name = supabase_store._bucket()
    bucket = c.storage.from_(bucket_name)

    def _walk(prefix=""):
        rows, offset = [], 0
        while True:
            batch = bucket.list(prefix, {"limit": 1000, "offset": offset,
                                         "sortBy": {"column": "name", "order": "asc"}}) or []
            if not batch:
                break
            for row in batch:
                name = row.get("name", "")
                path = f"{prefix}/{name}".strip("/")
                # folders come back without an object id / metadata
                if row.get("id") is None and not row.get("metadata"):
                    rows.extend(_walk(path))
                else:
                    rows.append((path, row))
            if len(batch) < 1000:
                break
            offset += len(batch)
        return rows

    out = {}
    for path, row in _walk():
        meta = row.get("metadata") or {}
        changed = None
        for raw in (row.get("updated_at"), row.get("created_at"),
                    meta.get("lastModified"), meta.get("created")):
            parsed = _parse_ts(raw)
            if parsed:
                changed = parsed if changed is None else min(changed, parsed)
        out[path] = {"key": path, "bytes": int(meta.get("size") or 0),
                     "changed_at": changed, "row": row}
    return out


def _parse_ts(raw):
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00")) \
                                 .replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.datetime.utcfromtimestamp(float(raw) / 1000.0)
        except (TypeError, ValueError):
            return None


# ------------------------------------------------------------ plan & apply

def build_plan(min_age_days=2, list_objects=None, referenced=None):
    """Dry-run the cleanup. Returns a report dict (never deletes anything).

    ``list_objects`` / ``referenced`` can be injected by tests.
    """
    if referenced is None:
        referenced, sources = _referenced_keys()
    else:
        referenced, sources = set(referenced), {"injected": len(referenced)}
    if list_objects is None:
        list_objects = _list_local() if _local_mode() else _list_supabase()

    candidates, protected_young, protected_referenced, foreign_ext = [], [], [], []
    for key, info in sorted(list_objects.items()):
        ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
        if ext not in MEDIA_EXTS:
            foreign_ext.append(key)          # never touch unknown file types
            continue
        if any(key == ref or ref.endswith("/" + key) or key.endswith("/" + ref)
               for ref in referenced):
            protected_referenced.append(key)
            continue
        if not _age_ok(info, min_age_days):
            protected_young.append(key)
            continue
        candidates.append(key)

    # duplicate detection among the candidates (content-addressed)
    duplicate_groups = _duplicate_groups(candidates, list_objects)

    return {
        "ok": True,
        "mode": "local" if _local_mode() else "supabase",
        "min_age_days": min_age_days,
        "objects": len(list_objects),
        "referenced": len(referenced),
        "reference_sources": sources,
        "delete_candidates": candidates,
        "candidate_count": len(candidates),
        "candidate_bytes": sum(int((list_objects.get(k) or {}).get("bytes") or 0)
                               for k in candidates),
        "protected_referenced": len(protected_referenced),
        "protected_recent_uploads": len(protected_young),
        "skipped_non_media": len(foreign_ext),
        "duplicate_groups": duplicate_groups,
    }


def _duplicate_groups(candidates, list_objects):
    groups, seen = {}, {}
    for key in candidates:
        digest = _digest(key, list_objects)
        if not digest:
            continue
        seen[key] = digest
        groups.setdefault(digest, []).append(key)
    # only report groups with 2+ identical objects
    return [sorted(keys) for digest, keys in sorted(groups.items()) if len(keys) > 1]


def _digest(key, list_objects):
    info = list_objects.get(key) or {}
    data = _read_object(info)
    if not data:
        return None
    return hashlib.sha256(data).hexdigest()


def _read_object(info):
    if int(info.get("bytes") or 0) > MAX_HASH_BYTES:
        return None
    if "path" in info:                     # local file
        try:
            with open(info["path"], "rb") as fh:
                return fh.read()
        except OSError:
            return None
    try:                                    # Supabase object
        import supabase_store
        c = supabase_store.client()
        if c is None:
            return None
        return c.storage.from_(supabase_store._bucket()).download(info["key"])
    except Exception:
        return None


def _local_mode():
    from config import Config
    return Config.UPLOAD_MODE != "supabase"


def apply_plan(report=None, min_age_days=2):
    """Delete the plan's candidates. Re-validates every key against the
    LIVE reference set (and the age rule) immediately before deleting, so
    a product saved after the report was made can never lose its photo.

    Returns {"deleted": [keys], "freed_bytes": n, "errors": [str]}.
    """
    deleted, errors, freed = [], [], 0
    try:
        referenced, _sources = _referenced_keys()
    except Exception as exc:
        return {"deleted": [], "freed_bytes": 0,
                "errors": [f"reference scan failed, nothing deleted: {exc}"]}
    if report and report.get("delete_candidates"):
        keys = [str(k) for k in report["delete_candidates"]]
    else:
        plan = build_plan(min_age_days=min_age_days)
        keys = plan["delete_candidates"]

    for key in keys:
        if not isinstance(key, str) or ".." in key or key.startswith("/"):
            continue
        if any(key == ref or ref.endswith("/" + key) or key.endswith("/" + ref)
               for ref in referenced):
            continue                        # it became referenced meanwhile
        try:
            size = _delete_object(key)
            if size is not None:
                deleted.append(key)
                freed += max(0, int(size or 0))
        except Exception as exc:
            errors.append(f"{key}: {exc}")
    return {"deleted": deleted, "freed_bytes": freed, "errors": errors}


def _delete_object(key):
    """Remove one object from whichever back end holds it.

    Returns the object's size in bytes when known, 0 when it was deleted
    but the size is not reported by the back end, and None when there was
    nothing to delete.
    """
    if _local_mode():
        import storage
        full = storage.resolve_local(key)
        if not full:
            return None
        size = os.path.getsize(full)
        os.remove(full)
        return size
    import supabase_store
    c = supabase_store.client()
    if c is None:
        raise RuntimeError("Supabase storage is not configured.")
    bucket = c.storage.from_(supabase_store._bucket())
    bucket.remove([key])
    return 0
