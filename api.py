"""All JSON endpoints. Every mutating route is CSRF-protected."""
import csv, io, json, os, datetime, secrets, hashlib, re
from flask import Blueprint, request, jsonify, session, current_app, make_response
from config import Config
from db import execute, one, query, audit
import security as sec
import auth as authmod
import storage
import catalog as catalog_mod
import analytics as analytics_mod
import delivery

api = Blueprint("api", __name__, url_prefix="/api")

ORDER_ID = re.compile(r"^JA-[A-Z0-9]{4,16}$")

def _ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else "") or request.remote_addr or ""

# -------------------------------------------------------------- categories
import os as _os
CATEGORIES_FILE = _os.environ.get(
    "CATEGORIES_PATH",
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "categories.json"))
DEFAULT_CATEGORIES = [
    {"id": "clothing", "name": "Clothings for men and women", "nameFr": "Vêtements homme et femme", "image": "images/categories/fashion.jpg", "hidden": False},
    {"id": "household", "name": "Household & Kitchen", "nameFr": "Maison & cuisine", "image": "images/categories/household.jpg", "hidden": False},
    {"id": "ankara", "name": "Ankara ready to wear", "nameFr": "Ankara prêt-à-porter", "image": "images/categories/fashion.jpg", "hidden": False},
    {"id": "accessories", "name": "Accessories", "nameFr": "Accessoires", "image": "images/categories/gadgets.jpg", "hidden": False},
    {"id": "beauty", "name": "Beauty & skincare", "nameFr": "Beauté & soins", "image": "images/categories/beauty.jpg", "hidden": False},
    {"id": "shoes", "name": "Shoes", "nameFr": "Chaussures", "image": "images/categories/shoes.jpg", "hidden": False},
    {"id": "gadgets", "name": "Gadgets / Electronics", "nameFr": "Gadgets / Électronique", "image": "images/categories/gadgets.jpg", "hidden": False},
    {"id": "packaging", "name": "Packaging", "nameFr": "Emballage", "image": "images/categories/household.jpg", "hidden": False},
    {"id": "bags", "name": "Bags", "nameFr": "Sacs", "image": "images/categories/bags.jpg", "hidden": False},
    {"id": "hair-care", "name": "Hair care", "nameFr": "Soins des cheveux", "image": "images/categories/beauty.jpg", "hidden": False},
    {"id": "nails", "name": "Nails", "nameFr": "Ongles", "image": "images/categories/beauty.jpg", "hidden": False},
    {"id": "gift-set", "name": "Gift set", "nameFr": "Coffret cadeau", "image": "images/categories/household.jpg", "hidden": False},
    {"id": "children", "name": "Children items", "nameFr": "Articles pour enfants", "image": "images/categories/fashion.jpg", "hidden": False},
    {"id": "decor", "name": "Decor", "nameFr": "Décoration", "image": "images/categories/household.jpg", "hidden": False},
]


def _ordered_categories(categories):
    categories = [dict(c) for c in categories]
    for c in categories:
        # Rename only the old shipped default, not an owner's custom label.
        if c.get("id") == "household" and c.get("name") == "Household items":
            c["name"] = "Household & Kitchen"
            if c.get("nameFr") in (None, "", "Articles ménagers"):
                c["nameFr"] = "Maison & cuisine"
    def key(c):
        try:
            return int(c["order"])
        except (KeyError, TypeError, ValueError):
            return 0 if c.get("id") == "household" else 1
    return sorted(categories, key=key)


def _categories_data():
    """Read categories from Supabase (production), disk (test/dev) or defaults.

    In production there is NO silent local fallback: when the Supabase
    ``categories`` table is unreachable this raises, and the route answers a
    clear 503 instead of serving stale/empty data.
    """
    if Config.ENV != "testing":
        try:
            from supabase_store import enabled as _sb_enabled
            from supabase_store import load_categories_table
            if _sb_enabled():
                rows = load_categories_table()
                from supabase_store import load_categories
                details = load_categories()
                if rows is not None and details:
                    by_id = {str(c.get("id")): c for c in details}
                    if not rows:
                        rows = details
                    else:
                        for row in rows:
                            extra = by_id.get(str(row.get("id")), {})
                            for key in ("nameFr", "image", "image_url", "hidden", "order"):
                                if key in extra:
                                    row[key] = extra[key]
                if rows is None:
                    raise RuntimeError("Supabase categories unavailable")
                # A reachable but EMPTY table is not "the shop has no
                # categories", it is a table that was never seeded (or was
                # wiped). Serving [] left every phone with an empty category
                # strip and no way back. Self-heal to the built-in defaults,
                # photos included, so the storefront always has something to
                # show; the owner's own rows replace these the moment they
                # save any category.
                if not rows:
                    return {"categories": [dict(c) for c in DEFAULT_CATEGORIES],
                            "updatedAt": "", "updatedBy": ""}
                return {"categories": rows, "updatedAt": "", "updatedBy": ""}
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Supabase categories unavailable") from exc
    try:
        with open(CATEGORIES_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list) and data:
            return {"categories": data, "updatedAt": "", "updatedBy": ""}
        if isinstance(data, dict) and isinstance(data.get("categories"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"categories": [dict(c) for c in DEFAULT_CATEGORIES], "updatedAt": "", "updatedBy": ""}


def _save_categories(categories, actor=None):
    payload = {
        "categories": categories,
        "updatedAt": _utcnow(),
        "updatedBy": actor or "",
    }
    if Config.ENV != "testing":
        try:
            from supabase_store import enabled as _sb_enabled
            if _sb_enabled():
                from supabase_store import save_categories_table
                if not save_categories_table(categories):
                    raise RuntimeError("Supabase categories write failed")
                # The legacy table may lack French/image/hidden/order columns.
                # Its durable JSON companion preserves those fields without a
                # manual schema migration. Never acknowledge a lost order.
                from supabase_store import save_categories
                if not save_categories(categories):
                    raise RuntimeError("Category details could not be saved; please retry")
                return payload
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Supabase category save unavailable") from exc
    tmp = CATEGORIES_FILE + ".tmp"
    _os.makedirs(_os.path.dirname(CATEGORIES_FILE) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    _os.replace(tmp, CATEGORIES_FILE)
    try:
        from supabase_store import save_categories
        save_categories(categories)
    except Exception:
        pass
    return payload

def _utcnow():
    return datetime.datetime.utcnow().isoformat(timespec="seconds")

# =============================================================== public: misc
@api.get("/csrf")
def csrf():
    return jsonify(ok=True, token=sec.issue_csrf())

@api.get("/config")
def public_config():
    return jsonify(ok=True, csrf=sec.issue_csrf(), env=Config.ENV,
                   lowStockThreshold=Config.LOW_STOCK_THRESHOLD,
                   recaptchaSiteKey=Config.RECAPTCHA_SITE_KEY)

@api.get("/products")
def products():
    """The seed catalogue as it ships, before any admin edit.

    This used to call send_static_file(), but the app is created with
    static_folder=None (everything is served from the project root), so that
    call raised 500 on every request. The file is read directly instead.
    """
    body = jsonify(ok=True, products=[_public_product(p)
                                      for p in catalog_mod.base_products()])
    body.headers["Cache-Control"] = "public, max-age=300"
    return body

# ============================================================ public: catalog
# Customers never see numerical stock: the public catalogue carries only an
# In Stock / Out of Stock flag (the admin portal, with a session, still gets
# the numbers it needs to manage the shop).
_FORBIDDEN_PUBLIC_KEYS = ("stock", "stock_quantity", "optionStock",
                         "variantStock", "inventory")


def _public_product(p):
    out = {k: v for k, v in dict(p or {}).items() if k not in _FORBIDDEN_PUBLIC_KEYS}
    try:
        qty = int(p.get("stock") if p.get("stock") is not None else p.get("stock_quantity") or 0)
    except (TypeError, ValueError):
        qty = 0
    out["stock_status"] = "in" if qty > 0 else "out"
    return out


@api.get("/catalog")
def catalog():
    """Seed products + every admin edit, merged. This is the live catalogue."""
    admin = bool(authmod.current_admin())
    include_hidden = admin and request.args.get("all") == "1"
    products = catalog_mod.merged(include_hidden=include_hidden)
    if not admin:
        products = [_public_product(p) for p in products]
    body = json.dumps({
        "ok": True,
        "products": products,
        "meta": catalog_mod.meta(),
    }, ensure_ascii=False, separators=(",", ":"))
    etag = 'W/"' + hashlib.sha256(body.encode("utf-8")).hexdigest()[:28] + '"'
    if request.headers.get("If-None-Match") == etag:
        resp = make_response("", 304)
    else:
        resp = make_response(body, 200)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "public, max-age=30, must-revalidate"
    return resp

# ========================================================== public: categories
@api.get("/categories")
def categories_public():
    """The category list used by the shop, filters and admin manager."""
    try:
        return jsonify(ok=True, categories=_ordered_categories(_categories_data().get("categories") or []))
    except Exception as exc:
        print(f"[supabase] categories serve failed: {exc}")
        return jsonify(ok=False, error="Categories are temporarily unavailable. Please refresh in a moment."), 503


@api.put("/admin/categories")
@authmod.require_admin
@sec.require_csrf
def categories_admin_set():
    """Save the category table server-side (data/categories.json)."""
    d = request.get_json(silent=True) or {}
    cats = d.get("categories")
    if not isinstance(cats, list):
        return jsonify(ok=False, error="Send {categories: [...]}."), 400
    clean = []
    seen = set()
    for index, c in enumerate(cats):
        if not isinstance(c, dict):
            continue
        cid = sec.clean(c.get("id"), 40)
        name = sec.clean(c.get("name"), 120)
        if not cid or not name or cid in seen:
            continue
        seen.add(cid)
        clean.append({
            "id": cid,
            "name": name,
            "nameFr": sec.clean(c.get("nameFr"), 120),
            "image": sec.safe_url(c.get("image_url") or c.get("image") or ""),
            "image_url": sec.safe_url(c.get("image_url") or c.get("image") or ""),
            "hidden": bool(c.get("hidden")),
            "order": sec.clean_int(c.get("order"), index, 0, 10**9),
        })
    try:
        payload = _save_categories(clean, authmod.current_admin())
    except Exception as exc:
        print(f"[supabase] categories save failed: {exc}")
        return jsonify(ok=False, error="Could not confirm all category details in Supabase. Please retry the save."), 503
    audit(authmod.current_admin(), "categories.update", f"saved={len(clean)}", _ip())
    return jsonify(ok=True, count=len(clean), **payload)


# ========================================================== public: analytics
@api.post("/track")
@sec.require_csrf
def track():
    """Record page views + engagement. Batched, rate limited, no PII stored."""
    d = request.get_json(silent=True) or {}
    items = d.get("events")
    if not isinstance(items, list):
        items = [d]
    items = [i for i in items if isinstance(i, dict)][:40]
    if not items:
        return jsonify(ok=False, error="nothing to record"), 400
    vid, is_new = analytics_mod.visitor_id()
    # keyed per visitor, not per IP: whole mobile networks share one address
    limited = sec.guard("track", limit=400, window=300, key_extra=vid)
    if limited: return limited
    stored = analytics_mod.record(items, vid, is_new)
    resp = make_response(jsonify(ok=True, recorded=stored))
    return analytics_mod.stamp_cookie(resp, vid)

@api.get("/most-viewed")
def most_viewed():
    limit = sec.clean_int(request.args.get("limit"), 8, 1, 48)
    rows = query(
        "SELECT product_id productId, MAX(product_name) name, "
        "SUM(CASE WHEN type='view' THEN 1 ELSE 0 END) views, "
        "SUM(CASE WHEN type='cart' THEN 1 ELSE 0 END) carts "
        "FROM events WHERE product_id != '' GROUP BY product_id "
        "ORDER BY (views + carts*3) DESC LIMIT ?", (limit,))
    return jsonify(ok=True, items=[dict(r) for r in rows])

# ======================================================== public: engagement
@api.post("/views")
def track_view():
    pid = sec.clean(request.json.get("productId") if request.is_json else request.form.get("productId"), 64)
    if not pid:
        return jsonify(ok=False, error="productId required"), 400
    execute("INSERT INTO product_views (product_id, views, updated_at) VALUES (?,1,?) "
            "ON CONFLICT(product_id) DO UPDATE SET views=views+1, updated_at=excluded.updated_at",
            (pid, datetime.datetime.utcnow().isoformat(timespec="seconds")))
    return jsonify(ok=True)

def _stock_rows_for_read():
    """Production reads come from Supabase; local SQLite is test/dev only."""
    if catalog_mod._prod_source():
        from supabase_store import load_variant_stock_strict
        return load_variant_stock_strict()
    return [dict(r) for r in query(
        "SELECT product_id, variant_key, variant_label, qty, low_threshold FROM variant_stock")]


@api.get("/stock")
def stock():
    rows = _stock_rows_for_read()
    if rows is None:
        return jsonify(ok=False, error="Stock is temporarily unavailable."), 503
    admin = bool(authmod.current_admin())
    out = {}
    for r in rows:
        item = {
            "variant": r["variant_key"], "label": r["variant_label"],
            "state": "out" if r["qty"] <= 0 else ("low" if r["qty"] <= r["low_threshold"] else "in"),
        }
        if admin:
            item["qty"] = r["qty"]
            item["lowThreshold"] = r["low_threshold"]
        out.setdefault(r["product_id"], []).append(item)
    body = {"ok": True, "stock": out}
    if admin:
        body["lowStockThreshold"] = Config.LOW_STOCK_THRESHOLD
    return jsonify(body)

@api.get("/activity")
def activity():
    since = sec.clean_int(request.args.get("since"), 0, 0)
    limit = sec.clean_int(request.args.get("limit"), 12, 1, 50)
    rows = query("SELECT id, kind, product_id, product_name, city, qty, at FROM activity_events "
                 "WHERE id > ? ORDER BY id DESC LIMIT ?", (since, limit))
    return jsonify(ok=True, events=[dict(r) for r in rows])

@api.post("/activity")
def log_activity():
    d = request.get_json(silent=True) or {}
    kind = sec.clean(d.get("kind"), 24)
    if kind not in ("cart", "purchase", "view"):
        return jsonify(ok=False, error="bad kind"), 400
    pid = sec.clean(d.get("productId"), 64)
    name = sec.clean(d.get("productName"), 160)
    city = sec.clean(d.get("city"), 60)
    qty = sec.clean_int(d.get("qty"), 1, 1, 99)
    if not pid:
        return jsonify(ok=False, error="productId required"), 400
    cur = execute("INSERT INTO activity_events (kind, product_id, product_name, city, qty) VALUES (?,?,?,?,?)",
                  (kind, pid, name, city, qty))
    return jsonify(ok=True, id=cur.lastrowid)

CUSTOMER_FIELDS = ("firstName", "lastName", "name", "phone", "email", "country",
                   "city", "zone", "address", "note")
STATUSES = ("pending", "confirmed", "declined")


@api.post("/uploads/proof")
@sec.require_csrf
def upload_proof():
    """Store a payment screenshot. Anyone may call it (a customer has no
    account), so it is size capped, magic-byte checked and rate limited."""
    limited = sec.guard("upload", limit=12, window=600)
    if limited: return limited
    f = request.files.get("file") or request.files.get("proof")
    if not f:
        return jsonify(ok=False, error="No file received."), 400
    data = f.read(storage.MAX_BYTES + 1)
    ok, msg, _ext = storage.validate_image(data, f.filename or "")
    if not ok:
        return jsonify(ok=False, error=msg), 400
    ok, msg, url = storage.save_image(data, "proofs", f.filename or "")
    if not ok:
        return jsonify(ok=False, error=msg), 500
    return jsonify(ok=True, url=url)


def _fold(value):
    """Case/punctuation-insensitive key for matching variant option values."""
    return "".join(c.lower() for c in str(value or "") if c.isalnum())


def _variant_values(variant):
    """Option values from a cart variant string like "Color: Red · Size: M"."""
    v = str(variant or "")
    if not v or v == "__default__":
        return []
    out = []
    for part in re.split(r"[·;|]", v):
        p = str(part or "").strip()
        if not p:
            continue
        if ":" in p:
            _title, val = p.split(":", 1)
            val = val.strip()
            if val:
                out.append(val)
        else:
            out.append(p)
    return out


def _stock_available(product, variant="__default__"):
    """Integer stock for one product+variant, falling back to product["stock"]."""
    if not isinstance(product, dict):
        return 0
    try:
        base = int(product.get("stock") or 0)
    except (TypeError, ValueError):
        base = 0
    base = max(0, base)
    os_map = product.get("optionStock")
    if not isinstance(os_map, dict) or not os_map:
        return base
    vals = _variant_values(variant)
    if not vals:
        return base
    folded = {}
    for k, qty in os_map.items():
        fk = _fold(k)
        if not fk:
            continue
        try:
            folded[fk] = max(0, int(qty or 0))
        except (TypeError, ValueError):
            folded[fk] = 0
    for val in vals:
        fk = _fold(val)
        if fk and fk in folded:
            return folded[fk]
    return base


def _stock_problems(items):
    """Sum requested quantities per product+variant and compare with stock.

    Returns a list of {id, name, variant, available, requested} (with left /
    asked aliases) for every line that asks for more than is left. Unknown
    product ids are skipped so legacy / custom items never block checkout.
    """
    try:
        products = {p.get("id"): p for p in catalog_mod.merged(include_hidden=True)}
    except Exception:
        return []
    groups = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("id") or "")
        if not pid:
            continue
        variant = str(it.get("color") or it.get("variant") or "__default__")
        if not variant:
            variant = "__default__"
        try:
            qty = int(it.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        key = (pid, variant)
        if key not in groups:
            groups[key] = {"id": pid, "variant": variant, "requested": 0,
                           "name": str(it.get("name") or "")}
        groups[key]["requested"] += qty
        if not groups[key]["name"]:
            groups[key]["name"] = str(it.get("name") or "")
    problems = []
    for (pid, variant), g in groups.items():
        product = products.get(pid)
        if product is None:
            continue
        avail = _stock_available(product, variant)
        if g["requested"] > avail:
            name = product.get("name") or g["name"] or pid
            problems.append({
                "id": pid,
                "name": name,
                "variant": "" if variant == "__default__" else variant,
                "available": avail,
                "requested": g["requested"],
                "left": avail,
                "asked": g["requested"],
            })
    return problems


def _stock_message(problems):
    """One human sentence naming the product, the stock left and the ask."""
    if not problems:
        return ""
    bits = []
    for p in problems:
        bits.append(f'Only {p.get("available", 0)} left of "{p.get("name", "")}"'
                    f' — you asked for {p.get("requested", 0)}.')
    return " ".join(bits)


def _option_stock_key(product, variant):
    """The optionStock map key that matches a cart variant, or None."""
    if not isinstance(product, dict):
        return None
    os_map = product.get("optionStock")
    if not isinstance(os_map, dict) or not os_map:
        return None
    vals = _variant_values(variant)
    if not vals:
        return None
    folded = {}
    for k in os_map:
        fk = _fold(k)
        if fk:
            folded[fk] = k
    for val in vals:
        fk = _fold(val)
        if fk and fk in folded:
            return folded[fk]
    return None


def _order_stock_moves(payload):
    """[{id, option, qty}, ...] for every cart line on an order.

    The id is normalised to the product's canonical primary key. An order line
    stores whichever id the customer's cart held - which can be a legacyId
    alias - and stock must be applied to the row that actually exists, so a
    confirm decrements and a decline restores the same canonical row.
    """
    items = (payload or {}).get("items") or []
    try:
        products = catalog_mod.product_index()
    except Exception:
        products = {}
    moves = []
    for it in items:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("id") or "").strip()
        if not pid:
            continue
        try:
            qty = int(it.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        product = products.get(pid)
        # An alias resolves to the canonical id; an id that is not in the
        # catalogue at all (a deleted product) is left as written so the move
        # is still recorded rather than silently dropped.
        pid = str((product or {}).get("id") or "").strip() or pid
        option = _option_stock_key(product, it.get("color") or it.get("variant") or "") if product else None
        moves.append({"id": pid, "option": option, "qty": qty})
    return moves


def _apply_stock_moves(moves, sign, actor=None):
    """sign -1 decrements, +1 restores. Never raises."""
    applied = []
    for m in moves or []:
        if not isinstance(m, dict):
            continue
        pid = m.get("id")
        try:
            qty = int(m.get("qty") or 0) * int(sign)
        except (TypeError, ValueError):
            qty = 0
        if not pid or not qty:
            continue
        try:
            catalog_mod.apply_stock_delta(pid, qty, option_key=m.get("option"), actor=actor)
        except Exception:
            continue
        applied.append(m)
    return applied


def _sync_order_stock(payload, old_status, new_status, actor=None):
    """Decrement catalog stock when an order becomes confirmed; restore when it leaves.

    Mutates ``payload`` in place: ``stockApplied`` holds the exact deltas so a
    second confirm (already=True, admin re-save) never double-decrements,
    and a decline / reopen / delete restores the same quantities.

    In production the stock was already reserved atomically at checkout, so a
    confirm only marks the reservation (it never decrements again), while a
    decline or reopen releases it back.
    """
    if not isinstance(payload, dict):
        return payload
    old_c = (old_status or "pending") == "confirmed"
    new_c = (new_status or "pending") == "confirmed"
    if catalog_mod._prod_source():
        if new_c and not old_c:
            payload["stockApplied"] = _order_stock_moves(payload)
        elif (old_c and not new_c) or \
                ((old_status or "pending") == "pending" and new_status == "declined"):
            moves = payload.get("stockApplied") or _order_stock_moves(payload)
            if moves:
                _apply_stock_moves(moves, +1, actor=actor)
            payload["stockApplied"] = None
        return payload
    if old_c == new_c:
        return payload
    if new_c:
        if payload.get("stockApplied"):
            return payload
        moves = _order_stock_moves(payload)
        _apply_stock_moves(moves, -1, actor=actor)
        payload["stockApplied"] = moves
    else:
        moves = payload.get("stockApplied") or _order_stock_moves(payload)
        if moves:
            _apply_stock_moves(moves, +1, actor=actor)
        payload["stockApplied"] = None
    return payload


def _price_int(value):
    """Non-negative integer price from a product row, else 0."""
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _server_unit_price(product, currency):
    """The authoritative unit price for one product in the order currency.

    Loaded from the live catalogue (Supabase in production): the browser's
    price is never trusted. CFA is derived from NGN at the house rate when
    the product has no explicit priceCfa.
    """
    cfa = _price_int(product.get("priceCfa") if product.get("priceCfa") is not None
                     else product.get("price_cfa"))
    ngn = _price_int(product.get("priceNgn") if product.get("priceNgn") is not None
                     else product.get("price_ngn"))
    if currency == "CFA":
        if cfa:
            return cfa
        return max(0, round((ngn or 0) * catalog_mod.NGN_TO_CFA))
    if ngn:
        return ngn
    return max(0, round((cfa or 0) / catalog_mod.NGN_TO_CFA))


def _checkout_items(clean_items, currency):
    """Server-authoritative item validation + pricing.

    Loads every product from the live catalogue (Supabase in production),
    aggregates duplicate lines, validates online/qty/stock and returns
    (items, subtotal, error_response). The error response is 400 for an
    unknown product, 409 with code ``out_of_stock`` for an unavailable line
    - and never contains a numerical stock count (only In/Out of Stock).
    """
    try:
        live = catalog_mod.merged(include_hidden=True)
    except Exception:
        live = []
    # Keyed by canonical id AND legacyId, so a cart saved against an old
    # wix-* id still prices and stock-checks against the right row.
    products_map = catalog_mod.product_index(live)

    aggregated = {}
    for it in clean_items:
        key = (str(it.get("id") or ""), str(it.get("color") or ""))
        g = aggregated.setdefault(key, {
            "id": str(it.get("id") or ""),
            "variant": str(it.get("color") or ""),
            "qty": 0,
            "name": str(it.get("name") or ""),
        })
        g["qty"] += int(it.get("qty") or 0)

    items = []
    subtotal = 0
    for g in aggregated.values():
        pid = g["id"]
        prod = products_map.get(pid)
        if prod is None:
            return [], 0, (jsonify(ok=False, error=(
                f'"{g["name"] or pid}" is no longer available. '
                "Please remove it from your cart and try again."),
                code="unknown_product",
                items=[{"id": pid, "name": g["name"] or pid}]), 400)
        if prod.get("online") is False:
            return [], 0, (jsonify(ok=False, error=(
                f'"{prod.get("name") or g["name"]}" is out of stock. '
                "Please remove it and try again."),
                code="out_of_stock",
                items=[{"id": pid, "name": prod.get("name") or g["name"],
                        "variant": g["variant"]}]), 409)
        avail = _stock_available(prod, g["variant"])
        if Config.ENFORCE_STOCK and g["qty"] > avail:
            return [], 0, (jsonify(ok=False, error=(
                f'"{prod.get("name") or g["name"]}" is out of stock. '
                "Please remove it or choose fewer items."),
                code="out_of_stock",
                items=[{"id": pid, "name": prod.get("name") or g["name"],
                        "variant": g["variant"]}]), 409)
        unit = _server_unit_price(prod, currency)
        line_price = unit * g["qty"]
        subtotal += line_price
        items.append({
            "id": pid,
            "name": prod.get("name") or g["name"],
            "qty": g["qty"],
            "price": line_price,
            "color": g["variant"],
        })
    return items, subtotal, None


def _release_stock_lines(lines):
    """Best-effort release of reserved stock after a failed checkout."""
    try:
        from supabase_store import release_product_stock
        for pid, qty in (lines or []):
            try:
                release_product_stock(pid, qty)
            except Exception:
                pass
    except Exception:
        pass


def _benin_togo_min(zone, country, currency, total):
    """The 5,000 CFA / 12,000 NGN minimum for Benin & Togo deliveries."""
    if not re.search(r"(?i)\bbenin\b|\btogo\b|cotonou|calavi|porto|lom[ée]|lome", zone) and \
       not re.search(r"(?i)\bbenin\b|\btogo\b", country or ""):
        return None
    if currency == "CFA" and total < 5000:
        return ("Benin & Togo deliveries: minimum order 5,000 F CFA "
                "(about 12,000 naira). Please add a few more items to meet "
                "the minimum.")
    if currency == "NGN" and total < 12000:
        return ("Benin & Togo deliveries: minimum order 12,000 naira "
                "(about 5,000 F CFA). Please add a few more items to meet "
                "the minimum.")
    return None


@api.post("/orders")
@sec.require_csrf
def create_order():
    """Store a completed checkout - the whole form plus the payment proof.
    Accepts JSON or multipart/form-data (field `order` = JSON, field `proof`
    = image). Re-posting the same order id returns the stored order instead of
    creating a duplicate, so a queued offline retry is always safe.

    The browser's prices and total are never trusted: every line is loaded
    from the live catalogue (Supabase in production), prices are recomputed,
    duplicate lines are aggregated, stock is validated (and atomically
    reserved in production) and the totals stored are the server's."""
    # 30/hour: plenty for a real shopper, and mobile networks share one IP
    limited = sec.guard("order", limit=30, window=3600)
    if limited: return limited
    bounced = sec.recaptcha_gate("checkout")
    if bounced: return bounced

    if request.files or "order" in request.form:
        try:
            d = json.loads(request.form.get("order") or "{}")
        except ValueError:
            d = {}
        proof_file = request.files.get("proof")
    else:
        d = request.get_json(silent=True) or {}
        proof_file = None
    if not isinstance(d, dict):
        d = {}

    customer_raw = d.get("customer") if isinstance(d.get("customer"), dict) else {}
    customer = {k: sec.clean(customer_raw.get(k), 300) for k in CUSTOMER_FIELDS}
    email = sec.clean_email(customer.get("email") or d.get("email"))
    if not email:
        return jsonify(ok=False, error="A valid email address is required."), 400
    customer["email"] = email

    items = d.get("items")
    if not isinstance(items, list) or not items:
        return jsonify(ok=False, error="Cart is empty."), 400
    clean_items = []
    for it in items[:60]:
        if not isinstance(it, dict):
            continue
        clean_items.append({
            "id": sec.clean(it.get("id"), 64),
            "name": sec.clean(it.get("name"), 200),
            "qty": sec.clean_int(it.get("qty"), 1, 1, 999),
            "price": sec.clean_int(it.get("price"), 0, 0, 10**9),
            "color": sec.clean(it.get("color"), 60),
        })
    if not clean_items:
        return jsonify(ok=False, error="Cart is empty."), 400

    currency = sec.clean(d.get("currency"), 3).upper() or "NGN"
    total = sec.clean_int(d.get("total"), 0, 0, 10**12)

    # ---- delivery zone + fare: the server is the authority ----
    # The zone list used to be hardcoded in checkout.html and this endpoint
    # accepted any free text, only regex-blocking the word "pickup". Now the
    # zone is resolved against the admin-editable delivery_zones table, which
    # also supplies the fare range. An unknown zone is rejected outright: that
    # subsumes the old pickup heuristic (an invented "pick up at my house" is
    # simply not a zone) and means the order always carries a fare the server
    # can stand behind.
    zone = sec.clean(customer_raw.get("zone") or d.get("zone"), 80)
    fare_ok, fare = delivery.fare_for(zone, currency)
    if not fare_ok:
        return jsonify(ok=False,
                       error=fare.get("error") or "Choose a delivery zone."), 400
    # Store the canonical name, not whatever the browser sent, so analytics and
    # the admin order list group by real zones.
    zone = fare["zone_name"]

    # ---- server-authoritative lines: prices from the live catalogue ----
    # (Supabase in production; the browser's price/total is never trusted)
    clean_items, subtotal, err = _checkout_items(clean_items, currency)
    if err:
        return err
    total = subtotal
    discount = 0

    oid = sec.clean(d.get("id"), 24).upper()
    if not ORDER_ID.match(oid or ""):
        oid = "JA-" + secrets.token_hex(3).upper()

    existing = one("SELECT id, status, at FROM orders WHERE id=?", (oid,))
    if existing:
        return jsonify(ok=True, id=oid, duplicate=True, status=existing["status"])

    # ---- referral / promo code (validated on the server, never trusted) ----
    import growth
    promo = None
    promo_in = d.get("promo") if isinstance(d.get("promo"), dict) else {}
    promo_raw = sec.clean(d.get("promoCode") or promo_in.get("code"), 32)
    if promo_raw:
        chk = growth.check_code(promo_raw)
        if chk.get("ok"):
            promo = {"code": chk["code"], "percent": chk["percent"], "kind": chk["kind"]}
    if promo:
        discount = round(subtotal * int(promo["percent"]) / 100)
        total = max(0, subtotal - discount)

    # Benin & Togo: the minimum is enforced against the SERVER total, never
    # the number the browser sent.
    country_raw = sec.clean(customer_raw.get("country") or d.get("country") or "", 80)
    min_error = _benin_togo_min(zone, country_raw, currency, total)
    if min_error:
        return jsonify(ok=False, error=min_error), 400

    proof_url = ""
    data = b""
    ext = ""
    if proof_file:
        data = proof_file.read(storage.MAX_BYTES + 1)
        ok, msg, ext = storage.validate_upload(data, proof_file.filename or "",
                                               allow_pdf=True,
                                               max_bytes=storage.MAX_RECEIPT_BYTES)
        if not ok:
            return jsonify(ok=False, error=msg), 400
        ok, msg, proof_url = storage.save_image(
            data, "proofs", proof_file.filename or "",
            allow_pdf=True, max_bytes=storage.MAX_RECEIPT_BYTES)
        if not ok:
            return jsonify(ok=False, error=msg), 400
        if not ok:
            return jsonify(ok=False, error="Could not save the payment screenshot. Try again."), 500
    elif d.get("proofUrl"):
        candidate = sec.clean(d.get("proofUrl"), 500)
        if candidate.startswith("https://") and "/storage/v1/object/public/" in candidate:
            proof_url = candidate

    now = _utcnow()
    import customers as customers_mod
    owner_id = customers_mod.current_customer_id()
    order = {
        "id": oid,
        "at": sec.clean(d.get("at"), 40) or now,
        "status": "pending",
        "customer": customer,
        "items": clean_items,
        "subtotal": subtotal,
        "discount": discount,
        "total": total,
        "currency": currency,
        "payment": sec.clean(d.get("payment"), 60) or currency,
        "proofUrl": proof_url,
        "source": sec.clean(d.get("source"), 20) or "web",
        # The server-computed fare snapshot. The exact figure is agreed with
        # the customer after payment (transport varies with weight), so this
        # records the RANGE the checkout quoted plus who has to confirm it.
        "delivery": fare,
    }
    if promo:
        order["promo"] = promo

    sb_row = {
        "id": oid, "email": email,
        "customer_name": sec.clean(customer.get("name") or "", 200),
        "phone": customer.get("phone", ""), "country": customer.get("country", ""),
        "city": customer.get("city", ""), "zone": zone,
        "address": customer.get("address", ""), "note": customer.get("note", ""),
        "payment": order["payment"], "proof_url": proof_url,
        "items_count": len(clean_items), "total": total, "currency": currency,
        "source": order["source"], "status": "pending",
        "payload": order, "at": order["at"], "updated_at": now,
    }
    if owner_id:
        sb_row["customer_user_id"] = owner_id

    prod_source = bool(catalog_mod._prod_source())
    reserved = []

    if prod_source and Config.ENFORCE_STOCK:
        # Reserve every line atomically BEFORE the order is written, so two
        # concurrent checkouts can never sell the same last unit.
        try:
            from supabase_store import reserve_product_stock
            for it in clean_items:
                res = reserve_product_stock(it["id"], int(it["qty"]))
                if res is None:
                    _release_stock_lines(reserved)
                    return jsonify(ok=False, error=(
                        f'"{it["name"]}" is out of stock. Please remove it '
                        "or choose fewer items."),
                        code="out_of_stock",
                        items=[{"id": it["id"], "name": it["name"],
                                "variant": it.get("color") or ""}]), 409
                if res is False:
                    _release_stock_lines(reserved)
                    return jsonify(ok=False, error=(
                        "We could not confirm your stock right now. "
                        "Please try again in a moment.")), 503
                reserved.append((it["id"], int(it["qty"])))
        except Exception as exc:
            print(f"[supabase] order reserve failed: {exc}")
            _release_stock_lines(reserved)
            return jsonify(ok=False, error=(
                "We could not confirm your stock right now. "
                "Please try again in a moment.")), 503

    if prod_source:
        # Supabase PostgreSQL is the record of the sale. A failed write is a
        # clear 503 (stock already released) - never a silent local fallback.
        try:
            from supabase_store import create_order_strict
            saved = bool(create_order_strict(sb_row))
        except Exception as exc:
            print(f"[supabase] order write failed: {exc}")
            saved = False
        if not saved:
            _release_stock_lines(reserved)
            return jsonify(ok=False, error=(
                "Your order could not be saved right now. Please try again "
                "in a moment — nothing was charged.")), 503
        # SQLite stays only a cache for the admin portal / fast reads.
        try:
            execute(
                "INSERT INTO orders (id, payload, email, customer_name, phone, country, city, zone, "
                "address, note, payment, proof_url, items_count, total, currency, source, status, at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, json.dumps(order, ensure_ascii=False), email,
                 sec.clean(customer.get("name") or (customer.get("firstName") + " " + customer.get("lastName")).strip(), 200),
                 customer.get("phone", ""), customer.get("country", ""), customer.get("city", ""),
                 zone, customer.get("address", ""), customer.get("note", ""),
                 order["payment"], proof_url, len(clean_items), total, currency,
                 order["source"], "pending", order["at"], now),
            )
        except Exception as exc:
            print(f"[sqlite] order cache write skipped: {exc}")
    else:
        execute(
            "INSERT INTO orders (id, payload, email, customer_name, phone, country, city, zone, "
            "address, note, payment, proof_url, items_count, total, currency, source, status, at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, json.dumps(order, ensure_ascii=False), email,
             sec.clean(customer.get("name") or (customer.get("firstName") + " " + customer.get("lastName")).strip(), 200),
             customer.get("phone", ""), customer.get("country", ""), customer.get("city", ""),
             zone, customer.get("address", ""), customer.get("note", ""),
             order["payment"], proof_url, len(clean_items), total, currency,
             order["source"], "pending", order["at"], now),
        )
        # mirror into Supabase when enabled (best effort in test/dev;
        # production never reaches this branch)
        from supabase_store import create_order as _sb_create_order
        if Config.SUPABASE_ENABLED:
            try:
                _sb_create_order(sb_row)
            except Exception as exc:
                print(f"[supabase] order mirror skipped: {exc}")
    if owner_id:
        try:
            execute("UPDATE orders SET customer_user_id=? WHERE id=?", (owner_id, oid))
        except Exception:
            pass

    # conversion tracking: a finished checkout is the purchase event
    vid, _is_new = analytics_mod.visitor_id()
    analytics_mod.record([{
        "type": "purchase", "path": "/checkout.html", "page": "checkout",
        "value": total, "currency": currency, "sid": sec.clean(d.get("sid"), 48),
    }], vid, False)

    # WhatsApp notification to the owner (fire-and-forget; never blocks the sale)
    import threading as _threading
    def _notify_whatsapp(order_copy):
        try:
            import whatsapp
            sent, detail = whatsapp.send_order_notification(order_copy)
            audit("system", "order.whatsapp", f"{order_copy.get('id')} sent={sent} {detail}"[:400], "")
        except Exception:
            pass
    _threading.Thread(target=_notify_whatsapp, args=(dict(order),), daemon=True).start()

    # Email the shop about the new order too (fire-and-forget; mailer no-ops
    # until MAIL_FROM / MAIL_TO / a provider key are configured).
    try:
        import mailer
        mailer.notify_new_order_async(order)
    except Exception:
        pass

    # growth hooks: count the promo use and mint a referral code when the order qualifies
    referral_code = ""
    try:
        if promo:
            growth.record_code_use(promo["code"], email, oid)
        referral_code = growth.maybe_issue_referral(
            email, customer.get("name") or "", total, currency)
        if referral_code:
            order["referralCode"] = referral_code
            execute("UPDATE orders SET payload=? WHERE id=?",
                    (json.dumps(order, ensure_ascii=False), oid))
    except Exception:
        pass

    resp = make_response(jsonify(ok=True, id=oid, status="pending", proofUrl=proof_url,
                                 referralCode=referral_code,
                                 promo=promo or None,
                                 subtotal=subtotal, discount=discount, total=total,
                                 # The fare range this checkout quoted, so the
                                 # confirmation page can restate it instead of
                                 # re-deriving it client-side.
                                 delivery=fare,
                                 items=clean_items))
    return analytics_mod.stamp_cookie(resp, vid)


# ================================================== public: payment receipt
ALLOWED_PAYMENT_METHODS = (
    "UBA bank transfer (₦ Naira)",
    "MTN MoMo Benin (F CFA)",
    "Moov Money Togo (F CFA)",
    "Other bank transfer",
)


@api.post("/payment-proof")
@sec.require_csrf
def payment_proof():
    """A customer sends their receipt. The original file is stored in the
    configured storage and listed in the authenticated admin portal."""
    limited = sec.guard("payment-proof", limit=20, window=3600,
                        key_extra=sec.clean(request.form.get("email"), 120))
    if limited: return limited
    bounced = sec.recaptcha_gate("receipt")
    if bounced: return bounced

    f = request.files.get("file") or request.files.get("receipt")
    if not f:
        return jsonify(ok=False, error="Choose your receipt file (JPG, PNG, PDF, DOC or DOCX)."), 400
    data = f.read(storage.MAX_RECEIPT_BYTES + 1)
    ok, msg, ext = storage.validate_upload(data, f.filename or "", allow_pdf=True,
                                           max_bytes=storage.MAX_RECEIPT_BYTES)
    if not ok:
        return jsonify(ok=False, error=msg), 400

    form = request.form
    get = lambda k: sec.clean(form.get(k), 300)
    email = sec.clean_email(form.get("email"))
    if not email:
        return jsonify(ok=False, error="Enter the email address you used for the order."), 400
    name = sec.clean(form.get("name"), 120)
    if not name:
        return jsonify(ok=False, error="Enter your name."), 400
    phone = sec.clean(form.get("phone"), 60)
    if not phone:
        return jsonify(ok=False, error="Enter your phone number."), 400
    method = sec.clean(form.get("method"), 80) or "Other bank transfer"
    if method not in ALLOWED_PAYMENT_METHODS:
        method = "Other bank transfer"

    order_id = sec.clean(form.get("orderId"), 24).upper()
    if not re.match(r"^[A-Z0-9-]{4,24}$", order_id or ""):
        order_id = "NO-ID"

    mime = storage.mime_for(ext)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", (f.filename or f"receipt.{ext}"))[:80]
    attach_name = f"payment-{order_id}-{safe_name}"[:110]

    stored, msg2, url = storage.save_image(data, "proofs", f.filename or "",
                                           allow_pdf=True, max_bytes=storage.MAX_RECEIPT_BYTES)
    if not stored:
        return jsonify(ok=False, error=msg2), 500

    details = {
        "name": name, "phone": phone, "email": email, "orderId": order_id,
        "items": sec.clean(form.get("items"), 600),
        "quantity": sec.clean(form.get("quantity"), 60),
        "method": method,
        "total": sec.clean(form.get("total"), 60),
        "amount": sec.clean(form.get("amount"), 60),
        "note": sec.clean(form.get("note"), 600),
        "currency": sec.clean(form.get("currency"), 3),
        "at": _utcnow(),
    }

    # Receipts are stored in Supabase PostgreSQL and Supabase Storage.

    proof_row = {
        "id": order_id, "order_id": order_id, "name": name, "phone": phone,
        "email": email, "method": method, "items": details["items"],
        "quantity": details["quantity"], "amount": details["amount"],
        "note": details["note"], "file_url": url, "file_name": attach_name,
        "file_size": len(data), "file_type": mime,
    }

    prod_source = bool(catalog_mod._prod_source())
    if prod_source:
        # The file is already in the private bucket; the RECORD must land in
        # Supabase first. A failure removes the orphan object and returns a
        # clear error instead of pretending the receipt was saved.
        try:
            from supabase_store import create_receipt_strict
            saved = bool(create_receipt_strict(proof_row))
        except Exception as exc:
            print(f"[supabase] receipt write failed: {exc}")
            saved = False
        if not saved:
            try:
                storage.delete_upload(url)
            except Exception:
                pass
            return jsonify(ok=False, error=(
                "Your receipt could not be saved right now. Please try again "
                "in a moment.")), 503
        try:
            execute(
                "INSERT INTO payment_proofs (order_id, name, phone, email, method, items, quantity, "
                "amount, note, file_url, file_name, file_size, mime) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (order_id, name, phone, email, method, details["items"], details["quantity"],
                 details["amount"], details["note"], url, attach_name, len(data), mime),
            )
        except Exception as exc:
            print(f"[sqlite] receipt cache write skipped: {exc}")
    else:
        execute(
            "INSERT INTO payment_proofs (order_id, name, phone, email, method, items, quantity, "
            "amount, note, file_url, file_name, file_size, mime) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, name, phone, email, method, details["items"], details["quantity"],
             details["amount"], details["note"], url, attach_name, len(data), mime),
        )
        # mirror into Supabase when enabled (best effort outside production)
        from supabase_store import create_receipt as _sb_create_receipt
        if Config.SUPABASE_ENABLED:
            try:
                _sb_create_receipt(proof_row)
            except Exception as exc:
                print(f"[supabase] receipt mirror skipped: {exc}")
    audit("customer", "payment_proof", f"{order_id} {attach_name} stored", _ip())

    # Email the shop the receipt WITH THE CUSTOMER'S OWN FILE ATTACHED - the
    # exact bytes they uploaded. Fire-and-forget over HTTPS (Resend/Brevo)
    # first because Render's free instances block outbound SMTP; the upload
    # must never wait on a mail provider. No-op until MAIL_FROM, MAIL_TO and
    # a provider key are configured (see ENVIRONMENT_VARIABLES.md).
    try:
        import mailer
        mailer.notify_receipt_async(proof_row, attach_name, data, mime)
    except Exception:
        pass

    return jsonify(ok=True, stored=True, url=url,
                   fileName=attach_name, size=len(data),
                   message="Your receipt is saved in the admin portal. We will confirm your payment shortly.")


@api.get("/payment-methods")
def payment_methods():
    return jsonify(ok=True, methods=list(ALLOWED_PAYMENT_METHODS))


@api.get("/orders/<oid>")
def public_order(oid):
    """Minimal, rate-limited status lookup for the Track-order page."""
    limited = sec.guard("order-lookup", limit=30, window=600)
    if limited: return limited
    row = one("SELECT id, payload, at, status, total, currency, items_count, customer_name, city "
              "FROM orders WHERE id=?", (sec.clean(oid, 24).upper(),))
    if not row:
        return jsonify(ok=False, error="We could not find that order id."), 404
    try:
        payload = json.loads(row["payload"] or "{}")
    except ValueError:
        payload = {}
    out = dict(row)
    out.pop("payload", None)
    out["items"] = [{"name": i.get("name", ""), "qty": i.get("qty", 1),
                     "price": i.get("price", 0), "color": i.get("color", "")}
                    for i in (payload.get("items") or [])]
    return jsonify(ok=True, order=out)

# =================================================================== admin
@api.post("/admin/login")
def admin_login():
    d = request.get_json(silent=True) or {}
    limited = sec.guard("admin-login", limit=6, window=300,
                        key_extra=sec.clean((d or {}).get("email"), 120) or "single")
    if limited: return limited
    email = sec.clean_email(d.get("email"))
    pw = d.get("password") or ""
    if not pw:
        return jsonify(ok=False, error="Password is required."), 400
    if not email:
        email = authmod.sole_admin_email()
        if not email:
            # several admin accounts -> the single-account convenience cannot
            # know which shared-password account to open
            return jsonify(ok=False, error="Enter the admin email address to sign in."), 400
    # ADMIN_MASTER_PASSWORD is the primary credential: checked first inside
    # auth.verify_login(), read live from the environment, so a new value
    # saved in the Render dashboard works on the very next attempt - no
    # restart, no database update. ADMIN_BOOTSTRAP_PASSWORD stays as the
    # secondary/permanent credential, and only CONFIGURED admin accounts can
    # ever be opened (no enumeration).
    known = authmod.is_known_admin(email)
    via_master = known and authmod.master_password_matches(pw)
    # identical response for unknown email vs wrong password (no enumeration)
    ok = via_master or (known and authmod.verify_login(email, pw))
    if not ok:
        audit(email or "?", "admin.login_failed", "bad credentials", _ip())
        return jsonify(ok=False, error="Invalid email or password."), 401
    authmod.login(email)
    sec.clear_rate("admin-login", email)
    audit(email, "admin.login",
          "master password" if via_master else "password", _ip())
    return jsonify(ok=True, email=email, csrf=sec.issue_csrf())

@api.post("/admin/logout")
def admin_logout():
    actor = authmod.current_admin()
    if actor: audit(actor, "admin.logout", "", _ip())
    authmod.logout()
    return jsonify(ok=True, csrf=sec.issue_csrf())

@api.get("/admin/session")
def admin_session():
    a = authmod.current_admin()
    return jsonify(ok=True, authenticated=bool(a), email=a, csrf=sec.issue_csrf())

# ------------------------------------------------------------ admin: stock
@api.get("/admin/stock")
@authmod.require_admin
def admin_stock():
    rows = _stock_rows_for_read()
    if rows is None:
        return jsonify(ok=False, error="Stock is temporarily unavailable."), 503
    rows = sorted((dict(r) for r in rows),
                  key=lambda r: (str(r.get("product_id") or ""), str(r.get("variant_key") or "")))
    return jsonify(ok=True, items=rows)

@api.put("/admin/stock")
@authmod.require_admin
@sec.require_csrf
def admin_stock_set():
    d = request.get_json(silent=True) or {}
    pid = sec.clean(d.get("productId"), 64)
    variant = sec.clean(d.get("variant"), 64) or "__default__"
    label = sec.clean(d.get("label"), 120) or None
    qty = sec.clean_int(d.get("qty"), None, 0, 10**7)
    thr = sec.clean_int(d.get("lowThreshold"), Config.LOW_STOCK_THRESHOLD, 0, 10**7)
    if not pid or qty is None:
        return jsonify(ok=False, error="productId and qty are required."), 400
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    if catalog_mod._prod_source():
        from supabase_store import load_variant_stock_strict, replace_variant_stock_strict
        rows = load_variant_stock_strict()
        if rows is None:
            return jsonify(ok=False, error="Stock is temporarily unavailable. No changes were made."), 503
        updated = []
        found = False
        for row in rows:
            row = dict(row)
            if str(row.get("product_id") or "") == pid and str(row.get("variant_key") or "__default__") == variant:
                row.update({"product_id": pid, "variant_key": variant, "variant_label": label or row.get("variant_label"),
                            "qty": qty, "low_threshold": thr, "updated_at": now})
                found = True
            updated.append(row)
        if not found:
            updated.append({"product_id": pid, "variant_key": variant, "variant_label": label,
                            "qty": qty, "low_threshold": thr, "updated_at": now})
        saved = replace_variant_stock_strict(updated)
        if saved is None:
            return jsonify(ok=False, error="Stock could not be saved to Supabase. No changes were made."), 503
        items = sorted((dict(r) for r in saved),
                       key=lambda r: (str(r.get("product_id") or ""), str(r.get("variant_key") or "")))
    else:
        execute("INSERT INTO variant_stock (product_id, variant_key, variant_label, qty, low_threshold, updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(product_id, variant_key) DO UPDATE SET "
                "qty=excluded.qty, low_threshold=excluded.low_threshold, "
                "variant_label=COALESCE(excluded.variant_label, variant_stock.variant_label), updated_at=excluded.updated_at",
                (pid, variant, label, qty, thr, now))
        items = [dict(r) for r in query(
            "SELECT product_id, variant_key, variant_label, qty, low_threshold, updated_at "
            "FROM variant_stock ORDER BY product_id, variant_key")]
        if Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY:
            try:
                from supabase_store import save_variant_stock
                save_variant_stock(items)
            except Exception:
                pass
    audit(authmod.current_admin(), "stock.set", f"{pid}/{variant} = {qty}", _ip())
    return jsonify(ok=True, item=next((r for r in items if r.get("product_id") == pid and
                                       (r.get("variant_key") or "__default__") == variant), None),
                   items=items)

@api.get("/admin/low-stock")
@authmod.require_admin
def low_stock():
    rows = _stock_rows_for_read()
    if rows is None:
        return jsonify(ok=False, error="Stock is temporarily unavailable."), 503
    items = [dict(r) for r in rows if int(r.get("qty") or 0) <= int(r.get("low_threshold") or 0)]
    items.sort(key=lambda r: int(r.get("qty") or 0))
    return jsonify(ok=True, count=len(items), items=items)

# --------------------------------------------------------- admin: analytics
@api.get("/admin/most-viewed")
@authmod.require_admin
def admin_most_viewed():
    limit = sec.clean_int(request.args.get("limit"), 20, 1, 200)
    rows = query("SELECT product_id, views, updated_at FROM product_views ORDER BY views DESC LIMIT ?", (limit,))
    return jsonify(ok=True, items=[dict(r) for r in rows])

@api.get("/admin/audit")
@authmod.require_admin
def admin_audit():
    limit = sec.clean_int(request.args.get("limit"), 50, 1, 500)
    rows = query("SELECT actor, action, detail, ip, at FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    return jsonify(ok=True, items=[dict(r) for r in rows])

# ------------------------------------------------------- admin: analytics
@api.get("/admin/analytics")
@authmod.require_admin
def admin_analytics():
    """Dashboard payload: traffic, top pages, top products, conversion."""
    days = sec.clean_int(request.args.get("days"), 30, 1, 400)
    return jsonify(analytics_mod.report(days))

@api.get("/admin/live")
@authmod.require_admin
def admin_live():
    return jsonify(ok=True, windowSeconds=Config.LIVE_WINDOW_SECONDS,
                   visitors=analytics_mod.live_now(),
                   activity=analytics_mod.recent_activity())

# ------------------------------------------------------------ admin: sales
@api.get("/admin/sales")
@authmod.require_admin
def admin_sales():
    """Confirmed-only sales totals; pending orders are counted separately."""
    raw = (request.args.get("days") or "30").strip().lower()
    days = "all" if raw == "all" else sec.clean_int(raw, 30, 1, 3650)
    return jsonify(analytics_mod.sales_report(days))

@api.get("/admin/sales.csv")
@authmod.require_admin
def admin_sales_csv():
    raw = (request.args.get("days") or "30").strip().lower()
    days = "all" if raw == "all" else sec.clean_int(raw, 30, 1, 3650)
    body = analytics_mod.sales_csv(days)
    resp = make_response("\ufeff" + body)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=jaura-sales.csv"
    return resp

# ---------------------------------------------------------- admin: orders
def _order_row(r):
    try:
        payload = json.loads(r["payload"] or "{}")
    except ValueError:
        payload = {}
    out = dict(r)
    out.pop("payload", None)
    out["customer"] = payload.get("customer") or {}
    out["items"] = payload.get("items") or []
    out["proofUrl"] = r["proof_url"] or payload.get("proofUrl") or ""
    return out

@api.get("/admin/orders")
@authmod.require_admin
def admin_orders():
    limit = sec.clean_int(request.args.get("limit"), 200, 1, 1000)
    status = sec.clean(request.args.get("status"), 20)
    q = sec.clean(request.args.get("q"), 80).lower()
    sql = ("SELECT id, payload, email, customer_name, phone, country, city, zone, address, "
           "note, payment, proof_url, items_count, total, currency, source, status, at, updated_at "
           "FROM orders")
    params = []
    if status in STATUSES:
        sql += " WHERE status=?"; params.append(status)
    sql += " ORDER BY at DESC LIMIT ?"
    params.append(limit)
    rows = query(sql, tuple(params))
    out = [_order_row(r) for r in rows]
    if q:
        out = [o for o in out if q in json.dumps(o, ensure_ascii=False).lower()]
    return jsonify(ok=True, count=len(out), orders=out)

@api.get("/admin/orders.csv")
@authmod.require_admin
def admin_orders_csv():
    rows = query("SELECT id, at, status, customer_name, phone, email, country, city, zone, "
                 "address, note, payment, total, currency, items_count, proof_url FROM orders "
                 "ORDER BY at DESC")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "date", "status", "name", "phone", "email", "country", "city",
                "zone", "address", "note", "payment", "total", "currency", "items", "proof"])
    for r in rows:
        w.writerow([r["id"], r["at"], r["status"], r["customer_name"], r["phone"], r["email"],
                    r["country"], r["city"], r["zone"], r["address"], r["note"], r["payment"],
                    r["total"], r["currency"], r["items_count"], r["proof_url"]])
    resp = make_response("\ufeff" + buf.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=jaura-orders.csv"
    return resp

@api.get("/admin/payment-proofs")
@authmod.require_admin
def admin_payment_proofs():
    """Read payment receipts from the configured production store."""
    limit = sec.clean_int(request.args.get("limit"), 200, 1, 1000)
    if catalog_mod._prod_source():
        from supabase_store import load_receipts
        rows = load_receipts(limit)
        if rows is None:
            return jsonify(ok=False, error="Receipts are temporarily unavailable."), 503
    else:
        rows = [dict(r) for r in query(
            "SELECT id, order_id, name, phone, email, method, items, quantity, amount, "
            "note, file_url, file_name, file_size, mime, at "
            "FROM payment_proofs ORDER BY at DESC LIMIT ?", (limit,))]
    proofs = []
    for row in rows:
        d = dict(row)
        d.setdefault("at", d.get("created_at") or "")
        d.setdefault("mime", d.get("file_type") or "")
        if d.get("file_url"):
            d["file_url"] = storage.signed_url_for(d["file_url"])
        proofs.append(d)
    response = jsonify(ok=True, count=len(proofs), proofs=proofs)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@api.delete("/admin/payment-proofs/<pid>")
@authmod.require_admin
@sec.require_csrf
def admin_payment_proof_delete(pid):
    """Delete one payment receipt and its Storage object."""
    prod_source = bool(catalog_mod._prod_source())
    if prod_source:
        from supabase_store import load_receipt, delete_receipt_strict
        row = load_receipt(pid)
        if not row:
            return jsonify(ok=False, error="That receipt is no longer there."), 404
        file_url = row.get("file_url") or ""
        if file_url and not storage.delete_upload(file_url):
            return jsonify(ok=False, error=(
                "The receipt file could not be removed from Storage. No changes were made.")), 503
        if not delete_receipt_strict(receipt_id=pid, order_id=row.get("order_id"), file_url=file_url):
            return jsonify(ok=False, error=(
                "The receipt could not be removed from Supabase. No changes were made.")), 503
        audit(authmod.current_admin(), "payment_proof.delete",
              f"{pid} {row.get('order_id') or ''} stored", _ip())
        return jsonify(ok=True, id=str(pid), fileRemoved=bool(file_url))

    try:
        local_id = int(pid)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Invalid receipt id."), 400
    row = one("SELECT id, order_id, file_url, file_name FROM payment_proofs WHERE id=?", (local_id,))
    if not row:
        return jsonify(ok=False, error="That receipt is no longer there."), 404
    file_url = row["file_url"] or ""
    removed = storage.delete_upload(file_url) if file_url else False
    execute("DELETE FROM payment_proofs WHERE id=?", (local_id,))
    if Config.SUPABASE_ENABLED:
        try:
            from supabase_store import delete_receipt
            delete_receipt(receipt_id=local_id, order_id=row["order_id"], file_url=file_url)
        except Exception:
            pass
    audit(authmod.current_admin(), "payment_proof.delete",
          f"{local_id} {row['order_id'] or ''} file_removed={removed}", _ip())
    return jsonify(ok=True, id=local_id, fileRemoved=removed)


@api.patch("/admin/orders/<oid>")
@authmod.require_admin
@sec.require_csrf
def admin_order_update(oid):
    """Confirm / decline / reopen an order. Nothing is ever deleted here."""
    oid = sec.clean(oid, 24).upper()
    d = request.get_json(silent=True) or {}
    status = sec.clean(d.get("status"), 20)
    if status not in STATUSES:
        return jsonify(ok=False, error="status must be pending, confirmed or declined"), 400
    row = one("SELECT id, payload, status FROM orders WHERE id=?", (oid,))
    if not row:
        return jsonify(ok=False, error="Order not found."), 404
    try:
        payload = json.loads(row["payload"] or "{}")
    except ValueError:
        payload = {}
    old_status = row["status"] or payload.get("status") or "pending"
    payload["status"] = status
    payload["updatedAt"] = _utcnow()
    payload["updatedBy"] = authmod.current_admin()
    if old_status != status:
        _sync_order_stock(payload, old_status, status, actor=authmod.current_admin())
    execute("UPDATE orders SET status=?, payload=?, updated_at=? WHERE id=?",
            (status, json.dumps(payload, ensure_ascii=False), _utcnow(), oid))
    audit(authmod.current_admin(), f"order.{status}", oid, _ip())

    # mirror the new status so the Supabase copy (used by the boot restore)
    # cannot put a stale pending order back
    if Config.SUPABASE_ENABLED:
        try:
            from supabase_store import update_order as _sb_update_order
            _sb_update_order(oid, status=status, payload=payload)
        except Exception:
            pass

    return jsonify(ok=True, id=oid, status=status)


@api.delete("/admin/orders/<oid>")
@authmod.require_admin
@sec.require_csrf
def admin_order_delete(oid):
    """Delete an order (with its uploaded payment receipt). Used only from the
    admin portal's explicit Delete button; it never runs on a status change."""
    oid = sec.clean(oid, 24).upper()
    row = one("SELECT id, status, payload FROM orders WHERE id=?", (oid,))
    if not row:
        return jsonify(ok=False, error="Order not found."), 404
    try:
        payload = json.loads(row["payload"] or "{}")
    except ValueError:
        payload = {}
    old_status = row["status"] or payload.get("status") or "pending"
    if old_status == "confirmed":
        _sync_order_stock(payload, "confirmed", "pending",
                         actor=authmod.current_admin())
    elif catalog_mod._prod_source():
        # stock was reserved at checkout; deleting a pending order frees it
        _sync_order_stock(payload, "pending", "declined",
                          actor=authmod.current_admin())

    # take the receipts out first — their files go too, so a deleted receipt
    # can no longer be downloaded from its old URL (and cannot be restored
    # from Supabase at the next boot, which is how they used to come back)
    proofs = query("SELECT id, file_url FROM payment_proofs WHERE order_id=?", (oid,)) or []
    files_removed = 0
    for p in proofs:
        url = (p["file_url"] or "") if "file_url" in p.keys() else ""
        if not url:
            continue
        try:
            if storage.delete_upload(url):
                files_removed += 1
        except Exception:
            pass
    prod_source = bool(catalog_mod._prod_source())
    if prod_source:
        # Supabase row + storage objects must go FIRST, or the boot restore
        # would resurrect the deleted order (and its receipts).
        try:
            from supabase_store import delete_order as _sb_delete_order
            deleted = bool(_sb_delete_order(oid))
        except Exception as exc:
            print(f"[supabase] order delete failed: {exc}")
            deleted = False
        if not deleted:
            return jsonify(ok=False, error=(
                "The order could not be deleted from Supabase. "
                "No changes were made.")), 503
    execute("DELETE FROM payment_proofs WHERE order_id=?", (oid,))
    execute("DELETE FROM orders WHERE id=?", (oid,))

    if Config.SUPABASE_ENABLED and not prod_source:
        # the mirrored copy must go as well, or the boot-time restore from
        # Supabase quietly brings the order (and its receipts) straight back
        try:
            from supabase_store import delete_order as _sb_delete_order
            _sb_delete_order(oid)
        except Exception:
            pass

    audit(authmod.current_admin(), "order.delete",
          f"{oid} receipts={len(proofs)} files_removed={files_removed}", _ip())
    return jsonify(ok=True, id=oid, filesRemoved=files_removed)

# -------------------------------------------------------- admin: products
@api.post("/admin/products")
@authmod.require_admin
@sec.require_csrf
def admin_product_upsert():
    """Save one product. Live for the next visitor immediately."""
    d = request.get_json(silent=True) or {}
    result = catalog_mod.upsert(d.get("product") or d, authmod.current_admin())
    product, action = result[0], result[1]
    mirrored = result[2] if len(result) > 2 else True
    if not product:
        if action == "test-fixture":
            return jsonify(ok=False, error=(
                "That is a test product from the test suite, not a shop piece. "
                "It cannot be added to the storefront. Delete it instead.")), 400
        if action == "error" or (mirrored is False and catalog_mod._prod_source()):
            return jsonify(ok=False, error=(
                "The product could not be saved to Supabase. No changes were made.")), 503
        return jsonify(ok=False, error="A product needs at least a name."), 400
    return jsonify(ok=True, product=product, action=action, mirrored=mirrored,
                   meta=catalog_mod.meta())

@api.delete("/admin/products/<pid>")
@authmod.require_admin
@sec.require_csrf
def admin_product_delete(pid):
    """Soft-delete one product. In production the tombstone MUST land in
    Supabase first: a failed portal call never reports success, so the admin
    can retry instead of believing a product is gone while it still sells.

    Soft-deleting the products-table row alone is not enough for a seed
    product: catalog.merged() unions the 258 bundled seed rows on every
    read, so the durable deleted-ids list in growth_settings must be
    written too (see catalog.remove / supabase_store.add_deleted_id).
    """
    pid = sec.clean(pid, 64)
    if catalog_mod._prod_source():
        from supabase_store import delete_products_strict, add_deleted_id
        if not delete_products_strict([pid]):
            return jsonify(ok=False, error=(
                "The product could not be deleted from Supabase. "
                "No changes were made.")), 503
        # Durable tombstone so a seed product stays gone across redeploys.
        # A swallowed failure here used to report "deleted" while the
        # growth_settings write never landed - the product then came back
        # after the next deploy. Surface the failure so the admin retries
        # instead of believing the product is gone for good.
        if not add_deleted_id(pid):
            return jsonify(ok=False, error=(
                "The product was removed from the catalogue, but its "
                "deletion could not be recorded in the durable tombstone "
                "list (Supabase growth_settings write failed). Tap Delete "
                "again so it stays gone.")), 503
        catalog_mod._sync_repo_async()
    else:
        catalog_mod.remove(pid, authmod.current_admin())
    audit(authmod.current_admin(), "product.delete", pid, _ip())
    return jsonify(ok=True, id=pid, meta=catalog_mod.meta())

@api.put("/admin/products")
@authmod.require_admin
@sec.require_csrf
def admin_products_replace():
    """Replace the whole catalogue (CSV / bulk import)."""
    d = request.get_json(silent=True) or {}
    products = d.get("products")
    if not isinstance(products, list):
        return jsonify(ok=False, error="Send {products: [...]}."), 400
    kept, rejected = catalog_mod.replace_all(products, authmod.current_admin())
    return jsonify(ok=True, saved=len(kept), rejected=rejected, meta=catalog_mod.meta())

# ---------------------------------------------------- admin: repo / dual sync
@api.post("/admin/photos/repair")
@authmod.require_admin
@sec.require_csrf
def admin_photos_repair():
    """Re-point products whose stored photo is missing from the bucket.

    A product whose uploaded photo no longer exists in Supabase Storage shows
    the branded "PHOTO COMING SOON" card forever, however often it is saved
    with a new photo - the row keeps pointing at the dead URL. This walks the
    live catalogue, finds covers that are really missing, swaps in a photo
    that does exist (a sibling gallery entry, otherwise a committed repo photo
    for the slug) and saves, so the fix survives the next deploy.
    """
    limit = sec.clean_int(request.args.get("limit"), 80, 1, 200)
    report = catalog_mod.repair_dead_photos(limit=limit,
                                            actor=authmod.current_admin())
    audit(authmod.current_admin(), "product.photos_repair",
          f"checked={report.get('checked')} missing={len(report.get('missing') or [])} "
          f"repaired={len(report.get('repaired') or [])}", _ip())
    return jsonify(ok=True, **report)


@api.post("/photo-missing")
def photo_missing():
    """A browser tells us one of our product photos 404ed; heal the row.

    The storefront's image `onerror` handler already swaps in the placeholder
    (nothing ever shows a broken icon); this is the other half - the shop
    learns WHICH photo is really gone and, when the product still has a usable
    one, re-points the row so every device and every later deploy shows the
    real photo again. Only a same-origin /uploads reference that a live
    product row actually carries may be reported; anything else is ignored.
    """
    limited = sec.guard("photo-missing", limit=60, window=600)
    if limited:
        return limited
    d = request.get_json(silent=True) or {}
    url = sec.safe_url(d.get("url") or "", 500)
    if not url:
        return jsonify(ok=False, error="url required"), 400
    try:
        import storage as _storage
        key = _storage.own_upload_path(url)
    except Exception:
        key = ""
    if not key:
        return jsonify(ok=False, error="Not one of our uploads."), 400
    hit = None
    for p in catalog_mod.merged(include_hidden=True):
        refs = [p.get("image")] + list(p.get("images") or [])
        if any(_storage.own_upload_path(str(r or "")) == key for r in refs):
            hit = p
            break
    if hit is None:
        return jsonify(ok=True, repaired=False, reason="no product uses that photo")
    try:
        if _storage.object_exists(hit.get("image")) is not False:
            return jsonify(ok=True, repaired=False, reason="the object is there")
    except Exception:
        return jsonify(ok=True, repaired=False, reason="could not check")
    image = catalog_mod.repair_product_photo(hit.get("id"))
    return jsonify(ok=True, repaired=bool(image), image=image or "")


@api.get("/admin/sync/status")
@authmod.require_admin
def admin_sync_status():
    """Report whether Supabase and the GitHub repo sync are configured."""
    sb = Config.SUPABASE_ENABLED
    try:
        from supabase_store import ping as _sb_ping
        health = _sb_ping()
    except Exception:
        health = "unreachable" if sb else "not_configured"
    ght = bool(Config.GITHUB_TOKEN)
    repo = Config.GITHUB_REPOSITORY or ""
    # try to resolve the repo from git remote when not set explicitly
    if not repo:
        try:
            import repo_sync
            repo = repo_sync._resolve_repo()
        except Exception:
            repo = ""
    # Surface the publish gate so the portal can explain WHY the repository
    # copy cannot be updated from this instance.
    try:
        import repo_sync
        blocked_reason = repo_sync.repo_sync_blocked_reason()
    except Exception:
        blocked_reason = "Repository sync availability could not be determined."
    return jsonify(ok=True, supabase=sb, supabaseHealth=health, gitToken=ght,
                   gitRepo=repo, gitBranch=Config.GITHUB_BRANCH,
                   onWrite=bool(Config.REPO_SYNC_ON_WRITE),
                   syncBlockedReason=blocked_reason)


@api.post("/admin/sync/repo")
@authmod.require_admin
@sec.require_csrf
def admin_sync_repo():
    """Regenerate the repository data files and commit/push to GitHub.

    This is the manual "Sync to GitHub" button in the admin portal. It applies
    the same best-effort path as the automatic post-write sync but runs it
    synchronously so the admin gets an immediate result. It is gated by the
    same rule as every publish path (repo_sync.repo_sync_blocked_reason): on
    anything but a deployed production/staging instance it answers 409 with
    the reason instead of publishing scratch catalogue state to the repo.
    """
    limited = sec.guard("repo-sync", limit=10, window=600)
    if limited:
        return limited
    import repo_sync
    reason = repo_sync.repo_sync_blocked_reason()
    if reason:
        return jsonify(ok=False, error=reason, blocked=True), 409
    try:
        ok, report = repo_sync.regenerate(commit=True, push=True)
    except Exception as exc:
        return jsonify(ok=False, error=f"Sync failed: {exc}"), 500
    audit(authmod.current_admin(), "admin.repo_sync",
          f"ok={ok} committed={report.get('committed')} pushed={report.get('pushed')}",
          _ip())
    return jsonify(ok=bool(ok), **report)


@api.get("/admin/mail/status")
@authmod.require_admin
def admin_mail_status():
    """Which email transport is live for order/receipt mails (no secrets).

    The Orders tab shows this above the receipts table: orders and customer
    receipts are emailed to the shop, the receipt WITH the customer's own
    file attached, over HTTPS (Resend/Brevo) first because Render's free
    instances block outbound SMTP ports, with SMTP as a fallback.
    """
    import mailer
    st = mailer.transport_status()
    return jsonify(ok=True, **st)


@api.post("/admin/mail/test")
@authmod.require_admin
@sec.require_csrf
def admin_mail_test():
    """The Orders tab's "Email a test" button: one probe email to MAIL_TO."""
    limited = sec.guard("mail-test", limit=5, window=600)
    if limited:
        return limited
    import mailer
    if not mailer.configured():
        st = mailer.transport_status()
        return jsonify(ok=False, error=(
            "Shop email is not configured yet. Set " + ", ".join(st["missing"])
            + " in Render (see ENVIRONMENT_VARIABLES.md), then redeploy."),
            missing=st["missing"]), 400
    ok, detail = mailer.send_mail("Jaura Store test email",
                                  mailer.test_email_html())
    audit(authmod.current_admin(), "admin.mail_test",
          f"ok={ok} provider={mailer.provider()} {detail}"[:400], _ip())
    if not ok:
        return jsonify(ok=False, error=f"Test email failed — {detail}",
                       provider=mailer.provider()), 502
    return jsonify(ok=True, provider=mailer.provider(),
                   to=mailer.transport_status()["to"],
                   message="Test email sent — check the inbox (and spam).")


@api.post("/admin/uploads/image")
@authmod.require_admin
@sec.require_csrf
def admin_upload_image():
    """Product photo upload. Stored as a real file, never as a data URL."""
    limited = sec.guard("admin-upload", limit=60, window=600)
    if limited: return limited
    f = request.files.get("file") or request.files.get("image")
    if not f:
        return jsonify(ok=False, error="No file received."), 400
    data = f.read(storage.MAX_BYTES + 1)
    ok, msg, _ext = storage.validate_image(data, f.filename or "")
    if not ok:
        return jsonify(ok=False, error=msg), 400
    ok, msg, url = storage.save_image(data, "products", f.filename or "")
    if not ok:
        return jsonify(ok=False, error=msg), 500
    return jsonify(ok=True, url=url)

# ------------------------------------------------------ admin: hero video
@api.post("/admin/uploads/video")
@authmod.require_admin
@sec.require_csrf
def admin_upload_video():
    """Homepage hero video upload (MP4 or WebM). Stored like every other
    upload: as a real file under /uploads/, never inside the database."""
    limited = sec.guard("admin-upload", limit=20, window=600)
    if limited: return limited
    f = request.files.get("file") or request.files.get("video")
    if not f:
        return jsonify(ok=False, error="No file received."), 400
    data = f.read(storage.MAX_VIDEO_BYTES + 1)
    ok, msg, url = storage.save_video(data, "videos", f.filename or "")
    if not ok:
        return jsonify(ok=False, error=msg), 400
    audit(authmod.current_admin(), "site.hero_video_upload", url, _ip())
    return jsonify(ok=True, url=url)


@api.post("/admin/uploads/category")
@authmod.require_admin
@sec.require_csrf
def admin_upload_category():
    """Category cover / asset upload (image, video or document). Stored as a
    real file under /uploads/categories/, never as a data URL. Documents are
    served as attachments, never rendered inline in the site's origin."""
    limited = sec.guard("admin-upload", limit=40, window=600)
    if limited: return limited
    f = request.files.get("file") or request.files.get("image")
    if not f:
        return jsonify(ok=False, error="No file received."), 400
    data = f.read(storage.MAX_VIDEO_BYTES + 1)
    ok, msg, url = storage.save_asset(data, "categories", f.filename or "")
    if not ok:
        return jsonify(ok=False, error=msg), 400
    ext = storage._ext_from_bytes(data[:32])
    audit(authmod.current_admin(), "site.category_asset_upload", url, _ip())
    return jsonify(ok=True, url=url, kind=storage.kind_for(ext))


@api.post("/admin/uploads/product")
@authmod.require_admin
@sec.require_csrf
def admin_upload_product():
    """Product media upload: an image OR a video. The format is decided by the
    file's own bytes (never the name), so the same slot accepts a photo from
    the gallery or a video from the gallery/files. Stored under
    /uploads/products/ with the URL kept in the product's media list."""
    limited = sec.guard("admin-upload", limit=60, window=600)
    if limited: return limited
    f = request.files.get("file") or request.files.get("image")
    if not f:
        return jsonify(ok=False, error="No file received."), 400
    data = f.read(storage.MAX_VIDEO_BYTES + 1)
    ext = storage._ext_from_bytes(data[:32])
    kind = storage.kind_for(ext)
    if kind == "video":
        ok, msg, _vext = storage.validate_video(data, f.filename or "")
        if not ok:
            return jsonify(ok=False, error=msg), 400
        ok, msg, url = storage.save_video(data, "products", f.filename or "")
    elif kind == "image":
        ok, msg, _ext = storage.validate_image(data, f.filename or "")
        if not ok:
            return jsonify(ok=False, error=msg), 400
        ok, msg, url = storage.save_image(data, "products", f.filename or "")
    else:
        return jsonify(ok=False, error="Only JPG, PNG, WebP, GIF, AVIF, MP4, WebM or MOV files can be uploaded here."), 400
    if not ok:
        return jsonify(ok=False, error=msg), 500
    audit(authmod.current_admin(), "site.product_media_upload", url, _ip())
    return jsonify(ok=True, url=url, kind=kind)


@api.post("/admin/uploads/hero")
@authmod.require_admin
@sec.require_csrf
def admin_upload_hero():
    """Homepage hero upload: a video OR a document (or an image poster). Like
    every upload it is stored as a real file under /uploads/. Documents are
    served as attachments and never rendered inline in the site's origin."""
    limited = sec.guard("admin-upload", limit=20, window=600)
    if limited: return limited
    f = request.files.get("file") or request.files.get("video")
    if not f:
        return jsonify(ok=False, error="No file received."), 400
    data = f.read(storage.MAX_VIDEO_BYTES + 1)
    ok, msg, url = storage.save_asset(data, "videos", f.filename or "")
    if not ok:
        return jsonify(ok=False, error=msg), 400
    ext = storage._ext_from_bytes(data[:32])
    audit(authmod.current_admin(), "site.hero_asset_upload", url, _ip())
    return jsonify(ok=True, url=url, kind=storage.kind_for(ext))

# ----------------------------------------------------------- site settings
# Site configuration is deliberately not cached on disk. Every read comes from
# Supabase and every admin write is an immediate SQL/PostgREST update.
SITE_KEYS = ("bank_name", "account_number", "account_name",
             "referral_commission_percentage", "hero_banner_title",
             "hero_banner_subtitle", "contact_email", "contact_phone",
             "site_logo_url",
             # Checkout payment details: served from the Supabase row so the
             # storefront carries no hardcoded account number.
             "cfa_payment_provider", "cfa_payment_name",
             "cfa_payment_account", "cfa_payment_instructions",
             "togo_payment_provider", "togo_payment_name",
             "togo_payment_account", "togo_payment_instructions",
             "naira_payment_bank", "naira_payment_name",
             "naira_payment_account", "naira_payment_instructions",
             # Canonical shipping-note column. It was only reachable through
             # the legacy `shippingNote` alias, so an admin form posting the
             # real column name had it silently dropped.
             "shipping_note")

def _load_site():
    if Config.ENV == "testing":
        path = os.environ.get("SITE_CONFIG_PATH", "")
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"heroVideo":"", "heroPoster":"", "heroDoc":"", "logoUrl":"", "shopBannerUrl":"", "bannerFrom":"2026-09-15", "bannerTo":"2026-09-25", "convBanner":"", "convBannerFr":"", "convBold":"", "shippingNote":""}
    from supabase_settings import get_site_settings
    return get_site_settings()

@api.get("/site")
def site_config():
    try:
        resp = jsonify(ok=True, site=_site_payload(_load_site()))
        # Never cacheable: the bank details on the checkout come from this
        # answer, and a CDN (or a bfcache) holding yesterday's row after an
        # Admin edit is indistinguishable from "my edit disappeared".
        resp.headers["Cache-Control"] = "private, no-store"
        return resp
    except Exception:
        return jsonify(ok=False, error="Site settings are temporarily unavailable."), 503

# Legacy front-end keys mapped onto site_settings columns (kept so the
# branding / hero / banner controls still persist in production).
SITE_LEGACY_MAP = {
    "logoUrl": "site_logo_url",
    "heroVideo": "hero_video_url",
    "heroPoster": "hero_poster_url",
    "heroDoc": "hero_doc_url",
    "shopBannerUrl": "shop_banner_url",
    "shippingNote": "shipping_note",
    "bannerFrom": "banner_from",
    "bannerTo": "banner_to",
    "convBanner": "conv_banner",
    "convBannerFr": "conv_banner_fr",
    "convBold": "conv_bold",
}

# Reverse mapping used when serving /api/site back to the browser: the row is
# canonical (site_logo_url, hero_video_url, ...) but the pages also read the
# legacy aliases (logoUrl, heroVideo, ...), so a production response carries
# both shapes of every value.
SITE_LEGACY_ALIASES = {col: key for key, col in SITE_LEGACY_MAP.items()}


# ------------------------------------------------------------- delivery page
# The Delivery page (delivery.html) used to be frozen in the repo: the owner
# could not add a town without a developer. It is now an editable document
# stored in Supabase growth_settings and served on the site row; the static
# markup stays as the offline / static-host fallback.
DELIVERY_PAGE_FILE = _os.environ.get(
    "DELIVERY_PAGE_PATH",
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "delivery_page.json"))


def _normalize_delivery_page(raw):
    """Sanitise an incoming Delivery page. Returns None when unusable.

    Everything is stripped of markup (sec.clean) and length-capped, so a
    saved page can never inject HTML into the storefront. A page is only
    accepted when it has at least one block carrying at least one NAMED
    location - an empty document would silently blank the page for every
    customer.
    """
    if not isinstance(raw, dict):
        return None
    title = sec.clean(raw.get("title"), 120, allow_newlines=False)
    lead = sec.clean(raw.get("lead"), 240, allow_newlines=False)
    blocks_in = raw.get("blocks")
    if not isinstance(blocks_in, list):
        return None
    blocks = []
    for b in blocks_in[:12]:                       # at most 12 blocks
        if not isinstance(b, dict):
            continue
        heading = sec.clean(b.get("heading"), 80, allow_newlines=False)
        locs_in = b.get("locations")
        locations = []
        if isinstance(locs_in, list):
            for loc in locs_in[:40]:
                if not isinstance(loc, dict):
                    continue
                name = sec.clean(loc.get("name"), 80, allow_newlines=False)
                detail = sec.clean(loc.get("detail"), 300, allow_newlines=False)
                if not name:
                    continue
                locations.append({"name": name, "detail": detail})
        if not heading and not locations:
            continue
        blocks.append({"heading": heading, "locations": locations})
    if not any(b["locations"] for b in blocks):
        return None
    return {
        "title": title or "Delivery Locations",
        "lead": lead,
        "blocks": blocks,
    }


def _load_delivery_page():
    """The saved Delivery page, or None when the owner has not made one."""
    if Config.ENV == "testing":
        path = _os.environ.get("DELIVERY_PAGE_PATH", DELIVERY_PAGE_FILE)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data else None
    try:
        from supabase_store import enabled as _sb_enabled
        from supabase_store import load_delivery_page
        if _sb_enabled():
            return load_delivery_page()
    except Exception as exc:
        print(f"[supabase] delivery page read failed: {exc}")
    return None


def _save_delivery_page(page):
    """Write the page, then RE-READ it and return what is actually stored.

    The portal repaints from this answer, so the owner always sees what the
    store will serve rather than what they typed into the form.
    """
    if Config.ENV == "testing":
        path = _os.environ.get("DELIVERY_PAGE_PATH", DELIVERY_PAGE_FILE)
        _os.makedirs(_os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(page, fh, ensure_ascii=False, indent=2)
        _os.replace(tmp, path)
        return _load_delivery_page()
    from supabase_store import enabled as _sb_enabled
    from supabase_store import save_delivery_page
    if not _sb_enabled():
        raise RuntimeError("Supabase is not configured")
    if not save_delivery_page(page):
        raise RuntimeError("Supabase delivery page write failed")
    return _load_delivery_page()


@api.post("/admin/delivery-page")
@authmod.require_admin
@sec.require_csrf
def admin_delivery_page_save():
    d = request.get_json(silent=True) or {}
    page = _normalize_delivery_page(d.get("page") if isinstance(d.get("page"), dict) else d)
    if page is None:
        return jsonify(ok=False,
                       error="Add at least one location before saving."), 400
    try:
        saved = _save_delivery_page(page)
    except Exception as exc:
        print(f"[delivery-page] save failed: {exc}")
        return jsonify(ok=False, error="Could not save the delivery page."), 503
    audit(authmod.current_admin(), "delivery_page.save", "delivery_page", _ip())
    return jsonify(ok=True, page=saved)


def _site_payload(site):
    """Canonical site_settings row + the legacy front-end aliases."""
    out = dict(site or {})
    for col, alias in SITE_LEGACY_ALIASES.items():
        if col in out and alias not in out:
            out[alias] = out[col]
    # Delivery zones ride along with the site config so the storefront stops
    # hardcoding the fare list in checkout.html. Active zones only - an
    # inactive zone must not be selectable at checkout.
    try:
        out["delivery_zones"] = delivery.zones()
    except Exception:
        out["delivery_zones"] = []
    # The owner-editable Delivery page. None when nothing has been saved yet,
    # and the storefront then keeps the static delivery.html fallback.
    try:
        out["delivery_page"] = _load_delivery_page()
    except Exception:
        out["delivery_page"] = None
    return out
_SITE_URL_KEYS = frozenset(SITE_KEYS) | {
    "site_logo_url", "hero_video_url", "hero_poster_url", "hero_doc_url",
    "shop_banner_url", "logoUrl", "heroVideo", "heroPoster", "heroDoc",
    "shopBannerUrl",
}
_SITE_TEXT_KEYS = frozenset({"conv_banner", "conv_banner_fr", "conv_bold",
                             "convBanner", "convBannerFr", "convBold",
                             "shipping_note", "shippingNote"})


def _site_clear_list(d):
    """Columns the client says it is deliberately emptying.

    An empty field on its own means "leave what is stored alone": a form that
    has not loaded the live row yet (slow/failed GET /api/site, a cached bundle
    from before the deploy, a half-painted tab) must never be able to blank the
    account details it cannot show. Clearing is therefore explicit.
    """
    raw = d.get("_clear")
    if isinstance(raw, str):
        names = {raw}
    elif isinstance(raw, (list, tuple, set)):
        names = {str(x) for x in raw if isinstance(x, (str, bytes))}
    else:
        names = set()
    # Accept either spelling of every field (the canonical column, or the
    # legacy front-end alias it is stored under).
    return names | {SITE_LEGACY_MAP.get(n, n) for n in names}


@api.post("/admin/site")
@authmod.require_admin
@sec.require_csrf
def admin_site_update():
    d = request.get_json(silent=True) or {}
    clear = _site_clear_list(d)
    values = {}
    for k in SITE_KEYS:
        if k in d:
            values[k] = d[k]
    for legacy, column in SITE_LEGACY_MAP.items():
        if legacy in d:
            values.setdefault(column, d[legacy])
    # A clear can arrive without the field itself ({_clear: ["convBanner"]}):
    # it still has to reach the write as an empty value.
    for name in clear:
        column = SITE_LEGACY_MAP.get(name, name)
        if column in SITE_KEYS or column in SITE_LEGACY_MAP.values():
            values.setdefault(column, "")
    for k in list(values):
        if k == "referral_commission_percentage":
            try: values[k] = max(0, min(100, float(values[k])))
            except (TypeError, ValueError): return jsonify(ok=False, error="Invalid referral percentage."), 400
        elif k in _SITE_URL_KEYS:
            values[k] = sec.safe_url(str(values[k] or ""))
        elif k in _SITE_TEXT_KEYS:
            values[k] = re.sub(r"<[^>]+>", "", str(values[k] or ""))
        elif k in ("banner_from", "banner_to"):
            v = str(values[k] or "")
            if v and not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                # an invalid date keeps the previously saved value; it must
                # not leak into the write below through the legacy mapping
                values.pop(k, None)
                continue
            values[k] = v
        else:
            values[k] = sec.clean(values[k], 500)
    # Supabase column names only.
    values = {k: v for k, v in values.items()
              if k in ("site_logo_url", "hero_video_url", "hero_poster_url",
                       "hero_doc_url", "shop_banner_url", "shipping_note",
                       "banner_from", "banner_to", "conv_banner",
                       "conv_banner_fr", "conv_bold")
              or k in SITE_KEYS}
    # An empty value only counts as a change when the client asked for that
    # column to be cleared (see _site_clear_list). Without this, one Save from
    # a form that never received the live row blanked every field it could not
    # show - the bank details included.
    values = {k: v for k, v in values.items()
              if str(v if v is not None else "").strip() or k in clear}
    if Config.ENV == "testing":
        path = os.environ.get("SITE_CONFIG_PATH", "")
        current = _load_site()
        legacy = ("heroVideo", "heroPoster", "heroDoc", "logoUrl", "shopBannerUrl",
                  "bannerFrom", "bannerTo", "convBanner", "convBannerFr",
                  "convBold", "shippingNote")
        colmap = {v: k for k, v in SITE_LEGACY_MAP.items()}
        for k in legacy:
            if k in d:
                value = str(d.get(k) or "")
                if k in ("heroVideo", "heroPoster", "heroDoc", "logoUrl", "shopBannerUrl"):
                    value = sec.safe_url(value)
                if k in ("bannerFrom", "bannerTo") and not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                    continue
                if k in ("convBanner", "convBannerFr", "convBold", "shippingNote"):
                    value = re.sub(r"<[^>]+>", "", value)
                # Same rule as the canonical columns above: empty means "leave
                # it" unless this column was explicitly cleared.
                if not value.strip() and k not in clear and SITE_LEGACY_MAP.get(k, k) not in clear:
                    continue
                current[k] = value
        for k, v in values.items():
            current[colmap.get(k, k)] = v
            # Keep the canonical column name as well. Production returns the
            # real site_settings row (canonical columns) and _site_payload adds
            # the legacy aliases; if this testing branch only kept the alias,
            # a canonical-field-name bug would pass here and fail in production.
            current[k] = v
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(current, fh)
        return jsonify(ok=True, site=_site_payload(current))
    try:
        site = __import__("supabase_settings", fromlist=["update_site_settings"]).update_site_settings(values)
    except Exception as exc:
        print(f"[supabase] site settings update failed: {exc}")
        # The detail names the column and the one ALTER statement that repairs
        # the live table - without it the owner sees "could not save" with no
        # way forward, and the Render log is the only copy.
        detail = " ".join(str(exc).split())
        if len(detail) > 300:
            detail = detail[:300] + "…"
        return jsonify(
            ok=False,
            error=("Could not update Supabase site settings. No changes were "
                   "made." + (f" {detail}" if detail else ""))), 503
    audit(authmod.current_admin(), "site.update", json.dumps(values)[:200], _ip())
    return jsonify(ok=True, site=_site_payload(site))

# ==================================================== admin: delivery zones
# Delivery zones and their fare ranges are admin-editable, and the storefront
# reads them from GET /api/site. Every write returns the zone list re-read from
# the store, so the portal repaints from what was actually saved rather than
# from what the admin typed.

@api.get("/admin/delivery-zones")
@authmod.require_admin
def admin_delivery_zones():
    return jsonify(ok=True, zones=delivery.zones(include_inactive=True))


@api.post("/admin/delivery-zones")
@authmod.require_admin
@sec.require_csrf
def admin_delivery_zone_save():
    d = request.get_json(silent=True) or {}
    zone_id = sec.clean(d.get("id"), 64).strip().lower()
    if not zone_id:
        # Derive a stable slug from the name so the Admin form can create a
        # zone without making the operator invent an id.
        zone_id = re.sub(r"[^a-z0-9]+", "-",
                         str(d.get("name") or "").lower()).strip("-")[:64]
    if not zone_id:
        return jsonify(ok=False, error="A zone needs a name."), 400
    saved, error = delivery.save_zone(zone_id, d)
    if error:
        return jsonify(ok=False, error=error), 400
    audit(authmod.current_admin(), "delivery_zone.save", zone_id, _ip())
    return jsonify(ok=True, zone=saved, zones=delivery.zones(include_inactive=True))


@api.delete("/admin/delivery-zones/<zone_id>")
@authmod.require_admin
@sec.require_csrf
def admin_delivery_zone_delete(zone_id):
    ok, error = delivery.delete_zone(zone_id)
    if not ok:
        return jsonify(ok=False, error=error), 404
    audit(authmod.current_admin(), "delivery_zone.delete", zone_id, _ip())
    return jsonify(ok=True, zones=delivery.zones(include_inactive=True))

# ============================================ admin: reviews & coupon usage
@api.get("/admin/coupon-uses")
@authmod.require_admin
def admin_coupon_uses():
    """The coupon redemption log. Answers "which order used this code", which
    the coupons.uses counter never could."""
    code = sec.clean(request.args.get("code"), 32) or None
    if Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY:
        try:
            from supabase_store import load_coupon_uses
            rows = load_coupon_uses(code)
            if rows is not None:
                return jsonify(ok=True, uses=rows, source="supabase:coupon_uses")
        except Exception:
            pass
    sql = "SELECT code, email, order_id, percent, used_at FROM coupon_uses"
    args = ()
    if code:
        sql += " WHERE code=?"
        args = (code,)
    rows = query(sql + " ORDER BY used_at DESC LIMIT 500", args)
    return jsonify(ok=True, uses=[dict(r) for r in rows], source="local")


@api.get("/admin/reviews")
@authmod.require_admin
def admin_reviews():
    """Every review, including hidden ones - moderation needs to see them."""
    pid = sec.clean(request.args.get("productId"), 64) or None
    sql = ("SELECT product_id, order_id, email, name, rating, title, body, "
           "hidden, created_at, updated_at FROM product_reviews")
    args = ()
    if pid:
        sql += " WHERE product_id=?"
        args = (pid,)
    rows = query(sql + " ORDER BY created_at DESC LIMIT 500", args)
    return jsonify(ok=True, reviews=[dict(r) for r in rows])


@api.patch("/admin/reviews")
@authmod.require_admin
@sec.require_csrf
def admin_reviews_moderate():
    """Hide or unhide one review. Targets (product_id, email) - the unique key
    - so it can never touch a different customer's review."""
    d = request.get_json(silent=True) or {}
    pid = sec.clean(d.get("productId"), 64)
    email = sec.clean_email(d.get("email"))
    if not pid or not email:
        return jsonify(ok=False, error="A product id and the reviewer's email are required."), 400
    hidden = 1 if d.get("hidden", True) else 0
    row = one("SELECT id FROM product_reviews WHERE product_id=? AND email=?", (pid, email))
    if not row:
        return jsonify(ok=False, error="No review from that email on that product."), 404
    execute("UPDATE product_reviews SET hidden=? WHERE product_id=? AND email=?",
            (hidden, pid, email))
    if Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY:
        try:
            from supabase_store import set_product_review_hidden
            set_product_review_hidden(pid, email, bool(hidden))
        except Exception:
            pass
    audit(authmod.current_admin(), "review.moderated",
          f"{pid} {'hidden' if hidden else 'shown'} ({email})", _ip())
    return jsonify(ok=True, productId=pid, email=email, hidden=bool(hidden))


@api.delete("/admin/reviews")
@authmod.require_admin
@sec.require_csrf
def admin_reviews_delete():
    """Delete one review. The unique key is the only safe address for it."""
    pid = sec.clean(request.args.get("productId"), 64)
    email = sec.clean_email(request.args.get("email"))
    if not pid or not email:
        return jsonify(ok=False, error="A product id and the reviewer's email are required."), 400
    row = one("SELECT id FROM product_reviews WHERE product_id=? AND email=?", (pid, email))
    if not row:
        return jsonify(ok=False, error="No review from that email on that product."), 404
    execute("DELETE FROM product_reviews WHERE product_id=? AND email=?", (pid, email))
    if Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY:
        try:
            from supabase_store import delete_product_review
            delete_product_review(pid, email)
        except Exception:
            pass
    audit(authmod.current_admin(), "review.deleted", f"{pid} ({email})", _ip())
    return jsonify(ok=True, productId=pid, email=email, deleted=True)


@api.post("/admin/reviews/migrate")
@authmod.require_admin
@sec.require_csrf
def admin_reviews_migrate():
    """Copy legacy reviews from the growth_settings blob into the real table.

    Dry-run by default. It never deletes the blob - that only happens once the
    copy has been verified, and it is a separate, deliberate act.
    """
    d = request.get_json(silent=True) or {}
    dry = bool(d.get("dry_run", True))
    if not (Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY):
        return jsonify(ok=False,
                       error="Supabase is not configured; there is nothing to migrate."), 503
    try:
        from supabase_store import migrate_product_reviews_from_blob
        report = migrate_product_reviews_from_blob(dry_run=dry)
    except Exception as exc:
        return jsonify(ok=False, error=f"Migration failed: {exc}"), 503
    audit(authmod.current_admin(), "reviews.migrate",
          f"dry_run={dry} verified={report.get('verified')}", _ip())
    return jsonify(ok=report.get("error") is None, report=report)


# ==================================================== public: promo & referral
@api.post("/promo/check")
def promo_check():
    """Validate a referral or coupon code typed at checkout."""
    limited = sec.guard("promo-check", limit=60, window=600)
    if limited: return limited
    import growth
    d = request.get_json(silent=True) or {}
    res = growth.check_code(d.get("code"))
    status = 200 if res.get("ok") else 404
    return jsonify(res), status

# ================================================= public: verified reviews
def _public_review(row):
    """Shape one review row for the storefront.

    `rating` / `title` / `body` / `created_at` are the contract. `stars` and
    `note` are repeated as aliases so a browser still running the previous
    js/app.js keeps rendering instead of showing blank reviews - the old bundle
    stays in CDN and customer caches long after a deploy.
    """
    row = row or {}
    rating = int(row.get("rating") or 5)
    return {
        "name": row.get("name"),
        "rating": rating,
        "title": row.get("title"),
        "body": row.get("body"),
        "created_at": row.get("created_at"),
        "stars": rating,                      # deprecated alias
        "note": row.get("body"),              # deprecated alias
        "at": row.get("created_at"),          # deprecated alias
    }


@api.get("/reviews/<pid>")
def reviews_list(pid):
    """Reviews for one product.

    Supabase product_reviews is the source of truth when it is configured;
    SQLite is the local mirror. Note that None from the loader means
    "unavailable", NOT "no reviews" - conflating those would show an empty
    list during an outage and invite duplicate reviews.
    """
    pid = sec.clean(pid, 64)
    items = None
    source = "local"
    if Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY:
        try:
            from supabase_store import load_product_reviews_table
            rows = load_product_reviews_table(pid)
            if rows is not None:
                items = [_public_review(r)
                         for r in rows if not r.get("hidden")][:100]
                source = "supabase:product_reviews"
        except Exception:
            items = None
    if items is None:
        rows = query("SELECT name, rating, title, body, created_at "
                     "FROM product_reviews "
                     "WHERE product_id=? AND hidden=0 "
                     "ORDER BY created_at DESC LIMIT 100", (pid,))
        items = [_public_review(dict(r)) for r in rows]
    n = len(items)
    avg = round(sum(int(r.get("rating") or 0) for r in items) / n, 2) if n else 0
    return jsonify(ok=True, productId=pid, count=n, average=avg, reviews=items,
                   source=source)

@api.post("/reviews")
@sec.require_csrf
def reviews_create():
    """A customer review: rating + title + body, only for products they bought.
    The buyer is verified against the orders table by email.

    Accepts the legacy `stars` / `note` keys as well, so a cached copy of the
    old storefront bundle can still post.
    """
    limited = sec.guard("review", limit=10, window=3600)
    if limited: return limited
    d = request.get_json(silent=True) or {}
    pid = sec.clean(d.get("productId"), 64)
    email = sec.clean_email(d.get("email"))
    name = sec.clean(d.get("name"), 60)
    title = sec.clean(d.get("title"), 120)
    # dict.get(key, fallback) only falls back when the key is ABSENT, so an
    # explicit {"rating": null} would have won over a valid "stars". Treat null
    # as absent: an old bundle sends stars/note, a new one sends rating/body.
    def _first(*keys):
        for k in keys:
            if d.get(k) is not None:
                return d.get(k)
        return None

    rating = sec.clean_int(_first("rating", "stars"), 5, 1, 5)
    body = sec.clean(_first("body", "note"), 600)
    if not pid or not body:
        return jsonify(ok=False, error="Add a short note about the product."), 400
    if not email:
        return jsonify(ok=False, error="Enter the email you used for your order."), 400

    bought = one("SELECT id FROM orders WHERE email=? AND status != 'declined' "
                 "AND payload LIKE ? LIMIT 1", (email, f'%"id": "{pid}"%'))
    if not bought:
        return jsonify(ok=False, error="Reviews are for customers who bought this product. "
                                       "Use the same email as your order."), 403

    now = _utcnow()
    execute("INSERT INTO product_reviews (product_id, order_id, email, name, rating, "
            "title, body, hidden, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(product_id, email) DO UPDATE SET "
            "name=excluded.name, rating=excluded.rating, title=excluded.title, "
            "body=excluded.body, updated_at=excluded.updated_at",
            (pid, bought["id"], email, name or "Customer", rating, title, body,
             0, now, now))
    # Persist to the real product_reviews table, which is the source of truth.
    # The old growth_settings JSON blob is no longer written: re-serialising the
    # whole table on every review was last-write-wins, so two reviews arriving
    # together lost one. One row is upserted on (product_id, email) instead.
    durable = True
    if Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY:
        try:
            from supabase_store import save_product_reviews_table
            _saved, err = save_product_reviews_table([{
                "product_id": pid, "order_id": bought["id"], "email": email,
                "name": name or "Customer", "rating": rating, "title": title,
                "body": body, "created_at": now, "updated_at": now}])
            durable = not err
        except Exception:
            durable = False
    audit("customer", "review.posted", f"{pid} {rating}★ by {email}", _ip())
    # The review is in SQLite either way, but if the durable write failed the
    # caller is told - a silent "thank you" over a lost review is worse than a
    # warning.
    if not durable:
        return jsonify(ok=True, productId=pid, count=1, average=float(rating),
                       reviews=[_public_review({
                           "name": name or "Customer", "rating": rating,
                           "title": title, "body": body, "created_at": now})],
                       source="local", durable=False,
                       warning="Your review was saved but could not be stored "
                               "durably; it may need re-submitting."), 200
    return reviews_list(pid)

# =============================================== admin: growth & marketing
@api.get("/admin/growth/settings")
@authmod.require_admin
def admin_growth_settings():
    import growth
    s = growth.settings()
    if Config.ENV != "testing":
        try:
            from supabase_settings import get_site_settings
            s["referralCommissionPercentage"] = float(
                get_site_settings().get("referral_commission_percentage") or 0)
        except Exception:
            s["referralCommissionPercentage"] = float(s.get("referrerPercent") or 0)
    return jsonify(ok=True, settings=s)

@api.post("/admin/growth/settings")
@authmod.require_admin
@sec.require_csrf
def admin_growth_settings_save():
    import growth
    d = request.get_json(silent=True) or {}
    saved = growth.save_settings(d, authmod.current_admin())
    # The payout control lives in the persistent site_settings row
    # (referral_commission_percentage, 0-100, validated server-side).
    if "referral_commission_percentage" in d or \
            (Config.ENV != "testing" and "referrerPercent" in d):
        try:
            raw = d.get("referral_commission_percentage",
                        d.get("referrerPercent"))
            pct = max(0.0, min(100.0, float(raw)))
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Referral commission must be a number between 0 and 100."), 400
        if Config.ENV != "testing":
            try:
                from supabase_settings import update_site_settings
                site = update_site_settings({"referral_commission_percentage": pct})
            except Exception as exc:
                print(f"[supabase] referral commission save failed: {exc}")
                return jsonify(ok=False, error=(
                    "Could not save the referral commission to Supabase. "
                    "No changes were made.")), 503
            saved["referralCommissionPercentage"] = float(
                site.get("referral_commission_percentage") or pct)
    return jsonify(ok=True, settings=saved)

@api.get("/admin/referrals")
@authmod.require_admin
def admin_referrals():
    rows = query("SELECT code, email, name, uses, reward_issued, reward_coupon, created_at "
                 "FROM referral_codes ORDER BY created_at DESC LIMIT 500")
    return jsonify(ok=True, referrals=[dict(r) for r in rows])

@api.get("/admin/coupons")
@authmod.require_admin
def admin_coupons():
    rows = query("SELECT code, percent, kind, email, note, active, max_uses, uses, "
                 "expires_at, created_at FROM coupons ORDER BY created_at DESC LIMIT 500")
    return jsonify(ok=True, coupons=[dict(r) for r in rows])

@api.post("/admin/coupons")
@authmod.require_admin
@sec.require_csrf
def admin_coupon_create():
    import growth
    d = request.get_json(silent=True) or {}
    code = growth.normalize_code(d.get("code")) or growth._mint_code("PROMO")
    # Validate before clamping. sec.clean_int silently clamps to its bounds, so
    # an admin who typed 99% used to get a 90% coupon and a success toast - a
    # discount that was never what they asked for, with nothing to say so.
    try:
        percent = int(str(d.get("percent")).strip())
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Percent must be a whole number."), 400
    if percent < 1 or percent > 90:
        return jsonify(ok=False, error="Percent must be between 1 and 90."), 400
    if one("SELECT 1 FROM coupons WHERE code=?", (code,)) or \
       one("SELECT 1 FROM referral_codes WHERE code=?", (code,)):
        return jsonify(ok=False, error="That code already exists."), 400
    max_uses = sec.clean_int(d.get("maxUses"), None, 1, 10**6)
    expires = sec.clean(d.get("expiresAt"), 32) or None
    note = sec.clean(d.get("note"), 200)
    execute("INSERT INTO coupons (code, percent, kind, note, active, max_uses, expires_at) "
            "VALUES (?,?,?,?,1,?,?)", (code, percent, "manual", note, max_uses, expires))
    audit(authmod.current_admin(), "coupon.created", f"{code} {percent}%", _ip())
    growth._mirror_coupon(code)
    return jsonify(ok=True, code=code)

@api.patch("/admin/coupons/<code>")
@authmod.require_admin
@sec.require_csrf
def admin_coupon_update(code):
    import growth
    code = growth.normalize_code(code)
    row = one("SELECT code FROM coupons WHERE code=?", (code,))
    if not row:
        return jsonify(ok=False, error="Coupon not found."), 404
    d = request.get_json(silent=True) or {}
    if "percent" in d:
        pct = sec.clean_int(d.get("percent"), None, 1, 90)
        if pct: execute("UPDATE coupons SET percent=? WHERE code=?", (pct, code))
    if "active" in d:
        execute("UPDATE coupons SET active=? WHERE code=?", (1 if d.get("active") else 0, code))
    if "expiresAt" in d:
        execute("UPDATE coupons SET expires_at=? WHERE code=?",
                (sec.clean(d.get("expiresAt"), 32) or None, code))
    if "maxUses" in d:
        execute("UPDATE coupons SET max_uses=? WHERE code=?",
                (sec.clean_int(d.get("maxUses"), None, 1, 10**6), code))
    if "note" in d:
        execute("UPDATE coupons SET note=? WHERE code=?", (sec.clean(d.get("note"), 200), code))
    audit(authmod.current_admin(), "coupon.updated", code, _ip())
    growth._mirror_coupon(code)
    return jsonify(ok=True, code=code)

@api.delete("/admin/coupons/<code>")
@authmod.require_admin
@sec.require_csrf
def admin_coupon_delete(code):
    import growth
    code = growth.normalize_code(code)
    execute("DELETE FROM coupons WHERE code=?", (code,))
    audit(authmod.current_admin(), "coupon.deleted", code, _ip())
    try:
        from supabase_store import client as _sb_client
        c = _sb_client()
        if c is not None:
            c.table("coupons").delete().eq("code", code).execute()
    except Exception:                              # pragma: no cover
        pass
    return jsonify(ok=True)

@api.post("/admin/backup")
@authmod.require_admin
@sec.require_csrf
def admin_backup_now():
    """Manual 'Back up now': the same job the scheduler runs at midnight."""
    limited = sec.guard("backup", limit=6, window=3600)
    if limited: return limited
    import backup
    ok, report = backup.run(actor=authmod.current_admin())
    if ok:
        backup.mark_backup_done()
    return jsonify(ok=bool(ok), **report)
