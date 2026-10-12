#!/usr/bin/env python3
"""Single-run supplier-cost backfill — fetches missing supplier costs.

Purpose
-------
Fill in the supplier COST of products/variants whose owner never typed one,
by reading the price off the supplier page each product (or per-option link)
points at. This is the replacement for the old always-on background sync:
**one administrative run, on demand, then exit.** It is safe to run anywhere
(your laptop, a Render one-off shell) because it holds no locks, spawns no
loops and writes nothing unless you pass ``--apply``.

What it fills
-------------
* ``supplierCost``          — product-level cost, only when empty.
* ``optionSupplierCost[k]`` — per-variant cost, only when that key is empty.

What it never touches
---------------------
* Selling prices (``priceNgn`` / ``optionPrices``) — a supplier's price is a
  COST signal, not the shop's margin (same rule as supplier_watchdog).
* Any value an owner already entered: existing costs always win.
* Rows without a supplier URL, and URLs that fail SSRF/private-address
  checks (the same ``security.safe_fetch`` guard the app uses).

Usage
-----
    python scripts/backfill_variant_prices.py             # dry run (default)
    python scripts/backfill_variant_prices.py --apply     # write the fills
    python scripts/backfill_variant_prices.py --limit 20  # first 20 rows
    python scripts/backfill_variant_prices.py --id jau-abc
    python scripts/backfill_variant_prices.py --status    # coverage report

Exit code is 0 even when some pages fail (failures are reported per row);
it is non-zero only when the catalogue itself could not be read (or an
--apply write failed outright), so a cron/CI caller can trust it.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---------------------------------------------------------------- parsing
# A supplier page shows its price with a Naira marker: "₦2,500", "NGN 2,500",
# "N2,500", "2,500 naira". The FIRST confident positive number wins; numbers
# without a currency marker are ignored (a "3 for 2" promo line must never
# become a cost of 3).
_NGN_MARKERS = ("₦", "ngn", "naira")
_AMOUNT_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?|\d+)(?![\d])")
_STRIP_TAGS_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_MAX_SCAN_CHARS = 200_000


def extract_naira_price(html: str):
    """The first Naira-marked amount on a supplier page, or None."""
    text = _STRIP_TAGS_RE.sub(" ", html or "")
    text = _TAG_RE.sub(" ", text)
    text = text[:_MAX_SCAN_CHARS]
    lowered = text.lower()
    best = None
    for match in _AMOUNT_RE.finditer(text):
        start = max(0, match.start() - 24)
        context = lowered[start:match.end() + 8]
        if not any(marker in context for marker in _NGN_MARKERS):
            continue
        try:
            value = float(match.group(1).replace(",", "").replace(" ", ""))
        except ValueError:
            continue
        if value <= 0 or value > 10**9:
            continue
        candidate = int(round(value))
        if best is None or candidate < best:
            # Suppliers list promo/crossed-out prices above the real one;
            # the LOWEST naira figure in view is the safest cost estimate.
            best = candidate
    return best


def _is_configured(url) -> bool:
    return bool(url) and isinstance(url, str) and url.strip().lower().startswith(("http://", "https://"))


def _variant_keys(product):
    """Every per-option supplier link, as {key: url} with owner spellings."""
    out = {}
    raw = product.get("optionSupplierSku") or product.get("optionSupplierUrls") \
        or product.get("option_supplier_urls") or {}
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            url = value[0] if isinstance(value, (list, tuple)) and value else value
            if _is_configured(url):
                out[str(key)] = str(url).strip()
    return out


def _page_cost_cache(_unused=None):
    """Memoize one URL -> parsed price per run (variants share listings)."""
    cache = {}

    def wrapped(url):
        if url not in cache:
            try:
                import security
                cache[url] = extract_naira_price(security.safe_fetch_text(url))
            except Exception as exc:            # SSRF guard, timeouts, 404s…
                cache[url] = ("error", str(exc)[:160])
        return cache[url]

    return wrapped


def collect_plan(products, fetch_page):
    """Walk the catalogue and decide what WOULD be filled. Never writes."""
    fills = []
    failures = []
    scanned = 0
    for product in products:
        if not isinstance(product, dict) or not product.get("id"):
            continue
        scanned += 1
        pid = str(product["id"])
        entry = {"id": pid, "name": str(product.get("name") or "")[:80],
                 "fills": {}, "errors": []}

        # 1) Product-level cost.
        if not product.get("supplierCost") and _is_configured(
                product.get("supplierSku") or product.get("supplierUrl")
                or product.get("supplier_url")):
            url = str(product.get("supplierSku") or product.get("supplierUrl")
                      or product.get("supplier_url")).strip()
            result = fetch_page(url)
            if isinstance(result, tuple):
                entry["errors"].append(f"product: {result[1]}")
            elif result:
                entry["fills"]["supplierCost"] = result
            else:
                entry["errors"].append(f"product: no naira price found ({url})")

        # 2) Per-variant costs — only the EMPTY keys.
        costs = product.get("optionSupplierCost") or {}
        if isinstance(costs, str):
            import json
            try:
                costs = json.loads(costs)
            except (TypeError, ValueError):
                costs = {}
        costs = costs if isinstance(costs, dict) else {}
        for key, url in _variant_keys(product).items():
            existing = costs.get(key)
            if isinstance(existing, (int, float)) and existing:
                continue
            result = fetch_page(url)
            if isinstance(result, tuple):
                entry["errors"].append(f"{key}: {result[1]}")
            elif result:
                entry["fills"].setdefault("optionSupplierCost", {})[key] = result
            else:
                entry["errors"].append(f"{key}: no naira price found ({url})")

        if entry["fills"]:
            fills.append(entry)
        if entry["errors"]:
            failures.append(entry)
    return scanned, fills, failures


def _apply(products, plan_entries, actor="backfill-script"):
    """Write the fills through catalog.upsert (the canonical save path)."""
    import catalog
    by_id = {entry["id"]: entry for entry in plan_entries}
    written = 0
    for product in products:
        if not isinstance(product, dict):
            continue
        entry = by_id.get(str(product.get("id")))
        if not entry or not entry["fills"]:
            continue
        payload = dict(product)
        if "supplierCost" in entry["fills"]:
            payload["supplierCost"] = entry["fills"]["supplierCost"]
        if "optionSupplierCost" in entry["fills"]:
            payload["optionSupplierCost"] = {
                **(product.get("optionSupplierCost") or {}),
                **entry["fills"]["optionSupplierCost"],
            }
        saved, state, _ = catalog.upsert(payload, actor)
        if saved is not None:
            written += 1
        else:
            print(f"[backfill] {entry['id']}: save rejected ({state or 'error'})")
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Single-run supplier-cost backfill (dry run by default).")
    parser.add_argument("--apply", action="store_true",
                        help="write the fills (default: dry run, nothing saved)")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N products with fills")
    parser.add_argument("--id", action="append", default=[],
                        help="only this product id (repeatable)")
    parser.add_argument("--status", action="store_true",
                        help="just report current supplier-cost coverage")
    args = parser.parse_args(argv)

    import catalog as catalog_mod
    try:
        products = catalog_mod.merged(include_hidden=True)
    except Exception as exc:
        print(f"[backfill] catalogue unreadable: {exc}")
        return 2
    if not products:
        print("[backfill] catalogue is empty; nothing to do.")
        return 0
    if args.id:
        wanted = {str(x).strip() for x in args.id}
        products = [p for p in products if str(p.get("id")) in wanted]
        if not products:
            print(f"[backfill] none of {sorted(wanted)} are in the catalogue.")
            return 1

    total = len(products)
    with_cost = sum(1 for p in products if isinstance(p, dict) and p.get("supplierCost"))
    with_variants = sum(1 for p in products
                        if isinstance(p, dict)
                        and any(isinstance(v, (int, float)) and v
                                for v in (p.get("optionSupplierCost") or {}).values())
                        ) if products else 0

    if args.status:
        print(f"catalogue rows            : {total}")
        print(f"product-level cost set    : {with_cost}")
        print(f"rows with any variant cost: {with_variants}")
        return 0

    fetched = _page_cost_cache(None)

    def fetch_page(url):
        return fetched(url)

    scanned, fills, failures = collect_plan(products, fetch_page)
    print(f"[backfill] scanned {scanned} row(s); "
          f"{len(fills)} fillable, {len(failures)} with fetch errors.")
    for entry in fills[:args.limit or len(fills)]:
        detail = []
        if "supplierCost" in entry["fills"]:
            detail.append(f"supplierCost=NGN {entry['fills']['supplierCost']:,}")
        for key, value in (entry["fills"].get("optionSupplierCost") or {}).items():
            detail.append(f"{key}=NGN {value:,}")
        print(f"  {entry['id']}  {entry['name']}: " + "; ".join(detail))
    for entry in failures:
        for message in entry["errors"][:4]:
            print(f"  ! {entry['id']}  {entry['name']}: {message}")

    if args.limit and len(fills) > args.limit:
        print(f"[backfill] --limit {args.limit}: the remaining "
              f"{len(fills) - args.limit} row(s) were NOT processed this run.")
        fills = fills[:args.limit]

    if not args.apply:
        print("[backfill] DRY RUN complete — nothing was written. "
              "Re-run with --apply to save these figures.")
        return 0
    by_id = {entry["id"]: entry for entry in fills}
    written = _apply(products, [{"id": k, "fills": v["fills"], "name": "", "errors": []}
                                for k, v in by_id.items()])
    print(f"[backfill] saved supplier costs on {written} row(s).")
    return 0 if written == len(fills) else 1


if __name__ == "__main__":
    raise SystemExit(main())
