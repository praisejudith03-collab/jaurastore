"""Homepage Featured Products admin selector and storefront currency behaviour."""
import os
import shutil
import subprocess

import catalog as catalog_mod
import pytest

import app as appmod
from db import execute, init_db
from _pw import PW

EMAIL = "jaurastore@gmail.com"


@pytest.fixture()
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


class _Admin:
    def __init__(self, client, token):
        self._c = client
        self.token = token

    def _kw(self, kw):
        headers = dict(kw.pop("headers", None) or {})
        if self.token:
            headers.setdefault("X-CSRF-Token", self.token)
        kw["headers"] = headers
        return kw

    def get(self, url, **kw):
        return self._c.get(url, **self._kw(kw))

    def post(self, url, **kw):
        return self._c.post(url, **self._kw(kw))


@pytest.fixture()
def admin(app):
    init_db()
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        r = c.post("/api/admin/login", json={"email": EMAIL, "password": PW})
        assert r.status_code == 200, r.data
        yield _Admin(c, r.get_json()["csrf"])


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(ROOT, "tests", "_homepage_currency_dom_sim.mjs")


def _two_categories():
    by_cat = {}
    for p in catalog_mod.merged(include_hidden=True):
        if not p.get("id") or not p.get("category"):
            continue
        by_cat.setdefault(p["category"], []).append(p)
    cats = [c for c, rows in by_cat.items() if rows]
    assert len(cats) >= 2, "seed catalogue should cover multiple categories"
    return cats[0], by_cat[cats[0]][0], cats[1], by_cat[cats[1]][0]


def test_admin_homepage_featured_save_updates_public_payload(admin):
    cat_a, prod_a, cat_b, prod_b = _two_categories()
    payload = {cat_a: [prod_a["id"]], cat_b: [prod_b["id"]]}

    r = admin.post("/api/admin/homepage-featured", json={"categories": payload})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert body["featured"]["categories"][cat_a] == [prod_a["id"]]
    assert body["featured"]["categories"][cat_b] == [prod_b["id"]]
    assert {g["category"] for g in body["groups"]} >= {cat_a, cat_b}

    public = admin.get("/api/homepage-featured")
    assert public.status_code == 200
    data = public.get_json()
    assert data["featured"]["categories"][cat_a] == [prod_a["id"]]
    assert data["featured"]["categories"][cat_b] == [prod_b["id"]]
    assert [p["id"] for g in data["groups"] for p in g["products"]][:2] == [prod_a["id"], prod_b["id"]]

    catalog = admin.get("/api/catalog")
    assert catalog.status_code == 200
    meta_featured = catalog.get_json()["meta"]["homepageFeatured"]["categories"]
    assert meta_featured[cat_a] == [prod_a["id"]]
    assert meta_featured[cat_b] == [prod_b["id"]]


# =====================================================================
# Owner request 2026-09-28: the admin picker was one long scroll of every
# category's products. Each category must render as its own collapsed-by-
# default accordion instead.
# =====================================================================
def _admin_js():
    with open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8") as f:
        return f.read()


def test_the_admin_featured_picker_renders_categories_as_accordions():
    src = _admin_js()
    body = src[src.index("function paintHomepageFeaturedPicker("):]
    body = body[:body.index("\nfunction ")]
    assert "<details class=\"home-featured-admin-cat\"" in body
    assert "<summary class=\"admin-h\">" in body
    # All categories are collapsed by default on render
    assert "data-home-featured-admin-cat=" in body
    assert 'pickedHere ? "open" : ""' not in body
    assert "<details class=\"home-featured-admin-cat\" name=\"home-featured-cat-accordion\"" in body


def test_the_admin_featured_picker_includes_category_filter_input():
    src = _admin_js()
    body = src[src.index("function paintHomepageFeaturedPicker("):]
    body = body[:body.index("\nfunction ")]
    assert "data-home-featured-filter=" in body
    assert "home-featured-cat-search" in body
    bind_body = src[src.index("function bindHomepageFeatured("):]
    bind_body = bind_body[:bind_body.index("\nfunction ")]
    assert "data-home-featured-filter" in bind_body
    assert "choice.hidden" in bind_body


def test_each_featured_category_accordion_opens_independently():
    """<details> elements toggle independently by default; this pins
    down that the picker uses one <details> per category rather than a
    single shared open/close flag."""
    src = _admin_js()
    body = src[src.index("function paintHomepageFeaturedPicker("):]
    body = body[:body.index("\nfunction ")]
    assert "cats.map((c) =>" in body
    assert body.count("<details") == 1  # one template repeated per category


def test_the_featured_accordion_has_a_styled_toggle_marker():
    css_path = os.path.join(ROOT, "css", "style.css")
    with open(css_path, encoding="utf-8") as f:
        css = f.read()
    assert ".home-featured-admin-cat > summary::before" in css
    assert ".home-featured-admin-cat[open] > summary::before" in css


def test_homepage_currency_dom_updates_featured_and_most_viewed():
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node is not installed (frontend sim needs Node >= 18)")
    proc = subprocess.run([node, SIM], cwd=ROOT, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, (
        "homepage currency DOM simulation failed:\n" + proc.stdout + proc.stderr)
    assert "all homepage currency DOM checks passed" in proc.stdout
