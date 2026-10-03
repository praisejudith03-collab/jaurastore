"""The simplified product editor labels supplier links succinctly.

The create/edit form calls the product-level field "Main supplier URL" and
separates independent option URLs in their own section. Long watchdog prose and
legacy metadata labels stay out of the product editor.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _admin_src():
    src = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    return src


def test_supplier_fields_are_named_for_the_simplified_product_editor():
    src = _admin_src()
    start = src.index("function productForm(p = {})")
    end = src.index("async function handleProductSubmit", start)
    product_form = src[start:end]
    assert '<label>Main supplier URL</label>' in product_form
    assert 'name="supplierSku"' in product_form
    assert "Options and variant supplier URLs" in product_form
    assert "Supplier URL per option" in src


def test_no_long_helper_paragraph_around_the_supplier_field():
    for path in ("js/admin.js", "admin.html"):
        src = open(os.path.join(ROOT, path), encoding="utf-8").read()
        for frag in (
            "For single products, this controls the whole product stock",
            "For products with variants, the system will try to match",
        ):
            assert frag not in src, f"{path}: the long supplier helper text must not return"
