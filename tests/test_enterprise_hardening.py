"""Enterprise hardening anti-regression suite (owner request 2026-10-05).

One module, one job: fail the build the moment any of these four contracts
regresses. Each test pins behaviour, not formatting - the JS checks execute the
real js/store.js + js/app.js in the node vm harness the rest of the suite uses,
and the Python ones run the real Flask app / settings writer.

  a) Admin settings save (including the Welcome Pop-up toggle) returns HTTP 200
     every time - on a healthy table AND on a table that lacks the column.
  b) The PDP renders with zero supplier notice boxes / download fields / extra
     informational boxes.
  c) An incomplete checkout form redirects the shopper to the exact missing
     field (focus + smooth scroll + inline highlight).
  d) The checkout draft survives an accidental refresh and clears after an
     order, and the stock rules never cap a supplier count at one unit.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NODE = None
for _candidate in ("node", "nodejs"):
    from shutil import which
    NODE = which(_candidate)
    if NODE:
        break

_spec = importlib.util.spec_from_file_location("_photo_fix_harness",
                                               str(ROOT / "tests" / "test_photo_fix.py"))
_harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_harness)
_PRELUDE = _harness._PRELUDE


def _read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _node(script, timeout=180):
    if not NODE:
        raise AssertionError("node is required by this suite but is not on PATH")
    proc = subprocess.run([NODE, "-"], input=_PRELUDE + script, cwd=str(ROOT),
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(
            f"node harness exited {proc.returncode}\n--- stderr ---\n"
            f"{proc.stderr[-4000:]}\n--- stdout ---\n{proc.stdout[-2000:]}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("__RESULT__")]
    assert lines, f"harness emitted no result\nstdout:\n{proc.stdout[-2000:]}"
    return json.loads(lines[-1][len("__RESULT__"):])


# ===========================================================================
# a) Admin settings save - the popup toggle may never fail with a db error
# ===========================================================================
def test_admin_popup_toggle_answers_http_200_on_every_save(tmp_path, monkeypatch):
    """OFF then ON, each a 200 with the storefront reading the new value."""
    import app as appmod

    monkeypatch.setenv("SITE_CONFIG_PATH", str(tmp_path / "site.json"))
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as client:
        login = client.post("/api/admin/login", json={
            "email": "jaurastore@gmail.com",
            "password": os.environ["ADMIN_BOOTSTRAP_PASSWORD"],
        })
        assert login.status_code == 200
        token = login.get_json()["csrf"]
        for wanted in (False, False, True, True, False):
            saved = client.post("/api/admin/site",
                                headers={"X-CSRF-Token": token},
                                json={"popup_banner_active": wanted,
                                      "welcome_enabled": "1" if wanted else "0"})
            assert saved.status_code == 200, saved.data
            body = saved.get_json()
            assert body["ok"] is True and body["site"]["popup_banner_active"] is wanted
            served = client.get("/api/site").get_json()["site"]
            assert served["popup_banner_active"] is wanted


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    """A site_settings table whose update() honours the live column set."""

    def __init__(self, columns):
        self.columns = set(columns)
        self.rows = [{}, ]

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def update(self, payload):
        unknown = [k for k in payload if k not in self.columns]
        if unknown:
            raise Exception(
                f"PGRST204 Could not find the '{unknown[0]}' column of "
                f"'site_settings' in the schema cache")
        self.rows[0].update(payload)
        return self

    def insert(self, payload):
        self.rows[0].update({k: v for k, v in payload.items() if k in self.columns})
        return self

    def execute(self):
        return _FakeResult([dict(self.rows[0])])


class _FakeClient:
    def __init__(self, columns):
        self._table = _FakeTable(columns)

    def table(self, _name):
        return self._table


def test_popup_toggle_heals_a_missing_column_before_giving_up(monkeypatch):
    """The writer creates the column, then stores the boolean - no error."""
    import supabase_settings as ss
    import auto_migrate

    client = _FakeClient(["welcome_enabled", "bank_name"])
    monkeypatch.setattr(ss, "enabled", lambda: True)
    monkeypatch.setattr(ss, "client", lambda: client)
    monkeypatch.setattr(ss, "get_site_settings", lambda: {"popup_banner_active": True})
    called = {}

    def fake_heal(columns=None, logger=None):
        called["columns"] = list(columns or [])
        client._table.columns.add("popup_banner_active")
        return True

    monkeypatch.setattr(auto_migrate, "ensure_site_settings_columns", fake_heal)
    ss._HEAL_ATTEMPTED.clear()
    stored = ss.update_site_settings({"popup_banner_active": False,
                                      "welcome_enabled": "0"})
    assert called["columns"] == ["popup_banner_active"]
    assert stored["popup_banner_active"] is False
    assert client._table.rows[0]["popup_banner_active"] is False


def test_popup_toggle_still_saves_200_when_the_column_cannot_be_created(monkeypatch):
    """No database connection: the legacy text flag carries the toggle.

    The save must not raise (which is what produced the admin schema pop-up),
    and the storefront must still see the OFF the owner chose.
    """
    import supabase_settings as ss
    import auto_migrate

    client = _FakeClient(["welcome_enabled", "bank_name"])
    monkeypatch.setattr(ss, "enabled", lambda: True)
    monkeypatch.setattr(ss, "client", lambda: client)
    monkeypatch.setattr(auto_migrate, "ensure_site_settings_columns",
                        lambda columns=None, logger=None: False)
    ss._HEAL_ATTEMPTED.clear()
    stored = ss.update_site_settings({"popup_banner_active": False,
                                      "welcome_enabled": "0"})
    row = client._table.rows[0]
    assert row["welcome_enabled"] == "0"
    assert "popup_banner_active" not in row
    assert stored is not None


def test_popup_state_is_served_from_the_legacy_flag_when_the_column_is_absent(monkeypatch):
    """An Admin OFF must survive a table that only has welcome_enabled."""
    import supabase_settings as ss

    monkeypatch.setattr(ss, "enabled", lambda: True)
    monkeypatch.setattr(ss, "client", lambda: _LegacyRowClient())
    served = ss.get_site_settings()
    assert served["popup_banner_active"] is False, served

    class _OnRow(_LegacyRowClient):
        class _T(_LegacyRowClient._T):
            def execute(self):
                return _FakeResult([{"id": 1, "welcome_enabled": "1"}])

    monkeypatch.setattr(ss, "client", lambda: _OnRow())
    assert ss.get_site_settings()["popup_banner_active"] is True


class _LegacyRowClient:
    """A client whose id=1 row predates the boolean column."""

    class _T:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def execute(self):
            return _FakeResult([{"id": 1, "welcome_enabled": "0", "bank_name": "UBA"}])

    def table(self, _name):
        return self._T()


def test_boot_runs_the_schema_auto_migration():
    import auto_migrate

    app_py = _read("app.py")
    assert "import auto_migrate" in app_py and "auto_migrate.run(app.logger)" in app_py
    assert auto_migrate.statements()[0] == (
        "alter table site_settings add column if not exists "
        "popup_banner_active boolean not null default true")
    auto = _read("auto_migrate.py")
    assert "add column if not exists" in auto
    # The statement must be additive/idempotent-only: never a drop or rewrite.
    for forbidden in ("drop column", "drop table", "delete from", "truncate"):
        assert forbidden not in auto.lower()


def test_auto_migration_runs_the_owner_statement_through_a_direct_connection(monkeypatch):
    import auto_migrate

    ran = []

    def fake_psycopg(dsn, sql_list):
        ran.append((dsn, list(sql_list)))
        return True, ""

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://user:pw@db.example:5432/postgres")
    monkeypatch.setattr(auto_migrate, "_apply_with_psycopg", fake_psycopg)
    monkeypatch.setattr(auto_migrate.Config, "ENV", "production")
    auto_migrate.reset_for_tests()
    outcome = auto_migrate.run(columns=["popup_banner_active"])
    assert outcome["applied"] is True
    assert ran and ran[0][0].startswith("postgresql://")
    assert ran[0][1] == [
        "alter table site_settings add column if not exists "
        "popup_banner_active boolean not null default true"]


# ===========================================================================
# b) PDP renders cleanly: zero supplier notice boxes / download fields / boxes
# ===========================================================================
def test_pdp_source_has_no_supplier_notice_or_extra_boxes():
    app = _read("js", "app.js")
    i18n = _read("js", "i18n.js")
    css = _read("css", "style.css")
    for gone in ("supplierAvailabilityHTML", "pdp-supplier-availability",
                 "pdp-bulk", "pdp-info", "pdp-dims", "pdp.hint",
                 "additionalInfo", "Supplier-linked"):
        assert gone not in app, f"PDP still carries {gone!r}"
    assert "Supplier-linked availability" not in i18n
    assert "disponibilité auprès du fournisseur" not in i18n
    assert ".pdp-supplier-availability" not in css
    assert ".pdp-info" not in css


def test_rendered_pdp_is_minimal_and_has_no_supplier_or_download_ui():
    out = _node(r"""
loadApp();
const p = {
  id: "jau-pdp-clean", name: "Clean PDP Item", category: "beauty",
  priceNgn: 12000, stock: 4,
  supplierTracked: true, supplierSku: "https://supplier.example/secret",
  description: "Silky serum.", dimensions: "10 x 4 cm",
  additionalInfo: [{ title: "How to use", description: "Apply daily" }],
  bulkQty: 3, bulkPercent: 10, sku: "SKU-1",
  images: ["images/products/a.jpg", "/uploads/products/doc.pdf"],
  options: [], optionStock: {}, optionStockStatus: {},
};
const root = el("div");
sandbox.paintProduct(root, p);
emit({ html: root.innerHTML });
""")
    html = out["html"]
    lowered = html.lower()
    # No supplier notice, no supplier link, no download field anywhere.
    assert "supplier" not in lowered, html
    assert "download" not in lowered, html
    assert "https://supplier.example" not in html
    # No extra informational boxes (the additionalInfo block, the bulk advert,
    # the SKU hint, the dimensions line).
    for gone in ("pdp-info", "pdp-bulk", "pdp-dims", "bulk.label", "How to use"):
        assert gone not in html, f"{gone!r} survived in the PDP: {html}"
    # The core commerce blocks are all still there.
    for kept in ("<h1>", 'class="price"', "pdp-gallery", "pdp-thumbs",
                 "data-qty", "data-buy", "qty"):
        assert kept in html, f"{kept!r} missing from the PDP: {html}"


# ===========================================================================
# c) Incomplete checkout redirects to the exact missing field
# ===========================================================================
def test_uncompleted_checkout_redirects_to_the_first_missing_field():
    out = _node(r"""
loadApp();
let hash = "";
sandbox.history = { state: null, replaceState: (a, b, c) => { hash = c; } };
const controls = {}, hosts = {};
const VALUES = {
  firstName: "Ada", lastName: "Obi", country: "Nigeria",
  address: "12 Marina Street", city: "", zone: "Lagos Island",
  phone: "+2348012345678", email: "ada@example.com",
};
function makeField(name, value) {
  const host = el("div");
  const c = el("input");
  c.name = name; c.id = "ck-" + name; c.value = value;
  c.closest = () => host;
  host.appendChild(c);
  host.querySelector = (sel) => (sel === ".field-error"
    ? (host.children.find((x) => String(x.className || "").indexOf("field-error") >= 0) || null)
    : null);
  controls[name] = c; hosts[name] = host;
  return host;
}
const form = el("form");
Object.keys(VALUES).forEach((name) => form.appendChild(makeField(name, VALUES[name])));
form.querySelector = (sel) => {
  const m = /^\[name="([^"]+)"\]$/.exec(sel);
  return m ? (controls[m[1]] || null) : null;
};
form.querySelectorAll = (sel) => (sel === "[required]" ? Object.values(controls) : []);
sandbox.document.querySelector = () => null;
let scrolled = 0;
controls.city.scrollIntoView = () => { scrolled += 1; };
const res = sandbox.validateCheckoutForm(form, {});
const errorEl = hosts.city.children.find(
  (x) => String(x.className || "").indexOf("field-error") >= 0) || {};
emit({
  ok: res.ok,
  failures: res.failures.map((f) => f.name),
  scrolled,
  invalid: controls.city.getAttribute("aria-invalid"),
  hasError: hosts.city.classList.contains("has-error"),
  isMissing: hosts.city.classList.contains("is-missing"),
  message: errorEl.textContent || "",
  hash,
  othersUntouched: controls.firstName.getAttribute("aria-invalid"),
});
""")
    assert out["ok"] is False
    assert out["failures"] == ["city"], out
    assert out["scrolled"] == 1, "the missing field must be scrolled into view"
    assert out["invalid"] == "true"
    assert out["hasError"] is True
    assert out["isMissing"] is True
    assert "city" in out["message"].lower() or out["message"], out
    assert out["hash"] == "#ck-city"
    assert out["othersUntouched"] in (None, "false"), "only failing fields are flagged"


def test_checkout_source_keeps_the_redirect_and_highlight_contract():
    app = _read("js", "app.js")
    css = _read("css", "style.css")
    assert "scrollIntoView" in app and "block: \"center\"" in app
    assert "is-missing" in app and ".is-missing" in css
    assert "ck-missing-pulse" in css
    assert "aria-invalid" in app and "ck-inline-tooltip" in app


# ===========================================================================
# d) Checkout draft survives a refresh; stock is never capped at one unit
# ===========================================================================
def test_checkout_draft_saves_restores_and_clears():
    out = _node(r"""
loadApp();
const controls = {};
const VALUES = {
  firstName: "Ada", lastName: "Obi", country: "Nigeria",
  address: "12 Marina Street", zone: "Lagos Island",
  phone: "+2348012345678", email: "ada@example.com", note: "Leave at the gate",
};
const form = el("form");
Object.keys(VALUES).forEach((name) => {
  const c = el("input");
  c.name = name; c.id = "ck-" + name; c.value = VALUES[name];
  form.appendChild(c); controls[name] = c;
});
form.querySelector = (sel) => {
  const m = /^\[name="([^"]+)"\]$/.exec(sel);
  if (m) return controls[m[1]] || null;
  return null;
};
sandbox.saveCheckoutDraft(form);
// Simulate the accidental refresh: a brand-new empty form on the same device.
const fresh = el("form");
const freshControls = {};
Object.keys(VALUES).forEach((name) => {
  const c = el("input"); c.name = name; c.id = "ck-" + name; c.value = "";
  fresh.appendChild(c); freshControls[name] = c;
});
fresh.querySelector = (sel) => {
  const m = /^\[name="([^"]+)"\]$/.exec(sel);
  return m ? (freshControls[m[1]] || null) : null;
};
const restored = sandbox.restoreCheckoutDraft(fresh);
const draftKey = runInCtx("CHECKOUT_DRAFT_KEY");
sandbox.clearCheckoutDraft();
emit({
  restored,
  firstName: freshControls.firstName.value,
  zone: freshControls.zone.value,
  note: freshControls.note.value,
  key: draftKey,
  afterClear: sandbox.localStorage.getItem(draftKey),
});
""")
    assert out["restored"] is True
    assert out["firstName"] == "Ada"
    assert out["zone"] == "Lagos Island"
    assert out["note"] == "Leave at the gate"
    assert out["key"] == "jaura_checkout_draft"
    assert out["afterClear"] in (None, "null")
    app = _read("js", "app.js")
    assert "clearCheckoutDraft();" in app.split("const finishOrder = () => {", 1)[1][:120]


def test_supplier_counts_are_never_capped_at_one_unit():
    import supplier_watchdog as sw

    assert sw._apply_stock_rule(1, 0) == 1
    assert sw._apply_stock_rule(25, 0) == 25
    assert sw._apply_stock_rule(100, 3) == 100
    assert sw._apply_option_stock_rule(40, 0) == 40
    # A bare "In stock" (no count) opens a multi-unit shelf, never one unit.
    assert sw._apply_stock_rule(1, 0, flagged=True) == sw.SUPPLIER_IN_STOCK_UNITS
    assert sw.SUPPLIER_IN_STOCK_UNITS > 1
    assert sw._apply_stock_rule(0, 9) == 0
    # No 40%/50% buffer arithmetic may come back.
    source = _read("supplier_watchdog.py")
    assert "// 100" not in source.split("def _apply_stock_rule", 1)[1].split("def ", 1)[0]


# ===========================================================================
# e) Strict policy: the CI build fails when any of the above regresses
# ===========================================================================
def test_ci_fails_the_build_on_any_regression():
    ci = _read(".github", "workflows", "ci.yml")
    assert "python -m pytest tests/" in ci
    assert "--junitxml=pytest-results.xml" in ci
    assert "continue-on-error" not in ci
    assert "|| true" not in ci
    assert re.search(r"^on:\s*\n\s*push:", ci, re.M)
    # This anti-regression module is part of the full run the workflow executes.
    assert (ROOT / "tests" / "test_enterprise_hardening.py").exists()

# ===========================================================================
# f) Exact inventory boundaries + first paint is not blank
# ===========================================================================
def test_generic_stock_refusal_copy_is_gone_from_every_shipped_file():
    """The obsolete generic refusal must stay gone; exact remaining counts
    are now shown in the storefront and enforced by the API."""
    banned = "cannot order more than the available stock"
    shipped = ([f for f in (ROOT / "js").glob("*.js")]
               + [f for f in (ROOT / "css").glob("*.css")]
               + list(ROOT.glob("*.html"))
               + [ROOT / "api.py", ROOT / "catalog.py", ROOT / "sw.js"])
    for path in shipped:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        assert banned not in text, f"{path.relative_to(ROOT)} still carries the refusal"


def test_exact_inventory_allows_stock_and_rejects_stock_plus_one(tmp_path, monkeypatch):
    """End to end: stock is orderable exactly; stock plus one returns a 409."""
    import app as appmod
    import catalog as catalog_mod

    def make(pid, stock):
        catalog_mod.upsert({
            "id": pid, "sku": pid.upper(), "slug": pid, "name": pid,
            "category": "beauty", "priceNgn": 2000, "stock": stock,
            "stock_quantity": stock, "online": True,
        }, "tester")

    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as client:
        token = client.get("/api/config").get_json()["csrf"]
        for oid, pid, qty, stock in (("JA-HARD1", "jau-hard-shelf5", 3, 5),
                                     ("JA-HARD2", "jau-hard-shelf1", 4, 1)):
            make(pid, stock)
            response = client.post("/api/orders", json={
                "id": oid, "currency": "NGN", "total": 2000 * qty,
                "customer": {"name": "Buyer", "email": "buyer@example.com",
                             "phone": "+2348012345678", "city": "Lagos",
                             "zone": "Lagos Mainland", "address": "1 Test St"},
                "items": [{"id": pid, "name": "X", "qty": qty, "price": 2000}],
            }, headers={"X-CSRF-Token": token})
            expected_status = 200 if stock >= qty else 409
            assert response.status_code == expected_status, response.get_json()
            if stock < qty:
                assert response.get_json()["error"] == f"Only {stock} items remaining in stock"
        left = {str(p.get("id")): max(0, int(p.get("stock") or 0))
                for p in catalog_mod.merged(include_hidden=True)}
        assert left["jau-hard-shelf5"] == 2
        assert left["jau-hard-shelf1"] == 1, "the rejected order did not reserve stock"


def test_first_paint_markup_ships_with_the_html():
    """Requirement 5: the critical CSS, the static skeleton and the early
    catalogue request must be parseable BEFORE css/style.css - and store.js
    must consume that prefetched response instead of fetching again."""
    for name in ("index.html", "shop.html"):
        html = _read(name)
        style = html.index("<style>")
        fetch = html.index("__JA_CATALOG_FETCH__")
        sheet = html.index("css/style.css")
        assert style < sheet, f"{name}: the critical CSS must come first"
        assert fetch < sheet, f"{name}: the early catalogue fetch must come first"
        assert 'class="ja-sk"' in html, f"{name}: no static skeleton card"
        assert "ja-shimmer" in html, f"{name}: the skeleton has no shimmer"
        assert "prefers-reduced-motion:reduce" in html, \
            f"{name}: the shimmer ignores reduced motion"
    store = _read("js", "store.js")
    assert "__JA_CATALOG_FETCH__" in store, "store.js ignores the early fetch"


def test_shell_first_boot_paints_chrome_before_the_data_roundtrip():
    app_js = _read("js", "app.js")
    boot = app_js.split("async function boot()", 1)[1]
    chrome = boot.index("JA.mountChrome()")
    await_ready = boot.index("await catalogReady")
    assert chrome < await_ready, \
        "the page shell must paint before the catalogue roundtrip"
    # And exactly once: mountChrome() replaces the whole header/footer DOM, so
    # a second repaint when the catalogue lands swaps live nodes out from under
    # the customer (and flaked the CI header-geometry check at 390px).
    assert boot.count("JA.mountChrome()") == 1, \
        "the shell must be mounted once, not rebuilt after the catalogue"
