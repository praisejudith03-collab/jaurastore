"""French is complete: no raw English dictionary value, every shipped
description has a French copy, and the Supabase read path fills it.

Run with: python3 -m pytest tests/test_french_copy_complete.py -q
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")


def _i18n():
    return open(os.path.join(ROOT, "js", "i18n.js"), encoding="utf-8").read()


def _dict_block(src, name):
    start = src.index("const " + name + " = {")
    start = src.index("{", start)
    end = src.index("\n  };", start)
    return src[start:end]


def _pairs(src, name):
    block = _dict_block(src, name)
    return dict(re.findall(r'^\s*"([^"]+)":\s*"(.*)",?$', block, re.M))


def test_french_dictionary_keys_cover_every_english_key():
    src = _i18n()
    en, fr = _pairs(src, "en"), _pairs(src, "fr")
    missing = sorted(set(en) - set(fr))
    assert not missing, f"French keys missing: {missing}"
    assert len(fr) == len(en), (len(fr), len(en))


def test_no_french_dictionary_value_is_known_raw_english():
    fr = _pairs(_i18n(), "fr")
    banned = {
        "ck.uploadHere": "Upload payment receipt",
        "ck.togoFeeNotice": "Moov Money Togo may charge a fee for cross-border transfers.",
        "toast.langEn": "Language: English",
    }
    for key, english in banned.items():
        assert fr[key] != english, f"{key} is still raw English"
    assert "Upload payment receipt" not in fr["ck.uploadHere"]
    assert "may charge a fee" not in fr["ck.togoFeeNotice"]
    assert "SHOP LUXE" not in fr["ticker"]
    assert fr["ck.uploadHere"].startswith(("Télé", "Tele"))


def test_every_shipped_english_description_has_a_french_translation():
    seed = json.load(open(os.path.join(ROOT, "data", "seed.json"), encoding="utf-8"))
    table = json.load(open(os.path.join(ROOT, "data", "descriptions_fr.json"),
                           encoding="utf-8"))
    missing = [p["id"] for p in seed
               if str(p.get("description") or "").strip()
               and not str(p.get("descriptionFr") or "").strip()]
    assert not missing, f"seed descriptions without descriptionFr: {missing}"
    uncovered = [p["id"] for p in seed
                 if str(p.get("description") or "").strip() and p["id"] not in table]
    assert not uncovered, f"translation table does not cover: {uncovered}"
    assert len(table) >= 52


def test_the_bundled_offline_snapshot_has_the_same_french_copy():
    raw = open(os.path.join(ROOT, "js", "products-data.js"), encoding="utf-8").read()
    prefix = "window.JA_SEED = "
    body = raw[len(prefix):].rstrip()
    if body.endswith(";"):
        body = body[:-1]
    rows = json.loads(body)
    missing = [p["id"] for p in rows
               if str(p.get("description") or "").strip()
               and not str(p.get("descriptionFr") or "").strip()]
    assert not missing, missing


def test_the_server_fills_a_blank_french_description_for_supabase_rows():
    import catalog
    row = {"id": "wix-003", "name": "10in1 RAF sandwich maker",
           "description": "Non sticking coating", "descriptionFr": ""}
    out = catalog._with_french_description(row)
    assert out["descriptionFr"] == "Revêtement antiadhésif"
    # An owner-written French paragraph is never replaced.
    owned = {"id": "wix-003", "descriptionFr": "Ma propre traduction"}
    assert catalog._with_french_description(owned)["descriptionFr"] == "Ma propre traduction"
    # Unknown copy is left alone rather than invented.
    unknown = {"id": "jau-new", "description": "A brand new piece"}
    assert not str(catalog._with_french_description(unknown).get("descriptionFr") or "").strip()


def test_the_admin_editor_can_write_a_french_description():
    js = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    assert 'name="descriptionFr"' in js
    assert 'descriptionFr: ["descriptionFr"]' in js
    assert 'descriptionFr: String(fd.get("descriptionFr")' in js
    assert "Description (Français)" in js


def test_a_french_description_survives_normalisation_and_storage_aliases():
    import catalog
    clean = catalog.normalize({"id": "jau-fr-1", "name": "Test",
                               "description": "English copy",
                               "descriptionFr": "Copie française"})
    assert clean["descriptionFr"] == "Copie française"
    snake = catalog.normalize({"id": "jau-fr-2", "name": "Test",
                               "description_fr": "Copie française snake"})
    assert snake["descriptionFr"] == "Copie française snake"


def test_storefront_status_copy_is_read_through_the_dictionary():
    """A French shopper must not read English from a toast or a stock line.

    The cart/storefront contract pins two canonical English sentences ("Out of
    Stock", "Only N items remaining in stock") in js/store.js and js/app.js,
    and the server answers in English with a machine code. Both are fine as
    *data* - they are the contract with the API - but the shopper must read the
    active language, so the storefront passes them through tx()/t() at the
    moment they are shown.
    """
    store = open(os.path.join(ROOT, "js", "store.js"), encoding="utf-8").read()
    app = open(os.path.join(ROOT, "js", "app.js"), encoding="utf-8").read()
    net = open(os.path.join(ROOT, "js", "net.js"), encoding="utf-8").read()
    assert 'toast(tx("store.paused"))' in store
    assert 'toast(tx("cart.oos"))' in store
    assert 'toast(tx("cart.onlyLeft", { n: remaining }))' in store
    assert 'return tx("cart.verifyFailed")' in store
    # The English source strings stay (the API contract depends on them).
    assert "Only ${remaining} items remaining in stock" in store
    assert "Only ${room} items remaining in stock" in app
    assert "stockCopy(qtyStockMessage)" in app
    assert 'JA.toast(t("contact.whatsapp"))' in app
    assert '"sync.sending"' in net


def test_the_new_status_keys_are_translated_not_copied():
    en, fr = _pairs(_i18n(), "en"), _pairs(_i18n(), "fr")
    for key in ("store.paused", "cart.oos", "cart.onlyLeft",
                "cart.verifyFailed", "contact.whatsapp", "sync.sending"):
        assert key in en and key in fr, key
        assert fr[key] != en[key], f"{key} is not translated"
