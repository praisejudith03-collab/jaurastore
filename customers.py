"""Customer accounts and sessions.

Shoppers are not admins. A customer session lives in Flask's ``customer_id``
key and is independent of ``admin_email``. Guest checkout stays available,
but account access has no claim-by-email flow.
"""
import datetime, json, secrets
from flask import jsonify, request, session
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from db import execute, one, query, audit
import auth as authmod
import security as sec


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


def _ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else "") or request.remote_addr or ""


def public_customer(row):
    """Profile fields the shopper may see. Never includes password_hash."""
    if not row:
        return None
    d = dict(row)
    d.pop("password_hash", None)
    return {
        "id": d.get("id"),
        "email": d.get("email"),
        "name": d.get("name") or "",
        "phone": d.get("phone") or "",
        "country": d.get("country") or "",
        "city": d.get("city") or "",
        "delivery_address": d.get("delivery_address") or "",
        "preferred_currency": d.get("preferred_currency") or "NGN",
        "created_at": d.get("created_at") or "",
        "updated_at": d.get("updated_at") or "",
    }


def current_customer_id():
    cid = session.get("customer_id")
    return str(cid) if cid else None


def current_customer():
    cid = current_customer_id()
    if not cid:
        return None
    return one("SELECT * FROM customers WHERE id=?", (cid,))


def login_session(customer_id):
    """Open a customer session. Never leaves an admin session in place."""
    session.permanent = True
    session["customer_id"] = customer_id
    session.pop("admin_email", None)


def logout_session():
    session.pop("customer_id", None)


def require_customer(f):
    from functools import wraps

    @wraps(f)
    def wrapper(*a, **kw):
        if not current_customer_id():
            return jsonify(ok=False, error="Please sign in."), 401
        return f(*a, **kw)

    return wrapper


def customer_order_view(row):
    """Strip proof URLs, hashes and admin fields from an order row."""
    if not row:
        return None
    d = dict(row)
    raw = d.get("payload")
    if isinstance(raw, dict):
        payload = raw
    else:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            payload = {}
    items = []
    for it in (payload.get("items") or []):
        if not isinstance(it, dict):
            continue
        items.append({
            "name": it.get("name") or "",
            "qty": it.get("qty") or 1,
            "price": it.get("price") or 0,
            "color": it.get("color") or "",
        })
    delivery = payload.get("delivery")
    if not isinstance(delivery, dict):
        delivery = {
            "zone": d.get("zone") or "",
            "city": d.get("city") or "",
            "address": d.get("address") or "",
            "country": d.get("country") or "",
        }
    return {
        "id": d.get("id"),
        "status": d.get("status") or payload.get("status") or "pending",
        "at": d.get("at") or payload.get("at") or "",
        "items": items,
        "total": d.get("total") if d.get("total") is not None else payload.get("total"),
        "currency": d.get("currency") or payload.get("currency") or "",
        "delivery": delivery,
        "payment": d.get("payment") or payload.get("payment") or "",
        # Current admin review notices are safe customer-facing text. Exposing
        # them here makes decline/partial-payment messages visible in My Account
        # even when an email provider is delayed or unavailable.
        "customer_notice": payload.get("customer_notice") or None,
        "payment_review": payload.get("payment_review") or None,
    }


def persist_customer(row):
    try:
        from supabase_store import save_customer
        save_customer(dict(row) if row else None)
    except Exception:
        pass


def link_guest_orders_to_account(customer_id, email):
    """Link earlier guest checkouts to the account that owns the email.

    The customer must first create and authenticate an account password; this
    is not an email-reset or recovery route. Existing orders are linked only
    when they have no account owner, and the same association is mirrored in
    Supabase when production is configured.
    """
    if not customer_id or not email:
        return 0
    linked = 0
    try:
        cur = execute(
            "UPDATE orders SET customer_user_id=? "
            "WHERE customer_user_id IS NULL AND lower(email)=lower(?)",
            (customer_id, email),
        )
        linked = int(getattr(cur, "rowcount", 0) or 0)
    except Exception:
        pass
    try:
        from supabase_store import enabled, link_guest_orders
        if enabled():
            linked = max(linked, int(link_guest_orders(customer_id, email) or 0))
    except Exception:
        pass
    return linked


_ROUTES_REGISTERED = False


def register_routes(bp):
    """Attach /api/account/* onto the existing API blueprint."""
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return
    _ROUTES_REGISTERED = True


    @bp.get("/account/session")
    def account_session():
        row = current_customer()
        return jsonify(ok=True, authenticated=bool(row),
                       customer=public_customer(row), csrf=sec.issue_csrf())

    @bp.post("/account/register")
    @sec.require_csrf
    def account_register():
        limited = sec.guard("cust-register", limit=8, window=3600)
        if limited:
            return limited
        d = request.get_json(silent=True) or {}
        email = sec.clean_email(d.get("email"))
        pw = d.get("password") or ""
        if not email:
            return jsonify(ok=False, error="A valid email address is required."), 400
        ok, msg = authmod.password_strong(pw)
        if not ok:
            return jsonify(ok=False, error=msg), 400
        if one("SELECT id FROM customers WHERE email=?", (email,)):
            return jsonify(ok=False, error="An account with that email already exists."), 409
        cid = secrets.token_hex(16)
        name = sec.clean(d.get("name"), 120)
        phone = sec.clean(d.get("phone"), 60)
        country = sec.clean(d.get("country"), 80)
        city = sec.clean(d.get("city"), 80)
        address = sec.clean(d.get("delivery_address") or d.get("address"), 300)
        currency = (sec.clean(d.get("preferred_currency"), 3) or "NGN").upper()
        if currency not in ("NGN", "CFA"):
            currency = "NGN"
        now = _now()
        execute(
            "INSERT INTO customers (id, email, password_hash, name, phone, country, city, "
            "delivery_address, preferred_currency, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, email, generate_password_hash(str(pw)), name, phone, country, city,
             address, currency, now, now),
        )
        row = one("SELECT * FROM customers WHERE id=?", (cid,))
        persist_customer(row)
        link_guest_orders_to_account(cid, email)
        login_session(cid)
        audit(email, "customer.register", cid, _ip())
        return jsonify(ok=True, customer=public_customer(row), csrf=sec.issue_csrf()), 201

    @bp.post("/account/login")
    @sec.require_csrf
    def account_login():
        d = request.get_json(silent=True) or {}
        email = sec.clean_email(d.get("email"))
        pw = d.get("password") or ""
        limited = sec.guard("cust-login", limit=8, window=300,
                            key_extra=email or "anon")
        if limited:
            return limited
        row = one("SELECT * FROM customers WHERE email=?", (email,)) if email else None
        if not row or not pw or not check_password_hash(row["password_hash"], str(pw)):
            audit(email or "?", "customer.login_failed", "bad credentials", _ip())
            return jsonify(ok=False, error="Invalid email or password."), 401
        link_guest_orders_to_account(row["id"], email)
        login_session(row["id"])
        sec.clear_rate("cust-login", email)
        audit(email, "customer.login", "success", _ip())
        return jsonify(ok=True, customer=public_customer(row), csrf=sec.issue_csrf())

    @bp.post("/account/logout")
    def account_logout_post():
        row = current_customer()
        if row:
            audit(row["email"], "customer.logout", "", _ip())
        logout_session()
        return jsonify(ok=True, csrf=sec.issue_csrf())

    @bp.patch("/account/profile")
    @require_customer
    @sec.require_csrf
    def account_profile():
        row = current_customer()
        d = request.get_json(silent=True) or {}
        name = sec.clean(d.get("name"), 120) if "name" in d else row["name"]
        email = sec.clean_email(d.get("email")) if "email" in d else row["email"]
        if not email:
            return jsonify(ok=False, error="A valid email address is required."), 400
        if email != (row["email"] or "").lower():
            clash = one("SELECT id FROM customers WHERE email=? AND id!=?",
                        (email, row["id"]))
            if clash:
                return jsonify(ok=False, error="An account with that email already exists."), 409
        phone = sec.clean(d.get("phone"), 60) if "phone" in d else row["phone"]
        country = sec.clean(d.get("country"), 80) if "country" in d else row["country"]
        city = sec.clean(d.get("city"), 80) if "city" in d else row["city"]
        address = (sec.clean(d.get("delivery_address") or d.get("address"), 300)
                   if ("delivery_address" in d or "address" in d)
                   else row["delivery_address"])
        currency = row["preferred_currency"]
        if "preferred_currency" in d:
            currency = (sec.clean(d.get("preferred_currency"), 3) or "NGN").upper()
            if currency not in ("NGN", "CFA"):
                currency = "NGN"
        now = _now()
        execute(
            "UPDATE customers SET email=?, name=?, phone=?, country=?, city=?, "
            "delivery_address=?, preferred_currency=?, updated_at=? WHERE id=?",
            (email, name, phone, country, city, address, currency, now, row["id"]),
        )
        fresh = one("SELECT * FROM customers WHERE id=?", (row["id"],))
        persist_customer(fresh)
        audit(email, "customer.profile", row["id"], _ip())
        return jsonify(ok=True, customer=public_customer(fresh))

    @bp.post("/account/password")
    @require_customer
    @sec.require_csrf
    def account_password():
        limited = sec.guard("cust-password", limit=8, window=600)
        if limited:
            return limited
        row = current_customer()
        d = request.get_json(silent=True) or {}
        if not check_password_hash(row["password_hash"], str(d.get("currentPassword") or "")):
            return jsonify(ok=False, error="Your current password is incorrect."), 403
        newpw = d.get("newPassword") or d.get("password") or ""
        ok, msg = authmod.password_strong(newpw)
        if not ok:
            return jsonify(ok=False, error=msg), 400
        execute("UPDATE customers SET password_hash=?, updated_at=? WHERE id=?",
                (generate_password_hash(str(newpw)), _now(), row["id"]))
        persist_customer(one("SELECT * FROM customers WHERE id=?", (row["id"],)))
        audit(row["email"], "customer.password_changed", "", _ip())
        return jsonify(ok=True, message="Password updated.")

    @bp.get("/account/orders")
    @require_customer
    def account_orders():
        cid = current_customer_id()
        rows = None
        try:
            from supabase_store import enabled as _sb_on, load_orders_for_customer
            if _sb_on():
                rows = load_orders_for_customer(cid)
        except Exception:
            rows = None
        if rows is None:
            rows = [dict(r) for r in query(
                "SELECT * FROM orders WHERE customer_user_id=? ORDER BY at DESC LIMIT 200",
                (cid,))]
        return jsonify(ok=True, orders=[customer_order_view(r) for r in rows])

    @bp.get("/account/orders/<oid>")
    @require_customer
    def account_order(oid):
        cid = current_customer_id()
        oid = sec.clean(oid, 24).upper()
        row = None
        try:
            from supabase_store import enabled as _sb_on, load_order_for_customer
            if _sb_on():
                row = load_order_for_customer(oid, cid)
        except Exception:
            row = None
        if row is None:
            found = one("SELECT * FROM orders WHERE id=? AND customer_user_id=?",
                        (oid, cid))
            row = dict(found) if found else None
        if not row:
            return jsonify(ok=False, error="We could not find that order."), 404
        return jsonify(ok=True, order=customer_order_view(row))
