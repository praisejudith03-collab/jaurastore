"""Variant-level stockout alerts: the colour that sells out must email.

The reported failure
--------------------
"The black big channel bag ran out and nothing emailed me." The supplier
pipeline skips anything the shop has already zeroed
(``catalog.supplier_alert_sellable`` returns False when own stock is 0), and
nothing else watched the shop's own numbers — so a variant dropping to 0 while
its parent product still had other colours produced NO alert at all.

What is pinned here
-------------------
* the sweep expands a product into its ``optionStock`` grid, so "Black" is
  judged on its own quantity, not the parent total;
* a variant hitting 0 queues exactly ONE pending alert and one email to the
  owner inbox (``ADMIN_EMAIL`` = jaurastore@gmail.com);
* a whole product with no grid is judged on its total;
* staying at 0 never re-emails; a restock closes the episode and the next drop
  alerts again; low -> out escalates;
* a sale that takes the last unit wakes the worker, which re-reads the row and
  sends immediately (the request itself never sends mail);
* a hidden / trashed / deliberately locked-out product is not news and is
  never emailed;
* the scheduler runs the sweep every tick, and the admin endpoints expose the
  ledger, a manual check and "seen".

Run with:  python3 -m pytest tests/test_variant_stock_alerts.py -q
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

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import inventory_alerts  # noqa: E402
import mailer  # noqa: E402
import task_queue  # noqa: E402
from config import Config  # noqa: E402
from db import execute, init_db, query  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EMAIL = "jaurastore@gmail.com"


# ------------------------------------------------------------------ fixtures
@pytest.fixture()
def app():
    application = appmod.create_app()
    application.config.update(TESTING=True)
    init_db()
    return application


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def clean_ledger():
    init_db()
    execute("DELETE FROM inventory_alerts")
    yield
    execute("DELETE FROM inventory_alerts")


@pytest.fixture()
def sent(monkeypatch):
    """Capture alert emails instead of sending them."""
    box = []

    def _send(alerts):
        box.append([dict(a) for a in alerts])
        return True, "sent"

    monkeypatch.setattr(mailer, "send_inventory_stock_alert", _send)
    return box


@pytest.fixture()
def manual_queue():
    task_queue.reset()
    task_queue.set_manual(True)
    yield task_queue
    task_queue.set_manual(False)
    task_queue.reset()


def bag(black=0, red=4, total=None, **extra):
    """A live bag with a colour grid — the reported case."""
    row = {
        "id": "jau-bag-1", "name": "Big Channel Bag", "sku": "BAG-CH",
        "online": True, "priceNgn": 25000,
        "optionStock": {"Black": black, "Red": red},
    }
    row["stock"] = row["stock_quantity"] = (black + red) if total is None else total
    row.update(extra)
    return row


def _patch_catalogue(monkeypatch, rows):
    monkeypatch.setattr(inventory_alerts, "_catalogue_rows", lambda: list(rows))
    monkeypatch.setattr(catalog_mod, "merged", lambda include_hidden=False: list(rows))


def _ledger():
    return {r["key"]: dict(r) for r in query("SELECT * FROM inventory_alerts")}


# ------------------------------------------------------------ the reported bug
def test_a_single_colour_selling_out_is_detected_while_the_product_has_stock(
        monkeypatch, sent):
    """Black = 0, Red = 4: the parent total is 4, the variant is gone."""
    _patch_catalogue(monkeypatch, [bag(black=0, red=4)])
    result = inventory_alerts.sweep()
    assert result["scanned"] == 2, "the sweep must see both variants"
    assert result["sent"] is True
    assert len(sent) == 1
    alert = sent[0][0]
    assert alert["product_id"] == "jau-bag-1"
    assert alert["option"] == "Black"
    assert alert["level"] == "out"
    assert alert["qty"] == 0
    assert alert["product_total"] == 4        # the parent is still sellable
    ledger = _ledger()
    assert ledger["jau-bag-1::Black"]["level"] == "out"
    assert ledger["jau-bag-1::Black"]["pending"] == 1 or ledger["jau-bag-1::Black"]["emailed_level"] == "out"
    # Red (4 left) is inside the default threshold of 5 → one low alert,
    # but it travels in the SAME email, not a second one.
    assert any(a["option"] == "Red" for a in sent[0])


def test_a_whole_product_without_variants_is_judged_on_its_total(monkeypatch, sent):
    _patch_catalogue(monkeypatch, [{
        "id": "jau-simple-1", "name": "Silk Scarf", "sku": "SC-1",
        "online": True, "stock_quantity": 0,
    }])
    result = inventory_alerts.sweep()
    assert result["scanned"] == 1
    assert sent and sent[0][0]["option"] == ""
    assert sent[0][0]["level"] == "out"


def test_a_healthy_catalogue_sends_nothing(monkeypatch, sent):
    _patch_catalogue(monkeypatch, [bag(black=9, red=12)])
    result = inventory_alerts.sweep()
    assert result["scanned"] == 2
    assert result["sent"] is False
    assert sent == []
    assert _ledger() == {}


# --------------------------------------------------------------- no spam
def test_a_variant_that_stays_out_is_emailed_once(monkeypatch, sent):
    _patch_catalogue(monkeypatch, [bag(black=0, red=9)])
    inventory_alerts.sweep()
    assert len(sent) == 1
    for _ in range(3):
        inventory_alerts.sweep()
    assert len(sent) == 1, "a sold-out colour re-emailed on every sweep"
    entry = _ledger()["jau-bag-1::Black"]
    assert entry["emailed_level"] == "out"
    assert entry["pending"] == 0


def test_a_restock_rearms_the_alert(monkeypatch, sent):
    rows = [bag(black=0, red=9)]
    _patch_catalogue(monkeypatch, rows)
    inventory_alerts.sweep()
    assert len(sent) == 1
    # Restocked: the episode closes…
    rows[0] = bag(black=6, red=9)
    _patch_catalogue(monkeypatch, rows)
    inventory_alerts.sweep()
    assert "jau-bag-1::Black" not in _ledger()
    # …so the next drop is news again.
    rows[0] = bag(black=0, red=9)
    _patch_catalogue(monkeypatch, rows)
    inventory_alerts.sweep()
    assert len(sent) == 2


def test_a_restock_below_the_threshold_still_rearms_the_next_stockout(
        monkeypatch, sent):
    """out -> restocked to 4 (still "low") -> sold out again must email twice.

    A restock that lands *inside* the low band is an improvement, not a new
    problem: no email for it, but the next drop to zero is news. (Before this
    rule the episode never closed and the second stockout was silent.)
    """
    rows = [bag(black=0, red=9)]
    _patch_catalogue(monkeypatch, rows)
    inventory_alerts.sweep()
    assert len(sent) == 1 and sent[0][0]["level"] == "out"
    rows[0] = bag(black=4, red=9)          # partially restocked, still low
    _patch_catalogue(monkeypatch, rows)
    inventory_alerts.sweep()
    assert len(sent) == 1, "a restock must not re-email"
    rows[0] = bag(black=0, red=9)          # sold out again
    _patch_catalogue(monkeypatch, rows)
    inventory_alerts.sweep()
    assert len(sent) == 2, "the second stockout was swallowed by the old episode"
    assert any(a["level"] == "out" for a in sent[1])


def test_low_to_out_escalates_into_a_second_email(monkeypatch, sent,
                                                  monkeypatch_env=None):
    rows = [bag(black=3, red=9)]
    _patch_catalogue(monkeypatch, rows)
    inventory_alerts.sweep()
    assert len(sent) == 1 and sent[0][0]["level"] == "low"
    rows[0] = bag(black=0, red=9)
    _patch_catalogue(monkeypatch, rows)
    inventory_alerts.sweep()
    assert len(sent) == 2 and any(a["level"] == "out" for a in sent[1])
    # out -> low (a restock below the threshold but above zero) is not news.
    rows[0] = bag(black=2, red=9)
    _patch_catalogue(monkeypatch, rows)
    inventory_alerts.sweep()
    assert len(sent) == 2


def test_a_failed_email_keeps_the_alert_pending(monkeypatch):
    _patch_catalogue(monkeypatch, [bag(black=0, red=9)])
    monkeypatch.setattr(mailer, "send_inventory_stock_alert",
                        lambda alerts: (False, "provider 500"))
    result = inventory_alerts.sweep()
    assert result["sent"] is False
    entry = _ledger()["jau-bag-1::Black"]
    assert entry["pending"] == 1
    assert not entry["emailed_at"]
    # The next pass retries it.
    box = []
    monkeypatch.setattr(mailer, "send_inventory_stock_alert",
                        lambda alerts: (box.append(alerts), (True, "ok"))[1])
    assert inventory_alerts.sweep()["sent"] is True
    assert box and _ledger()["jau-bag-1::Black"]["pending"] == 0


# ------------------------------------------------------- not news, no email
def test_a_hidden_or_trashed_product_is_never_emailed(monkeypatch, sent):
    _patch_catalogue(monkeypatch, [
        bag(black=0, red=0, online=False),
        bag(black=0, red=0, id="jau-bag-2", online=None, deleted=True),
        bag(black=0, red=0, id="jau-bag-3", status="archived"),
    ])
    result = inventory_alerts.sweep()
    assert result["scanned"] == 0
    assert sent == []


def test_an_owner_declared_out_of_stock_lock_is_not_re_emailed(monkeypatch, sent):
    row = bag(black=0, red=0, stockStatus="out")
    _patch_catalogue(monkeypatch, [row])
    monkeypatch.setattr(catalog_mod, "_manual_stock_lock", lambda product: True)
    monkeypatch.setattr(catalog_mod, "is_stock_locked",
                        lambda product_or_id: True)
    inventory_alerts.sweep()
    assert sent == []


# ------------------------------------------------------- the sale path (event)
def test_a_sale_that_takes_the_last_unit_wakes_the_worker(monkeypatch, sent,
                                                          manual_queue):
    rows = [bag(black=1, red=9)]
    _patch_catalogue(monkeypatch, rows)
    assert catalog_mod.reserve_stock("jau-bag-1", 1, option_key="Black") is not None
    # The reservation must NOT send mail inline, but it must queue the check.
    assert sent == []
    assert manual_queue.pending() >= 1
    rows[0] = bag(black=0, red=9)          # what the write just did
    manual_queue.drain()
    assert sent and sent[0][0]["option"] == "Black"
    assert sent[0][0]["level"] == "out"


def test_the_alert_quotes_the_fresh_row_not_a_stale_one(monkeypatch, sent):
    """check_products re-reads the catalogue: a stale copy cannot mislead."""
    _patch_catalogue(monkeypatch, [bag(black=0, red=9)])
    result = inventory_alerts.check_products(["jau-bag-1"])
    assert result["checked"] == 1
    assert result["sent"] is True
    assert sent[0][0]["qty"] == 0


# ------------------------------------------------------------------- email
def test_the_email_goes_to_the_owner_inbox_and_names_the_colour(monkeypatch):
    posted = {}

    def fake_post(url, headers, payload):
        posted.update(url=url, payload=payload)
        return True, "{}"

    monkeypatch.setattr(mailer, "_http_post_json", fake_post)
    monkeypatch.setattr(mailer, "_cfg", lambda name, default="": {
        "RESEND_API_KEY": "re_test_key",
        "MAIL_FROM": "orders@jaurastore.com.ng",
        "ADMIN_EMAIL": EMAIL,
    }.get(name, default))
    ok, _detail = mailer.send_inventory_stock_alert([{
        "product_id": "jau-bag-1", "product_name": "Big Channel Bag",
        "sku": "BAG-CH", "option": "Black", "option_label": "Black",
        "level": "out", "qty": 0, "product_total": 4,
    }])
    assert ok
    assert posted["payload"]["to"] == [EMAIL]
    assert "Inventory alert" in posted["payload"]["subject"]
    assert "Big Channel Bag" in posted["payload"]["html"]
    assert "Black" in posted["payload"]["html"]
    assert "other variants still on sale" in posted["payload"]["html"]


def test_the_email_says_variants_and_counts_them():
    html = mailer.inventory_stock_alert_email_html([
        {"product_name": "Bag", "option": "Black", "level": "out", "qty": 0},
        {"product_name": "Bag", "option": "Red", "level": "out", "qty": 0},
    ])
    assert "2 variants out of stock" in html


# ------------------------------------------------------------- admin surface
def _login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def test_the_admin_can_see_and_acknowledge_a_stockout(client, monkeypatch, sent):
    _patch_catalogue(monkeypatch, [bag(black=0, red=9)])
    inventory_alerts.sweep()
    token = _login(client)
    r = client.get("/api/admin/inventory-alerts")
    assert r.status_code == 200, r.data
    inv = r.get_json()["inventory"]
    assert inv["ready"] is True
    assert inv["out"] >= 1
    assert any(a["productId"] == "jau-bag-1" and a["option"] == "Black"
               for a in inv["alerts"])
    r = client.post("/api/admin/inventory-alerts/mark-seen",
                    headers={"X-CSRF-Token": token},
                    json={"productId": "jau-bag-1", "option": "Black"})
    assert r.status_code == 200, r.data
    entry = _ledger()["jau-bag-1::Black"]
    assert entry["seen_at"]
    assert entry["seen_level"] == "out"


def test_check_now_runs_the_sweep(client, monkeypatch, sent):
    _patch_catalogue(monkeypatch, [bag(black=0, red=9)])
    token = _login(client)
    r = client.post("/api/admin/inventory-alerts/check",
                    headers={"X-CSRF-Token": token})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["result"]["sent"] is True
    assert body["inventory"]["out"] >= 1


def test_the_alert_endpoints_are_admin_only(client):
    assert client.get("/api/admin/inventory-alerts").status_code == 401
    assert client.post("/api/admin/inventory-alerts/check").status_code in (401, 403)


# --------------------------------------------------------------- the wiring
def test_the_scheduler_and_the_stock_write_paths_are_wired():
    scheduler = (ROOT / "scheduler.py").read_text(encoding="utf-8")
    assert "inventory.alerts" in scheduler
    assert "inventory_alerts.sweep" in scheduler
    catalog = (ROOT / "catalog.py").read_text(encoding="utf-8")
    # The sale path wakes the worker in BOTH backends (local + Supabase).
    assert catalog.count("_wake_inventory_alerts(") >= 5
    api = (ROOT / "api.py").read_text(encoding="utf-8")
    assert "import inventory_alerts" in api
    # …and the handler is registered at boot, not only when something imports it.
    assert "register()" in (ROOT / "inventory_alerts.py").read_text(encoding="utf-8")


def test_the_ledger_survives_a_restart_through_the_durable_mirror(monkeypatch):
    """A redeploy empties /tmp: the Supabase copy must restore the episodes."""
    _patch_catalogue(monkeypatch, [bag(black=0, red=9)])
    inventory_alerts.sweep()
    mirrored = {}

    import supabase_store
    monkeypatch.setattr(supabase_store, "save_inventory_alert_state",
                        lambda state: (mirrored.update(state), True)[1])
    _patch_catalogue(monkeypatch, [bag(black=0, red=11)])
    inventory_alerts.sweep()
    assert mirrored.get("entries"), "the ledger was never mirrored"

    execute("DELETE FROM inventory_alerts")      # simulate the wiped disk
    monkeypatch.setattr(supabase_store, "load_inventory_alert_state",
                        lambda: mirrored)
    ledger = inventory_alerts.load_ledger()
    assert ledger and "jau-bag-1::Black" in ledger


def test_the_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("INVENTORY_LOW_STOCK_THRESHOLD", "2")
    assert inventory_alerts.low_stock_threshold() == 2
    assert inventory_alerts.level_for(3) == "ok"
    assert inventory_alerts.level_for(2) == "low"
    assert inventory_alerts.level_for(0) == "out"
    monkeypatch.setenv("INVENTORY_LOW_STOCK_THRESHOLD", "rubbish")
    assert inventory_alerts.low_stock_threshold() == 5


def test_the_kill_switch_stops_every_email(monkeypatch, sent):
    monkeypatch.setenv("INVENTORY_STOCK_ALERTS_ENABLED", "0")
    _patch_catalogue(monkeypatch, [bag(black=0, red=9)])
    result = inventory_alerts.sweep()
    assert result["detail"] == "disabled"
    assert sent == []
