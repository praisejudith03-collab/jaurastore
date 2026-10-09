"""In-stock broadcast post generation for Telegram / WhatsApp.

The evening broadcast is the shop's daily "what can I actually sell tonight"
message. Everything here exists to make that message impossible to get wrong:

* **Only verified in-stock lines appear.** A variant is listed when its
  ``optionStock`` entry (or the product's own ``stock_quantity`` when it has no
  variants) is greater than zero.
* **The watchdog's own state overrides stale frontend data.** Even if a
  browser, a CDN copy or a cached catalogue still shows a colour as available,
  an option the watchdog watched drop to zero is excluded. See
  :func:`blocked_options`.
* **Prices are shown in Naira and F CFA**, converted with the same
  ``currency.to_cfa`` the storefront renders with, so a customer never sees a
  figure in a broadcast that disagrees with the product page.
* **Every line carries a deep link** to the live storefront URL.

The module is deliberately free of Flask, network calls and database access:
the endpoint collects products and hands them over, which keeps the formatting
rules testable offline.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import urllib.parse
from decimal import Decimal

import currency

# The storefront's real product route is /products/<slug> (see app.py:
# @app.route("/products/<slug>")). It is plural - /product/<slug> does not
# resolve, so a broadcast built with it would send customers to a 404.
STORE_BASE_DEFAULT = "https://jaurastore.com.ng"
PRODUCT_PATH = "/products/"

CATEGORY_FALLBACK = "More from Jaura"
NO_OPTION_KEY = ""          # key for a product that has no variants at all
DEFAULT_TITLE = "Jaura Store — Evening Drop"
DEFAULT_FOOTER = "Tap a link to order. Prices shown in Naira and F CFA."

# Emoji are kept out of the copy on purpose: they render inconsistently across
# Telegram, WhatsApp and plain-SMS forwards, and a broken glyph in the middle
# of a price line is worse than no glyph at all.
MAX_PRODUCTS = 120


def store_base():
    """The public storefront origin (env ``STORE_BASE_URL`` wins)."""
    base = str(os.environ.get("STORE_BASE_URL") or "").strip()
    return (base or STORE_BASE_DEFAULT).rstrip("/")


def product_url(slug, base=""):
    """The direct storefront deep link for one product slug."""
    text = str(slug or "").strip()
    if not text:
        return ""
    return (base or store_base()) + PRODUCT_PATH + urllib.parse.quote(text, safe="")


def active_rate():
    """The live admin-controlled NGN -> F CFA rate.

    There is no literal here on purpose: ``currency.live_rate`` reads the admin
    setting and falls back to that setting's own configured default
    (``growth.DEFAULTS``), never to a number baked into this file.
    """
    try:
        return float(currency.live_rate())
    except Exception:
        import growth
        return float(growth.DEFAULTS["cfaRate"])


def cfa_for(ngn, rate=None):
    """The FCFA figure the storefront would render for this Naira price."""
    try:
        return int(currency.to_cfa(ngn, rate))
    except Exception:
        return 0


def money(ngn, rate=None):
    """``"₦18,500 · 8,200 FCFA"`` - both currencies, storefront rounding."""
    naira = _int(ngn)
    if naira <= 0:
        return ""
    return f"₦{naira:,} · {cfa_for(naira, rate):,} FCFA"


def _int(value):
    try:
        return int(Decimal(str(value)).quantize(Decimal("1")))
    except Exception:
        return 0


def _qty(value):
    """A stock quantity; anything unreadable or negative means 0 (sold out)."""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def option_key(label):
    """Fold an option label for comparison: ``"Sky Blue "`` -> ``"sky blue"``."""
    return re.sub(r"\s+", " ", str(label or "").strip()).casefold()


# ------------------------------------------------------------------ product read
def option_stock(product):
    """``{option label: quantity}`` for a product, or ``{}`` when it has no
    variants. Tolerates the dict and the legacy JSON-string shapes."""
    if not isinstance(product, dict):
        return {}
    raw = product.get("optionStock") or product.get("option_stock") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for label, qty in raw.items():
        label = str(label or "").strip()
        if label:
            out[label] = _qty(qty)
    return out


def option_price_ngn(product, label):
    """The Naira price of one option, falling back to the product price."""
    if not isinstance(product, dict):
        return 0
    raw = product.get("optionPrices") or product.get("option_prices") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    if isinstance(raw, dict):
        wanted = option_key(label)
        for key, value in raw.items():
            if option_key(key) == wanted:
                price = _int(value)
                if price > 0:
                    return price
    return base_price_ngn(product)


def base_price_ngn(product):
    """The product's own Naira price."""
    if not isinstance(product, dict):
        return 0
    for key in ("priceNgn", "price_ngn", "price"):
        value = _int(product.get(key))
        if value > 0:
            return value
    return 0


def product_qty(product):
    """Total units: the sum of variant stock, or the product's own quantity."""
    if not isinstance(product, dict):
        return 0
    options = option_stock(product)
    if options:
        return sum(options.values())
    for key in ("stock_quantity", "stockQuantity", "stock"):
        raw = product.get(key)
        if raw is not None and str(raw).strip() != "":
            return _qty(raw)
    return 0


# ------------------------------------------------------------- watchdog OOS guard
def oos_key(product_id, option=""):
    """The blocklist key for one product/option pair."""
    return f"{str(product_id or '').strip()}::{option_key(option)}"


def blocked_options(oos, product_id):
    """Option keys the watchdog has seen go out of stock for this product.

    ``oos`` is the watchdog's durable out-of-stock set - a collection of
    ``"<product id>::<option key>"`` strings, where an empty option key blocks
    the whole product. Returns the set of blocked option keys (which may be
    empty). A missing or unreadable blocklist blocks nothing: the guard fails
    open to the database, never to stale frontend state.
    """
    if not oos:
        return set()
    pid = str(product_id or "").strip()
    if not pid:
        return set()
    blocked = set()
    for entry in oos:
        text = str(entry or "")
        if "::" not in text:
            continue
        entry_id, _, option = text.partition("::")
        if entry_id.strip() != pid:
            continue
        blocked.add(option_key(option))
    return blocked


def is_blocked(oos, product_id, option=""):
    """True when the watchdog has marked this product/option out of stock."""
    return option_key(option) in blocked_options(oos, product_id)


# Mirrors tools/catalog_watchdog.STOCK_STATE_KEY: the watchdog writes its stock
# reading there, and broadcasts read it back. Kept as a literal here rather
# than imported so this module never drags the watchdog (and its network
# helpers) into the request path.
WATCHDOG_STOCK_STATE_KEY = "watchdog_stock_state_json"


def watchdog_out_of_stock():
    """The watchdog's durable out-of-stock blocklist, or ``[]``.

    Read from ``growth_settings``, the same key/value map the app itself uses,
    so "out of stock" means one thing to the storefront, the watchdog and the
    broadcast. Returns ``[]`` when nothing has been measured yet or Supabase
    is unreachable: the guard then fails open to the database, which is still
    the truthful source, rather than blocking everything on a read error.
    """
    try:
        import supabase_store
        settings = supabase_store.load_growth_settings() or {}
        raw = settings.get(WATCHDOG_STOCK_STATE_KEY)
        if raw is None or raw == "":
            return []
        data = json.loads(raw) if isinstance(raw, str) else raw
        out = (data or {}).get("outOfStock") if isinstance(data, dict) else None
        if not isinstance(out, list):
            return []
        return [str(x).strip() for x in out if str(x or "").strip()]
    except Exception:
        return []


# ------------------------------------------------------------------ availability
def available_options(product, *, oos=None):
    """``[(label, quantity)]`` for every variant that can actually be sold.

    A variant is excluded when its quantity is 0 or when the watchdog has
    watched it drop to zero - the watchdog wins even if the row here still
    carries a stale positive number, which is the whole point of the guard.
    """
    options = option_stock(product)
    if not options:
        # No variants: the product is either in stock or not.
        if product_qty(product) > 0 and not is_blocked(oos, (product or {}).get("id")):
            return [(NO_OPTION_KEY, product_qty(product))]
        return []
    out = []
    pid = (product or {}).get("id")
    for label, qty in options.items():
        if qty <= 0:
            continue
        if is_blocked(oos, pid, label):
            continue
        out.append((label, qty))
    # Keep the owner's own ordering (dict order), not an alphabetical sort.
    return out


def is_postable(product, *, oos=None):
    """True when at least one variant (or the product itself) is sellable."""
    if not isinstance(product, dict):
        return False
    if str(product.get("online")).strip().casefold() in ("false", "0", "no"):
        return False
    if product.get("deleted") or product.get("deletedAt"):
        return False
    return bool(available_options(product, oos=oos))


# ------------------------------------------------------------------- formatting
def category_label(product):
    text = str((product or {}).get("category") or "").strip()
    return text or CATEGORY_FALLBACK


def _line_view(product, *, oos=None, rate=None, base=""):
    """One product rendered for the post, or ``None`` when it is not sellable."""
    options = available_options(product, oos=oos)
    if not options:
        return None
    pid = product.get("id")
    labels = [label for label, _ in options if label]
    price = option_price_ngn(product, labels[0]) if labels else base_price_ngn(product)
    return {
        "id": pid,
        "name": str(product.get("name") or product.get("nameFr") or "Item").strip(),
        "category": category_label(product),
        "slug": str(product.get("slug") or "").strip(),
        "url": product_url(product.get("slug"), base),
        "priceNgn": price,
        "priceCfa": cfa_for(price, rate),
        "priceText": money(price, rate),
        "options": [{"label": label, "qty": qty} for label, qty in options],
        "quantity": sum(qty for _, qty in options),
    }


def _option_text(view):
    options = [o for o in view["options"] if o["label"]]
    if not options:
        return ""
    parts = [f"{o['label']} ({o['qty']})" if o["qty"] > 0 else o["label"]
             for o in options]
    return "Colours: " + ", ".join(parts)


def build_post(products, *, oos=None, rate=None, title=DEFAULT_TITLE,
               footer=DEFAULT_FOOTER, base="", include_date=True,
               max_products=MAX_PRODUCTS):
    """Build the evening broadcast copy.

    Returns a dict with the markdown post (``text``, WhatsApp/Telegram ready),
    a ``plain`` variant with no formatting markers, the ``lines`` the post was
    built from, and the counts an admin needs to trust it:

    ``{"text", "plain", "lines", "productCount", "optionCount",
       "categories", "rate", "skipped", "generatedAt"}``

    ``skipped`` lists products that were left out and why, so the owner can see
    the guard working rather than wondering where a product went.
    """
    rate = float(rate) if rate else active_rate()
    base = base or store_base()
    views, skipped = [], []

    for product in products or []:
        if not isinstance(product, dict):
            continue
        if str(product.get("online")).strip().casefold() in ("false", "0", "no"):
            skipped.append({"id": product.get("id"), "name": product.get("name"),
                            "reason": "hidden"})
            continue
        if product.get("deleted") or product.get("deletedAt"):
            skipped.append({"id": product.get("id"), "name": product.get("name"),
                            "reason": "deleted"})
            continue
        view = _line_view(product, oos=oos, rate=rate, base=base)
        if view is None:
            blocked = blocked_options(oos, product.get("id"))
            reason = "out of stock (watchdog)" if blocked else "out of stock"
            skipped.append({"id": product.get("id"), "name": product.get("name"),
                            "reason": reason})
            continue
        views.append(view)

    views.sort(key=lambda v: (v["category"].casefold(), v["name"].casefold()))
    if max_products and max_products > 0:
        views = views[:max_products]

    # Group by category, preserving first-seen order.
    order, grouped = [], {}
    for view in views:
        if view["category"] not in grouped:
            grouped[view["category"]] = []
            order.append(view["category"])
        grouped[view["category"]].append(view)

    stamp = datetime.datetime.now().strftime("%d %b %Y")
    lines, plain_lines = [], []
    lines.append(f"*{title}*")
    plain_lines.append(title)
    if include_date:
        header = f"_Verified in stock · {stamp}_"
        lines.append(header)
        plain_lines.append(f"Verified in stock · {stamp}")

    number = 0
    for category in order:
        lines.append("")
        plain_lines.append("")
        lines.append(f"*{category}*")
        plain_lines.append(category)
        for view in grouped[category]:
            number += 1
            lines.append(f"{number}. *{view['name']}*")
            plain_lines.append(f"{number}. {view['name']}")
            options = _option_text(view)
            if options:
                lines.append(f"   {options}")
                plain_lines.append(f"   {options}")
            if view["priceText"]:
                lines.append(f"   {view['priceText']}")
                plain_lines.append(f"   {view['priceText']}")
            if view["url"]:
                lines.append(f"   {view['url']}")
                plain_lines.append(f"   {view['url']}")
    if footer:
        lines.append("")
        lines.append(footer)
        plain_lines.append("")
        plain_lines.append(footer)

    return {
        "text": "\n".join(lines).strip(),
        "plain": "\n".join(plain_lines).strip(),
        "lines": views,
        "productCount": len(views),
        "optionCount": sum(len(v["options"]) for v in views),
        "categories": order,
        "rate": rate,
        "skipped": skipped,
        "skippedCount": len(skipped),
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def build_post_from_products(products, *, oos=None, rate=None, **kwargs):
    """Convenience wrapper: never raises, always returns a usable post."""
    try:
        return build_post(products, oos=oos, rate=rate, **kwargs)
    except Exception as exc:                            # pragma: no cover
        return {"text": "", "plain": "", "lines": [], "productCount": 0,
                "optionCount": 0, "categories": [], "rate": rate or active_rate(),
                "skipped": [], "skippedCount": 0,
                "error": f"Could not build the broadcast: {exc}",
                "generatedAt": datetime.datetime.now().isoformat(timespec="seconds")}
