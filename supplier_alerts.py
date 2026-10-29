"""Supplier low/out-of-stock email alerts (owner directive, this session).

The supplier watchdog is **alert-only**: it observes supplier pages and
records a diagnostic ``supplier_status``, but it never changes the shop's
own inventory (``stock`` / ``stock_quantity`` / ``optionStock`` / online).
This module is the missing notification half of that rule:

* every confident supplier reading becomes an observation
  ``{product_id, option, supplier_qty, flagged, ...}``;
* an observation is classified ``ok`` / ``low`` / ``out``
  (out = 0, low = 1..SUPPLIER_LOW_STOCK_THRESHOLD, default 9 — the same
  band the watchdog already uses for the admin status badge);
* entering ``low``/``out`` — or escalating low -> out — queues **one**
  pending alert per product/variant in durable Supabase state
  (``growth_settings.supplier_stock_alert_state_json``), so a supplier
  that stays empty for a week emails once, not every 20 minutes;
* recovering to ``ok`` clears the entry (after any queued email has gone
  out), so the *next* drop alerts again;
* :func:`drain_pending_alerts` sends one batched email for everything
  pending to the shop inbox (``ADMIN_EMAIL``, default
  jaurastore@gmail.com) and only marks entries emailed after the mail
  provider accepts the message. It runs from the supplier watchdog CLI
  (GitHub Actions, when mail secrets are present there) and from the
  Render scheduler's five-minute tick — whichever gets there first;
  a short-lived claim stamp stops the two racing each other.

Nothing in this module ever writes a product row. It reads/writes only
its own alert-state JSON and sends mail through ``mailer``. Every public
function is fail-soft: an alert problem must never break a watchdog run
or a scheduler tick.
"""
from __future__ import annotations

import copy
import datetime
import os
import secrets

STATE_VERSION = 1
MAX_ENTRIES = 1000            # tracked low/out product-variants
MAX_EMAIL_ROWS = 100          # one email never carries more lines than this
CLAIM_TTL_SECONDS = 15 * 60   # a crashed drainer frees its claim after this

_LEVELS = ("ok", "low", "out")
_SEVERITY = {"ok": 0, "low": 1, "out": 2}


def enabled():
    raw = str(os.environ.get("SUPPLIER_STOCK_ALERTS_ENABLED", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def low_stock_threshold():
    """Supplier units at or below this (and above zero) count as LOW."""
    try:
        value = int(float(os.environ.get("SUPPLIER_LOW_STOCK_THRESHOLD", "9") or 9))
    except (TypeError, ValueError):
        value = 9
    return max(0, min(10 ** 6, value))


def level_for(qty, flagged=False):
    """Classify one supplier reading: ``"ok"`` / ``"low"`` / ``"out"`` / None.

    A bare availability flag (the supplier page only says "In stock") is
    ``ok`` — the synthetic quantity behind it is not a count, so it can
    never raise a low-stock alert. ``None`` (no confident reading) maps
    to None: silence, never an alert.
    """
    if flagged:
        return "ok"
    if qty is None:
        return None
    try:
        units = int(float(qty))
    except (TypeError, ValueError):
        return None
    if units <= 0:
        return "out"
    if units <= low_stock_threshold():
        return "low"
    return "ok"


def supplier_status_for(readings):
    """The admin ``supplier_status`` badge for a set of readings.

    ``readings`` is an iterable of ``(option, qty, flagged)``. All
    readings out -> ``out_of_stock``; any low/out among healthy ones ->
    ``low_stock``; otherwise ``ok``. This is the one derivation the
    watchdog uses for both the badge and the email alert, so the two can
    never disagree about what "low" means.
    """
    levels = [level_for(qty, flagged) for _option, qty, flagged in (readings or [])]
    levels = [level for level in levels if level]
    if not levels:
        return "ok"
    if all(level == "out" for level in levels):
        return "out_of_stock"
    if any(level in ("low", "out") for level in levels):
        return "low_stock"
    return "ok"


# --------------------------------------------------------------------- state

def _now(now=None):
    moment = now or datetime.datetime.utcnow()
    return moment.isoformat(timespec="seconds") + "Z"


def _parse_ts(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _empty_state():
    return {"version": STATE_VERSION, "entries": {}}


def _normalise_state(raw):
    if not isinstance(raw, dict):
        return _empty_state()
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        return _empty_state()
    clean = {}
    for key, entry in entries.items():
        if isinstance(entry, dict) and str(entry.get("level") or "") in ("low", "out"):
            clean[str(key)] = dict(entry)
    return {"version": STATE_VERSION, "entries": clean}


def _default_load():
    try:
        import supabase_store
        return supabase_store.load_supplier_stock_alert_state()
    except Exception:
        return None


def _default_save(state):
    try:
        import supabase_store
        return bool(supabase_store.save_supplier_stock_alert_state(state))
    except Exception:
        return False


def _entry_key(product_id, option):
    return f"{str(product_id or '').strip()}::{str(option or '').strip()}"


def apply_observations(state, observations, now=None):
    """Fold observations into alert state (pure; returns a new state).

    Transition rules per product-variant:
      * ok reading  -> entry cleared, unless its alert email is still
        pending (the owner should still hear that it *was* low/out);
      * low/out     -> entry created/updated; ``pending`` (email owed)
        only when the entry is new or the level escalated since the
        last emailed level. Staying low, or easing out -> low, never
        re-emails.
    """
    out = _normalise_state(copy.deepcopy(state) if isinstance(state, dict) else state)
    entries = out["entries"]
    stamp = _now(now)
    for obs in observations or []:
        if not isinstance(obs, dict):
            continue
        pid = str(obs.get("product_id") or "").strip()
        if not pid:
            continue
        level = level_for(obs.get("supplier_qty"), bool(obs.get("flagged")))
        if level is None:
            continue
        key = _entry_key(pid, obs.get("option"))
        previous = entries.get(key)
        if level == "ok":
            if previous and previous.get("pending"):
                previous["recoveredAt"] = stamp
                previous["lastAt"] = stamp
            else:
                entries.pop(key, None)
            continue
        try:
            supplier_qty = int(float(obs.get("supplier_qty")))
        except (TypeError, ValueError):
            supplier_qty = 0 if level == "out" else None
        emailed_level = str((previous or {}).get("emailedLevel") or "")
        pending = bool((previous or {}).get("pending")) or (
            _SEVERITY[level] > _SEVERITY.get(emailed_level, 0))
        entries[key] = {
            "product_id": pid,
            "product_name": str(obs.get("product_name") or (previous or {}).get("product_name") or "")[:240],
            "sku": str(obs.get("sku") or (previous or {}).get("sku") or "")[:80],
            "option": str(obs.get("option") or "")[:160],
            "level": level,
            "supplier_qty": supplier_qty,
            "shop_stock": obs.get("shop_stock"),
            "firstAt": str((previous or {}).get("firstAt") or stamp),
            "lastAt": stamp,
            "pending": pending,
            "emailedAt": str((previous or {}).get("emailedAt") or ""),
            "emailedLevel": emailed_level,
            "claimedAt": str((previous or {}).get("claimedAt") or ""),
            "claimId": str((previous or {}).get("claimId") or ""),
        }
    if len(entries) > MAX_ENTRIES:
        keep = sorted(entries.items(),
                      key=lambda item: str(item[1].get("lastAt") or ""),
                      reverse=True)[:MAX_ENTRIES]
        out["entries"] = dict(keep)
    return out


def pending_alerts(state, now=None):
    """Entries that still owe the owner an email (claimable right now)."""
    moment = now or datetime.datetime.utcnow()
    out = []
    for key, entry in _normalise_state(state)["entries"].items():
        if not entry.get("pending"):
            continue
        claimed = _parse_ts(entry.get("claimedAt"))
        if claimed and (moment - claimed).total_seconds() < CLAIM_TTL_SECONDS:
            continue
        out.append(dict(entry, key=key))
    # Worst first, then the oldest problem, so the email leads with what
    # can no longer be bought from the supplier at all.
    out.sort(key=lambda e: (-_SEVERITY.get(str(e.get("level") or ""), 0),
                            str(e.get("firstAt") or ""),
                            str(e.get("product_name") or "")))
    return out


def record_observations(observations, load=None, save=None, now=None):
    """Persist observations into durable alert state. Never raises.

    Returns ``{"recorded": n, "pending": n}``, or a ``skipped`` reason
    when the durable state is unreadable — an unreadable ledger must
    look like "not now", never like "empty" (that would re-email every
    known problem once the read recovers).
    """
    if not enabled():
        return {"recorded": 0, "pending": 0, "skipped": "disabled"}
    obs = [o for o in (observations or []) if isinstance(o, dict)]
    if not obs:
        return {"recorded": 0, "pending": 0}
    load = load or _default_load
    save = save or _default_save
    try:
        state = load()
        if state is None:
            return {"recorded": 0, "pending": 0, "skipped": "state-unavailable"}
        updated = apply_observations(state, obs, now=now)
        if updated == _normalise_state(state):
            return {"recorded": len(obs), "pending": len(pending_alerts(updated, now=now))}
        if not save(updated):
            return {"recorded": 0, "pending": 0, "skipped": "state-save-failed"}
        return {"recorded": len(obs), "pending": len(pending_alerts(updated, now=now))}
    except Exception as exc:                          # pragma: no cover - defensive
        return {"recorded": 0, "pending": 0, "skipped": f"{type(exc).__name__}: {exc}"[:200]}


def drain_pending_alerts(send=None, load=None, save=None, now=None):
    """Email every pending alert in one batch. Never raises.

    Claims the batch in durable state before sending, so the GitHub
    Actions watchdog and the Render scheduler cannot both mail the same
    transition. Entries are marked emailed only after the provider
    accepts the message; on any failure the claim is released and the
    alerts stay pending for the next drain.
    """
    if not enabled():
        return {"sent": False, "count": 0, "pending": 0, "detail": "disabled"}
    load = load or _default_load
    save = save or _default_save
    moment = now or datetime.datetime.utcnow()
    try:
        state = load()
        if state is None:
            return {"sent": False, "count": 0, "pending": 0, "detail": "state-unavailable"}
        state = _normalise_state(state)
        alerts = pending_alerts(state, now=moment)
        if not alerts:
            return {"sent": False, "count": 0, "pending": 0, "detail": "nothing pending"}
        claim_id = secrets.token_hex(6)
        stamp = _now(moment)
        for alert in alerts:
            entry = state["entries"].get(alert["key"])
            if entry is not None:
                entry["claimedAt"] = stamp
                entry["claimId"] = claim_id
        if not save(state):
            return {"sent": False, "count": 0, "pending": len(alerts),
                    "detail": "state-save-failed"}

        if send is None:
            import mailer
            send = mailer.send_supplier_stock_alert
        ok, detail = send(alerts[:MAX_EMAIL_ROWS])

        fresh = load()
        fresh = _normalise_state(fresh) if fresh is not None else state
        for alert in alerts:
            entry = fresh["entries"].get(alert["key"])
            if entry is None or entry.get("claimId") != claim_id:
                continue
            entry["claimedAt"] = ""
            entry["claimId"] = ""
            if ok:
                entry["pending"] = False
                entry["emailedAt"] = _now(moment)
                entry["emailedLevel"] = str(entry.get("level") or "")
        save(fresh)
        remaining = len(pending_alerts(fresh, now=moment))
        return {"sent": bool(ok), "count": len(alerts) if ok else 0,
                "pending": remaining, "detail": str(detail or "")[:300]}
    except Exception as exc:                          # pragma: no cover - defensive
        return {"sent": False, "count": 0, "pending": 0,
                "detail": f"{type(exc).__name__}: {exc}"[:300]}
