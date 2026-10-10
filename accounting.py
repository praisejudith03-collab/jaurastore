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
SUPPLIER_LINK_MAX = 500
PERCENT_MAX = Decimal("100")


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
    """Read the live, admin-controlled NGN -> CFA rate (growth setting cfaRate).

    Never falls back to a hardcoded literal: the only fallback is the admin
    setting's own configured default (growth.DEFAULTS). LEGACY_RATE is reserved
    for historical snapshots whose rate was never captured.
    """
    import growth
    try:
        return safe_rate(growth.settings().get("cfaRate"))
    except Exception:
        return safe_rate(growth.DEFAULTS["cfaRate"])


def supplier_cost_cfa(supplier_cost_ngn, rate):
    """Convert NGN supplier spend into whole CFA using NGN * rate."""
    ngn = Decimal(amount(supplier_cost_ngn))
    return int((ngn * safe_rate(rate)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))


# Default supplier cost ratio used as a catalog-fallback when neither the
# watchdog price book nor an explicit unit price has been recorded yet.
# Estimated as retail price × DEFAULT_SUPPLIER_COST_RATIO; the owner is still
# expected to correct this on the accounting desk.
DEFAULT_SUPPLIER_COST_RATIO = Decimal("0.55")


def _catalog_supplier_cost_ratio():
    """Read the admin-tunable supplier cost ratio from growth settings."""
    try:
        import growth
        settings = growth.settings()
        raw = settings.get("supplierCostRatio")
        if raw is not None:
            ratio = Decimal(str(raw))
            if Decimal("0.1") <= ratio <= Decimal("0.95"):
                return ratio
    except Exception:
        pass
    return DEFAULT_SUPPLIER_COST_RATIO


def _catalog_default_unit_cost(product, variant_label=""):
    """Estimate a unit supplier cost for one catalog product.

    Returns (unit_cost_ngn, link) tuple; (0, "") when there is no usable
    catalog data. Used only as a fallback when the supplier watchdog has no
    recorded price for the product yet.
    """
    if not isinstance(product, dict):
        return 0, ""
    # Owner-entered explicit supplier unit cost if the catalog grows one.
    for key in ("supplierCostNgn", "supplierUnitCost", "supplierUnitPrice"):
        raw = product.get(key)
        try:
            val = int(Decimal(str(raw or 0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, ValueError, TypeError):
            val = 0
        if val > 0:
            link = clean_supplier_link(
                product.get("supplierSku") or product.get("supplierUrl")
                or product.get("supplier_url"))
            return val, link
    # Fallback: ratio of the NGN retail price.
    retail = 0
    for key in ("priceNgn",):
        try:
            retail = int(Decimal(str(product.get(key) or 0)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, ValueError, TypeError):
            retail = 0
        if retail > 0:
            break
    link = clean_supplier_link(
        product.get("supplierSku") or product.get("supplierUrl")
        or product.get("supplier_url"))
    if retail > 0:
        ratio = _catalog_supplier_cost_ratio()
        return int((Decimal(retail) * ratio).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP)), link
    return 0, link


def saved_supplier_defaults(items):
    """Saved per-product supplier defaults for one order's items.

    Lookup order:
      1. Supplier watchdog's persisted last-seen price book
         (``growth_settings.supplier_price_watch_json``) - most accurate.
      2. Catalog's explicit supplier cost field (``supplierCostNgn``)
         if the product has one set.
      3. Catalog retail price × admin-tunable supplier cost ratio
         (default 55%) - a reasonable estimate so Column G is never blank
         for products the catalogue knows about.

    The owner can still override every field by hand on the accounting desk;
    this only pre-fills what is already known. Returns
    ``{"costNgn": int, "qty": int, "link": str}`` - zero/blank when nothing
    is known. Never raises.
    """
    result = {"costNgn": 0, "qty": 0, "link": ""}
    try:
        import supplier_watchdog
        book = supplier_watchdog.saved_price_book()
        fold = getattr(supplier_watchdog, "fold", None) \
            or (lambda s: str(s or "").strip().casefold())
    except Exception:
        book, fold = {}, (lambda s: str(s or "").strip().casefold())
    items = [i for i in (items or []) if isinstance(i, dict)]
    if not items:
        return result
    # Catalog index is fetched lazily (once per call) and only when needed
    # for fallback.
    catalog_index = None
    catalog_link = ""

    def _catalog():
        nonlocal catalog_index
        if catalog_index is None:
            try:
                import catalog as catalog_mod
                catalog_index = catalog_mod.product_index()
            except Exception:
                catalog_index = {}
        return catalog_index or {}

    total = 0
    qty = 0
    for item in items:
        pid = str(item.get("id") or item.get("productId") or "").strip()
        line_qty = quantity(item.get("qty") or item.get("quantity"), 1)
        qty += line_qty
        price = 0.0
        link = ""
        if pid:
            entry = book.get(pid)
            prices = entry.get("prices") if isinstance(entry, dict) \
                and isinstance(entry.get("prices"), dict) else {}
            if prices:
                folded = {fold(k): v for k, v in prices.items()}
                found = None
                for raw in (item.get("variant"), item.get("color"),
                            item.get("option")):
                    key = fold(raw)
                    if key and key in folded:
                        found = folded[key]
                        break
                if found is None and fold("product") in folded:
                    found = folded[fold("product")]
                if found is None and len(folded) == 1:
                    found = next(iter(folded.values()))
                try:
                    price = float(found) if found is not None else 0.0
                except (TypeError, ValueError):
                    price = 0.0
            # Fallback to catalog defaults when watchdog has no price.
            if not (price and price > 0):
                product = _catalog().get(pid)
                variant_label = ""
                for raw in (item.get("variant"), item.get("color"),
                            item.get("option")):
                    if raw:
                        variant_label = str(raw)
                        break
                unit, link = _catalog_default_unit_cost(product, variant_label)
                if unit > 0:
                    price = float(unit)
        if price and price > 0:
            total += int(round(price * line_qty))
        if link and not catalog_link:
            catalog_link = link
    result["costNgn"] = int(total)
    result["qty"] = int(qty)
    result["link"] = catalog_link
    # If we still have no link, scan the catalog index to pick up any saved
    # supplier URL from the ordered items.
    if not result["link"]:
        try:
            idx = _catalog()
            for item in items:
                pid = str(item.get("id") or item.get("productId") or "").strip()
                product = idx.get(pid) if pid else None
                if not isinstance(product, dict):
                    continue
                link = clean_supplier_link(
                    product.get("supplierSku") or product.get("supplierUrl")
                    or product.get("supplier_url"))
                if link:
                    result["link"] = link
                    break
        except Exception:
            pass
    return result


def apply_supplier_defaults_to_snapshot(snapshot, order):
    """Fill in supplier cost defaults when the snapshot has a blank/zero cost.

    Ensures Column G ("Supplier costs · NGN / FCFA") is populated from the
    catalog / watchdog price book whenever the supplier cost was left blank
    or zero at staging or processing time. Mutates and returns the snapshot.
    """
    if not isinstance(snapshot, dict):
        return snapshot
    snapshot = dict(snapshot)
    payload = order_payload(order)
    items = payload.get("items") or []
    existing_cost = amount(snapshot.get("supplierCostNgn"))
    existing_unit = amount(snapshot.get("supplierUnitPriceNgn"))
    if existing_cost > 0 or existing_unit > 0:
        return snapshot
    defaults = saved_supplier_defaults(items)
    if defaults.get("costNgn", 0) > 0:
        qty = quantity(snapshot.get("supplierQty"),
                       quantity(None, order_quantity(order)))
        cost = int(defaults["costNgn"])
        unit = int((Decimal(cost) / Decimal(max(1, qty))).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP))
        snapshot["supplierCostNgn"] = cost
        snapshot["supplierUnitPriceNgn"] = unit
        snapshot["supplierQty"] = qty
    if defaults.get("link") and not snapshot.get("supplierLink"):
        snapshot["supplierLink"] = clean_supplier_link(defaults["link"])
    return snapshot


def order_location(order):
    """The customer's destination for the ledger's Location column.

    Country first (the ledger's destination granularity: Nigeria, Benin
    Republic, Togo, ...), falling back to city then delivery zone. Never
    raises; blank when the order carries no location at all.
    """
    payload = order_payload(order)
    customer = payload.get("customer")
    customer = customer if isinstance(customer, dict) else {}
    for value in (order_value(order, "country"), customer.get("country"),
                  order_value(order, "city"), customer.get("city"),
                  order_value(order, "zone"), customer.get("zone")):
        text = str(value or "").strip()
        if text:
            return text[:80]
    return ""


# ------------------------------------------------- supplier price / discounts
# The accounting desk accepts a UNIT supplier price and the ordered quantity
# per order (Unit Supplier Price x Quantity = Total Supplier Cost). Both the
# unit price and the supplier link may stay blank - a costless or unlinked
# item simply contributes 0 to the batch until the owner fills it in, and
# every figure recalculates the moment it is typed.


def clean_supplier_link(value, limit=SUPPLIER_LINK_MAX):
    """A supplier URL/reference; blank is valid (the link is optional)."""
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"[\r\n\t]+", " ", text)[:max(1, int(limit or SUPPLIER_LINK_MAX))]


def quantity(value, default=1):
    """Parse a positive whole quantity; invalid or blank means ``default``."""
    parsed = decimal_value(value, None)
    if parsed is None or not parsed.is_finite() or parsed <= 0:
        return max(1, int(default or 1))
    if parsed > AMOUNT_MAX:
        return max(1, int(default or 1))
    return int(parsed.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def order_quantity(order):
    """The ordered quantity of an order (sum of its item quantities).

    This is the default multiplier for a unit supplier price: an order for
    3 pieces x ₦2,000 costs ₦6,000 before any manual override.
    """
    payload = order_payload(order)
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
    total = 0
    for item in items:
        total += quantity(item.get("qty") or item.get("quantity"), 1)
    return total or 1


def percent_value(value):
    """Parse a 0-100 discount percentage (``None`` when invalid/blank)."""
    parsed = decimal_value(value, None)
    if parsed is None or not parsed.is_finite() or parsed < 0 or parsed > PERCENT_MAX:
        return None
    return parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def discount_from_percent(sale, percent):
    """Whole-unit discount for a percentage of the selling price."""
    pct = percent_value(percent)
    if pct is None or pct <= 0:
        return 0
    base = Decimal(amount(sale))
    return int((base * pct / Decimal(100)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))


def percent_from_discount(sale, value):
    """The percentage a whole-unit discount represents (0.00 when no sale)."""
    base = Decimal(amount(sale))
    if base <= 0:
        return Decimal("0.00")
    return (Decimal(amount(value)) * Decimal(100) / base).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)


def supplier_total(unit_price, qty):
    """Unit Supplier Price x Quantity = Total Supplier Cost (NGN)."""
    return amount(Decimal(amount(unit_price)) * Decimal(quantity(qty, 1)))


def editable_amount(value):
    """A whole-unit amount for an editable field; blank/absent means 0.

    The accounting desk must accept an EMPTY supplier price, supplier link,
    discount or transport box (a cost the owner has not looked up yet) without
    breaking the calculation - an emptied box simply means 0. A value that is
    present but not a valid non-negative amount still fails, so a typo is
    never silently treated as zero.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    return validated_amount(value)


def apply_supplier_fields(snapshot, order, values, *, sale=None):
    """Apply an accounting edit onto a snapshot and return ``(snapshot, error)``.

    Accepts the editable supplier-shape fields:

      * ``supplierLink``          - free text / URL, may be blank at any time
      * ``supplierUnitPriceNgn``  - unit price; total = unit x quantity
      * ``supplierQty``           - quantity override (defaults to the order's)
      * ``supplierCostNgn``       - a directly typed total (unit price derived)
      * ``discount``              - applied discount, whole units
      * ``discountPercent``       - applied discount as a % of the sale

    Blank input is always valid: an order can stage with no supplier price and
    no supplier link, and simply contributes 0 until the owner fills them in.
    """
    values = values if isinstance(values, dict) else {}
    snapshot = dict(snapshot or {})
    sale_value = amount(snapshot.get("saleAmount") if sale is None else sale)

    if "supplierLink" in values:
        snapshot["supplierLink"] = clean_supplier_link(values.get("supplierLink"))

    qty = quantity(values.get("supplierQty"), quantity(
        snapshot.get("supplierQty"), order_quantity(order)))
    unit_present = "supplierUnitPriceNgn" in values
    qty_present = "supplierQty" in values
    total_present = "supplierCostNgn" in values

    if unit_present:
        unit = editable_amount(values.get("supplierUnitPriceNgn"))
        if unit is None:
            return None, "Unit supplier price must be a non-negative whole amount."
        snapshot["supplierUnitPriceNgn"] = unit
        snapshot["supplierQty"] = qty
        snapshot["supplierCostNgn"] = supplier_total(unit, qty)
    elif total_present:
        total = editable_amount(values.get("supplierCostNgn"))
        if total is None:
            return None, "supplierCostNgn must be a non-negative whole amount."
        snapshot["supplierQty"] = qty
        snapshot["supplierCostNgn"] = total
        snapshot["supplierUnitPriceNgn"] = (
            int((Decimal(total) / Decimal(qty)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP)) if qty else 0)
    elif qty_present:
        # Quantity alone re-multiplies the stored unit price when there is one.
        unit = amount(snapshot.get("supplierUnitPriceNgn"))
        if unit:
            snapshot["supplierQty"] = qty
            snapshot["supplierCostNgn"] = supplier_total(unit, qty)
        else:
            snapshot["supplierQty"] = qty
            snapshot["supplierUnitPriceNgn"] = 0

    snapshot.setdefault("supplierUnitPriceNgn", 0)
    snapshot.setdefault("supplierQty", quantity(snapshot.get("supplierQty"),
                                                order_quantity(order)))
    snapshot.setdefault("supplierLink", "")

    if "discountPercent" in values:
        raw_percent = values.get("discountPercent")
        if raw_percent is None or (isinstance(raw_percent, str) and not raw_percent.strip()):
            raw_percent = 0
        pct = percent_value(raw_percent)
        if pct is None:
            return None, "Discount percent must be between 0 and 100."
        snapshot["discountPercent"] = float(pct)
        snapshot["discount"] = discount_from_percent(sale_value, pct)
    elif "discount" in values:
        discount = editable_amount(values.get("discount"))
        if discount is None:
            return None, "Discount must be a non-negative whole amount."
        snapshot["discount"] = discount
        snapshot["discountPercent"] = float(percent_from_discount(sale_value, discount))

    snapshot.setdefault("discount", 0)
    snapshot.setdefault("discountPercent", 0.0)
    return snapshot, ""


def entry_figures(snapshot, currency="NGN", rate=LEGACY_RATE):
    """Net profit figures for one entry.

    Net Profit = Selling Price - Applied Discounts - Total Supplier Cost
    Net Cash Profit = Net Profit - Delivery / Transport Fee
    (The Starting Profit / opening balance is added once at the batch ledger
    level - ``bank_balances`` - never per row.)
    """
    currency = normalize_currency(currency) or "NGN"
    rate = safe_rate(rate)
    sale = amount(snapshot.get("saleAmount"))
    discount = amount(snapshot.get("discount"))
    supplier_ngn = amount(snapshot.get("supplierCostNgn"))
    supplier_cfa = supplier_cost_cfa(supplier_ngn, rate)
    supplier_in_currency = supplier_cfa if currency == "CFA" else supplier_ngn
    transport = amount(snapshot.get("deliveryExpense"))
    net_profit = sale - discount - supplier_in_currency
    return {
        "saleAmount": sale,
        "discount": discount,
        "supplierCostNgn": supplier_ngn,
        "supplierCostCfa": supplier_cfa,
        "supplierCostInCurrency": supplier_in_currency,
        "deliveryExpense": transport,
        "netProfit": net_profit,
        "netCashProfit": net_profit - transport,
    }


def new_snapshot(total, currency, rate, confirmed_at, *, legacy=False,
                 supplier_cost_ngn=0, supplier_qty=0, supplier_link=""):
    """Create a once-only order accounting snapshot.

    New confirmations use the active admin rate. Pre-feature confirmed orders
    use the explicitly marked 0.44 baseline because their historical rate was
    never captured; subsequent rate changes cannot move that baseline.

    ``supplier_cost_ngn`` / ``supplier_qty`` / ``supplier_link`` pre-fill the
    desk from the saved product supplier defaults (see
    :func:`saved_supplier_defaults`); the owner can edit every field on the
    accounting desk afterwards - checkout never accepts cost data from a
    customer.
    """
    currency = normalize_currency(currency) or "NGN"
    cost = amount(supplier_cost_ngn)
    qty = quantity(supplier_qty, 0) if supplier_qty else 0
    unit = 0
    if cost and qty:
        unit = int((Decimal(cost) / Decimal(qty)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP))
    return {
        "version": 1,
        "currency": currency,
        "confirmedAt": str(confirmed_at or ""),
        "exchangeRate": float(safe_rate(rate)),
        "saleAmount": amount(total),
        # Supplier costs and transport are intentionally editable on the
        # accounting sheet. When saved product defaults are known they arrive
        # pre-filled; otherwise a blank supplier link / unit price is a valid
        # starting state - the owner fills them in from the accounting desk
        # and the net profit recalculates immediately (unit x quantity).
        "supplierCostNgn": cost,
        "supplierUnitPriceNgn": unit,
        # 0 means "follow this order's own quantity"; the desk shows the
        # effective multiplier (item quantity) and the owner may override it.
        "supplierQty": qty,
        "supplierLink": clean_supplier_link(supplier_link),
        "discount": 0,
        "discountPercent": 0.0,
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


def account_block(order, *, current_rate=None, apply_defaults=True):
    """Return a copy of a stored snapshot, or a stable legacy estimate.

    When ``apply_defaults`` is true (the default), blank/zero supplier costs
    are auto-filled from the catalog / watchdog default so Column G is never
    empty for known products. This applies at staging, processing and
    push-time.
    """
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
        result.setdefault("supplierUnitPriceNgn", amount(result.get("supplierCostNgn")))
        result.setdefault("supplierQty", order_quantity(order))
        result.setdefault("supplierLink", "")
        result.setdefault("discount", 0)
        result.setdefault("discountPercent", 0.0)
        result.setdefault("deliveryExpense", 0)
        result.setdefault("notes", "")
        result.setdefault("snapshotSource", "legacy-estimate")
        if apply_defaults:
            result = apply_supplier_defaults_to_snapshot(result, order)
        return result

    # Before accounting shipped, no rate was recorded on an order. Use a
    # fixed, clearly-labelled baseline instead of re-reading today's setting
    # on every view (which would make old profit totals drift over time).
    currency = normalize_currency(
        order_value(order, "currency") or payload.get("currency")) or "NGN"
    total = order_value(order, "total", payload.get("total", 0))
    at = (order_value(order, "updated_at") or order_value(order, "at")
          or payload.get("at") or "")
    defaults = saved_supplier_defaults(payload.get("items") or [])
    snap = new_snapshot(total, currency, LEGACY_RATE, at, legacy=True,
                        supplier_cost_ngn=defaults.get("costNgn", 0),
                        supplier_qty=defaults.get("qty", 0),
                        supplier_link=defaults.get("link", ""))
    return snap


def entry_from_order(order, archived_ids=None):
    """Translate one confirmed order into its isolated ledger row."""
    payload = order_payload(order)
    snapshot = account_block(order)
    currency = normalize_currency(snapshot.get("currency") or
                                 order_value(order, "currency") or
                                 payload.get("currency")) or "NGN"
    rate = safe_rate(snapshot.get("exchangeRate"))
    figures = entry_figures(snapshot, currency, rate)
    sale = amount(snapshot.get("saleAmount", order_value(
        order, "total", payload.get("total", 0))))
    supplier_ngn = figures["supplierCostNgn"]
    delivery_expense = figures["deliveryExpense"]
    supplier_cfa = figures["supplierCostCfa"]
    supplier_in_currency = figures["supplierCostInCurrency"]
    profit = figures["netProfit"]
    cash_profit = figures["netCashProfit"]
    customer = payload.get("customer") or {}
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
    item_names = []
    for item in items[:12]:
        name = str(item.get("name") or item.get("id") or "Item").strip()
        qty = amount(item.get("qty"), 1)
        if name:
            item_names.append(f"{qty}× {name}" if qty else name)
    item_quantity = order_quantity(order)
    oid = str(order_value(order, "id") or payload.get("id") or "")
    archived = set(archived_ids or ())
    return {
        "id": oid,
        "date": str(snapshot.get("confirmedAt") or order_value(
            order, "updated_at") or order_value(order, "at") or payload.get("at") or ""),
        "currency": currency,
        "customer": str(customer.get("name") or order_value(order, "customer_name") or "Customer"),
        "location": order_location(order),
        "itemsSummary": ", ".join(item_names),
        "itemQuantity": item_quantity,
        "saleAmount": sale,
        "discount": figures["discount"],
        "discountPercent": float(decimal_value(snapshot.get("discountPercent"), 0)),
        "supplierCostNgn": supplier_ngn,
        "supplierCostCfa": supplier_cfa,
        "supplierUnitPriceNgn": amount(snapshot.get("supplierUnitPriceNgn")),
        "supplierQty": quantity(snapshot.get("supplierQty"), item_quantity),
        "supplierLink": clean_supplier_link(snapshot.get("supplierLink")),
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


def allocate_transport(entries, fee):
    """Spread one Batch Transportation Fee across a currency group's rows.

    Used only for the Google Sheet write: the batch itself keeps the single
    figure, and each sheet row carries its share so the sheet's own net-profit
    column (a live formula in the app-created ledgers) agrees with the batch
    total the app shows. Whole units, remainder on the first row, so the shares
    always add back up to the fee exactly.
    """
    rows = [row for row in (entries or []) if isinstance(row, dict)]
    if not rows:
        return {}
    total = amount(fee)
    share, remainder = divmod(total, len(rows))
    result = {}
    for index, row in enumerate(rows):
        oid = str(row.get("id") or "")
        result[oid] = share + (remainder if index == 0 else 0)
    return result


def batch_transport_fee(batch):
    """The batch-level transport fee, or ``None`` when it falls back per-order.

    A blank Batch Transportation Fee box means "use the individual per-order
    transport fees"; an explicit amount (including 0) is one single cost logged
    for the whole batch.
    """
    if not isinstance(batch, dict):
        return None
    return batch.get("batchTransportFee")


def batch_transport(batch):
    """Return ``(amount, mode)`` for one batch's transport cost."""
    totals = batch.get("totals") if isinstance(batch.get("totals"), dict) else {}
    fee = batch_transport_fee(batch)
    if fee is None:
        return amount(totals.get("transportExpense")), "per-order"
    return amount(fee), "batch"


def batch_net_profit(batch):
    """Batch net profit: revenue - discounts - supplier - transport."""
    totals = batch.get("totals") if isinstance(batch.get("totals"), dict) else {}
    transport, mode = batch_transport(batch)
    if mode == "batch" and amount(totals.get("transportExpense")) != transport:
        return (amount(totals.get("customerRevenue"))
                - amount(totals.get("discountsTotal"))
                - amount(totals.get("supplierCostInCurrency"))
                - transport)
    return amount(totals.get("netCashProfit"))


def batch_totals(entries, currency, batch_transport_fee=None):
    """Currency-safe calculator totals for a single-currency batch.

    ``batch_transport_fee`` is the single Batch Transportation Fee for the
    whole batch. When it is ``None`` (the box was left blank) the individual
    per-order transport fees are summed instead.
    """
    currency = normalize_currency(currency) or "NGN"
    rows = [row for row in (entries or [])
            if normalize_currency(row.get("currency")) == currency]
    revenue = sum(amount(row.get("saleAmount")) for row in rows)
    discounts = sum(amount(row.get("discount")) for row in rows)
    supplier_ngn = sum(amount(row.get("supplierCostNgn")) for row in rows)
    supplier_currency = sum(amount(row.get("supplierCostInCurrency")) for row in rows)
    per_order_transport = sum(amount(row.get("deliveryExpense")) for row in rows)
    if batch_transport_fee is None:
        transport = per_order_transport
    else:
        transport = amount(batch_transport_fee)
    return {
        "currency": currency,
        "orderCount": len(rows),
        "customerRevenue": revenue,
        "discountsTotal": discounts,
        "supplierPayableNgn": supplier_ngn,
        "supplierCostInCurrency": supplier_currency,
        "transportExpense": transport,
        "netCashProfit": revenue - discounts - supplier_currency - transport,
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


def starting_profit(settings, currency):
    """The Starting Profit / Opening Balance the owner entered for a ledger.

    ``startingBalanceNgn`` is the canonical key; the ``startingProfit*`` and
    ``openingBalance*`` spellings are accepted aliases so the input box on the
    accounting desk can call the figure what the owner calls it.
    """
    settings = settings if isinstance(settings, dict) else {}
    suffix = "Ngn" if normalize_currency(currency) == "NGN" else "Cfa"
    for key in (f"startingBalance{suffix}", f"startingProfit{suffix}",
                f"openingBalance{suffix}"):
        if settings.get(key) not in (None, ""):
            return amount(settings.get(key))
    return 0


def bank_balances(batches, expenses, settings):
    """Live running bank balance for both currencies.

    Current Bank Balance = Starting Profit / Opening Balance + Cumulative
    Sales Net Profit - Manual Expenses - Bank/Transfer Charges. Cumulative
    sales net profit is the sum of every PUSHED batch snapshot (a batch only
    exists once the owner pushed it off the accounting queue), so the balance
    never counts a sale twice and never moves while an order is still staged,
    and each new batch's net profit accumulates on top of the opening balance.
    """
    settings = settings if isinstance(settings, dict) else {}
    result = {}
    for currency in ("NGN", "CFA"):
        starting = starting_profit(settings, currency)
        profit = 0
        for batch in batches or []:
            if not isinstance(batch, dict):
                continue
            if normalize_currency(batch.get("currency")) != currency:
                continue
            # The batch's own profit already honours its transport mode: the
            # single Batch Transportation Fee when one was logged, otherwise
            # the sum of the individual per-order transport fees.
            profit += batch_net_profit(batch)
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
            "startingProfit": starting,
            "openingBalance": starting,
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
    revenue = discounts = supplier = transport = fees = order_count = 0
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
        discounts += amount(totals.get("discountsTotal"))
        supplier += amount(totals.get("supplierCostInCurrency"))
        # A batch logged with a single Batch Transportation Fee contributes
        # that one figure; a batch without one contributes the sum of its
        # individual per-order transport fees.
        transport += batch_transport(batch)[0]
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
        "discounts": discounts,
        "supplierCosts": supplier,
        "transport": transport,
        "transferFees": fees,
        "netProfit": revenue - discounts - supplier - transport - fees,
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
