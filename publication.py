"""Publication policy: may this product row appear on the public storefront?

One function, ``decide(product)``, is the single source of truth for the
question. It is used by:

* ``catalog.upsert`` - so a product saved from Admin is published only when
  it is complete, and stays offline until it is;
* the read-only publication audit / SQL generator in ``docs/``;
* the tests.

The policy (operator decision, 2026-09-08):

    publish  <=>  not a test fixture
              and not explicitly operator-offline
              and a real, non-placeholder image
              and priceNgn > 0 and priceCfa > 0
              and stock_quantity is a non-negative integer (0 = out of stock,
                  still visible; None / not a number = incomplete)

Anything else stays ``online = false``. Nothing here deletes, renames or
touches prices, stock or images: the only field the policy ever influences is
``online``.
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))

PLACEHOLDER_STEM = "_placeholder"

# pytest fixture ids that have leaked into the production table. Never live.
FIXTURE_ID_RE = re.compile(r"^jau-(stock|mirror|unit|sync|opt)")

# Explicit operator decisions. Each entry keeps the row intact - only
# ``online`` is affected, and only by the reviewed SQL, never automatically.
OPERATOR_OFFLINE = {
    "wix-001": ("placeholder image and stock_quantity=0; flagged online "
                "before it was ready"),
    "wix-012": ("priceNgn=0 is not a valid retail price; stays offline until "
                "a valid priceNgn is supplied"),
}

# A complete public object URL under the public `uploads` bucket.
PUBLIC_UPLOADS_URL_RE = re.compile(
    r"^https://[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}"
    r"/storage/v1/object/public/uploads/\S+$", re.IGNORECASE)

# Buckets, in the order a reviewer reads them.
BUCKETS = ("live", "operator_offline", "fixture", "placeholder_only",
           "no_image", "incomplete")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                                   # NaN
        return None
    return f


def _int(v):
    if isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def image_status(product) -> tuple:
    """(status, value) for the product's primary image.

    status is one of:
      "public"      - complete https URL under the public uploads bucket
      "repo"        - a committed file under <repo>/images/ that exists
      "placeholder" - the branded placeholder
      "relative"    - a /uploads/<key> or other same-origin path we cannot
                      verify from here (needs confirmation)
      "external"    - a URL on some other host (never served)
      "blank"       - nothing at all
    """
    raw = str((product or {}).get("image_url")
              or (product or {}).get("image") or "").strip()
    if not raw:
        return "blank", ""
    if PLACEHOLDER_STEM in os.path.basename(raw):
        return "placeholder", raw
    if PUBLIC_UPLOADS_URL_RE.match(raw):
        return "public", raw
    if raw.startswith(("http://", "https://", "data:", "blob:")):
        return "external", raw
    if raw.startswith("/"):
        return "relative", raw
    if ".." in raw.split("/"):
        return "relative", raw
    full = os.path.realpath(os.path.join(ROOT, raw))
    images_root = os.path.realpath(os.path.join(ROOT, "images"))
    if full.startswith(images_root + os.sep) and os.path.isfile(full):
        return "repo", raw
    return "relative", raw


def has_real_image(product) -> bool:
    return image_status(product)[0] in ("public", "repo")


def decide(product, fixtures=True) -> dict:
    """Classify one product row under the publication policy.

    ``fixtures=True`` (audit) also applies the leaked-fixture id rule;
    ``catalog.upsert`` passes ``fixtures=False`` so a save is judged on
    completeness alone.

    Returns::

        {"id": ..., "online": bool, "bucket": <one of BUCKETS>,
         "reasons": [...], "image_status": ..., "priceNgn": ..., "priceCfa": ...,
         "stock_quantity": ...}

    ``online`` is True only for bucket "live".
    """
    p = dict(product or {})
    pid = str(p.get("id") or "").strip()
    status, img = image_status(p)
    ngn = _num(p.get("priceNgn"))
    cfa = _num(p.get("priceCfa"))
    stock_raw = p.get("stock_quantity")
    if stock_raw is None:
        stock_raw = p.get("stock")
    stock = _int(stock_raw)

    out = {"id": pid, "online": False, "bucket": "incomplete", "reasons": [],
           "codes": [], "image_status": status, "image": img,
           "priceNgn": p.get("priceNgn"), "priceCfa": p.get("priceCfa"),
           "stock_quantity": stock}

    if fixtures and FIXTURE_ID_RE.match(pid):
        out["bucket"] = "fixture"
        out["reasons"].append("test fixture id")
        out["codes"].append("fixture")
        return out
    if pid in OPERATOR_OFFLINE:
        out["bucket"] = "operator_offline"
        out["reasons"].append("operator decision: " + OPERATOR_OFFLINE[pid])
        out["codes"].append("operator_offline")
        return out

    reasons, codes = [], []
    if status == "placeholder":
        reasons.append("placeholder image")
        codes.append("placeholder_image")
    elif status in ("blank", "external", "relative"):
        reasons.append(f"no verifiable image ({status})")
        codes.append("no_image" if status == "blank" else status + "_image")
    if ngn is None or ngn <= 0:
        reasons.append(f"invalid priceNgn={p.get('priceNgn')!r}")
        codes.append("invalid_price")
    if cfa is None or cfa <= 0:
        reasons.append(f"invalid priceCfa={p.get('priceCfa')!r}")
        if "invalid_price" not in codes:
            codes.append("invalid_price")
    if stock is None or stock < 0:
        reasons.append(f"invalid stock_quantity={stock_raw!r}")
        codes.append("invalid_stock")

    if reasons:
        out["reasons"] = reasons
        out["codes"] = codes
        if status == "placeholder" and len(reasons) == 1:
            out["bucket"] = "placeholder_only"
        elif status in ("blank", "external", "relative") and len(reasons) == 1:
            out["bucket"] = "no_image"
        else:
            out["bucket"] = "incomplete"
        return out

    out["online"] = True
    out["bucket"] = "live"
    return out


def publishable(product) -> bool:
    """True when the policy says this row may be online."""
    return bool(decide(product)["online"])


def classify(products) -> dict:
    """Bucket a list of rows. Returns {bucket: [decision, ...]} sorted by id."""
    buckets = {b: [] for b in BUCKETS}
    for p in products or []:
        d = decide(p)
        buckets[d["bucket"]].append(d)
    for b in buckets:
        buckets[b].sort(key=lambda d: d["id"])
    return buckets
