"""One-shot boot seed: keep every shipped default category in the live table.

The production Supabase ``categories`` table holds the owner's rows, and a
table rewrite (a save from a client with a stale local copy) can drop a
shipped default that no product currently uses - which is exactly what
happened to ``perfume``: it is part of the shipped defaults
(js/store.js DEFAULT_CATEGORIES + data/categories.json) but no product in the
live catalogue carries the category, so it vanished from the shop pills, the
categories page and the header menu, and the owner cannot re-add it without
re-entering the French name and finding a cover photo.

``seed_missing_default_categories()`` runs once per deployment family (the
``category_default_seed_v1`` marker lives in Supabase growth_settings, the
same durable home the category-merge marker uses) and re-inserts any shipped
default that is missing from the table:

  * the category is appended AFTER the owner's own rows (order = max + 1),
    never re-ordered;
  * a copy the owner still has in the growth_settings details (nameFr, cover,
    order) is reused instead of the shipped default;
  * a category the owner HID (details with hidden=true) is never forced back;
  * the marker is only set once the write landed, so a failed write retries
    on the next boot, and once it has run it never runs again - if the owner
    later deletes the category on purpose, it stays deleted.

Run with:  python3 -m pytest tests/test_category_seed.py -q
"""
import datetime

MARKER_KEY = "category_default_seed_v1"

# The shipped defaults that must exist in the live table. `image` doubles as
# image_url: both keys are written so the row and the growth_settings details
# agree whatever shape the reader looks at (api._categories_data merges the
# details' image/image_url over the table row).
SHIPPED_DEFAULTS = (
    {
        "id": "perfume",
        "name": "Perfume",
        "nameFr": "Parfum",
        "image": "images/categories/beauty.jpg",
    },
)


def _log(message):
    print(message)


def _marker_get():
    """The seed marker from Supabase (durable), else local SQLite, else None."""
    try:
        from supabase_store import load_growth_settings, enabled
        if enabled():
            gs = load_growth_settings()
            if isinstance(gs, dict) and gs.get(MARKER_KEY):
                return gs[MARKER_KEY]
    except Exception:
        pass
    try:
        from db import one
        row = one("SELECT value FROM growth_settings WHERE key=?", (MARKER_KEY,))
        if row:
            return row["value"] if isinstance(row, dict) else (
                row[0] if isinstance(row, (tuple, list)) else row)
    except Exception:
        pass
    return None


def _marker_set(value):
    """Persist the marker to Supabase AND local SQLite (best effort)."""
    stamp = str(value or "")
    try:
        from supabase_store import mirror_growth_settings, enabled
        if enabled():
            mirror_growth_settings({MARKER_KEY: stamp})
    except Exception:
        pass
    try:
        from db import execute
        execute(
            "INSERT INTO growth_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (MARKER_KEY, stamp))
    except Exception:
        pass


def _next_order(rows, details):
    """One past the highest explicit order in the table/details (0 minimum)."""
    highest = -1
    for c in list(rows or []) + list(details or []):
        try:
            highest = max(highest, int((c or {}).get("order")))
        except (KeyError, TypeError, ValueError):
            continue
    return highest + 1


def seed_missing_default_categories(defaults=None):
    """Re-insert shipped default categories the live table lost.

    Returns one of:
      - ``("seeded", [ids])``        rows were written and the marker set
      - ``("nothing-to-do", [])``    every default is already live; marker set
      - ``("already-applied", [])``  the marker is set: never run again
      - ``("not-configured", [])``   no Supabase credentials (test/dev boot)
      - ``("unavailable", [])``      the table could not be read (retry next
                                     boot; the marker is NOT set)
      - ``("failed", [ids])``        a write failed (retry next boot; the
                                     marker is NOT set)
    """
    import supabase_store

    if not supabase_store.enabled():
        return "not-configured", []
    if _marker_get():
        return "already-applied", []

    rows = supabase_store.load_categories_table()
    if rows is None:
        return "unavailable", []
    details = supabase_store.load_categories() or []

    rows_by_id = {str((r or {}).get("id") or "").strip(): r for r in rows}
    details_by_id = {str((c or {}).get("id") or "").strip(): c
                     for c in details if isinstance(c, dict)}
    order = _next_order(rows, details)
    out_rows = [dict(r) for r in rows]
    out_details = [dict(c) for c in details]
    seeded = []

    for default in (defaults or SHIPPED_DEFAULTS):
        cid = str(default.get("id") or "").strip()
        if not cid:
            continue
        if cid in rows_by_id:
            continue                      # already live in the table
        detail = details_by_id.get(cid)
        if detail and detail.get("hidden"):
            continue                      # the owner hid it: never force it back
        # Reuse the owner's own copy where they still have one; the shipped
        # default only fills the gaps.
        source = dict(detail) if detail else {}
        name = str(source.get("name") or default["name"]).strip()
        name_fr = str(source.get("nameFr") or source.get("name_fr")
                      or default.get("nameFr") or "").strip()
        image = str(source.get("image_url") or source.get("image")
                    or default.get("image") or "").strip()
        out_rows.append({
            "id": cid,
            "name": name,
            "name_fr": name_fr,
            "image_url": image,
            "hidden": False,
            "updated_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        })
        out_details.append({
            "id": cid,
            "name": name,
            "nameFr": name_fr,
            "image": image,
            "image_url": image,
            "hidden": False,
            "order": order,
        })
        rows_by_id[cid] = out_rows[-1]
        order += 1
        seeded.append(cid)

    if not seeded:
        _marker_set(datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z")
        return "nothing-to-do", []

    # Full-set writes, exactly like an admin save (api._save_categories): the
    # table upsert prunes ids that are absent, so the complete row set goes,
    # and the growth_settings details keep nameFr / cover / order durable.
    if not supabase_store.save_categories_table(out_rows):
        return "failed", seeded
    if not supabase_store.save_categories(out_details):
        return "failed", seeded
    _marker_set(datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z")
    _log("[category-seed] restored %s to the live categories table"
         % ", ".join(seeded))
    return "seeded", seeded
