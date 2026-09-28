"""Per-product Open Graph / Twitter tags on /product.html.

Owner request 2026-09-28 (WhatsApp Broadcast Feed follow-up): link-preview
crawlers (WhatsApp, Facebook, Twitter/X, ...) never run the page's
JavaScript, so the client-side meta tag updates in js/store.js were
invisible to them - every shared product link previewed with the generic
store cover photo instead of the actual item. app.product_page() now
stamps the exact product's own name, active price and photo into the
static product.html response whenever ?id= names a live product, and
otherwise serves the page completely unchanged.

Run with:  python3 -m pytest tests/test_product_open_graph.py -q
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
from config import Config  # noqa: E402
from db import execute, init_db  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app, monkeypatch):
    init_db()
    execute("DELETE FROM rate_limits")
    monkeypatch.setattr(Config, "SITE_ORIGIN", "https://jaurastore.com.ng")
    with app.test_client() as c:
        yield c


def _override(monkeypatch, tmp_path, product):
    cat_file = tmp_path / "catalog.json"
    cat_file.write_text(json.dumps({"products": [product], "deleted": []}),
                        encoding="utf-8")
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(cat_file))


def _get(client, pid=None):
    url = "/product.html" + (f"?id={pid}" if pid else "")
    r = client.get(url)
    assert r.status_code == 200, r.data
    return r.get_data(as_text=True)


BASE = {
    "id": "wix-003", "name": "Sandwich Maker", "category": "household",
    "priceNgn": 69850, "compareNgn": None, "priceCfa": 28500,
    "image": "images/products/10in1-raf-sandwich-maker.jpg",
    "stock": 24, "online": True, "colors": [], "options": [],
}


def test_a_specific_product_link_carries_its_own_exact_photo(client, monkeypatch, tmp_path):
    # A different real repo photo than this product's own default, proving
    # the SPECIFIC product's resolved photo is used, not a generic one.
    _override(monkeypatch, tmp_path, {**BASE, "image": "images/products/12-pcs-dessini-pot-set-85000.jpg"})
    body = _get(client, "wix-003")
    assert 'og:image" content="https://jaurastore.com.ng/images/products/12-pcs-dessini-pot-set-85000.jpg"' in body
    assert 'twitter:image" content="https://jaurastore.com.ng/images/products/12-pcs-dessini-pot-set-85000.jpg"' in body
    # Never the generic store cover photo once a real product is resolved.
    assert "og-cover.jpg" not in body


def test_a_local_uploaded_photo_is_used_as_is(client, monkeypatch, tmp_path):
    # /uploads/<key> is this app's own same-origin link for an admin photo
    # upload (see storage.own_upload_path) - it must ride straight through,
    # the same as a committed repo path.
    _override(monkeypatch, tmp_path, {**BASE, "image": "/uploads/exact-item-photo.jpg"})
    body = _get(client, "wix-003")
    assert 'og:image" content="https://jaurastore.com.ng/uploads/exact-item-photo.jpg"' in body



def test_the_generic_tags_are_kept_when_no_id_is_given(client):
    body = _get(client)
    assert "<title>Product · Jaura Store</title>" in body
    assert "og-cover.jpg" in body


def test_the_generic_tags_are_kept_for_an_unknown_id(client):
    body = _get(client, "does-not-exist")
    assert "<title>Product · Jaura Store</title>" in body
    assert "og-cover.jpg" in body


def test_a_deleted_product_falls_back_to_the_generic_tags(client, monkeypatch, tmp_path):
    cat_file = tmp_path / "catalog.json"
    cat_file.write_text(json.dumps({"products": [], "deleted": ["wix-003"]}),
                        encoding="utf-8")
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", str(cat_file))
    body = _get(client, "wix-003")
    assert "og-cover.jpg" in body


def test_price_line_shows_both_currencies_and_never_the_compare_price(client, monkeypatch, tmp_path):
    _override(monkeypatch, tmp_path, {**BASE, "priceNgn": 10000, "compareNgn": 25000})
    body = _get(client, "wix-003")
    assert "₦10,000" in body
    assert "25,000" not in body and "25 000" not in body


def test_bilingual_name_appears_in_the_title_when_french_name_differs(client, monkeypatch, tmp_path):
    _override(monkeypatch, tmp_path, {**BASE, "name": "Sandwich Maker", "nameFr": "Appareil à sandwich"})
    body = _get(client, "wix-003")
    assert "<title>Sandwich Maker / Appareil à sandwich · Jaura Store</title>" in body
    assert 'og:title" content="Sandwich Maker / Appareil à sandwich · Jaura Store"' in body


def test_no_duplicate_name_when_the_french_name_matches_the_english_one(client, monkeypatch, tmp_path):
    _override(monkeypatch, tmp_path, {**BASE, "name": "Sandwich Maker", "nameFr": "sandwich maker"})
    body = _get(client, "wix-003")
    assert "<title>Sandwich Maker · Jaura Store</title>" in body
    assert " / " not in re.search(r"<title>(.*?)</title>", body).group(1)


def test_colours_and_sizes_appear_in_the_description_but_hex_codes_never_do(client, monkeypatch, tmp_path):
    _override(monkeypatch, tmp_path, {
        **BASE,
        "colors": ["Ash", "Blue"],
        "options": [{"title": "Colour", "type": "COLOR", "values": ["#E1BCC3"]},
                    {"title": "Size", "type": "DROP_DOWN", "values": ["S", "M", "L"]}],
    })
    body = _get(client, "wix-003")
    desc = re.search(r'og:description" content="([^"]*)"', body).group(1)
    assert "Ash" in desc and "Blue" in desc
    assert "S" in desc and "M" in desc and "L" in desc
    assert "#E1BCC3" not in desc


def test_html_in_a_product_name_is_escaped_not_injected(client, monkeypatch, tmp_path):
    _override(monkeypatch, tmp_path, {**BASE, "name": '"><script>alert(1)</script>'})
    body = _get(client, "wix-003")
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_the_canonical_and_og_url_point_at_this_exact_product(client, monkeypatch, tmp_path):
    _override(monkeypatch, tmp_path, BASE)
    body = _get(client, "wix-003")
    assert 'rel="canonical" href="https://jaurastore.com.ng/product.html?id=wix-003"' in body
    assert 'og:url" content="https://jaurastore.com.ng/product.html?id=wix-003"' in body
