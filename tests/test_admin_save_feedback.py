"""Every confirmed admin save is a green toast and returns to the list.

Directive 2026-10-19. The neutral toast still exists for storefront notices;
saves use the success variant. Product saves redirect before the toast lands;
category photo / category save follow the same contract.

Run with: python3 -m pytest tests/test_admin_save_feedback.py -q
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_success_toast_is_green_and_cannot_be_confused_with_an_error():
    css = _read("css", "style.css")
    assert '.toast[data-kind="success"]' in css
    assert "#1f7a4d" in css
    assert '.toast[data-kind="error"]' in css


def test_toast_accepts_a_kind_and_exports_a_success_helper():
    js = _read("js", "store.js")
    assert "function toast(msg, options)" in js
    assert 'data-kind' in js
    assert "function success(msg)" in js
    assert "toast, upsertProduct, success" in js


def test_product_save_redirects_first_and_then_raises_the_green_toast():
    js = _read("js", "admin.js")
    save = js[js.index("const res = await JA.upsertProduct({"):]
    save = save[:save.index("\nlet prodPage")]
    assert save.index("restoreProductsReturn();") < save.index("JA.success(savedMsg);")


def test_category_save_and_photo_upload_return_to_the_catalog_list():
    js = _read("js", "admin.js")
    assert "const persist = async (msg, options)" in js
    assert "restoreProductsReturn();" in js
    assert "Asset saved — banner will use this image on shop page." in js
    assert "{ redirect: true }" in js
    assert "JA.success(text);" in js


def test_the_main_admin_saves_use_the_success_variant():
    js = _read("js", "admin.js")
    for message in (
        "Photo update is live; unused replaced files were deleted.",
        "Settings saved — live on the site now.",
        "Homepage featured products saved — live now.",
        "Zone saved — live now.",
        "Social links saved — live on the storefront now.",
    ):
        assert 'JA.success("' + message + '");' in js, message
