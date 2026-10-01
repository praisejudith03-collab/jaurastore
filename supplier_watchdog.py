"""In-process supplier stock watchdog.

The admin can attach one supplier URL to a product that has several Jaura
variants (Colour, Size, Scent, Flavour, ...). This module fetches that supplier
page in small batches from the main web service, extracts whatever variant
availability the page exposes (JSON-LD/product JSON/data attributes/plain
HTML), and mirrors only the matching Jaura variant's stock flag.

Design goals:
  * no separate Render worker/service;
  * one URL fetched once per tick even when it feeds many variants;
  * bounded memory/network use (small batches, byte cap, short timeout);
  * fuzzy alias matching ("Dark Brown" supplier == "Chocolate" Jaura,
    "Lime" == "Lemon");
  * isolated updates: a supplier's sold-out Red never zeros Blue.

The scraper is intentionally generic and conservative. If a page cannot be
parsed with confidence, it records an admin-visible warning and leaves stock as
it was rather than guessing.
"""
from __future__ import annotations

import datetime
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

import catalog as catalog_mod

DEFAULT_BATCH_SIZE = 8
MAX_BYTES = 700_000
FETCH_TIMEOUT = 8
CACHE_TTL_SECONDS = 20 * 60
USER_AGENT = "jaurastore-supplier-watchdog/1.0 (+https://jaurastore.com.ng)"

# Products that were checked recently. Kept in-process only; losing it on a
# deploy merely lets the next tick check a product earlier, which is harmless.
_last_checked: Dict[str, float] = {}
_url_cache: Dict[str, Tuple[float, str]] = {}
_last_summary: Dict[str, Any] = {
    "at": "",
    "checked": 0,
    "updated": 0,
    "warnings": 0,
    "lastError": "",
}

_WORD_RE = re.compile(r"[a-z0-9]+")
_URL_RE = re.compile(r"^https?://", re.I)

# Canonical colour/flavour families. Matching is symmetric inside a family, so
# supplier "Dark Brown" maps to Jaura "Chocolate" and supplier "Lime" maps to
# Jaura "Lemon" without the owner needing to enter duplicate names.
_ALIAS_GROUPS = [
    ("black", "jet black", "matte black", "onyx"),
    ("white", "ivory", "cream", "off white", "off-white"),
    ("brown", "dark brown", "light brown", "chocolate", "coffee", "mocha", "cocoa", "tan", "camel"),
    ("beige", "nude", "skin", "khaki", "champagne"),
    ("red", "wine", "burgundy", "maroon", "ruby"),
    ("pink", "rose", "rosy", "blush", "fuchsia", "hot pink"),
    ("purple", "violet", "lavender", "lilac"),
    ("blue", "navy", "royal blue", "sky blue", "aqua", "teal", "turquoise"),
    ("green", "olive", "mint", "emerald"),
    ("yellow", "lemon", "lime", "gold", "mustard"),
    ("orange", "peach", "coral"),
    ("grey", "gray", "silver", "ash"),
    ("clear", "transparent"),
]
_ALIAS_BY_FOLD: Dict[str, set] = {}
for _group in _ALIAS_GROUPS:
    folded = {"".join(_WORD_RE.findall(x.lower())) for x in _group}
    for _name in folded:
        _ALIAS_BY_FOLD.setdefault(_name, set()).update(folded)

_OUT_TERMS = (
    "out of stock", "sold out", "unavailable", "currently unavailable",
    "not available", "no stock", "stock: 0", "quantity: 0", "disabled",
)
_IN_TERMS = (
    "in stock", "available", "add to cart", "buy now", "quantity available",
    "qty available", "available now",
)
_AVAILABILITY_OUT = ("outofstock", "soldout", "discontinued", "preorder")
_AVAILABILITY_IN = ("instock", "in stock", "available", "limitedavailability")


def _now() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def enabled() -> bool:
    raw = os.environ.get("SUPPLIER_WATCHDOG_ENABLED", os.environ.get("SUPPLIER_WATCHDOG", "1"))
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def summary() -> Dict[str, Any]:
    return dict(_last_summary)


def _clean_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or not _URL_RE.search(raw):
        return ""
    return raw[:1000]


def product_supplier_urls(product: Dict[str, Any]) -> List[str]:
    """Supplier URLs attached to a product, with the product-level URL first."""
    urls: List[str] = []
    for key in ("supplierSku", "supplierUrl", "supplier_url", "supplierURL", "supplier_sku"):
        url = _clean_url((product or {}).get(key))
        if url and url not in urls:
            urls.append(url)
    mapping = (product or {}).get("optionSupplierSku") or (product or {}).get("option_supplier_sku") or {}
    if isinstance(mapping, dict):
        for value in mapping.values():
            values = value if isinstance(value, (list, tuple)) else [value]
            for item in values:
                url = _clean_url(item)
                if url and url not in urls:
                    urls.append(url)
    return urls


def variant_labels(product: Dict[str, Any]) -> List[str]:
    """Variant values Jaura sells independently for this product."""
    labels: List[str] = []
    seen = set()
    opts = product.get("options") if isinstance(product, dict) else []
    if isinstance(opts, list):
        for opt in opts[:8]:
            if not isinstance(opt, dict):
                continue
            title = str(opt.get("title") or "Option").strip() or "Option"
            values = opt.get("values") if isinstance(opt.get("values"), list) else []
            for value in values[:80]:
                label = str(value or "").strip()
                if not label:
                    continue
                # First option values are the keys currently used by optionStock;
                # full "Colour: Red" labels are also accepted by the matcher.
                candidates = [label, f"{title}: {label}"]
                for cand in candidates:
                    folded = fold(cand)
                    if folded and folded not in seen:
                        seen.add(folded)
                        labels.append(cand)
    for value in product.get("colors") or []:
        label = str(value or "").strip()
        folded = fold(label)
        if label and folded and folded not in seen:
            seen.add(folded)
            labels.append(label)
    os_map = product.get("optionStock") if isinstance(product.get("optionStock"), dict) else {}
    for value in os_map.keys():
        label = str(value or "").strip()
        folded = fold(label)
        if label and folded and folded not in seen:
            seen.add(folded)
            labels.append(label)
    return labels


def fold(value: Any) -> str:
    return "".join(_WORD_RE.findall(str(value or "").lower()))


def _tokens(value: Any) -> set:
    text = str(value or "").lower()
    if ":" in text:
        text = text.split(":", 1)[1]
    return set(_WORD_RE.findall(text))


def aliases(value: Any) -> set:
    f = fold(value)
    out = {f} if f else set()
    out.update(_ALIAS_BY_FOLD.get(f, set()))
    for token in _tokens(value):
        tf = fold(token)
        out.add(tf)
        out.update(_ALIAS_BY_FOLD.get(tf, set()))
    return {x for x in out if x}


def option_value_only(label: Any) -> str:
    text = str(label or "").strip()
    if ":" in text:
        return text.split(":", 1)[1].strip()
    return text


def match_score(jaura_label: str, supplier_label: str) -> int:
    j, s = option_value_only(jaura_label), option_value_only(supplier_label)
    jf, sf = fold(j), fold(s)
    if not jf or not sf:
        return 0
    if jf == sf:
        return 100
    ja, sa = aliases(j), aliases(s)
    if ja & sa:
        return 90
    jt, st = _tokens(j), _tokens(s)
    if jt and st and (jt <= st or st <= jt):
        return 80
    if jf in sf or sf in jf:
        return 70
    overlap = jt & st
    if overlap:
        return min(65, 30 + len(overlap) * 10)
    return 0


def _availability_from_value(value: Any) -> Optional[int]:
    """Return 0/1/qty when a parsed field clearly describes stock."""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value or "").strip().lower()
    if not text:
        return None
    compact = re.sub(r"[^a-z0-9]+", "", text)
    if any(term.replace(" ", "") in compact for term in _AVAILABILITY_OUT):
        return 0
    if any(term in text for term in _OUT_TERMS):
        return 0
    # Explicit quantities win over generic availability words.
    m = re.search(r"(?:stock|qty|quantity|inventory)[^0-9]{0,16}(\d{1,7})", text)
    if m:
        return max(0, int(m.group(1)))
    if any(term.replace(" ", "") in compact for term in _AVAILABILITY_IN):
        return 1
    if any(term in text for term in _IN_TERMS):
        return 1
    return None


def _label_from_dict(row: Dict[str, Any]) -> str:
    bits: List[str] = []
    for key in ("name", "title", "label", "variant", "option", "option1", "option2", "option3",
                "color", "colour", "flavor", "flavour", "size", "value"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            bits.append(value.strip())
    opts = row.get("options") or row.get("attributes")
    if isinstance(opts, dict):
        for value in opts.values():
            if isinstance(value, str) and value.strip():
                bits.append(value.strip())
    elif isinstance(opts, list):
        for item in opts[:6]:
            if isinstance(item, str) and item.strip():
                bits.append(item.strip())
            elif isinstance(item, dict):
                value = item.get("value") or item.get("name") or item.get("label")
                if isinstance(value, str) and value.strip():
                    bits.append(value.strip())
    seen, out = set(), []
    for bit in bits:
        f = fold(bit)
        if f and f not in seen:
            seen.add(f)
            out.append(bit)
    return " · ".join(out)


def _stock_from_dict(row: Dict[str, Any]) -> Optional[int]:
    for key in ("quantity", "qty", "stock", "inventory", "inventory_quantity",
                "quantityAvailable", "available_quantity", "stock_quantity"):
        if key in row:
            qty = _availability_from_value(row.get(key))
            if qty is not None:
                return qty
    for key in ("available", "isAvailable", "inStock", "in_stock", "soldOut",
                "sold_out", "disabled", "availability", "stockStatus", "stock_status"):
        if key not in row:
            continue
        value = row.get(key)
        if key in ("soldOut", "sold_out", "disabled") and isinstance(value, bool):
            return 0 if value else 1
        qty = _availability_from_value(value)
        if qty is not None:
            return qty
    offers = row.get("offers")
    if isinstance(offers, dict):
        return _stock_from_dict(offers)
    if isinstance(offers, list):
        for offer in offers:
            if isinstance(offer, dict):
                qty = _stock_from_dict(offer)
                if qty is not None:
                    return qty
    return None


def _walk_json(node: Any, rows: List[Dict[str, Any]], depth: int = 0) -> None:
    if depth > 8 or len(rows) > 300:
        return
    if isinstance(node, dict):
        label = _label_from_dict(node)
        qty = _stock_from_dict(node)
        if label and qty is not None:
            rows.append({"label": label, "qty": max(0, int(qty)), "source": "json"})
        for key in ("variants", "variant", "products", "items", "offers", "options", "data", "nodes", "edges"):
            child = node.get(key)
            if child is not None:
                _walk_json(child, rows, depth + 1)
        # Some commerce apps put variant objects under arbitrary ids.
        for value in list(node.values())[:80]:
            if isinstance(value, (dict, list)):
                _walk_json(value, rows, depth + 1)
    elif isinstance(node, list):
        for item in node[:300]:
            _walk_json(item, rows, depth + 1)


def _json_blobs(text: str) -> Iterable[Any]:
    # application/ld+json and Next/Nuxt data scripts.
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", text, flags=re.I | re.S):
        body = html.unescape(m.group(1) or "").strip()
        if not body or len(body) > 500_000:
            continue
        candidates = [body]
        # window.__DATA__ = {...}; fallback: parse the first balanced object.
        eq = re.search(r"=\s*([\[{].*[\]}])\s*;?\s*$", body, flags=re.S)
        if eq:
            candidates.append(eq.group(1))
        for cand in candidates:
            try:
                yield json.loads(cand)
                break
            except Exception:
                continue


def parse_supplier_variants(text: str, jaura_labels: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """Extract supplier variant availability from HTML/text.

    Output rows are {label, qty, source}. qty=0 means out, qty>=1 means in or
    exact quantity when the page exposed one.
    """
    rows: List[Dict[str, Any]] = []
    for blob in _json_blobs(text or ""):
        _walk_json(blob, rows)

    # Structured HTML attributes frequently contain enough variant info even
    # when the surrounding JavaScript is minified.
    for m in re.finditer(
        r"(?:data-(?:variant|option|value|color|colour|size|flavo[u]?r)|aria-label|title)\s*=\s*['\"]([^'\"]{1,120})['\"][^>]{0,500}",
        text or "", flags=re.I | re.S):
        label = html.unescape(m.group(1)).strip()
        chunk = html.unescape(m.group(0)).lower()
        qty = _availability_from_value(chunk)
        if label and qty is not None:
            rows.append({"label": label, "qty": qty, "source": "html-attr"})

    # Conservative plain-text fallback: only for the Jaura labels we know, and
    # only when stock words appear close to that label.
    if jaura_labels:
        plain = re.sub(r"<[^>]+>", " ", text or "")
        plain = html.unescape(re.sub(r"\s+", " ", plain))
        lowered = plain.lower()
        for label in jaura_labels:
            candidates = {option_value_only(label), label}
            candidates.update(_ALIAS_BY_FOLD.get(fold(option_value_only(label)), set()))
            for cand in sorted(candidates, key=len, reverse=True):
                if not cand:
                    continue
                search = str(cand).lower()
                idx = lowered.find(search)
                if idx < 0:
                    continue
                window = lowered[max(0, idx - 180): idx + len(search) + 220]
                qty = _availability_from_value(window)
                if qty is not None:
                    rows.append({"label": str(cand), "qty": qty, "source": "text"})
                    break

    # Dedupe by folded label, keeping an exact quantity/out-of-stock over a
    # generic boolean when possible.
    by_label: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        key = fold(label)
        qty = max(0, int(row.get("qty") or 0))
        incoming = {"label": label, "qty": qty, "source": row.get("source") or ""}
        prev = by_label.get(key)
        if prev is None:
            by_label[key] = incoming
            continue
        prev_source = str(prev.get("source") or "")
        incoming_source = str(incoming.get("source") or "")
        # A nearby-text match can accidentally see another variant's quantity;
        # never let that weaker fallback override structured JSON/attributes.
        if prev_source == "text" and incoming_source != "text":
            by_label[key] = incoming
        elif incoming_source == "text" and prev_source != "text":
            continue
        elif qty == 0 or qty > int(prev.get("qty") or 0):
            by_label[key] = incoming
    return list(by_label.values())


def fetch_url(url: str) -> str:
    url = _clean_url(url)
    if not url:
        return ""
    cached = _url_cache.get(url)
    if cached and cached[0] > time.time():
        return cached[1]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        data = resp.read(MAX_BYTES + 1)
    text = data[:MAX_BYTES].decode("utf-8", "replace")
    if len(_url_cache) > 128:
        _url_cache.clear()
    _url_cache[url] = (time.time() + CACHE_TTL_SECONDS, text)
    return text


def map_supplier_to_jaura(jaura_labels: List[str], supplier_rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for j in jaura_labels:
        best: Tuple[int, Optional[Dict[str, Any]]] = (0, None)
        for row in supplier_rows:
            score = match_score(j, str(row.get("label") or ""))
            if score > best[0]:
                best = (score, row)
        if best[0] >= 70 and best[1] is not None:
            out[j] = {**best[1], "score": best[0]}
    return out


def _stock_keys(product: Dict[str, Any], labels: List[str]) -> List[str]:
    os_map = product.get("optionStock") if isinstance(product.get("optionStock"), dict) else {}
    if os_map:
        return [str(k) for k in os_map.keys()]
    # Prefer first option values; optionStock is keyed by values, not "Colour: X".
    opts = product.get("options") if isinstance(product.get("options"), list) else []
    if opts and isinstance(opts[0], dict) and isinstance(opts[0].get("values"), list):
        return [str(v) for v in opts[0].get("values") or [] if str(v or "").strip()]
    return [option_value_only(x) for x in labels]


def sync_product(product: Dict[str, Any], actor: str = "supplier-watchdog") -> Tuple[bool, List[Dict[str, Any]]]:
    """Check one product. Returns (updated, warnings). Never raises."""
    warnings: List[Dict[str, Any]] = []
    p = dict(product or {})
    pid = str(p.get("id") or "").strip()
    urls = product_supplier_urls(p)
    if not pid or not urls:
        return False, warnings
    labels = variant_labels(p)
    keys = _stock_keys(p, labels)
    if not keys:
        return False, warnings

    # Single-URL multi-variant path: fetch product-level URL once, then match
    # all Jaura variant keys. Per-option URLs, when present, can augment it.
    supplier_rows: List[Dict[str, Any]] = []
    for url in urls[:4]:
        try:
            body = fetch_url(url)
            supplier_rows.extend(parse_supplier_variants(body, keys + labels))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            warnings.append(_warning(p, "supplier_fetch_failed", f"Could not fetch {url}: {exc}"))
        except Exception as exc:  # defensive: a supplier page must not kill the scheduler
            warnings.append(_warning(p, "supplier_parse_failed", f"Could not parse {url}: {exc}"))
    if not supplier_rows:
        warnings.append(_warning(p, "supplier_no_variants", "No supplier variant stock could be read; existing stock was left unchanged."))
        return False, warnings

    matched = map_supplier_to_jaura(keys, supplier_rows)
    if not matched:
        warnings.append(_warning(p, "supplier_no_matches", "Supplier variants did not confidently match this product's option names; existing stock was left unchanged."))
        return False, warnings

    current = p.get("optionStock") if isinstance(p.get("optionStock"), dict) else {}
    next_stock: Dict[str, int] = {}
    changed = False
    for key in keys:
        old = max(0, int(current.get(key, p.get("stock") or 0) or 0)) if current else max(0, int(p.get("stock") or 0))
        row = matched.get(key)
        if row is None:
            next_stock[key] = old
            continue
        qty = max(0, int(row.get("qty") or 0))
        # If the supplier only tells us "available", keep the owner's positive
        # count when there is one; otherwise mark it sellable with quantity 1.
        new_qty = 0 if qty <= 0 else (qty if qty > 1 else max(1, old))
        next_stock[key] = new_qty
        if new_qty != old:
            changed = True

    if not changed:
        return False, warnings
    row = {**p, "optionStock": next_stock, "stock": sum(next_stock.values()), "stock_quantity": sum(next_stock.values())}
    try:
        saved, _action, mirrored = catalog_mod.upsert(row, actor=actor)
        if not saved or mirrored is False:
            warnings.append(_warning(p, "supplier_save_failed", "Supplier stock was read but could not be saved to the catalogue."))
            return False, warnings
        return True, warnings
    except Exception as exc:
        warnings.append(_warning(p, "supplier_save_failed", f"Supplier stock was read but saving failed: {exc}"))
        return False, warnings


def _warning(product: Dict[str, Any], code: str, reason: str) -> Dict[str, Any]:
    return {
        "product_id": str(product.get("id") or ""),
        "product_name": str(product.get("name") or ""),
        "code": code,
        "reason": str(reason or "")[:500],
        "at": _now(),
    }


def _save_warnings(new_warnings: List[Dict[str, Any]]) -> None:
    if not new_warnings:
        return
    try:
        import supabase_store
        existing = [w for w in (supabase_store.load_supplier_sync_warnings() or [])
                    if isinstance(w, dict) and w.get("code") != "supplier_watchdog_ok"]
        by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in existing + new_warnings:
            key = (str(row.get("product_id") or ""), str(row.get("code") or ""))
            by_key[key] = row
        supabase_store.save_supplier_sync_warnings(list(by_key.values())[-1000:])
    except Exception:
        pass


def tick(limit: int = DEFAULT_BATCH_SIZE, min_interval_seconds: int = 60 * 60, logger=None) -> Dict[str, Any]:
    """Run one bounded supplier watchdog batch inside the web service."""
    global _last_summary
    if not enabled():
        _last_summary = {**_last_summary, "at": _now(), "checked": 0, "updated": 0, "warnings": 0}
        return dict(_last_summary)
    checked = updated = 0
    warnings: List[Dict[str, Any]] = []
    try:
        products = catalog_mod.merged(include_hidden=True)
    except Exception as exc:
        _last_summary = {"at": _now(), "checked": 0, "updated": 0, "warnings": 1, "lastError": str(exc)[:200]}
        return dict(_last_summary)
    now_ts = time.time()
    candidates = []
    for product in products or []:
        pid = str((product or {}).get("id") or "").strip()
        if not pid or not product_supplier_urls(product):
            continue
        if now_ts - float(_last_checked.get(pid) or 0) < min_interval_seconds:
            continue
        candidates.append(product)
        if len(candidates) >= max(1, int(limit or DEFAULT_BATCH_SIZE)):
            break
    for product in candidates:
        pid = str((product or {}).get("id") or "").strip()
        _last_checked[pid] = now_ts
        checked += 1
        ok, warn = sync_product(product)
        if ok:
            updated += 1
        warnings.extend(warn)
    _save_warnings(warnings)
    _last_summary = {
        "at": _now(),
        "checked": checked,
        "updated": updated,
        "warnings": len(warnings),
        "lastError": "",
    }
    if logger and (checked or updated or warnings):
        logger.info("supplier watchdog: checked=%s updated=%s warnings=%s", checked, updated, len(warnings))
    return dict(_last_summary)
