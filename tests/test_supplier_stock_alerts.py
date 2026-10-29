"""Supplier low/out email alerts — the alert-only watchdog contract.

Owner directive (this session):
  * the supplier watchdog must NEVER change store inventory;
  * supplier low/out instead emails the owner (jaurastore@gmail.com)
    once per transition, so stock is updated by hand when free.

Run with:  python3 -m pytest tests/test_supplier_stock_alerts.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")

import supplier_alerts  # noqa: E402
import supplier_watchdog  # noqa: E402


def _obs(qty, option="", flagged=False, pid="jau-perf-1"):
    return {"product_id": pid, "product_name": "Parfum Élégance",
            "sku": "JAU-P1", "option": option, "supplier_qty": qty,
            "flagged": flagged, "shop_stock": 7}


class _Store:
    """In-memory durable state with the load/save shapes supabase_store uses."""

    def __init__(self, state=None, fail_load=False):
        self.state = state
        self.fail_load = fail_load

    def load(self):
        return None if self.fail_load else self.state

    def save(self, state):
        self.state = state
        return True


# ------------------------------------------------------------ classification

def test_levels_use_supplier_quantities_and_never_alert_on_a_bare_flag():
    assert supplier_alerts.level_for(0) == "out"
    assert supplier_alerts.level_for(1) == "low"
    assert supplier_alerts.level_for(9) == "low"
    assert supplier_alerts.level_for(10) == "ok"
    assert supplier_alerts.level_for(None) is None
    # "In stock" with no printed count is healthy, never "1 left".
    assert supplier_alerts.level_for(1, flagged=True) == "ok"


def test_badge_and_alert_share_one_derivation():
    assert supplier_alerts.supplier_status_for([("", 0, False)]) == "out_of_stock"
    assert supplier_alerts.supplier_status_for([("Red", 50, False),
                                                ("Blue", 0, False)]) == "low_stock"
    assert supplier_alerts.supplier_status_for([("", 1, True)]) == "ok"


# --------------------------------------------------------------- transitions

def test_entering_low_queues_one_alert_and_staying_low_does_not_repeat():
    store = _Store({})
    first = supplier_alerts.record_observations([_obs(4)], store.load, store.save)
    assert first["pending"] == 1
    again = supplier_alerts.record_observations([_obs(3)], store.load, store.save)
    assert again["pending"] == 1  # still just the one queued email


def test_escalation_low_to_out_queues_a_fresh_alert_after_the_first_was_sent():
    store = _Store({})
    supplier_alerts.record_observations([_obs(4)], store.load, store.save)
    sent = []
    out = supplier_alerts.drain_pending_alerts(
        send=lambda alerts: (sent.extend(alerts), (True, "ok"))[1],
        load=store.load, save=store.save)
    assert out["sent"] is True and len(sent) == 1
    # Same low level after mailing: silence.
    assert supplier_alerts.record_observations([_obs(2)], store.load, store.save)["pending"] == 0
    # Worsening to out: a fresh alert is owed.
    assert supplier_alerts.record_observations([_obs(0)], store.load, store.save)["pending"] == 1


def test_recovery_clears_the_entry_so_the_next_drop_alerts_again():
    store = _Store({})
    supplier_alerts.record_observations([_obs(0)], store.load, store.save)
    supplier_alerts.drain_pending_alerts(send=lambda a: (True, "ok"),
                                         load=store.load, save=store.save)
    supplier_alerts.record_observations([_obs(40)], store.load, store.save)
    assert supplier_alerts.pending_alerts(store.state) == []
    assert supplier_alerts.record_observations([_obs(5)], store.load, store.save)["pending"] == 1


def test_variants_alert_independently():
    store = _Store({})
    result = supplier_alerts.record_observations(
        [_obs(0, option="Noir"), _obs(30, option="Rose")], store.load, store.save)
    assert result["pending"] == 1
    [alert] = supplier_alerts.pending_alerts(store.state)
    assert alert["option"] == "Noir" and alert["level"] == "out"


def test_unreadable_state_never_looks_empty():
    store = _Store(fail_load=True)
    result = supplier_alerts.record_observations([_obs(0)], store.load, store.save)
    assert result["skipped"] == "state-unavailable"


# ------------------------------------------------------------------- sending

def test_failed_send_leaves_alerts_pending_for_the_next_drain():
    store = _Store({})
    supplier_alerts.record_observations([_obs(0)], store.load, store.save)
    out = supplier_alerts.drain_pending_alerts(send=lambda a: (False, "not configured"),
                                               load=store.load, save=store.save)
    assert out["sent"] is False and out["pending"] == 1
    out = supplier_alerts.drain_pending_alerts(send=lambda a: (True, "ok"),
                                               load=store.load, save=store.save)
    assert out["sent"] is True and out["pending"] == 0


def test_alert_email_goes_to_the_shop_inbox_and_says_stock_was_not_changed(monkeypatch):
    import mailer
    box = {}
    monkeypatch.setattr(mailer, "send_mail",
                        lambda subject, html, attachments=(): box.update(
                            subject=subject, html=html) or (True, "ok"))
    ok, _ = mailer.send_supplier_stock_alert(
        [{"product_id": "jau-perf-1", "product_name": "Parfum Élégance",
          "sku": "JAU-P1", "option": "50 ml", "level": "out",
          "supplier_qty": 0, "shop_stock": 7}])
    assert ok is True
    assert "out of stock" in box["subject"]
    assert "Parfum" in box["html"] and "OUT OF STOCK" in box["html"]
    assert "NOT changed automatically" in box["html"]
    assert "tab=supplierAlerts" in box["html"]
    # The canonical owner inbox is jaurastore@gmail.com.
    assert mailer._shop_inbox() == "jaurastore@gmail.com"


# ---------------------------------------------------- watchdog stays hands-off

def test_watchdog_never_writes_shop_stock_and_records_raw_readings(monkeypatch):
    import json as _json
    product = {
        "id": "jau-perf-1", "name": "Parfum Élégance", "sku": "JAU-P1",
        "priceNgn": 5000, "supplierSku": "https://supplier.example/p/1",
        "options": [{"title": "Size", "values": ["50 ml", "100 ml"]}],
        "optionStock": {"50 ml": 7, "100 ml": 7}, "stock": 14,
    }

    def forbidden(*a, **k):  # any shop-stock write fails the test
        raise AssertionError("watchdog tried to write shop stock")
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "apply_supplier_stock", forbidden)

    statuses = {}
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "apply_supplier_status",
                        lambda pid, status, actor="x": statuses.update(pid=pid, status=status) or True)
    readings = {}
    monkeypatch.setattr(supplier_watchdog, "_record_stock_alerts",
                        lambda p, r: readings.update(readings=list(r)))
    html = ('<script type="application/json">{"variants": %s}</script>'
            % _json.dumps([{"title": "50 ml", "available": False, "quantity": 0},
                           {"title": "100 ml", "available": True, "quantity": 3}]))
    monkeypatch.setattr(supplier_watchdog, "fetch_url", lambda url: html)
    ok, _warnings = supplier_watchdog.sync_product(product)
    assert ok is True
    assert statuses["status"] == "low_stock"          # diagnostic badge only
    assert ("50 ml", 0, False) in readings["readings"]  # raw supplier numbers
    assert ("100 ml", 3, False) in readings["readings"]
    assert product["stock"] == 14 and product["optionStock"] == {"50 ml": 7, "100 ml": 7}


def test_bare_in_stock_flag_is_ok_not_out_of_stock(monkeypatch):
    statuses = {}
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "apply_supplier_status",
                        lambda pid, status, actor="x": statuses.update(status=status) or True)
    recorded = {}
    monkeypatch.setattr(supplier_watchdog, "_record_stock_alerts",
                        lambda p, r: recorded.update(readings=list(r)))
    ok, _ = supplier_watchdog._sync_whole_product(
        {"id": "jau-x", "name": "Simple", "stock": 5},
        [{"qty": 1, "flag": True, "label": "Simple"}], "test", [])
    assert ok is True and statuses["status"] == "ok"
    # The queued observation classifies ok, so it can never email.
    [(_opt, qty, flagged)] = recorded["readings"]
    assert supplier_alerts.level_for(qty, flagged) == "ok"
