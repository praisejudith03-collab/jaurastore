"""Shop-side inventory alerts — at the VARIANT level, not just the product.

The gap this closes
-------------------
The Supplier Alerts pipeline watches the *supplier's* page. It deliberately
skips anything the shop has already run down to zero
(``catalog.supplier_alert_sellable`` treats "your own stock is 0" as
*handled*), and nothing else watched the shop's own numbers. So the last unit
of "Black" on the big channel bag could sell, ``optionStock["Black"]`` could
fall to 0, the storefront could grey that colour out — and the owner was
never told. The product total was still positive (other colours remained),
so anything that looked only at the parent product saw nothing wrong at all.

What this module does
---------------------
* :func:`scan` reads every live product and expands it into ONE observation
  per sellable unit: each ``optionStock`` key (the colour / size grid) plus,
  for a product without a grid, the product itself;
* a quantity is classified ``out`` (0), ``low`` (1..``INVENTORY_LOW_STOCK_
  THRESHOLD``, default 5 — the same band the admin low-stock screen uses) or
  ``ok``;
* a transition into ``out`` (or into ``low``) queues exactly ONE pending
  alert per product+variant. Staying out, or easing out -> low, is not news
  and never re-emails; recovering to ``ok`` closes the episode so the NEXT
  drop alerts again;
* the pending alerts are emailed to the owner inbox (``ADMIN_EMAIL``,
  jaurastore@gmail.com) in one batched message, and are only marked emailed
  after the provider accepts it;
* it runs from three places, so "immediately" is true on every path:
  - the task queue, woken by :func:`notify_after_stock_change` the moment a
    checkout reservation (or an admin stock edit) writes the new quantity;
  - the scheduler's five-minute tick, which sweeps the whole catalogue — the
    net under a missed wake, a supplier sync that zeroed a colour, or a
    deploy that lost the in-process queue;
  - the admin's "Check now" action, via :func:`sweep`.

State
-----
The ledger lives in SQLite (``inventory_alerts``) so a variant that stays out
for a week is mailed once, and is mirrored best-effort into Supabase
(``growth_settings.inventory_alert_state_json``) so a redeploy on Render's
ephemeral disk does not email the same sold-out colour again.

Nothing here ever writes a product row or a stock number: this module is
alert-only, exactly like the supplier watchdog. Every public function is
fail-soft — a broken alert can never break a checkout or a scheduler tick.
"""
from __future__ import annotations

import datetime
import json
import os

MAX_EMAIL_ROWS = 100          # one email never carries more than this
MAX_TRACKED = 4000            # variants tracked per shop (bounded memory)

SEVERITY = {"ok": 0, "low": 1, "out": 2}
EMAIL_SOURCE = "inventory"


# --------------------------------------------------------------------- config
def enabled():
    raw = str(os.environ.get("INVENTORY_STOCK_ALERTS_ENABLED", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def low_stock_threshold():
    """Units at or below this (and above zero) count as LOW."""
    raw = os.environ.get("INVENTORY_LOW_STOCK_THRESHOLD", "")
    if str(raw).strip() == "":
        try:
            from config import Config
            return max(0, int(getattr(Config, "LOW_STOCK_THRESHOLD", 5) or 5))
        except Exception:
            return 5
    try:
        return max(0, min(10 ** 6, int(float(raw))))
    except (TypeError, ValueError):
        return 5


def level_for(qty, threshold=None):
    """``"out"`` / ``"low"`` / ``"ok"`` for one variant quantity."""
    try:
        units = max(0, int(float(qty)))
    except (TypeError, ValueError):
        units = 0
    if units <= 0:
        return "out"
    if units <= (low_stock_threshold() if threshold is None else int(threshold)):
        return "low"
    return "ok"


def _now(now=None):
    moment = now or datetime.datetime.utcnow()
    return moment.isoformat(timespec="seconds") + "Z"


def _key(product_id, option):
    return f"{str(product_id or '').strip()}::{str(option or '').strip()}"


# ------------------------------------------------------------ observations
def _variant_label(product, option):
    """The name to show the owner for one optionStock key."""
    if not option:
        return ""
    options = product.get("options")
    if isinstance(options, list):
        for group in options:
            if not isinstance(group, dict):
                continue
            values = group.get("values")
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, dict):
                        label = str(value.get("label") or value.get("value") or "").strip()
                        key = str(value.get("key") or value.get("value") or "").strip()
                        if option in (label, key):
                            return label or option
    return str(option)


def variant_quantities(product):
    """``[(option_key, qty)]`` for one row — the full variant grid.

    ``optionStock`` is the canonical per-variant quantity in this app (the
    checkout reserves against it, the storefront greys out from it). A row
    without a grid is a single whole-product observation. A row that is
    hidden, trashed, switched off by the owner, or already locked out
    manually is skipped: that is a decision the owner already made, not news.
    """
    import catalog as catalog_mod
    row = product if isinstance(product, dict) else {}
    if not row or not str(row.get("id") or "").strip():
        return []
    if not catalog_mod.is_live_product(row):
        return []
    if catalog_mod._manual_stock_lock(row) or catalog_mod.is_stock_locked(row.get("id")):
        return []
    option_stock = row.get("optionStock")
    out = []
    if isinstance(option_stock, dict) and option_stock:
        for key, value in option_stock.items():
            try:
                qty = max(0, int(str(value).strip() or 0))
            except (TypeError, ValueError):
                qty = 0
            out.append((str(key), qty))
        return out
    # No grid: the parent quantity IS the sellable unit.
    return [("", catalog_mod.stock_of(row))]


def observe_product(product):
    """Observations for one row (no I/O, no email)."""
    row = product if isinstance(product, dict) else {}
    pid = str(row.get("id") or "").strip()
    if not pid:
        return []
    import catalog as catalog_mod
    total = catalog_mod.stock_of(row)
    # A non-variant product is judged on the TOTAL; a variant grid is judged on
    # each variant, and the total is reported alongside for context.
    threshold = low_stock_threshold()
    observations = []
    for option, qty in variant_quantities(row):
        observations.append({
            "product_id": pid,
            "product_name": str(row.get("name") or row.get("title") or pid)[:240],
            "sku": str(row.get("sku") or row.get("supplierSku") or "")[:80],
            "option": option[:160],
            "option_label": _variant_label(row, option)[:160],
            "qty": qty,
            "product_total": total,
            "level": level_for(qty, threshold),
        })
    return observations


def scan(rows=None):
    """Every live product expanded into its variants. Never raises."""
    if rows is None:
        try:
            rows = _catalogue_rows()
        except Exception as exc:                    # pragma: no cover - defensive
            print(f"[inventory] catalogue read skipped: {exc}")
            return []
    observations = []
    for row in rows or []:
        try:
            observations.extend(observe_product(row))
        except Exception as exc:                    # one bad row never stops the scan
            print(f"[inventory] row skipped: {exc}")
    return observations


def _catalogue_rows():
    """One row per product, the same read the storefront and admin use."""
    import catalog as catalog_mod
    rows = None
    try:
        import supabase_store
        rows = supabase_store.products_table_rows()
    except Exception:
        rows = None
    if rows is None:
        rows = catalog_mod.merged(include_hidden=True)
    return [row for row in (rows or []) if isinstance(row, dict)]


# ---------------------------------------------------------------- the ledger
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS inventory_alerts ("
    "  key TEXT PRIMARY KEY, product_id TEXT NOT NULL, product_name TEXT,"
    "  sku TEXT, option TEXT, option_label TEXT, level TEXT NOT NULL,"
    "  qty INTEGER NOT NULL DEFAULT 0, product_total INTEGER NOT NULL DEFAULT 0,"
    "  first_at TEXT, changed_at TEXT, last_at TEXT,"
    "  pending INTEGER NOT NULL DEFAULT 0, claimed_at TEXT, claim_id TEXT,"
    "  emailed_at TEXT, emailed_level TEXT, seen_at TEXT, seen_level TEXT"
    ")"
)
TABLE_READY = False

_FIELDS = ("key", "product_id", "product_name", "sku", "option", "option_label",
           "level", "qty", "product_total", "first_at", "changed_at", "last_at",
           "pending", "claimed_at", "claim_id", "emailed_at", "emailed_level",
           "seen_at", "seen_level")


def _ensure_table():
    global TABLE_READY
    if TABLE_READY:
        return True
    try:
        from db import execute
        execute(SCHEMA)
        TABLE_READY = True
        return True
    except Exception as exc:                        # pragma: no cover - defensive
        print(f"[inventory] ledger table unavailable: {exc}")
        return False


def load_ledger():
    """``{key: entry}`` from SQLite, seeded from Supabase after a fresh boot.

    Returns None when the ledger cannot be read at all — callers treat that as
    "not now", never as "nothing is out of stock" (which would re-email every
    known sold-out variant as soon as reads recover).
    """
    if not _ensure_table():
        return None
    try:
        from db import query
        rows = query("SELECT * FROM inventory_alerts")
        ledger = {}
        for row in rows:
            entry = dict(row)
            entry["pending"] = bool(entry.get("pending"))
            ledger[str(entry.get("key"))] = entry
    except Exception as exc:
        print(f"[inventory] ledger read failed: {exc}")
        return None
    if not ledger:
        # A redeploy on Render wipes /tmp; the mirror is what stops the same
        # sold-out colour being emailed again after every deploy.
        try:
            import supabase_store
            remote = supabase_store.load_inventory_alert_state()
        except Exception:
            remote = None
        if isinstance(remote, dict) and remote.get("entries"):
            ledger = _save_entries(remote["entries"])
    return ledger


def _save_entries(entries):
    """Upsert a whole ledger into SQLite and return it. Best effort."""
    if not isinstance(entries, dict) or not entries:
        return {}
    if not _ensure_table():
        return {}
    from db import execute
    saved = {}
    for key, entry in list(entries.items())[:MAX_TRACKED]:
        if not isinstance(entry, dict):
            continue
        record = {field: entry.get(field) for field in _FIELDS}
        record["key"] = str(key)
        record["pending"] = 1 if entry.get("pending") else 0
        try:
            execute(
                "INSERT OR REPLACE INTO inventory_alerts (" + ",".join(_FIELDS) + ") "
                "VALUES (" + ",".join("?" for _ in _FIELDS) + ")",
                tuple(record.get(field) for field in _FIELDS))
        except Exception as exc:                    # pragma: no cover - defensive
            print(f"[inventory] ledger write skipped: {exc}")
            continue
        saved[key] = dict(entry, key=key)
    return saved


def _mirror(ledger):
    """Best-effort durable copy (Supabase). Never raises, never blocks a sale."""
    try:
        import supabase_store
        supabase_store.save_inventory_alert_state(
            {"version": 1, "entries": ledger,
             "updatedAt": _now()})
    except Exception as exc:
        print(f"[inventory] ledger mirror skipped: {exc}")


def _persist(entry):
    if not _ensure_table():
        return False
    try:
        from db import execute
        record = {field: entry.get(field) for field in _FIELDS}
        record["pending"] = 1 if entry.get("pending") else 0
        execute("INSERT OR REPLACE INTO inventory_alerts (" + ",".join(_FIELDS) + ") "
                "VALUES (" + ",".join("?" for _ in _FIELDS) + ")",
                tuple(record.get(field) for field in _FIELDS))
        return True
    except Exception as exc:
        print(f"[inventory] ledger write skipped: {exc}")
        return False


def _remove(key):
    try:
        from db import execute
        execute("DELETE FROM inventory_alerts WHERE key=?", (key,))
    except Exception:                               # pragma: no cover - defensive
        pass


# --------------------------------------------------------------- transitions
def apply_observations(ledger, observations, now=None):
    """Fold observations into the ledger; return ``(ledger, changed_keys)``.

    Rules (per product + variant):
      * ``ok``      — close the episode (the row is deleted) so the next drop
                      is a fresh alert;
      * ``low``     — pending only when the owner has never been told about
                      this level for this episode;
      * ``out``     — pending unless ``out`` was already emailed for this
                      episode; low -> out escalates and IS emailed.
    """
    if ledger is None:
        return None, []
    stamp = _now(now)
    changed = []
    for obs in observations or []:
        if not isinstance(obs, dict):
            continue
        pid = str(obs.get("product_id") or "").strip()
        if not pid:
            continue
        level = str(obs.get("level") or "")
        if level not in SEVERITY:
            continue
        key = _key(pid, obs.get("option"))
        entry = dict(ledger.get(key) or {})
        if level == "ok":
            if entry:
                ledger.pop(key, None)
                _remove(key)
                changed.append(key)
            continue
        previous_level = str(entry.get("level") or "")
        emailed_level = str(entry.get("emailed_level") or "")
        improved = (previous_level in SEVERITY
                    and SEVERITY[level] < SEVERITY[previous_level])
        if improved:
            # A restock (or a rise above the threshold) is the state the owner
            # already knows about: silence now, and treat the NEXT drop as the
            # news. Without this, "out -> restocked to 4 -> sold out again"
            # stayed inside the same episode and never emailed the second
            # stockout - the exact miss this module exists to prevent.
            emailed_level = level
        pending = bool(entry.get("pending")) or (
            SEVERITY[level] > SEVERITY.get(emailed_level, 0))
        level_changed = previous_level != level
        was_seen = (entry.get("seen_at")
                    and SEVERITY[level] <= SEVERITY.get(str(entry.get("seen_level") or ""), -1))
        entry.update({
            "key": key,
            "product_id": pid,
            "product_name": str(obs.get("product_name") or entry.get("product_name") or "")[:240],
            "sku": str(obs.get("sku") or entry.get("sku") or "")[:80],
            "option": str(obs.get("option") or "")[:160],
            "option_label": str(obs.get("option_label") or entry.get("option_label") or "")[:160],
            "level": level,
            "qty": int(obs.get("qty") or 0),
            "product_total": int(obs.get("product_total") or 0),
            "first_at": str(entry.get("first_at") or stamp),
            "changed_at": stamp if level_changed else str(entry.get("changed_at") or stamp),
            "last_at": stamp,
            "pending": pending,
            "emailed_at": "" if improved else str(entry.get("emailed_at") or ""),
            "emailed_level": emailed_level,
            "seen_at": str(entry.get("seen_at") or "") if was_seen else "",
            "seen_level": level if was_seen else "",
        })
        ledger[key] = entry
        _persist(entry)
        if pending:
            changed.append(key)
    if len(ledger) > MAX_TRACKED:
        for key in sorted(ledger, key=lambda k: str(ledger[k].get("last_at") or ""))[:-MAX_TRACKED]:
            ledger.pop(key, None)
            _remove(key)
    if changed:
        _mirror(ledger)
    return ledger, changed


def pending_alerts(ledger):
    """Entries that still owe the owner an email, worst first."""
    if not ledger:
        return []
    out = [dict(entry, key=key) for key, entry in ledger.items() if entry.get("pending")]
    out.sort(key=lambda e: (-SEVERITY.get(str(e.get("level") or ""), 0),
                            str(e.get("first_at") or ""),
                            str(e.get("product_name") or "")))
    return out


# --------------------------------------------------------------- email drain
def drain(send=None, ledger=None):
    """Email every pending alert in one batch. Never raises.

    Entries are marked emailed only after the provider accepts the message, so
    a failed send is retried on the next pass. A variant that has since been
    restocked (or hidden) is closed without an email: the owner does not need
    yesterday's news.
    """
    if not enabled():
        return {"sent": False, "count": 0, "pending": 0, "detail": "disabled"}
    ledger = load_ledger() if ledger is None else ledger
    if ledger is None:
        return {"sent": False, "count": 0, "pending": 0, "detail": "ledger-unavailable"}
    alerts = pending_alerts(ledger)
    if not alerts:
        return {"sent": False, "count": 0, "pending": 0, "detail": "nothing pending"}
    if send is None:
        import mailer
        send = mailer.send_inventory_stock_alert
    batch = alerts[:MAX_EMAIL_ROWS]
    try:
        ok, detail = send(batch)
    except Exception as exc:                        # pragma: no cover - defensive
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    stamp = _now()
    if ok:
        for alert in batch:
            entry = ledger.get(alert["key"])
            if not entry:
                continue
            entry["pending"] = False
            entry["emailed_at"] = stamp
            entry["emailed_level"] = str(entry.get("level") or "")
            _persist(entry)
        _mirror(ledger)
    return {"sent": bool(ok), "count": len(batch) if ok else 0,
            "pending": len(pending_alerts(ledger)), "detail": str(detail or "")[:300]}


def sweep(send=None):
    """Full variant-level sweep: scan, record, email. Never raises."""
    if not enabled():
        return {"scanned": 0, "changed": 0, "sent": False, "detail": "disabled"}
    try:
        ledger = load_ledger()
        if ledger is None:
            return {"scanned": 0, "changed": 0, "sent": False,
                    "detail": "ledger-unavailable"}
        observations = scan()
        _, changed = apply_observations(ledger, observations)
        result = drain(send=send, ledger=ledger)
        result.update({"scanned": len(observations), "changed": len(changed)})
        return result
    except Exception as exc:                        # pragma: no cover - defensive
        print(f"[inventory] sweep skipped: {exc}")
        return {"scanned": 0, "changed": 0, "sent": False,
                "detail": f"{type(exc).__name__}: {exc}"[:300]}


def check_products(product_ids, send=None):
    """Re-read the named products and alert on a variant that just hit zero.

    This is the path a checkout takes (through the task queue): the variant
    that sold its last unit is read FRESH from the catalogue, so the alert
    email always quotes the quantity the shop is actually serving.
    """
    if not enabled():
        return {"checked": 0, "changed": 0, "sent": False, "detail": "disabled"}
    ids = {str(pid).strip() for pid in (product_ids or []) if str(pid or "").strip()}
    if not ids:
        return {"checked": 0, "changed": 0, "sent": False, "detail": "no product"}
    try:
        rows = [row for row in _catalogue_rows()
                if str((row or {}).get("id") or "").strip() in ids]
        observations = []
        for row in rows:
            try:
                observations.extend(observe_product(row))
            except Exception:
                continue
        ledger = load_ledger()
        if ledger is None:
            return {"checked": len(rows), "changed": 0, "sent": False,
                    "detail": "ledger-unavailable"}
        _, changed = apply_observations(ledger, observations)
        result = drain(send=send, ledger=ledger)
        result.update({"checked": len(rows), "changed": len(changed)})
        return result
    except Exception as exc:                        # pragma: no cover - defensive
        print(f"[inventory] check skipped: {exc}")
        return {"checked": 0, "changed": 0, "sent": False,
                "detail": f"{type(exc).__name__}: {exc}"[:300]}


# ------------------------------------------------------------- queue plumbing
def notify_after_stock_change(product_id=None, option_key=None):
    """Wake the alert worker after a stock write. Cheap, and never raises.

    Called from the reservation path (a sale) and from admin stock edits. The
    request only enqueues: the catalogue read and the email happen on the task
    queue, so a slow mail provider can never delay a checkout.
    """
    pid = str(product_id or "").strip()
    try:
        import task_queue
        task_queue.enqueue("inventory.alert.check",
                          {"id": pid, "option": str(option_key or "")},
                          dedupe=False)
        return True
    except Exception as exc:                        # pragma: no cover - defensive
        print(f"[inventory] alert wake skipped: {exc}")
        return False


def task_check(payload):
    """Task-queue handler for one stock change."""
    payload = payload if isinstance(payload, dict) else {}
    pid = str(payload.get("id") or payload.get("product") or "").strip()
    if not pid:
        return {"skipped": "no product"}
    return check_products([pid])


def register():
    """Bind the worker handler (idempotent)."""
    try:
        import task_queue
        task_queue.register("inventory.alert.check", task_check,
                            label="Check stock alerts")
        return True
    except Exception as exc:                        # pragma: no cover - defensive
        print(f"[inventory] task registration skipped: {exc}")
        return False


# ------------------------------------------------------------------ admin view
def status():
    """What the watchdog currently knows — used by the admin endpoint/card."""
    ledger = load_ledger()
    if ledger is None:
        return {"ready": False, "enabled": enabled(), "out": 0, "low": 0,
                "pending": 0, "variants": 0, "alerts": []}
    out = [e for e in ledger.values() if str(e.get("level") or "") == "out"]
    low = [e for e in ledger.values() if str(e.get("level") or "") == "low"]
    # Everything currently low/out is listed (worst first) - not only what is
    # still waiting for an email. The owner needs to see a colour that ran out
    # this morning even after the email about it has gone.
    ordered = sorted(ledger.values(),
                     key=lambda e: (-SEVERITY.get(str(e.get("level") or ""), 0),
                                    str(e.get("first_at") or ""),
                                    str(e.get("product_name") or "")))
    alerts = []
    for entry in ordered[:50]:
        alerts.append({
            "productId": entry.get("product_id"),
            "productName": entry.get("product_name"),
            "sku": entry.get("sku"),
            "option": entry.get("option"),
            "optionLabel": entry.get("option_label"),
            "level": entry.get("level"),
            "qty": int(entry.get("qty") or 0),
            "productTotal": int(entry.get("product_total") or 0),
            "firstAt": entry.get("first_at"),
            "pending": bool(entry.get("pending")),
        })
    return {"ready": True, "enabled": enabled(), "threshold": low_stock_threshold(),
            "out": len(out), "low": len(low), "variants": len(ledger),
            "pending": len(pending_alerts(ledger)), "alerts": alerts}


def mark_seen(product_id, option=""):
    """Acknowledge one variant alert (admin "seen")."""
    key = _key(product_id, option)
    ledger = load_ledger()
    if ledger is None or key not in ledger:
        return {"ok": False, "skipped": "unknown key"}
    entry = ledger[key]
    entry["seen_at"] = _now()
    entry["seen_level"] = str(entry.get("level") or "")
    _persist(entry)
    _mirror(ledger)
    return {"ok": True, "key": key}


register()
