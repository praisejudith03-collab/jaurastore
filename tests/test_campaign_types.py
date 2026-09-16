"""Campaign discriminator contract and scheduler isolation regressions."""
import sys
from types import SimpleNamespace

import pytest

from campaign_types import CAMPAIGN_TYPES, campaign_type_from, serialize_campaign


EXPECTED = {
    "abandoned_cart", "price_drop", "new_arrivals", "customer_appreciation",
}


def test_all_campaign_variants_are_registered_and_serialize_canonically():
    assert set(CAMPAIGN_TYPES) == EXPECTED
    for variant in EXPECTED:
        assert campaign_type_from({"campaign_type": variant}) == variant
        assert campaign_type_from({"type": variant}) == variant
        assert campaign_type_from({"campaignType": variant}) == variant
        assert serialize_campaign({"type": variant, "subject": "x"}) == {
            "campaign_type": variant, "subject": "x",
        }


def test_unknown_campaign_discriminator_is_rejected():
    assert campaign_type_from({"campaign_type": "old_variant"}) is None
    with pytest.raises(ValueError, match="discriminator"):
        serialize_campaign({"campaign_type": "old_variant"})


def test_campaign_schema_registers_every_variant():
    schema = open("supabase_schema.sql", encoding="utf-8").read().lower()
    campaign_schema = schema.split("create table if not exists marketing_campaigns", 1)[1]
    assert "constraint marketing_campaign_type_check" in campaign_schema
    assert "check (campaign_type in" in campaign_schema
    assert all(repr(variant) in campaign_schema for variant in EXPECTED)


def test_abandoned_worker_retries_without_leaking_an_exception(monkeypatch):
    import scheduler

    calls = []

    def send_due_reminders(limit):
        calls.append(limit)
        if len(calls) == 1:
            raise RuntimeError("temporary provider failure")
        return {"sent": 1, "failed": 0}

    monkeypatch.setitem(sys.modules, "abandoned", SimpleNamespace(
        send_due_reminders=send_due_reminders))
    monkeypatch.setattr(scheduler.time, "sleep", lambda _seconds: None)
    assert scheduler._abandoned_tick(attempts=3) == {"sent": 1, "failed": 0}
    assert calls == [25, 25]
