"""Admin product form pins the supplier-link field wording.

Owner directive (2026-10-01):
  * the product form's supplier field label reads EXACTLY
    "Supplier URL for Auto Stock Sync";
  * the form stays clean - the long explainer paragraph
    ("For single products, this controls the whole product stock. For
    products with variants...") must never appear again;
  * the sync itself runs quietly in the background (watchdog), which
    tests/test_no_background_supplier_sync.py pins;
  * a supplier link already on a product can never be wiped by any save
    path that later touches the product (covered by
    tests/test_supplier_stock_rules.py).

Run with:  python3 -m pytest tests/test_supplier_form_label.py -q
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _admin_src():
    src = open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()
    return src


def test_supplier_field_label_is_exact():
    src = _admin_src()
    assert "Supplier URL for Auto Stock Sync" in src, (
        "the admin product form must label the supplier field "
        "exactly 'Supplier URL for Auto Stock Sync'")


def test_no_long_helper_paragraph_around_the_supplier_field():
    for path in ("js/admin.js", "admin.html"):
        src = open(os.path.join(ROOT, path), encoding="utf-8").read()
        for frag in (
            "For single products, this controls the whole product stock",
            "For products with variants, the system will try to match",
        ):
            assert frag not in src, f"{path}: the long supplier helper text must not return"
