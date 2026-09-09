"""Supabase-backed site settings."""
from supabase_store import client, enabled

DEFAULT_SETTINGS = {
    "bank_name": "", "account_number": "", "account_name": "",
    "referral_commission_percentage": 0,
    "hero_banner_title": "", "hero_banner_subtitle": "",
    "contact_email": "", "contact_phone": "", "site_logo_url": "",
    # legacy front-end keys, persisted in the same id=1 row
    "hero_video_url": "", "hero_poster_url": "", "hero_doc_url": "",
    "shop_banner_url": "", "shipping_note": "", "banner_from": "",
    "banner_to": "", "conv_banner": "", "conv_bold": "",
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

def update_site_settings(values):
    allowed = set(DEFAULT_SETTINGS)
    clean = {k: v for k, v in values.items() if k in allowed}
    if "referral_commission_percentage" in clean:
        clean["referral_commission_percentage"] = max(0, min(100, float(clean["referral_commission_percentage"])))
    if not enabled() or client() is None:
        raise RuntimeError("Supabase is required for site settings")
    result = client().table("site_settings").update(clean).eq("id", 1).execute()
    rows = getattr(result, "data", None) or []
    return {**DEFAULT_SETTINGS, **(rows[0] if rows else get_site_settings())}
