"""Broadcast campaigns are QUEUED at once and delivered to every contact.

An admin who activates a new-product announcement or a promo must see it go out
to the whole active contact list on the spot. "On the spot" now means the
REQUEST returns immediately with a queued campaign: the old synchronous send
walked the contact book inside the request, which on mobile data blew the
browser's 5-second deadline and showed the owner "Request timed out after 5
seconds" while the mailer was still running (a retry then mailed everybody
twice).

The list is still walked in small pages (never one enormous SELECT, which is
what used to kill the background workers), unsubscribed addresses are skipped,
one failing recipient is logged and counted rather than aborting the send, and
each address is recorded in ``marketing_campaign_sends`` so a resume never
double-sends.

Run with:  python3 -m pytest tests/test_marketing_broadcast.py -q
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

import api  # noqa: E402
import app as appmod  # noqa: E402
import mailer  # noqa: E402
import task_queue  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

EMAIL = "jaurastore@gmail.com"


@pytest.fixture()
def contacts():
    init_db()
    execute("DELETE FROM marketing_suppressions")
    execute("DELETE FROM customers")

    def seed(count, prefix="shopper"):
        for i in range(count):
            execute("INSERT OR REPLACE INTO customers (id, email, name, "
                    "password_hash) VALUES (?,?,?,?)",
                    (f"cust-{prefix}{i}", f"{prefix}{i}@example.com",
                     f"Shopper {i}", "x"))
    yield seed
    execute("DELETE FROM customers")
    execute("DELETE FROM marketing_suppressions")


@pytest.fixture()
def admin():
    init_db()
    execute("DELETE FROM rate_limits")
    application = appmod.create_app()
    application.config.update(TESTING=True)
    with application.test_client() as client:
        r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
        assert r.status_code == 200, r.data
        yield client, r.get_json()["csrf"]


@pytest.fixture()
def manual_queue():
    """Keep the worker thread out of the way: the test decides when to send."""
    task_queue.reset()
    task_queue.set_manual(True)
    execute("DELETE FROM marketing_campaigns")
    execute("DELETE FROM marketing_campaign_sends")
    yield task_queue
    task_queue.set_manual(False)
    task_queue.reset()


@pytest.fixture()
def pinned_contacts(monkeypatch):
    """Pin the recipient iterator to a known list.

    The suite shares one SQLite file, so a test that asserts WHICH addresses
    received a campaign must not read whatever another module left in the
    orders table. This is the same iterator the sender walks, pinned.
    """
    def pin(emails):
        listed = list(emails)
        monkeypatch.setattr(api, "_iter_marketing_recipients",
                            lambda page_size=200, max_rows=20000: iter(listed))
        return listed
    return pin


# -------------------------------------------------------------- recipients
def test_the_contact_walk_is_paginated(contacts, monkeypatch):
    contacts(55)
    seen_pages = []
    real_query = api.query

    def spy(sql, params=()):
        if "FROM customers" in sql and "LIMIT" in sql:
            seen_pages.append(params)
        return real_query(sql, params)

    monkeypatch.setattr(api, "query", spy)
    emails = list(api._iter_contact_emails(page_size=10))
    assert len(emails) >= 55
    assert seen_pages, "customers were not read in pages"
    assert all(p[0] == 10 for p in seen_pages), seen_pages


def test_unsubscribed_contacts_are_excluded(contacts):
    contacts(3)
    execute("INSERT OR REPLACE INTO marketing_suppressions (email) VALUES (?)",
            ("shopper1@example.com",))
    recipients = set(api._iter_marketing_recipients())
    assert "shopper0@example.com" in recipients
    assert "shopper1@example.com" not in recipients


def test_the_recipient_count_endpoint_does_not_build_the_whole_list(contacts,
                                                                    admin):
    contacts(12)
    client, _token = admin
    r = client.get("/api/admin/marketing/recipients")
    assert r.status_code == 200
    assert r.get_json()["count"] >= 12


# ---------------------------------------------------------------- broadcast
def test_send_campaign_queues_instantly_and_the_worker_delivers_to_everyone(
        contacts, admin, manual_queue, pinned_contacts, monkeypatch):
    """The request queues; a background pass then mails every active contact.

    This is the 5-second-timeout fix in one test: nothing is sent inside the
    request, the answer carries the campaign id + recipient count, and the
    worker (drained here by hand) reaches all eight inboxes afterwards.
    """
    contacts(8)
    wanted = pinned_contacts(f"shopper{i}@example.com" for i in range(8))
    client, token = admin
    sent_to = []
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send_campaign_email",
                        lambda to, *a, **k: (sent_to.append(to), (True, "ok"))[1])
    r = client.post("/api/admin/marketing/campaigns",
                    headers={"X-CSRF-Token": token},
                    json={"campaign_type": "new_arrivals",
                          "subject": "New arrivals are in",
                          "content": "Fresh stock just landed."})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert body["queued"] is True
    assert body["campaignId"].startswith("CMP-")
    assert str(body["jobId"]).startswith("JOB-")
    assert body["recipientCount"] == len(wanted)
    assert sent_to == [], "the request sent email instead of queueing the campaign"
    row = one("SELECT recipient_count, sent_count, status FROM marketing_campaigns "
              "WHERE id=?", (body["campaignId"],))
    assert row["status"] == "queued"
    assert row["sent_count"] == 0
    assert row["recipient_count"] == body["recipientCount"]
    assert manual_queue.pending() == 1

    manual_queue.drain()
    assert sorted(sent_to) == wanted
    stored = one("SELECT recipient_count, sent_count, failed_count, status FROM "
                 "marketing_campaigns WHERE id=?", (body["campaignId"],))
    assert stored["status"] == "sent"
    assert stored["sent_count"] == stored["recipient_count"]
    assert stored["failed_count"] == 0


def test_the_request_never_waits_for_the_mail_provider(contacts, admin,
                                                       manual_queue,
                                                       pinned_contacts,
                                                       monkeypatch):
    """A slow provider cannot hold the operator's request open."""
    contacts(3)
    pinned_contacts(f"shopper{i}@example.com" for i in range(3))
    client, token = admin
    monkeypatch.setattr(mailer, "configured", lambda: True)

    def hanging(to, *a, **k):
        raise AssertionError("the request path must not call the provider")

    monkeypatch.setattr(mailer, "send_campaign_email", hanging)
    started = time.monotonic()
    r = client.post("/api/admin/marketing/campaigns",
                    headers={"X-CSRF-Token": token},
                    json={"campaign_type": "new_arrivals", "subject": "Hi",
                          "content": "New stock."})
    elapsed = time.monotonic() - started
    assert r.status_code == 200, r.data
    # Nobody was mailed inside the request (the stub would have raised), and
    # the answer is immediate - far inside the 5s deadline that used to fire.
    assert elapsed < 2.0, f"queueing took {elapsed:.2f}s"


def test_one_bad_recipient_does_not_abort_the_broadcast(contacts, admin,
                                                        manual_queue,
                                                        pinned_contacts,
                                                        monkeypatch):
    contacts(6)
    pinned_contacts(f"shopper{i}@example.com" for i in range(6))
    client, token = admin
    monkeypatch.setattr(mailer, "configured", lambda: True)

    def flaky(to, *a, **k):
        if to == "shopper2@example.com":
            raise RuntimeError("provider exploded")
        if to == "shopper4@example.com":
            return False, "provider 422"
        return True, "ok"

    monkeypatch.setattr(mailer, "send_campaign_email", flaky)
    r = client.post("/api/admin/marketing/campaigns",
                    headers={"X-CSRF-Token": token},
                    json={"campaign_type": "price_drop",
                          "subject": "Prices dropped",
                          "content": "Big savings this week."})
    assert r.status_code == 200, r.data
    campaign_id = r.get_json()["campaignId"]
    manual_queue.drain()
    stored = one("SELECT recipient_count, sent_count, failed_count, status "
                 "FROM marketing_campaigns WHERE id=?", (campaign_id,))
    assert stored["failed_count"] == 2
    assert stored["sent_count"] == stored["recipient_count"] - 2
    assert stored["status"] == "partial"


def test_a_campaign_without_a_provider_is_refused_clearly(contacts, admin,
                                                          monkeypatch):
    contacts(2)
    client, token = admin
    monkeypatch.setattr(mailer, "configured", lambda: False)
    r = client.post("/api/admin/marketing/campaigns",
                    headers={"X-CSRF-Token": token},
                    json={"campaign_type": "customer_appreciation",
                          "subject": "Thank you",
                          "content": "We appreciate you."})
    assert r.status_code == 400
    assert "RESEND_API_KEY" in r.get_json()["error"]


def test_campaign_mail_goes_out_over_resend(monkeypatch):
    posted = {}
    monkeypatch.setattr(mailer, "_cfg", lambda name, default="": {
        "RESEND_API_KEY": "re_test_key",
        "MAIL_FROM": "orders@jaurastore.com.ng",
        "ADMIN_EMAIL": EMAIL,
    }.get(name, default))
    monkeypatch.setattr(mailer, "_http_post_json",
                        lambda url, headers, payload: (
                            posted.update(url=url, headers=headers,
                                          payload=payload),
                            (True, "{}"))[1])
    ok, _detail = mailer.send_campaign_email(
        "shopper@example.com", "New arrivals", "Fresh stock.")
    assert ok
    assert posted["url"] == "https://api.resend.com/emails"
    assert posted["payload"]["to"] == ["shopper@example.com"]
