"""Underlying-issue hardening: concurrent-edit safety, production secret
key, stale-tab catalogue refresh, account profile rehydration and escaping.

Three real defects found by the 2026-10-01 whole-site audit:

1. LAST-WRITE-WINS ON PRODUCT SAVES. The admin editor saves the ENTIRE
   product from the copy in the tab's memory, so a save built on a stale
   copy silently reverted whatever changed in between (another admin,
   another tab, an API integration). The editor now ships the row's
   updated_at as it was when the editor was OPENED (baseUpdatedAt); the
   server answers 409 instead of silently overwriting a newer row.
2. A PRODUCTION DEPLOYMENT WITHOUT SECRET_KEY BOOTS WITH A PUBLIC KEY.
   The repository-public development default signed admin session cookies.
   Production now falls back to a random per-boot secret and screams in the
   boot log; development keeps the default so nothing local breaks.
3. AN OPEN ADMIN TAB NEVER SEES OTHER ADMINS' WRITES. The catalogue is
   fetched once at boot; the tab now quietly refetches when it comes back
   to the foreground (never under an open editor - the 409 guard covers
   that window).
"""
import os
import subprocess
import sys

import pytest

import api as api_mod
import app as appmod
import catalog as catalog_mod
from db import init_db, query

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = "jaurastore@gmail.com"


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app, tmp_path, monkeypatch):
    init_db()
    path = tmp_path / "catalog.json"
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(path))
    api_mod._invalidate_catalog_cache()
    api_mod._invalidate_category_cache()
    with app.test_client() as c:
        yield c
    api_mod._invalidate_catalog_cache()
    api_mod._invalidate_category_cache()


@pytest.fixture()
def admin(client):
    login = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert login.status_code == 200
    token = login.get_json()["csrf"]

    def _save(payload):
        return client.post("/api/products",
                           headers={"X-CSRF-Token": token}, json=payload)
    return _save


def _row(client, pid):
    data = client.get("/api/catalog?all=1").get_json()
    return next((p for p in data["products"] if p.get("id") == pid), None)


def _mk_product(pid="jau-concurrency-1", name="Concurrency Check Purse"):
    return {"id": pid, "name": name, "sku": "CONC-1", "category": "accessories",
            "priceNgn": 9000, "stock": 4, "online": True,
            "images": ["images/products/3in1-towel.jpg"]}


# --------------------------------------------- the 409 freshness guard (API)

def test_a_save_built_on_a_stale_copy_is_rejected_not_silently_applied(client, admin):
    r = admin({"product": _mk_product()})
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()["product"]["id"]
    row = _row(client, pid)
    assert row and row["name"] == "Concurrency Check Purse"

    # pretend another admin saved in between: the stored updated_at moved on
    newer = admin({"product": dict(_mk_product(), name="Concurrency Purse v2")})
    assert newer.status_code == 200
    stored_after = _row(client, pid)
    assert stored_after["name"] == "Concurrency Purse v2"

    # now the FIRST admin saves their stale copy: must be rejected with 409
    # and must NOT revert the second admin's rename
    stale = admin({"product": dict(_mk_product(),
                                   baseUpdatedAt="2000-01-01T00:00:00Z")})
    assert stale.status_code == 409
    body = stale.get_json()
    assert body["ok"] is False and body.get("conflict") is True
    assert "changed by someone else" in body["error"]
    assert _row(client, pid)["name"] == "Concurrency Purse v2"


def test_a_save_with_the_current_token_is_accepted(client, admin):
    r = admin({"product": _mk_product("jau-concurrency-2", "Fresh Token Purse")})
    pid = r.get_json()["product"]["id"]
    current = _row(client, pid)["updated_at"]
    ok = admin({"product": dict(_mk_product("jau-concurrency-2", "Fresh Token Purse"),
                                name="Fresh Token Purse v2",
                                baseUpdatedAt=current)})
    assert ok.status_code == 200, ok.get_json()
    assert _row(client, pid)["name"] == "Fresh Token Purse v2"


def test_a_save_without_the_token_keeps_the_previous_behaviour(client, admin):
    """The guard is opt-in: API clients, imports and mirrors never send it."""
    r = admin({"product": _mk_product("jau-concurrency-3", "No Token Purse")})
    pid = r.get_json()["product"]["id"]
    again = admin({"product": _mk_product("jau-concurrency-3", "No Token Purse")})
    assert again.status_code == 200


def test_the_freshness_token_is_never_persisted_on_the_row(client, admin):
    r = admin({"product": _mk_product("jau-concurrency-4", "Token Strip Purse")})
    pid = r.get_json()["product"]["id"]
    current = _row(client, pid)["updated_at"]
    ok = admin({"product": dict(_mk_product("jau-concurrency-4", "Token Strip Purse"),
                                baseUpdatedAt=current)})
    assert ok.status_code == 200
    row = _row(client, pid)
    assert "baseUpdatedAt" not in row and "base_updated_at" not in row


def test_a_conflicting_save_is_audited(client, admin):
    from db import query
    admin({"product": _mk_product("jau-concurrency-5", "Audit Purse")})
    stale = admin({"product": dict(_mk_product("jau-concurrency-5", "Audit Purse"),
                                   baseUpdatedAt="1999-01-01T00:00:00Z")})
    assert stale.status_code == 409
    rows = query("SELECT action, detail FROM audit_log"
                 " WHERE action='product.save_conflict' ORDER BY id DESC LIMIT 1")
    assert rows and "jau-concurrency-5" in rows[0]["detail"]


# ------------------------------------- production SECRET_KEY never public

def test_production_boots_with_a_random_secret_never_the_public_default():
    code = (
        "import os, sys;"
        "os.environ.pop('SECRET_KEY', None);"
        "os.environ['FLASK_ENV'] = 'production';"
        "sys.path.insert(0, %r);"
        "import config;"
        "c = config.Config;"
        "assert c.SECRET_KEY_IS_RANDOM_FALLBACK is True, 'flag not set';"
        "assert c.SECRET_KEY != config.Config._INSECURE_DEFAULT_KEY, 'public default!';"
        "assert len(c.SECRET_KEY) >= 32, 'too short'"
    ) % ROOT
    subprocess.run([sys.executable, "-c", code], check=True,
                   capture_output=True, timeout=60)


def test_an_explicit_secret_key_is_always_honoured():
    code = (
        "import os, sys;"
        "os.environ['SECRET_KEY'] = 'owner-set-secret-value';"
        "os.environ['FLASK_ENV'] = 'production';"
        "sys.path.insert(0, %r);"
        "import config;"
        "c = config.Config;"
        "assert c.SECRET_KEY == 'owner-set-secret-value';"
        "assert c.SECRET_KEY_IS_RANDOM_FALLBACK is False"
    ) % ROOT
    subprocess.run([sys.executable, "-c", code], check=True,
                   capture_output=True, timeout=60)


def test_development_still_uses_the_shared_dev_default():
    code = (
        "import os, sys;"
        "os.environ.pop('SECRET_KEY', None);"
        "os.environ['FLASK_ENV'] = 'development';"
        "sys.path.insert(0, %r);"
        "import config;"
        "c = config.Config;"
        "assert c.SECRET_KEY == config.Config._INSECURE_DEFAULT_KEY;"
        "assert c.SECRET_KEY_IS_RANDOM_FALLBACK is False"
    ) % ROOT
    subprocess.run([sys.executable, "-c", code], check=True,
                   capture_output=True, timeout=60)


# --------------------------------------------------- admin.js source pins

def _admin_js():
    return open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()


def test_editor_stashes_the_freshness_token_when_it_opens():
    js = _admin_js()
    assert "window.__editBaseUpdatedAt = String((p && p.updated_at) || \"\");" in js
    # the stash is set at the same moment as the editor's other per-session
    # state, so it can never leak from one product's editor into another's
    assert js.index("window.__editImages = productImages(p);") < \
        js.index("window.__editBaseUpdatedAt")


def test_editor_ships_the_freshness_token_with_every_save():
    js = _admin_js()
    payload = js[js.index("JA.upsertProduct({"):]
    payload = payload[:payload.index("});") + 3]
    assert "baseUpdatedAt: window.__editBaseUpdatedAt" in payload


def test_open_admin_tab_refreshes_the_catalogue_when_it_regains_focus():
    js = _admin_js()
    block = js[js.index("fresh data on refocus"):]
    block = block[:block.index("});") + 3]
    assert 'document.addEventListener("visibilitychange"' in block
    assert "JA.reloadCatalog()" in block
    # NEVER repaint under an open editor - that would discard in-progress edits
    assert "if (editingId) return;" in block
    # throttled, so a tab-switcher cannot hammer the API
    assert "60000" in block


# -------------------------------------------------- store.js source pins

def _store_js():
    return open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()


def test_upsert_product_keeps_the_token_off_the_local_copy():
    js = _store_js()
    fn = js[js.index("function upsertProduct"):]
    fn = fn[:fn.index("function removeProduct")]
    assert "delete next.baseUpdatedAt;" in fn
    assert "Object.assign({}, next, { baseUpdatedAt: baseUpdatedAt })" in fn


def test_a_409_conflict_rolls_back_the_optimistic_local_edit():
    js = _store_js()
    fn = js[js.index("function upsertProduct"):]
    fn = fn[:fn.index("function removeProduct")]
    rollback = fn[fn.index("err.status === 409"):]
    rollback = rollback[:rollback.index("});") + 3]
    # the phantom edit must not survive on the tab, or a later outbox flush
    # would resend the stale copy
    assert "write(KEYS.custom, prevCustom);" in rollback
    assert "clearPending(next.id);" in rollback
    assert "conflict: true" in rollback


def test_escaping_is_pinned_on_the_product_render_paths():
    js = _store_js()
    # product name is escaped on the shop card link
    assert ">${escape(nm)}</a>" in js
    # media src / alt / data-ph are escaped inside mediaHTML
    assert 'src="${escape(assetSrc)}"' in js
    assert 'alt="${escape(opts.alt || "")}"' in js
    assert 'data-ph="${escape(ph)}"' in js
    # attribute values built from opts are escaped too
    assert '${k}="${escape(opts.attrs[k])}"' in js


# --------------------------------------------------- app.js source pins

def test_account_profile_save_rehydrates_the_whole_view():
    js = open(os.path.join(ROOT, "js", "app.js"), encoding="utf-8").read()
    handler = js[js.index('data-account-profile]")?.addEventListener'):]
    handler = handler[:handler.index("});") + 3]
    then = handler[handler.index(".then((d) => {"):]
    assert "renderAccount()" in then
    assert 'querySelector("[data-profile-msg]")' in then


# --------------------------------------------------- net.js source pins

def test_the_offline_queue_never_retries_a_conflict():
    """The 409 guard relies on the request layer treating 4xx (except 429)
    as PERMANENT: a conflict must reject the promise, never sit in the
    outbox to be resent later."""
    js = open(os.path.join(ROOT, "js", "net.js"), encoding="utf-8").read()
    assert "r.status >= 500 || r.status === 429 || r.status === 0" in js
    assert "if (err.retryable) return enqueue(job);" in js


# --------------------------------------------------- api.py source pins

def test_the_guard_is_opt_in_and_covers_every_save_surface():
    src = open(os.path.join(ROOT, "api.py"), encoding="utf-8").read()
    assert 'payload.pop("baseUpdatedAt", "")' in src
    assert "product.save_conflict" in src
    # the funnel: every save alias routes through _product_save_response
    assert src.count("_product_save_response(") >= 4
