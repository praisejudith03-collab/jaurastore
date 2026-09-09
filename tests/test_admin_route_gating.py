"""Every Admin Portal route must reject an unauthenticated caller.

The suite previously tested individual admin endpoints, so a NEW route added
without `@authmod.require_admin` would pass CI unnoticed - exactly the
"it works under FLASK_ENV=testing" trap. This walks the live Flask url_map
instead of a hand-written list, so coverage cannot drift as routes are added.

It also checks that no public storefront response carries receipt or payment
proof data, and that the receipt routes are admin-gated specifically.

Run with:  python3 -m pytest tests/test_admin_route_gating.py -q
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

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
from db import execute, init_db, one  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

EMAIL = "jaurastore@gmail.com"

# Routes under /api/admin/ that are intentionally reachable without a session.
# Kept explicit and short so a new exemption has to be a deliberate act.
#
# /api/admin/session is the probe the admin page calls to decide whether to
# show the login form. It answers {authenticated: false, email: null} and hands
# out the CSRF token the login POST needs - no admin data. It is asserted
# separately below so the exemption cannot quietly start leaking.
PUBLIC_ADMIN_EXEMPTIONS = {"/api/admin/session"}


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


def _admin_rules(app):
    for rule in app.url_map.iter_rules():
        path = str(rule)
        if path.startswith("/api/admin") and path not in PUBLIC_ADMIN_EXEMPTIONS:
            yield rule


def test_there_are_admin_routes_to_audit(app):
    """Guard against this test silently passing over an empty list."""
    assert len(list(_admin_rules(app))) >= 20


def test_every_admin_route_rejects_an_anonymous_caller(client, app):
    offenders = {}
    for rule in _admin_rules(app):
        # skip the login/logout endpoints: they are the auth boundary itself
        if rule.endpoint in ("api.admin_login", "api.admin_logout"):
            continue
        path = str(rule).replace("<int:pid>", "1").replace("<pid>", "1") \
                        .replace("<order_id>", "JA-000000").replace("<path:p>", "1") \
                        .replace("<p>", "1")
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            fn = getattr(client, method.lower())
            try:
                r = fn(path, json={})
            except Exception as exc:                 # a 404 handler etc.
                offenders[f"{method} {path}"] = f"raised {exc.__class__.__name__}"
                continue
            if r.status_code in (200, 201):
                offenders[f"{method} {path}"] = f"{r.status_code} without auth"
    assert not offenders, f"admin routes reachable anonymously: {offenders}"


def test_the_session_probe_leaks_nothing(client):
    """The one anonymous /api/admin route must stay a bare yes/no."""
    r = client.get("/api/admin/session")
    assert r.status_code == 200
    body = r.get_json()
    assert body["authenticated"] is False
    assert body["email"] is None
    assert set(body) == {"ok", "authenticated", "email", "csrf"}, \
        f"the session probe started returning more: {sorted(body)}"


def test_receipt_routes_are_admin_gated(client):
    r = client.get("/api/admin/payment-proofs")
    assert r.status_code in (401, 403), r.status_code
    r = client.delete("/api/admin/payment-proofs/1")
    assert r.status_code in (401, 403), r.status_code


def test_no_public_response_carries_receipt_or_proof_data(client):
    """A customer must never be handed someone's payment receipt."""
    public = ["/api/site", "/api/products", "/api/categories", "/api/config",
              "/api/delivery", "/api/growth/state"]
    leaked = {}
    for path in public:
        r = client.get(path)
        if r.status_code != 200:
            continue
        body = r.get_data(as_text=True).lower()
        for needle in ("payment_proofs", "proofs/", "/object/sign/", "file_url"):
            if needle in body:
                leaked[path] = needle
    assert not leaked, f"public responses leaked receipt data: {leaked}"


def test_receipt_deletion_removes_the_row_and_the_file(client):
    """Admin delete must clear both the database row and the stored object."""
    import storage
    tok = client.post("/api/admin/login",
                      json={"email": EMAIL, "password": PW}).get_json()["csrf"]
    from db import execute as ex
    ex("INSERT INTO payment_proofs (order_id, name, email, method, amount, "
       "file_url, file_name, at) VALUES (?,?,?,?,?,?,?,datetime('now'))",
       ("JA-RCPT1", "R", "r@e.com", "bank", 100,
        "images/products/_placeholder.jpg", "proof.jpg"))
    row = one("SELECT id FROM payment_proofs WHERE order_id=?", ("JA-RCPT1",))
    assert row is not None
    deleted = []
    orig = storage.delete_upload
    storage.delete_upload = lambda url: deleted.append(url) or True
    try:
        r = client.delete(f"/api/admin/payment-proofs/{row['id']}",
                          headers={"X-CSRF-Token": tok})
    finally:
        storage.delete_upload = orig
    assert r.status_code == 200, r.get_json()
    assert one("SELECT id FROM payment_proofs WHERE id=?", (row["id"],)) is None, \
        "the receipt row survived the delete"
    assert deleted, "the stored file was not removed"


def test_receipt_delete_requires_csrf(client):
    from db import execute as ex
    ex("INSERT INTO payment_proofs (order_id, name, email, method, amount, "
       "file_url, file_name, at) VALUES (?,?,?,?,?,?,?,datetime('now'))",
       ("JA-RCPT2", "R", "r@e.com", "bank", 100,
        "images/products/_placeholder.jpg", "proof.jpg"))
    tok = client.post("/api/admin/login",
                      json={"email": EMAIL, "password": PW}).get_json()["csrf"]
    row = one("SELECT id FROM payment_proofs WHERE order_id=?", ("JA-RCPT2",))
    r = client.delete(f"/api/admin/payment-proofs/{row['id']}",
                      headers={"X-CSRF-Token": "not-the-token"})
    assert r.status_code in (400, 403), r.status_code
    assert one("SELECT id FROM payment_proofs WHERE id=?", (row["id"],)) is not None
