"""Supabase-backed site settings and persistent admin reset tokens."""
from datetime import datetime, timezone
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
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

def _token_row(email, token, purpose="reset"):
    return {"email": email.lower().strip(), "token_hash": generate_password_hash(token),
            "purpose": purpose, "expires_at": datetime.now(timezone.utc).isoformat(),
            "consumed_at": None, "attempts": 0}

def create_reset_token(email, ttl_seconds=600):
    if not enabled() or client() is None: raise RuntimeError("Supabase is required")
    token = f"{secrets.randbelow(1000000):06d}"
    from datetime import timedelta
    row = _token_row(email, token)
    row["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    client().table("admin_reset_tokens").delete().eq("email", email.lower().strip()).eq("purpose", "reset").execute()
    client().table("admin_reset_tokens").insert(row).execute()
    return token

def reset_token_recent(email, cooldown=60):
    if not enabled() or client() is None: return False
    from datetime import timedelta
    r = client().table("admin_reset_tokens").select("created_at").eq("email", email.lower().strip()).eq("purpose", "reset").order("created_at", desc=True).limit(1).execute()
    rows = getattr(r, "data", None) or []
    if not rows or not rows[0].get("created_at"): return False
    created = datetime.fromisoformat(rows[0]["created_at"].replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - created < timedelta(seconds=cooldown)

def verify_reset_token(email, token, max_attempts=5):
    if not enabled() or client() is None: return False, "Supabase is unavailable."
    r = client().table("admin_reset_tokens").select("*").eq("email", email.lower().strip()).eq("purpose", "reset").is_("consumed_at", "null").order("created_at", desc=True).limit(1).execute()
    rows = getattr(r, "data", None) or []
    if not rows: return False, "No verification code pending. Request a new one."
    row = rows[0]
    if int(row.get("attempts") or 0) >= max_attempts: return False, "Too many attempts. Request a new code."
    if datetime.now(timezone.utc) > datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")):
        return False, "That code has expired. Request a new one."
    if not check_password_hash(row["token_hash"], str(token or "")):
        client().table("admin_reset_tokens").update({"attempts": int(row.get("attempts") or 0) + 1}).eq("id", row["id"]).execute()
        return False, "That code is not correct."
    client().table("admin_reset_tokens").update({"consumed_at": datetime.now(timezone.utc).isoformat()}).eq("id", row["id"]).execute()
    return True, ""
