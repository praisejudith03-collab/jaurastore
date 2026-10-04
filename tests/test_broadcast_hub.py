"""The admin Email Broadcast Hub: /admin/marketing/broadcast.

The owner asked for one place where they can (a) see the customer addresses
collected from order history and registered accounts, (b) compose and PREVIEW a
promotional email (new arrivals, promos, coupon announcements), and (c) send it
in background batches without the page - or the server's memory - stalling.

What is pinned here:

* the audience endpoint reports the same number the sender will use, with the
  two sources split out and unsubscribed addresses excluded;
* the preview renders the real email and sends nothing at all;
* queueing creates the campaign row, hands the work to the task queue and
  answers immediately - the request never sends an email;
* the dispatcher sends at most a fixed chunk per pass, re-queues itself while
  addresses remain, logs each address so a resume never double-sends, counts
  failures without aborting, and stops when the operator cancels;
* every broadcast kind maps onto a stored ``campaign_type`` the database
  accepts (the Supabase mirror would reject anything else).

Run with:  python3 -m pytest tests/test_broadcast_hub.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

from campaign_types import (BROADCAST_KINDS, CAMPAIGN_TYPES,  # noqa: E402
                            broadcast_kind_options,
                            campaign_type_for_broadcast)
from config import Config  # noqa: E402
from db import execute, init_db, one, query  # noqa: E402

import app as appmod  # noqa: E402
import api  # noqa: E402
import mailer  # noqa: E402
import task_queue  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"


@pytest.fixture()
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    init_db()
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_contacts():
    """A known contact list: two orderers, one account holder, one opt-out."""
    init_db()
    execute("DELETE FROM marketing_campaign_sends")
    execute("DELETE FROM marketing_campaigns")
    execute("DELETE FROM marketing_suppressions")
    execute("DELETE FROM customers")
    execute("DELETE FROM rate_limits")
    for email in ("buyer1@example.com", "buyer2@example.com",
                  "optout@example.com", "leaver@example.com"):
        execute("INSERT OR REPLACE INTO orders (id,payload,email,customer_name,total,"
                "currency,status,at) VALUES (?,?,?,?,?,?,?,datetime('now'))",
                ("JA-HUB-" + email.split("@")[0].upper(), "{}", email, "Buyer",
                 5000, "NGN", "confirmed"))
    execute("INSERT OR REPLACE INTO customers (id,email,password_hash,name,created_at,updated_at) "
            "VALUES (?,?,?,?,datetime('now'),datetime('now'))",
            ("c1", "account@example.com", "x", "Account"))
    execute("INSERT INTO marketing_suppressions (email) VALUES (?)",
            ("optout@example.com",))
    yield
    execute("DELETE FROM marketing_campaign_sends")
    execute("DELETE FROM marketing_campaigns")
    execute("DELETE FROM marketing_suppressions")
    execute("DELETE FROM customers")


@pytest.fixture()
def manual_queue():
    task_queue.reset()
    task_queue.set_manual(True)
    yield task_queue
    task_queue.set_manual(False)
    task_queue.reset()


@pytest.fixture()
def known_list(monkeypatch):
    """A deterministic contact list.

    The suite shares one SQLite file with every other module, so a test that
    asserts exact recipient numbers must not read whatever another module left
    in the orders table: it pins the same iterator the sender walks.
    """
    listed = ["buyer1@example.com", "buyer2@example.com",
              "account@example.com", "leaver@example.com"]
    monkeypatch.setattr(api, "_iter_marketing_recipients",
                        lambda page_size=200, max_rows=20000: iter(listed))
    return listed


@pytest.fixture()
def sent(monkeypatch):
    """Capture every outbound campaign email; nothing leaves the machine."""
    box = []
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(Config, "MAIL_FROM", "shop@example.com")

    def _send(to, subject, content, products=None):
        if str(to) == "bad@example.com":
            return False, "550 mailbox unavailable"
        box.append({"to": str(to), "subject": subject, "content": content,
                    "products": list(products or [])})
        return True, "sent"

    monkeypatch.setattr(mailer, "send_campaign_email", _send)
    return box


def _login(client):
    r = client.post("/api/admin/login",
                    json={"email": EMAIL, "password": PW, "recaptcha": ""})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


# ------------------------------------------------------------------- audience
def test_the_audience_endpoint_reports_two_sources_and_excludes_opt_outs(client):
    """One contact book, two sources, opt-outs never in the list.

    The suite shares one SQLite file with other modules, so this asserts the
    facts that must hold whatever else is in the table, plus the addresses this
    module seeded: orderers, an account holder, and one opt-out.
    """
    tok = _login(client)
    assert tok
    body = client.get("/api/admin/marketing/broadcast/audience").get_json()
    assert body["ok"] is True
    assert body["total"] == (body["fromOrders"] + body["fromAccounts"]
                             + body["fromRemoteOnly"])
    assert body["total"] >= 4
    assert body["suppressed"] >= 1
    accounts, orderers = api._broadcast_local_sets()
    assert {"buyer1@example.com", "buyer2@example.com", "leaver@example.com"} <= orderers
    assert "account@example.com" in accounts
    assert "optout@example.com" not in body["sample"]
    assert body["kinds"] and {k["kind"] for k in body["kinds"]} == set(BROADCAST_KINDS)
    assert body["ceiling"] >= 1


def test_the_source_split_is_exact_for_a_known_contact_list(client, monkeypatch):
    """The split is counted over the recipients that will actually receive it."""
    listed = ["fromorder1@example.com", "fromorder2@example.com",
              "fromaccount@example.com", "remoteonly@example.com"]
    monkeypatch.setattr(api, "_iter_marketing_recipients",
                        lambda page_size=200, max_rows=20000: iter(listed))
    monkeypatch.setattr(api, "_broadcast_local_sets",
                        lambda: ({"fromaccount@example.com"},
                                 {"fromorder1@example.com", "fromorder2@example.com"}))
    monkeypatch.setattr(api, "_marketing_suppressed_emails",
                        lambda: {"ignored@example.com"})
    tok = _login(client)
    assert tok
    body = client.get("/api/admin/marketing/broadcast/audience").get_json()
    assert body["total"] == 4
    assert body["fromOrders"] == 2
    assert body["fromAccounts"] == 1
    assert body["fromRemoteOnly"] == 1
    assert body["suppressed"] == 1
    assert body["sample"] == listed


def test_the_audience_endpoint_needs_an_admin(client):
    r = client.get("/api/admin/marketing/broadcast/audience")
    assert r.status_code in (401, 403, 302)


# -------------------------------------------------------------------- preview
def test_the_preview_renders_the_email_and_sends_nothing(client, sent, known_list):
    tok = _login(client)
    r = client.post("/api/admin/marketing/broadcast/preview",
                    json={"subject": "New arrivals", "content": "Fresh pieces\nfor you."},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True and body["dryRun"] is True
    assert "Fresh pieces" in body["html"]
    assert "New arrivals" in body["html"]
    assert body["recipients"] == len(known_list)
    assert sent == [], "the preview sent an email"


def test_the_preview_needs_a_subject_and_a_message(client):
    tok = _login(client)
    r = client.post("/api/admin/marketing/broadcast/preview",
                    json={"subject": "", "content": "x"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 400
    r = client.post("/api/admin/marketing/broadcast/preview",
                    json={"subject": "x", "content": "   "},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 400


def test_the_preview_requires_csrf(client):
    _login(client)
    r = client.post("/api/admin/marketing/broadcast/preview",
                    json={"subject": "x", "content": "y"})
    assert r.status_code in (400, 403)


# --------------------------------------------------------------- queue it up
def test_queueing_creates_the_row_and_never_sends_in_the_request(
        client, sent, manual_queue, known_list):
    tok = _login(client)
    r = client.post("/api/admin/marketing/broadcast",
                    json={"kind": "new_arrivals", "subject": "New in",
                          "content": "Take a look."},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True and body["queued"] is True
    assert body["recipientCount"] == len(known_list)
    assert body["campaignId"].startswith("CMP-")
    assert str(body["jobId"]).startswith("JOB-")
    assert sent == [], "the request sent an email instead of queueing"
    row = one("SELECT * FROM marketing_campaigns WHERE id=?", (body["campaignId"],))
    assert row["status"] == "queued"
    assert row["campaign_type"] == "new_arrivals"
    assert row["recipient_count"] == len(known_list)
    assert manual_queue.pending() == 1


def test_a_coupon_broadcast_requires_and_announces_the_code(
        client, sent, manual_queue, known_list):
    tok = _login(client)
    r = client.post("/api/admin/marketing/broadcast",
                    json={"kind": "coupon", "subject": "A gift",
                          "content": "Enjoy 10% off."},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 400
    r = client.post("/api/admin/marketing/broadcast",
                    json={"kind": "coupon", "subject": "A gift",
                          "content": "Enjoy 10% off.", "couponCode": "JAURA10"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    row = one("SELECT * FROM marketing_campaigns WHERE id=?",
              (r.get_json()["campaignId"],))
    assert "JAURA10" in row["content"]
    assert row["campaign_type"] == "customer_appreciation"


def test_an_unknown_broadcast_kind_is_rejected(client, sent, manual_queue):
    tok = _login(client)
    r = client.post("/api/admin/marketing/broadcast",
                    json={"kind": "telepathy", "subject": "x", "content": "y"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 400
    assert "broadcast type" in r.get_json()["error"].lower()


def test_a_new_arrivals_job_needs_a_configured_mailer(
        client, manual_queue, monkeypatch):
    monkeypatch.setattr(mailer, "configured", lambda: False)
    tok = _login(client)
    r = client.post("/api/admin/marketing/broadcast",
                    json={"kind": "new_arrivals", "subject": "x", "content": "y"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 400
    assert "Resend" in r.get_json()["error"]


# ------------------------------------------------------------- the dispatcher
def _queue(client, tok, **over):
    payload = {"kind": "new_arrivals", "subject": "New in",
               "content": "Take a look."}
    payload.update(over)
    r = client.post("/api/admin/marketing/broadcast", json=payload,
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    return r.get_json()


def test_the_dispatcher_sends_one_chunk_and_reschedules_itself(
        client, sent, manual_queue, known_list):
    tok = _login(client)
    queued = _queue(client, tok)
    # The first pass sends at most one chunk * the passes-per-job budget.
    manual_queue.drain()
    job = manual_queue.get(queued["jobId"])
    budget = api.BROADCAST_CHUNK * api.BROADCAST_PASSES_PER_JOB
    assert len(sent) <= budget
    assert job["state"] in ("done", "retry", "queued"), job
    task_queue.wait_idle(timeout=10)
    assert sorted(m["to"] for m in sent) == [
        "account@example.com", "buyer1@example.com", "buyer2@example.com",
        "leaver@example.com"]
    row = one("SELECT * FROM marketing_campaigns WHERE id=?", (queued["campaignId"],))
    assert row["status"] == "sent"
    assert row["sent_count"] == 4
    assert row["failed_count"] == 0


def test_every_recipient_is_logged_so_a_resume_never_double_sends(
        client, sent, manual_queue, known_list):
    tok = _login(client)
    queued = _queue(client, tok)
    task_queue.wait_idle(timeout=10)
    logged = query("SELECT email, status FROM marketing_campaign_sends "
                   "WHERE campaign_id=? ORDER BY email", (queued["campaignId"],))
    assert [r["email"] for r in logged] == [
        "account@example.com", "buyer1@example.com", "buyer2@example.com",
        "leaver@example.com"]
    assert all(r["status"] == "sent" for r in logged)
    # Simulate the worker dying right after the log landed and the job being
    # retried: the same campaign must not re-send to anybody.
    task_queue.enqueue("campaign.dispatch", {"campaign": queued["campaignId"]},
                       dedupe=False)
    task_queue.wait_idle(timeout=10)
    assert len(sent) == len(logged), "a resumed broadcast re-sent to the same addresses"


def test_a_failing_address_is_counted_and_does_not_stop_the_batch(
        client, sent, manual_queue, known_list, monkeypatch):
    tok = _login(client)
    monkeypatch.setattr(api, "_iter_marketing_recipients",
                        lambda page_size=200, max_rows=20000: iter(
                            known_list + ["bad@example.com"]))
    queued = _queue(client, tok)
    task_queue.wait_idle(timeout=10)
    row = one("SELECT * FROM marketing_campaigns WHERE id=?", (queued["campaignId"],))
    assert row["failed_count"] == 1
    assert row["sent_count"] == 4
    assert row["status"] == "partial"
    bad = one("SELECT status, detail FROM marketing_campaign_sends "
              "WHERE campaign_id=? AND email=?", (queued["campaignId"], "bad@example.com"))
    assert bad["status"] == "failed"
    assert "mailbox" in bad["detail"]


def test_cancelling_stops_the_next_pass_and_keeps_what_was_sent(
        client, sent, manual_queue):
    tok = _login(client)
    queued = _queue(client, tok)
    r = client.post(f"/api/admin/marketing/broadcast/{queued['campaignId']}/cancel",
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert r.get_json()["cancelled"] is True
    already = len(sent)
    task_queue.wait_idle(timeout=10)
    assert len(sent) == already, "a cancelled broadcast kept sending"
    row = one("SELECT status FROM marketing_campaigns WHERE id=?",
              (queued["campaignId"],))
    assert row["status"] == "cancelled"


def test_progress_is_readable_while_it_sends(client, sent, manual_queue,
                                             known_list):
    tok = _login(client)
    queued = _queue(client, tok)
    body = client.get(f"/api/admin/marketing/broadcast/{queued['campaignId']}").get_json()
    assert body["ok"] is True
    assert body["campaign"]["id"] == queued["campaignId"]
    assert body["campaign"]["recipientCount"] == len(known_list)
    assert body["logCounts"] == {"sent": 0, "failed": 0}
    task_queue.wait_idle(timeout=10)
    body = client.get(f"/api/admin/marketing/broadcast/{queued['campaignId']}").get_json()
    assert body["campaign"]["sentCount"] == len(known_list)
    assert body["campaign"]["done"] is True
    assert client.get("/api/admin/marketing/broadcast/CMP-NOPE").status_code == 404


def test_the_storefront_shell_serves_the_hub_url(client):
    """GET /admin/marketing/broadcast is the portal shell (no login needed)."""
    r = client.get("/admin/marketing/broadcast")
    assert r.status_code == 200
    assert b"admin-root" in r.data
    r = client.get("/admin")
    assert r.status_code == 200
    assert b"admin-root" in r.data


# ------------------------------------------------------------------ contracts
def test_every_broadcast_kind_maps_onto_an_allowed_campaign_type():
    for kind in BROADCAST_KINDS:
        stored = campaign_type_for_broadcast(kind)
        assert stored in CAMPAIGN_TYPES, (kind, stored)
        assert campaign_type_for_broadcast(kind.upper()) == stored
    assert campaign_type_for_broadcast("nope") is None
    assert {o["kind"] for o in broadcast_kind_options()} == set(BROADCAST_KINDS)


def test_the_portal_targets_the_broadcast_routes_and_the_job_panel():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    admin = open(os.path.join(root, "js", "admin.js"), encoding="utf-8").read()
    assert "mk-hub-card" in admin
    assert "api/admin/marketing/broadcast" in admin
    assert "api/admin/tasks/jobs" in admin
    assert "Background jobs" in admin
    assert "Email broadcast hub" in admin
    api_src = open(os.path.join(root, "api.py"), encoding="utf-8").read()
    for route in ('"/admin/marketing/broadcast/audience"',
                  '"/admin/marketing/broadcast/preview"',
                  '"/admin/marketing/broadcast"',
                  '"/admin/marketing/broadcast/<cid>"',
                  '"/admin/marketing/broadcast/<cid>/cancel"',
                  '"/admin/tasks/jobs"',
                  '"/admin/tasks/jobs/<job_id>"',
                  '"/admin/tasks/jobs/<job_id>/retry"'):
        assert route in api_src, route
