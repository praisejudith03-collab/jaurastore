"""Abandoned-cart reminder lifecycle.

A reminder is only created after a shopper has entered an email on the
checkout form. The cart is eligible exactly once, after two hours with no
activity. The SQLite row is the local working copy and is mirrored to
Supabase when configured; order completion marks it converted before a
reminder can be sent.
"""
import datetime
import json

from db import execute, one, query

REMINDER_AFTER_HOURS = 2


def _now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat()


def _cutoff():
    return (datetime.datetime.utcnow()
            - datetime.timedelta(hours=REMINDER_AFTER_HOURS)).replace(microsecond=0).isoformat()


def _row_from_supabase(row):
    """Normalize a Supabase row to the SQLite/reminder shape."""
    if not isinstance(row, dict):
        return None
    items = row.get("items") or []
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (TypeError, ValueError):
            items = []
    return {
        "token": str(row.get("token") or ""),
        "email": str(row.get("email") or "").strip().lower(),
        "customer_name": str(row.get("customer_name") or ""),
        "items": json.dumps(items if isinstance(items, list) else [], ensure_ascii=False),
        "currency": str(row.get("currency") or ""),
        "total": row.get("total") or 0,
        "last_activity_at": str(row.get("last_activity_at") or ""),
        "reminder_sent": int(bool(row.get("reminder_sent"))),
        "reminder_sent_at": row.get("reminder_sent_at"),
        "converted_at": row.get("converted_at"),
        "created_at": row.get("created_at") or _now(),
        "updated_at": row.get("updated_at") or _now(),
    }


def due_carts():
    """Return local due carts, supplementing from Supabase after a restart."""
    rows = [dict(r) for r in query(
        "SELECT * FROM abandoned_carts "
        "WHERE reminder_sent=0 AND converted_at IS NULL AND last_activity_at <= ? "
        "ORDER BY last_activity_at ASC LIMIT 500", (_cutoff(),))]
    try:
        from supabase_store import load_due_abandoned_carts
        for remote in load_due_abandoned_carts(_cutoff()) or []:
            row = _row_from_supabase(remote)
            if not row or not row["token"]:
                continue
            if not any(str(local.get("token")) == row["token"] for local in rows):
                rows.append(row)
                execute(
                    "INSERT INTO abandoned_carts "
                    "(token,email,customer_name,items,currency,total,last_activity_at,"
                    "reminder_sent,reminder_sent_at,converted_at,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(token) DO UPDATE SET email=excluded.email,"
                    "customer_name=excluded.customer_name,items=excluded.items,"
                    "currency=excluded.currency,total=excluded.total,"
                    "last_activity_at=excluded.last_activity_at,"
                    "reminder_sent=excluded.reminder_sent,reminder_sent_at=excluded.reminder_sent_at,"
                    "converted_at=excluded.converted_at,updated_at=excluded.updated_at",
                    (row["token"], row["email"], row["customer_name"], row["items"],
                     row["currency"], row["total"], row["last_activity_at"],
                     row["reminder_sent"], row["reminder_sent_at"], row["converted_at"],
                     row["created_at"], row["updated_at"]),
                )
    except Exception as exc:
        print(f"[abandoned] Supabase due-cart load skipped: {exc}")
    return rows


def mark_converted(token):
    """Stop a completed checkout from receiving a reminder."""
    token = str(token or "").strip()
    if not token:
        return False
    now = _now()
    execute("UPDATE abandoned_carts SET converted_at=?, updated_at=? WHERE token=?",
            (now, now, token))
    try:
        from supabase_store import mark_abandoned_converted
        mark_abandoned_converted(token, now)
    except Exception as exc:
        print(f"[abandoned] Supabase conversion mark skipped: {exc}")
    return True


def send_due_reminders():
    """Send due reminders and set reminder_sent only after Resend accepts."""
    import mailer

    sent = 0
    failed = 0
    for row in due_carts():
        token = str(row.get("token") or "").strip()
        if not token or row.get("reminder_sent") or row.get("converted_at"):
            continue
        # Claim locally before dispatch so two scheduler ticks/workers cannot
        # send the same cart. A failed provider call releases the claim.
        claimed = execute(
            "UPDATE abandoned_carts SET reminder_sent=1, updated_at=? "
            "WHERE token=? AND reminder_sent=0 AND converted_at IS NULL",
            (_now(), token)).rowcount
        if not claimed:
            continue
        ok, detail = mailer.send_abandoned_cart_reminder(row)
        if ok:
            sent += 1
            now = _now()
            execute("UPDATE abandoned_carts SET reminder_sent_at=?, updated_at=? WHERE token=?",
                    (now, now, token))
            try:
                from supabase_store import mark_abandoned_reminder_sent
                mark_abandoned_reminder_sent(token, now)
            except Exception as exc:
                print(f"[abandoned] Supabase reminder mark skipped: {exc}")
        else:
            failed += 1
            execute("UPDATE abandoned_carts SET reminder_sent=0, updated_at=? WHERE token=?",
                    (_now(), token))
            print(f"[abandoned] reminder failed for {token}: {detail}")
    return {"sent": sent, "failed": failed}
