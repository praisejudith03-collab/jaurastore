"""Autopilot stability release checks (owner request 2026-10-05).

This module is the machine-checkable half of the "runs flawlessly on
autopilot" audit. Four contracts are pinned here:

  a) ADMIN SAVE SPEED - POST /api/admin/products and its aliases answer
     HTTP 200 in under 100ms, so the product editor can never be the thing
     that makes the dashboard feel dead.

  b) PRICE TEXT NEVER BREAKS - on a 320px-414px phone every money token
     (₦12,500 / "6,000 CFA" / a ₦100,000.00 - ₦150,000.00 variant range)
     renders as ONE unbroken box: `white-space: nowrap` plus a responsive
     `clamp()` font-size, proved statically here and measured in a real
     Chromium when one is available (CI installs it; the sandbox skips).

  c) DASHBOARD STOCK QUEUES ARE COLLAPSIBLE - Low Stock / Out of Stock /
     Supplier-linked render as single-line summary bars that open a drawer,
     and every row deep-links to /admin/products?id=<ID>. Proved by
     tests/_admin_attention_drawer_sim.mjs, which boots the real
     js/store.js + js/admin.js.

  d) STOCK BOUNDS ARE EXACT - stock = N accepts N and refuses N + 1 with
     "Only N items remaining in stock", on the server AND in the cart.
     Proved by tests/_stock_bounds_cart_sim.mjs plus the live API test.

Run with:  python3 -m pytest tests/test_autopilot_stability.py -q
"""
import os
import re
import shutil
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("SITE_CONFIG_PATH", "/tmp/jaura_test_site_autopilot.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

import app as appmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
from db import execute, init_db  # noqa: E402

EMAIL = "jaurastore@gmail.com"
SAVE_BUDGET_MS = 100.0

# CI installs chromium explicitly (`.github/workflows/ci.yml`), so a browser
# that will not start there is a BROKEN BUILD, not a reason to skip. Without
# this switch the four mobile measurements below report green while measuring
# nothing - the flake that looks like a pass. Locally the switch stays off and
# the tests keep skipping with a printed reason (`pytest -rs`).
REQUIRE_BROWSER = os.environ.get("JA_REQUIRE_BROWSER", "").strip().lower() not in (
    "", "0", "false", "no", "off")
DRAWER_SIM = os.path.join(ROOT, "tests", "_admin_attention_drawer_sim.mjs")
CART_SIM = os.path.join(ROOT, "tests", "_stock_bounds_cart_sim.mjs")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def csrf(client):
    return client.get("/api/config").get_json()["csrf"]


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


# =====================================================================
# a) Admin product saves answer 200, fast
# =====================================================================
SAVE_ENDPOINTS = [
    ("POST", "/api/admin/products"),
    ("POST", "/api/products"),
    ("PUT", "/api/products"),
]


@pytest.mark.parametrize("method,url", SAVE_ENDPOINTS)
def test_admin_product_save_answers_200_under_100ms(client, method, url):
    """The editor's save button hits one of these. On a 512MB single dyno
    every one of them must stay well under the 100ms interaction budget -
    a save that waits on a sleeping database is how "Could not reach the
    server" starts."""
    token = login(client)
    headers = {"X-CSRF-Token": token}

    def save(pid, name):
        payload = {"product": {
            "id": pid, "name": name, "priceNgn": 7500, "category": "beauty",
            "stock": 6, "stock_quantity": 6,
        }}
        started = time.perf_counter()
        response = client.open(url, method=method, json=payload, headers=headers)
        elapsed = (time.perf_counter() - started) * 1000.0
        return response, elapsed

    # One warm-up save pays the one-time catalogue/schema read; the budget is
    # about steady-state saves, which is what the owner taps all day.
    warm, _ = save("jau-speed-warm", "Speed Warm-up")
    assert warm.status_code == 200, warm.data

    timings = []
    for i in range(4):
        response, elapsed = save(f"jau-speed-{i}", f"Speed Check {i}")
        assert response.status_code == 200, response.data
        timings.append(elapsed)

    worst = max(timings)
    assert worst < SAVE_BUDGET_MS, (
        f"{method} {url} took {worst:.1f}ms "
        f"(all runs: {[round(t, 1) for t in timings]}), budget {SAVE_BUDGET_MS}ms"
    )


def test_the_saved_row_is_really_there_after_a_fast_save(client):
    """Speed must never come from skipping the write."""
    token = login(client)
    r = client.post("/api/admin/products",
                    json={"product": {"id": "jau-speed-persist", "name": "Persisted",
                                      "priceNgn": 4200, "category": "beauty",
                                      "stock": 3, "stock_quantity": 3}},
                    headers={"X-CSRF-Token": token})
    assert r.status_code == 200, r.data
    row = next((p for p in catalog_mod.merged(include_hidden=True)
                if str(p.get("id")) == "jau-speed-persist"), None)
    assert row is not None and int(catalog_mod.stock_of(row)) == 3


# =====================================================================
# b) Price tokens never break on a phone
# =====================================================================
MONEY_TOKEN_SELECTORS = (
    ".price .now",
    ".price s",
    ".price .price-range .range-min",
    ".price .price-range .range-max",
    "[data-mv-price-for]",
)
CONTAINER_SELECTORS = (".price", ".card .price", ".modal .price", ".welcome-pop .price")


def _css_rules(css, width_cap=None):
    """Yield (selectors, declarations, width_cap) for every rule, media-aware.

    Nested @media blocks are walked with a brace counter; the innermost
    `max-width` cap is carried onto every rule inside it.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # comments are not selectors
    out = []
    i, n = 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace < 0:
            break
        prelude = css[i:brace].strip()
        if prelude.startswith("@media"):
            m = re.search(r"max-width:\s*(\d+)px", prelude)
            cap = int(m.group(1)) if m else width_cap
            if width_cap is not None:
                cap = min(cap, width_cap) if cap is not None else width_cap
            depth, j = 1, brace + 1
            while j < n and depth:
                if css[j] == "{":
                    depth += 1
                elif css[j] == "}":
                    depth -= 1
                j += 1
            out.extend(_css_rules(css[brace + 1:j - 1], cap))
            i = j
            continue
        if prelude.startswith("@") or not prelude:
            close = css.find("}", brace)
            i = close + 1 if close >= 0 else n
            continue
        close = css.find("}", brace)
        if close < 0:
            break
        out.append((prelude, css[brace + 1:close], width_cap))
        i = close + 1
    return out


def _declares(selector_text, target):
    """Does this selector list name the target element itself (not a child)?"""
    for part in (s.strip() for s in selector_text.split(",")):
        if part == target:
            return True
    return False


@pytest.mark.parametrize("width", [320, 360, 390, 414])
def test_money_tokens_are_nowrap_at_phone_widths(width):
    """`white-space: nowrap` on every money token, at every phone width."""
    css = _read("css/style.css")
    rules = [r for r in _css_rules(css) if r[2] is None or width <= r[2]]
    for selector_text, decls, _cap in rules:
        if "white-space" not in decls:
            continue
        for target in MONEY_TOKEN_SELECTORS:
            if _declares(selector_text, target):
                assert "nowrap" in decls, (
                    f"{target} must be nowrap at {width}px, "
                    f"but {selector_text.strip()} says: {decls.strip()}"
                )
                break


@pytest.mark.parametrize("width", [320, 360, 390, 414])
def test_phone_prices_use_a_responsive_clamp(width):
    """A responsive clamp() is what keeps the one-line price inside a card."""
    css = _read("css/style.css")
    rules = [r for r in _css_rules(css) if r[2] is not None and width <= r[2]]
    clamps = [
        decls for selectors, decls, _cap in rules
        if "font-size" in decls and "clamp(" in decls
        and (".price" in selectors or "[data-mv-price-for]" in selectors)
    ]
    assert clamps, f"no narrow-viewport clamp() applies to prices at {width}px"
    assert any("!important" in decls for decls in clamps), (
        "the phone price size must win the cascade outright (!important)"
    )


def test_the_range_label_keeps_each_amount_whole():
    """A range may break BETWEEN two whole amounts, never inside one."""
    store = _read("js/store.js")
    assert 'class="range-min"' in store and 'class="range-max"' in store, (
        "each end of a price range must be its own nowrap token"
    )
    css = _read("css/style.css")
    assert ".price .price-range .range-min" in css and ".price .price-range .range-max" in css


def test_no_later_rule_re_enables_wrapping_on_a_money_token():
    """The cascade is checked for a late `white-space: normal` override."""
    css = _read("css/style.css")
    offenders = []
    for selector_text, decls, cap in _css_rules(css):
        if "white-space" not in decls or "nowrap" in decls:
            continue
        for target in MONEY_TOKEN_SELECTORS:
            if _declares(selector_text, target):
                offenders.append((selector_text.strip(), decls.strip(), cap))
    assert not offenders, f"a rule re-enables wrapping on a money token: {offenders}"


@pytest.fixture()
def live_shop(monkeypatch, tmp_path):
    """A real HTTP server for the browser measurement (Chromium in CI)."""
    import threading

    import db
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(tmp_path / "catalog.json"))
    monkeypatch.setattr(appmod.Config, "DB_PATH", str(tmp_path / "browser.db"))
    monkeypatch.setattr(appmod.Config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(db, "_local", threading.local())
    cats = tmp_path / "categories.json"
    cats.write_bytes(open(os.path.join(ROOT, "data/categories.json"), "rb").read())
    import api as apimod
    monkeypatch.setattr(apimod, "CATEGORIES_FILE", str(cats))
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", PW)
    app = appmod.create_app()
    app.config.update(TESTING=True)
    execute("DELETE FROM rate_limits")
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.parametrize("width", [320, 360, 390, 414])
def test_mobile_card_prices_measure_as_one_line(live_shop, width):
    """The real measurement: a Chromium lays the card out and the money token
    must occupy ONE line box that sits inside its card. Skipped where no
    browser can be installed (this sandbox); CI runs it for real."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on the machine
        if REQUIRE_BROWSER:
            pytest.fail(f"JA_REQUIRE_BROWSER is set but Playwright is missing: {exc}")
        pytest.skip(f"Playwright is not installed: {exc}")

    # A demanding, realistic worst case: a variant range with big prices.
    catalog_mod.upsert({
        "id": "jau-price-range", "name": "Range Price Check", "category": "beauty",
        "priceNgn": 100000, "stock": 9, "stock_quantity": 9, "online": True,
        "options": [{"title": "Size", "type": "SIZE", "values": ["S", "XL"]}],
        "optionStock": {"S": 4, "XL": 5},
        "optionPrices": {"Size: S": 100000, "Size: XL": 150000},
    }, "tester")

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(
                executable_path=os.environ.get("CHROMIUM_EXECUTABLE"),
                args=["--no-sandbox", "--disable-dev-shm-usage"])
        except Exception as exc:  # pragma: no cover - browser not installed
            if REQUIRE_BROWSER:
                pytest.fail(
                    "JA_REQUIRE_BROWSER is set but chromium could not launch, so "
                    f"nothing was measured: {exc}")
            pytest.skip(f"Playwright chromium not available: {exc}")
        context = browser.new_context(viewport={"width": width, "height": 844},
                                      is_mobile=True, has_touch=True,
                                      service_workers="block")
        context.add_init_script("sessionStorage.setItem('jaura_welcome_seen','1')")
        page = context.new_page()
        # Search narrows the grid to the seeded worst case, so the range
        # label is guaranteed to be on the page (24 products per page).
        page.goto(live_shop + "/shop.html?q=Range%20Price%20Check")
        page.wait_for_selector("[data-shop-grid] .card .price", timeout=20000)
        measurements = page.evaluate("""() => {
          const out = [];
          document.querySelectorAll("[data-shop-grid] .card").forEach((card) => {
            const price = card.querySelector(".price");
            if (!price) return;
            const cardRect = card.getBoundingClientRect();
            const money = [...price.querySelectorAll(".now, s, .range-min, .range-max")];
            [price].concat(money).forEach((token) => {
              const rect = token.getBoundingClientRect();
              out.push({
                text: token.textContent.trim(),
                // The price BLOCK may break between two whole amounts (a
                // struck-through "was" and the "now" price); a money TOKEN
                // must never break.
                lines: token.getClientRects().length,
                maxLines: token === price ? 2 : 1,
                right: rect.right,
                cardRight: cardRect.right,
                width: rect.width,
              });
            });
          });
          return {
            tokens: out,
            scrollWidth: document.documentElement.scrollWidth,
            innerWidth: window.innerWidth,
          };
        }""")
        browser.close()

    assert measurements["tokens"], "no product card price was rendered to measure"
    assert any("\u2013" in t["text"] or " - " in t["text"] for t in measurements["tokens"]), (
        "the seeded variant range was not rendered, so the worst case was not measured")
    for token in measurements["tokens"]:
        assert token["lines"] <= token["maxLines"], (
            f'"{token["text"]}" wrapped onto {token["lines"]} line boxes at {width}px')
        assert token["right"] <= token["cardRight"] + 2, (
            f'"{token["text"]}" overflows its card at {width}px '
            f'(right {token["right"]:.1f} vs card {token["cardRight"]:.1f})')
    assert measurements["scrollWidth"] <= measurements["innerWidth"] + 1, (
        f"the page scrolls sideways at {width}px: "
        f'{measurements["scrollWidth"]} > {measurements["innerWidth"]}')


# =====================================================================
# c) Compact dashboard queues + drawer
# =====================================================================
def test_the_dashboard_drawer_simulation_passes():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (dashboard sim needs Node >= 18)")
    proc = subprocess.run([node, DRAWER_SIM], cwd=ROOT, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "all admin dashboard checks passed" in proc.stdout


def test_the_needs_attention_endpoint_ships_every_queue_with_counts(client):
    login(client)
    r = client.get("/api/admin/needs-attention")
    assert r.status_code == 200, r.data
    body = r.get_json()
    for key in ("lowStock", "outOfStock", "supplierOutOfStock", "supplierLowStock"):
        assert key in body, f"the dashboard queue {key} is missing"
        assert isinstance(body[key], list)
        assert len(body[key]) <= 200, (
            f"{key} must stay paged server-side; a 900-row payload is how a "
            "dashboard refresh turns into a timeout")
    counts = body.get("counts") or {}
    for key in ("lowStock", "outOfStock", "supplierOutOfStock", "supplierLowStock"):
        assert key in counts, f"counts.{key} is missing (the bar needs a true total)"


def test_the_dashboard_renders_compact_bars_not_expanded_lists():
    admin = _read("js/admin.js")
    assert "attentionSummaryBar" in admin and "data-attention-bar" in admin, (
        "stock queues must render as compact summary bars")
    assert "Tap to view" in admin, "each bar must invite the tap"
    assert "attention-drawer" in admin and "openAttentionDrawer" in admin, (
        "the list must live in a drawer, not on the dashboard")
    assert "productsStateFromUrl" in admin and "attentionProductHref" in admin
    css = _read("css/style.css")
    for needle in (".attention-bar", ".attention-drawer", ".attention-drawer-row"):
        assert needle in css, f"{needle} styling is missing"


def test_a_low_stock_row_links_straight_into_the_editor():
    """The four queues must all be reachable as /admin/products?id=<ID>."""
    admin = _read("js/admin.js")
    assert 'return onAdminPath ? `/admin/products?${query}`' in admin
    assert 'const query = "id=" + encodeURIComponent(id);' in admin
    # And the products desk must honour that query when it loads.
    assert 'search.get("id")' in admin and 'editingId = wantedId' in admin


# =====================================================================
# d) Exact stock bounds
# =====================================================================
def test_the_stock_bound_cart_simulation_passes():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (cart sim needs Node >= 18)")
    proc = subprocess.run([node, CART_SIM], cwd=ROOT, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "all stock-bound checks passed" in proc.stdout


def _make_product(pid, stock):
    saved, _action, _mirrored = catalog_mod.upsert({
        "id": pid, "sku": pid.upper(), "slug": pid, "name": f"Bounds {pid}",
        "category": "beauty", "priceNgn": 2000, "stock": stock,
        "stock_quantity": stock, "online": True,
    }, "tester")
    assert saved is not None, f"product {pid} was rejected"


def _place(client, oid, pid, qty):
    execute("DELETE FROM rate_limits WHERE action='order'")
    body = {
        "id": oid, "currency": "NGN", "total": 2000 * qty,
        "customer": {"name": "Bounds Tester", "email": "bounds@example.com",
                     "phone": "+2348012345678", "city": "Lagos",
                     "zone": "Lagos Mainland", "address": "1 Test St"},
        "items": [{"id": pid, "name": "Bounds", "qty": qty, "price": 2000}],
    }
    return client.post("/api/orders", json=body,
                       headers={"X-CSRF-Token": csrf(client)})


def test_stock_n_accepts_n_and_refuses_n_plus_one(client):
    _make_product("jau-bound-exact", 5)
    over = _place(client, "JA-BOUND-OVER", "jau-bound-exact", 6)
    assert over.status_code == 409, over.data
    assert over.get_json()["error"] == "Only 5 items remaining in stock"
    assert over.get_json()["code"] == "insufficient_stock"

    exact = _place(client, "JA-BOUND-EXACT", "jau-bound-exact", 5)
    assert exact.status_code == 200, exact.get_json()
    assert exact.get_json()["ok"] is True

    gone = _place(client, "JA-BOUND-GONE", "jau-bound-exact", 1)
    assert gone.status_code == 409
    assert gone.get_json()["error"] == "Out of Stock"
    assert gone.get_json()["code"] == "out_of_stock"


def test_a_refused_order_never_moves_stock(client):
    _make_product("jau-bound-untouched", 3)
    before = int(catalog_mod.stock_of(next(
        p for p in catalog_mod.merged(include_hidden=True)
        if str(p.get("id")) == "jau-bound-untouched")))
    refused = _place(client, "JA-BOUND-NOPE", "jau-bound-untouched", 4)
    assert refused.status_code == 409
    after = int(catalog_mod.stock_of(next(
        p for p in catalog_mod.merged(include_hidden=True)
        if str(p.get("id")) == "jau-bound-untouched")))
    assert after == before == 3, "a refused order must reserve nothing"
