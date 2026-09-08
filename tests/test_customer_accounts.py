"""Customer accounts: register/login, ownership, claim tokens, no leaks."""
import json, os, re, sys, uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402
import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
import emailer  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

EMAIL = "jaurastore@gmail.com"
SHOP_PW = "Shopper1x"
_RUN = uuid.uuid4().hex[:10]


def _mail(tag):
    return f"acct-{tag}-{_RUN}@example.com"


def _oid(n):
    return f"JA-{_RUN[:4].upper()}{n:02d}"


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
    try:
        execute("DELETE FROM customer_tokens")
        execute("DELETE FROM customers")
    except Exception:
        pass
    with app.test_client() as c:
        yield c


def csrf(client):
    return client.get("/api/config").get_json()["csrf"]


def H(client):
    return {"X-CSRF-Token": csrf(client)}


def register(client, email, password=SHOP_PW, **extra):
    body = {"email": email, "password": password}
    body.update(extra)
    return client.post("/api/account/register", json=body, headers=H(client))


def login_cust(client, email, password=SHOP_PW):
    return client.post("/api/account/login", json={"email": email, "password": password},
                       headers=H(client))


def place_order(client, oid, email, name="Ada Shopper"):
    execute("DELETE FROM rate_limits WHERE action='order'")
    r = client.post("/api/orders", headers=H(client), json={
        "id": oid, "currency": "NGN", "total": 8550,
        "customer": {"name": name, "phone": "+2348012345678", "email": email,
                     "city": "Lagos", "zone": "Lagos Mainland", "address": "1 Test Street",
                     "country": "Nigeria"},
        "items": [{"id": "wix-005", "name": "Bag", "qty": 1, "price": 8550}],
    })
    assert r.status_code == 200, r.data
    return r.get_json()


def test_no_public_register_alias(client):
    for path in ("/api/register", "/api/signup", "/api/admin/register"):
        assert client.post(path, json={}).status_code in (404, 405)


def test_account_spa_routes_serve_account_html(client):
    for path in ("/account", "/account/", "/account/forgot", "/account/reset-password"):
        r = client.get(path)
        assert r.status_code == 200, path
        body = r.get_data(as_text=True)
        assert "data-account-root" in body
        assert 'base href="/"' in body


def test_register_login_logout_and_session(client):
    email = _mail("reg")
    r = register(client, email, name="Ada Shopper", phone="+2348011111111")
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert body["customer"]["email"] == email
    assert "password_hash" not in json.dumps(body)
    sess = client.get("/api/account/session").get_json()
    assert sess["authenticated"] is True
    assert sess["customer"]["name"] == "Ada Shopper"
    assert client.post("/api/account/logout").status_code == 200
    assert client.get("/api/account/session").get_json()["authenticated"] is False
    bad = login_cust(client, email, "WrongPass1")
    assert bad.status_code == 401
    assert login_cust(client, email).status_code == 200
    r = client.get("/account/logout")
    assert r.status_code == 200
    assert client.get("/api/account/session").get_json()["authenticated"] is False


def test_register_needs_csrf_and_strong_password(client):
    assert client.post("/api/account/register", json={
        "email": _mail("weak"), "password": "Shopper1x",
    }).status_code == 403
    r = register(client, _mail("weak"), password="short")
    assert r.status_code == 400
    r = register(client, _mail("dup"))
    assert r.status_code == 201
    r = register(client, _mail("dup"))
    assert r.status_code == 409


def test_profile_and_password_change(client):
    email = _mail("prof")
    assert register(client, email, name="Old Name").status_code == 201
    r = client.patch("/api/account/profile", headers=H(client), json={
        "name": "New Name", "phone": "+22990000000", "country": "Benin",
        "city": "Cotonou", "delivery_address": "Rue 12", "preferred_currency": "CFA",
    })
    assert r.status_code == 200, r.data
    c = r.get_json()["customer"]
    assert c["name"] == "New Name" and c["preferred_currency"] == "CFA"
    assert c["delivery_address"] == "Rue 12"
    bad = client.post("/api/account/password", headers=H(client), json={
        "currentPassword": "nope", "newPassword": "FreshPass9",
    })
    assert bad.status_code == 403
    ok = client.post("/api/account/password", headers=H(client), json={
        "currentPassword": SHOP_PW, "newPassword": "FreshPass9",
    })
    assert ok.status_code == 200
    client.post("/api/account/logout")
    assert login_cust(client, email).status_code == 401
    assert login_cust(client, email, "FreshPass9").status_code == 200


def test_forgot_reset_does_not_enumerate_and_is_one_use(client, monkeypatch):
    email = _mail("reset")
    assert register(client, email).status_code == 201
    client.post("/api/account/logout")
    sent = []

    def fake_send(to, subject, body):
        sent.append((to, subject, body))
        return True, "ok"

    monkeypatch.setattr(emailer, "send", fake_send)
    unknown = client.post("/api/account/forgot", headers=H(client),
                          json={"email": "nobody-acct@example.com"})
    known = client.post("/api/account/forgot", headers=H(client),
                        json={"email": email})
    assert unknown.status_code == known.status_code == 200
    assert unknown.get_json()["message"] == known.get_json()["message"]
    assert len(sent) == 1
    token = re.search(r"token=([A-Za-z0-9_-]+)", sent[0][2]).group(1)
    weak = client.post("/api/account/reset", headers=H(client),
                       json={"token": token, "newPassword": "short"})
    assert weak.status_code == 400
    # weak password must not burn the token
    ok = client.post("/api/account/reset", headers=H(client),
                     json={"token": token, "newPassword": "ResetPass9"})
    assert ok.status_code == 200, ok.data
    again = client.post("/api/account/reset", headers=H(client),
                        json={"token": token, "newPassword": "ResetPass8"})
    assert again.status_code == 400
    client.post("/api/account/logout")
    assert login_cust(client, email, "ResetPass9").status_code == 200


def test_guest_checkout_is_not_listed_until_claim(client, monkeypatch):
    email = _mail("claim")
    oid = _oid(1)
    place_order(client, oid, email)
    assert register(client, email, name="Claim Me").status_code == 201
    listed = client.get("/api/account/orders").get_json()["orders"]
    assert listed == []
    sent = []
    monkeypatch.setattr(emailer, "send",
                        lambda to, subject, body: sent.append(body) or (True, "ok"))
    req = client.post("/api/account/claim-request", json={}, headers=H(client))
    assert req.status_code == 200
    token = re.search(r"claim=([A-Za-z0-9_-]+)", sent[0]).group(1)
    # wrong token does nothing
    bad = client.post("/api/account/claim", headers=H(client), json={"token": "nope"})
    assert bad.status_code == 400
    assert client.get("/api/account/orders").get_json()["orders"] == []
    ok = client.post("/api/account/claim", headers=H(client), json={"token": token})
    assert ok.status_code == 200 and ok.get_json()["linked"] >= 1
    ids = {o["id"] for o in client.get("/api/account/orders").get_json()["orders"]}
    assert oid in ids
    # one-use
    again = client.post("/api/account/claim", headers=H(client), json={"token": token})
    assert again.status_code == 400


def test_signed_in_checkout_owns_order_even_if_email_differs(client):
    email = _mail("owner")
    assert register(client, email, name="Owner").status_code == 201
    oid2, oid3 = _oid(2), _oid(3)
    place_order(client, oid2, "other-checkout@example.com", name="Someone Else")
    row = one("SELECT customer_user_id, email FROM orders WHERE id=?", (oid2,))
    assert row["email"] == "other-checkout@example.com"
    assert row["customer_user_id"]
    ids = {o["id"] for o in client.get("/api/account/orders").get_json()["orders"]}
    assert oid2 in ids
    guest = appmod.create_app().test_client()
    execute("DELETE FROM rate_limits WHERE action='order'")
    place_order(guest, oid3, email)
    row = one("SELECT customer_user_id FROM orders WHERE id=?", (oid3,))
    assert not row["customer_user_id"]


def test_owned_miss_is_404_not_403_and_no_receipt_leak(client):
    a = _mail("a")
    b = _mail("b")
    oid4 = _oid(4)
    assert register(client, a).status_code == 201
    place_order(client, oid4, a)
    execute("UPDATE orders SET payload=? WHERE id=?",
            (json.dumps({"id": oid4, "proofUrl": "/uploads/proofs/secret.jpg",
                         "items": [{"name": "Bag", "qty": 1, "price": 100}],
                         "customer": {"email": a}}), oid4))
    mine = client.get(f"/api/account/orders/{oid4}").get_json()["order"]
    blob = json.dumps(mine)
    assert "proof" not in blob.lower()
    assert "secret.jpg" not in blob
    assert a not in blob
    assert mine["id"] == oid4
    client.post("/api/account/logout")
    assert register(client, b).status_code == 201
    r = client.get(f"/api/account/orders/{oid4}")
    assert r.status_code == 404
    pub = client.get(f"/api/orders/{oid4}").get_json()
    assert "email" not in json.dumps(pub)


def test_login_rate_limit(client):
    execute("DELETE FROM rate_limits")
    email = _mail("lock")
    assert register(client, email).status_code == 201
    client.post("/api/account/logout")
    codes = [login_cust(client, email, "BadPass" + str(i)).status_code for i in range(9)]
    assert 429 in codes, codes


def test_admin_login_drops_customer_session(client):
    email = "acct-admin@example.com"
    assert register(client, email).status_code == 201
    assert client.get("/api/account/session").get_json()["authenticated"] is True
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200
    assert client.get("/api/account/session").get_json()["authenticated"] is False


def test_public_stock_is_state_only(client):
    execute("INSERT INTO variant_stock "
            "(product_id, variant_key, variant_label, qty, low_threshold) "
            "VALUES ('wix-005','__default__','Default',12,3) "
            "ON CONFLICT(product_id, variant_key) DO UPDATE SET qty=12, low_threshold=3")
    pub = client.get("/api/stock").get_json()
    blob = json.dumps(pub)
    assert "\"qty\"" not in blob
    assert "lowThreshold" not in blob
    assert pub["stock"]["wix-005"][0]["state"] in ("in", "low", "out")
    client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    admin = client.get("/api/stock").get_json()
    assert admin["stock"]["wix-005"][0]["qty"] == 12
