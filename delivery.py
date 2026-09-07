"""Delivery zones and fare ranges - the server is the authority.

Before this module the fare list was hardcoded in checkout.html as <option>
text, and `POST /api/orders` accepted any free-text zone. That meant the
customer-facing fare and the operator's idea of the fare could drift apart,
and the server could not say what a given zone actually costs.

Rules this module enforces:

* The fare is a RANGE, never a single price. Transport varies with weight and
  the exact figure is agreed with the customer after payment, so publishing a
  fixed number would be a lie. `kind` distinguishes the three cases:
    delivery - a published min..max range in the zone's currency
    pickup   - free collection, no fare at all
    quote    - no published range, the fare is agreed per order
* A zone belongs to exactly one currency. Serving a CFA range to a naira
  checkout (or vice versa) is a pricing bug, so `fare_for` returns a currency
  mismatch rather than silently converting.
* Supabase is the source of truth when it is configured; SQLite mirrors it so
  the app still starts locally and so tests are deterministic.
"""
import re

from db import execute, init_db, query

VALID_KINDS = ("delivery", "pickup", "quote")
VALID_CURRENCIES = ("CFA", "NGN")


def _clean_name(name, limit=80):
    """Normalise a zone name the same way on the way in and the way out."""
    name = str(name or "").strip()
    name = re.sub(r"\s+", " ", name)
    return name[:limit]


def _get(row, key, default=None):
    """Read a column from either a sqlite3.Row or a Supabase dict.

    sqlite3.Row has no .get(), so a plain row.get(...) blows up on the local
    path while working fine against Supabase - exactly the kind of bug that
    only shows up in production or only shows up in tests.
    """
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _row_to_zone(row):
    """One delivery_zones row -> the dict the API and storefront consume."""
    kind = str(_get(row, "kind") or "delivery")
    if kind not in VALID_KINDS:
        kind = "delivery"
    currency = str(_get(row, "currency") or "CFA").upper()
    if currency not in VALID_CURRENCIES:
        currency = "CFA"
    fare_min = int(_get(row, "fare_min") or 0)
    fare_max = int(_get(row, "fare_max") or 0)
    if fare_max < fare_min:
        # A reversed range would let a customer be quoted min > max.
        fare_min, fare_max = fare_max, fare_min
    active = _get(row, "active")
    return {
        "id": str(_get(row, "id") or ""),
        "name": _clean_name(_get(row, "name")),
        "currency": currency,
        "fare_min": fare_min,
        "fare_max": fare_max,
        "kind": kind,
        "active": True if active is None else bool(active),
        "sort_order": int(_get(row, "sort_order") or 0),
        "note": str(_get(row, "note") or ""),
    }


# Zone cache. Reading the table on every checkout was both slow and unsafe:
# init_db() runs executescript(), and sqlite3's executescript() implicitly
# COMMITS any transaction in flight - so calling it from inside checkout would
# commit a half-finished order. The list is small and only changes when an
# admin edits it, so it is cached and explicitly invalidated on every write.
_CACHE = {"zones": None}


def _invalidate():
    _CACHE["zones"] = None


def _supabase_enabled():
    try:
        from supabase_store import client, enabled
        return bool(enabled()) and client() is not None
    except Exception:
        return False


def _load_all():
    """Every zone including inactive ones, in display order."""
    if _CACHE["zones"] is not None:
        return _CACHE["zones"]
    if _supabase_enabled():
        try:
            from supabase_store import client
            result = client().table("delivery_zones").select("*").execute()
            rows = getattr(result, "data", None)
            if rows is not None:
                out = [_row_to_zone(r) for r in rows]
                out.sort(key=lambda z: (z["sort_order"], z["name"]))
                _CACHE["zones"] = out
                return out
        except Exception:
            pass  # fall through to the local mirror
    try:
        rows = query("SELECT * FROM delivery_zones")
    except Exception:
        # Table not created yet (a database older than this feature).
        init_db()
        rows = query("SELECT * FROM delivery_zones")
    out = [_row_to_zone(r) for r in rows]
    out.sort(key=lambda z: (z["sort_order"], z["name"]))
    _CACHE["zones"] = out
    return out


def zones(include_inactive=False):
    """Every zone, in display order. Supabase first, SQLite as the mirror."""
    out = _load_all()
    if not include_inactive:
        out = [z for z in out if z["active"]]
    return out


def zone_for(value):
    """Resolve a zone by id or by name, case-insensitively.

    Checkout submits the zone NAME (that is what the <select> has always
    carried), while the Admin Portal edits by id. Both must resolve, and an
    unknown zone must return None rather than guess.
    """
    needle = _clean_name(value)
    if not needle:
        return None
    low = needle.lower()
    for z in zones(include_inactive=True):
        if z["id"].lower() == low or z["name"].lower() == low:
            return z
    return None


def fare_for(zone_value, currency):
    """The authoritative fare for a zone + currency.

    Returns (ok, payload). `payload` always carries the resolved zone when it
    was found, so the caller can store it on the order even when the fare
    cannot be published.
    """
    currency = str(currency or "").upper()
    zone = zone_for(zone_value)
    if zone is None:
        return False, {"error": "Unknown delivery zone. Choose one from the list."}
    if not zone["active"]:
        return False, {"error": "That delivery zone is not available right now."}
    payload = {
        "zone_id": zone["id"],
        "zone_name": zone["name"],
        "zone_currency": zone["currency"],
        "zone_kind": zone["kind"],
        "delivery_fee_min": zone["fare_min"],
        "delivery_fee_max": zone["fare_max"],
        "delivery_fee_currency": zone["currency"],
        "delivery_fee_confirmed": False,
    }
    if zone["kind"] == "pickup":
        payload["delivery_fee_min"] = 0
        payload["delivery_fee_max"] = 0
        payload["delivery_fee_confirmed"] = True
        payload["fare_status"] = "pickup"
        return True, payload
    if zone["kind"] == "quote":
        payload["delivery_fee_min"] = 0
        payload["delivery_fee_max"] = 0
        payload["fare_status"] = "quote"
        # A quote zone is still a valid choice: the fare is agreed afterwards.
        return True, payload
    if currency and currency != zone["currency"]:
        # NOT a hard rejection. Currency and zone are chosen independently at
        # checkout, so blocking here would turn away a naira customer ordering
        # to Cotonou - a sale the shop has always taken. The fare is agreed
        # with the customer after payment anyway, so the right move is to
        # publish no range, flag the mismatch for the operator, and let the
        # existing Benin/Togo minimum-order rule still apply.
        payload["delivery_fee_min"] = 0
        payload["delivery_fee_max"] = 0
        payload["fare_status"] = "currency_mismatch"
        payload["fare_note"] = (f"{zone['name']} is normally quoted in "
                                f"{zone['currency']}; this checkout is in "
                                f"{currency}. Confirm the fare with the customer.")
        return True, payload
    payload["fare_status"] = "range"
    return True, payload


def validate_zone_payload(payload):
    """Admin-side validation before a zone is written. Returns (clean, error)."""
    name = _clean_name(payload.get("name"))
    if not name:
        return None, "A zone needs a name."
    currency = str(payload.get("currency") or "CFA").upper()
    if currency not in VALID_CURRENCIES:
        return None, f"currency must be one of {', '.join(VALID_CURRENCIES)}"
    kind = str(payload.get("kind") or "delivery").lower()
    if kind not in VALID_KINDS:
        return None, f"kind must be one of {', '.join(VALID_KINDS)}"
    try:
        fare_min = int(payload.get("fare_min") or 0)
        fare_max = int(payload.get("fare_max") or 0)
    except (TypeError, ValueError):
        return None, "fare_min and fare_max must be whole numbers"
    if fare_min < 0 or fare_max < 0:
        return None, "fares cannot be negative"
    if kind == "delivery" and fare_max <= 0:
        return None, "A delivery zone needs a maximum fare above zero."
    if fare_max < fare_min:
        fare_min, fare_max = fare_max, fare_min
    clean = {
        "name": name, "currency": currency, "kind": kind,
        "fare_min": fare_min, "fare_max": fare_max,
        "active": bool(payload.get("active", True)),
        "sort_order": int(payload.get("sort_order") or 0),
        "note": _clean_name(payload.get("note"), 400),
    }
    return clean, None


def save_zone(zone_id, payload):
    """Insert or update one zone. Idempotent on the id."""
    clean, error = validate_zone_payload(payload)
    if error:
        return None, error
    clean["id"] = str(zone_id or "").strip().lower()
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", clean["id"]):
        return None, "id must be a lowercase slug (letters, digits, hyphens)"
    existing = query("SELECT id FROM delivery_zones WHERE id=?", (clean["id"],))
    cols = ("name", "currency", "fare_min", "fare_max", "kind", "active",
            "sort_order", "note")
    if existing:
        execute(
            "UPDATE delivery_zones SET name=?, currency=?, fare_min=?, "
            "fare_max=?, kind=?, active=?, sort_order=?, note=? WHERE id=?",
            tuple(clean[c] for c in cols) + (clean["id"],))
    else:
        execute(
            "INSERT INTO delivery_zones (id, name, currency, fare_min, "
            "fare_max, kind, active, sort_order, note) VALUES (?,?,?,?,?,?,?,?,?)",
            (clean["id"],) + tuple(clean[c] for c in cols))
    _invalidate()
    if _supabase_enabled():
        try:
            from supabase_store import client
            row = dict(clean)
            row["updated_at"] = None  # let the database default fill it
            row.pop("updated_at", None)
            client().table("delivery_zones").upsert(row).execute()
        except Exception as exc:
            return None, f"saved locally but Supabase rejected it: {exc}"
    return zone_for(clean["id"]), None


def delete_zone(zone_id):
    """Remove a zone. Never touches stored orders - they keep their snapshot."""
    zone_id = str(zone_id or "").strip().lower()
    rows = query("SELECT id FROM delivery_zones WHERE id=?", (zone_id,))
    if not rows:
        return False, "No such zone."
    execute("DELETE FROM delivery_zones WHERE id=?", (zone_id,))
    _invalidate()
    if _supabase_enabled():
        try:
            from supabase_store import client
            client().table("delivery_zones").delete().eq("id", zone_id).execute()
        except Exception as exc:
            return False, f"deleted locally but Supabase rejected it: {exc}"
    return True, None
