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

# House display rate: 1 NGN = 0.44 F CFA.
NGN_TO_CFA = 0.44

# CFA amounts are always a multiple of this step.
CFA_STEP = 50


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


def to_cfa(ngn, rate=None):
    """Convert a Naira amount to a clean, rounded-up F CFA amount."""
    try:
        naira = float(ngn or 0)
    except (TypeError, ValueError):
        return 0
    if naira <= 0:
        return 0
    try:
        rate = float(rate) if rate else NGN_TO_CFA
    except (TypeError, ValueError):
        rate = NGN_TO_CFA
    if rate <= 0:
        rate = NGN_TO_CFA
    return round_cfa(naira * rate)


def to_ngn(cfa, rate=None):
    """Convert an F CFA amount back to Naira. Naira is never rounded."""
    try:
        value = float(cfa or 0)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    try:
        rate = float(rate) if rate else NGN_TO_CFA
    except (TypeError, ValueError):
        rate = NGN_TO_CFA
    if rate <= 0:
        rate = NGN_TO_CFA
    return max(0, int(round(value / rate)))
