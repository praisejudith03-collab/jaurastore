"""The catch-all route must publish the storefront - and nothing else.

The storefront is flat files at the repo root, but so is everything else: the
Python sources, the SQLite database, the CI config and - until this was fixed -
a stray copy of git's own internals. `static_for()` used to serve any file that
existed, so `/config.py` answered 200 in production and handed out the public
admin bootstrap password along with it.

These checks pin both halves of the fix: every asset the shop needs still
answers 200, and every file that is not an asset answers 404 with the shop's
own 404 page. One of them walks every href/src in every page, so the allowlist
can never silently take the shop down.
"""
import hashlib
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SCHEDULER_ENABLED", "0")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

import app as appmod  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def app():
    return appmod.create_app()


@pytest.fixture()
def client(app):
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


# --------------------------------------------------------- 1. the shop works
ALLOWED_PATHS = [
    "/", "/index.html", "/shop.html", "/product.html", "/cart.html",
    "/checkout.html", "/admin.html", "/404.html", "/css/style.css",
    "/js/net.js", "/js/app.js", "/js/store.js", "/sw.js", "/robots.txt",
]


@pytest.mark.parametrize("path", ALLOWED_PATHS)
def test_storefront_asset_is_served(client, path):
    assert client.get(path).status_code == 200, path


def test_robots_txt_is_allowed_by_name(client):
    """.txt is not an allowed extension - robots.txt is allowed as a name."""
    assert client.get("/robots.txt").status_code == 200


def test_root_logo_is_served(client):
    assert client.get("/logo.png").status_code == 200


def test_brand_assets_are_served(client):
    assert client.get("/images/brand/logo.jpg").status_code == 200


# ------------------------------------------------- 2. the repository is not
BLOCKED_PATHS = [
    "/config.py", "/auth.py", "/security.py", "/api.py", "/db.py",
    "/render.yaml", "/requirements.txt", "/.env.example", "/.gitignore",
    "/README.md", "/Procfile", "/_headers", "/supabase_schema.sql",
    "/data/jaura.db", "/data/seed.json", "/tests/test_api.py",
    "/sample-receipt-email.eml", "/i18n.py", "/seed_admin.py",
    "/HEAD", "/index", "/main", "/css/../config.py", "/%2e%2e/config.py",
    "/.github/workflows/ci.yml",
]


@pytest.mark.parametrize("path", BLOCKED_PATHS)
def test_repository_file_is_not_served(client, path):
    assert client.get(path).status_code == 404, path


def test_blocked_path_returns_the_shops_own_404_page(client):
    r = client.get("/config.py")
    assert r.status_code == 404
    body = r.get_data(as_text=True)
    assert "<!DOCTYPE html>" in body or "<html" in body


def test_blocked_path_never_leaks_the_file_contents(client):
    body = client.get("/requirements.txt").get_data(as_text=True)
    assert "Flask==" not in body


def test_blocked_source_never_leaks_its_code(client):
    body = client.get("/config.py").get_data(as_text=True)
    assert "SECRET_KEY" not in body
    assert "RECAPTCHA_SECRET_KEY" not in body


# ---------------------------------------------------------- 3. servable() unit
@pytest.mark.parametrize("name", [
    "index.html", "style.css", "app.js", "seed.json", "photo.png",
    "photo.jpg", "reel.mp4", "font.woff2",
])
def test_servable_allows_the_asset_types_the_shop_ships(name):
    assert appmod.servable(name) is True, name


@pytest.mark.parametrize("name", [
    "config.py", "requirements.txt", "README.md", "render.yaml",
    "supabase_schema.sql", "jaura.db",
])
def test_servable_refuses_source_and_config(name):
    assert appmod.servable(name) is False, name


@pytest.mark.parametrize("path", [
    "data/seed.json", "tests/test_api.py", "node_modules/x/package.json",
    "__pycache__/app.cpython-311.pyc",
])
def test_servable_refuses_whole_private_trees(path):
    assert appmod.servable(path) is False, path


@pytest.mark.parametrize("path", [
    ".env", ".gitignore", ".git/config", ".github/workflows/ci.yml",
])
def test_servable_refuses_dotfiles_and_dot_directories(path):
    assert appmod.servable(path) is False, path


@pytest.mark.parametrize("path", [
    "css/../config.py", "images/../../etc/passwd", "js/./net.js",
])
def test_servable_normalises_traversal_before_deciding(path):
    expected = path == "js/./net.js"
    assert appmod.servable(path) is expected, path


def test_servable_is_case_insensitive_on_extensions():
    assert appmod.servable("CONFIG.PY") is False
    assert appmod.servable("LOGO.PNG") is True


def test_servable_rejects_an_empty_path():
    assert appmod.servable("") is False


# ------------------------------------------------------ 4. the API is untouched
@pytest.mark.parametrize("path", [
    "/api/config", "/api/catalog", "/api/categories", "/healthz", "/sitemap.xml",
])
def test_api_routes_are_unaffected(client, path):
    assert client.get(path).status_code == 200, path


def test_unknown_api_route_still_answers_json(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.get_json()["ok"] is False


# --------------------------------------- 5. the allowlist cannot break the shop
def test_every_internal_link_in_every_page_is_servable():
    """Walk every href/src of every page: a page that links an asset the
    allowlist blocks is a page that silently loses it."""
    link = re.compile(r'(?:href|src)="([^"#?]+)')
    problems = []
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".html"):
            continue
        html = open(os.path.join(ROOT, name), encoding="utf-8").read()
        for href in link.findall(html):
            if href.startswith(("http", "mailto:", "tel:", "//", "data:")):
                continue
            target = href.split("?")[0].lstrip("/")
            if not target or target.startswith("api/") or target == "sitemap.xml":
                continue
            if not appmod.servable(target) and not appmod.servable(os.path.basename(target)):
                problems.append(f"{name} -> {href}")
            elif not (os.path.exists(os.path.join(ROOT, target))
                      or os.path.exists(os.path.join(ROOT, os.path.basename(target)))):
                problems.append(f"{name} -> {href} (missing)")
    assert not problems, "blocked or missing internal links:\n" + "\n".join(problems)


# ------------------------------------------- 6. no published password comes back
# Two admin passwords were committed to this PUBLIC repository: the
# BOOTSTRAP_ADMIN_PASSWORD default in config.py, and the test suite's own login
# password (the same value as the hash inside the committed database). Both are
# dead - the owner rotated the live password - and both are deleted. They are
# remembered here as SHA-256 digests so the guard itself never re-publishes
# them; read them back only if you must, from `git show d3088ed:config.py`.
PUBLISHED_PASSWORD_SHA256 = {
    "b313422f27853cf4bcb66e9a980867b28a0b92975e3bee2b45e6ab4d406b1c7f":
        "the former BOOTSTRAP_ADMIN_PASSWORD default (config.py)",
    "7726eec09fbff761e091788db911f3928d075bb29f5b0371f320455da4eab66e":
        "the former test-suite admin password (data/jaura.db hash)",
}
_SEPARATORS = re.compile(r"[\s\"'`=:,()\[\]<>{};]+")


def _committed_password_hits():
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                            capture_output=True, check=True).stdout
    hits = []
    for rel in [f for f in listed.decode("utf-8", "replace").split("\0") if f]:
        try:
            with open(os.path.join(ROOT, rel), "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue                     # binary: a source literal is not in there
        for token in _SEPARATORS.split(raw.decode("utf-8", "replace")):
            if not token:
                continue
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if digest in PUBLISHED_PASSWORD_SHA256:
                hits.append(f"{rel}: {PUBLISHED_PASSWORD_SHA256[digest]}")
    return hits


def test_no_published_password_literals_are_committed_anywhere():
    hits = _committed_password_hits()
    assert not hits, "published admin passwords are back:\n" + "\n".join(hits)


def test_bootstrap_password_default_is_unset_by_default():
    from config import Config
    assert Config.BOOTSTRAP_ADMIN_PASSWORD == ""
