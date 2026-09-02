"""Backend security + behaviour tests. Run with:  python3 -m pytest tests -q

These cover the things that must never silently break: CSRF, no user
enumeration, brute-force lockout, HTML stripping, order retention and the
analytics counts the dashboard shows.
"""
import io, json, os, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

import app as appmod  # noqa: E402
import emailer  # noqa: E402
import auth as authmod  # noqa: E402
from db import execute, init_db, one, query  # noqa: E402

PW = "JauraStore2026x"
EMAIL = "jaurastore@gmail.com"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    authmod.ensure_seed_admins()
    authmod.set_password(EMAIL, PW)
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def csrf(client):
    return client.get("/api/config").get_json()["csrf"]


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


def test_healthz(client):
    assert client.get("/healthz").get_json()["ok"] is True


def test_security_headers(client):
    r = client.get("/")
    for h in ("X-Content-Type-Options", "X-Frame-Options", "Content-Security-Policy",
              "Referrer-Policy", "Permissions-Policy"):
        assert r.headers.get(h), h


def test_login_sets_session_and_unknown_email_is_identical(client):
    wrong_pw = client.post("/api/admin/login", json={"email": EMAIL, "password": "nope"})
    unknown = client.post("/api/admin/login", json={"email": "nobody@example.com", "password": "nope"})
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.get_json()["error"] == unknown.get_json()["error"]


def test_sole_admin_email_when_one_account(client):
    assert authmod.sole_admin_email() == EMAIL


def test_admin_login_is_password_only_with_single_account(client):
    """No email in the body: the server resolves the one admin account."""
    r = client.post("/api/admin/login", json={"password": PW})
    assert r.status_code == 200, r.data
    assert r.get_json()["email"] == EMAIL


def test_admin_login_with_multiple_accounts_asks_for_email(client, monkeypatch):
    double = ["one@example.com", "two@example.com"]
    monkeypatch.setattr("auth.Config.ADMIN_EMAILS", double)
    assert authmod.sole_admin_email() is None
    r = client.post("/api/admin/login", json={"password": PW})
    assert r.status_code == 400, r.data
    assert "email" in r.get_json()["error"].lower()


def test_two_devices_can_be_signed_in_at_once(client):
    """Per-device session cookies: two clients can stay signed in together."""
    a = client.post("/api/admin/login", json={"password": PW})
    b = client.post("/api/admin/login", json={"password": PW})
    assert a.status_code == b.status_code == 200
    assert client.get("/api/admin/analytics").status_code == 200


def test_brute_force_lockout(client):
    execute("DELETE FROM rate_limits")
    codes = [client.post("/api/admin/login", json={"email": EMAIL, "password": "bad" + str(i)}).status_code
             for i in range(8)]
    assert 429 in codes, codes


def test_no_registration_route(client):
    for path in ("/api/register", "/api/signup", "/api/admin/register"):
        assert client.post(path, json={}).status_code in (404, 405)


def test_admin_routes_need_a_session(client):
    assert client.get("/api/admin/analytics").status_code == 401
    assert client.get("/api/admin/orders").status_code == 401
    assert client.get("/api/admin/products").status_code in (401, 404, 405)


def test_csrf_required_on_every_write(client):
    assert client.post("/api/orders", json={"email": "a@b.com", "items": [{"id": "x"}]}).status_code == 403
    tok = csrf(client)
    assert client.post("/api/admin/products", json={"name": "x"},
                       headers={"X-CSRF-Token": tok}).status_code == 401  # token ok, but not signed in


def test_order_keeps_the_whole_form_and_strips_markup(client):
    tok = csrf(client)
    r = client.post("/api/orders", data={
        "order": json.dumps({
            "id": "JA-UNIT01",
            "currency": "CFA",
            "total": 15000,
            "customer": {
                "name": "<img src=x onerror=alert(1)>Ama",
                "email": "ama@example.com",
                "phone": "+229 90 00 00 00",
                "city": "Cotonou",
                "zone": "Cotonou",
                "address": "<script>alert('x')</script> Rue 12",
                "note": "javascript:alert(1)",
            },
            "items": [{"id": "wix-001", "name": "Bag", "qty": 1, "price": 15000}],
        })
    }, headers={"X-CSRF-Token": tok}, content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    row = one("SELECT * FROM orders WHERE id='JA-UNIT01'")
    payload = json.loads(row["payload"])
    assert "<" not in payload["customer"]["name"]
    assert "<script>" not in payload["customer"]["address"]
    assert payload["customer"]["note"] == "alert(1)"
    assert row["proof_url"] is not None          # column exists after migration
    assert row["status"] == "pending"


def test_replaying_an_order_never_duplicates_it(client):
    tok = csrf(client)
    body = {
        "id": "JA-UNIT02", "currency": "NGN", "total": 5000,
        "customer": {"name": "Re", "email": "re@example.com", "zone": "Lagos Mainland"},
        "items": [{"id": "wix-002", "name": "Cup", "qty": 1, "price": 5000}],
    }
    first = client.post("/api/orders", json=body, headers={"X-CSRF-Token": tok})
    again = client.post("/api/orders", json=body, headers={"X-CSRF-Token": tok})
    assert first.status_code == 200 and again.status_code == 200
    assert again.get_json().get("duplicate") is True
    assert one("SELECT COUNT(*) n FROM orders WHERE id='JA-UNIT02'")["n"] == 1


@pytest.mark.parametrize("zone", ["Pick Up", "pick up", "Pickup in store", "Self collect"])
def test_pickup_is_not_a_delivery_option(client, zone):
    tok = csrf(client)
    r = client.post("/api/orders", json={
        "id": "JA-UNIT03", "currency": "CFA", "total": 100,
        "customer": {"name": "x", "email": "x@example.com", "zone": zone},
        "items": [{"id": "wix-001", "name": "x", "qty": 1, "price": 100}],
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 400


def _post_min_order(client, oid, currency, total, zone):
    tok = csrf(client)
    return client.post("/api/orders", json={
        "id": oid, "currency": currency, "total": total,
        "customer": {"name": "Min Tester", "email": "min@example.com",
                     "phone": "+229 90 00 00 00", "city": "Cotonou", "zone": zone},
        "items": [{"id": "wix-001", "name": "Min item", "qty": 1, "price": total}],
    }, headers={"X-CSRF-Token": tok})


def test_benin_minimum_is_enforced_on_orders(client):
    r = _post_min_order(client, "JA-BJ1", "CFA", 4999, "Cotonou")
    assert r.status_code == 400, r.data
    assert "5,000 F CFA" in r.get_json()["error"]


def test_benin_minimum_in_naira_is_enforced(client):
    r = _post_min_order(client, "JA-BJ2", "NGN", 11399, "Calavi")
    assert r.status_code == 400, r.data
    assert "11,400" in r.get_json()["error"]


def test_benin_minimum_exact_cfa_and_ngn_are_accepted(client):
    a = _post_min_order(client, "JA-BJ3", "CFA", 5000, "Cotonou")
    b = _post_min_order(client, "JA-BJ4", "NGN", 11400, "Porto-Novo")
    assert a.status_code == 200, a.data
    assert b.status_code == 200, b.data


def test_lagos_orders_have_no_benin_minimum(client):
    r = _post_min_order(client, "JA-LAG", "NGN", 100, "Lagos Mainland")
    assert r.status_code == 200, r.data


def test_analytics_counts_and_dashboard_shape(client):
    tok = csrf(client)
    client.post("/api/track", json={"events": [
        {"type": "visit", "path": "/index.html", "page": "home", "sid": "s1"},
        {"type": "view", "path": "/product.html", "page": "product", "productId": "wix-007", "sid": "s1"},
        {"type": "cart", "productId": "wix-007", "sid": "s1"},
        {"type": "checkout_start", "sid": "s1", "page": "checkout"},
    ]}, headers={"X-CSRF-Token": tok})
    tok = login(client)
    data = client.get("/api/admin/analytics?days=7").get_json()
    assert data["totals"]["pageViews"] >= 1
    assert data["totals"]["visits"] >= 1
    assert data["totals"]["uniqueVisitors"] >= 1
    assert data["series"][-1]["day"]
    assert data["topPages"][0]["path"] == "/index.html"
    assert data["topProducts"][0]["productId"] == "wix-007"
    assert data["conversion"]["checkoutAttempts"] >= 1
    assert isinstance(data["conversion"]["averageOrderValue"], (int, float))


def test_analytics_is_private(client):
    assert client.get("/api/admin/analytics").status_code == 401


def test_categories_are_public_and_admin_writable(tmp_path, monkeypatch, client):
    import api as apimod
    monkeypatch.setattr(apimod, "CATEGORIES_FILE", str(tmp_path / "categories.json"))
    pub = client.get("/api/categories")
    assert pub.status_code == 200
    assert len(pub.get_json()["categories"]) >= 10

    assert client.put("/api/admin/categories", json={"categories": []}).status_code in (401, 403)
    tok = login(client)
    cats = pub.get_json()["categories"][:2]
    r = client.put("/api/admin/categories", json={"categories": cats},
                   headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert r.get_json()["count"] == len(cats)
    on_disk = json.load(open(str(tmp_path / "categories.json")))
    assert on_disk["categories"] and on_disk["updatedBy"] == EMAIL


def test_catalogue_round_trip_is_live_immediately(client):
    tok = login(client)
    r = client.post("/api/admin/products", json={"product": {
        "id": "jau-unit", "name": "<b>Unit</b> Bag", "category": "bags",
        "priceNgn": 10000, "image": "javascript:alert(1)", "stock": 5,
    }}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["product"]["name"] == "Unit Bag"
    assert body["product"]["image"] == ""
    assert body["product"]["priceCfa"] == 4400          # derived at 1 NGN = 0.44 CFA

    cat = client.get("/api/catalog").get_json()
    assert any(p["id"] == "jau-unit" for p in cat["products"])

    client.delete("/api/admin/products/jau-unit", headers={"X-CSRF-Token": tok})
    cat = client.get("/api/catalog").get_json()
    assert not any(p["id"] == "jau-unit" for p in cat["products"])


def test_upload_rejects_files_that_are_not_images(client):
    tok = csrf(client)
    r = client.post("/api/uploads/proof", data={"file": (io.BytesIO(b"MZ fake exe"), "x.jpg")},
                    headers={"X-CSRF-Token": tok}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_public_order_lookup_returns_status_only(client):
    ok = client.get("/api/orders/JA-UNIT01").get_json()
    assert ok["order"]["id"] == "JA-UNIT01"
    assert "email" not in json.dumps(ok)
    assert client.get("/api/orders/JA-NOPE9").status_code == 404


def test_password_change_needs_the_current_password(client):
    tok = login(client)
    bad = client.post("/api/admin/password",
                      json={"currentPassword": "wrong", "newPassword": "AnotherPass1"},
                      headers={"X-CSRF-Token": tok})
    assert bad.status_code == 403
    weak = client.post("/api/admin/password",
                       json={"currentPassword": PW, "newPassword": "short"},
                       headers={"X-CSRF-Token": tok})
    assert weak.status_code == 400


# --------------------------------------------------------- payment receipts

PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n"
       b"trailer<</Root 1 0 R>>\n%%EOF\n")


def proof_fields(**over):
    f = {
        "name": "Grace Mensah", "phone": "+229 97 00 11 22",
        "email": "grace@example.com", "orderId": "JA-TEST01",
        "items": "2x Valentino bag", "quantity": "2",
        "amount": "FCFA 25 000", "method": "MTN MoMo Benin (F CFA)",
    }
    f.update(over)
    return f


def post_proof(client, data, filename, **over):
    return client.post(
        "/api/payment-proof",
        data={**proof_fields(**over),
              "file": (io.BytesIO(data), filename)},
        headers={"X-CSRF-Token": csrf(client)},
        content_type="multipart/form-data")


def test_pdf_receipt_is_accepted_and_stored(client):
    r = post_proof(client, PDF, "receipt.pdf")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True and body["size"] == len(PDF)
    row = one("SELECT * FROM payment_proofs ORDER BY id DESC LIMIT 1")
    assert dict(row)["mime"] == "application/pdf"
    assert dict(row)["order_id"] == "JA-TEST01"


def test_jpeg_receipt_is_accepted(client):
    assert post_proof(client, b"\xff\xd8\xff\xe0" + b"\x00" * 400, "r.jpg").status_code == 200


def test_renamed_executable_is_rejected(client):
    r = post_proof(client, b"MZ\x90\x00" + b"\x00" * 500, "receipt.pdf")
    assert r.status_code == 400
    assert "JPG" in r.get_json()["error"]


def test_oversized_receipt_is_rejected(client):
    body = b"%PDF-1.4\n" + (b"A" * (8 * 1024 * 1024 + 10)) + b"\n%%EOF\n"
    r = post_proof(client, body, "big.pdf")
    assert r.status_code == 400
    assert "MB" in r.get_json()["error"]


def test_truncated_pdf_is_rejected(client):
    r = post_proof(client, b"%PDF-1.4\n" + b"B" * 500, "cut.pdf")
    assert r.status_code == 400


def test_payment_proof_needs_csrf(client):
    r = client.post("/api/payment-proof",
                    data={**proof_fields(),
                          "file": (io.BytesIO(PDF), "r.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 403


def test_payment_proof_keeps_no_markup(client):
    r = post_proof(client, PDF, "r.pdf",
                   name="<script>alert(1)</script>Grace",
                   items="<img src=x onerror=alert(1)>bag")
    assert r.status_code == 200
    row = dict(one("SELECT * FROM payment_proofs ORDER BY id DESC LIMIT 1"))
    assert "<script" not in row["name"] and "onerror" not in (row["items"] or "")
    assert "Grace" in row["name"]


def test_receipts_are_private_to_the_admin(client):
    post_proof(client, PDF, "r.pdf")
    assert client.get("/api/admin/payment-proofs").status_code in (401, 403)
    login(client)
    body = client.get("/api/admin/payment-proofs").get_json()
    assert body["proofs"] and body["proofs"][0]["order_id"] == "JA-TEST01"


def test_payment_methods_are_public(client):
    body = client.get("/api/payment-methods").get_json()
    assert body["ok"] is True and len(body["methods"]) >= 2


# ------------------------------------------------- the mail really goes out

def test_payment_receipt_is_emailed_with_the_original_file_attached(client, monkeypatch):
    """End-to-end proof: upload -> SMTP -> the bytes in the inbox are identical."""
    import email as emailmod
    from email import policy as epolicy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mail_sink import MailSink
    import config

    with MailSink() as sink:
        monkeypatch.setattr(config.Config, "MAIL_MODE", "smtp")
        monkeypatch.setattr(config.Config, "MAIL_FROM", "jaurastore@gmail.com")
        monkeypatch.setattr(config.Config, "SMTP_HOST", sink.host)
        monkeypatch.setattr(config.Config, "SMTP_PORT", sink.port)
        monkeypatch.setattr(config.Config, "SMTP_USER", "")
        monkeypatch.setattr(config.Config, "SMTP_PASS", "")

        r = post_proof(client, PDF, "receipt.pdf", orderId="JA-DELIVER1")
        assert r.status_code == 200, r.data
        assert r.get_json()["emailed"] is True, r.get_json()

        assert sink.messages, "the app said it sent, but nothing reached the server"
        msg = emailmod.message_from_bytes(sink.messages[0]["data"], policy=epolicy.default)
        assert "jaurastore@gmail.com" in sink.messages[0]["to"][0]
        assert "JA-DELIVER1" in msg["Subject"]

        atts = list(msg.iter_attachments())
        assert atts, "no attachment on the receipt email"
        assert atts[0].get_payload(decode=True) == PDF, "the attached file was altered"
        assert atts[0].get_content_type() == "application/pdf"

        body = msg.get_body(preferencelist=("plain",)).get_content()
        for field in ("Grace Mensah", "+229 97 00 11 22", "grace@example.com",
                      "JA-DELIVER1", "2x Valentino bag", "Quantity",
                      "MTN MoMo Benin (F CFA)"):
            assert field in body, f"{field!r} missing from the receipt email"

        # the customer gets a confirmation, and it carries no attachment
        assert len(sink.messages) == 2
        confirm = emailmod.message_from_bytes(sink.messages[1]["data"], policy=epolicy.default)
        assert "grace@example.com" in sink.messages[1]["to"][0]
        assert list(confirm.iter_attachments()) == []


# ------------------------------------------- products must stay saved

def test_saved_product_is_still_there_after_a_fresh_read(client):
    """The acceptance test: save a product, read the catalogue again, it is there."""
    tok = login(client)
    r = client.post("/api/admin/products",
                    json={"product": {"name": "Stays Saved Bag", "priceNgn": 9000,
                                      "category": "bags", "stock": 5}},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    pid = r.get_json()["product"]["id"]

    cat = client.get("/api/catalog").get_json()
    assert any(p["id"] == pid for p in cat["products"]), "not in /api/catalog after saving"

    # as if the server had restarted: read the file from disk with no cache
    import catalog as catalog_mod
    on_disk = catalog_mod.overrides()["products"]
    assert any(str(p.get("id")) == pid for p in on_disk), "not in data/catalog.json"

    # and still there after another request, and after a second save
    client.post("/api/admin/products",
                json={"product": {"name": "Second Saved Bag", "priceNgn": 4000}},
                headers={"X-CSRF-Token": tok})
    cat = client.get("/api/catalog").get_json()
    names = {p["name"] for p in cat["products"]}
    assert "Stays Saved Bag" in names and "Second Saved Bag" in names

    client.delete(f"/api/admin/products/{pid}", headers={"X-CSRF-Token": tok})
    client.delete("/api/admin/products/" + next(
        p["id"] for p in client.get("/api/catalog").get_json()["products"]
        if p["name"] == "Second Saved Bag"), headers={"X-CSRF-Token": tok})


def test_concurrent_saves_from_several_processes_lose_nothing(client):
    """Two gunicorn workers saving at once used to erase each other's product."""
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "race_check.py")
    env = dict(os.environ,
               PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # race_check.py builds its own temp folder, so it never touches this DB
    out = subprocess.run([sys.executable, script, "4", "8"], env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "lost 0" in out.stdout, out.stdout


def test_a_corrupt_catalog_file_falls_back_to_the_backup(tmp_path, monkeypatch):
    """A damaged file must never silently empty the shop."""
    import catalog as catalog_mod
    target = str(tmp_path / "catalog.json")
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", target)

    catalog_mod.upsert({"id": "jau-backup-test", "name": "Backup Bag", "priceNgn": 1000})
    shutil.copy2(target, target + ".bak")
    with open(target, "w") as fh:
        fh.write("{ this is not json")           # as if the server died mid-write

    data = catalog_mod.overrides()
    assert any(str(p.get("id")) == "jau-backup-test" for p in data["products"]), \
        "the catalogue went empty instead of falling back to the backup"


def test_the_catalogue_path_is_configurable():
    """On a host with an ephemeral disk this must point at the volume."""
    import catalog as catalog_mod
    from config import Config
    assert catalog_mod.CATALOG_FILE == Config.CATALOG_PATH
    assert hasattr(Config, "CATALOG_PATH"), \
        "CATALOG_PATH missing: admin products would reset on every deploy"


# ------------------------------------------- Supabase product fetching query

class _FakeSupabaseQuery:
    """Chainable stand-in for the supabase-py select/order/range builder.

    ``cap`` simulates a PostgREST ``max-rows`` limit: the server silently
    truncates any response longer than that, exactly like the real API.
    """

    def __init__(self, rows, calls, cap=None):
        self._rows = rows
        self._calls = calls
        self._cap = cap
        self._count = None
        self._start = 0
        self._end = -1

    def select(self, _cols, count=None):
        self._count = count
        return self

    def order(self, _col, **_kw):
        return self

    def range(self, start, end):
        self._start, self._end = start, end
        return self

    def execute(self):
        self._calls.append((self._start, self._end))
        window = self._rows[self._start:self._end + 1]
        if self._cap is not None:
            window = window[:self._cap]      # the server-side truncation
        res = type("Res", (), {"data": window})()
        if self._count == "exact":
            res.count = len(self._rows)
        return res


class _FakeSupabaseTable:
    def __init__(self, rows, calls, cap=None):
        # sorted the way a PostgREST `.order("id")` would return them, so
        # page windows behave exactly like the real API
        self._rows = sorted(rows, key=lambda r: str(r.get("id") or ""))
        self._calls = calls
        self._cap = cap

    def select(self, cols, count=None):
        return _FakeSupabaseQuery(self._rows, self._calls, self._cap).select(cols, count)


class _FakeSupabaseClient:
    def __init__(self, rows, cap=None):
        self.calls = []
        self._rows = rows
        self._cap = cap

    def table(self, _name):
        return _FakeSupabaseTable(self._rows, self.calls, self._cap)


def _supabase_style_rows():
    """The live situation that broke the shop: all 258 catalogue products in
    the Supabase table, mixed sources (only some marked 'admin'), plus
    tombstone rows from deletes / bulk imports."""
    import catalog as catalog_mod
    rows = []
    for i, p in enumerate(catalog_mod._seed_products()):
        row = {k: v for k, v in p.items()}
        row["id"] = f"sb-{i:03d}"               # ids differ from the seed
        row["source"] = "admin" if i % 5 else ("seed" if i % 2 else None)
        rows.append(row)
    rows.append({"id": "sb-tomb-1", "slug": "deleted-piece", "source": "deleted"})
    rows.append({"id": "sb-tomb-2", "slug": "replaced-piece", "source": "replaced"})
    return rows


def test_supabase_query_reads_every_live_row_and_pages(monkeypatch):
    """"The fetching query must not cap or filter the catalogue:
    every source except tombstones comes back, and the read pages through
    the table so a PostgREST max-rows limit can never truncate it."""
    import catalog as catalog_mod
    import supabase_store
    n = len(catalog_mod._seed_products())
    rows = _supabase_style_rows()
    fake = _FakeSupabaseClient(rows)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(supabase_store, "PAGE_SIZE", 100)

    out = supabase_store.products_table_rows()

    assert out is not None
    assert len(out) == n, f"expected all {n} live rows, got {len(out)}"
    slugs = [p["slug"] for p in out]
    assert len(slugs) == len(set(slugs)), "the same product came back twice"
    assert "deleted-piece" not in slugs and "replaced-piece" not in slugs
    # paged: (n + tombstones) / 100 per page -> at least two windows
    assert len(fake.calls) >= 2, f"expected multiple pages, saw {fake.calls}"
    assert fake.calls[0] == (0, 99) and fake.calls[-1] == (200, 299)


def test_supabase_query_never_caps_at_a_single_page(monkeypatch):
    """A 500-row first page must not be the end: the loop keeps asking for
    the next window until the table runs dry."""
    import supabase_store
    import catalog as catalog_mod
    rows = _supabase_style_rows()[:len(catalog_mod._seed_products())]
    rows += [{"id": f"sb-x-{i:03d}", "slug": f"extra-piece-{i}", "source": "admin"}
             for i in range(300)]
    fake = _FakeSupabaseClient(rows)
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(supabase_store, "PAGE_SIZE", 250)

    out = supabase_store.products_table_rows()
    assert len(out) == len(catalog_mod._seed_products()) + 300, f"expected all live + extra rows, got {len(out)}"
    assert len(fake.calls) == 3, f"expected 3 pages, saw {fake.calls}"


def test_supabase_query_survives_a_server_side_row_cap(monkeypatch):
    """The regression behind the shop capping at 240 items: a PostgREST
    max-rows limit silently truncates oversized responses. A short page that
    still has rows left must shrink the window and keep walking, not stop."""
    import supabase_store
    import catalog as catalog_mod
    n = len(catalog_mod._seed_products())
    rows = _supabase_style_rows()[:n]      # n live products, no tombstones
    fake = _FakeSupabaseClient(rows, cap=240)  # the server caps at 240 rows
    monkeypatch.setattr(supabase_store, "client", lambda: fake)
    monkeypatch.setattr(supabase_store, "PAGE_SIZE", 500)

    out = supabase_store.products_table_rows()
    assert len(out) == n, f"a 240-row server cap truncated the catalogue to {len(out)}"
    # page 1 is cut at 240; the walk must continue past it
    assert len(fake.calls) >= 2, f"pagination stopped after one page: {fake.calls}"
    assert fake.calls[0] == (0, 499)         # asked for the full window
    assert fake.calls[1][0] == 240           # resumed where the server stopped


def test_merged_catalogue_has_no_duplicates_when_supabase_ids_differ(monkeypatch):
    """The bug shoppers saw: the same product in Supabase under a fresh id
    was unioned next to its seed copy and rendered twice. merged() must keep
    one copy of each product (matched by id, then slug, then sku), keeping
    the live Supabase version."""
    import catalog as catalog_mod
    seed = catalog_mod._seed_products()
    sb = []
    for i, p in enumerate(seed):
        row = dict(p)
        row["id"] = f"sb-{i:03d}"               # same product, different id
        row["name"] = p["name"] + " (live)"     # prove the Supabase copy wins
        sb.append(row)
    sb.append({"id": "sb-new", "sku": "JAUNEW", "slug": "only-in-supabase",
               "name": "Only in Supabase", "category": "bags", "priceNgn": 1000})
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: sb)
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [], "deleted": []})

    merged = catalog_mod.merged()

    slugs = [p["slug"] for p in merged]
    assert len(merged) == len(seed) + 1, \
        f"expected {len(seed) + 1} unique products, got {len(merged)}"
    assert len(slugs) == len(set(slugs)), "a product appears twice in the catalogue"
    assert all("(live)" in p["name"] for p in merged
               if p["id"].startswith("sb-") and p["id"] != "sb-new"), \
        "the stale seed copy won over the live Supabase row"
    assert not any(p["id"].startswith("wix-") for p in merged), \
        "seed duplicates of Supabase products were not replaced"


def test_merged_catalogue_applies_deleted_ids_with_supabase(monkeypatch):
    """A product the admin deleted must not come back to life just because
    Supabase is the source of truth (the deleted list used to be ignored)."""
    import catalog as catalog_mod
    seed = catalog_mod._seed_products()
    sb = [dict(p) for p in seed]                # same ids as the seed
    target = seed[0]["id"]
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: sb)
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [], "deleted": [target]})

    merged = catalog_mod.merged()
    ids = [p["id"] for p in merged]
    assert target not in ids, "a soft-deleted product was resurrected"
    assert len(merged) == len(seed) - 1


def test_offline_outbox_survives_a_page_refresh(client):
    """js/net.js must keep a queued save somewhere that outlives the page.

    IndexedDB is missing in Safari private mode and some in-app browsers, so
    the outbox has a localStorage mirror. If neither existed, a product saved
    offline would vanish on refresh.
    """
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "js", "net.js")).read()
    assert "lsPut" in src and "lsDelete" in src, "no localStorage mirror in js/net.js"
    assert 'localStorage.getItem(LS_KEY)' in src or "lsAll()" in src
    # the mirror must be read back when the page loads
    boot = src[src.index("function boot()"):src.index("function paintPill()")]
    assert "lsAll" in boot, "boot() does not restore the localStorage outbox"


# ------------------------------------------- confirming from the email link

def _make_order(client, oid="JA-EMAILCONF", email="customer@example.com"):
    """Create an order the way the browser does, clearing the rate limit."""
    from db import execute
    execute("DELETE FROM rate_limits WHERE action='order'")
    r = client.post("/api/orders", headers={"X-CSRF-Token": csrf(client)}, json={
        "id": oid, "currency": "CFA", "total": 9000, "status": "pending",
        "customer": {"name": "Confirm Tester", "phone": "+229 90 00 00 00",
                     "email": email, "city": "Cotonou", "zone": "Cotonou",
                     "address": "Rue 5"},
        "items": [{"id": "wix-001", "name": "Bag", "qty": 1, "price": 9000}],
    })
    assert r.status_code == 200, r.data
    return oid


def test_email_confirm_link_confirms_the_order(client, monkeypatch):
    import security
    oid = _make_order(client)
    token = security.order_token(oid, "confirm")
    r = client.get(f"/api/orders/{oid}/confirm?action=confirm&token={token}")
    assert r.status_code == 200, r.data
    assert r.get_json()["status"] == "confirmed"
    from db import one
    assert one("SELECT status FROM orders WHERE id=?", (oid,))["status"] == "confirmed"


def test_email_decline_link_declines_the_order(client):
    import security
    oid = _make_order(client, "JA-EMAILDEC")
    token = security.order_token(oid, "decline")
    r = client.get(f"/api/orders/{oid}/confirm?action=decline&token={token}")
    assert r.status_code == 200 and r.get_json()["status"] == "declined"


def test_confirm_link_rejects_forged_and_mismatched_tokens(client):
    import security
    oid = _make_order(client, "JA-EMAILBAD")
    assert client.get(f"/api/orders/{oid}/confirm?action=confirm&token={'0'*40}").status_code == 403
    good = security.order_token(oid, "confirm")
    # the same token must not work for a different action or a different order
    assert client.get(f"/api/orders/{oid}/confirm?action=decline&token={good}").status_code == 403
    assert client.get(f"/api/orders/JA-OTHER/confirm?action=confirm&token={good}").status_code == 403
    assert client.get(f"/api/orders/{oid}/confirm?action=delete&token={good}").status_code == 400


def test_confirming_by_email_tells_the_customer(client, monkeypatch):
    """The confirmation the shop triggers must reach the customer, for real."""
    import email as emailmod
    from email import policy as epolicy
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    from mail_sink import MailSink
    import config, security

    oid = _make_order(client, "JA-EMAILCUST", email="customer@example.com")
    with MailSink() as sink:
        monkeypatch.setattr(config.Config, "MAIL_MODE", "smtp")
        monkeypatch.setattr(config.Config, "SMTP_HOST", sink.host)
        monkeypatch.setattr(config.Config, "SMTP_PORT", sink.port)
        monkeypatch.setattr(config.Config, "SMTP_USER", "")
        monkeypatch.setattr(config.Config, "SMTP_PASS", "")
        r = client.get(f"/api/orders/{oid}/confirm?action=confirm"
                       f"&token={security.order_token(oid, 'confirm')}")
    assert r.get_json().get("customerEmailed") is True, r.get_json()
    assert len(sink.messages) == 1
    msg = emailmod.message_from_bytes(sink.messages[0]["data"], policy=epolicy.default)
    assert "customer@example.com" in sink.messages[0]["to"][0]
    assert "confirmed" in msg["Subject"].lower()
    assert oid in msg.get_body(preferencelist=("plain",)).get_content()


def test_shop_emails_reply_to_the_customer_not_the_shop(client, monkeypatch):
    """The shop's own address is sender AND recipient, so without Reply-To a
    reply would come straight back to the shop instead of the customer."""
    import email as emailmod
    from email import policy as epolicy
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    from mail_sink import MailSink
    import config

    pdf = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "receipt.pdf"), "rb").read()
    order = {
        "id": "JA-REPLYTO", "currency": "CFA", "total": 1000,
        "customer": {"name": "Reply Tester", "email": "customer@example.com",
                     "phone": "+229 90 00 00 00", "city": "Cotonou"},
        "items": [{"qty": 1, "name": "Bag", "price": 1000}],
    }
    with MailSink() as sink:
        monkeypatch.setattr(config.Config, "MAIL_MODE", "smtp")
        monkeypatch.setattr(config.Config, "MAIL_FROM", "jaurastore@gmail.com")
        monkeypatch.setattr(config.Config, "SMTP_HOST", sink.host)
        monkeypatch.setattr(config.Config, "SMTP_PORT", sink.port)
        monkeypatch.setattr(config.Config, "SMTP_USER", "")
        monkeypatch.setattr(config.Config, "SMTP_PASS", "")
        emailer.send_order_notice(order, pdf, "receipt.pdf", "application/pdf")
        emailer.send_payment_proof(
            {"name": "Reply Tester", "phone": "+229 90 00 00 00",
             "email": "customer@example.com", "orderId": "JA-REPLYTO",
             "items": "1x Bag", "quantity": "1", "method": "MTN MoMo",
             "amount": "1000", "currency": "CFA", "note": "", "at": "2026-01-01T00:00:00"},
            pdf, "receipt.pdf", "application/pdf")

    shop_mails = [m for m in sink.messages if "jaurastore@gmail.com" in m["to"][0]]
    assert len(shop_mails) == 2
    for raw in shop_mails:
        msg = emailmod.message_from_bytes(raw["data"], policy=epolicy.default)
        assert msg["Reply-To"] == "customer@example.com", msg["Subject"]
        assert "Reply to this email" in msg.get_body(preferencelist=("plain",)).get_content()


def test_the_seed_products_endpoint_serves_the_catalogue(client):
    """It used to raise 500: the app is built with static_folder=None, so
    send_static_file() could never work. The store's front end does not call
    this route, which is why it stayed broken."""
    r = client.get("/api/products")
    assert r.status_code == 200, r.status_code
    body = r.get_json()
    assert body["ok"] is True
    assert len(body["products"]) > 100
    assert all("name" in p for p in body["products"][:20])


# ------------------------------------------- no Wix / third-party references

def test_no_product_references_an_external_image_host(client):
    """No Wix / third-party photo may be served: every product's image must be
    a repository-relative asset or the committed branded placeholder."""
    cat = client.get("/api/catalog").get_json()["products"]
    assert len(cat) > 100
    for p in cat:
        img = p.get("image") or ""
        assert not img.startswith(("http://", "https://", "data:", "blob:")), \
            f"{p.get('id')} still points at an external image: {img}"
        assert "imageUrl" not in p, f"{p.get('id')} still carries an imageUrl"


def test_genuinely_local_products_keep_their_repo_image(client):
    """A product whose repo photo exists keeps it; only missing ones fall back."""
    import catalog as catalog_mod
    merged = catalog_mod.resolve_images(catalog_mod.merged(include_hidden=True))
    local = [p for p in merged if (p.get("image") or "").startswith("images/products/")]
    assert local, "expected at least some products to use a committed repo photo"


def test_seed_file_and_snapshot_contain_no_external_image_urls():
    """Both the canonical seed and the static snapshot must be Wix-free."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in (os.path.join(root, "data", "seed.json"),
                 os.path.join(root, "js", "products-data.js")):
        raw = open(path, encoding="utf-8").read()
        assert "wixstatic" not in raw, f"{path} still references the Wix CDN"


# ------------------------------------------- shared admin password

def test_shared_password_applies_to_every_admin(client):
    """New password set once must work for any admin account (multi-user)."""
    import auth as authmod
    assert authmod.set_shared_password("Shared2026x") is True
    assert authmod.verify_login(EMAIL, "Shared2026x") is True
    assert authmod.verify_login(EMAIL, PW) is False
    assert authmod.verify_login("not-an-admin@example.com", "Shared2026x") is False
    authmod.set_shared_password(PW)   # restore for the rest of the suite


def test_password_change_route_updates_the_shared_password(client):
    """The admin 'Change shared password' flow sets one password for all."""
    import auth as authmod
    tok = login(client)
    r = client.post("/api/admin/password",
                    json={"currentPassword": PW, "newPassword": "Another2026x"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert authmod.verify_login(EMAIL, "Another2026x") is True
    assert authmod.verify_login(EMAIL, PW) is False
    authmod.set_shared_password(PW)   # restore


# ------------------------------------------- Supabase + GitHub dual-sync

def test_sync_status_endpoint_is_admin_only(client):
    assert client.get("/api/admin/sync/status").status_code in (401, 403)
    login(client)
    body = client.get("/api/admin/sync/status").get_json()
    assert body["ok"] is True
    assert "supabase" in body and "gitRepo" in body and "onWrite" in body


def test_repo_sync_dry_run_reports_a_complete_catalogue():
    import repo_sync
    ok, report = repo_sync._check()
    assert ok is True
    assert report["productsInCatalogue"] >= 100
    assert report["pushConfigured"] is False or report["gitRepo"]


def test_catalog_json_repo_copy_is_refreshed_from_overrides(tmp_path, monkeypatch, client):
    """After an admin write, repo_sync regenerates js/products-data.js and the
    repo catalog.json from the live catalogue (no product loss).

    REPO_ROOT is pointed at a temp folder so the test never mutates the real
    repository data files.
    """
    import catalog as catalog_mod, repo_sync
    monkeypatch.setattr(repo_sync, "REPO_ROOT", str(tmp_path))
    login(client)
    catalog_mod.upsert({"id": "jau-sync-bag", "name": "Sync Bag", "priceNgn": 5000}, "tester")
    catalog_mod.upsert({"id": "jau-sync-shoe", "name": "Sync Shoe", "priceNgn": 3000}, "tester")
    ok, report = repo_sync.regenerate(commit=False, push=False)
    assert ok is True
    assert report.get("js/products-data.js") is not None
    snapshot = open(os.path.join(str(tmp_path), "js", "products-data.js"),
                    encoding="utf-8").read()
    assert "Sync Bag" in snapshot and "Sync Shoe" in snapshot
    catalog_mod.remove("jau-sync-bag", "tester")
    catalog_mod.remove("jau-sync-shoe", "tester")


# ===================================================== admin panel rebuild
os.environ.setdefault("SITE_CONFIG_PATH", "/tmp/jaura_test_site.json")


def test_bulk_upload_endpoint_is_gone(client):
    """The admin bulk-upload feature was removed entirely."""
    tok = login(client)
    r = client.post("/api/admin/bulk-upload",
                    data={"csv": "name,category\nX,bags"},
                    headers={"X-CSRF-Token": tok})
    # 404/405: the API route no longer exists (405 = only the static-file
    # catch-all GET route matches this URL now)
    assert r.status_code in (404, 405)


def test_delivery_tracking_is_removed(client):
    """There is no delivery-status tracking: 'delivered' must be rejected."""
    oid = _make_order(client, "JA-DELIV1")
    tok = login(client)
    r = client.patch(f"/api/admin/orders/{oid}", json={"status": "delivered"},
                     headers={"X-CSRF-Token": tok})
    assert r.status_code == 400
    assert one("SELECT status FROM orders WHERE id=?", (oid,))["status"] == "pending"
    # the admin panel offers no delivered button / filter either
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "js", "admin.js")).read()
    assert "delivered" not in src
    # confirmed / pending / declined still work
    r = client.patch(f"/api/admin/orders/{oid}", json={"status": "confirmed"},
                     headers={"X-CSRF-Token": tok})
    assert r.status_code == 200 and r.get_json()["status"] == "confirmed"
    r = client.patch(f"/api/admin/orders/{oid}", json={"status": "pending"},
                     headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    r = client.patch(f"/api/admin/orders/{oid}", json={"status": "shipped-to-mars"},
                     headers={"X-CSRF-Token": tok})
    assert r.status_code == 400


def test_site_config_public_read_and_admin_write(client):
    if os.path.exists("/tmp/jaura_test_site.json"):
        os.remove("/tmp/jaura_test_site.json")
    # public read works logged out and starts empty
    r = client.get("/api/site")
    assert r.status_code == 200
    assert r.get_json()["site"]["heroVideo"] == ""
    # writing needs an admin session
    assert client.post("/api/admin/site", json={"heroVideo": "/uploads/videos/x.mp4"},
                       headers={"X-CSRF-Token": csrf(client)}).status_code == 401
    tok = login(client)
    r = client.post("/api/admin/site", json={"heroVideo": "/uploads/videos/x.mp4"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert client.get("/api/site").get_json()["site"]["heroVideo"] == "/uploads/videos/x.mp4"
    # dangerous schemes are stripped, and clearing works
    client.post("/api/admin/site", json={"heroVideo": "javascript:alert(1)"},
                headers={"X-CSRF-Token": tok})
    assert client.get("/api/site").get_json()["site"]["heroVideo"] == ""


def test_hero_video_upload_validates_real_bytes(client):
    tok = login(client)
    # a renamed exe is refused
    r = client.post("/api/admin/uploads/video",
                    data={"file": (io.BytesIO(b"MZ\x90\x00" + b"\x00" * 200), "movie.mp4")},
                    headers={"X-CSRF-Token": tok},
                    content_type="multipart/form-data")
    assert r.status_code == 400
    # a real MP4 header is accepted and stored under /uploads/
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 400
    r = client.post("/api/admin/uploads/video",
                    data={"file": (io.BytesIO(mp4), "hero.mp4")},
                    headers={"X-CSRF-Token": tok},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    url = r.get_json()["url"]
    assert url.startswith("/uploads/") and url.endswith(".mp4")
    # the stored video is served back
    assert client.get(url).status_code == 200


def test_option_stock_survives_product_save(client):
    import catalog as catalog_mod
    tok = login(client)
    r = client.post("/api/admin/products", headers={"X-CSRF-Token": tok}, json={
        "id": "jau-optstock", "name": "Option Stock Tee", "priceNgn": 4000,
        "category": "fashion", "stock": 7,
        "options": [{"title": "Size", "type": "DROP_DOWN", "values": ["S", "M", "L"]}],
        "optionStock": {"S": 3, "M": 4, "L": 0, "<script>x</script>": 2},
    })
    assert r.status_code == 200, r.data
    saved = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == "jau-optstock")
    assert saved["optionStock"]["S"] == 3 and saved["optionStock"]["L"] == 0
    assert "<script>x</script>" not in saved["optionStock"]      # keys are cleaned
    assert saved["stock"] == 7
    catalog_mod.remove("jau-optstock", "tester")


# =============================================== growth suite (referrals,
# coupons, abandoned carts, verified reviews, backups, WhatsApp alerts)

def _growth_order(client, oid, email, total=25000, currency="NGN",
                  promo="", cart_token="", pid="wix-001"):
    execute("DELETE FROM rate_limits WHERE action='order'")
    body = {
        "id": oid, "currency": currency, "total": total,
        "customer": {"name": "Growth Tester", "phone": "+2348012345678",
                     "email": email, "city": "Lagos", "zone": "Lagos",
                     "address": "1 Test Street"},
        "items": [{"id": pid, "name": "Bag", "qty": 1, "price": total}],
    }
    if promo:
        body["promoCode"] = promo
    if cart_token:
        body["cartToken"] = cart_token
    r = client.post("/api/orders", headers={"X-CSRF-Token": csrf(client)}, json=body)
    assert r.status_code == 200, r.data
    return r.get_json()


def test_referral_code_minted_only_for_qualifying_orders(client):
    # under the 20,000 NGN minimum: no code
    d = _growth_order(client, "JA-GRLOW1", "low@example.com", total=15000)
    assert d.get("referralCode") == ""
    # at/over the minimum: a JA- code arrives with the order response
    d = _growth_order(client, "JA-GRHI01", "hi@example.com", total=25000)
    code = d.get("referralCode")
    assert code and code.startswith("JA-")
    row = one("SELECT * FROM referral_codes WHERE code=?", (code,))
    assert row["email"] == "hi@example.com" and row["uses"] == 0
    # the same customer keeps the same code on their next big order
    d2 = _growth_order(client, "JA-GRHI02", "hi@example.com", total=30000)
    assert d2.get("referralCode") == code


def test_referral_code_minted_for_cfa_equivalent(client):
    # 10,000 F CFA ≈ 22,727 NGN at the storefront's 0.44 rate — qualifies
    d = _growth_order(client, "JA-GRCFA1", "cfa@example.com", total=10000, currency="CFA")
    assert d.get("referralCode", "").startswith("JA-")
    # 5,000 F CFA ≈ 11,364 NGN — does not
    d = _growth_order(client, "JA-GRCFA2", "cfa2@example.com", total=5000, currency="CFA")
    assert d.get("referralCode") == ""


def test_promo_check_and_referral_discount(client):
    d = _growth_order(client, "JA-GRPC01", "owner@example.com", total=25000)
    code = d["referralCode"]
    # a stranger's code check: 5% for the buyer
    execute("DELETE FROM rate_limits WHERE action='promo-check'")
    r = client.post("/api/promo/check", json={"code": code.lower()})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and j["percent"] == 5 and j["kind"] == "referral" and j["code"] == code
    # junk is refused
    r = client.post("/api/promo/check", json={"code": "NOPE-99"})
    assert r.status_code == 404 and r.get_json()["ok"] is False


def test_milestone_reward_exactly_two_uses_capped_at_ten(client):
    d = _growth_order(client, "JA-GRM001", "referrer@example.com", total=25000)
    code = d["referralCode"]
    # the owner using their own code never counts
    _growth_order(client, "JA-GRM002", "referrer@example.com", promo=code)
    assert one("SELECT uses FROM referral_codes WHERE code=?", (code,))["uses"] == 0
    # first real use: counted, but NO reward yet
    _growth_order(client, "JA-GRM003", "friend1@example.com", promo=code)
    row = one("SELECT uses, reward_issued FROM referral_codes WHERE code=?", (code,))
    assert row["uses"] == 1 and not row["reward_issued"]
    assert one("SELECT COUNT(*) n FROM coupons WHERE email='referrer@example.com'")["n"] == 0
    # second use: the 10% single-use reward coupon is minted automatically
    _growth_order(client, "JA-GRM004", "friend2@example.com", promo=code)
    row = one("SELECT uses, reward_issued, reward_coupon FROM referral_codes WHERE code=?", (code,))
    assert row["uses"] == 2 and row["reward_issued"] and row["reward_coupon"].startswith("THANKS-")
    cp = one("SELECT * FROM coupons WHERE code=?", (row["reward_coupon"],))
    assert cp["percent"] == 10 and cp["kind"] == "reward" and cp["max_uses"] == 1
    # a third use never mints a second reward
    _growth_order(client, "JA-GRM005", "friend3@example.com", promo=code)
    assert one("SELECT COUNT(*) n FROM coupons WHERE email='referrer@example.com'")["n"] == 1


def test_growth_settings_admin_only_and_reward_capped(client):
    assert client.get("/api/admin/growth/settings").status_code == 401
    tok = login(client)
    r = client.get("/api/admin/growth/settings")
    s = r.get_json()["settings"]
    assert s["minSpendNgn"] == 20000 and s["referrerPercent"] == 10 and s["milestone"] == 2
    # the referrer reward is hard-capped at 10% no matter what is typed
    r = client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                    json={"referrerPercent": 50, "minSpendNgn": 30000, "milestone": 3})
    s = r.get_json()["settings"]
    assert s["referrerPercent"] == 10 and s["minSpendNgn"] == 30000 and s["milestone"] == 3
    # put the defaults back for the other tests
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"minSpendNgn": 20000, "milestone": 2})


def test_admin_coupon_crud_expiry_and_max_uses(client):
    tok = login(client)
    # create
    r = client.post("/api/admin/coupons", headers={"X-CSRF-Token": tok},
                    json={"code": "SALE-10", "percent": 10, "maxUses": 1})
    assert r.status_code == 200 and r.get_json()["code"] == "SALE-10"
    # duplicate refused
    assert client.post("/api/admin/coupons", headers={"X-CSRF-Token": tok},
                       json={"code": "SALE-10", "percent": 15}).status_code == 400
    # checkout sees it
    execute("DELETE FROM rate_limits WHERE action='promo-check'")
    j = client.post("/api/promo/check", json={"code": "sale-10"}).get_json()
    assert j["ok"] and j["percent"] == 10 and j["kind"] == "coupon"
    # one use exhausts maxUses=1 and deactivates it
    _growth_order(client, "JA-GRCP01", "couponuser@example.com", promo="SALE-10")
    assert client.post("/api/promo/check", json={"code": "SALE-10"}).status_code == 404
    # edit: reactivate with more uses
    r = client.patch("/api/admin/coupons/SALE-10", headers={"X-CSRF-Token": tok},
                     json={"active": True, "maxUses": 100, "percent": 12})
    assert r.status_code == 200
    assert client.post("/api/promo/check", json={"code": "SALE-10"}).get_json()["percent"] == 12
    # expire it
    client.patch("/api/admin/coupons/SALE-10", headers={"X-CSRF-Token": tok},
                 json={"expiresAt": "2020-01-01 00:00:00"})
    assert client.post("/api/promo/check", json={"code": "SALE-10"}).status_code == 404
    # delete it
    assert client.delete("/api/admin/coupons/SALE-10",
                         headers={"X-CSRF-Token": tok}).status_code == 200
    assert one("SELECT 1 FROM coupons WHERE code='SALE-10'") is None
    # admin session required for all of it
    client.post("/api/admin/logout", headers={"X-CSRF-Token": tok})
    assert client.get("/api/admin/coupons").status_code == 401


def test_abandoned_cart_capture_remind_recover_complete(client):
    import growth
    tok_h = {"X-CSRF-Token": csrf(client)}
    # capture early in checkout
    r = client.post("/api/cart/abandon", headers=tok_h, json={
        "token": "CT-TESTTOKEN1", "email": "sleepy@example.com", "currency": "NGN",
        "items": [{"id": "wix-001", "name": "Bag", "qty": 2}]})
    assert r.status_code == 200 and r.get_json()["ok"]
    # the recovery link returns the saved cart
    j = client.get("/api/cart/recover/CT-TESTTOKEN1").get_json()
    assert j["ok"] and j["items"][0]["qty"] == 2 and j["currency"] == "NGN"
    # nothing is emailed before the 2-hour mark
    assert growth.send_abandoned_reminders() == 0
    # age the record past the window: exactly one reminder goes out, once
    import datetime as dtm
    old = (dtm.datetime.utcnow() - dtm.timedelta(hours=3)).isoformat(timespec="seconds")
    execute("UPDATE abandoned_carts SET updated_at=? WHERE token='CT-TESTTOKEN1'", (old,))
    assert growth.send_abandoned_reminders() == 1
    assert growth.send_abandoned_reminders() == 0
    assert one("SELECT reminded_at FROM abandoned_carts WHERE token='CT-TESTTOKEN1'")["reminded_at"]
    # the customer comes back and buys: the record closes, the link dies
    _growth_order(client, "JA-GRAB01", "sleepy@example.com", cart_token="CT-TESTTOKEN1")
    assert one("SELECT completed_at FROM abandoned_carts WHERE token='CT-TESTTOKEN1'")["completed_at"]
    assert client.get("/api/cart/recover/CT-TESTTOKEN1").status_code == 404


def test_abandoned_module_can_be_switched_off(client):
    tok = login(client)
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"abandonedEnabled": False})
    r = client.post("/api/cart/abandon", headers={"X-CSRF-Token": csrf(client)}, json={
        "token": "CT-OFFTOKEN", "email": "off@example.com",
        "items": [{"id": "wix-001", "qty": 1}]})
    assert r.get_json().get("skipped") is True
    assert one("SELECT 1 FROM abandoned_carts WHERE token='CT-OFFTOKEN'") is None
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"abandonedEnabled": True})


def test_reviews_are_purchase_verified_and_stored_server_side(client):
    _growth_order(client, "JA-GRRV01", "reviewer@example.com", pid="wix-007")
    execute("DELETE FROM rate_limits WHERE action='review'")
    H = {"X-CSRF-Token": csrf(client)}
    # not a buyer -> refused
    r = client.post("/api/reviews", headers=H, json={
        "productId": "wix-007", "email": "stranger@example.com",
        "name": "Nobody", "stars": 5, "note": "fake"})
    assert r.status_code == 403
    # a declined order does not count as a purchase
    _growth_order(client, "JA-GRRV02", "declined@example.com", pid="wix-008")
    execute("UPDATE orders SET status='declined' WHERE id='JA-GRRV02'")
    r = client.post("/api/reviews", headers=H, json={
        "productId": "wix-008", "email": "declined@example.com",
        "name": "D", "stars": 5, "note": "nope"})
    assert r.status_code == 403
    # the real buyer posts: stored in the DB, average is served publicly
    r = client.post("/api/reviews", headers=H, json={
        "productId": "wix-007", "email": "reviewer@example.com",
        "name": "Amaka", "stars": 4, "note": "Lovely <b>bag</b>"})
    assert r.status_code == 200
    j = client.get("/api/reviews/wix-007").get_json()
    assert j["count"] == 1 and j["average"] == 4.0
    assert j["reviews"][0]["name"] == "Amaka"
    assert "<b>" not in j["reviews"][0]["note"]      # HTML stripped
    # posting again replaces (one review per product per customer)
    execute("DELETE FROM rate_limits WHERE action='review'")
    client.post("/api/reviews", headers=H, json={
        "productId": "wix-007", "email": "reviewer@example.com",
        "name": "Amaka", "stars": 5, "note": "Even better after a week"})
    j = client.get("/api/reviews/wix-007").get_json()
    assert j["count"] == 1 and j["average"] == 5.0


def test_backup_dump_and_daily_flag(client, tmp_path):
    import backup
    _growth_order(client, "JA-GRBK01", "backup@example.com")
    p = tmp_path / "orders-backup.json"
    n = backup.dump_orders(str(p))
    assert n >= 1
    data = json.loads(p.read_text())
    assert "orders" in data and "paymentProofs" in data and "generatedAt" in data
    assert any(o["id"] == "JA-GRBK01" for o in data["orders"])
    # the midnight flag: due until marked, then not due again today
    execute("DELETE FROM growth_settings WHERE key='lastBackupDate'")
    assert backup.due() is True
    backup.mark_backup_done()
    assert backup.due() is False


def test_whatsapp_notifier_safe_when_unconfigured(client):
    import whatsapp
    sent, detail = whatsapp.send_order_notification({
        "id": "JA-X", "total": 25000, "currency": "NGN",
        "customer": {"name": "Ada", "city": "Lagos", "zone": "Ikeja", "address": "1 Road"},
        "items": [{"qty": 1, "name": "Bag"}]})
    assert sent is False and "not configured" in detail


# ====================================================== adjustable CFA rate
def test_cfa_rate_default_and_admin_adjustable(client):
    tok = login(client)
    s = client.get("/api/admin/growth/settings").get_json()["settings"]
    assert s["cfaRate"] == 0.44

    # raise the rate: 1 NGN = 2 CFA, so 10,000 F CFA is only ~N5,000 and
    # no longer qualifies for a referral code (threshold N20,000)
    r = client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                    json={"cfaRate": 2.0})
    assert r.get_json()["settings"]["cfaRate"] == 2.0
    d = _growth_order(client, "JA-RATE01", "rate1@example.com",
                      total=10000, currency="CFA")
    assert d.get("referralCode") == ""

    # back to the default rate the same order qualifies again
    client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                json={"cfaRate": 0.44})
    d = client.get("/api/admin/growth/settings").get_json()["settings"]
    assert d["cfaRate"] == 0.44
    d = _growth_order(client, "JA-RATE02", "rate2@example.com",
                      total=10000, currency="CFA")
    assert d.get("referralCode", "").startswith("JA-")

    # nonsense rates are rejected and fall back to the safe default
    s = client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                    json={"cfaRate": 0}).get_json()["settings"]
    assert s["cfaRate"] == 0.44
    s = client.post("/api/admin/growth/settings", headers={"X-CSRF-Token": tok},
                    json={"cfaRate": "banana"}).get_json()["settings"]
    assert s["cfaRate"] == 0.44


def test_supabase_growth_mirrors_are_safe_noops_when_disabled(client):
    import supabase_store as sb
    assert sb.client() is None  # tests never talk to Supabase
    sb.mirror_referral_code({"code": "JA-XX", "email": "a@b.c"})
    sb.mirror_referral_use({"code": "JA-XX", "order_id": "JA-1"})
    sb.mirror_coupon({"code": "SALE", "percent": 5})
    sb.mirror_growth_settings({"cfaRate": 0.44})
    import growth
    growth._mirror_referral("JA-NOPE")     # unknown code: silently ignored
    growth._mirror_coupon("NOPE")


# =================================================== homepage & shop polish
def test_homepage_hero_video_and_single_wave_layer(client):
    html = client.get("/index.html").get_data(as_text=True)
    # hero video: autoplay muted loop inline, aggressive preload, no PiP
    assert 'id="hero-video"' in html
    for attr in ("autoplay", "muted", "loop", "playsinline",
                 'preload="auto"', "disablepictureinpicture"):
        assert attr in html, f"hero video is missing {attr}"
    # centered overlay statement + Explore button
    assert 'id="hero-video-copy"' in html
    assert 'data-i18n="home.standard"' in html and 'data-i18n="home.explore"' in html
    # the payment-upload widget is gone from the homepage
    assert "how-upload" not in html and "uploadProofText" not in html
    # only ONE wave layer remains: the footer wave, not the fixed overlay
    store_js = client.get("/js/store.js").get_data(as_text=True)
    assert "foot-wavez" in store_js
    assert "paintWaves" not in store_js and "site-waves" not in store_js
    css = client.get("/css/style.css").get_data(as_text=True)
    assert ".site-waves" not in css.replace("/* Footer flies up; just-in rows (the old fixed .site-waves overlay was removed:", "")


def test_shop_pagination_is_real(client):
    app_js = client.get("/js/app.js").get_data(as_text=True)
    assert "const per = 24;" in app_js          # a real page size, not the whole list
    assert "pager.prev" in app_js and "pager.next" in app_js
    shop = client.get("/shop.html").get_data(as_text=True)
    assert "data-pager" in shop


# ================================================== admin access recovery
def _clear_bootstrap():
    """Reset the one-shot recovery marker + code slots between tests."""
    execute("DELETE FROM growth_settings WHERE key=?", (authmod.BOOTSTRAP_MARKER,))
    execute("DELETE FROM otp_codes")


def test_bootstrap_password_applies_once_and_is_never_reapplied(client):
    from config import Config
    _clear_bootstrap()
    pw = "Recovery#Pass2026"

    # first boot after the deploy: the shared password is forced once
    assert authmod.apply_bootstrap_password(pw) is True
    assert one("SELECT key FROM growth_settings WHERE key=?",
               (authmod.BOOTSTRAP_MARKER,)) is not None
    assert authmod.verify_login(EMAIL, pw)
    # ... and it is written for EVERY configured admin email
    assert authmod.verify_login(Config.ADMIN_EMAILS[0], pw)
    assert one("SELECT action FROM audit_log WHERE action='admin.bootstrap_password_applied'")

    # the owner signs in and picks their own password ...
    changed = "OwnerChoice2026x"
    assert authmod.set_shared_password(changed) is True
    # ... so a reboot (or any later call) must not restore the bootstrap value
    assert authmod.apply_bootstrap_password(pw) is False
    assert authmod.verify_login(EMAIL, pw) is False
    assert authmod.verify_login(EMAIL, changed) is True

    authmod.set_shared_password(PW)
    _clear_bootstrap()


def test_bootstrap_rejects_a_weak_password_without_burning_the_marker(client):
    _clear_bootstrap()
    # too short / empty: refused, and the marker is NOT written, so a corrected
    # ADMIN_BOOTSTRAP_PASSWORD can still recover the account on a later boot
    assert authmod.apply_bootstrap_password("short") is False
    assert authmod.apply_bootstrap_password("") is False
    assert one("SELECT key FROM growth_settings WHERE key=?",
               (authmod.BOOTSTRAP_MARKER,)) is None

    assert authmod.apply_bootstrap_password("LateRecovery2026x") is True
    assert authmod.verify_login(EMAIL, "LateRecovery2026x") is True

    authmod.set_shared_password(PW)
    _clear_bootstrap()


def test_bootstrap_config_default_is_always_usable():
    import config
    default = config.Config.BOOTSTRAP_ADMIN_PASSWORD
    assert default, "BOOTSTRAP_ADMIN_PASSWORD must never be empty"
    assert authmod.password_strong(default)[0], "the shipped default must pass password_strong()"


def test_create_app_never_applies_the_bootstrap_under_testing(client, monkeypatch):
    """FLASK_ENV=testing must leave the suite's own passwords untouched."""
    import app as appmod2
    _clear_bootstrap()
    called = []
    monkeypatch.setattr(authmod, "apply_bootstrap_password",
                        lambda pw: called.append(pw) or True)
    appmod2.create_app()
    assert called == [], "create_app() applied the bootstrap while FLASK_ENV=testing"
    assert one("SELECT key FROM growth_settings WHERE key=?",
               (authmod.BOOTSTRAP_MARKER,)) is None
    _clear_bootstrap()


# ========================================== admin reset code: email first
def test_admin_reset_code_is_emailed_first(client, monkeypatch):
    _clear_bootstrap()
    sent = {"email": [], "whatsapp": []}

    def fake_email(to, code, purpose="reset"):
        sent["email"].append((to, code, purpose))
        return True, "sent"

    def fake_whatsapp(text):
        sent["whatsapp"].append(text)
        return True, "cloud-api"

    monkeypatch.setattr(emailer, "send_otp", fake_email)
    monkeypatch.setattr("whatsapp.send_text", fake_whatsapp)

    r = client.post("/api/admin/otp/request", json={"email": EMAIL})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert len(sent["email"]) == 1, "the code must be emailed"
    assert sent["email"][0][0] == EMAIL
    assert sent["whatsapp"] == [], "WhatsApp is only a fallback - it must not be used"
    assert EMAIL in body["message"]

    # the emailed code is a real, usable reset code
    code = sent["email"][0][1]
    assert len(code) == 6 and code.isdigit()
    r2 = client.post("/api/admin/otp/verify", json={"email": EMAIL, "code": code})
    assert r2.status_code == 200, r2.data
    newpw = "FreshOwner2026x"
    r3 = client.post("/api/admin/otp/reset", json={"newPassword": newpw})
    assert r3.status_code == 200, r3.data
    assert authmod.verify_login(EMAIL, newpw) is True

    authmod.set_shared_password(PW)
    _clear_bootstrap()


def test_admin_reset_code_falls_back_to_whatsapp_when_mail_fails(client, monkeypatch):
    _clear_bootstrap()
    calls = {"email": 0, "whatsapp": []}

    def fake_email(to, code, purpose="reset"):
        calls["email"] += 1
        return False, "smtp error: boom"

    def fake_whatsapp(text):
        calls["whatsapp"].append(text)
        return True, "cloud-api"

    monkeypatch.setattr(emailer, "send_otp", fake_email)
    monkeypatch.setattr("whatsapp.send_text", fake_whatsapp)

    r = client.post("/api/admin/otp/request", json={"email": EMAIL})
    assert r.status_code == 200, r.data
    assert calls["email"] == 1, "email must be attempted first"
    assert len(calls["whatsapp"]) == 1, "WhatsApp must take over when email fails"
    assert "WhatsApp" in r.get_json()["message"]

    authmod.set_shared_password(PW)
    _clear_bootstrap()


def test_admin_reset_code_is_a_502_when_both_channels_fail(client, monkeypatch):
    _clear_bootstrap()
    monkeypatch.setattr(emailer, "send_otp",
                        lambda to, code, purpose="reset": (False, "smtp error: boom"))
    monkeypatch.setattr("whatsapp.send_text", lambda text: (False, "not configured"))

    r = client.post("/api/admin/otp/request", json={"email": EMAIL})
    assert r.status_code == 502, r.data
    assert r.get_json()["ok"] is False
    _clear_bootstrap()
    authmod.set_shared_password(PW)


# ------------------------------------------------------------ media uploads
def test_category_asset_upload_accepts_image_and_document(client):
    tok = login(client)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    r = client.post("/api/admin/uploads/category", data={"file": (io.BytesIO(png), "cat.png")},
                    headers={"X-CSRF-Token": tok}, content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    assert r.get_json()["kind"] == "image"
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
    r = client.post("/api/admin/uploads/category", data={"file": (io.BytesIO(pdf), "brochure.pdf")},
                    headers={"X-CSRF-Token": tok}, content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    assert r.get_json()["kind"] == "document"
    url = r.get_json()["url"]
    resp = client.get(url)
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    assert "nosniff" in resp.headers.get("X-Content-Type-Options", "")
    # a renamed executable is refused by magic bytes, never the name
    r = client.post("/api/admin/uploads/category", data={"file": (io.BytesIO(b"MZ\x90\x00" + b"\x00" * 100), "x.jpg")},
                    headers={"X-CSRF-Token": tok}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_product_upload_accepts_video_and_rejects_executable(client):
    tok = login(client)
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 300
    r = client.post("/api/admin/uploads/product", data={"file": (io.BytesIO(mp4), "item.mp4")},
                    headers={"X-CSRF-Token": tok}, content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["kind"] == "video" and body["url"].endswith(".mp4")
    # the stored video is served back
    assert client.get(body["url"]).status_code == 200
    r = client.post("/api/admin/uploads/product", data={"file": (io.BytesIO(b"MZ\x90\x00" + b"\x00" * 100), "x.mp4")},
                    headers={"X-CSRF-Token": tok}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_document_receipt_is_accepted_and_stored(client):
    docx = b"PK\x03\x04" + b"\x00" * 200
    r = post_proof(client, docx, "receipt.docx")
    assert r.status_code == 200, r.data
    row = dict(one("SELECT * FROM payment_proofs ORDER BY id DESC LIMIT 1"))
    assert "openxmlformats" in row["mime"] or "msword" in row["mime"]


def test_admin_order_delete(client):
    tok = login(client)
    execute("INSERT INTO orders (id,payload,total,currency,status) VALUES (?,?,?,?,?)",
            ("JA-DELETE1", "{}", 100, "NGN", "confirmed"))
    r = client.delete("/api/admin/orders/JA-DELETE1", headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.data
    assert one("SELECT id FROM orders WHERE id='JA-DELETE1'") is None
    # deleting twice is a 404, never a crash
    assert client.delete("/api/admin/orders/JA-DELETE1", headers={"X-CSRF-Token": tok}).status_code == 404


def test_category_merge_is_idempotent(tmp_path, client, monkeypatch):
    import catalog as catmod
    execute("DELETE FROM growth_settings WHERE key='category_merge_v2'")
    cat_file = tmp_path / "categories.json"
    cat_file.write_text(json.dumps({"categories": [
        {"id": "nails", "name": "Nails", "nameFr": "", "image": "x"},
        {"id": "packaging", "name": "Packaging", "nameFr": "", "image": "x"},
        {"id": "beauty", "name": "Beauty & skincare", "nameFr": "Beauté & soins", "image": "x"},
        {"id": "gift-set", "name": "Gift set", "nameFr": "Coffret", "image": "x"},
    ]}))
    ov = tmp_path / "catalog.json"
    ov.write_text(json.dumps({"products": [{"id": "p1", "name": "Nail varnish", "category": "nails"}],
                              "deleted": []}))
    monkeypatch.setenv("CATEGORIES_PATH", str(cat_file))
    monkeypatch.setattr(catmod, "CATALOG_FILE", str(ov))
    assert catmod.merge_categories() is True
    data = json.loads(cat_file.read_text())
    ids = [c["id"] for c in data["categories"]]
    assert "nails" not in ids and "packaging" not in ids
    assert next(c["name"] for c in data["categories"] if c["id"] == "beauty") == "Beauty"
    assert next(c["name"] for c in data["categories"] if c["id"] == "gift-set") == "Gift Sets & Packaging"
    assert next(c["nameFr"] for c in data["categories"] if c["id"] == "gift-set") == "Coffret"
    ovd = json.loads(ov.read_text())
    assert next(p for p in ovd["products"] if p["id"] == "p1")["category"] == "beauty"
    # a second run no-ops (the marker is set)
    assert catmod.merge_categories() is False


def test_merged_catalogue_never_serves_a_folded_out_category(monkeypatch):
    """The live catalogue must never leak a merged / legacy category id.

    The one-shot migration writes fold overrides to the local file, but when
    Supabase is the source of truth those overrides are ignored (only the
    `deleted` list is read), so a seed/supabase row still carrying `skincare`,
    `nails` or `packaging` used to surface to shoppers. `merged()` now folds
    the category at read time for every backend.
    """
    import catalog as catmod
    seed = catmod._seed_products()
    assert any(str(p.get("category")) == "skincare" for p in seed)

    # Supabase is the source of truth; a skincare product row comes back live.
    sb = [dict(p) for p in seed if str(p.get("category")) == "skincare"]
    sb[0]["category"] = "nails"                       # legacy id leak
    monkeypatch.setattr(catmod, "_supabase_products", lambda: sb)
    monkeypatch.setattr(catmod, "overrides", lambda: {"products": [], "deleted": []})

    merged = catmod.merged(include_hidden=True)
    assert all(str(p.get("category")) not in ("skincare", "nails", "packaging")
               for p in merged)
    # The legacy rows themselves were re-pointed onto the survivor.
    assert {p["id"] for p in merged} >= {p["id"] for p in sb}
    for p in merged:
        if p["id"] in {r["id"] for r in sb}:
            assert p["category"] == "beauty", p["id"]

    # Same guarantee on the local (no-Supabase) path.
    monkeypatch.setattr(catmod, "_supabase_products", lambda: None)
    monkeypatch.setattr(catmod, "overrides", lambda: {"products": [], "deleted": []})
    merged = catmod.merged(include_hidden=True)
    assert all(str(p.get("category")) not in ("skincare", "nails", "packaging")
               for p in merged)


def test_confirmation_receipt_email_includes_referral_code(client, monkeypatch):
    """When an order qualifies for a referral code, the confirmation email
    includes the code so the customer can immediately share it with friends."""
    import email as emailmod
    from email import policy as epolicy
    from mail_sink import MailSink
    import config, security

    # Place a qualifying order (9000 CFA is ~20,454 NGN, > 20000 NGN threshold)
    oid = _make_order(client, "JA-REFTEST1", email="refcust@example.com")
    with MailSink() as sink:
        monkeypatch.setattr(config.Config, "MAIL_MODE", "smtp")
        monkeypatch.setattr(config.Config, "SMTP_HOST", sink.host)
        monkeypatch.setattr(config.Config, "SMTP_PORT", sink.port)
        monkeypatch.setattr(config.Config, "SMTP_USER", "")
        monkeypatch.setattr(config.Config, "SMTP_PASS", "")
        r = client.get(f"/api/orders/{oid}/confirm?action=confirm"
                       f"&token={security.order_token(oid, 'confirm')}")
    assert r.get_json().get("customerEmailed") is True
    assert len(sink.messages) == 1
    msg = emailmod.message_from_bytes(sink.messages[0]["data"], policy=epolicy.default)
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "referral code" in body.lower()
    assert "JA-" in body


def test_admin_orders_filter_by_status(client):
    """Admin orders endpoint correctly filters by status."""
    oid1 = _make_order(client, "JA-PEND01", email="pend1@example.com")
    oid2 = _make_order(client, "JA-CONF01", email="conf1@example.com")

    tok = login(client)
    client.patch(f"/api/admin/orders/{oid2}", json={"status": "confirmed"},
                 headers={"X-CSRF-Token": tok})

    res_all = client.get("/api/admin/orders")
    assert res_all.status_code == 200
    orders_all = res_all.get_json()["orders"]
    assert any(o["id"] == oid1 for o in orders_all)
    assert any(o["id"] == oid2 for o in orders_all)

    res_conf = client.get("/api/admin/orders?status=confirmed")
    assert res_conf.status_code == 200
    orders_conf = res_conf.get_json()["orders"]
    assert all(o["status"] == "confirmed" for o in orders_conf)
    assert any(o["id"] == oid2 for o in orders_conf)


def test_benin_and_togo_payment_methods_supported(client):
    """Payment methods list includes MTN MoMo Benin and Moov Togo."""
    r = client.get("/api/payment-methods")
    assert r.status_code == 200
    methods = r.get_json().get("methods", [])
    assert any("MTN MoMo Benin" in m for m in methods)
    assert any("Moov Money Togo" in m for m in methods)


def test_benin_minimum_order_cfa_and_ngn_limits(client):
    """Benin deliveries enforce 5,000 CFA and 11,400 NGN minimums."""
    tok = client.get("/api/csrf").get_json()["token"]
    order_cfa_low = {
        "id": "JA-BJMIN-1",
        "customer": {"name": "Benin User", "email": "bj@test.com", "zone": "Cotonou"},
        "items": [{"id": "p1", "name": "Item", "qty": 1, "price": 4000}],
        "currency": "CFA",
        "total": 4000,
    }
    r1 = client.post("/api/orders", json=order_cfa_low, headers={"X-CSRF-Token": tok})
    assert r1.status_code == 400
    assert "5,000 F CFA" in r1.get_json().get("error", "")

    order_ngn_low = {
        "id": "JA-BJMIN-2",
        "customer": {"name": "Benin User", "email": "bj@test.com", "zone": "Porto-Novo"},
        "items": [{"id": "p1", "name": "Item", "qty": 1, "price": 10000}],
        "currency": "NGN",
        "total": 10000,
    }
    r2 = client.post("/api/orders", json=order_ngn_low, headers={"X-CSRF-Token": tok})
    assert r2.status_code == 400
    assert "11,400 naira" in r2.get_json().get("error", "")


def test_net_js_blob_uploads_wait_five_minutes_and_persist_timeout():
    src = open(os.path.join(os.path.dirname(__file__), "..", "js", "net.js"),
               encoding="utf-8").read()
    assert "opts.blob ? 300000 : 25000" in src
    assert "job.bodyKind === \"blob\" ? 300000 : 25000" in src
    assert "timeout: job.timeout || (job.bodyKind === \"blob\" ? 300000 : 25000)" in src


def test_admin_login_copy_is_reset_by_email():
    src = open(os.path.join(os.path.dirname(__file__), "..", "js", "admin.js"),
               encoding="utf-8").read()
    assert "Reset it by email" in src
    assert "send a 6-digit code to that email" in src
    assert "Reset it via WhatsApp" not in src
    assert "timeout: 300000" in src
    assert "api/admin/uploads/product" in src


def test_github_sync_refuses_put_without_sha_and_skips_orders_backup():
    import repo_sync
    assert "data/backups/orders-backup.json" not in repo_sync.REPO_DATA_FILES
    src = open(os.path.join(os.path.dirname(__file__), "..", "repo_sync.py"),
               encoding="utf-8").read()
    assert "GET sha" in src
    assert "could not recover sha" in src
    assert "(no sha)" in src
    gi = open(os.path.join(os.path.dirname(__file__), "..", ".gitignore"),
              encoding="utf-8").read()
    assert "orders-backup.json" in gi
    admin = open(os.path.join(os.path.dirname(__file__), "..", "js", "admin.js"),
                 encoding="utf-8").read()
    assert "Customer orders stay on the server" in admin

