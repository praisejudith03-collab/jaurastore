"""Per-field merge for concurrent product edits.

Last-write-wins (see api._product_save_response) guarantees a save is never
refused, but it is ROW-level: the editor sends the whole product from the copy
in the tab's memory, so a price typed on a phone is reverted by a photo swap
made on a laptop a minute later.

This is the three-way merge that fixes it, without ever refusing a save:

    base    the row as it was when THIS editor was opened
    edited  the row as this admin just changed it
    stored  the row as it is in the database right now
    dirty   the fields the admin actually interacted with (optional)

For every mergeable field:

  * the admin changed it  -> the admin's value wins, always;
  * the admin did not touch it, and someone else did
                          -> the stored value is kept;
  * nobody changed it    -> either copy is identical.

WHY ``dirty`` IS NEEDED

Comparing ``edited`` against ``base`` alone cannot tell "the admin never
opened this field" from "the admin deliberately retyped the value it already
had". Both look identical - and "set the price back to 5,000" is an ordinary
thing for a shopkeeper to do. Guessing wrong there silently discards a
deliberate edit, which is far worse than the revert we are fixing. So the
editor reports which controls were actually touched and that is what decides.
When no ``dirty`` list is supplied (an older client, an API integration) the
base comparison is used as a best-effort fallback, and callers that want the
old behaviour simply send no base at all.

SCOPE

Only the fields in MERGEABLE_FIELDS are merged. Everything else - internal
bookkeeping, anything not understood well enough to merge safely - keeps plain
last-write-wins. A narrow allowlist is deliberate: a merge that guesses about
a field it does not understand is how shop data gets quietly corrupted.

This is a merge, never a guard. It decides which values win; it can never turn
a save into an error.
"""
import json

# Server-owned: the editor's copy is either stale by construction or
# recomputed on every save (the slug is re-derived against the catalogue).
SERVER_OWNED_FIELDS = frozenset((
    "id", "legacyId", "source", "slug",
    "updated_at", "updatedAt", "created_at", "createdAt",
    "deleted", "deletedIds",
))

# The fields this shop's admins actually contend over, merged one by one.
# A plain value is compared as a value; a dict is merged per key.
MERGEABLE_FIELDS = frozenset((
    # identity / copy
    "name", "nameFr", "description", "descriptionFr", "badge", "dimensions",
    # money
    "priceNgn", "compareNgn", "priceCfa", "compareCfa",
    # shelving
    "category", "online", "featured", "sku",
    # media
    "image", "image_url", "imageUrl", "images", "video", "video_url",
    # stock
    "stock", "stock_quantity", "stockStatus",
    # supplier
    "supplierSku", "supplierUrl", "supplier_url",
    # per-variant maps - merged per key, so one variant's quantity cannot
    # wipe another's
    "optionStock", "optionPrices", "optionCompareAt",
    "optionSku", "option_sku", "optionSupplierSku",
    "optionSupplierUrls", "option_supplier_urls",
))

_MISSING = object()


def _known(base, field):
    """True when the base copy actually carries a value for this field.

    This matters more than it looks. The base row the editor snapshots comes
    from the catalogue the admin tab loaded, and that payload is a PUBLIC
    projection: it deliberately omits fields like `stock`. A key that is
    absent is not "unchanged", it is UNKNOWN, and treating unknown as
    unchanged makes the merge overwrite the admin's own value with the stored
    one while announcing it "preserved a newer change" - the most confusing
    possible outcome.

    So: no evidence means no preservation. The admin's value stands.
    """
    return field in base


def _same(a, b):
    """Equal for merge purposes, ignoring how the value was written down.

    1 and 1.0 are the same price, and an absent key and a None mean the same
    thing here. Comparing raw values would report a conflict on every numeric
    field and quietly disable the merge.
    """
    if a is _MISSING or b is _MISSING:
        a = None if a is _MISSING else a
        b = None if b is _MISSING else b
    if a is None and b is None:
        return True
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        return _canon(a) == _canon(b)
    if a is None or b is None:
        # An empty string and None both mean "nothing set"; treating them as
        # different would make a cleared field look like a deliberate edit.
        return not a and not b
    return a == b


def _canon(value):
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _merge_map(base, edited, stored, kept, name):
    """Per-key merge of a dict-valued field (optionStock, optionPrices, ...).

    Only reached when the admin did NOT edit this field (a field they did edit
    keeps their whole map), so every key here is one they left alone: a key the
    row moved on since the editor opened is kept as it is in the database.
    """
    out = dict(edited or {})
    for key in list((base or {}).keys()) + list((stored or {}).keys()):
        if key in out:
            # Only preserve the stored value when the base actually knew this
            # key and the admin left it exactly as it was.
            if _known(base or {}, key) \
                    and _same((base or {}).get(key, _MISSING), out[key]) \
                    and not _same((stored or {}).get(key, _MISSING),
                                  (base or {}).get(key, _MISSING)):
                out[key] = (stored or {}).get(key)
                kept.append(f"{name}.{key}")
        else:
            # A variant the admin's copy does not know about: keep it, or a
            # save from an older tab would silently drop the variant.
            out[key] = (stored or {}).get(key)
    return out


def merge_product_edit(base, edited, stored, dirty=None):
    """Three-way merge of one product edit. Returns (merged, kept_fields).

    ``dirty`` is the set of field names the admin actually edited. Pass None
    to fall back to comparing ``edited`` against ``base``.

    ``kept_fields`` names the fields whose value came from the database
    because the admin had not touched them - the honest report of what this
    save did NOT change.
    """
    base = dict(base or {})
    edited = dict(edited or {})
    stored = dict(stored or {})
    dirty = None if dirty is None else set(dirty or ())
    kept = []
    merged = dict(edited)

    for field, value in edited.items():
        if field in SERVER_OWNED_FIELDS or field not in MERGEABLE_FIELDS:
            continue
        # A field the base copy does not mention is one we have no evidence
        # about, so it is left exactly as last-write-wins would leave it: the
        # admin's value. Guessing "unchanged" here would silently drop a real
        # edit whenever the base row is a narrower projection than the row
        # being saved - which it is for stock, among others.
        if not _known(base, field):
            continue
        base_value = base.get(field, _MISSING)
        stored_value = stored.get(field, _MISSING)
        # Did the admin change this field? The explicit list answers that
        # honestly; the base comparison is only a fallback.
        if dirty is not None:
            admin_changed = field in dirty
        else:
            admin_changed = not _same(base_value, value)
        if admin_changed:
            continue
        # Untouched by the admin. If the row moved on, keep the newer value.
        if _same(stored_value, base_value):
            continue
        if isinstance(value, dict) or isinstance(stored_value, dict):
            merged[field] = _merge_map(
                base_value if isinstance(base_value, dict) else {},
                value if isinstance(value, dict) else {},
                stored_value if isinstance(stored_value, dict) else {},
                kept, field)
        else:
            merged[field] = None if stored_value is _MISSING else stored_value
            kept.append(field)

    return merged, kept


def merge_is_worthwhile(base, edited, stored):
    """True when a three-way merge can be attempted at all.

    Guard rails, so a merge is never attempted when it could do damage: the
    caller must hand over a base copy of the SAME row, and the row must
    already exist (creating a product has nothing to merge with).
    """
    base = dict(base or {})
    edited_id = str((edited or {}).get("id") or "").strip()
    base_id = str(base.get("id") or "").strip()
    return bool(edited_id) and base_id == edited_id and bool(stored)
