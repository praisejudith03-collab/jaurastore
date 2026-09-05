"""All JSON endpoints. Every mutating route is CSRF-protected."""
import csv, io, json, os, datetime, secrets, hashlib, re
from flask import Blueprint, request, jsonify, session, current_app, make_response
from config import Config
from db import execute, one, query, audit
import security as sec
import auth as authmod
import emailer
import storage
import catalog as catalog_mod
import analytics as analytics_mod

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
    {"id": "clothing", "name": "Clothings for men and women", "nameFr": "", "image": "images/categories/fashion.jpg", "hidden": False},
    {"id": "household", "name": "Household items", "nameFr": "", "image": "images/categories/household.jpg", "hidden": False},
    {"id": "ankara", "name": "Ankara ready to wear", "nameFr": "", "image": "images/categories/fashion.jpg", "hidden": False},
    {"id": "accessories", "name": "Accessories", "nameFr": "", "image": "images/categories/gadgets.jpg", "hidden": False},
    {"id": "beauty", "name": "Beauty & skincare", "nameFr": "Beauté & soins", "image": "images/categories/beauty.jpg", "hidden": False},
    {"id": "shoes", "name": "Shoes", "nameFr": "", "image": "images/categories/shoes.jpg", "hidden": False},
    {"id": "gadgets", "name": "Gadgets / Electronics", "nameFr": "", "image": "images/categories/gadgets.jpg", "hidden": False},
    {"id": "packaging", "name": "Packaging", "nameFr": "", "image": "images/categories/household.jpg", "hidden": False},
    {"id": "bags", "name": "Bags", "nameFr": "", "image": "images/categories/bags.jpg", "hidden": False},
    {"id": "hair-care", "name": "Hair care", "nameFr": "", "image": "images/categories/beauty.jpg", "hidden": False},
    {"id": "nails", "name": "Nails", "nameFr": "", "image": "images/categories/beauty.jpg", "hidden": False},
    {"id": "gift-set", "name": "Gift set", "nameFr": "", "image": "images/categories/household.jpg", "hidden": False},
    {"id": "children", "name": "Children items", "nameFr": "", "image": "images/categories/fashion.jpg", "hidden": False},
    {"id": "decor", "name": "Decor", "nameFr": "", "image": "images/categories/household.jpg", "hidden": False},
]


def _categories_data():
    """Read categories from disk, falling back to the defaults."""
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
    body = jsonify(ok=True, products=catalog_mod.base_products())
    body.headers["Cache-Control"] = "public, max-age=300"
    return body

# ============================================================ public: catalog
@api.get("/catalog")
def catalog():
    """Seed products + every admin edit, merged. This is the live catalogue."""
    admin = bool(authmod.current_admin())
    include_hidden = admin and request.args.get("all") == "1"
    body = json.dumps({
        "ok": True,
        "products": catalog_mod.merged(include_hidden=include_hidden),
        "meta": catalog_mod.meta(),
    }, ensure_ascii=False, separators=(",", ":"))
    etag = 'W/"' + hashlib.sha256(
        (str(catalog_mod.meta()) + str(len(catalog_mod.base_products()))).encode()
    ).hexdigest()[:28] + '"'
    if request.headers.get("If-None-Match") == etag:
        resp = make_response("", 304)
    else:
        resp = make_response(body, 200)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "public, max-age=30"
    return resp

# ========================================================== public: categories
@api.get("/categories")
def categories_public():
    """The category list used by the shop, filters and admin manager."""
    return jsonify(ok=True, categories=_categories_data().get("categories") or [])


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
    for c in cats[:200]:
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
            "image": sec.safe_url(c.get("image") or ""),
            "hidden": bool(c.get("hidden")),
        })
    payload = _save_categories(clean, authmod.current_admin())
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

@api.get("/stock")
def stock():
    rows = query("SELECT product_id, variant_key, variant_label, qty, low_threshold FROM variant_stock")
    out = {}
    for r in rows:
        out.setdefault(r["product_id"], []).append({
            "variant": r["variant_key"], "label": r["variant_label"],
            "qty": r["qty"], "lowThreshold": r["low_threshold"],
            "state": "out" if r["qty"] <= 0 else ("low" if r["qty"] <= r["low_threshold"] else "in"),
        })
    return jsonify(ok=True, stock=out, lowStockThreshold=Config.LOW_STOCK_THRESHOLD)

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
    """[{id, option, qty}, ...] for every cart line on an order."""
    items = (payload or {}).get("items") or []
    try:
        products = {str(p.get("id")): p for p in catalog_mod.merged(include_hidden=True)}
    except Exception:
        products = {}
    moves = []
    for it in items:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("id") or "")
        if not pid:
            continue
        try:
            qty = int(it.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        product = products.get(pid)
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
    second confirm (email already=True, admin re-save) never double-decrements,
    and a decline / reopen / delete restores the same quantities.
    """
    if not isinstance(payload, dict):
        return payload
    old_c = (old_status or "pending") == "confirmed"
    new_c = (new_status or "pending") == "confirmed"
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


@api.post("/orders")
@sec.require_csrf
def create_order():
    """Store a completed checkout - the whole form plus the payment proof.
    Accepts JSON or multipart/form-data (field `order` = JSON, field `proof`
    = image). Re-posting the same order id returns the stored order instead of
    creating a duplicate, so a queued offline retry is always safe."""
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

    # delivery: location only, pickup only allowed as "Pickup in Cotonou is free for lighter products"
    zone = sec.clean(customer_raw.get("zone") or d.get("zone"), 80)
    zone_lower = zone.lower()
    # Allow the specific free pickup option, block other pickup attempts
    is_allowed_pickup = bool(re.search(r"(?i)pickup in cotonou.*free.*lighter|free.*lighter.*cotonou", zone)) or zone_lower == "pickup in cotonou" or "pickup in cotonou is free for lighter products" in zone_lower
    if not is_allowed_pickup and re.search(r"(?i)\bpick[\s-]?up\b|collect\s+in\s+store|self[\s-]?collect", zone):
        return jsonify(ok=False, error="Choose a delivery location."), 400

    # Benin & Togo deliveries carry a minimum order (or its naira equivalent).
    # Enforce for Benin, Togo, Cotonou, Calavi, Porto-Novo, Lomé
    if re.search(r"(?i)\bbenin\b|\btogo\b|cotonou|calavi|porto|lom[ée]|lome", zone):
        min_cfa = 5000
        min_ngn = 11400
        if currency == "CFA" and total < min_cfa:
            return jsonify(ok=False, error=(
                "Benin & Togo deliveries: minimum order 5,000 F CFA (about 11,400 naira). "
                "Please add a few more items to meet the minimum.")), 400
        if currency == "NGN" and total < min_ngn:
            return jsonify(ok=False, error=(
                "Benin & Togo deliveries: minimum order 11,400 naira (about 5,000 F CFA). "
                "Please add a few more items to meet the minimum.")), 400
    # Also check country field for Benin/Togo even if zone is generic
    country_raw = sec.clean(customer_raw.get("country") or d.get("country") or "", 80)
    if re.search(r"(?i)\bbenin\b|\btogo\b", country_raw):
        # if zone didn't already trigger, still enforce
        if not re.search(r"(?i)\bbenin\b|\btogo\b|cotonou|calavi|porto|lom[ée]|lome", zone):
            min_cfa = 5000
            min_ngn = 11400
            if currency == "CFA" and total < min_cfa:
                return jsonify(ok=False, error=(
                    "Benin & Togo deliveries: minimum order 5,000 F CFA (about 11,400 naira). "
                    "Please add a few more items to meet the minimum.")), 400
            if currency == "NGN" and total < min_ngn:
                return jsonify(ok=False, error=(
                    "Benin & Togo deliveries: minimum order 11,400 naira (about 5,000 F CFA). "
                    "Please add a few more items to meet the minimum.")), 400

    oid = sec.clean(d.get("id"), 24).upper()
    if not ORDER_ID.match(oid or ""):
        oid = "JA-" + secrets.token_hex(3).upper()

    existing = one("SELECT id, status, at FROM orders WHERE id=?", (oid,))
    if existing:
        return jsonify(ok=True, id=oid, duplicate=True, status=existing["status"])

    if Config.ENFORCE_STOCK:
        problems = _stock_problems(clean_items)
        if problems:
            return jsonify(ok=False, error=_stock_message(problems),
                           code="out_of_stock", items=problems), 409

    # ---- referral / promo code (validated on the server, never trusted) ----
    import growth
    promo = None
    promo_in = d.get("promo") if isinstance(d.get("promo"), dict) else {}
    promo_raw = sec.clean(d.get("promoCode") or promo_in.get("code"), 32)
    if promo_raw:
        chk = growth.check_code(promo_raw)
        if chk.get("ok"):
            promo = {"code": chk["code"], "percent": chk["percent"], "kind": chk["kind"]}

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
        if candidate.startswith("/uploads/") and storage.resolve_local(candidate[len("/uploads/"):]):
            proof_url = candidate

    now = _utcnow()
    order = {
        "id": oid,
        "at": sec.clean(d.get("at"), 40) or now,
        "status": "pending",
        "customer": customer,
        "items": clean_items,
        "total": total,
        "currency": currency,
        "payment": sec.clean(d.get("payment"), 60) or currency,
        "proofUrl": proof_url,
        "source": sec.clean(d.get("source"), 20) or "web",
    }
    if promo:
        order["promo"] = promo
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

    # mirror into Supabase when enabled (never blocks the sale on failure)
    from supabase_store import create_order as _sb_create_order
    if Config.SUPABASE_ENABLED:
        _sb_create_order({
            "id": oid, "email": email,
            "customer_name": sec.clean(customer.get("name") or "", 200),
            "phone": customer.get("phone", ""), "country": customer.get("country", ""),
            "city": customer.get("city", ""), "zone": zone,
            "address": customer.get("address", ""), "note": customer.get("note", ""),
            "payment": order["payment"], "proof_url": proof_url,
            "items_count": len(clean_items), "total": total, "currency": currency,
            "source": order["source"], "status": "pending",
            "payload": order, "at": order["at"], "updated_at": now,
        })

    # conversion tracking: a finished checkout is the purchase event
    vid, _is_new = analytics_mod.visitor_id()
    analytics_mod.record([{
        "type": "purchase", "path": "/checkout.html", "page": "checkout",
        "value": total, "currency": currency, "sid": sec.clean(d.get("sid"), 48),
    }], vid, False)

    if Config.MAIL_MODE != "none":
        try:
            if proof_file and proof_url:
                emailer.send_order_notice(
                    order, data, f"payment-{oid}-checkout.{ext}", storage.mime_for(ext))
            else:
                emailer.send_order_notice(order)
        except Exception:
            pass

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

    # growth hooks: count the promo use, mint a referral code when the order
    # qualifies, and close any abandoned-cart record for this checkout
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
        growth.complete_abandoned(sec.clean(d.get("cartToken"), 64), email)
    except Exception:
        pass

    resp = make_response(jsonify(ok=True, id=oid, status="pending", proofUrl=proof_url,
                                 referralCode=referral_code,
                                 promo=promo or None))
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
    """A customer sends their receipt. The original file is stored and emailed
    to the shop as a real attachment, together with their details."""
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

    try:
        delivered, info = emailer.send_payment_proof(details, data, attach_name, mime)
    except Exception as exc:                      # never lose the receipt
        delivered, info = False, f"mail error: {exc}"

    execute(
        "INSERT INTO payment_proofs (order_id, name, phone, email, method, items, quantity, "
        "amount, note, file_url, file_name, file_size, mime, emailed, email_info) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (order_id, name, phone, email, method, details["items"], details["quantity"],
         details["amount"], details["note"], url, attach_name, len(data), mime,
         1 if delivered else 0, str(info)[:300]),
    )
    # mirror into Supabase when enabled (receipts live in the `receipts` table)
    from supabase_store import create_receipt as _sb_create_receipt
    if Config.SUPABASE_ENABLED:
        _sb_create_receipt({
            "id": order_id, "order_id": order_id, "name": name, "phone": phone,
            "email": email, "method": method, "items": details["items"],
            "quantity": details["quantity"], "amount": details["amount"],
            "note": details["note"], "file_url": url, "file_name": attach_name,
            "file_size": len(data), "file_type": mime,
            "emailed": bool(delivered), "email_info": str(info)[:300],
        })
    audit("customer", "payment_proof", f"{order_id} {attach_name} emailed={delivered}", _ip())

    return jsonify(ok=True, emailed=delivered, info=info, url=url,
                   fileName=attach_name, size=len(data),
                   message=("Receipt sent to " + Config.ADMIN_EMAILS[0] + " with your file attached."
                            if delivered else
                           "Your receipt is saved with us. We will confirm your payment shortly."))


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
    # identical response for unknown email vs wrong password (no enumeration)
    ok = authmod.is_known_admin(email) and authmod.verify_login(email, pw)
    if not ok:
        audit(email or "?", "admin.login_failed", "bad credentials", _ip())
        return jsonify(ok=False, error="Invalid email or password."), 401
    authmod.login(email)
    sec.clear_rate("admin-login", email)
    audit(email, "admin.login", "success", _ip())
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

@api.post("/admin/password")
@authmod.require_admin
@sec.require_csrf
def change_password():
    limited = sec.guard("admin-password", limit=8, window=600)
    if limited: return limited
    actor = authmod.current_admin()
    d = request.get_json(silent=True) or {}
    if not authmod.verify_login(actor, d.get("currentPassword") or ""):
        audit(actor, "admin.password_change_failed", "wrong current password", _ip())
        return jsonify(ok=False, error="Your current password is incorrect."), 403
    newpw = d.get("newPassword") or ""
    ok, msg = authmod.password_strong(newpw)
    if not ok:
        return jsonify(ok=False, error=msg), 400
    authmod.set_password(actor, newpw)
    audit(actor, "admin.password_changed", "", _ip())
    return jsonify(ok=True, message="Password updated.")

@api.post("/admin/otp/request")
def otp_request():
    d = request.get_json(silent=True) or {}
    email = sec.clean_email(d.get("email"))
    if not email:
        return jsonify(ok=False, error="Enter a valid email address."), 400
    limited = sec.guard("otp-request", limit=3, window=900, key_extra=email)
    if limited: return limited
    if not authmod.is_known_admin(email):
        # do not reveal whether an address is an admin
        return jsonify(ok=True, message="If that address is registered, a code has been sent.")
    if authmod.otp_requested_recently(email):
        return jsonify(ok=False, error="A code was just sent. Wait a minute before requesting another."), 429
    code = authmod.create_otp(email)
    delivered, info = emailer.send_otp(email, code)
    audit(email, "admin.otp_requested", f"via=email delivered={delivered} {info}", _ip())
    if not delivered:
        return jsonify(ok=False, error="The code could not be emailed. Check the mail settings on the server, or message the shop directly to recover access."), 502
    return jsonify(ok=True, message=f"Verification code sent to {email}.")

@api.post("/admin/otp/verify")
def otp_verify():
    d = request.get_json(silent=True) or {}
    email = sec.clean_email(d.get("email")); code = sec.clean(d.get("code"), 12)
    if not email or not code:
        return jsonify(ok=False, error="Email and code are required."), 400
    limited = sec.guard("otp-verify", limit=8, window=600, key_extra=email)
    if limited: return limited
    ok, msg = authmod.verify_otp(email, code)
    if not ok:
        return jsonify(ok=False, error=msg), 400
    ticket = secrets.token_urlsafe(32)
    session["reset_ticket"] = email
    session["reset_ok"] = True
    audit(email, "admin.otp_verified", "", _ip())
    return jsonify(ok=True, message="Code verified. Set your new password.", ticket=ticket)

@api.post("/admin/otp/reset")
def otp_reset():
    d = request.get_json(silent=True) or {}
    email = session.get("reset_ticket") or sec.clean_email(d.get("email"))
    if not session.get("reset_ok") or not email:
        return jsonify(ok=False, error="Verify a code first."), 403
    limited = sec.guard("otp-reset", limit=6, window=600, key_extra=email)
    if limited: return limited
    newpw = d.get("newPassword") or ""
    ok, msg = authmod.password_strong(newpw)
    if not ok:
        return jsonify(ok=False, error=msg), 400
    if not authmod.is_known_admin(email):
        return jsonify(ok=False, error="Unknown account."), 404
    authmod.set_password(email, newpw)
    session.pop("reset_ticket", None); session.pop("reset_ok", None)
    audit(email, "admin.password_reset_via_otp", "", _ip())
    return jsonify(ok=True, message="Password reset. You can sign in now.", csrf=sec.issue_csrf())

# ------------------------------------------------------------ admin: stock
@api.get("/admin/stock")
@authmod.require_admin
def admin_stock():
    rows = query("SELECT product_id, variant_key, variant_label, qty, low_threshold, updated_at "
                 "FROM variant_stock ORDER BY product_id, variant_key")
    return jsonify(ok=True, items=[dict(r) for r in rows])

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
    execute("INSERT INTO variant_stock (product_id, variant_key, variant_label, qty, low_threshold, updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(product_id, variant_key) DO UPDATE SET "
            "qty=excluded.qty, low_threshold=excluded.low_threshold, "
            "variant_label=COALESCE(excluded.variant_label, variant_stock.variant_label), updated_at=excluded.updated_at",
            (pid, variant, label, qty, thr, datetime.datetime.utcnow().isoformat(timespec="seconds")))
    # variant_stock is SQLite-only (wiped with the Render disk), so mirror the
    # whole table into Supabase growth_settings - the boot restore in app.py
    # writes it back. Best effort: a Supabase hiccup never blocks the change.
    if Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY:
        try:
            from supabase_store import save_variant_stock
            rows = query("SELECT product_id, variant_key, variant_label, qty, low_threshold, "
                         "updated_at FROM variant_stock")
            save_variant_stock([dict(r) for r in rows])
        except Exception:
            pass
    audit(authmod.current_admin(), "stock.set", f"{pid}/{variant} = {qty}", _ip())
    return jsonify(ok=True)

@api.get("/admin/low-stock")
@authmod.require_admin
def low_stock():
    rows = query("SELECT product_id, variant_key, variant_label, qty, low_threshold FROM variant_stock "
                 "WHERE qty <= low_threshold ORDER BY qty ASC LIMIT 200")
    items = [dict(r) for r in rows]
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
    """Every receipt a customer has sent from the payment form.

    Proof URLs saved in Supabase mode are signed URLs, and signed URLs
    expire after 7 days - storage.signed_url_for() refreshes each one here
    (best effort, public / local URLs pass through untouched) so a receipt
    stays viewable for as long as the file exists.
    """
    limit = sec.clean_int(request.args.get("limit"), 200, 1, 1000)
    rows = query("SELECT id, order_id, name, phone, email, method, items, quantity, amount, "
                 "note, file_url, file_name, file_size, mime, emailed, email_info, at "
                 "FROM payment_proofs ORDER BY at DESC LIMIT ?", (limit,))
    proofs = []
    for r in rows:
        d = dict(r)
        if d.get("file_url"):
            d["file_url"] = storage.signed_url_for(d["file_url"])
        proofs.append(d)
    return jsonify(ok=True, count=len(proofs), proofs=proofs)


@api.delete("/admin/payment-proofs/<int:pid>")
@authmod.require_admin
@sec.require_csrf
def admin_payment_proof_delete(pid):
    """Delete one payment receipt: the uploaded file AND the row.

    Used by the Delete button on Orders -> Receipts. The file is removed too,
    so a deleted receipt can no longer be downloaded from its old URL.
    """
    row = one("SELECT id, order_id, file_url, file_name FROM payment_proofs WHERE id=?", (pid,))
    if not row:
        return jsonify(ok=False, error="That receipt is no longer there."), 404
    file_url = row["file_url"] or ""
    removed = False
    try:
        removed = storage.delete_upload(file_url)
    except Exception:
        removed = False
    execute("DELETE FROM payment_proofs WHERE id=?", (pid,))
    if Config.SUPABASE_ENABLED:
        try:
            from supabase_store import delete_receipt as _sb_delete_receipt
            _sb_delete_receipt(receipt_id=pid, order_id=row["order_id"], file_url=file_url)
        except Exception:
            pass
    audit(authmod.current_admin(), "payment_proof.delete",
          f"{pid} {row['order_id'] or ''} file_removed={removed}", _ip())
    return jsonify(ok=True, id=pid, fileRemoved=removed)


@api.get("/orders/<oid>/confirm")
def order_confirm_by_email(oid):
    """Confirm or decline from the link in the email - one tap, no sign in.

    The link cannot carry a session cookie, so it is signed with SECRET_KEY
    (see security.order_token): it only works for this order and this one
    action, and the page behind it never acts until a human presses a button.
    """
    oid = sec.clean(oid, 24).upper()
    action = sec.clean(request.args.get("action") or "confirm", 20).lower()
    token = sec.clean(request.args.get("token") or "", 64)
    if action not in ("confirm", "decline"):
        return jsonify(ok=False, error="That action is not recognised."), 400
    if not sec.order_token_ok(oid, action, token):
        audit("email-link", "order.bad_token", oid, _ip())
        return jsonify(ok=False, error="That link is not valid for this order."), 403
    limited = sec.guard("order-confirm", limit=60, window=3600, key_extra=oid)
    if limited:
        return limited

    row = one("SELECT id, status, payload FROM orders WHERE id=?", (oid,))
    if not row:
        return jsonify(ok=False, error="We could not find that order."), 404
    try:
        payload = json.loads(row["payload"] or "{}")
    except ValueError:
        payload = {}

    status = "confirmed" if action == "confirm" else "declined"
    # the column is the truth; the payload is only a copy for the customer view
    old_status = row["status"] or payload.get("status") or "pending"
    if old_status == status:
        payload["status"] = status
        execute("UPDATE orders SET payload=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), oid))
        return jsonify(ok=True, id=oid, status=status, already=True)

    payload["status"] = status
    payload["updatedAt"] = _utcnow()
    payload["updatedBy"] = "email link"
    _sync_order_stock(payload, old_status, status, actor="email link")
    execute("UPDATE orders SET status=?, payload=?, updated_at=? WHERE id=?",
            (status, json.dumps(payload, ensure_ascii=False), _utcnow(), oid))
    audit("email-link", f"order.{status}", oid, _ip())

    emailed = False
    customer = (payload.get("customer") or {})
    if str(customer.get("email") or "").strip():
        try:
            if status == "confirmed":
                emailed = bool(emailer.send_receipt(payload)[0])
            else:
                emailed = bool(emailer.send_order_declined(payload)[0])
        except Exception:
            emailed = False
    return jsonify(ok=True, id=oid, status=status, customerEmailed=emailed)


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

    # tell the customer: confirming or declining from the portal must send the
    # same email the one-tap link in the admin's inbox sends
    emailed = False
    customer = (payload.get("customer") or {})
    if status in ("confirmed", "declined") and str(customer.get("email") or "").strip():
        try:
            if status == "confirmed":
                emailed = bool(emailer.send_receipt(payload)[0])
            else:
                emailed = bool(emailer.send_order_declined(payload)[0])
        except Exception:
            emailed = False

    # mirror the new status so the Supabase copy (used by the boot restore)
    # cannot put a stale pending order back
    if Config.SUPABASE_ENABLED:
        try:
            from supabase_store import update_order as _sb_update_order
            _sb_update_order(oid, status=status, payload=payload)
        except Exception:
            pass

    return jsonify(ok=True, id=oid, status=status, customerEmailed=emailed)


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
    execute("DELETE FROM payment_proofs WHERE order_id=?", (oid,))
    execute("DELETE FROM orders WHERE id=?", (oid,))

    # the mirrored copy must go as well, or the boot-time restore from
    # Supabase quietly brings the order (and its receipts) straight back
    if Config.SUPABASE_ENABLED:
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
    product, action = catalog_mod.upsert(d.get("product") or d, authmod.current_admin())
    if not product:
        return jsonify(ok=False, error="A product needs at least a name."), 400
    return jsonify(ok=True, product=product, action=action, meta=catalog_mod.meta())

@api.delete("/admin/products/<pid>")
@authmod.require_admin
@sec.require_csrf
def admin_product_delete(pid):
    catalog_mod.remove(pid, authmod.current_admin())
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
@api.get("/admin/sync/status")
@authmod.require_admin
def admin_sync_status():
    """Report whether Supabase and the GitHub repo sync are configured."""
    sb = Config.SUPABASE_ENABLED
    ght = bool(Config.GITHUB_TOKEN)
    repo = Config.GITHUB_REPOSITORY or ""
    # try to resolve the repo from git remote when not set explicitly
    if not repo:
        try:
            import repo_sync
            repo = repo_sync._resolve_repo()
        except Exception:
            repo = ""
    return jsonify(ok=True, supabase=sb, gitToken=ght,
                   gitRepo=repo, gitBranch=Config.GITHUB_BRANCH,
                   onWrite=bool(Config.REPO_SYNC_ON_WRITE))


@api.post("/admin/sync/repo")
@authmod.require_admin
@sec.require_csrf
def admin_sync_repo():
    """Regenerate the repository data files and commit/push to GitHub.

    This is the manual "Sync to GitHub" button in the admin portal. It applies
    the same best-effort path as the automatic post-write sync but runs it
    synchronously so the admin gets an immediate result.
    """
    limited = sec.guard("repo-sync", limit=10, window=600)
    if limited:
        return limited
    try:
        import repo_sync
        ok, report = repo_sync.regenerate(commit=True, push=True)
    except Exception as exc:
        return jsonify(ok=False, error=f"Sync failed: {exc}"), 500
    audit(authmod.current_admin(), "admin.repo_sync",
          f"ok={ok} committed={report.get('committed')} pushed={report.get('pushed')}",
          _ip())
    return jsonify(ok=bool(ok), **report)


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
# Small owner-editable settings that every visitor needs (currently the
# homepage hero video). Kept in a JSON file next to the catalogue so it
# survives a redeploy on a persistent disk.
SITE_KEYS = ("heroVideo", "heroPoster", "heroDoc", "logoUrl", "shopBannerUrl")
SITE_TEXT_DEFAULTS = {"bannerFrom": "2026-09-15", "bannerTo": "2026-09-25"}
# Owner-written plain-text lines (the moving banner). Stored stripped of all
# markup; empty means the storefront shows its built-in default.
SITE_TEXT_KEYS = ("convBanner", "convBold", "shippingNote")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clean_iso_date(v, fallback=None):
    s = str(v or "").strip()[:10]
    if _ISO_DATE.match(s):
        return s
    return fallback

def _site_path():
    return os.environ.get("SITE_CONFIG_PATH") or os.path.join(
        os.path.dirname(Config.CATALOG_PATH) or ".", "site.json")

def _load_site():
    try:
        with open(_site_path(), "r", encoding="utf-8") as fh:
            d = json.load(fh) or {}
    except (OSError, ValueError):
        d = {}
    out = {k: sec.safe_url(str(d.get(k) or "")) for k in SITE_KEYS}
    for k, default in SITE_TEXT_DEFAULTS.items():
        out[k] = _clean_iso_date(d.get(k), default)
    for k in SITE_TEXT_KEYS:
        # shippingNote needs more space
        limit = 800 if k == "shippingNote" else 300
        out[k] = sec.clean(d.get(k) or "", limit)
    return out

@api.get("/site")
def site_config():
    """Public: the homepage reads this to know whether a hero video exists."""
    return jsonify(ok=True, site=_load_site())

@api.post("/admin/site")
@authmod.require_admin
@sec.require_csrf
def admin_site_update():
    d = request.get_json(silent=True) or {}
    cur = _load_site()
    for k in SITE_KEYS:
        if k in d:
            cur[k] = sec.safe_url(str(d.get(k) or ""))
    for k in SITE_TEXT_DEFAULTS:
        if k in d:
            cleaned = _clean_iso_date(d.get(k), None)
            if cleaned:
                cur[k] = cleaned
    for k in SITE_TEXT_KEYS:
        if k in d:
            limit = 800 if k == "shippingNote" else 300
            cur[k] = sec.clean(d.get(k), limit)
    path = _site_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cur, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    audit(authmod.current_admin(), "site.update", json.dumps(cur)[:200], _ip())
    return jsonify(ok=True, site=cur)

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

# ==================================================== public: abandoned carts
@api.post("/cart/abandon")
@sec.require_csrf
def cart_abandon():
    """Capture an in-progress checkout early (email + cart), so a stalled
    one can be emailed a recovery link after the configured delay."""
    limited = sec.guard("cart-abandon", limit=60, window=3600)
    if limited: return limited
    import growth
    if not growth.settings()["abandonedEnabled"]:
        return jsonify(ok=True, skipped=True)
    d = request.get_json(silent=True) or {}
    ok = growth.save_abandoned(d.get("token"), d.get("email"),
                               d.get("items"), d.get("currency"))
    return jsonify(ok=bool(ok))

@api.get("/cart/recover/<token>")
def cart_recover(token):
    """The link in the reminder email: returns the saved cart."""
    import growth
    cart = growth.recover_cart(token)
    if not cart:
        return jsonify(ok=False, error="This cart link has expired or was already used."), 404
    return jsonify(ok=True, **cart)

# ================================================= public: verified reviews
@api.get("/reviews/<pid>")
def reviews_list(pid):
    pid = sec.clean(pid, 64)
    rows = query("SELECT name, stars, note, at FROM product_reviews "
                 "WHERE product_id=? ORDER BY at DESC LIMIT 100", (pid,))
    items = [dict(r) for r in rows]
    n = len(items)
    avg = round(sum(r["stars"] for r in items) / n, 2) if n else 0
    return jsonify(ok=True, productId=pid, count=n, average=avg, reviews=items)

@api.post("/reviews")
@sec.require_csrf
def reviews_create():
    """A customer review: stars + note, only for products they bought.
    The buyer is verified against the orders table by email."""
    limited = sec.guard("review", limit=10, window=3600)
    if limited: return limited
    d = request.get_json(silent=True) or {}
    pid = sec.clean(d.get("productId"), 64)
    email = sec.clean_email(d.get("email"))
    name = sec.clean(d.get("name"), 60)
    stars = sec.clean_int(d.get("stars"), 5, 1, 5)
    note = sec.clean(d.get("note"), 600)
    if not pid or not note:
        return jsonify(ok=False, error="Add a short note about the product."), 400
    if not email:
        return jsonify(ok=False, error="Enter the email you used for your order."), 400

    bought = one("SELECT id FROM orders WHERE email=? AND status != 'declined' "
                 "AND payload LIKE ? LIMIT 1", (email, f'%"id": "{pid}"%'))
    if not bought:
        return jsonify(ok=False, error="Reviews are for customers who bought this product. "
                                       "Use the same email as your order."), 403

    execute("INSERT INTO product_reviews (product_id, order_id, email, name, stars, note, at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(product_id, email) DO UPDATE SET "
            "name=excluded.name, stars=excluded.stars, note=excluded.note, at=excluded.at",
            (pid, bought["id"], email, name or "Customer", stars, note, _utcnow()))
    # product_reviews is SQLite-only (wiped with the Render disk), so keep a
    # JSON copy in Supabase growth_settings - the boot restore in app.py
    # writes it back. Best effort: a Supabase hiccup never blocks the review.
    if Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY:
        try:
            from supabase_store import save_product_reviews
            rows = query("SELECT product_id, order_id, email, name, stars, note, at "
                         "FROM product_reviews")
            save_product_reviews([dict(r) for r in rows])
        except Exception:
            pass
    audit("customer", "review.posted", f"{pid} {stars}★ by {email}", _ip())
    return reviews_list(pid)

# =============================================== admin: growth & marketing
@api.get("/admin/growth/settings")
@authmod.require_admin
def admin_growth_settings():
    import growth
    return jsonify(ok=True, settings=growth.settings())

@api.post("/admin/growth/settings")
@authmod.require_admin
@sec.require_csrf
def admin_growth_settings_save():
    import growth
    d = request.get_json(silent=True) or {}
    saved = growth.save_settings(d, authmod.current_admin())
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
    percent = sec.clean_int(d.get("percent"), None, 1, 90)
    if not percent:
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

@api.get("/admin/abandoned")
@authmod.require_admin
def admin_abandoned():
    rows = query("SELECT id, email, cart_json, currency, created_at, updated_at, "
                 "reminded_at, completed_at FROM abandoned_carts "
                 "ORDER BY updated_at DESC LIMIT 200")
    items = []
    for r in rows:
        d = dict(r)
        try:
            d["items"] = json.loads(d.pop("cart_json") or "[]")
        except ValueError:
            d["items"] = []
        items.append(d)
    stats = dict(one("SELECT COUNT(*) total, "
                     "SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) recovered, "
                     "SUM(CASE WHEN reminded_at IS NOT NULL THEN 1 ELSE 0 END) reminded "
                     "FROM abandoned_carts") or {})
    return jsonify(ok=True, carts=items, stats=stats)

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
