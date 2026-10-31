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
* only ACTIVE, IN-STOCK items are alerted on. The watchdog reads the live
  listing before queueing anything, and a reading for an item the shop has
  hidden, trashed or set to zero is never queued (see
  ``catalog.supplier_alert_sellable``);
* :func:`drain_pending_alerts` sends one batched email for everything
  pending to the shop inbox (``ADMIN_EMAIL``, default
  jaurastore@gmail.com) and only marks entries emailed after the mail
  provider accepts the message. A pending entry whose item has since
  become inactive or sold out is closed without an email. It runs from the supplier watchdog CLI
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


def level_for(qty, flagged=False, shop_stock=None):
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
    if shop_stock is not None:
        try:
            return "low" if units < int(float(shop_stock)) else "ok"
        except (TypeError, ValueError, OverflowError):
            return None
    if units <= low_stock_threshold():
        return "low"
    return "ok"


def supplier_status_for(readings, shop_stock=None, option_stock=None):
    """The admin ``supplier_status`` badge for a set of readings.

    ``readings`` is an iterable of ``(option, qty, flagged)``. All
    readings out -> ``out_of_stock``; any low/out among healthy ones ->
    ``low_stock``; otherwise ``ok``. This is the one derivation the
    watchdog uses for both the badge and the email alert, so the two can
    never disagree about what "low" means.
    """
    levels = [level_for(qty, flagged, (option_stock or {}).get(option) if option else shop_stock)
              for option, qty, flagged in (readings or [])]
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
            item = dict(entry)
            if "seenAt" not in item:
                # Written before "Mark as seen" existed. These are already part
                # of the owner's history, so they are seen, never shown as new.
                item["seenAt"] = "legacy"
                item["seenLevel"] = str(item.get("level") or "")
            item.setdefault("seenLevel", "")
            clean[str(key)] = item
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
        level = level_for(obs.get("supplier_qty"), bool(obs.get("flagged")), obs.get("shop_stock"))
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
        # Seen stays seen while the level does not get worse than what the
        # owner acknowledged. An escalation (or a brand-new drop) is unseen.
        # Easing also lowers the level the owner has acknowledged, so a later
        # rise back to out (after out -> low) is a fresh drop and shows as new.
        seen_at, seen_level = "", ""
        if (previous and previous.get("seenAt")
                and _SEVERITY[level] <= _SEVERITY.get(str(previous.get("seenLevel") or ""), -1)):
            seen_at = str(previous.get("seenAt"))
            seen_level = level
        level_changed = previous is None or str(previous.get("level") or "") != level
        entries[key] = {
            "product_id": pid,
            "product_name": str(obs.get("product_name") or (previous or {}).get("product_name") or "")[:240],
            "sku": str(obs.get("sku") or (previous or {}).get("sku") or "")[:80],
            "option": str(obs.get("option") or "")[:160],
            "level": level,
            "supplier_qty": supplier_qty,
            "shop_stock": obs.get("shop_stock"),
            "firstAt": str((previous or {}).get("firstAt") or stamp),
            "changedAt": stamp if level_changed else str((previous or {}).get("changedAt") or stamp),
            "lastAt": stamp,
            "seenAt": seen_at,
            "seenLevel": seen_level,
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


def _catalogue_is_live():
    """A checker ``(product_id, option) -> bool`` over the live catalogue.

    Built once per drain. Raises when the catalogue cannot be read: the
    drain then sends nothing, because an unreadable catalogue must look
    like "not now", never like "every item is still live".
    """
    import catalog as catalog_mod
    by_id = {}
    for row in catalog_mod.supplier_alert_rows():
        pid = str(row.get("id") or "").strip()
        if pid:
            by_id[pid] = row

    def check(product_id, option=""):
        row = by_id.get(str(product_id or "").strip())
        return bool(catalog_mod.supplier_alert_sellable(row, option))
    return check


def drain_pending_alerts(send=None, load=None, save=None, now=None, is_live=None):
    """Email every pending alert in one batch. Never raises.

    Only alerts for items the shop is still selling (live on the website and
    in stock) are emailed. A pending alert whose item has since been hidden,
    trashed or set to zero is already handled: it is closed without an email,
    so the owner is not told about old news.

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

        check = is_live or _catalogue_is_live()
        live, handled = [], []
        for alert in alerts:
            if check(alert.get("product_id"), alert.get("option")):
                live.append(alert)
            else:
                handled.append(alert)
        if handled:
            stamp = _now(moment)
            for alert in handled:
                entry = state["entries"].get(alert["key"])
                if entry is None:
                    continue
                entry["pending"] = False
                entry["claimedAt"] = ""
                entry["claimId"] = ""
                # Remember the level so the same situation is never mailed
                # again; only a worse change (or a fresh drop) is news.
                entry["emailedLevel"] = str(entry.get("level") or "")
                entry["handledAt"] = stamp
            if not save(state):
                return {"sent": False, "count": 0, "pending": len(alerts),
                        "detail": "state-save-failed"}
        if not live:
            return {"sent": False, "count": 0,
                    "pending": len(pending_alerts(state, now=moment)),
                    "detail": f"{len(handled)} already handled on the website; no email sent"}
        alerts = live

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


# ------------------------------------------------------------- mark as seen

def read_ledger(load=None):
    """The alert ledger for reading, or None when it cannot be read.

    None is passed through rather than turned into an empty ledger, so a
    failed read shows items as new (never hides them as seen).
    """
    load = load or _default_load
    try:
        state = load()
    except Exception:
        return None
    return None if state is None else _normalise_state(state)


def alert_status(state, product, is_sellable):
    """``{"seen": bool, "since": str}`` for one flagged product.

    Only the product's live episodes count: an entry for a variant the owner
    has since zeroed, or for a hidden product, is handled and ignored. A
    product with no live entry on record has nothing new to show, so it is
    treated as seen. An unreadable ledger (``state is None``) is never seen.
    """
    if state is None:
        return {"seen": False, "since": ""}
    pid = str((product or {}).get("id") or "").strip()
    relevant = [entry for entry in state["entries"].values()
                if str(entry.get("product_id") or "") == pid
                and str(entry.get("level") or "") in ("low", "out")
                and is_sellable(product, str(entry.get("option") or ""))]
    if not relevant:
        return {"seen": True, "since": ""}
    seen = all(bool(entry.get("seenAt")) for entry in relevant)
    since = max(str(entry.get("changedAt") or entry.get("firstAt") or "")
                for entry in relevant)
    return {"seen": seen, "since": since}


def unseen_for(state, product_id, option=""):
    """True when one stock row (product + option) has an unseen low/out drop.

    An unreadable ledger (``state is None``) counts as unseen, so the
    dashboard never hides a drop it could not check.
    """
    if state is None:
        return True
    entry = state["entries"].get(_entry_key(product_id, option))
    if not entry or str(entry.get("level") or "") not in ("low", "out"):
        return False
    return not entry.get("seenAt")


def mark_seen(product_id, load=None, save=None, now=None):
    """Acknowledge every current low/out episode of one product. Never raises.

    Marking seen changes only the owner's view. Emails are unaffected, and a
    later, worse supplier change makes the product new again.
    """
    if not enabled():
        return {"marked": 0, "skipped": "disabled"}
    pid = str(product_id or "").strip()
    if not pid:
        return {"marked": 0, "skipped": "no-product"}
    load = load or _default_load
    save = save or _default_save
    try:
        state = load()
        if state is None:
            return {"marked": 0, "skipped": "state-unavailable"}
        state = _normalise_state(state)
        stamp = _now(now)
        marked = 0
        for entry in state["entries"].values():
            if str(entry.get("product_id") or "") == pid:
                entry["seenAt"] = stamp
                entry["seenLevel"] = str(entry.get("level") or "")
                marked += 1
        if marked and not save(state):
            return {"marked": 0, "skipped": "state-save-failed"}
        return {"marked": marked}
    except Exception as exc:                          # pragma: no cover - defensive
        return {"marked": 0, "skipped": f"{type(exc).__name__}: {exc}"[:200]}
