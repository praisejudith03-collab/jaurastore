"""#68 regressions: public headers, durable categories and browser ordering."""
from pathlib import Path
import shutil
import subprocess

import pytest
import app as appmod
import supabase_store
from test_categories_resilient import _LegacyCatTable, _FakeClient, _cats

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client():
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as client:
        yield client


@pytest.mark.parametrize("path", ["/", "/shop.html", "/categories.html", "/faq.html",
                                  "/cart.html", "/checkout.html", "/product.html"])
def test_public_header_and_no_admin_portal(client, path):
    # Public pages must work WITHOUT invented authentication headers.
    response = client.get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="site-header"' in html
    assert 'src="js/store.js?' in html
    # Actual injected logo/search visibility is checked in test_browser_smoke.py.
    assert "admin portal" not in html.lower()


def test_category_persistence_after_redeploy(monkeypatch):
    store = {}
    table = _LegacyCatTable(store)
    monkeypatch.setattr(supabase_store, "client", lambda: _FakeClient(store, table))
    monkeypatch.setattr(supabase_store, "_CATS_SHAPE", {"fill": {}, "drop": [], "values": {}})
    categories = _cats() + [{"id": "perfume", "name": "Perfume", "name_fr": "Parfum"}]
    assert supabase_store.save_categories_table(categories) is True
    assert store["perfume"]["name_fr"] == "Parfum"
    # A fresh client/schema cache still operates on the durable table.
    monkeypatch.setattr(supabase_store, "_CATS_SHAPE", {"fill": {}, "drop": [], "values": {}})
    assert supabase_store.save_categories_table(list(store.values())) is True
    assert store["perfume"]["name"] == "Perfume"


def test_household_and_explicit_category_order_in_real_store():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    result = subprocess.run([node, "tests/_store_sim.mjs"], cwd=ROOT,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "category ordering preserves every category" in result.stdout
