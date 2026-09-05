"""Backfill row-shaping must match the live mirrors exactly.

backfill_supabase_orders.py is the only production-touching code; it never
runs in CI (no network), so this offline check pins its row shapes instead:
an orders row must carry EXACTLY the keys api.py's checkout mirror sends,
and a receipts row must carry EXACTLY what supabase_store.create_receipt
receives - so a backfilled row is indistinguishable from a live-mirrored one.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_supabase_orders as bf  # noqa: E402

NOW = "2026-09-05T00:00:00Z"

ORDER = {"id": "JA-BF1", "email": "e@x.com", "customer_name": "N",
         "phone": "p", "country": "BJ", "city": "c", "zone": "z",
         "address": "a", "note": "", "payment": "momo", "proof_url": "",
         "items_count": 2, "total": 5000, "currency": "CFA",
         "source": "web", "status": "pending", "payload": '{"a": 1}',
         "at": "t", "updated_at": "t"}

RECEIPT = {"order_id": "JA-BF1", "name": "N", "phone": "p",
           "email": "e@x.com", "method": "momo", "items": "i",
           "quantity": "1", "amount": "5000", "note": "",
           "file_url": "/uploads/proofs/x.jpg", "file_name": "x.jpg",
           "file_size": 10, "mime": "image/jpeg", "emailed": 1,
           "email_info": "ok"}


def test_backfill_rows_match_the_live_mirrors():
    """One offline test: both row shapers reproduce the live mirrors exactly."""
    row = bf.order_row(dict(ORDER), NOW)
    mirror = {"id", "email", "customer_name", "phone", "country", "city",
              "zone", "address", "note", "payment", "proof_url",
              "items_count", "total", "currency", "source", "status",
              "payload", "at", "updated_at"}
    assert set(row) == mirror
    assert isinstance(row["payload"], str)
    assert json.loads(row["payload"]) == {"a": 1}      # one encoding, not two
    # a dict payload (not pre-dumped by SQLite) is also single-encoded
    assert json.loads(
        bf.order_row(dict(ORDER, payload={"a": 1}), NOW)["payload"]) == {"a": 1}
    assert row["updated_at"] == NOW

    rec = bf.receipt_row(dict(RECEIPT), NOW)
    assert rec["id"] == "JA-BF1" == rec["order_id"]
    receives = {"id", "order_id", "name", "phone", "email", "method", "items",
                "quantity", "amount", "note", "file_url", "file_name",
                "file_size", "file_type", "emailed", "email_info"}
    assert set(rec) == receives | {"created_at"}
    assert rec["file_type"] == "image/jpeg" and rec["emailed"] is True
