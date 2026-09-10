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
    # Admin Portal; the storefront must not carry a hardcoded fallback.
    "cfa_payment_provider": "", "cfa_payment_name": "",
    "cfa_payment_account": "", "cfa_payment_instructions": "",
    "togo_payment_provider": "", "togo_payment_name": "",
    "togo_payment_account": "", "togo_payment_instructions": "",
    "naira_payment_bank": "", "naira_payment_name": "",
    "naira_payment_account": "", "naira_payment_instructions": "",
}

def get_site_settings():
    if not enabled() or client() is None:
        raise RuntimeError("Supabase is required for site settings")
    result = client().table("site_settings").select("*").eq("id", 1).limit(1).execute()
    rows = getattr(result, "data", None) or []
    return {**DEFAULT_SETTINGS, **(rows[0] if rows else {})}


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


def update_site_settings(values):
    allowed = set(DEFAULT_SETTINGS)
    clean = {k: v for k, v in values.items() if k in allowed}
    if "referral_commission_percentage" in clean:
        clean["referral_commission_percentage"] = max(0, min(100, float(clean["referral_commission_percentage"])))
    if not enabled() or client() is None:
        raise RuntimeError("Supabase is required for site settings")
    result = _update_site_settings_resilient(client(), clean)
    rows = getattr(result, "data", None) or [] if result is not None else []
    return {**DEFAULT_SETTINGS, **(rows[0] if rows else get_site_settings())}
