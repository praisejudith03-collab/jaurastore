"""Currency conversion rules for the storefront.

Naira (₦) is the BASE currency: the price an admin types in Naira is stored
and displayed EXACTLY as entered — never rounded, never adjusted.

F CFA is a CONVERTED currency: every CFA figure is derived from a Naira price
at the house rate and is then rounded UP to a clean 50 / 100 step, so no odd
amount such as "24 F CFA" or "1 013 F CFA" can ever reach a shopper.

Rounding contract (ceiling to the next 50 step)
-----------------------------------------------
    0      -> 0        (free stays free)
    1..50  -> 50       (anything below 50 rounds UP to 50)
    51..100-> 100      (anything above 50 rounds UP to the next 100)
    101..150 -> 150, 151..200 -> 200, ...

The same function is mirrored in js/store.js (``toCfa``) so the browser, the
admin preview and the server always agree on the displayed amount.
"""

# CFA amounts are always a multiple of this step.
CFA_STEP = 50


def live_rate():
    """The live, admin-controlled NGN -> F CFA exchange rate.

    The single source of truth is the growth setting ``cfaRate`` (Admin ->
    Settings -> Currency & exchange rate), persisted in ``growth_settings``
    and mirrored to Supabase; ``growth._cap`` guarantees the stored value is
    always a sane positive number. There is NO hardcoded literal in this
    module: if the settings store is entirely unreachable (e.g. a bare import
    in a test process with no database), the admin setting's own configured
    default (``growth.DEFAULTS``) is used - the same value the admin sees in
    the UI before saving anything.
    """
    import growth
    try:
        rate = float(growth.settings()["cfaRate"])
    except Exception:
        return float(growth.DEFAULTS["cfaRate"])
    # growth._cap already enforces these bounds on the stored value; the check
    # here keeps a rate read straight from the row (or from a restored mirror)
    # from pricing the whole catalogue at zero.
    if not (0.01 <= rate <= 100):
        return float(growth.DEFAULTS["cfaRate"])
    return rate


def round_cfa(amount):
    """Round one F CFA amount UP to the nearest 50 / 100 step.

    Never raises: a non-numeric value becomes 0. Negative values clamp to 0
    because a price can never be below zero.
    """
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    steps = int(value // CFA_STEP)
    if value - (steps * CFA_STEP) > 1e-9:
        steps += 1
    return steps * CFA_STEP


def floor_cfa(amount):
    """DEPRECATED alias for :func:`round_cfa` - do not use in new code.

    Every F CFA amount now follows the one rounding contract: UP to the next
    50 step, no exceptions (base prices, compare-at, sale prices, percentage
    discounts, cart and checkout totals). This older name used to round DOWN
    for discounted totals; it is kept only so existing imports keep working
    and now returns exactly what ``round_cfa`` returns.
    """
    return round_cfa(amount)


def to_cfa(ngn, rate=None):
    """Convert a Naira amount to a clean, rounded-up F CFA amount.

    ``rate=None`` (the default) always uses the live admin-controlled rate
    from :func:`live_rate`; an explicit ``rate`` wins so historical snapshots
    can be repriced exactly as they were locked.
    """
    try:
        naira = float(ngn or 0)
    except (TypeError, ValueError):
        return 0
    if naira <= 0:
        return 0
    try:
        rate = float(rate) if rate else live_rate()
    except (TypeError, ValueError):
        rate = live_rate()
    if rate <= 0:
        rate = live_rate()
    return round_cfa(naira * rate)


def to_ngn(cfa, rate=None):
    """Convert an F CFA amount back to Naira. Naira is never rounded.

    ``rate=None`` (the default) always uses the live admin-controlled rate
    from :func:`live_rate`.
    """
    try:
        value = float(cfa or 0)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    try:
        rate = float(rate) if rate else live_rate()
    except (TypeError, ValueError):
        rate = live_rate()
    if rate <= 0:
        rate = live_rate()
    return max(0, int(round(value / rate)))
