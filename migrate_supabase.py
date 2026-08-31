#!/usr/bin/env python3
"""Seed Supabase from the existing J Aura Store catalogue.

Run once (with SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY set) to push the
existing products into Supabase so it can become the source of truth:

    python3 migrate_supabase.py            # import seed.json + catalog.json
    python3 migrate_supabase.py --reset    # drop rows first (DANGEROUS)

The script reads the seed catalogue (data/seed.json -> the first 258 products)
and the admin overrides (data/catalog.json, applied on top), then upserts them
into the Supabase `products` table expected by catalog.py / supabase_store.py.

It never deletes existing Supabase rows unless --reset is passed. Run it before
switching on SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY so the shop has data to
serve.
"""
import os, sys, json, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config              # noqa: E402


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _load_seed():
    """Return a dict of products from data/seed.json, keyed by id."""
    seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "seed.json")
    try:
        with open(seed_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = []
    return {p.get("id"): p for p in data if isinstance(p, dict) and p.get("id")}


def _load_overrides():
    """Return admin products from data/catalog.json, keyed by id."""
    cat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "catalog.json")
    try:
        with open(cat_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    return {p.get("id"): p for p in (data.get("products") or [])
            if isinstance(p, dict) and p.get("id")}


def main():
    reset = "--reset" in sys.argv
    if not (Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY):
        print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (and .env) first.")
        sys.exit(1)

    from supabase import create_client
    from supabase_store import client
    sb = client()
    if sb is None:
        print("Could not initialise the Supabase client.")
        sys.exit(1)

    if reset:
        try:
            sb.table("products").delete().gt("id", "").execute()
        except Exception as exc:
            print(f"(reset) delete failed — continuing: {exc}")

    merged = _load_seed()
    merged.update(_load_overrides())

    # Reconcile every product's image against the repository so the Supabase
    # rows link to a path the browser can actually show: a committed repo
    # image (images/products/x.jpg) when it exists, else the original Wix CDN
    # photo (the real product picture), with the branded placeholder as the
    # offline fallback. See catalog.resolve_image().
    import catalog as catalog_mod
    merged = {pid: catalog_mod.resolve_image(p) for pid, p in merged.items()}

    rows = []
    now = _now()
    for pid, p in merged.items():
        # Keep Supabase rows to the same fields the storefront reads.
        row = {
            "id": pid,
            "sku": p.get("sku", ""),
            "slug": p.get("slug", ""),
            "name": p.get("name", ""),
            "nameFr": p.get("nameFr", ""),
            "category": p.get("category", ""),
            "priceCfa": p.get("priceCfa", 0),
            "compareCfa": p.get("compareCfa", None),
            "priceNgn": p.get("priceNgn", 0),
            "compareNgn": p.get("compareNgn", None),
            "image": p.get("image", ""),
            "imageUrl": p.get("imageUrl", ""),
            "placeholderImage": p.get("placeholderImage", ""),
            "description": p.get("description", ""),
            "stock": p.get("stock", 0),
            "badge": p.get("badge", ""),
            "featured": bool(p.get("featured", False)),
            "online": p.get("online", True) is not False,
            "colors": p.get("colors", []),
            "options": p.get("options", []),
            "source": "admin" if pid in _load_overrides() else "seed",
            "updated_at": now,
        }
        rows.append(row)

    if rows:
        try:
            sb.table("products").upsert(rows).execute()
        except Exception as exc:
            print(f"Upsert failed: {exc}")
            sys.exit(1)
    print(f"Imported {len(rows)} products into Supabase (source of truth ready).")
    print("Now set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY to switch the shop over.")


if __name__ == "__main__":
    main()
