"""Per-field merge: two admins editing one product must not undo each other.

Last-write-wins (added in the concurrency pass) guarantees a save is never
REFUSED, but it is row-level: the editor sends the whole product from its
in-memory copy, so a price typed on a phone is reverted by a photo swap made
on a laptop a minute later.

These tests pin the three-way merge that fixes that, and - just as important -
the case it would otherwise break. Comparing the edited row against the row
as it was opened CANNOT tell "the admin never opened this box" from "the admin
deliberately set it back to the same value". Both look identical, and "put the
price back to 5,000" is an ordinary thing for a shopkeeper to do. The editor
therefore reports which controls it actually touched, and these tests hold
that report to account.

The merge is a MERGE, never a guard: it decides which values win, and there
is no input for which it refuses a save.

Run with:  python3 -m pytest tests/test_product_field_merge.py -q
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")

import pytest  # noqa: E402

import product_merge as pm  # noqa: E402


def _base(**over):
    row = {
        "id": "jau-merge-1", "name": "Test Bag", "nameFr": "Sac",
        "priceNgn": 5000, "compareNgn": 0, "priceCfa": 2200, "compareCfa": None,
        "image": "a.jpg", "images": ["a.jpg", "b.jpg"],
        "stock": 5, "stock_quantity": 5, "stockStatus": "in",
        "category": "bags", "badge": "", "online": True, "featured": False,
        "optionStock": {"Red": 3, "Blue": 2},
        "optionPrices": {"Red": 5000, "Blue": 5000},
        "updated_at": "2026-10-01T10:00:00Z",
    }
    row.update(over)
    return row


def _stored(**over):
    """What the database looks like after the OTHER admin's save."""
    row = _base(priceNgn=7000, priceCfa=3080, image="c.jpg",
                images=["a.jpg", "b.jpg", "c.jpg"], stock=0, stock_quantity=0,
                stockStatus="out", optionStock={"Red": 3, "Blue": 0},
                updated_at="2026-10-01T11:00:00Z")
    row.update(over)
    return row


def _merge(edited, dirty=None, stored=None, base=None):
    return pm.merge_product_edit(base or _base(), edited,
                                 stored if stored is not None else _stored(),
                                 dirty=dirty)


# ------------------------------------------------- the two admins, two fields

def test_a_rename_does_not_revert_the_other_admins_price():
    """Admin A renames on the phone; admin B has already changed the price.
    A's save must keep B's price."""
    merged, kept = _merge(_base(name="Test Bag v2"), dirty=["name"])
    assert merged["name"] == "Test Bag v2", "the admin's own edit must win"
    assert merged["priceNgn"] == 7000, "a field they never touched was reverted"
    assert "priceNgn" in kept


def test_a_photo_swap_does_not_revert_the_other_admins_price():
    merged, _kept = _merge(_base(image="d.jpg", images=["d.jpg"]),
                           dirty=["image", "image_url", "imageUrl", "images"])
    assert merged["image"] == "d.jpg"
    assert merged["priceNgn"] == 7000


def test_both_edits_survive_when_they_are_applied_in_either_order():
    """A renames, then B prices. Neither loses."""
    after_a, _ = _merge(_base(name="Test Bag v2"), dirty=["name"])
    # B's editor opened BEFORE A's save, so B's base is the original row
    after_b, _ = _merge(_base(priceNgn=7000, priceCfa=3080, dirty=None),
                        dirty=["priceNgn", "priceCfa"], stored=dict(_stored(), name="Test Bag v2"))
    assert after_b["name"] == "Test Bag v2"
    assert after_b["priceNgn"] == 7000


# ------------------------------------------- the deliberate same-value edit

def test_deliberately_setting_a_field_back_to_its_original_value_is_honoured():
    """THE case a base-comparison merge silently loses.

    The admin types 5,000 into the price box - the value it already had. The
    stored row meanwhile says 7,000. "The value equals the base" looks exactly
    like "nobody touched it", and guessing that way discards a deliberate
    edit. The editor's report of what was touched is what settles it.
    """
    merged, kept = _merge(_base(priceNgn=5000, priceCfa=2200),
                          dirty=["priceNgn", "priceCfa"])
    assert merged["priceNgn"] == 5000, "a deliberate re-price was discarded"
    assert merged["priceCfa"] == 2200
    assert "priceNgn" not in kept


def test_clearing_a_field_deliberately_is_honoured():
    """Emptying a badge is an edit, not an absence."""
    merged, _ = _merge(_base(badge="", ), dirty=["badge"], stored=_stored(badge="Sale"))
    assert merged["badge"] == ""


def test_untouched_fields_still_fall_back_to_the_base_comparison():
    """No edited-field list (an older client, an API integration): the
    comparison still protects the obvious case rather than doing nothing."""
    merged, _ = _merge(_base(name="Test Bag v2"), dirty=None)
    assert merged["name"] == "Test Bag v2"
    assert merged["priceNgn"] == 7000


# ------------------------------------------------------- per-variant merging

def test_one_variants_quantity_cannot_wipe_anothers():
    merged, kept = _merge(_base(optionStock={"Red": 3, "Blue": 2}),
                          dirty=[], stored=_stored(optionStock={"Red": 9, "Blue": 0}))
    assert merged["optionStock"] == {"Red": 9, "Blue": 0}
    assert "optionStock.Red" in kept and "optionStock.Blue" in kept


def test_editing_the_variant_editor_keeps_the_admin_whole_map():
    merged, _ = _merge(_base(optionStock={"Red": 3, "Blue": 9}),
                       dirty=["optionStock", "options", "stock", "stock_quantity"],
                       stored=_stored(optionStock={"Red": 9, "Blue": 0}))
    assert merged["optionStock"] == {"Red": 3, "Blue": 9}


def test_a_variant_the_editors_copy_never_saw_is_not_dropped():
    """A save from an older tab must not delete a variant added meanwhile."""
    merged, _ = _merge(_base(optionStock={"Red": 3}),
                       dirty=[], stored=_stored(optionStock={"Red": 3, "Green": 7}))
    assert merged["optionStock"].get("Green") == 7


def test_watchdog_stock_is_not_resurrected_by_an_unrelated_save():
    """The supplier watchdog writing "out of stock" must survive a rename."""
    merged, _ = _merge(_base(name="Test Bag v2"), dirty=["name"])
    assert merged["stock"] == 0
    assert merged["stockStatus"] == "out"


# --------------------------------------------------------------- scope rules

def test_server_owned_fields_are_never_taken_from_the_base():
    """The slug and the timestamps belong to the server, not to the editor's
    copy of a row that may be hours old."""
    old = _base(slug="old-slug", source="admin", legacyId="wix-999")
    merged, _ = pm.merge_product_edit(old, _base(slug="old-slug", source="admin"),
                                      _stored(slug="new-slug", source="admin",
                                              legacyId="wix-999"), dirty=[])
    assert merged["slug"] == "old-slug"      # what the editor sent...
    assert merged["source"] == "admin"
    # ...and catalog.upsert re-derives the slug, which the API test proves.


def test_fields_outside_the_allowlist_keep_last_write_wins():
    """Only fields we understand are merged. Anything else behaves exactly as
    it did before, so a merge can never quietly change shop data we have not
    thought about."""
    assert "bulkQty" not in pm.MERGEABLE_FIELDS
    stored = _stored(bulkQty=50)
    merged, kept = _merge(_base(bulkQty=10), dirty=[], stored=stored)
    assert merged["bulkQty"] == 10, "an unlisted field must be the admin's"
    assert "bulkQty" not in kept


def test_numeric_and_missing_values_do_not_read_as_conflicts():
    """1 and 1.0 are the same price; an absent key and None are the same
    'nothing set'. Treating them as different would report a conflict on
    every numeric field and switch the merge off."""
    assert pm._same(1, 1.0)
    assert pm._same(None, pm._MISSING)
    assert pm._same("", None)
    assert not pm._same(1, 2)
    assert not pm._same("Red", "Blue")


# ------------------------------------------------------------- the guard rails

def test_a_merge_is_only_attempted_for_the_same_existing_row():
    assert pm.merge_is_worthwhile(_base(), _base(), _stored()) is True
    # a base for a DIFFERENT row
    assert pm.merge_is_worthwhile(_base(id="other"), _base(), _stored()) is False
    # a brand new product has nothing to merge with
    assert pm.merge_is_worthwhile(_base(), _base(), None) is False
    assert pm.merge_is_worthwhile(_base(), {"name": "no id"}, _stored()) is False


def test_a_merge_never_invents_a_field_the_admin_did_not_send():
    merged, _ = _merge(_base(name="v2"), dirty=["name"])
    assert set(merged) == set(_base()), "the payload's own fields only"


# ------------------------------------------------------------ through the API

@pytest.fixture(scope="module")
def app():
    import app as appmod
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app, tmp_path, monkeypatch):
    import api as api_mod
    import catalog as catalog_mod
    from db import init_db
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
    from _pw import PW
    login = client.post("/api/admin/login",
                        json={"email": "jaurastore@gmail.com", "password": PW})
    assert login.status_code == 200
    token = login.get_json()["csrf"]

    def _save(payload):
        return client.post("/api/products",
                           headers={"X-CSRF-Token": token}, json=payload)
    return _save


def _row(client, pid):
    data = client.get("/api/catalog?all=1").get_json()
    return next((p for p in data["products"] if p.get("id") == pid), None)


def _mk(**over):
    return {"id": "jau-merge-api-1", "name": "API Merge Bag", "category": "bags",
            "priceNgn": 5000, "stock": 5, "online": True, "images": [], **over}


def test_the_api_merges_per_field_and_still_answers_200(client, admin):
    created = admin({"product": _mk()})
    assert created.status_code == 200, created.get_json()
    base = _row(client, "jau-merge-api-1")

    # The other admin re-prices while this editor is open.
    other = admin({"product": _mk(priceNgn= 7000)})
    assert other.status_code == 200

    # This admin renames, shipping the row as it was when they opened it.
    r = admin({"product": _mk(name="API Merge Bag v2", mergeBase=base,
                              mergeFields=["name"])})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] is True
    assert body.get("merged") is True
    assert "priceNgn" in (body.get("kept") or [])
    assert "notice" not in body  # merge metadata is quiet; audit log is retained

    row = _row(client, "jau-merge-api-1")
    assert row["name"] == "API Merge Bag v2", "the admin's edit must be live"
    assert row["priceNgn"] == 7000, "the other admin's price was reverted"


def test_a_deliberate_re_price_through_the_api_is_honoured(client, admin):
    admin({"product": _mk()})
    base = _row(client, "jau-merge-api-1")
    admin({"product": _mk(priceNgn=7000)})

    # The admin types 5000 again - the value they started from.
    r = admin({"product": _mk(priceNgn=5000, mergeBase=base,
                              mergeFields=["priceNgn"])})
    assert r.status_code == 200
    assert _row(client, "jau-merge-api-1")["priceNgn"] == 5000


def test_a_save_without_a_base_is_still_plain_last_write_wins(client, admin):
    """API integrations, CSV imports and the supplier watchdog send no base
    copy, and must keep exactly the behaviour they had."""
    admin({"product": _mk()})
    admin({"product": _mk(priceNgn=7000)})
    r = admin({"product": _mk(priceNgn=1234)})
    assert r.status_code == 200
    body = r.get_json()
    assert "merged" not in body and "kept" not in body
    assert _row(client, "jau-merge-api-1")["priceNgn"] == 1234


def test_a_base_for_a_different_row_is_ignored(client, admin):
    admin({"product": _mk()})
    r = admin({"product": _mk(priceNgn=4321,
                              mergeBase=dict(_row(client, "jau-merge-api-1"), id="someone-else"),
                              mergeFields=["priceNgn"])})
    assert r.status_code == 200
    assert "merged" not in r.get_json()
    assert _row(client, "jau-merge-api-1")["priceNgn"] == 4321


def test_the_merge_can_never_answer_409(client, admin):
    """The whole point of the concurrency pass was that no save is ever
    refused. The merge must not reintroduce a failure of any kind."""
    admin({"product": _mk()})
    base = _row(client, "jau-merge-api-1")
    admin({"product": _mk(stock=1)})
    r = admin({"product": _mk(name="v3", mergeBase=base, mergeFields=["name"],
                              baseUpdatedAt="2000-01-01T00:00:00Z")})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_the_merge_base_is_never_persisted_on_the_row(client, admin):
    admin({"product": _mk()})
    base = _row(client, "jau-merge-api-1")
    admin({"product": _mk(name="v2", mergeBase=base, mergeFields=["name"])})
    row = _row(client, "jau-merge-api-1")
    for leaked in ("mergeBase", "mergeFields", "baseUpdatedAt"):
        assert leaked not in row, f"{leaked} was written onto the product row"


def test_a_field_the_base_never_had_is_the_admins_own_value():
    """The base row the editor snapshots is the PUBLIC catalogue projection,
    which deliberately omits `stock`. An absent key is UNKNOWN, not
    "unchanged": reading it as unchanged made the merge overwrite the admin's
    own stock with the stored one while announcing it had "preserved a newer
    change". No evidence means no preservation."""
    base = {"id": "jau-merge-proj", "name": "Bag", "priceNgn": 5000,
            "image": "a.jpg", "category": "bags"}          # note: no stock
    edited = dict(base, name="Bag v2", stock=9)
    stored = dict(base, priceNgn=7000, image="c.jpg", stock=24)
    merged, kept = pm.merge_product_edit(base, edited, stored, dirty=["name"])
    assert merged["stock"] == 9, "the admin's own stock edit was discarded"
    assert "stock" not in kept
    # ...while a field the base DID know about is still protected
    assert merged["priceNgn"] == 7000
    assert "priceNgn" in kept


def test_a_variant_key_the_base_never_had_is_kept_not_trusted(client=None):
    """Same rule inside a per-variant map."""
    base = {"id": "jau-merge-proj2", "optionStock": {"Red": 3}}
    edited = dict(base, optionStock={"Red": 3, "Blue": 1})
    stored = dict(base, optionStock={"Red": 3, "Blue": 0, "Green": 5})
    merged, _ = pm.merge_product_edit(base, edited, stored, dirty=[])
    assert merged["optionStock"]["Blue"] == 1, "the admin's new variant was overwritten"
    assert merged["optionStock"]["Green"] == 5, "an unknown variant was dropped"


def test_an_overwrite_is_audited(client, admin):
    from db import query
    admin({"product": _mk(id="jau-merge-api-2")})
    base = _row(client, "jau-merge-api-2")
    admin({"product": _mk(id="jau-merge-api-2", stock=2)})
    admin({"product": _mk(id="jau-merge-api-2", name="Merged", mergeBase=base,
                          mergeFields=["name"])})
    rows = query("SELECT action, detail FROM audit_log "
                 "WHERE action='product.save_merged' ORDER BY id DESC LIMIT 1")
    assert rows, "a merge that preserved newer fields must be recorded"
    assert "jau-merge-api-2" in rows[0]["detail"]


# ------------------------------------------------------- client-side wiring

def _admin_js():
    return open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()


def test_the_editor_snapshots_the_row_when_it_opens():
    js = _admin_js()
    assert "window.__editBase = p && p.id ? JSON.parse(JSON.stringify(p)) : null;" in js
    # a deep copy, or a later mutation of the same object would be taken for
    # "the row as it was when the editor opened"
    assert "JSON.parse(JSON.stringify(p))" in js


def test_the_editor_resets_the_touched_set_per_product():
    """Otherwise a second product's save would claim to have edited fields the
    admin only ever touched on the first one."""
    js = _admin_js()
    form = js[js.index("function productForm(p = {})"):]
    form = form[:form.index("async function handleProductSubmit")]
    # reset with the rest of the per-editor session state
    assert "window.__editDirty = new Set();" in form
    assert "window.__editBase =" in form
    assert form.index("window.__editDirty = new Set();") < \
        form.index("window.__editUploads = [];")


def test_touching_a_control_records_the_fields_it_writes():
    """The mapping is what makes the merge honest, so it is pinned field by
    field: money, media and stock each travel with the control that edits
    them, because the editor writes several payload fields per control."""
    js = _admin_js()
    table = js[js.index("const EDIT_FIELD_MAP = {"):]
    table = table[:table.index("};") + 2]
    for control, fields in (
        ("name", ["name"]),
        ("priceNgn", ["priceNgn", "priceCfa"]),
        ("category", ["category"]),
        ("supplierSku", ["supplierSku", "supplierUrl", "supplier_url"]),
        ("stock", ["stock", "stock_quantity", "stockStatus"]),
        ("enableCustomNote", ["enableCustomNote"]),
        ("customNotePrompt", ["customNotePrompt"]),
    ):
        assert f"{control}:" in table, control
        for f in fields:
            assert f'"{f}"' in table, f"{control} must also mark {f}"
    # the media strip and variant rows use delegated data-* controls
    for marker in ("data-img-i", "data-opt-row", "data-opt-stock",
                   "data-opt-supplier", "data-var-row"):
        assert f'"{marker}"' in js
    # and the listener is delegated, so re-rendered rows still count
    form = js[js.index('const form = $("#prod-form");'):]
    form = form[:form.index('addEventListener("change", (e) => trackEditedField(e.target), true);')
                + len('addEventListener("change", (e) => trackEditedField(e.target), true);')]
    assert 'addEventListener("input", (e) => trackEditedField(e.target), true)' in form
    assert 'addEventListener("change", (e) => trackEditedField(e.target), true)' in form


def test_the_edited_field_list_ships_with_the_save():
    js = _admin_js()
    payload = js[js.index("JA.upsertProduct({"):]
    payload = payload[:payload.index("});") + 3]
    assert "mergeBase: window.__editBase || null," in payload
    assert "mergeFields: window.__editDirty ? Array.from(window.__editDirty) : null," in payload
