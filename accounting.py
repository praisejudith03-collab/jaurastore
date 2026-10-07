"""Dual-currency accounting helpers.

Accounting snapshots live inside the order payload in the existing orders
store.  That gives each confirmed order an idempotent, durable rate and cost
snapshot without introducing a second order identifier or a schema change.
Archived delivery batches are stored separately in growth_settings.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

LEGACY_RATE = Decimal("0.44")
RATE_MAX = Decimal("100")
AMOUNT_MAX = Decimal("1000000000000")


def normalize_currency(value):
    value = str(value or "").strip().upper()
    if value in ("CFA", "XOF", "FCFA"):
        return "CFA"
    if value in ("NGN", "NAIRA", "N"):
        return "NGN"
    return ""


def decimal_value(value, default=Decimal(0)):
    try:
        if isinstance(value, bool) or value is None or str(value).strip() == "":
            return default
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        return default


def amount(value, default=0):
    """Parse a non-negative whole-unit NGN/CFA value; invalid means default."""
    parsed = decimal_value(value, None)
    if parsed is None or not parsed.is_finite() or parsed < 0 or parsed > AMOUNT_MAX:
        return int(default)
    return int(parsed.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def validated_amount(value):
    """Return an integer amount, or None when an edit is invalid."""
    parsed = decimal_value(value, None)
    if parsed is None or not parsed.is_finite() or parsed < 0 or parsed > AMOUNT_MAX:
        return None
    return int(parsed.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def safe_rate(value, default=LEGACY_RATE):
    parsed = decimal_value(value, default)
    if not parsed.is_finite() or parsed <= 0 or parsed > RATE_MAX:
        parsed = default
    return parsed.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def current_exchange_rate():
    """Read the currently configured NGN -> CFA Admin Portal rate."""
    try:
        import growth
        return safe_rate(growth.settings().get("cfaRate"))
    except Exception:
        return LEGACY_RATE


def supplier_cost_cfa(supplier_cost_ngn, rate):
    """Convert NGN supplier spend into whole CFA using NGN * rate."""
    ngn = Decimal(amount(supplier_cost_ngn))
    return int((ngn * safe_rate(rate)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))


def new_snapshot(total, currency, rate, confirmed_at, *, legacy=False):
    """Create a once-only order accounting snapshot.

    New confirmations use the active admin rate. Pre-feature confirmed orders
    use the explicitly marked 0.44 baseline because their historical rate was
    never captured; subsequent rate changes cannot move that baseline.
    """
    currency = normalize_currency(currency) or "NGN"
    return {
        "version": 1,
        "currency": currency,
        "confirmedAt": str(confirmed_at or ""),
        "exchangeRate": float(safe_rate(rate)),
        "saleAmount": amount(total),
        # Supplier costs and transport are intentionally editable on the
        # accounting sheet; checkout never accepts cost data from a customer.
        "supplierCostNgn": 0,
        "deliveryExpense": 0,
        "notes": "",
        "snapshotSource": "legacy-estimate" if legacy else "confirmation",
    }


def order_payload(order):
    """Decode an order's payload without trusting its incoming type."""
    if isinstance(order, dict):
        raw = order.get("payload")
    else:
        try:
            raw = order["payload"]
        except (KeyError, TypeError, IndexError):
            raw = None
    if isinstance(raw, dict):
        payload = dict(raw)
    else:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            payload = {}
    return payload if isinstance(payload, dict) else {}


def order_value(order, key, default=None):
    if isinstance(order, dict):
        return order.get(key, default)
    try:
        return order[key]
    except (KeyError, TypeError, IndexError):
        return default


def account_block(order, *, current_rate=None):
    """Return a copy of a stored snapshot, or a stable legacy estimate."""
    payload = order_payload(order)
    stored = payload.get("accounting")
    if isinstance(stored, dict):
        result = dict(stored)
        result.setdefault("currency", normalize_currency(
            order_value(order, "currency") or payload.get("currency")) or "NGN")
        result.setdefault("confirmedAt", str(
            order_value(order, "updated_at") or order_value(order, "at") or ""))
        result.setdefault("exchangeRate", float(LEGACY_RATE))
        result.setdefault("saleAmount", amount(
            order_value(order, "total", payload.get("total", 0))))
        result.setdefault("supplierCostNgn", 0)
        result.setdefault("deliveryExpense", 0)
        result.setdefault("notes", "")
        result.setdefault("snapshotSource", "legacy-estimate")
        return result

    # Before accounting shipped, no rate was recorded on an order. Use a
    # fixed, clearly-labelled baseline instead of re-reading today's setting
    # on every view (which would make old profit totals drift over time).
    currency = normalize_currency(
        order_value(order, "currency") or payload.get("currency")) or "NGN"
    total = order_value(order, "total", payload.get("total", 0))
    at = (order_value(order, "updated_at") or order_value(order, "at")
          or payload.get("at") or "")
    return new_snapshot(total, currency, LEGACY_RATE, at, legacy=True)


def entry_from_order(order, archived_ids=None):
    """Translate one confirmed order into its isolated ledger row."""
    payload = order_payload(order)
    snapshot = account_block(order)
    currency = normalize_currency(snapshot.get("currency") or
                                 order_value(order, "currency") or
                                 payload.get("currency")) or "NGN"
    rate = safe_rate(snapshot.get("exchangeRate"))
    sale = amount(snapshot.get("saleAmount", order_value(
        order, "total", payload.get("total", 0))))
    supplier_ngn = amount(snapshot.get("supplierCostNgn", 0))
    delivery_expense = amount(snapshot.get("deliveryExpense", 0))
    supplier_cfa = supplier_cost_cfa(supplier_ngn, rate)
    supplier_in_currency = supplier_cfa if currency == "CFA" else supplier_ngn
    profit = sale - supplier_in_currency
    cash_profit = profit - delivery_expense
    customer = payload.get("customer") or {}
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
    item_names = []
    for item in items[:12]:
        name = str(item.get("name") or item.get("id") or "Item").strip()
        qty = amount(item.get("qty"), 1)
        if name:
            item_names.append(f"{qty}× {name}" if qty else name)
    oid = str(order_value(order, "id") or payload.get("id") or "")
    archived = set(archived_ids or ())
    return {
        "id": oid,
        "date": str(snapshot.get("confirmedAt") or order_value(
            order, "updated_at") or order_value(order, "at") or payload.get("at") or ""),
        "currency": currency,
        "customer": str(customer.get("name") or order_value(order, "customer_name") or "Customer"),
        "itemsSummary": ", ".join(item_names),
        "saleAmount": sale,
        "supplierCostNgn": supplier_ngn,
        "supplierCostCfa": supplier_cfa,
        "deliveryExpense": delivery_expense,
        "supplierCostInCurrency": supplier_in_currency,
        "netProfit": profit,
        "netCashProfit": cash_profit,
        "exchangeRate": float(rate),
        "notes": str(snapshot.get("notes") or ""),
        "legacySnapshot": snapshot.get("snapshotSource") == "legacy-estimate",
        "deleted": bool(snapshot.get("deletedAt")),
        "archived": oid in archived,
        "deletedAt": snapshot.get("deletedAt") or "",
    }


def batch_totals(entries, currency):
    """Currency-safe calculator totals for a single-currency batch."""
    currency = normalize_currency(currency) or "NGN"
    rows = [row for row in (entries or [])
            if normalize_currency(row.get("currency")) == currency]
    revenue = sum(amount(row.get("saleAmount")) for row in rows)
    supplier_ngn = sum(amount(row.get("supplierCostNgn")) for row in rows)
    supplier_currency = sum(amount(row.get("supplierCostInCurrency")) for row in rows)
    transport = sum(amount(row.get("deliveryExpense")) for row in rows)
    return {
        "currency": currency,
        "orderCount": len(rows),
        "customerRevenue": revenue,
        "supplierPayableNgn": supplier_ngn,
        "supplierCostInCurrency": supplier_currency,
        "transportExpense": transport,
        "netCashProfit": revenue - supplier_currency - transport,
    }


# ------------------------------------------------------- manual expense logger
# The owner records bulk stock purchases (Ankara / perfumes bought ahead of
# sales), personal payouts and bank transfer / withdrawal charges here. Every
# record is a plain dict so the same JSON row pattern that stores accounting
# batches (growth_settings, mirrored to Supabase) stores expenses too.
EXPENSE_KINDS = ("purchase", "payout", "fee")
EXPENSE_MAX = 5000


def normalize_expense_kind(value):
    text = str(value or "").strip().lower()
    aliases = {
        "purchase": "purchase", "stock": "purchase", "supplier": "purchase",
        "buy": "purchase", "bulk": "purchase",
        "payout": "payout", "withdrawal": "payout", "withdraw": "payout",
        "personal": "payout", "salary": "payout",
        "fee": "fee", "fees": "fee", "charge": "fee", "charges": "fee",
        "transfer": "fee", "transferfee": "fee", "bank": "fee",
    }
    return aliases.get(re.sub(r"[\s_/-]+", "", text), "")


def expense_kind_label(kind):
    return {"purchase": "Stock purchase", "payout": "Payout",
            "fee": "Bank fee"}.get(normalize_expense_kind(kind), "Expense")


def new_expense(kind, currency, value, *, note="", at="", actor="",
                batch_id="", expense_id=""):
    """Build one validated manual-expense record (or None when invalid)."""
    kind = normalize_expense_kind(kind)
    if not kind:
        return None
    total = validated_amount(value)
    if total is None or total <= 0 or total > EXPENSE_MAX * 10_000:
        return None
    import secrets as _secrets
    return {
        "id": str(expense_id or ("EXP-" + _secrets.token_hex(5).upper())),
        "kind": kind,
        "currency": normalize_currency(currency) or "NGN",
        "amount": total,
        "note": str(note or "")[:300],
        "batchId": str(batch_id or "")[:40],
        "at": str(at or "")[:32],
        "by": str(actor or "")[:120],
    }


def clean_expenses(rows, limit=1000):
    """Keep only well-formed expense records, newest first."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        record = new_expense(
            row.get("kind"), row.get("currency"), row.get("amount"),
            note=row.get("note"), at=row.get("at"), actor=row.get("by"),
            batch_id=row.get("batchId"), expense_id=row.get("id"))
        if record:
            out.append(record)
    out.sort(key=lambda row: str(row.get("at") or ""), reverse=True)
    return out[:max(1, min(int(limit or 1000), 5000))]


def bank_balances(batches, expenses, settings):
    """Live running bank balance for both currencies.

    Current Bank Balance = Starting Balance + Cumulative Sales Net Profit
    - Manual Expenses - Bank/Transfer Charges. Cumulative sales net profit is
    the sum of every PUSHED batch snapshot (a batch only exists once the owner
    pushed it off the accounting queue), so the balance never counts a sale
    twice and never moves while an order is still staged.
    """
    settings = settings if isinstance(settings, dict) else {}
    result = {}
    for currency in ("NGN", "CFA"):
        starting = amount(settings.get(
            "startingBalanceNgn" if currency == "NGN" else "startingBalanceCfa"))
        profit = 0
        for batch in batches or []:
            if not isinstance(batch, dict):
                continue
            if normalize_currency(batch.get("currency")) != currency:
                continue
            totals = batch.get("totals") if isinstance(batch.get("totals"), dict) else {}
            profit += amount(totals.get("netCashProfit"))
            # A transfer/withdrawal fee entered at push time is a charge on
            # the batch itself; it is not duplicated in the expense list.
            profit -= amount(batch.get("transferFee"))
        manual = charges = 0
        for expense in expenses or []:
            if not isinstance(expense, dict):
                continue
            if normalize_currency(expense.get("currency")) != currency:
                continue
            if normalize_expense_kind(expense.get("kind")) == "fee":
                charges += amount(expense.get("amount"))
            else:
                manual += amount(expense.get("amount"))
        result[currency] = {
            "currency": currency,
            "startingBalance": starting,
            "salesNetProfit": profit,
            "manualExpenses": manual,
            "bankCharges": charges,
            "balance": starting + profit - manual - charges,
        }
    return result


def batch_in_period(batch, start, end):
    """True when a pushed batch falls inside the inclusive date bounds."""
    day = str((batch or {}).get("pushedAt")
              or (batch or {}).get("createdAt") or "")[:10]
    if not day:
        return start is None and end is None
    try:
        import datetime as _datetime
        when = _datetime.date.fromisoformat(day)
    except ValueError:
        return False
    if start is not None and when < start:
        return False
    if end is not None and when > end:
        return False
    return True


def sales_summary(batches, expenses, currency, period="month", today=None):
    """Period profit analytics over PUSHED batches + unlinked expenses.

    This is the Sales / History page calculator: it reads the immutable batch
    snapshots (never the live order rows) so historical performance stays
    exactly as it was the day the owner pushed each batch. Standalone manual
    expenses recorded in the period are folded in the same way the Google
    ledger folds them: a stock purchase adds to supplier costs, a bank fee
    adds to transport/charges.
    """
    import datetime as _datetime
    import google_sheets as _sheets
    currency = normalize_currency(currency) or "NGN"
    today = today or _datetime.datetime.now(_datetime.timezone.utc).date()
    start, end = _sheets.period_bounds(period, today=today)
    revenue = supplier = transport = fees = order_count = 0
    matching = []
    for batch in batches or []:
        if not isinstance(batch, dict):
            continue
        if normalize_currency(batch.get("currency")) != currency:
            continue
        if not batch_in_period(batch, start, end):
            continue
        totals = batch.get("totals") if isinstance(batch.get("totals"), dict) else {}
        revenue += amount(totals.get("customerRevenue"))
        supplier += amount(totals.get("supplierCostInCurrency"))
        transport += amount(totals.get("transportExpense"))
        fees += amount(batch.get("transferFee"))
        order_count += amount(totals.get("orderCount"))
        matching.append(batch)
    for expense in expenses or []:
        if not isinstance(expense, dict):
            continue
        if normalize_currency(expense.get("currency")) != currency:
            continue
        day = str(expense.get("at") or "")[:10]
        if not _sheets._in_filters(_row_date_safe(day), start, end):
            continue
        if normalize_expense_kind(expense.get("kind")) == "fee":
            transport += amount(expense.get("amount"))
        else:
            supplier += amount(expense.get("amount"))
    matching.sort(key=lambda row: str(row.get("pushedAt")
                                      or row.get("createdAt") or ""), reverse=True)
    return {
        "currency": currency,
        "period": str(period or "month"),
        "periodStart": start.isoformat() if start else "",
        "periodEnd": end.isoformat() if end else "",
        "revenue": revenue,
        "supplierCosts": supplier,
        "transport": transport,
        "transferFees": fees,
        "netProfit": revenue - supplier - transport - fees,
        "orderCount": order_count,
        "batchCount": len(matching),
        "batches": matching,
    }


def _row_date_safe(value):
    try:
        import datetime as _datetime
        return _datetime.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None
