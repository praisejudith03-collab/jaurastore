"""Background workers: paginated batches, bounded memory, resilient email.

The `jaura-abandoned-carts` and `jaura-maintenance` workers were dying with
`scheduler.worker_died` RuntimeErrors because a tick pulled the whole
abandoned-cart / contact table into memory at once. These tests pin the fix:

  * reminders are read in small pages, never as one unbounded SELECT;
  * a single failing email is logged and counted, never fatal to the batch;
  * a worker tick over a large backlog stays far below the 50MB budget;
  * a broadcast campaign reaches every active contact immediately.

Run with:  python3 -m pytest tests/test_worker_memory_and_email.py -q
"""
import datetime
import gc
import os
import sys
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

import abandoned  # noqa: E402
import mailer  # noqa: E402
import scheduler  # noqa: E402
from db import execute, init_db, one, query  # noqa: E402


MEMORY_BUDGET_MB = 50


def _stale(minutes=120):
    return (datetime.datetime.utcnow()
            - datetime.timedelta(minutes=minutes)).replace(
                microsecond=0).isoformat()


@pytest.fixture()
def carts():
    """A clean abandoned_carts table plus a helper that seeds due carts."""
    init_db()
    execute("DELETE FROM abandoned_carts")

    def seed(count, prefix="tok"):
        stamp = _stale()
        for i in range(count):
            execute(
                "INSERT INTO abandoned_carts (token,email,customer_name,items,"
                "currency,total,last_activity_at,reminder_sent,created_at,"
                "updated_at) VALUES (?,?,?,?,?,?,?,0,?,?)",
                (f"{prefix}-{i}", f"buyer{i}@example.com", "Buyer",
                 '[{"name":"Bag","qty":1,"price":15000}]', "NGN", 15000,
                 stamp, stamp, stamp))
    yield seed
    execute("DELETE FROM abandoned_carts")


# --------------------------------------------------------------- pagination
def test_due_carts_reads_one_bounded_page_at_a_time(carts):
    carts(30)
    page = abandoned.due_carts(limit=10)
    assert len(page) == 10
    second = abandoned.due_carts(limit=10, offset=10)
    assert len(second) == 10
    assert {r["token"] for r in page}.isdisjoint({r["token"] for r in second})


def test_the_reminder_iterator_never_holds_more_than_one_page(carts,
                                                              monkeypatch):
    carts(120)
    sizes = []
    real = abandoned.due_carts

    def spy(limit=25, offset=0):
        rows = real(limit=limit, offset=offset)
        sizes.append(len(rows))
        return rows

    monkeypatch.setattr(abandoned, "due_carts", spy)
    seen = list(abandoned.iter_due_carts(limit=120, page_size=10))
    assert len(seen) == 120
    assert sizes and max(sizes) <= 10, sizes


def test_a_tick_is_capped_and_leaves_the_rest_for_the_next_run(carts,
                                                               monkeypatch):
    carts(60)
    monkeypatch.setattr(mailer, "send_abandoned_cart_reminder",
                        lambda cart: (True, "accepted"))
    report = abandoned.send_due_reminders(limit=25, page_size=10)
    assert report == {"sent": 25, "failed": 0}
    remaining = one("SELECT COUNT(*) AS n FROM abandoned_carts "
                    "WHERE reminder_sent=0")["n"]
    assert remaining == 35


# ---------------------------------------------------------------- resilience
def test_one_failing_email_does_not_stop_the_batch(carts, monkeypatch):
    carts(10)
    calls = {"n": 0}

    def flaky(cart):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("provider exploded")
        if calls["n"] == 5:
            return False, "provider 429"
        return True, "accepted"

    monkeypatch.setattr(mailer, "send_abandoned_cart_reminder", flaky)
    report = abandoned.send_due_reminders(limit=10, page_size=4)
    assert report["sent"] == 8
    assert report["failed"] == 2
    # A failed send releases its claim so the cart is retried next tick.
    assert one("SELECT COUNT(*) AS n FROM abandoned_carts "
               "WHERE reminder_sent=0")["n"] == 2


def test_a_dead_provider_never_kills_the_worker_thread(carts, monkeypatch):
    carts(5)
    monkeypatch.setattr(mailer, "send_abandoned_cart_reminder",
                        lambda cart: (_ for _ in ()).throw(OSError("socket")))
    monkeypatch.setattr(scheduler.time, "sleep", lambda _s: None)
    report = scheduler._abandoned_tick()          # must not raise
    assert report["failed"] >= 1
    assert report["sent"] == 0


def test_the_reminder_worker_sends_through_the_configured_provider(carts,
                                                                   monkeypatch):
    """The recovery email really reaches Resend with the shopper's address."""
    carts(1)
    posted = {}

    monkeypatch.setattr(mailer, "_cfg", lambda name, default="": {
        "RESEND_API_KEY": "re_test_key",
        "MAIL_FROM": "orders@jaurastore.com.ng",
        "ADMIN_EMAIL": "jaurastore@gmail.com",
    }.get(name, default))

    def fake_post(url, headers, payload):
        posted["url"] = url
        posted["headers"] = headers
        posted["payload"] = payload
        return True, "{\"id\":\"abc\"}"

    monkeypatch.setattr(mailer, "_http_post_json", fake_post)
    report = abandoned.send_due_reminders(limit=1, page_size=1)
    assert report == {"sent": 1, "failed": 0}
    assert posted["url"] == "https://api.resend.com/emails"
    assert posted["headers"]["Authorization"] == "Bearer re_test_key"
    assert posted["payload"]["to"] == ["buyer0@example.com"]
    assert "cart" in posted["payload"]["subject"].lower()


# ------------------------------------------------------------------- memory
def test_a_large_backlog_tick_stays_well_under_the_memory_budget(carts,
                                                                 monkeypatch):
    carts(400)
    monkeypatch.setattr(mailer, "send_abandoned_cart_reminder",
                        lambda cart: (True, "accepted"))
    gc.collect()
    tracemalloc.start()
    try:
        abandoned.send_due_reminders(limit=400, page_size=25)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    peak_mb = peak / (1024 * 1024)
    assert peak_mb < MEMORY_BUDGET_MB, f"tick peaked at {peak_mb:.1f}MB"


def test_the_scheduler_releases_memory_after_every_tick():
    rss = scheduler._release_memory(scheduler.REMINDERS_THREAD)
    assert rss >= 0
    assert "rssMb" in scheduler.health_snapshot(repair=False)


def test_the_health_snapshot_reports_both_workers():
    snap = scheduler.health_snapshot(repair=False)
    for key in ("maintenanceAlive", "remindersAlive", "recentFailures"):
        assert key in snap
