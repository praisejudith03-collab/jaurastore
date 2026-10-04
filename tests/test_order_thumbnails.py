"""Every order line carries its product photo, all the way through.

An order line has to answer "which piece is this?" for three different pairs
of eyes: the admin packing the parcel on a phone, the customer reading the
confirmation email, and the customer on the receipt screen. The chain is:

  product row photo -> checkout line (`image`, resolved SERVER-side) ->
  stored order payload -> admin list / receipt / email thumbnail

Orders placed before the field existed get the photo backfilled from the live
catalogue when they are read, and a line whose product has no photo at all
falls back to the shop's branded placeholder rather than leaving a hole.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# --------------------------------------------------------------- the resolver
def test_primary_image_prefers_the_cover_then_the_gallery():
    import catalog
    assert catalog.primary_image({"image": "a.jpg", "images": ["b.jpg"]}) == "a.jpg"
    assert catalog.primary_image({"image_url": "u.jpg"}) == "u.jpg"
    assert catalog.primary_image({"images": ["", "b.jpg"]}) == "b.jpg"
    assert catalog.primary_image({}) == "", "no photo is not an invented path"


# ------------------------------------------------------- the checkout line
def test_checkout_lines_carry_the_photo_resolved_from_the_catalogue(monkeypatch):
    """The browser's cart never supplies the photo; the server resolves it."""
    import api
    import catalog as catalog_mod

    product = {"id": "jau-photo", "name": "Serum", "priceNgn": 12000, "online": True,
               "stock": 5, "image": "images/products/serum.jpg",
               "enableCustomNote": True}
    monkeypatch.setattr(catalog_mod, "merged", lambda include_hidden=False: [product])
    monkeypatch.setattr(catalog_mod, "product_index",
                        lambda rows=None: {"jau-photo": product})
    items, subtotal, error = api._checkout_items(
        [{"id": "jau-photo", "name": "Serum", "qty": 1, "color": "", "note": "Lavender"}],
        "NGN")
    assert error is None
    line = items[0]
    assert line["image"] == "images/products/serum.jpg", line
    assert line["note"] == "Lavender", "the note must survive next to the photo"


def test_an_order_line_without_a_photo_is_left_empty_not_faked(monkeypatch):
    import api
    import catalog as catalog_mod
    product = {"id": "jau-nophoto", "name": "Plain", "priceNgn": 1000, "online": True,
               "stock": 2}
    monkeypatch.setattr(catalog_mod, "merged", lambda include_hidden=False: [product])
    monkeypatch.setattr(catalog_mod, "product_index",
                        lambda rows=None: {"jau-nophoto": product})
    items, _subtotal, error = api._checkout_items(
        [{"id": "jau-nophoto", "name": "Plain", "qty": 1}], "NGN")
    assert error is None
    assert items[0]["image"] == ""


# ------------------------------------------------- the stored-order backfill
def test_old_order_lines_get_the_photo_from_the_live_catalogue(monkeypatch):
    import api
    import catalog as catalog_mod
    monkeypatch.setattr(catalog_mod, "merged", lambda include_hidden=False: [
        {"id": "jau-old", "name": "Old", "priceNgn": 10, "image": "images/products/old.jpg"},
    ])
    api._order_image_cache["map"] = {}
    api._order_image_cache["at"] = 0.0
    lines = api._order_items_with_images([
        {"id": "jau-old", "name": "Old", "qty": 1, "price": 10},
        {"id": "jau-gone", "name": "Gone", "qty": 1, "price": 10},
    ])
    assert lines[0]["image"] == "images/products/old.jpg"
    assert lines[1]["image"] == "", "an unknown id keeps no invented photo"


def test_a_stored_line_keeps_its_own_photo_over_the_current_catalogue(monkeypatch):
    """The photo the line was saved with wins: a later product edit must not
    rewrite history in an order record."""
    import api
    monkeypatch.setattr(api.catalog_mod, "merged",
                        lambda include_hidden=False: [{"id": "p", "image": "new.jpg"}])
    api._order_image_cache["map"] = {}
    lines = api._order_items_with_images([{"id": "p", "name": "P", "image": "kept.jpg"}])
    assert lines[0]["image"] == "kept.jpg"


def test_the_order_row_and_public_lookup_both_expose_the_photo(monkeypatch):
    import api
    import catalog as catalog_mod
    monkeypatch.setattr(catalog_mod, "merged", lambda include_hidden=False: [
        {"id": "jau-x", "name": "X", "image": "images/products/x.jpg"},
    ])
    api._order_image_cache["map"] = {}
    row = {"id": "JA-1", "payload": json.dumps({"items": [
        {"id": "jau-x", "name": "X", "qty": 1, "price": 10}]}),
        "proof_url": "", "at": "2026-10-01T10:00:00", "status": "pending",
        "total": 10, "currency": "NGN", "items_count": 1}
    assert api._order_row(row)["items"][0]["image"] == "images/products/x.jpg"


# ------------------------------------------------------------------- the email
def test_the_confirmation_email_shows_a_small_thumbnail_per_line():
    import mailer
    order = {"currency": "NGN", "subtotal": 5000, "total": 5000,
             "items": [{"name": "Rose oil", "qty": 1, "price": 5000, "note": "Lavender",
                        "image": "images/products/rose.jpg"}]}
    html, _total = mailer._items_table(order)
    assert "<img" in html and "images/products/rose.jpg" in html
    assert 'width="56"' in html and 'height="56"' in html, \
        "a fixed box keeps a 1200px photo from blowing up the inbox"
    assert "Rose oil" in html and "Lavender" in html


def test_the_email_thumbnail_is_absolute_and_absent_when_there_is_no_photo():
    import mailer
    html, _ = mailer._items_table({"currency": "NGN", "subtotal": 1, "total": 1,
                                   "items": [{"name": "A", "qty": 1, "price": 1,
                                              "image": "/uploads/products/x.jpg"}]})
    assert "<img" in html
    # SITE_ORIGIN is unset in tests, so a root-relative path must not be
    # turned into a broken "images/..." guess; it stays as stored.
    assert "/uploads/products/x.jpg" in html
    plain, _ = mailer._items_table({"currency": "NGN", "subtotal": 1, "total": 1,
                                    "items": [{"name": "B", "qty": 1, "price": 1}]})
    assert "<img" not in plain, "no photo means no empty image cell"


# --------------------------------------------------------------- the surfaces
def test_the_admin_order_list_renders_a_thumbnail_per_line():
    src = open(os.path.join(ROOT, "js/admin.js"), encoding="utf-8").read()
    assert "function orderItemThumb(" in src
    assert "order-item-thumb" in src
    assert "class=\"order-item\"" in src, "the line is a flex row with its photo"
    # A plain <img> with the shop's own onerror handler. NOT a <picture>: a
    # <source> that 404s breaks the image instead of falling back, and it
    # would fire the photo-healing report for a companion that was never
    # generated (see tests/test_photo_fix.py).
    assert "onerror=\"fallbackImg(event)\"" in src


def test_the_storefront_receipt_and_lines_render_small_photos():
    store = open(os.path.join(ROOT, "js/store.js"), encoding="utf-8").read()
    app = open(os.path.join(ROOT, "js/app.js"), encoding="utf-8").read()
    assert "function lineThumb(" in store and "lineThumb," in store
    assert "line-thumb" in store
    assert "JA.lineThumb" in app, "the receipt table must ask for the line photo"
    assert "receipt-line" in app
    assert 'image: i.product.image || ""' in app, \
        "the locally saved order line carries the photo for the instant receipt"


def test_the_thumbnail_css_boxes_are_fixed_size():
    css = open(os.path.join(ROOT, "css/style.css"), encoding="utf-8").read()
    for cls in (".order-item-thumb", ".line-thumb"):
        block = css[css.index(cls):]
        block = block[:block.index("}")]
        assert "width: 40px" in block and "height: 40px" in block, block
        assert "object-fit: cover" in block, "a portrait photo must not stretch the row"


def test_thumbnails_load_lazily_and_never_at_hero_priority():
    admin = open(os.path.join(ROOT, "js/admin.js"), encoding="utf-8").read()
    store = open(os.path.join(ROOT, "js/store.js"), encoding="utf-8").read()
    for name, src in (("admin", admin), ("store", store)):
        thumb = src[src.index("orderItemThumb") if name == "admin" else src.index("function lineThumb("):]
        thumb = thumb[:thumb.index("}\n") + 2] if name == "store" else thumb[:3000]
        assert 'loading="lazy"' in thumb, f"{name}: order thumbnails must be lazy"


def test_the_pdp_note_input_is_one_line_and_shows_the_merchant_prompt():
    app = open(os.path.join(ROOT, "js/app.js"), encoding="utf-8").read()
    css = open(os.path.join(ROOT, "css/style.css"), encoding="utf-8").read()
    assert 'name="productNote"' in app
    assert '<input type="text"' in app, "the note field is a single-line input"
    assert "notePrompt" in app and "customNotePrompt" in app, \
        "the merchant's own prompt string is shown as the label"
    block = css[css.index(".pdp-product-note input {"):]
    block = block[:block.index("}")]
    assert "height: 42px" in block and "max-height: 42px" in block, \
        "a phone must never get the tall multi-line box back"


# ------------------------------------------------- the whole chain, for real
def test_a_real_order_stores_the_photo_and_the_note_and_reads_back():
    """End to end through the Flask test client: cart line -> stored order.

    This is the chain the owner described: what she types on the product page
    (and the photo she chose for the piece) must reach the fulfilment record
    and come back on the customer's own completion screen.
    """
    import json as _json
    import app as app_mod
    import catalog as catalog_mod
    from db import execute, one

    product = {
        "id": "jau-thumb-e2e", "sku": "THUMBE2E", "slug": "thumb-e2e",
        "name": "Thumbnail Test Piece", "category": "beauty", "priceNgn": 5000,
        "stock": 5, "stock_quantity": 5, "online": True,
        "image": "images/products/2-in-1-lipstick-lipgloss-2400.jpg",
        "enableCustomNote": True, "customNotePrompt": "Enter preferred colour",
    }
    saved, _action, _mirrored = catalog_mod.upsert(product, "tester")
    assert saved is not None

    a = app_mod.create_app()
    a.config.update(TESTING=True)
    with a.test_client() as client:
        csrf = client.get("/api/config").get_json()["csrf"]
        body = {
            "id": "JA-THUMB01", "currency": "NGN", "total": 5000,
            "paymentMethod": "naira",
            "customer": {"name": "Ama Test", "firstName": "Ama", "lastName": "Test",
                         "email": "ama@example.com", "phone": "+234 800 000 0000",
                         "city": "Lagos", "zone": "Lagos Mainland", "country": "Nigeria",
                         "address": "12 Test Street"},
            "items": [{"id": "jau-thumb-e2e", "name": "Thumbnail Test Piece",
                       "qty": 1, "price": 5000, "color": "",
                       "note": "Lavender please"}],
        }
        resp = client.post("/api/orders", json=body, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.data[:400]
        row = one("SELECT payload FROM orders WHERE id='JA-THUMB01'")
        stored = _json.loads(row["payload"])["items"][0]
        assert stored.get("image") == "images/products/2-in-1-lipstick-lipgloss-2400.jpg", stored
        assert stored.get("note") == "Lavender please", stored
        lookup = client.get("/api/orders/JA-THUMB01").get_json()
        assert lookup["ok"] is True
        assert lookup["order"]["items"][0]["image"] == stored["image"]
        execute("DELETE FROM orders WHERE id='JA-THUMB01'")
