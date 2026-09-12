"""Supabase-backed site settings."""
import re

from supabase_store import client, enabled

DEFAULT_SETTINGS = {
    "bank_name": "", "account_number": "", "account_name": "",
    "referral_commission_percentage": 0,
    "hero_banner_title": "", "hero_banner_subtitle": "",
    "contact_email": "", "contact_phone": "", "site_logo_url": "",
    # legacy front-end keys, persisted in the same id=1 row
    "hero_video_url": "", "hero_poster_url": "", "hero_doc_url": "",
    "shop_banner_url": "", "shipping_note": "", "banner_from": "",
    "banner_to": "", "conv_banner": "", "conv_banner_fr": "", "conv_bold": "",
    # Checkout payment details. Served by GET /api/site and edited from the
    # Admin Portal. The identity fields (provider/holder/account) carry the
    # owner's real destinations as the floor: an admin-saved value always
    # wins, but a blank row can never leave the checkout bank sheet empty.
    "cfa_payment_provider": "MTN MoMo Benin", "cfa_payment_name": "OKORAFOR GIFT",
    "cfa_payment_account": "01 52 01 99 30", "cfa_payment_instructions": "",
    "togo_payment_provider": "Moov Money Togo", "togo_payment_name": "OKORAFOR GOODNESS",
    "togo_payment_account": "+229 01 68 95 31 10", "togo_payment_instructions": "",
    "naira_payment_bank": "UBA", "naira_payment_name": "OKORAFOR PRAISE",
    "naira_payment_account": "23474678931", "naira_payment_instructions": "",
    # Dual-country WhatsApp lines (digits only). Admin-editable; the env
    # variables WHATSAPP_NUMBER_NG / WHATSAPP_NUMBER_BJ supply the default
    # when the row carries nothing (see api._site_payload).
    "whatsapp_number_ng": "", "whatsapp_number_bj": "",
}

# The payment identity floor: the same 9 values as DEFAULT_SETTINGS. When a
# live site_settings row has a blank (or missing) payment identity column,
# get_site_settings() serves this value AND writes it back to the row, so
# "Our bank details" can never go empty again - not after a wiped row, not
# after a stale-form save, not on a fresh deploy. An admin-saved value always
# beats the floor; only blanks heal. The *_instructions columns are free text
# the owner may legitimately leave empty, so they are NOT part of the floor.
PAYMENT_FALLBACKS = {
    "cfa_payment_provider": "MTN MoMo Benin",
    "cfa_payment_name": "OKORAFOR GIFT",
    "cfa_payment_account": "01 52 01 99 30",
    "togo_payment_provider": "Moov Money Togo",
    "togo_payment_name": "OKORAFOR GOODNESS",
    "togo_payment_account": "+229 01 68 95 31 10",
    "naira_payment_bank": "UBA",
    "naira_payment_name": "OKORAFOR PRAISE",
    "naira_payment_account": "23474678931",
}

# The heal writes each payment column back at most once per worker: repeated
# rewrites on every read would be wasted Supabase calls for no benefit (every
# read already serves the healed value from the merge below).
_HEALED_COLUMNS = set()


def get_site_settings():
    if not enabled() or client() is None:
        raise RuntimeError("Supabase is required for site settings")
    result = client().table("site_settings").select("*").eq("id", 1).limit(1).execute()
    rows = getattr(result, "data", None) or []
    row = rows[0] if rows else {}
    # Every payment identity column blank in the ROW is healed: served from
    # the floor and (once per worker) written back into Supabase.
    healed = {col: floor for col, floor in PAYMENT_FALLBACKS.items()
              if not _stored_text(row.get(col))}
    merged = {**DEFAULT_SETTINGS, **row}
    for col, floor in PAYMENT_FALLBACKS.items():
        if not _stored_text(merged.get(col)):
            merged[col] = floor
    fresh = {col: value for col, value in healed.items()
             if col not in _HEALED_COLUMNS}
    if fresh:
        # One write attempt per worker per column, whatever happens: a failed
        # write only prints (reads still serve the healed value), and a
        # successful one never repeats until the next deploy.
        _HEALED_COLUMNS.update(fresh)
        try:
            client().table("site_settings").update(fresh).eq("id", 1).execute()
        except Exception as exc:
            print(f"[supabase] site_settings payment heal not persisted: {exc}")
    return merged


# --------------------------------------------------------------------------
# The live site_settings table predates newer columns (conv_banner_fr was
# added for the French moving banner), and PostgREST answers a plain
# .update() carrying an unknown column with PGRST204 - which killed EVERY
# banner save until the column landed. The update therefore repairs its own
# payload against the LIVE table, the same way delivery.save_zone and
# supabase_store._upsert_products_resilient do for their legacy tables:
#   23502 "null value in column X"  -> fill X from the default (or a value
#                                       a previous repair proved works)
#   PGRST204/42703 unknown column X -> drop X (a dropped column is logged
#                                       with the one ALTER statement that
#                                       adds it, so the owner can run it)
#   22P02 wrong type                -> walk X through 0 -> False -> ""
# The discovered shape is cached per worker so the next save lands on its
# first attempt. Nothing else in the row is ever dropped: the other settings
# still save even while a legacy table lacks the newest column.
# --------------------------------------------------------------------------

_SITE_SHAPE = {"fill": [], "drop": [], "values": {}}

# The columns whose silent loss is unacceptable: the account a customer is
# told to pay into. The tolerant writer below drops a column the live table
# lacks so that the OTHER settings still save - which is right for a banner,
# and wrong here: a save that says "saved" while the bank details never
# reached the table is how an Admin edit "disappears again". These columns
# fail the save loudly, with the one ALTER statement that repairs the table.
CRITICAL_SETTINGS = (
    "bank_name", "account_number", "account_name",
    "naira_payment_bank", "naira_payment_name", "naira_payment_account",
    "naira_payment_instructions",
    "cfa_payment_provider", "cfa_payment_name", "cfa_payment_account",
    "cfa_payment_instructions",
    "togo_payment_provider", "togo_payment_name", "togo_payment_account",
    "togo_payment_instructions",
)
_NULL_VALUE_RE = re.compile(r'null value in column "([^"]+)"')
_MISSING_COLUMN_RE = re.compile(r"Could not find the '([^']+)' column")
_PG_MISSING_COLUMN_RE = re.compile(r'column "([^"]+)" of relation')
_TYPED_FILL_CHAIN = (0, False, "")


def _site_repair_statement(column):
    return f"alter table site_settings add column if not exists {column} text not null default ''"


def _remember_site_shape(kind, column):
    if column and column not in _SITE_SHAPE[kind]:
        _SITE_SHAPE[kind].append(column)


def _update_site_settings_resilient(c, clean):
    """Update the id=1 row, repairing the payload against the live table.

    Returns the PostgREST result of the update that finally stuck. Raises
    RuntimeError naming the offending column and the repair statement when
    the table cannot be satisfied - update_site_settings turns that into an
    error and touches nothing else.
    """
    pending = {k: v for k, v in (clean or {}).items()
               if k not in _SITE_SHAPE["drop"]}
    if not pending:
        return None
    for col in _SITE_SHAPE["fill"]:
        pending.setdefault(col, _SITE_SHAPE["values"].get(
            col, DEFAULT_SETTINGS.get(col, "")))

    width0 = max(len(pending), 1)
    last_column = None
    last_typed = None
    typed_step = {}
    for _attempt in range(2 * width0 + 8):
        try:
            result = c.table("site_settings").update(pending).eq("id", 1).execute()
            if last_typed:
                _SITE_SHAPE["values"][last_typed] = pending[last_typed]
            return result
        except Exception as exc:
            text = str(exc)
            m = _NULL_VALUE_RE.search(text)
            if m and "23502" in text:
                col = m.group(1)
                value = _SITE_SHAPE["values"].get(
                    col, DEFAULT_SETTINGS.get(col, ""))
                if pending.get(col) == value:
                    raise RuntimeError(
                        f'site_settings still rejects the update at column '
                        f'"{col}" ({str(exc)[:160]}). One statement repairs '
                        f'the live table: '
                        f"alter table site_settings alter column {col} "
                        f"drop not null") from exc
                pending[col] = value
                _remember_site_shape("fill", col)
                last_column = col
                continue
            m = _MISSING_COLUMN_RE.search(text)
            if m is None and "42703" in text:
                m = _PG_MISSING_COLUMN_RE.search(text)
            if m:
                col = m.group(1)
                if col not in pending:
                    raise RuntimeError(
                        f'site_settings still rejects the update at column '
                        f'"{col}" ({str(exc)[:160]}). One statement repairs '
                        f'the live table: {_site_repair_statement(col)}'
                    ) from exc
                if col in CRITICAL_SETTINGS:
                    # Dropping this one would answer "saved" while the value
                    # never reached the table.
                    raise RuntimeError(
                        f'"{col}" is missing from the live site_settings '
                        f"table, so it would be dropped instead of stored. "
                        f"One statement repairs the table: "
                        f"{_site_repair_statement(col)}"
                    ) from exc
                pending.pop(col, None)
                _remember_site_shape("drop", col)
                last_column = col
                print(f"[supabase] site_settings update: table lacks column "
                      f"{col!r}; dropped from this save. Run once in the "
                      f"Supabase SQL editor to store it: "
                      f"{_site_repair_statement(col)}")
                continue
            if ("22P02" in text or "invalid input syntax" in text) and last_column:
                col = last_column
                step = typed_step.get(col, -1) + 1
                if step >= len(_TYPED_FILL_CHAIN):
                    raise RuntimeError(
                        f'site_settings still rejects the update at column '
                        f'"{col}" ({str(exc)[:160]}).') from exc
                pending[col] = _TYPED_FILL_CHAIN[step]
                typed_step[col] = step
                last_typed = col
                continue
            raise
    raise RuntimeError(
        "site_settings still rejects the update (too many columns to repair)")


def _stored_text(value):
    """The value as the text the storefront will print ("" for None)."""
    if value is None:
        return ""
    return str(value).strip()


def _same_value(stored, intended):
    """Loose equality for numbers ("7.5" vs "7.50") and text."""
    a, b = _stored_text(stored), _stored_text(intended)
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


def _insert_site_row(c, clean):
    """Create the id=1 row when it does not exist.

    An UPDATE against a missing row matches nothing and PostgREST answers
    success with no rows - so every Admin save looked like it worked while
    storing nothing, and the owner saw the settings they had just typed
    reappear empty. Creating the row is what makes the save stick.
    """
    payload = {"id": 1, **DEFAULT_SETTINGS, **clean}
    try:
        result = c.table("site_settings").insert(payload).execute()
    except Exception as exc:
        print(f"[supabase] site_settings row insert failed: {exc}")
        return []
    print("[supabase] site_settings: created the missing id=1 row")
    return list(getattr(result, "data", None) or [])


def update_site_settings(values):
    allowed = set(DEFAULT_SETTINGS)
    clean = {k: v for k, v in values.items() if k in allowed}
    if "referral_commission_percentage" in clean:
        clean["referral_commission_percentage"] = max(0, min(100, float(clean["referral_commission_percentage"])))
    if not enabled() or client() is None:
        raise RuntimeError("Supabase is required for site settings")
    c = client()
    result = _update_site_settings_resilient(c, clean)
    rows = list(getattr(result, "data", None) or []) if result is not None else []
    if not rows:
        # The update matched nothing: either the row is missing or PostgREST
        # answered without a representation. Create it, then read the truth.
        rows = _insert_site_row(c, clean)
    stored = {**DEFAULT_SETTINGS, **(rows[0] if rows else {})}
    if not rows:
        stored = get_site_settings()
    # Verify-after-write: a column that was dropped (or a row that was never
    # written) must not be reported as saved. Payment details either persist
    # or the Admin is told exactly why they did not.
    lost = [k for k, v in clean.items()
            if _stored_text(v) and not _same_value(stored.get(k), v)]
    if lost:
        critical = sorted(k for k in lost if k in CRITICAL_SETTINGS)
        if critical:
            raise RuntimeError(
                "the live site_settings table did not store "
                + ", ".join(f'"{k}"' for k in critical)
                + ". One statement per column repairs it: "
                + "; ".join(_site_repair_statement(k) for k in critical))
        print("[supabase] site_settings: not stored (non-critical): "
              + ", ".join(sorted(lost)))
    return stored
