"""Client-IP geolocation, bot filtering, crash tracing, durable analytics
and the ten-per-page collapsible admin lists.

These cover four defects reported together:

  1. visitor locations were attributed to Finland / the server's own
     datacentre, because the app read the PROXY address and counted
     crawler traffic as customers;
  2. "background scheduler workers are not healthy" carried no detail -
     the exception, trace, memory and payload id were all lost;
  3. analytics and search history reset to zero on a deploy;
  4. admin lists grew without bound and were not readable one record at a
     time.

Run with:  python3 -m pytest tests/test_geo_crash_and_pagination.py -q
"""
import datetime
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

import pytest  # noqa: E402

import analytics as analytics_mod  # noqa: E402
import app as appmod  # noqa: E402
import observability  # noqa: E402
import scheduler  # noqa: E402
import security as sec  # noqa: E402
from config import Config  # noqa: E402
from db import execute, init_db, one, query  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# A real shopper in Lagos, seen through Cloudflare.
SHOPPER_IP = "197.210.53.20"
# Hetzner Helsinki - where the phantom "Finland visitors" came from.
FINLAND_DC_IP = "65.108.12.34"
BROWSER_UA = ("Mozilla/5.0 (Linux; Android 13; SM-A135F) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    for table in ("page_views", "events", "visitors", "presence",
                  "search_queries", "analytics_counters", "job_failures",
                  "rate_limits"):
        try:
            execute(f"DELETE FROM {table}")
        except Exception:
            pass
    observability.clear_recent()
    with app.test_client() as c:
        yield c


ADMIN_EMAIL = "jaurastore@gmail.com"


def _csrf(client):
    return client.get("/api/config").get_json()["csrf"]


def _login(client):
    """Sign the test client in as the shop owner and return its CSRF token."""
    execute("DELETE FROM rate_limits")
    r = client.post("/api/admin/login", json={"email": ADMIN_EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def _track(client, events, headers=None):
    head = {"X-CSRF-Token": _csrf(client), "User-Agent": BROWSER_UA}
    head.update(headers or {})
    return client.post("/api/track", json={"events": events}, headers=head)


# =====================================================================
# 1. client IP behind a CDN / proxy
# =====================================================================
def test_client_ip_prefers_the_cloudflare_header_over_the_proxy(app):
    """CF-Connecting-IP is written by the edge and names the shopper.

    remote_addr is the proxy; using it is what put visitors in the wrong
    country.
    """
    with app.test_request_context(
            "/", headers={"CF-Connecting-IP": SHOPPER_IP,
                          "X-Forwarded-For": f"{SHOPPER_IP}, 172.71.0.9"},
            environ_base={"REMOTE_ADDR": "10.0.0.7"}):
        assert sec.client_ip() == SHOPPER_IP


def test_client_ip_takes_the_leftmost_public_forwarded_address(app):
    """X-Forwarded-For is "client, proxy1, proxy2" - only the first counts."""
    with app.test_request_context(
            "/", headers={"X-Forwarded-For": f"{SHOPPER_IP}, 172.71.0.9, 10.0.0.3"},
            environ_base={"REMOTE_ADDR": "10.0.0.3"}):
        assert sec.client_ip() == SHOPPER_IP


def test_client_ip_skips_private_and_carrier_nat_hops(app):
    """Render's own mesh (100.64/10) and LAN hops are never the customer."""
    with app.test_request_context(
            "/", headers={"X-Forwarded-For": f"10.0.0.4, 100.64.1.2, {SHOPPER_IP}"},
            environ_base={"REMOTE_ADDR": "10.0.0.4"}):
        assert sec.client_ip() == SHOPPER_IP


def test_client_ip_handles_ports_ipv6_and_the_forwarded_header(app):
    with app.test_request_context(
            "/", headers={"X-Real-IP": f"{SHOPPER_IP}:44321"}):
        assert sec.client_ip() == SHOPPER_IP
    with app.test_request_context(
            "/", headers={"Forwarded": f'for="{SHOPPER_IP}";proto=https'}):
        assert sec.client_ip() == SHOPPER_IP
    with app.test_request_context(
            "/", headers={"CF-Connecting-IP": "2c0f:fb50:4002::a1"}):
        assert sec.client_ip() == "2c0f:fb50:4002::a1"


def test_client_ip_still_answers_in_local_development(app):
    """No proxy headers at all: fall back to remote_addr so rate limiting
    and the test suite keep a key."""
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert sec.client_ip() == "127.0.0.1"


def test_rate_limit_keys_on_the_real_client_not_the_shared_proxy(app):
    """Two shoppers behind one CDN must not share a rate-limit bucket."""
    with app.test_request_context(
            "/", headers={"CF-Connecting-IP": SHOPPER_IP},
            environ_base={"REMOTE_ADDR": "10.0.0.7"}):
        first = sec._client_key()
    with app.test_request_context(
            "/", headers={"CF-Connecting-IP": "102.89.1.5"},
            environ_base={"REMOTE_ADDR": "10.0.0.7"}):
        second = sec._client_key()
    assert first != second


def test_no_module_still_reads_the_raw_forwarded_header_for_an_ip():
    """Every call site goes through security.client_ip()."""
    offenders = []
    for name in ("api.py", "customers.py", "security.py", "analytics.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        for match in re.finditer(r'X-Forwarded-For"\s*,\s*""\)', source):
            window = source[match.start():match.start() + 260]
            if "remote_addr" in window and "def client_ip" not in window:
                offenders.append(name)
    assert not offenders, f"raw X-Forwarded-For parsing left in: {offenders}"


# =====================================================================
# 1b. bots never reach the analytics tables
# =====================================================================
@pytest.mark.parametrize("ua", [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "python-requests/2.31.0",
    "UptimeRobot/2.0; http://uptimerobot.com/",
    "jaura-keepalive/1.0",
    "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/120.0.0.0",
    "",
])
def test_known_crawlers_and_monitors_are_recognised(ua):
    assert sec.is_bot_ua(ua) is True


def test_a_real_phone_browser_is_not_a_bot():
    assert sec.is_bot_ua(BROWSER_UA) is False


def test_datacentre_ranges_are_recognised_and_shoppers_are_not():
    assert sec.is_bot_ip(FINLAND_DC_IP) is True       # Hetzner Helsinki
    assert sec.is_bot_ip("66.249.66.1") is True       # Googlebot
    assert sec.is_bot_ip(SHOPPER_IP) is False         # a Lagos shopper


def test_crawler_traffic_is_not_counted_as_a_visit(client):
    r = _track(client, [{"type": "visit", "path": "/", "page": "home", "sid": "s1"}],
               headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)",
                        "CF-Connecting-IP": "66.249.66.1"})
    assert r.status_code == 200
    assert r.get_json()["recorded"] == 0
    assert one("SELECT COUNT(*) n FROM page_views")["n"] == 0
    assert one("SELECT COUNT(*) n FROM presence")["n"] == 0


def test_a_datacentre_visitor_never_lands_on_the_location_map(client):
    """The exact defect: a Finnish datacentre IP claiming to be a visitor."""
    r = _track(client, [{"type": "visit", "path": "/", "page": "home", "sid": "s1"}],
               headers={"User-Agent": BROWSER_UA,
                        "CF-Connecting-IP": FINLAND_DC_IP,
                        "CF-IPCity": "Helsinki", "CF-IPCountry": "FI"})
    assert r.get_json()["recorded"] == 0
    locations = analytics_mod.report(days=7)["locations"]
    assert not any((loc.get("country") or "").upper() == "FI" for loc in locations)


def test_a_real_shopper_is_counted_with_their_own_city(client):
    r = _track(client, [{"type": "visit", "path": "/", "page": "home", "sid": "s1"}],
               headers={"User-Agent": BROWSER_UA,
                        "CF-Connecting-IP": SHOPPER_IP,
                        "CF-IPCity": "Lagos", "CF-IPCountry": "NG"})
    assert r.get_json()["recorded"] == 1
    row = one("SELECT city, country FROM page_views LIMIT 1")
    assert row["city"] == "Lagos" and row["country"] == "NG"


def test_edge_geo_without_a_client_address_is_discarded(app):
    """An internal hop carrying an edge header describes the datacentre,
    not a shopper - so no location is recorded at all."""
    with app.test_request_context(
            "/", headers={"CF-IPCity": "Helsinki", "CF-IPCountry": "FI"},
            environ_base={"REMOTE_ADDR": "10.0.0.7"}):
        assert analytics_mod._geo({}) == ("", "", "")


def test_cloudflare_unknown_country_codes_are_dropped(app):
    with app.test_request_context(
            "/", headers={"CF-Connecting-IP": SHOPPER_IP, "CF-IPCountry": "XX"}):
        assert analytics_mod._geo({}) == ("", "", "")


def test_the_browser_can_never_overrule_the_edge_location(app):
    """A spoofed body value must not move a shopper to another country."""
    with app.test_request_context(
            "/", headers={"CF-Connecting-IP": SHOPPER_IP,
                          "CF-IPCity": "Lagos", "CF-IPCountry": "NG"}):
        city, _region, country = analytics_mod._geo(
            {"city": "Helsinki", "country": "FI"})
    assert (city, country) == ("Lagos", "NG")


# =====================================================================
# 2. crash tracing for schedulers and notifications
# =====================================================================
def test_a_failure_record_carries_trace_memory_time_and_payload():
    observability.clear_recent()
    try:
        raise ValueError("provider exploded")
    except ValueError as exc:
        report = observability.record_failure(
            "notifications.test", exc, payload_id="JA-ABC123", attempt=2)
    assert report["job"] == "notifications.test"
    assert report["error_type"] == "ValueError"
    assert report["message"] == "provider exploded"
    assert report["payload_id"] == "JA-ABC123"
    assert report["attempt"] == 2
    assert "ValueError: provider exploded" in report["traceback"]
    assert "test_a_failure_record_carries" in report["traceback"]
    assert report["rss_mb"] > 0                       # process memory captured
    datetime.datetime.fromisoformat(report["at"])     # a real UTC timestamp
    assert report["host"]


def test_failures_are_stored_and_listed_newest_first(client):
    for i in range(3):
        try:
            raise RuntimeError(f"boom {i}")
        except RuntimeError as exc:
            observability.record_failure("scheduler.tick", exc, payload_id=str(i))
    rows = observability.recent_stored(limit=10)
    assert [r["payload_id"] for r in rows[:3]] == ["2", "1", "0"]
    assert rows[0]["job"] == "scheduler.tick"
    assert "RuntimeError: boom 2" in rows[0]["traceback"]
    assert observability.failure_count() >= 3


def test_the_guard_records_and_swallows_by_default():
    observability.clear_recent()
    with observability.guard("scheduler.guarded", payload_id="p1") as g:
        raise KeyError("missing")
    assert g.report["error_type"] == "KeyError"
    assert g.report["payload_id"] == "p1"
    with pytest.raises(KeyError):
        with observability.guard("scheduler.guarded", reraise=True):
            raise KeyError("again")


def test_a_failing_scheduler_step_is_traced_and_the_tick_survives():
    observability.clear_recent()

    def _explode():
        raise RuntimeError("catalog unreachable")

    assert scheduler._step("scheduler.remirror_strays", _explode) is None
    recent = observability.recent(1)
    assert recent[0]["job"] == "scheduler.remirror_strays"
    assert recent[0]["error_type"] == "RuntimeError"
    assert scheduler._health["lastErrorJob"] == "scheduler.remirror_strays"
    assert scheduler._health["lastError"] == "catalog unreachable"
    assert scheduler._health["lastErrorAt"]


def test_a_failing_reminder_send_is_traced_with_its_attempt(monkeypatch):
    import sys as _sys
    from types import SimpleNamespace
    observability.clear_recent()
    calls = []

    def send_due_reminders(limit):
        calls.append(limit)
        raise RuntimeError("smtp refused")

    monkeypatch.setitem(_sys.modules, "abandoned",
                        SimpleNamespace(send_due_reminders=send_due_reminders))
    monkeypatch.setattr(scheduler.time, "sleep", lambda _s: None)
    result = scheduler._abandoned_tick(attempts=2)
    assert result == {"sent": 0, "failed": 1}
    assert len(calls) == 2
    traced = [r for r in observability.recent(10)
              if r["job"] == "notifications.abandoned_cart"]
    assert len(traced) == 2
    assert {r["attempt"] for r in traced} == {1, 2}
    assert "RuntimeError: smtp refused" in traced[0]["traceback"]


def test_the_notification_pipeline_traces_the_failing_order(monkeypatch):
    import mailer
    observability.clear_recent()
    monkeypatch.setattr(mailer.threading, "Thread",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no thread")))

    def _boom(order):
        raise ConnectionError("resend unreachable")

    _boom.__name__ = "notify_new_order"
    mailer._fire(_boom, {"id": "JA-XYZ789"})
    traced = [r for r in observability.recent(10) if "JA-XYZ789" == r["payload_id"]]
    assert traced, observability.recent(10)
    assert any(r["error_type"] == "ConnectionError" for r in traced)


def test_a_shop_without_mail_keys_is_not_reported_as_a_crash():
    """"not configured" is a deliberate setup, not a broken pipeline."""
    import mailer
    observability.clear_recent()
    mailer._trace_soft_failure("notifications.test", "JA-1",
                               "not configured: MAIL_FROM, RESEND_API_KEY")
    assert observability.recent(5) == []


def test_health_reports_the_worker_state_and_recent_crashes():
    observability.clear_recent()
    try:
        raise RuntimeError("tick died")
    except RuntimeError as exc:
        scheduler._note_failure("scheduler.maintenance_tick", exc)
    snap = scheduler.health_snapshot(repair=False)
    for key in ("started", "maintenanceAlive", "remindersAlive", "lastError",
                "lastErrorJob", "lastErrorAt", "failures", "restarts",
                "recentFailures"):
        assert key in snap
    assert snap["recentFailures"][0]["job"] == "scheduler.maintenance_tick"
    assert snap["recentFailures"][0]["error"] == "RuntimeError"
    assert snap["recentFailures"][0]["rssMb"] > 0


def test_a_dead_worker_is_restarted_instead_of_failing_forever(monkeypatch):
    """The watchdog reported "workers are not healthy" permanently because
    nothing ever put a dead thread back."""
    started = []
    monkeypatch.setattr(scheduler, "_alive", lambda name: False)
    monkeypatch.setattr(scheduler, "_spawn",
                        lambda name, target, logger: started.append(name))
    monkeypatch.setattr(scheduler._started, "is_set", lambda: True)
    restarted = scheduler.ensure_alive()
    assert restarted == [scheduler.MAINTENANCE_THREAD, scheduler.REMINDERS_THREAD]
    assert started == restarted
    assert scheduler._health["restarts"] >= 2


def test_the_watchdog_message_now_names_the_actual_failure():
    from tools import catalog_watchdog
    detail = catalog_watchdog.describe_worker_failure({
        "started": True, "maintenanceAlive": False, "remindersAlive": True,
        "lastErrorJob": "scheduler.daily_backup",
        "lastError": "GitHub 401", "lastErrorAt": "2026-09-16T00:00:00Z",
        "failures": 4, "restarts": 1,
        "recentFailures": [{"job": "scheduler.daily_backup", "error": "HTTPError",
                            "message": "401 Unauthorized", "payloadId": "",
                            "rssMb": 91.2, "at": "2026-09-16T00:00:00"}],
    })
    assert "stopped worker(s): maintenance" in detail
    assert "scheduler.daily_backup" in detail
    assert "GitHub 401" in detail
    assert "HTTPError" in detail
    assert "4 failure(s)" in detail and "1 worker restart(s)" in detail


def test_the_crash_workflow_listens_for_the_dispatch_the_app_sends():
    workflow = (ROOT / ".github/workflows/crash-report.yml").read_text(encoding="utf-8")
    assert "repository_dispatch" in workflow
    assert "jaura-crash" in workflow
    assert "issues: write" in workflow
    source = (ROOT / "observability.py").read_text(encoding="utf-8")
    assert '"event_type": "jaura-crash"' in source
    assert "/dispatches" in source


def test_github_dispatch_is_rate_limited_per_job(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("FLASK_ENV", "production")
    observability.clear_recent()
    posted = []
    monkeypatch.setattr(observability, "_report_github",
                        lambda record: posted.append(record["job"]) or True)
    for _ in range(3):
        try:
            raise RuntimeError("repeat")
        except RuntimeError as exc:
            observability.record_failure("scheduler.tick", exc)
    assert len(posted) == 3          # the stub always runs; the window is below
    observability.clear_recent()
    assert observability._github_last == {}


def test_healthz_exposes_the_background_detail(client):
    body = client.get("/healthz").get_json()
    assert body["ok"] is True
    # Testing keeps the scheduler off, so background is null - but the key
    # is always present for the watchdog to read.
    assert "background" in body


# =====================================================================
# 3. durable analytics + search history
# =====================================================================
def test_page_views_advance_the_lifetime_counter(client):
    _track(client, [{"type": "visit", "path": "/", "page": "home", "sid": "s1"},
                    {"type": "view", "productId": "wix-001", "sid": "s1"}],
           headers={"CF-Connecting-IP": SHOPPER_IP})
    assert analytics_mod.counter("page_views_total") == 1
    assert analytics_mod.counter("events_total") == 2


def test_a_wiped_disk_cannot_reset_the_lifetime_counters(client):
    analytics_mod.bump_counter("page_views_total", 4200)
    rows = [{"name": n, "value": v}
            for n, v in analytics_mod.counters().items()]
    execute("DELETE FROM analytics_counters")          # the deploy wipe
    assert analytics_mod.counter("page_views_total") == 0
    assert analytics_mod.restore_counters(rows) >= 1
    assert analytics_mod.counter("page_views_total") == 4200


def test_restoring_counters_can_only_move_them_forward(client):
    analytics_mod.bump_counter("page_views_total", 900)
    analytics_mod.restore_counters([{"name": "page_views_total", "value": 5}])
    assert analytics_mod.counter("page_views_total") == 900


def test_pruning_keeps_the_counters_even_when_rows_go(client):
    _track(client, [{"type": "visit", "path": "/", "page": "home", "sid": "s1"}],
           headers={"CF-Connecting-IP": SHOPPER_IP})
    execute("UPDATE page_views SET day='2000-01-01', at='2000-01-01T00:00:00'")
    analytics_mod.prune(1)
    assert one("SELECT COUNT(*) n FROM page_views")["n"] == 0
    assert analytics_mod.counter("page_views_total") == 1


def test_the_report_carries_the_lifetime_totals(client):
    analytics_mod.bump_counter("page_views_total", 77)
    report = analytics_mod.report(days=7)
    assert report["totals"]["lifetimePageViews"] == 77
    assert report["lifetime"]["page_views_total"] == 77


def test_a_customer_search_is_stored_permanently(client):
    r = client.post("/api/search-log",
                    json={"q": "  Shea  Butter ", "results": 0, "sid": "s1"},
                    headers={"X-CSRF-Token": _csrf(client),
                             "User-Agent": BROWSER_UA,
                             "CF-Connecting-IP": SHOPPER_IP})
    assert r.status_code == 200 and r.get_json()["recorded"] is True
    row = one("SELECT q, q_norm, results FROM search_queries")
    assert row["q"] == "Shea  Butter" and row["q_norm"] == "shea butter"
    assert row["results"] == 0
    assert analytics_mod.counter("searches_total") == 1


def test_search_reporting_groups_terms_and_flags_the_misses(client):
    for q, hits in (("body oil", 3), ("Body Oil", 3), ("wig glue", 0)):
        analytics_mod.record_search(q, results=hits, vid="v1")
    report = analytics_mod.search_report(days=30)
    assert report["searches"] == 3
    assert report["withoutResults"] == 1
    top = {row["term"]: row for row in report["top"]}
    assert top["body oil"]["searches"] == 2
    assert top["wig glue"]["empty"] == 1
    assert report["recent"][0]["q"] == "wig glue"


def test_a_crawler_search_is_not_recorded(client):
    r = client.post("/api/search-log", json={"q": "bag"},
                    headers={"X-CSRF-Token": _csrf(client),
                             "User-Agent": "python-requests/2.31.0"})
    assert r.status_code == 200 and r.get_json()["recorded"] is False
    assert one("SELECT COUNT(*) n FROM search_queries")["n"] == 0


def test_search_history_is_restored_after_a_wiped_disk(client):
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    rows = [{"vid": "v1", "sid": "s1", "q": "hair oil", "q_norm": "hair oil",
             "results": 2, "category": "", "city": "Lagos", "country": "NG",
             "day": today, "at": f"{today}T10:00:00+00:00"}]
    execute("DELETE FROM search_queries")
    assert analytics_mod.restore_searches_from_supabase(rows) == 1
    restored = one("SELECT q, at FROM search_queries")
    assert restored["q"] == "hair oil"
    assert restored["at"] == f"{today}T10:00:00"       # normalised, sortable
    # A second boot with an intact table must not duplicate anything.
    assert analytics_mod.restore_searches_from_supabase(rows) == 0
    assert one("SELECT COUNT(*) n FROM search_queries")["n"] == 1


def test_the_search_and_crash_tables_ship_in_both_schemas():
    sqlite_schema = (ROOT / "db.py").read_text(encoding="utf-8")
    supabase_schema = (ROOT / "supabase_schema.sql").read_text(encoding="utf-8")
    for table in ("search_queries", "analytics_counters", "job_failures"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sqlite_schema
        assert f"create table if not exists {table}" in supabase_schema


def test_the_boot_restores_counters_and_searches():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "restore_searches_from_supabase()" in source
    assert "restore_counters()" in source


def test_admin_search_endpoint_requires_a_signed_in_admin(client):
    assert client.get("/api/admin/searches").status_code in (401, 403)
    assert client.get("/api/admin/job-failures").status_code in (401, 403)


# =====================================================================
# 4. admin pagination + collapsible rows
# =====================================================================
ADMIN_JS = (ROOT / "js/admin.js").read_text(encoding="utf-8")


def test_every_scaling_admin_list_pages_ten_at_a_time():
    assert "const ADMIN_PAGE_SIZE = 10;" in ADMIN_JS
    assert "const ORDER_PAGE = 10;" in ADMIN_JS


def test_the_shared_pager_offers_previous_and_next():
    assert "function pagerHTML(" in ADMIN_JS
    assert "‹ Previous" in ADMIN_JS and "Next ›" in ADMIN_JS
    for key in ("order", "proof", "crash"):
        assert f'pagerHTML("{key}"' in ADMIN_JS
        assert f'bindPager(pager, "{key}"' in ADMIN_JS


def test_receipts_and_crash_reports_render_as_collapsible_cards():
    for fn in ("function proofCardHTML(", "function crashCardHTML("):
        body = ADMIN_JS[ADMIN_JS.index(fn):]
        body = body[:body.index("\n}\n")]
        assert "<details" in body and "<summary" in body
        assert "adx-order-row" in body


def test_the_toggle_arrow_is_styled_on_every_collapsible_row():
    css = (ROOT / "css/style.css").read_text(encoding="utf-8")
    assert ".adx-order-row::before" in css
    assert ".adx-order[open] > .adx-order-row::before" in css


def test_the_orders_panel_mounts_both_new_pagers():
    assert 'id="proofs-pager"' in ADMIN_JS
    assert 'id="crash-pager"' in ADMIN_JS
    assert 'id="crash-box"' in ADMIN_JS


def test_a_deleted_last_record_falls_back_to_the_previous_page():
    body = ADMIN_JS[ADMIN_JS.index("async function fillProofs()"):]
    body = body[:body.index("\n}\n")]
    assert "if (proofPage > proofPages)" in body


def _seed_proofs(n):
    execute("DELETE FROM payment_proofs")
    for i in range(n):
        execute(
            "INSERT INTO payment_proofs (order_id, name, email, method, amount, "
            "file_url, file_name, at) VALUES (?,?,?,?,?,?,?,?)",
            (f"JA-P{i:03d}", f"Customer {i}", f"c{i}@example.com", "transfer",
             "10000", "", "", f"2026-09-{(i % 28) + 1:02d}T10:00:00"))


def test_the_receipts_endpoint_returns_ten_per_page(client, app):
    _seed_proofs(23)
    _login(client)
    r = client.get("/api/admin/payment-proofs?page=1&perPage=10")
    body = r.get_json()
    assert r.status_code == 200
    assert body["count"] == 10 and body["total"] == 23 and body["pages"] == 3
    assert body["page"] == 1 and body["perPage"] == 10
    first_page = [p["order_id"] for p in body["proofs"]]

    body2 = client.get("/api/admin/payment-proofs?page=2&perPage=10").get_json()
    second_page = [p["order_id"] for p in body2["proofs"]]
    assert body2["count"] == 10 and body2["page"] == 2
    assert not set(first_page) & set(second_page)        # no repeats

    last = client.get("/api/admin/payment-proofs?page=3&perPage=10").get_json()
    assert last["count"] == 3                            # the remainder
    # Every seeded receipt is reachable across the pages: nothing is lost.
    assert len(set(first_page + second_page +
                   [p["order_id"] for p in last["proofs"]])) == 23


def test_an_out_of_range_page_clamps_instead_of_erroring(client):
    _seed_proofs(4)
    _login(client)
    body = client.get("/api/admin/payment-proofs?page=99&perPage=10").get_json()
    assert body["page"] == 1 and body["count"] == 4


def test_receipts_are_kept_until_an_admin_deletes_one(client):
    """No expiry, no trimming: the archive only shrinks on an explicit
    delete, so an old receipt stays reachable on its page."""
    _seed_proofs(12)
    token = _login(client)
    before = client.get("/api/admin/payment-proofs?page=1&perPage=10").get_json()
    assert before["total"] == 12
    row = one("SELECT id FROM payment_proofs ORDER BY id LIMIT 1")
    r = client.delete(f"/api/admin/payment-proofs/{row['id']}",
                      headers={"X-CSRF-Token": token})
    assert r.status_code == 200
    after = client.get("/api/admin/payment-proofs?page=1&perPage=10").get_json()
    assert after["total"] == 11
    assert one("SELECT COUNT(*) n FROM payment_proofs")["n"] == 11


def test_the_crash_report_endpoint_pages_too(client):
    execute("DELETE FROM job_failures")
    for i in range(14):
        try:
            raise RuntimeError(f"failure {i}")
        except RuntimeError as exc:
            observability.record_failure("scheduler.tick", exc, payload_id=str(i))
    _login(client)
    body = client.get("/api/admin/job-failures?page=1&limit=10").get_json()
    assert body["ok"] is True
    assert body["count"] == 10 and body["total"] == 14 and body["pages"] == 2
    assert body["failures"][0]["payload_id"] == "13"      # newest first
    assert "RuntimeError" in body["failures"][0]["error_type"]
    assert body["failures"][0]["traceback"]
    page2 = client.get("/api/admin/job-failures?page=2&limit=10").get_json()
    assert page2["count"] == 4
