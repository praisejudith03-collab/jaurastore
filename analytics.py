"""Server-side analytics: page views, unique visitors, product interest,
checkout attempts and conversion. Everything is counted here, never in the
browser, so the numbers survive a cleared cache, a new device or a new browser.
"""
import datetime, json, secrets
from flask import request, make_response, jsonify
from config import Config
from db import execute, one, query
import security as sec

VID_COOKIE = "jaura_vid"
VID_MAX_AGE = 60 * 60 * 24 * 365          # one year
KINDS = ("visit", "view", "cart", "checkout_start", "purchase", "heartbeat")


def _now():
    return datetime.datetime.utcnow()


def _day(dt=None):
    return (dt or _now()).strftime("%Y-%m-%d")


def _iso(dt=None):
    return (dt or _now()).isoformat(timespec="seconds")


def _days_ago(n):
    return (_now() - datetime.timedelta(days=n)).strftime("%Y-%m-%d")


# --------------------------------------------------------------- visitor id
def visitor_id(resp=None):
    """Read (or mint) the long-lived visitor cookie. Returns (vid, is_new)."""
    vid = (request.cookies.get(VID_COOKIE) or "").strip()
    is_new = False
    if len(vid) < 8 or len(vid) > 64 or not all(c.isalnum() or c in "-_" for c in vid):
        vid = "v" + secrets.token_hex(12)
        is_new = True
    return vid, is_new


def stamp_cookie(resp, vid):
    resp.set_cookie(
        VID_COOKIE, vid,
        max_age=VID_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=(Config.ENV == "production"),
        path="/",
    )
    return resp


def _geo(d):
    city = sec.clean(d.get("city"), 80) or ""
    region = sec.clean(d.get("region"), 80) or ""
    country = sec.clean(d.get("country"), 80) or ""
    return city, region, country


# --------------------------------------------------------------- recording
def record(items, vid, is_new):
    """Store a batch of tracking events. `items` is a list of dicts."""
    now = _now()
    iso = _iso(now)
    day = _day(now)
    sid = ""
    ref = ""
    city = region = country = ""
    ua = (request.headers.get("User-Agent") or "")[:200]

    stored = 0
    for raw in items[:40]:
        if not isinstance(raw, dict):
            continue
        kind = sec.clean(raw.get("type"), 24)
        if kind not in KINDS:
            continue
        sid = sid or sec.clean(raw.get("sid"), 48)
        ref = ref or sec.clean(raw.get("ref"), 300)
        c, rg, co = _geo(raw)
        city, region, country = city or c, region or rg, country or co
        path = sec.clean(raw.get("path"), 200)
        page = sec.clean(raw.get("page"), 40)
        pid = sec.clean(raw.get("productId"), 64)
        pname = sec.clean(raw.get("productName"), 160)
        value = sec.clean_int(raw.get("value"), 0, 0, 10**12)
        currency = sec.clean(raw.get("currency"), 3).upper()

        if kind == "visit":
            execute(
                "INSERT INTO page_views (vid, sid, path, page, ref, city, country, day, at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (vid, sid, path, page, ref, city, country, day, iso),
            )
            stored += 1
        elif kind != "heartbeat":
            execute(
                "INSERT INTO events (type, vid, sid, product_id, product_name, page, path, "
                "value, currency, city, country, day, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (kind, vid, sid, pid, pname, page, path, value, currency, city, country, day, iso),
            )
            stored += 1

    if sid and not one("SELECT 1 FROM page_views WHERE vid=? AND sid=? LIMIT 1", (vid, sid)):
        execute("UPDATE visitors SET sessions=sessions+1 WHERE vid=?", (vid,))

    # presence heartbeat
    if items:
        last = items[-1] if isinstance(items[-1], dict) else {}
        execute(
            "INSERT INTO presence (vid, sid, page, path, city, country, at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(vid) DO UPDATE SET sid=excluded.sid, page=excluded.page, "
            "path=excluded.path, city=excluded.city, country=excluded.country, at=excluded.at",
            (vid, sid, sec.clean(last.get("page"), 40), sec.clean(last.get("path"), 200),
             city, country, iso),
        )

    execute(
        "INSERT INTO visitors (vid, first_at, last_at, sessions, city, region, country, referrer, ua) "
        "VALUES (?,?,?,1,?,?,?,?,?) ON CONFLICT(vid) DO UPDATE SET last_at=excluded.last_at, "
        "city=COALESCE(NULLIF(excluded.city,''), visitors.city), "
        "region=COALESCE(NULLIF(excluded.region,''), visitors.region), "
        "country=COALESCE(NULLIF(excluded.country,''), visitors.country)",
        (vid, iso, iso, city, region, country, ref, ua),
    )
    return stored


# ----------------------------------------------------------------- reporting
def live_now():
    cutoff = _iso(_now() - datetime.timedelta(seconds=Config.LIVE_WINDOW_SECONDS))
    rows = query(
        "SELECT vid, page, path, city, country, at FROM presence WHERE at >= ? "
        "ORDER BY at DESC LIMIT 50", (cutoff,))
    return [dict(r) for r in rows]


def recent_activity(minutes=60, limit=40):
    """The live feed: who viewed which product, what went into carts and
    which orders were placed — most recent first."""
    cutoff = _iso(_now() - datetime.timedelta(minutes=max(1, min(int(minutes or 60), 24 * 60))))
    rows = query(
        "SELECT type, vid, product_id productId, product_name productName, "
        "page, path, value, currency, city, country, at "
        "FROM events WHERE at >= ? AND type IN ('view','cart','checkout_start','purchase') "
        "ORDER BY at DESC LIMIT ?", (cutoff, max(1, min(int(limit or 40), 200))))
    return [dict(r) for r in rows]


def _period_block(since_day, since_iso):
    pv = dict(one("SELECT COUNT(*) n, COUNT(DISTINCT vid) uniq, COUNT(DISTINCT sid) sessions "
                  "FROM page_views WHERE day >= ?", (since_day,)) or {})
    o = dict(one("SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN status != 'declined' THEN total END),0) value "
                 "FROM orders WHERE at >= ?", (since_iso,)) or {})
    return {
        "pageViews": pv.get("n", 0) or 0,
        "visitors": pv.get("uniq", 0) or 0,
        "visits": pv.get("sessions", 0) or 0,
        "orders": o.get("n", 0) or 0,
        "revenue": o.get("value", 0) or 0,
    }


def periods():
    """Visits today / this week / this month — the Wix-style headline cards."""
    return {
        "today": _period_block(_day(), _iso(_now().replace(hour=0, minute=0, second=0, microsecond=0))),
        "week": _period_block(_days_ago(6), _iso(_now() - datetime.timedelta(days=6))),
        "month": _period_block(_days_ago(29), _iso(_now() - datetime.timedelta(days=29))),
    }


def sales_series(days=30):
    """Orders and revenue per day, for the sales-over-time chart."""
    days = max(1, min(int(days or 30), 400))
    since_day = _days_ago(days - 1)
    rows = query(
        "SELECT substr(at, 1, 10) day, COUNT(*) orders, "
        "COALESCE(SUM(CASE WHEN status != 'declined' THEN total END),0) revenue "
        "FROM orders WHERE substr(at, 1, 10) >= ? GROUP BY substr(at, 1, 10)", (since_day,))
    by_day = {r["day"]: dict(r) for r in rows}
    out = []
    for i in range(days - 1, -1, -1):
        d = _days_ago(i)
        row = by_day.get(d) or {}
        out.append({"day": d, "orders": row.get("orders", 0) or 0,
                    "revenue": row.get("revenue", 0) or 0})
    return out


def report(days=30):
    days = max(1, min(int(days or 30), 400))
    since_day = _days_ago(days - 1)
    since_iso = _iso(_now() - datetime.timedelta(days=days - 1))

    pv = dict(one("SELECT COUNT(*) n, COUNT(DISTINCT vid) uniq, COUNT(DISTINCT sid) sessions "
                  "FROM page_views WHERE day >= ?", (since_day,)) or {})
    totals = {
        "pageViews": pv.get("n", 0) or 0,
        "uniqueVisitors": pv.get("uniq", 0) or 0,
        "visits": pv.get("sessions", 0) or 0,
        "newVisitors": (dict(one("SELECT COUNT(*) n FROM visitors WHERE first_at >= ?", (since_iso,)) or {})).get("n", 0) or 0,
        "liveNow": len(live_now()),
    }

    series_rows = query(
        "SELECT day, COUNT(*) views, COUNT(DISTINCT vid) visitors, COUNT(DISTINCT sid) sessions "
        "FROM page_views WHERE day >= ? GROUP BY day ORDER BY day", (since_day,))
    by_day = {r["day"]: dict(r) for r in series_rows}
    series = []
    for i in range(days - 1, -1, -1):
        d = _days_ago(i)
        row = by_day.get(d) or {"day": d, "views": 0, "visitors": 0, "sessions": 0}
        series.append({"day": d, "views": row["views"] or 0,
                       "visitors": row["visitors"] or 0, "sessions": row["sessions"] or 0})

    top_pages = [dict(r) for r in query(
        "SELECT COALESCE(NULLIF(path,''), '/') path, page, COUNT(*) views, "
        "COUNT(DISTINCT vid) visitors FROM page_views WHERE day >= ? "
        "GROUP BY path ORDER BY views DESC LIMIT 12", (since_day,))]

    top_products = [dict(r) for r in query(
        "SELECT product_id productId, MAX(product_name) name, "
        "SUM(CASE WHEN type='view' THEN 1 ELSE 0 END) views, "
        "SUM(CASE WHEN type='cart' THEN 1 ELSE 0 END) carts, "
        "SUM(CASE WHEN type='purchase' THEN 1 ELSE 0 END) purchases "
        "FROM events WHERE day >= ? AND product_id != '' "
        "GROUP BY product_id ORDER BY (views + carts*3 + purchases*5) DESC LIMIT 12",
        (since_day,))]

    ev = {r["type"]: r["n"] for r in query(
        "SELECT type, COUNT(*) n FROM events WHERE day >= ? GROUP BY type", (since_day,))}

    ord_rows = query(
        "SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN status != 'declined' THEN total END),0) value, "
        "COALESCE(SUM(items_count),0) units FROM orders WHERE at >= ?", (since_iso,)) or []
    o = dict(ord_rows[0]) if ord_rows else {}
    order_count = o.get("n", 0) or 0
    revenue = o.get("value", 0) or 0
    by_cur = [dict(r) for r in query(
        "SELECT currency, COUNT(*) orders, COALESCE(SUM(total),0) value FROM orders "
        "WHERE at >= ? AND status != 'declined' GROUP BY currency", (since_iso,))]
    by_status = [dict(r) for r in query(
        "SELECT status, COUNT(*) n FROM orders WHERE at >= ? GROUP BY status", (since_iso,))]

    attempts = ev.get("checkout_start", 0) or 0
    conversion = {
        "orders": order_count,
        "units": o.get("units", 0) or 0,
        "revenue": revenue,
        "revenueByCurrency": by_cur,
        "statusBreakdown": by_status,
        "checkoutAttempts": attempts,
        "cartAdds": ev.get("cart", 0) or 0,
        "productViews": ev.get("view", 0) or 0,
        "averageOrderValue": round(revenue / order_count, 2) if order_count else 0,
        "visitToOrderRate": round(100.0 * order_count / totals["visits"], 2) if totals["visits"] else 0,
        "checkoutCompletionRate": round(100.0 * order_count / attempts, 2) if attempts else 0,
    }

    recent_orders = [dict(r) for r in query(
        "SELECT id, at, total, currency, status, customer_name, city, items_count "
        "FROM orders ORDER BY at DESC LIMIT 8")]

    locations = [dict(r) for r in query(
        "SELECT city, country, COUNT(*) visitors FROM visitors "
        "WHERE city != '' OR country != '' GROUP BY city, country "
        "ORDER BY visitors DESC LIMIT 12")]

    return {
        "ok": True,
        "range": {"days": days, "from": since_day, "to": _day()},
        "totals": totals,
        "periods": periods(),
        "series": series,
        "sales": sales_series(days),
        "topPages": top_pages,
        "topProducts": top_products,
        "conversion": conversion,
        "recentOrders": recent_orders,
        "locations": locations,
        "live": live_now(),
        "activity": recent_activity(),
    }


def prune(retention_days=None):
    """Drop raw analytics rows older than the retention window. Orders are kept."""
    days = int(retention_days or Config.ANALYTICS_RETENTION_DAYS)
    cutoff_day = _days_ago(max(1, days))
    cutoff_iso = _iso(_now() - datetime.timedelta(days=max(1, days)))
    execute("DELETE FROM page_views WHERE day < ?", (cutoff_day,))
    execute("DELETE FROM events WHERE day < ?", (cutoff_day,))
    execute("DELETE FROM presence WHERE at < ?",
            (_iso(_now() - datetime.timedelta(days=2)),))
    execute("DELETE FROM visitors WHERE last_at < ?", (cutoff_iso,))
    return True
