"""Underlying-issue hardening: save concurrency, production secret key,
stale-tab catalogue refresh, account profile rehydration and escaping.

1. PRODUCT SAVES ARE LAST WRITE WINS, and used not to be. An earlier pass
   added a 409 guard ("This product was changed by someone else while you
   were editing") that rejected any save whose editor had been opened before
   the stored row's updated_at moved on. It mis-fired constantly and locked
   admins out of their own shop: updated_at moves for reasons that have
   nothing to do with a human editing anything - the supplier watchdog
   writing a stock number, a cache re-hydration, a mirror, another tab that
   merely OPENED the product - and the admin's only way out was a popup
   telling them to close the editor and re-apply everything by hand.
   Saves are now unconditional and the newest complete copy wins, from any
   device, for any number of admins. The opened-at token is still sent, but
   only as a receipt: when a save replaced a newer row it is recorded in the
   audit trail and reported as information, so a crossed edit stays
   traceable without a blocking popup.
2. A PRODUCTION DEPLOYMENT WITHOUT SECRET_KEY BOOTS WITH A PUBLIC KEY.
   The repository-public development default signed admin session cookies.
   Production now falls back to a random per-boot secret and screams in the
   boot log; development keeps the default so nothing local breaks.
3. AN OPEN ADMIN TAB NEVER SEES OTHER ADMINS' WRITES. The catalogue is
   fetched once at boot; the tab now quietly refetches when it comes back
   to the foreground (never under an open editor, so in-progress edits are
   never repainted away).
4. THE SYNC PILL NEVER HANGS. A permanently failed outbox job used to be
   flagged `dead` and kept, where nothing ever removed it, so the indicator
   read "Syncing 1 change" for the rest of the session. It is now dropped
   once the admin has been told, and a job waiting out a retry backoff says
   "Retrying", not "Syncing".
5. SAVING EXITS TO THE SOURCE LIST. A confirmed save returns the admin to
   the exact page they opened the editor from (category, search, page,
   scroll), and the success banner is raised AFTER the redirect so it lands
   on that list rather than on an editor that is already gone.
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


# ------------------------------------- LAST WRITE WINS: saves never 409 (API)

def test_a_save_built_on_a_stale_copy_is_accepted_last_write_wins(client, admin):
    """The 409 "changed by someone else" guard is GONE.

    It used to reject any save whose editor had been opened before the stored
    row's updated_at moved on. That mis-fired constantly - the supplier
    watchdog writing a stock number, a cache re-hydration, a mirror, or a
    second tab merely OPENING the product all moved updated_at without a human
    ever editing - and locked admins out of their own shop with a popup they
    could not act on. The newest complete copy now simply wins.
    """
    r = admin({"product": _mk_product()})
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()["product"]["id"]
    row = _row(client, pid)
    assert row and row["name"] == "Concurrency Check Purse"

    # another admin saved in between: the stored updated_at moved on
    newer = admin({"product": dict(_mk_product(), name="Concurrency Purse v2")})
    assert newer.status_code == 200
    assert _row(client, pid)["name"] == "Concurrency Purse v2"

    # the FIRST admin's stale copy now saves instead of erroring
    stale = admin({"product": dict(_mk_product(), name="Concurrency Purse v3",
                                   baseUpdatedAt="2000-01-01T00:00:00Z")})
    assert stale.status_code == 200, stale.get_json()
    body = stale.get_json()
    assert body["ok"] is True
    # the save landed: last write wins
    assert _row(client, pid)["name"] == "Concurrency Purse v3"
    # ...and it is reported as a receipt, never as an error popup
    assert body.get("overwrote"), body  # quiet diagnostic metadata only
    assert "notice" not in body
    assert "error" not in body


def _py_code(path, strip_strings=False):
    """Return a Python file with comments (and optionally strings) blanked.

    These source pins have to check CODE, not prose: the explanatory comments
    deliberately quote the error message that was removed, and a naive
    substring check would either fail on that comment or force us to delete
    the explanation. tokenize is the only reliable way to tell the two apart.
    Tokens are overwritten in place (with spaces) so line numbers, column
    offsets and every other substring still match the real file.

    ``strip_strings`` also blanks string literals, which a pin needs when it
    is hunting a status code: a docstring MENTIONING 409 must never be
    mistaken for the code RETURNING one.
    """
    import io
    import tokenize
    src = open(path, encoding="utf-8").read()
    out = src.splitlines(keepends=True)
    skip = {tokenize.COMMENT}
    if strip_strings:
        skip.add(tokenize.STRING)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type not in skip:
            continue
        (r0, c0), (r1, c1) = tok.start, tok.end
        for r in range(r0, r1 + 1):
            line = out[r - 1]
            lo = c0 if r == r0 else 0
            hi = c1 if r == r1 else len(line)
            out[r - 1] = line[:lo] + " " * max(0, hi - lo) + line[hi:]
    return "".join(out)


def _py_function_source(path, name, strip_strings=False):
    """The exact source of one top-level function, comments removed."""
    import ast
    src = open(path, encoding="utf-8").read()
    node = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = _py_code(path, strip_strings=strip_strings).splitlines()
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


def test_no_save_surface_can_answer_409_any_more(client, admin):
    """Every alias funnels through _product_save_response, so one probe is
    enough to prove the whole surface is free of the blocking conflict."""
    guard = _py_function_source(os.path.join(ROOT, "api.py"),
                                "_product_save_response", strip_strings=True)
    assert "conflict=True" not in guard
    assert "product.save_conflict" not in guard
    # not a single 409 escapes the save funnel any more
    assert "409" not in guard
    # the funnel itself is unchanged: every save alias still routes through it
    assert _py_code(os.path.join(ROOT, "api.py")).count(
        "_product_save_response(") >= 4


def test_a_save_with_the_current_token_is_accepted(client, admin):
    r = admin({"product": _mk_product("jau-concurrency-2", "Fresh Token Purse")})
    pid = r.get_json()["product"]["id"]
    current = _row(client, pid)["updated_at"]
    ok = admin({"product": dict(_mk_product("jau-concurrency-2", "Fresh Token Purse"),
                                name="Fresh Token Purse v2",
                                baseUpdatedAt=current)})
    assert ok.status_code == 200, ok.get_json()
    assert _row(client, pid)["name"] == "Fresh Token Purse v2"
    # nothing to report: the row had not moved on
    assert not ok.get_json().get("overwrote")


def test_a_save_without_the_token_is_unaffected(client, admin):
    """API clients, imports and mirrors never send the token."""
    r = admin({"product": _mk_product("jau-concurrency-3", "No Token Purse")})
    pid = r.get_json()["product"]["id"]
    again = admin({"product": _mk_product("jau-concurrency-3", "No Token Purse")})
    assert again.status_code == 200
    assert not again.get_json().get("overwrote")


def test_the_freshness_token_is_never_persisted_on_the_row(client, admin):
    r = admin({"product": _mk_product("jau-concurrency-4", "Token Strip Purse")})
    pid = r.get_json()["product"]["id"]
    current = _row(client, pid)["updated_at"]
    ok = admin({"product": dict(_mk_product("jau-concurrency-4", "Token Strip Purse"),
                                baseUpdatedAt=current)})
    assert ok.status_code == 200
    row = _row(client, pid)
    assert "baseUpdatedAt" not in row and "base_updated_at" not in row


def test_an_overwriting_save_is_audited(client, admin):
    """A crossed edit stays traceable: the save that replaced a newer row is
    recorded, so "my change vanished" is explainable without a popup."""
    from db import query
    admin({"product": _mk_product("jau-concurrency-5", "Audit Purse")})
    admin({"product": dict(_mk_product("jau-concurrency-5", "Audit Purse"),
                           name="Audit Purse v2")})
    stale = admin({"product": dict(_mk_product("jau-concurrency-5", "Audit Purse"),
                                   name="Audit Purse v3",
                                   baseUpdatedAt="1999-01-01T00:00:00Z")})
    assert stale.status_code == 200
    rows = query("SELECT action, detail FROM audit_log"
                 " WHERE action='product.save_overwrite' ORDER BY id DESC LIMIT 1")
    assert rows and "jau-concurrency-5" in rows[0]["detail"]
    assert rows[0]["detail"].count("replaced=") == 1


def test_two_admins_editing_the_same_product_both_save(client, admin):
    """The multi-admin, multi-device case: neither save is ever refused."""
    pid = "jau-concurrency-6"
    assert admin({"product": _mk_product(pid, "Phone Edit Base")}).status_code == 200
    base = _row(client, pid)["updated_at"]
    phone = admin({"product": dict(_mk_product(pid, "Phone Edit Base"),
                                   name="Edited on the phone",
                                   baseUpdatedAt=base)})
    laptop = admin({"product": dict(_mk_product(pid, "Phone Edit Base"),
                                    name="Edited on the laptop",
                                    baseUpdatedAt=base)})
    assert phone.status_code == 200 and laptop.status_code == 200
    # last write wins, deterministically
    assert _row(client, pid)["name"] == "Edited on the laptop"


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
    # the merge machinery must travel on the REQUEST only, for the same
    # reason: a base copy or an edited-field list sitting in the local copy
    # would be replayed by a later outbox retry, hours out of date
    assert "delete next.mergeBase;" in fn
    assert "delete next.mergeFields;" in fn
    assert "Object.assign(" in fn
    assert "baseUpdatedAt: baseUpdatedAt" in fn
    assert "mergeBase: mergeBase" in fn
    assert "mergeFields: mergeFields" in fn
    # ...and a base that is not the row being saved is never shipped
    assert 'String(mergeBase.id) === String(next.id)' in fn


def test_the_409_rollback_path_is_gone_from_the_client():
    """The client used to roll the optimistic local edit back on a 409. With
    last-write-wins the server never answers 409 for a save, so that branch is
    dead code - and keeping it would be a trap if it ever fired."""
    js = _store_js()
    fn = js[js.index("function upsertProduct"):]
    fn = fn[:fn.index("function removeProduct")]
    assert "err.status === 409" not in fn
    assert "conflict: true" not in fn
    assert "prevCustom" not in fn
    assert "changed by someone else" not in fn


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

def test_the_offline_queue_never_retries_a_permanent_failure():
    """The request layer treats 4xx (except 429) as PERMANENT: a rejected
    call must reject the promise, never sit in the outbox to be resent."""
    js = open(os.path.join(ROOT, "js", "net.js"), encoding="utf-8").read()
    assert "r.status >= 500 || r.status === 429 || r.status === 0" in js
    assert "if (err.retryable) return enqueue(job, true);" in js


def test_a_permanently_failed_job_is_dropped_so_the_pill_closes():
    """The "Syncing 1 change" pill used to hang for the rest of the session.

    A dead job was flagged and KEPT in the outbox, and nothing ever removed it,
    so the indicator kept counting it forever. It is now dropped once the
    admin has been told, which returns the queue to empty and closes the pill.
    """
    js = open(os.path.join(ROOT, "js", "net.js"), encoding="utf-8").read()
    block = js[js.index("function flush(force)"):]
    block = block[:block.index("function api(path, opts)")]
    assert "drop(rec);" in block, "a dead job must leave the queue"
    # ...and it must only be dropped once it is genuinely unrecoverable
    dead = block[block.index("if (!err.retryable || rec.tries >= MAX_ATTEMPTS)"):]
    dead = dead[:dead.index("return idbPut(rec)")]
    assert "rec.dead = true;" in dead
    assert "JA.toast" in dead, "the admin must be told before it is dropped"


def test_the_sync_pill_distinguishes_syncing_from_waiting():
    """A job sleeping out its retry backoff is not "Syncing", and saying so
    made the pill look frozen. The label now says which it is."""
    js = open(os.path.join(ROOT, "js", "net.js"), encoding="utf-8").read()
    pill = js[js.index("function paintPill()"):]
    pill = pill[:pill.index('window.addEventListener("online"')]
    assert 'var live = visible.length - waiting;' in pill
    assert "Syncing " in pill and "Retrying " in pill


def test_the_sync_pill_slides_away_and_caps_its_lifetime():
    """A queued/offline status is brief feedback, never a permanent overlay."""
    js = open(os.path.join(ROOT, "js", "net.js"), encoding="utf-8").read()
    css = open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8").read()
    assert "PILL_AUTO_DISMISS_MS = 1400" in js
    assert "PILL_HARD_TIMEOUT_MS = 5000" in js
    assert "dismissPill(el, true)" in js
    assert 'el.classList.add("is-dismissing")' in js
    assert ".sync-pill.is-dismissing" in css
    assert "translate3d(calc(100% + 20px)" in css


def test_regular_network_requests_have_a_five_second_end_to_end_deadline():
    """The timeout includes CSRF, reCAPTCHA, headers and response-body reads."""
    js = open(os.path.join(ROOT, "js", "net.js"), encoding="utf-8").read()
    assert "Number(opts.timeout) || 5000" in js
    assert "var abortMs = job.timeout || (job.bodyKind === \"blob\" ? 300000 : 5000);" in js
    assert "Promise.race([attempt, timedOut])" in js
    # Failed/slow requests keep their durable outbox job but cannot recreate
    # the floating badge; the caller/background retry uses a temporary toast.
    api = js[js.index("function api(path, opts)"):js.index("function pending()")]
    assert "return enqueue(job, true);" in api
    flush = js[js.index("function flush(force)"):js.index("function api(path, opts)")]
    assert "is taking too long" in flush
    assert "rec.badgeHidden = true;" in flush


# --------------------------------------------------- api.py source pins

def test_the_token_is_consumed_by_one_funnel_that_covers_every_save_surface():
    src = open(os.path.join(ROOT, "api.py"), encoding="utf-8").read()
    assert 'payload.pop("baseUpdatedAt", "")' in src
    assert "product.save_overwrite" in src
    # the funnel: every save alias routes through _product_save_response
    assert src.count("_product_save_response(") >= 4


# ------------------------------- post-save exit to the source list (admin.js)

def test_saving_a_product_exits_to_the_captured_source_list():
    """Save must not leave the admin on the editor. The list they came from -
    category, search, page number, scroll - is captured when the editor opens
    and restored when the save is confirmed."""
    js = _admin_js()
    save = js[js.index("const res = await JA.upsertProduct({"):]
    save = save[:save.index("\nlet prodPage")]
    assert "restoreProductsReturn();" in save
    # the capture/restore pair is per-TAB, so two admins never overwrite each
    # other's list position
    assert "sessionStorage.setItem(PRODUCTS_RETURN_KEY" in js
    assert "function rememberProductsReturn()" in js
    assert "function restoreProductsReturn(" in js


def test_editor_return_url_captures_the_visible_category_and_search_controls():
    """The rendered filters win over stale module state when an editor opens."""
    js = _admin_js()
    url_fn = js[js.index("function productsReturnUrl()"):]
    url_fn = url_fn[:url_fn.index("function readProductsReturn()")]
    capture_fn = js[js.index("function rememberProductsReturn()"):]
    capture_fn = capture_fn[:capture_fn.index("function productsStateFromUrl(")]
    assert 'document.getElementById("prod-cat")' in url_fn
    assert 'document.getElementById("prod-search")' in url_fn
    assert 'document.getElementById("prod-cat")' in capture_fn
    assert 'document.getElementById("prod-search")' in capture_fn
    assert "const category = dashCat || String((catEl && catEl.value) || prodCatSel || \"\");" in capture_fn
    assert "const query = String((searchEl && searchEl.value) || prodSearchQ || \"\")" in capture_fn


def test_the_success_banner_lands_on_the_list_view_not_the_editor():
    """The toast is raised AFTER the redirect, so it is seen on the list the
    admin was returned to rather than on an editor that no longer exists."""
    js = _admin_js()
    save = js[js.index("const res = await JA.upsertProduct({"):]
    save = save[:save.index("\nlet prodPage")]
    assert save.index("restoreProductsReturn();") < save.index("JA.toast(savedMsg);")
    assert 'savedMsg = "Saved' in save


def test_save_success_toasts_never_include_overwrite_or_merge_notices():
    js = _admin_js()
    save = js[js.index("const res = await JA.upsertProduct({"):]
    save = save[:save.index("\nlet prodPage")]
    assert "data.notice" not in save
    assert "data.merged" not in save
    # no save may be refused for concurrency
    assert "conflict" not in save.lower()


def test_a_save_can_never_blank_a_products_category():
    """Saving must not take a product out of its category.

    The editor's category box is built from the category table, and that table
    is not always populated when the editor opens (a reload that raced the
    category load, a category hidden from this view, a slow API). The select
    then rendered with NO options, the form submitted an empty value, and the
    save wrote category:"" - quietly removing the product from its category
    and from every category-filtered list the owner had set up. The live
    check reproduced this against a server with an empty category table.
    """
    js = _admin_js()
    submit = js[js.index("async function handleProductSubmit("):]
    submit = submit[:submit.index("\nfunction ") if "\nfunction " in submit else len(submit)]
    assert "formCategory" in submit
    # an absent selection falls back to the row's own category
    assert 'formCategory || String((existing && existing.category) || "").trim()' in submit


def test_the_editor_always_offers_the_products_own_category():
    """Even with an empty category list, the stored category has to be a
    selectable option - otherwise the form is silently lying about the row."""
    js = _admin_js()
    form = js[js.index("function productForm(p = {})"):]
    form = form[:form.index("window.__editImages")]
    assert "allCats.concat([{ id: preCat, name: preCat }])" in form


def test_restoring_the_list_also_restores_the_filter_boxes():
    """Returning to the source list has to put the category box and the
    search box back too, not just filter the grid behind them: the admin was
    looking at an unfiltered-looking list, and the first tap on either control
    moved the products somewhere they did not ask for."""
    js = _admin_js()
    restore = js[js.index("function restoreProductsReturn(scrollToSaved)"):]
    restore = restore[:restore.index("function getFilteredProducts()")]
    assert 'document.getElementById("prod-cat")' in restore
    assert 'sel.value = dashCat || prodCatSel || "";' in restore
    assert 'document.getElementById("prod-search")' in restore

