"""Canonical discriminator contract for marketing campaigns.

The API, SQLite audit log and Supabase mirror all use ``campaign_type`` as the
stored/serialized discriminator.  Request aliases are accepted at the HTTP
boundary for backwards compatibility, but are normalized before validation or
persistence so polymorphic consumers always receive one stable field.
"""
from enum import Enum


class CampaignType(str, Enum):
    ABANDONED_CART = "abandoned_cart"
    PRICE_DROP = "price_drop"
    NEW_ARRIVALS = "new_arrivals"
    CUSTOMER_APPRECIATION = "customer_appreciation"


CAMPAIGN_TYPES = tuple(item.value for item in CampaignType)


def campaign_type_from(value):
    """Return a validated canonical campaign type or ``None``.

    ``value`` may be a discriminator string or an API object.  ``campaign_type``
    is canonical; ``type`` and ``campaignType`` remain accepted input aliases.
    """
    if isinstance(value, dict):
        value = (value.get("campaign_type") or value.get("type") or
                 value.get("campaignType"))
    raw = str(value or "").strip().lower()
    try:
        return CampaignType(raw).value
    except ValueError:
        return None


def serialize_campaign(row):
    """Normalize and validate a campaign object for JSON/Supabase output."""
    data = dict(row or {})
    discriminator = campaign_type_from(data)
    if discriminator is None:
        raise ValueError("unknown campaign_type discriminator")
    data["campaign_type"] = discriminator
    # Input aliases must not leak into the persisted polymorphic object.
    data.pop("type", None)
    data.pop("campaignType", None)
    return data
