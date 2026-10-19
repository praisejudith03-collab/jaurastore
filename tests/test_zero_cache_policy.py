"""Zero-cache contract for everything a shopper taps (owner directive 2026-10-19).

The complaint this file exists to make impossible: a customer taps
jaurastore.com.ng on a phone and gets yesterday's shop — an old layout, an old
price, a category image that was replaced hours ago — and the only cures were
incognito, clearing site data, or appending `?v=live` to the URL by hand.

Two things caused it, and both are pinned here rather than explained away:

  1. The homepage was deliberately given a shared-cache window
     (`public, max-age=300, s-maxage=3600, stale-while-revalidate=86400,
     stale-if-error=604800`) to shave a round trip off Google entries. A week of
     `stale-if-error` means a phone keeps rendering a dead origin's page after a
     failed deploy, and `s-maxage=3600` lets the edge answer for an hour without
     asking. The category list had the same disease in miniature
     (`public, max-age=60, …, stale-while-revalidate=120`), which is why a newly
     uploaded category cover looked like it had vanished.
  2. Caching a NEW build was a manual chore: bump a shared `?v=` token in 20
     pages, sw.js, three bundles and three test constants, or every returning
     phone keeps the old bundle for a year because versioned assets are served
     `immutable`.

So: every HTML document, the service worker and every dynamic API answer carry
the strict directive set; versioned static assets stay immutable and are the
only thing allowed to be cached; and the token is now derived from the shipped
bytes by `tools/asset_version.py`, so a stale build cannot be shipped at all.
"""
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_cache_policy.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_cache_policy_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SCHEDULER_ENABLED", "0")

import app as appmod                      # noqa: E402
from security import NO_STORE             # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A page the shopper navigates to. `no-store` is the load-bearing directive: it
# forbids BOTH the browser and any proxy from keeping a body, so a tap on a
# link is a request to this process. The rest are belt-and-braces for caches
# that implement only part of HTTP/1.1.
DOCUMENT_PATHS = ("/", "/index.html", "/shop.html", "/product.html", "/categories.html",
                  "/cart.html", "/checkout.html", "/account.html", "/order-complete.html",
                  "/faq.html", "/about.html", "/contact.html", "/privacy.html",
                  "/returns.html", "/shipping.html", "/terms.html", "/wishlist.html",
                  "/admin", "/admin/accounting", "/404.html")

# A cached copy of these is how a saved change becomes invisible on a phone.
DYNAMIC_API_PATHS = ("/api/site", "/api/config", "/api/stock", "/api/products",
                     "/api/categories", "/api/most-viewed", "/api/payment-methods",
                     "/api/csrf", "/healthz")


@pytest.fixture()
def client():
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as client:
        yield client


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# ------------------------------------------------- 1. documents are never cached
@pytest.mark.parametrize("path", DOCUMENT_PATHS)
def test_every_html_document_is_no_store(client, path):
    response = client.get(path)
    assert response.status_code == 200, f"{path} -> {response.status_code}"
    assert response.headers.get("Cache-Control") == NO_STORE, (
        f"{path} serves Cache-Control={response.headers.get('Cache-Control')!r}; "
        f"a navigable page must serve exactly {NO_STORE!r}")


# `immutable` is deliberately NOT in this list: it is the RIGHT policy for a
# versioned asset URL, and pinning its absence in the source would forbid the
# fingerprinting this file also requires. Documents get it behaviourally, from
# the exact-equality assertion in test_every_html_document_is_no_store above.
@pytest.mark.parametrize("forbidden", ("s-maxage", "stale-while-revalidate",
                                       "stale-if-error"))
def test_no_page_admits_a_shared_cache_window(forbidden):
    """One `s-maxage` anywhere in the request path re-opens this bug class."""
    src = _read("app.py") + _read("api.py") + _read("security.py")
    offenders = [line.strip() for line in src.splitlines()
                 if forbidden in line and not line.strip().startswith(("#", "*", "//"))
                 and '"' in line]
    assert not offenders, (
        f"{forbidden!r} is back on a response: {offenders} — a shared cache may "
        "hold a page (or a menu) a phone then renders with superseded prices")


def test_service_worker_is_no_store(client):
    """The worker itself must never be cached, or a phone runs the OLD worker —
    which then serves its own cache-first copies of everything else."""
    response = client.get("/sw.js")
    assert response.headers.get("Cache-Control") == NO_STORE, (
        f"sw.js serves {response.headers.get('Cache-Control')!r}")


def test_documents_vary_on_language_and_cookie(client):
    """app.py rewrites <html lang> per visitor, so the response is not shared."""
    response = client.get("/shop.html")
    vary = response.headers.get("Vary", "")
    assert "Accept-Language" in vary and "Cookie" in vary, (
        f"Vary={vary!r}; a page whose language depends on the request must not be "
        "served to a visitor whose preference differs")


@pytest.mark.parametrize("path", DYNAMIC_API_PATHS)
def test_dynamic_api_answers_carry_the_strict_set(client, path):
    response = client.get(path)
    assert response.status_code == 200, f"{path} -> {response.status_code}"
    cache_control = response.headers.get("Cache-Control", "")
    for directive in ("no-store", "no-cache", "must-revalidate", "proxy-revalidate",
                      "max-age=0"):
        assert directive in cache_control, (
            f"{path} serves Cache-Control={cache_control!r}; expected {directive!r}")


def test_category_list_is_not_shared_cacheable(client):
    """The 2026-10-19 cause of "my category image disappeared after uploading".

    /api/categories used to answer `public, max-age=60, s-maxage=60,
    stale-while-revalidate=120`: up to three minutes of a knowingly stale menu,
    served by the edge to *other* visitors for a minute after a save. The payload
    is small, so it is cheaper to always fetch it than to explain that.
    """
    response = client.get("/api/categories")
    assert response.headers.get("Cache-Control") == NO_STORE
    # The ETag stays so a proxy that ignores no-store still gets a 304 rather
    # than re-downloading the list.
    etag = response.headers.get("ETag")
    assert etag, "the category list should still ship an ETag for cheap revalidation"
    assert client.get("/api/categories", headers={"If-None-Match": etag}).status_code == 304


def test_catalogue_revalidates_instead_of_caching(client):
    """The catalogue is ~125 KB of JSON. It is `no-cache` (never reused without
    asking) plus an ETag (an unchanged shop costs a 304), which meets "always
    live data" without re-downloading the whole grid on every navigation."""
    first = client.get("/api/catalog")
    cache_control = first.headers.get("Cache-Control", "")
    assert "no-cache" in cache_control and "must-revalidate" in cache_control, cache_control
    assert "max-age=3" not in cache_control and "public" not in cache_control
    etag = first.headers.get("ETag")
    assert etag
    second = client.get("/api/catalog", headers={"If-None-Match": etag})
    assert second.status_code == 304 and not second.get_data()


# ----------------------------------------------------- 2. versioned = immutable
def _token():
    return re.search(r'const VERSION = "jaura-v(\d+)";', _read("sw.js")).group(1)


@pytest.mark.parametrize("asset", ("css/style.css", "js/store.js", "js/app.js",
                                   "css/fonts.css", "js/admin.js"))
def test_versioned_assets_are_immutable_and_untokened_ones_revalidate(client, asset):
    token = _token()
    versioned = client.get(f"/{asset}?v={token}")
    assert versioned.status_code == 200
    assert versioned.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert versioned.headers.get("ETag"), f"/{asset} ships no ETag"
    plain = client.get(f"/{asset}")
    assert "no-cache" in plain.headers["Cache-Control"], (
        f"/{asset} without the token must revalidate, got "
        f"{plain.headers['Cache-Control']!r}")


def test_a_non_numeric_token_is_never_trusted_as_a_version(client):
    """`?v=live` is what a stuck owner types by hand. It must not buy immutability
    — and, per this file's whole point, it must not be necessary either."""
    response = client.get("/css/style.css?v=live")
    assert "no-cache" in response.headers["Cache-Control"]
    assert "immutable" not in response.headers["Cache-Control"]


def test_no_page_hands_out_a_manual_cache_busting_recipe():
    """The shipped markup must not tell a shopper to append a query string."""
    for page in sorted(p for p in os.listdir(ROOT) if p.endswith(".html")):
        html = _read(page)
        assert "?v=live" not in html and "&v=live" not in html, (
            f"{page} still advertises a ?v=live workaround")
        for version in set(re.findall(r"\?v=([0-9A-Za-z._-]+)", html)):
            assert version == _token(), (
                f"{page} references v={version} while the build is v{_token()} — "
                "run tools/asset_version.py --write")


def test_every_asset_reference_in_html_is_the_numeric_shared_token():
    """Fingerprinting is only a promise if the URLs actually carry it."""
    token = _token()
    for page in sorted(p for p in os.listdir(ROOT) if p.endswith(".html")):
        html = _read(page)
        assert set(re.findall(r"\?v=(\d+)", html)) <= {token}, page
        assert not re.search(r"\?v=(?!%s)[0-9]" % token, html), (
            f"{page} mixes token versions; some phone will pair old CSS with new JS")


# ------------------------------------------- 3. automated build versioning
def test_asset_version_tool_passes_on_this_tree():
    """The build is self-consistent: every shipped asset matches its fingerprint."""
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "asset_version.py"),
                           "--check"], cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in proc.stdout


def test_asset_version_tool_is_idempotent(tmp_path):
    """`--write` with no byte change must not bump the token.

    A build step that re-versioned assets on every deploy would throw away a
    year of phone caching on releases that touched no frontend at all — the
    opposite of the fix — and it would make the first visit after every deploy
    cost the whole stylesheet again.
    """
    before = (_read("sw.js"), _read("js/store.js"), _read("index.html"))
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "asset_version.py"),
                           "--write"], cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nothing changed" in proc.stdout
    after = (_read("sw.js"), _read("js/store.js"), _read("index.html"))
    assert before == after, "a no-op run rewrote shipped files"


def test_fingerprint_guard_names_the_tool():
    """A guard nobody can act on is a guard that gets bypassed."""
    assert "tools/asset_version.py --write" in _read("tests/test_asset_cache_token.py")


# ------------------------------------------------------ 4. Accept-Language first
@pytest.mark.parametrize("header,expected", [
    ("fr-FR,fr;q=0.9", "fr"),
    ("fr-BJ", "fr"),
    ("fr-CI,fr;q=0.9,en;q=0.8", "fr"),
    ("en-NG,en;q=0.9,fr;q=0.8", "en"),      # the browser's own order wins
    ("fr;q=0", "en"),                        # q=0 is "never"
    ("en", "en"),
    ("", "en"),
])
def test_first_paint_language_follows_accept_language(client, header, expected):
    """A French phone must not paint an English page while js/i18n.js loads.

    Only a French decision rewrites anything, so an English visitor still gets
    index.html byte-for-byte as it ships (see the next test).
    """
    html = client.get("/shop.html", headers={"Accept-Language": header}).get_data(as_text=True)
    painted = re.search(r'<html lang="([a-zA-Z-]+)"', html)
    assert painted and painted.group(1) == expected, (
        f"Accept-Language: {header!r} painted <html lang="
        f"{painted.group(1) if painted else '(nothing)'}\">, expected {expected!r}")


def test_a_stored_choice_beats_the_device_on_the_server_too(client):
    """The server-side decision must mirror js/i18n.js exactly, or the page
    flashes from one language to the other as the scripts boot."""
    client.set_cookie("jaura_lang", "en")
    html = client.get("/shop.html", headers={"Accept-Language": "fr"}).get_data(as_text=True)
    assert '<html lang="en"' in html
    client.delete_cookie("jaura_lang")
    client.set_cookie("jaura_lang", "fr")
    html = client.get("/shop.html", headers={"Accept-Language": "en-NG"}).get_data(as_text=True)
    assert '<html lang="fr"' in html


def test_english_documents_are_served_byte_identical(client):
    """The rewrite is a French-only opt-in, so nothing about the ordinary path
    changes — including every test and tool that hashes shipped HTML."""
    served = client.get("/index.html").get_data()
    with open(os.path.join(ROOT, "index.html"), "rb") as fh:
        assert served == fh.read(), (
            "an English request must get the file exactly as it ships")


def test_the_rewrite_cannot_break_a_page(client, monkeypatch):
    """A failure to rewrite is a language flash, not a 500."""
    import security as sec
    monkeypatch.setattr(sec, "is_html_document", lambda resp: True)
    assert client.get("/shop.html").status_code == 200


@pytest.mark.parametrize("header,expected", [("fr", "fr"), ("en-NG", "en")])
def test_the_rewrite_survives_gzip_without_lying_about_length(client, header, expected):
    """app.py compresses AFTER the rewrite (and reads the body itself, because
    a streamed file cannot be gzipped), which is exactly where a stale
    Content-Length or a double-encoded body would appear. A phone on 3G gets
    every byte of this response, so both halves are pinned: the decompressed
    markup carries the requested language, and the declared length equals the
    bytes actually sent.
    """
    import gzip
    response = client.get("/shop.html", headers={"Accept-Language": header,
                                                 "Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers.get("Content-Encoding") == "gzip"
    raw = response.get_data()
    declared = response.headers.get("Content-Length")
    assert declared is None or int(declared) == len(raw), (
        f"Content-Length={declared} but {len(raw)} bytes were sent")
    body = gzip.decompress(raw)
    assert f'<html lang="{expected}"'.encode() in body, body[:200]
    # ...and the strict policy is untouched by the compression pass.
    assert response.headers["Cache-Control"] == NO_STORE
