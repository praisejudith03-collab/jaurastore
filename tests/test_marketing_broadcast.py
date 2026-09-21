"""Broadcast campaigns reach every subscribed contact, immediately.

An admin who activates a new-product announcement or a promo must see it go
out to the whole active contact list on the spot. The list is walked in small
pages (never one enormous SELECT, which is what used to kill the background
workers), unsubscribed addresses are skipped, and one failing recipient is
logged and counted rather than aborting the send.

Run with:  python3 -m pytest tests/test_marketing_broadcast.py -q
"""
import os
import sys

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
def test_a_campaign_sends_to_every_active_contact_immediately(contacts, admin,
                                                              monkeypatch):
    contacts(8)
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
    assert body["sent"] == body["recipientCount"] >= 8
    assert body["failed"] == 0
    assert body["campaign"]["status"] == "sent"
    for i in range(8):
        assert f"shopper{i}@example.com" in sent_to
    stored = one("SELECT recipient_count, sent_count, status FROM "
                 "marketing_campaigns WHERE id=?", (body["campaign"]["id"],))
    assert stored["sent_count"] == body["sent"]
    assert stored["status"] == "sent"


def test_one_bad_recipient_does_not_abort_the_broadcast(contacts, admin,
                                                        monkeypatch):
    contacts(6)
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
    body = r.get_json()
    assert body["failed"] == 2
    assert body["sent"] == body["recipientCount"] - 2
    assert body["campaign"]["status"] == "partial"


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
