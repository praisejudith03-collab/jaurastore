"""The scheduler budget is measured in distinct supplier URLs, not rows."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")

import security  # noqa: E402
import supplier_watchdog  # noqa: E402


def _product(pid, url):
    return {
        "id": pid,
        "name": f"Item {pid}",
        "supplierSku": url,
        "options": [],
        "optionStock": {},
        "stock": 2,
        "stock_quantity": 2,
    }


def test_tick_fetches_at_most_two_unique_urls_and_cools_each_url(monkeypatch):
    shared = "https://supplier.example/shared"
    second = "https://supplier.example/second"
    third = "https://supplier.example/third"
    rows = [_product("a", shared), _product("b", shared),
            _product("c", second), _product("d", third)]
    opened = []
    page = (b'<script type="application/ld+json">'
            b'{"@type":"Product","name":"Example item","quantity":5}'
            b'</script>')

    # Supplier pages are fetched through the SSRF-guarded opener
    # (security.guarded_open), so that is the network seam this test counts.
    # Patching here keeps the budget assertion honest while leaving the real
    # URL validation in place for tests/test_ssrf_guards.py.
    def fake_guarded_open(request, **_kwargs):
        opened.append(request.full_url)
        return page

    monkeypatch.setattr(supplier_watchdog.catalog_mod, "merged",
                        lambda include_hidden=True: rows)
    monkeypatch.setattr(supplier_watchdog.catalog_mod, "deleted_product_ids",
                        lambda: set())
    monkeypatch.setattr(security, "guarded_open", fake_guarded_open)
    monkeypatch.setattr(supplier_watchdog, "_save_warnings", lambda _warnings: None)
    supplier_watchdog._last_checked.clear()
    supplier_watchdog._url_cache.clear()

    first = supplier_watchdog.tick(limit=10, min_interval_seconds=3600,
                                   link_limit=2)
    assert first["links"] == 2
    # Products a and b share one selected link; c uses the other selected
    # link. d's third URL is not touched. The HTTP fetch still happens only
    # once for the shared URL, despite both products being synchronized.
    assert first["checked"] == 3
    assert opened == [shared, second]
    assert set(supplier_watchdog._last_checked) == {shared, second}

    # The third URL was not selected in the first batch, so it may fill one
    # slot in the next tick. The already checked URLs remain in cooldown.
    second_run = supplier_watchdog.tick(limit=10, min_interval_seconds=3600,
                                        link_limit=2)
    assert second_run["links"] == 1 and second_run["checked"] == 1
    assert opened == [shared, second, third]

    third_run = supplier_watchdog.tick(limit=10, min_interval_seconds=3600,
                                       link_limit=2)
    assert third_run["links"] == 0 and third_run["checked"] == 0
    assert opened == [shared, second, third], "URL cooldown must prevent repeat fetches"
