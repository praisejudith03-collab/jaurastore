"""Server-side analytics: page views, unique visitors, product interest,
checkout attempts and conversion. Everything is counted here, never in the
browser, so the numbers survive a cleared cache, a new device or a new browser.
"""
import csv
import datetime
import io
import json
import os
import secrets
import contextlib
from flask import request, make_response, jsonify
from config import Config
from db import execute, execute_many, one, query
import security as sec

CONFIRMED = "confirmed"

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
    """Resolve the visitor's location.

    The edge headers (Cloudflare / Vercel / Render) are geocoded from the
    CLIENT IP the edge saw, so they are the trustworthy source and they are
    read first. They are only meaningful when the request actually came
    through that edge for a real client address: if the connection carries
    no client IP at all (an internal health ping, the keep-alive worker)
    the header would describe the datacentre, which is how every visitor
    ended up pinned to Finland. In that case the location is dropped.

    The browser-reported values (`d`) are the last resort, and are never
    allowed to overrule the edge. No IP address is stored, in either path.
    """
    try:
        h = request.headers
    except RuntimeError:                  # no request context: browser data only
        h = {}
    if not h:
        city = sec.clean(d.get("city"), 80) or ""
        region = sec.clean(d.get("region"), 80) or ""
        country = sec.clean(d.get("country"), 80).upper() or ""
        return city, region, country
    city = (h.get("CF-IPCity") or h.get("X-Vercel-IP-City") or
            h.get("X-Render-City") or "")
    region = (h.get("CF-Region") or h.get("X-Vercel-IP-Country-Region") or
              h.get("X-Render-Region") or "")
    country = (h.get("CF-IPCountry") or h.get("X-Vercel-IP-Country") or
               h.get("X-Render-Country") or "")
    edge = bool(city or region or country)
    if edge and not sec.is_public_ip(sec.client_ip()):
        # Edge header with no real client address behind it: a proxy/monitor
        # hop. Attributing it to a shopper is exactly the Finland defect.
        city = region = country = ""
        edge = False
    if not edge:
        city = d.get("city") or ""
        region = d.get("region") or ""
        country = d.get("country") or ""
    try:
        from urllib.parse import unquote
        city, region = unquote(str(city)), unquote(str(region))
    except Exception:
        pass
    # "XX" is Cloudflare's own "this is a bot / unknown" country code, and
    # "T1" is Tor. Neither is a place a customer lives.
    country = sec.clean(country, 80).upper() or ""
    if country in ("XX", "T1"):
        city = region = country = ""
    return sec.clean(city, 80) or "", sec.clean(region, 80) or "", country


# --------------------------------------------------------------- recording
def should_skip_bot():
    """True when this request is automated traffic, not a customer.

    Crawlers, uptime monitors and scrapers never buy anything, but they hit
    every page: counting them inflated page views and - because most of
    them run on cheap European VPS estates - filled the live location map
    with visitors from Finland and other datacentres. The storefront still
    answers them normally; only the analytics tables ignore them.

    Outside a request (a server-side caller, a migration, a test) there is
    no user agent to judge, so nothing is skipped.
    """
    try:
        if not request:
            return False
        return bool(sec.bot_reason())
    except RuntimeError:                  # no request context
        return False


def record(items, vid, is_new):
    """Store a batch of tracking events. `items` is a list of dicts.

    Automated traffic is dropped before anything is written, so a crawler
    can never move a counter, a location or the live-visitor list.
    """
    if should_skip_bot():
        return 0
    now = _now()
    iso = _iso(now)
    day = _day(now)
    sid = ""
    ref = ""
    city = region = country = ""
    ua = (request.headers.get("User-Agent") or "")[:200]

    stored = 0
    mirror_batch = []
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
            mirror_batch.append({
                "kind": "page_view", "vid": vid, "sid": sid, "path": path,
                "page": page, "ref": ref, "product_id": "", "product_name": "",
                "value": 0, "currency": "", "city": city, "region": region,
                "country": country, "day": day, "at": iso,
            })
            stored += 1
        elif kind != "heartbeat":
            execute(
                "INSERT INTO events (type, vid, sid, product_id, product_name, page, path, "
                "value, currency, city, country, day, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (kind, vid, sid, pid, pname, page, path, value, currency, city, country, day, iso),
            )
            mirror_batch.append({
                "kind": kind, "vid": vid, "sid": sid, "path": path,
                "page": page, "ref": "", "product_id": pid,
                "product_name": pname, "value": value, "currency": currency,
                "city": city, "region": region, "country": country,
                "day": day, "at": iso,
            })
            stored += 1

    if mirror_batch:
        # The durable copy (Supabase analytics_events). Best-effort and out
        # of the counting path: when the mirror is down the shop keeps
        # counting locally and only durability has a gap for this batch.
        try:
            from supabase_store import mirror_analytics_events
            mirror_analytics_events(mirror_batch)
        except Exception:
            pass
        # Lifetime odometer. page_views/events are pruned to the retention
        # window and restored after a wipe; these totals are only ever
        # incremented, so the headline numbers can never fall back to zero.
        views = sum(1 for m in mirror_batch if m["kind"] == "page_view")
        if views:
            bump_counter("page_views_total", views)
        bump_counter("events_total", len(mirror_batch))

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


# ------------------------------------------------------- lifetime counters
# Page views, events and searches are stored as rows with a retention
# window, so pruning (or a wiped disk before the restore lands) moves the
# headline numbers. These counters are the store's odometer: monotonic,
# mirrored to Supabase and merged by taking the maximum of the two sides,
# so a deploy, a restart or a worker reboot can never reset them to zero.
COUNTER_NAMES = ("page_views_total", "events_total", "searches_total")


def bump_counter(name, by=1):
    """Increment a lifetime counter. Never raises - counting is not a sale."""
    try:
        by = int(by or 0)
        if by <= 0:
            return 0
        execute(
            "INSERT INTO analytics_counters (name, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET value=analytics_counters.value+excluded.value, "
            "updated_at=excluded.updated_at",
            (str(name)[:60], by, _iso()))
        return counter(name)
    except Exception:
        return 0


def counter(name):
    """The current value of one lifetime counter."""
    try:
        row = one("SELECT value FROM analytics_counters WHERE name=?", (str(name),))
        return int((row or {"value": 0})["value"] or 0)
    except Exception:
        return 0


def counters():
    """Every lifetime counter as a plain dict."""
    out = {name: 0 for name in COUNTER_NAMES}
    try:
        for row in query("SELECT name, value FROM analytics_counters"):
            out[row["name"]] = int(row["value"] or 0)
    except Exception:
        pass
    return out


def persist_counters():
    """Push the odometer to Supabase. Best-effort; returns True on success."""
    try:
        rows = [{"name": name, "value": int(value), "updated_at": _iso()}
                for name, value in counters().items()]
        from supabase_store import save_analytics_counters
        return bool(save_analytics_counters(rows))
    except Exception:
        return False


def restore_counters(rows=None):
    """Merge the stored odometer back in after a wipe. Never raises.

    The maximum of (local, remote) wins, so restoring can only ever move a
    counter forward - a stale mirror cannot undo traffic counted since.
    Returns the number of counters that were moved forward.
    """
    try:
        if rows is None:
            from supabase_store import load_analytics_counters
            rows = load_analytics_counters()
        moved = 0
        local = counters()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")[:60]
            if not name:
                continue
            try:
                remote = int(row.get("value") or 0)
            except (TypeError, ValueError):
                continue
            if remote > int(local.get(name, 0) or 0):
                execute(
                    "INSERT INTO analytics_counters (name, value, updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=excluded.value, "
                    "updated_at=excluded.updated_at",
                    (name, remote, _iso()))
                moved += 1
        return moved
    except Exception as exc:
        print(f"[analytics] counter restore failed: {exc}")
        return 0


# -------------------------------------------------------- search history
def _normalise_query(value):
    """Fold case and whitespace so "Body Oil" and "body  oil" group together."""
    return " ".join(str(value or "").lower().split())[:120]


def record_search(q, results=0, category="", vid="", sid=""):
    """Store one customer search. Returns True when it was stored.

    Bots never reach this (the same filter the event tracker uses), and an
    empty query is not a search. Every stored row is mirrored to Supabase
    so the history survives a redeploy.
    """
    try:
        if should_skip_bot():
            return False
        text = sec.clean(q, 120, allow_newlines=False)
        norm = _normalise_query(text)
        if not norm:
            return False
        now = _now()
        iso, day = _iso(now), _day(now)
        city, region, country = _geo({})
        results = sec.clean_int(results, 0, 0, 10 ** 6)
        category = sec.clean(category, 60)
        vid = sec.clean(vid, 64)
        sid = sec.clean(sid, 48)
        execute(
            "INSERT INTO search_queries (vid, sid, q, q_norm, results, category, "
            "city, country, day, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (vid, sid, text, norm, results, category, city, country, day, iso))
        bump_counter("searches_total", 1)
        try:
            from supabase_store import mirror_search_queries
            mirror_search_queries([{
                "vid": vid, "sid": sid, "q": text, "q_norm": norm,
                "results": results, "category": category, "city": city,
                "country": country, "day": day, "at": iso,
            }])
        except Exception:
            pass                      # durability gap only; the row is stored
        return True
    except Exception as exc:
        print(f"[analytics] search not recorded: {exc}")
        return False


def top_searches(days=30, limit=20):
    """The most-used search terms, with how often they found nothing."""
    days = max(1, min(int(days or 30), 400))
    since = _days_ago(days - 1)
    rows = query(
        "SELECT q_norm term, COUNT(*) searches, "
        "COUNT(DISTINCT vid) visitors, "
        "SUM(CASE WHEN results = 0 THEN 1 ELSE 0 END) empty, "
        "MAX(at) last_at FROM search_queries WHERE day >= ? "
        "GROUP BY q_norm ORDER BY searches DESC, last_at DESC LIMIT ?",
        (since, max(1, min(int(limit or 20), 200))))
    return [dict(r) for r in rows]


def recent_searches(limit=40):
    """The newest searches, most recent first - the raw demand feed."""
    rows = query(
        "SELECT q, results, category, city, country, at FROM search_queries "
        "ORDER BY id DESC LIMIT ?", (max(1, min(int(limit or 40), 200)),))
    return [dict(r) for r in rows]


def search_report(days=30, limit=20):
    total = dict(one(
        "SELECT COUNT(*) n, COUNT(DISTINCT vid) visitors, "
        "SUM(CASE WHEN results = 0 THEN 1 ELSE 0 END) empty "
        "FROM search_queries WHERE day >= ?", (_days_ago(max(1, int(days or 30)) - 1),)) or {})
    return {
        "ok": True,
        "days": days,
        "searches": total.get("n", 0) or 0,
        "searchers": total.get("visitors", 0) or 0,
        "withoutResults": total.get("empty", 0) or 0,
        "lifetimeSearches": counter("searches_total"),
        "top": top_searches(days, limit),
        "recent": recent_searches(limit),
    }


def restore_searches_from_supabase(rows=None):
    """Copy the search history back after a wiped disk. Never raises.

    Mirrors restore_from_supabase(): a no-op unless the local window is
    empty (checked again inside the cross-process restore lock), so a normal
    reboot - or two workers booting together - cannot duplicate a row.
    """
    try:
        with _restore_lock():
            cutoff = _retention_cutoff_day()
            local = one("SELECT COUNT(*) n FROM search_queries WHERE day >= ?", (cutoff,))
            if local is None or (local["n"] or 0) > 0:
                return 0
            if rows is None:
                from supabase_store import load_search_queries
                rows = load_search_queries(cutoff)
            if not rows:
                return 0
            now_iso = _iso()
            batch = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                day = str(r.get("day") or "")[:10]
                if len(day) != 10 or day < cutoff:
                    continue
                text = str(r.get("q") or "")[:120]
                norm = str(r.get("q_norm") or "") or _normalise_query(text)
                if not norm:
                    continue
                try:
                    results = int(r.get("results") or 0)
                except (TypeError, ValueError):
                    results = 0
                batch.append((str(r.get("vid") or "")[:64], str(r.get("sid") or "")[:48],
                              text, norm[:120], results, str(r.get("category") or "")[:60],
                              str(r.get("city") or "")[:80], str(r.get("country") or "")[:80],
                              day, _clean_restored_at(r.get("at"), day, now_iso)))
            restored = 0
            for i in range(0, len(batch), 500):
                chunk = batch[i:i + 500]
                execute_many(
                    "INSERT INTO search_queries (vid, sid, q, q_norm, results, category, "
                    "city, country, day, at) VALUES (?,?,?,?,?,?,?,?,?,?)", chunk)
                restored += len(chunk)
            return restored
    except Exception as exc:
        print(f"[analytics] search restore failed: {exc}")
        return 0


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
    """Visits today / this week / this month — the headline cards."""
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
    # The odometer travels with the window totals so the dashboard can show
    # "since the shop opened" even right after a deploy replaced the disk.
    lifetime = counters()
    totals["lifetimePageViews"] = lifetime.get("page_views_total", 0)
    totals["lifetimeEvents"] = lifetime.get("events_total", 0)
    totals["lifetimeSearches"] = lifetime.get("searches_total", 0)

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
        "SELECT city, country, COUNT(*) visitors, COALESCE(SUM(sessions),0) sessions FROM visitors "
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
        "searches": top_searches(days, 12),
        "lifetime": counters(),
    }


def _sales_cutoffs(days=30):
    """Parse the sales range. "all"/0/None means every order ever."""
    if days is None or (isinstance(days, str) and days.strip().lower() == "all"):
        return None, None, "all", True
    try:
        n = int(days)
    except (TypeError, ValueError):
        n = 30
    if n <= 0:
        return None, None, "all", True
    n = max(1, min(n, 3650))
    return _days_ago(n - 1), _iso(_now() - datetime.timedelta(days=n - 1)), n, False


def _sales_bounds(days=30, date_from=None, date_to=None):
    """Return (start day, start timestamp, range label, all time, end ts, end day).

    Custom dates are inclusive at the day level and exclusive at midnight on
    the following day. Invalid custom values are ignored individually, which
    keeps the report useful when an admin clears one side of the range.
    """
    def day_value(value):
        try:
            value = str(value or "").strip()
            return datetime.date.fromisoformat(value).isoformat() if value else ""
        except (TypeError, ValueError):
            return ""

    from_day = day_value(date_from)
    to_day = day_value(date_to)
    if from_day or to_day:
        end_day = (datetime.date.fromisoformat(to_day) + datetime.timedelta(days=1)).isoformat() if to_day else ""
        return (from_day, from_day + "T00:00:00" if from_day else "", "custom", False,
                end_day + "T00:00:00" if end_day else "", to_day or _day())
    since_day, since_iso, days_out, is_all = _sales_cutoffs(days)
    return since_day, since_iso, days_out, is_all, "", _day()


def _sales_where(days=30, date_from=None, date_to=None, search=None, status=CONFIRMED):
    since_day, since_iso, days_out, is_all, end_iso, to_day = _sales_bounds(days, date_from, date_to)
    where = ["status=?"]
    params = [status]
    if since_iso:
        where.append("at >= ?")
        params.append(since_iso)
    if end_iso:
        where.append("at < ?")
        params.append(end_iso)
    q = str(search or "").strip().lower()[:80]
    if q:
        like = "%" + q + "%"
        where.append("(id LIKE ? OR customer_name LIKE ? OR email LIKE ? OR payload LIKE ?)")
        params.extend([like, like, like, like])
    return " AND ".join(where), tuple(params), since_day, days_out, to_day


def sales_report(days=30, date_from=None, date_to=None, search=None):
    """Confirmed-only sales totals. Pending orders never touch revenue/units.

    ``date_from`` and ``date_to`` are inclusive HTML date values. ``search``
    searches order/customer/product text before aggregation, so the cards and
    top-products list describe the same filtered set.
    """
    where, params, since_day, days_out, to_day = _sales_where(days, date_from, date_to, search, CONFIRMED)
    pend_where, pend_params, _pday, _pout, _pto = _sales_where(days, date_from, date_to, search, "pending")

    o = dict(one(
        f"SELECT COUNT(*) n, COALESCE(SUM(total),0) value, "
        f"COALESCE(SUM(items_count),0) units FROM orders WHERE {where}",
        params) or {})
    order_count = o.get("n", 0) or 0
    revenue = o.get("value", 0) or 0
    units = o.get("units", 0) or 0
    by_cur = [dict(r) for r in query(
        f"SELECT currency, COUNT(*) orders, COALESCE(SUM(total),0) value "
        f"FROM orders WHERE {where} GROUP BY currency", params)]
    avg_by_cur = [
        {"currency": r.get("currency"),
         "orders": r.get("orders", 0) or 0,
         "average": round((r.get("value", 0) or 0) / (r.get("orders", 0) or 1), 2)
         if (r.get("orders", 0) or 0) else 0}
        for r in by_cur
    ]
    pend = dict(one(
        f"SELECT COUNT(*) n FROM orders WHERE {pend_where}", pend_params) or {})
    pending_count = pend.get("n", 0) or 0

    # Top products from the same filtered confirmed-order payloads only.
    agg = {}
    rows = query(f"SELECT id, payload FROM orders WHERE {where}", params) or []
    for r in rows:
        oid = r["id"] if "id" in r.keys() else ""
        try:
            payload = json.loads(r["payload"] or "{}")
        except ValueError:
            continue
        items = payload.get("items") or []
        seen_in_order = set()
        for it in items:
            if not isinstance(it, dict):
                continue
            pid = str(it.get("id") or it.get("name") or "")
            if not pid:
                continue
            try:
                qty = int(it.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            try:
                price = int(it.get("price") or 0)
            except (TypeError, ValueError):
                price = 0
            if qty <= 0:
                continue
            row = agg.setdefault(pid, {"id": pid, "name": str(it.get("name") or pid),
                                       "units": 0, "revenue": 0, "orders": 0})
            row["units"] += qty
            row["revenue"] += qty * price
            if pid not in seen_in_order:
                seen_in_order.add(pid)
                row["orders"] += 1
    top = sorted(agg.values(), key=lambda d: (-d["units"], -d["revenue"]))[:10]

    return {
        "ok": True,
        "days": days_out,
        "from": since_day or "",
        "to": to_day,
        "orders": order_count,
        "units": units,
        "revenue": revenue,
        "revenueByCurrency": by_cur,
        "averageOrderValue": round(revenue / order_count, 2) if order_count else 0,
        "averageByCurrency": avg_by_cur,
        "topProducts": top,
        "pendingCount": pending_count,
        "pendingOrders": pending_count,
    }


def sales_csv(days=30, date_from=None, date_to=None, search=None):
    """One row per confirmed order. No BOM here — the HTTP layer adds it."""
    where, params, _since_day, _days_out, _to_day = _sales_where(
        days, date_from, date_to, search, CONFIRMED)
    rows = query(
        "SELECT id, at, customer_name, email, phone, city, zone, payment, "
        "total, currency, items_count, payload FROM orders "
        f"WHERE {where} ORDER BY at DESC", params) or []
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Order", "Date (UTC)", "Customer", "Email", "Phone", "City",
                "Zone", "Items", "Units", "Total", "Currency", "Payment"])
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except ValueError:
            payload = {}
        items = payload.get("items") or []
        bits = []
        units = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                qty = int(it.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            units += max(0, qty)
            name = str(it.get("name") or it.get("id") or "")
            color = str(it.get("color") or "")
            bits.append(f"{qty}x {name}" + (f" ({color})" if color else ""))
        w.writerow([r["id"], r["at"], r["customer_name"], r["email"], r["phone"],
                    r["city"], r["zone"], " · ".join(bits),
                    units or r["items_count"], r["total"], r["currency"],
                    r["payment"]])
    return buf.getvalue()


def prune(retention_days=None):
    """Drop raw analytics rows older than the retention window. Orders are kept.

    The lifetime counters (analytics_counters) are deliberately NOT pruned:
    they are the odometer the headline numbers fall back on.
    """
    days = int(retention_days or Config.ANALYTICS_RETENTION_DAYS)
    cutoff_day = _days_ago(max(1, days))
    cutoff_iso = _iso(_now() - datetime.timedelta(days=max(1, days)))
    execute("DELETE FROM page_views WHERE day < ?", (cutoff_day,))
    execute("DELETE FROM events WHERE day < ?", (cutoff_day,))
    execute("DELETE FROM search_queries WHERE day < ?", (cutoff_day,))
    execute("DELETE FROM presence WHERE at < ?",
            (_iso(_now() - datetime.timedelta(days=2)),))
    execute("DELETE FROM visitors WHERE last_at < ?", (cutoff_iso,))
    return True


# ------------------------------------------------------- durable insights
def _retention_cutoff_day():
    """Oldest day kept locally - the same window prune() enforces."""
    return _days_ago(max(1, int(Config.ANALYTICS_RETENTION_DAYS)))


try:
    import fcntl
except ImportError:                                 # non-POSIX: no-op lock
    fcntl = None

RESTORE_LOCK_PATH = (os.environ.get("ANALYTICS_RESTORE_LOCK")
                     or os.path.join(os.path.dirname(os.path.abspath(
                         Config.DB_PATH or "data/jaura.db")), ".analytics-restore.lock"))


@contextlib.contextmanager
def _restore_lock():
    """Serialise the boot restore across gunicorn workers.

    Every worker runs create_app() at boot. Two workers can both see an
    empty local window and both copy the mirrored rows back, duplicating
    every page view (the visitors metric dedupes, the view counts do not).
    An advisory POSIX lock on a file next to the SQLite database - the same
    trick catalog._catalog_lock uses for product saves - makes the second
    worker wait, re-check emptiness inside the lock, and find the first
    worker's rows already there. Falls back to no-op when fcntl is missing.
    """
    try:
        os.makedirs(os.path.dirname(RESTORE_LOCK_PATH) or ".", exist_ok=True)
        with open(RESTORE_LOCK_PATH, "a+") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError:
        yield                     # never block a boot because of locking


def _clean_restored_at(value, day, fallback):
    """Supabase returns timestamptz as ISO text with an offset
    ("2026-09-10T12:00:00+00:00"); local `at` is naive YYYY-MM-DDTHH:MM:SS.
    Normalise so restored rows sort and prune exactly like local ones."""
    s = str(value or "").strip().replace(" ", "T")
    if (len(s) >= 19 and s[4:5] == "-" and s[7:8] == "-" and s[10:11] == "T"
            and s[13:14] == ":" and s[16:17] == ":"):
        return s[:19]
    if len(str(day or "")) == 10:
        return f"{day}T12:00:00"
    return fallback


def _rebuild_visitors(cutoff):
    """Re-derive the visitors table from restored page views.

    Visitors carry no mirrored rows of their own (they are an aggregate of
    the same traffic), so a wipe loses them; without this the restored
    window would show page views but zero new visitors and no locations.
    """
    execute(
        "INSERT INTO visitors (vid, first_at, last_at, sessions, city, region, country, referrer, ua) "
        "SELECT vid, MIN(at), MAX(at), COUNT(DISTINCT sid), MAX(city), '', MAX(country), '', '' "
        "FROM page_views WHERE day >= ? AND vid IS NOT NULL AND vid != '' GROUP BY vid "
        "ON CONFLICT(vid) DO NOTHING",
        (cutoff,))


def restore_from_supabase(rows=None):
    """Copy the retention window back from Supabase after a disk wipe.

    A no-op unless the local window is empty: SQLite is a single file, so a
    wipe takes every analytics row and an intact disk needs nothing - which
    also makes the restore idempotent across boots. Pass `rows` to restore
    from an explicit list (tests); otherwise the window is read from the
    Supabase analytics_events table (fully paged, newest first - see
    supabase_store.load_analytics_events). The whole check-then-copy runs
    under a cross-process lock so two gunicorn workers booting together can
    never duplicate the window. Returns the number of rows restored.
    Never raises: a failed restore must not stop the boot or the sale.
    """
    try:
        with _restore_lock():
            return _restore_from_supabase_locked(rows)
    except Exception as exc:                    # never stop the boot
        print(f"[analytics] restore failed: {exc}")
        return 0


def _restore_from_supabase_locked(rows):
    cutoff = _retention_cutoff_day()
    local = one(
        "SELECT (SELECT COUNT(*) FROM page_views WHERE day >= ?) + "
        "(SELECT COUNT(*) FROM events WHERE day >= ?) n",
        (cutoff, cutoff))
    if local is None or (local["n"] or 0) > 0:
        return 0
    if rows is None:
        from supabase_store import load_analytics_events
        rows = load_analytics_events(cutoff)
    if not rows:
        return 0
    now_iso = _iso()
    pv_batch, ev_batch = [], []
    for r in rows:
        if not isinstance(r, dict):
            continue
        day = str(r.get("day") or "")[:10]
        if len(day) != 10 or day < cutoff:
            continue                        # outside the window (or junk)
        at = _clean_restored_at(r.get("at"), day, now_iso)
        kind = str(r.get("kind") or "")

        def _get(key, n):
            return str(r.get(key) or "")[:n]

        if kind == "page_view":
            pv_batch.append((_get("vid", 64), _get("sid", 48), _get("path", 200),
                             _get("page", 40), _get("ref", 300), _get("city", 80),
                             _get("country", 80), day, at))
        elif kind in ("view", "cart", "checkout_start", "purchase"):
            try:
                value = float(r.get("value") or 0)
            except (TypeError, ValueError):
                value = 0
            ev_batch.append((kind, _get("vid", 64), _get("sid", 48),
                             _get("product_id", 64), _get("product_name", 160),
                             _get("page", 40), _get("path", 200), value,
                             _get("currency", 3).upper(), _get("city", 80),
                             _get("country", 80), day, at))
    restored = 0
    # Batched inserts (one transaction per chunk) instead of one commit per
    # row: a busy 400-day window is tens of thousands of rows, and a
    # row-at-a-time restore could hold the boot for minutes.
    for i in range(0, len(pv_batch), 500):
        chunk = pv_batch[i:i + 500]
        execute_many(
            "INSERT INTO page_views (vid, sid, path, page, ref, city, country, day, at) "
            "VALUES (?,?,?,?,?,?,?,?,?)", chunk)
        restored += len(chunk)
    for i in range(0, len(ev_batch), 500):
        chunk = ev_batch[i:i + 500]
        execute_many(
            "INSERT INTO events (type, vid, sid, product_id, product_name, page, path, "
            "value, currency, city, country, day, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            chunk)
        restored += len(chunk)
    if restored:
        _rebuild_visitors(cutoff)
    return restored
