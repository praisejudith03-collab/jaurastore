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

# What the Broadcast Hub offers the owner, and what each choice is stored as.
# The stored discriminator is constrained in the database
# (``marketing_campaign_type_check``), so a new button in the composer must map
# onto one of the four allowed values rather than invent a fifth - and a
# promotion or a coupon announcement IS one of them: a price/discount alert and
# a customer-appreciation send. Keeping the map here (not in the UI) means the
# portal, the API and the SQL constraint can never disagree.
BROADCAST_KINDS = {
    "new_arrivals": ("New arrivals", CampaignType.NEW_ARRIVALS.value),
    "promo": ("Promotion / price drop", CampaignType.PRICE_DROP.value),
    "coupon": ("Discount coupon", CampaignType.CUSTOMER_APPRECIATION.value),
    "appreciation": ("Customer appreciation", CampaignType.CUSTOMER_APPRECIATION.value),
}


def broadcast_kind_from(value):
    """Validate a Broadcast Hub kind, or return ``None``."""
    raw = str(value or "").strip().lower()
    return raw if raw in BROADCAST_KINDS else None


def campaign_type_for_broadcast(kind):
    """The stored ``campaign_type`` for a broadcast kind, or ``None``.

    Every value returned here is in ``CAMPAIGN_TYPES``; ``test_broadcast_hub``
    asserts that, because a value outside the set would be rejected by the
    database CHECK on the Supabase mirror while the local row silently saved.
    """
    entry = BROADCAST_KINDS.get(str(kind or "").strip().lower())
    return entry[1] if entry else None


def broadcast_kind_options():
    """Serialisable choices for the composer."""
    return [{"kind": kind, "label": label, "campaignType": stored}
            for kind, (label, stored) in BROADCAST_KINDS.items()]


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
