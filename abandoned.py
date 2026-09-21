"""Abandoned-cart reminder lifecycle.

A reminder is only created after a shopper has entered an email on the
checkout form. The cart is eligible exactly once, after twenty minutes with no
activity. The SQLite row is the local working copy and is mirrored to
Supabase when configured; order completion marks it converted before a
reminder can be sent.
"""
import datetime
import json

from db import execute, one, query

REMINDER_AFTER_MINUTES = 20


def _now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat()


def _cutoff():
    return (datetime.datetime.utcnow()
            - datetime.timedelta(minutes=REMINDER_AFTER_MINUTES)).replace(microsecond=0).isoformat()


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


PAGE_SIZE = 25          # rows held in memory at once
MAX_PER_TICK = 200      # hard ceiling on one worker tick


def due_carts(limit=25, offset=0):
    """Return ONE bounded page of due carts, supplementing from Supabase.

    A bounded page prevents a provider outage/backlog from occupying the
    background worker indefinitely AND keeps the worker's resident memory
    flat: at most `limit` cart rows exist at any moment, never the whole
    table. Remaining carts are picked up by the next page/tick.
    """
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 25
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    rows = [dict(r) for r in query(
        "SELECT * FROM abandoned_carts "
        "WHERE reminder_sent=0 AND converted_at IS NULL AND last_activity_at <= ? "
        "ORDER BY last_activity_at ASC LIMIT ? OFFSET ?",
        (_cutoff(), limit, offset))]
    try:
        from supabase_store import load_due_abandoned_carts
        remote_rows = load_due_abandoned_carts(_cutoff(), limit=limit,
                                               offset=offset) or []
        for remote in remote_rows:
            if len(rows) >= limit:
                break
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


def mark_converted_for_email(email):
    """Close every still-open cart belonging to a buyer who just ordered.

    The cart token lives in the browser's localStorage, so a checkout
    completed on a different tab or device - or one whose token was cleared -
    arrives without it. Matching on the email address stops that buyer being
    chased for a cart they have already paid for.
    """
    email = str(email or "").strip().lower()
    if not email:
        return 0
    now = _now()
    rows = query(
        "SELECT token FROM abandoned_carts "
        "WHERE lower(email)=? AND converted_at IS NULL",
        (email,),
    )
    for row in rows:
        mark_converted(row["token"])
    return len(rows)


def iter_due_carts(limit=MAX_PER_TICK, page_size=PAGE_SIZE):
    """Yield due carts one page at a time, never holding more than a page.

    This is the memory contract for the `jaura-abandoned-carts` worker: the
    generator loads `page_size` rows, hands them out, drops them, and only
    then reads the next page - so a backlog of thousands of carts costs the
    same resident memory as a backlog of ten.
    """
    try:
        limit = max(1, min(int(limit), 5000))
    except (TypeError, ValueError):
        limit = MAX_PER_TICK
    try:
        page_size = max(1, min(int(page_size), 100))
    except (TypeError, ValueError):
        page_size = PAGE_SIZE
    seen = set()
    yielded = 0
    offset = 0
    while yielded < limit:
        want = min(page_size, limit - yielded)
        try:
            page = due_carts(limit=want, offset=offset)
        except Exception as exc:
            print(f"[abandoned] due-cart page {offset} skipped: {exc}")
            return
        if not page:
            return
        advanced = False
        for row in page:
            token = str((row or {}).get("token") or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            advanced = True
            yielded += 1
            yield row
            if yielded >= limit:
                return
        # Claimed/sent rows leave the due window, so the next page starts at
        # 0 again; only a page that produced nothing new needs a real offset
        # bump, which also guarantees this loop always terminates.
        if not advanced:
            offset += want
        del page


def _send_one(row):
    """Send one reminder. Returns (sent, failed) and NEVER raises.

    A single bad row - malformed JSON, a provider 4xx, a dead socket - must
    never abort the batch or kill the worker thread, so every step here is
    individually guarded and logged.
    """
    import mailer

    token = str((row or {}).get("token") or "").strip()
    if not token or row.get("reminder_sent") or row.get("converted_at"):
        return 0, 0
    try:
        # Claim locally before dispatch so two scheduler ticks/workers cannot
        # send the same cart. A failed provider call releases the claim.
        claimed = execute(
            "UPDATE abandoned_carts SET reminder_sent=1, updated_at=? "
            "WHERE token=? AND reminder_sent=0 AND converted_at IS NULL",
            (_now(), token)).rowcount
    except Exception as exc:
        print(f"[abandoned] claim failed for {token}: {exc}")
        return 0, 1
    if not claimed:
        return 0, 0
    try:
        ok, detail = mailer.send_abandoned_cart_reminder(row)
    except Exception as exc:                      # provider/library blew up
        ok, detail = False, f"unhandled mailer error: {exc}"
    if ok:
        now = _now()
        try:
            execute("UPDATE abandoned_carts SET reminder_sent_at=?, updated_at=? "
                    "WHERE token=?", (now, now, token))
        except Exception as exc:
            print(f"[abandoned] sent-stamp failed for {token}: {exc}")
        try:
            from supabase_store import mark_abandoned_reminder_sent
            mark_abandoned_reminder_sent(token, now)
        except Exception as exc:
            print(f"[abandoned] Supabase reminder mark skipped: {exc}")
        return 1, 0
    try:
        execute("UPDATE abandoned_carts SET reminder_sent=0, updated_at=? "
                "WHERE token=?", (_now(), token))
    except Exception as exc:
        print(f"[abandoned] claim release failed for {token}: {exc}")
    print(f"[abandoned] reminder failed for {token}: {detail}")
    return 0, 1


def send_due_reminders(limit=MAX_PER_TICK, page_size=PAGE_SIZE):
    """Send reminders page by page; mark sent only after acceptance.

    Memory stays flat (one page of rows) and a failing individual email is
    counted and logged rather than stopping the queue.
    """
    sent = 0
    failed = 0
    for row in iter_due_carts(limit=limit, page_size=page_size):
        try:
            ok_count, fail_count = _send_one(row)
        except Exception as exc:                  # belt and braces
            print(f"[abandoned] reminder crashed: {exc}")
            ok_count, fail_count = 0, 1
        sent += ok_count
        failed += fail_count
    return {"sent": sent, "failed": failed}
