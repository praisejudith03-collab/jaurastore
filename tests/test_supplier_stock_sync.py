"""Proportional supplier stock mirroring (tools/supplier_stock_sync.py).

The script must:
  * NEVER touch a product that has no explicit supplierId/supplierSku
    mapping - an Ankara waist piece (or anything else the admin hasn't
    opted in) keeps its own stock number no matter what the script does;
  * set a mapped product's stock to EXACTLY the number the supplier
    reports (proportional/direct mirroring, not an on/off toggle);
  * leave a mapped product's stock completely unchanged when the supplier
    fetch fails or is ambiguous - it must never guess.

Run with:  python3 -m pytest tests/test_supplier_stock_sync.py -q
No network access is used: every test monkeypatches
supplier_stock_sync.fetch_supplier_quantity instead of calling splendall.com.
"""
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

import catalog as catalog_mod  # noqa: E402
import supabase_store  # noqa: E402
import tools.supplier_stock_sync as sync_mod  # noqa: E402

_REAL_HTTP_GET_JSON = sync_mod._http_get_json


@pytest.fixture(autouse=True)
def _no_supabase(monkeypatch):
    """Every test here runs against the local override file, not Supabase."""
    monkeypatch.setattr(supabase_store, "enabled", lambda: False)


@pytest.fixture(autouse=True)
def _no_real_crawl(monkeypatch):
    """Never let a test hit the real network by default.

    Patching at the _http_get_json level (rather than crawl_supplier_catalog
    itself) means crawl_supplier_catalog's OWN fail-closed logic is what
    turns this into "supplier unreachable this run" (returns None) - so
    tests of that fail-closed logic can still call the real function, and a
    test that wants a full fake catalog just overrides crawl_supplier_catalog
    directly instead.
    """
    def _blocked(url):
        raise sync_mod.SupplierFetchError("network blocked in tests")
    monkeypatch.setattr(sync_mod, "_http_get_json", _blocked)


@pytest.fixture(autouse=True)
def _iso_sync_files(tmp_path, monkeypatch):
    """The image-hash cache and review report never touch the real repo files."""
    monkeypatch.setattr(sync_mod, "CACHE_FILE", str(tmp_path / "supplier_sync_cache.json"))
    monkeypatch.setattr(sync_mod, "REVIEW_FILE", str(tmp_path / "supplier_match_review.json"))
    monkeypatch.setattr(sync_mod, "WARNING_FILE", str(tmp_path / "supplier_sync_warnings.json"))


@pytest.fixture()
def iso_catalog():
    """An empty, isolated catalogue override file for one test, then restored."""
    path = catalog_mod._norm_filename(catalog_mod.CATALOG_FILE)
    bak = path + ".bak"
    saved = {p: (open(p, "rb").read() if os.path.isfile(p) else None)
             for p in (path, bak)}
    catalog_mod._write_overrides(
        {"products": [], "deleted": [], "updatedAt": "", "updatedBy": ""}, path)
    try:
        yield path
    finally:
        for p, raw in saved.items():
            try:
                if raw is None:
                    if os.path.isfile(p):
                        os.remove(p)
                else:
                    with open(p, "wb") as fh:
                        fh.write(raw)
            except OSError:
                pass


def _make(iso_catalog, **overrides):
    base = {
        "name": "Test Product",
        "priceNgn": 5000,
        "stock": 10,
        "category": "beauty",
        "online": True,
    }
    base.update(overrides)
    product, action = catalog_mod.upsert(base, actor="test")[:2]
    assert product, f"fixture product failed to save: {action}"
    return product


# --------------------------------------------------------------- mapping


def test_supplier_fields_default_blank_and_round_trip(iso_catalog):
    """A brand-new product is unmapped by default; an explicit mapping persists."""
    plain = _make(iso_catalog, name="Ankara Waist Piece")
    assert plain.get("supplierId") == ""
    assert plain.get("supplierSku") == ""

    mapped = _make(iso_catalog, name="Splendall Towel",
                    supplierId="splendall", supplierSku="5in1-mini-towel")
    assert mapped["supplierId"] == "splendall"
    assert mapped["supplierSku"] == "5in1-mini-towel"


def test_unmapped_products_are_never_in_scope(iso_catalog):
    ankara = _make(iso_catalog, name="Ankara Waist Piece", stock=10)
    other = _make(iso_catalog, name="Random Unrelated Item", stock=7)

    mapped = {p["id"] for p in sync_mod.mapped_products()}
    assert ankara["id"] not in mapped
    assert other["id"] not in mapped


def test_unmapped_products_survive_a_full_sync_run_untouched(iso_catalog, monkeypatch):
    ankara = _make(iso_catalog, name="Ankara Waist Piece", stock=10)

    # Even if the fetch function were somehow called, it must never be asked
    # to fetch this product because it is not mapped. Make it explode if it
    # is ever called at all, to prove the sync loop never reaches it.
    def _boom(_sku):
        raise AssertionError("fetch_supplier_quantity must not be called for an unmapped product")
    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity", _boom)

    rc = sync_mod.main(["supplier_stock_sync.py"])
    assert rc == 0

    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == ankara["id"])
    assert catalog_mod.stock_of(refreshed) == 10


# --------------------------------------------------------- exact mirroring


def test_mapped_product_stock_set_to_exact_supplier_number(iso_catalog, monkeypatch):
    product = _make(iso_catalog, name="Splendall Bag", stock=50,
                     supplierId="splendall", supplierSku="rita-bag")

    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity",
                         lambda sku: (5, "5 in stock"))

    rc = sync_mod.main(["supplier_stock_sync.py", product["id"]])
    assert rc == 0

    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    assert catalog_mod.stock_of(refreshed) == 5
    assert refreshed["stock_quantity"] == 5


def test_mapped_product_mirrors_a_different_low_count_too(iso_catalog, monkeypatch):
    """Proportional means an arbitrary exact count, not just a toggle."""
    product = _make(iso_catalog, name="Splendall Towel", stock=1,
                     supplierId="splendall", supplierSku="2in1-towel")

    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity",
                         lambda sku: (2, "2 in stock"))
    sync_mod.main(["supplier_stock_sync.py", product["id"]])
    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    assert catalog_mod.stock_of(refreshed) == 2

    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity",
                         lambda sku: (30, "30 in stock"))
    sync_mod.main(["supplier_stock_sync.py", product["id"]])
    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    # Automatic positive restocks use the conservative default cap.
    assert catalog_mod.stock_of(refreshed) == 20


def test_mapped_product_can_mirror_down_to_zero(iso_catalog, monkeypatch):
    product = _make(iso_catalog, name="Splendall Pyjamas", stock=20,
                     supplierId="splendall", supplierSku="love-cotton-pyjamas")
    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity",
                         lambda sku: (0, "supplier reports out of stock"))
    sync_mod.main(["supplier_stock_sync.py", product["id"]])
    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    assert catalog_mod.stock_of(refreshed) == 0


# --------------------------------------------------------------- fail-closed


def test_failed_fetch_leaves_stock_unchanged(iso_catalog, monkeypatch):
    product = _make(iso_catalog, name="Splendall Sail Book Tab", stock=42,
                     supplierId="splendall", supplierSku="sail-book-tab")

    def _fail(_sku):
        raise sync_mod.SupplierFetchError("network error: simulated timeout")
    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity", _fail)

    rc = sync_mod.main(["supplier_stock_sync.py", product["id"]])
    assert rc == 0

    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    assert catalog_mod.stock_of(refreshed) == 42


def test_ambiguous_supplier_reply_is_treated_as_a_failure(iso_catalog, monkeypatch):
    """A reply with no product row is a failure, not '0 units'."""
    monkeypatch.setattr(sync_mod, "_http_get_json", _REAL_HTTP_GET_JSON)
    import json

    class _Resp:
        status = 200
        def read(self):
            return json.dumps([]).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(*args, **kwargs):
        return _Resp()

    import urllib.request
    real_urlopen = urllib.request.urlopen
    urllib.request.urlopen = _fake_urlopen
    try:
        with pytest.raises(sync_mod.SupplierFetchError):
            sync_mod.fetch_supplier_quantity("nonexistent-product")
    finally:
        urllib.request.urlopen = real_urlopen


def test_dry_run_never_writes(iso_catalog, monkeypatch):
    product = _make(iso_catalog, name="Splendall Dry Run Item", stock=9,
                     supplierId="splendall", supplierSku="dry-run-item")
    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity",
                         lambda sku: (3, "3 in stock"))
    sync_mod.main(["supplier_stock_sync.py", product["id"], "--dry-run"])
    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    assert catalog_mod.stock_of(refreshed) == 9


# ------------------------------------------------------- protected keyword


def test_ankara_stays_untouched_even_if_mistakenly_mapped(iso_catalog, monkeypatch):
    """Belt-and-braces: an Ankara item is refused even under an admin mistake."""
    product = _make(iso_catalog, name="Ankara Waist Piece (mismapped)", stock=15,
                     supplierId="splendall", supplierSku="some-slug")

    def _boom(_sku):
        raise AssertionError("an Ankara product must never reach the fetch step")
    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity", _boom)

    rc = sync_mod.main(["supplier_stock_sync.py", product["id"]])
    assert rc == 0

    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    assert catalog_mod.stock_of(refreshed) == 15


# ------------------------------------------------------------- slug parsing


@pytest.mark.parametrize("raw,expected", [
    ("5in1-mini-towel", "5in1-mini-towel"),
    ("https://www.splendall.com/product/5in1-mini-towel/", "5in1-mini-towel"),
    ("https://www.splendall.com/product/rita-bag", "rita-bag"),
    ("", ""),
    (None, ""),
])
def test_slug_from_sku_accepts_a_bare_slug_or_a_full_url(raw, expected):
    assert sync_mod._slug_from_sku(raw) == expected


def test_public_catalog_never_exposes_supplier_fields(iso_catalog):
    import api as api_mod
    product = _make(iso_catalog, name="Splendall Hidden Field Check", stock=4,
                     supplierId="splendall", supplierSku="hidden-field-check")
    public = api_mod._public_product(product)
    assert "supplierId" not in public
    assert "supplierSku" not in public


# ============================================================ autonomous
# Zero-manual-mapping: catalog crawl, autonomous name+photo matching, and
# discontinued-item hiding. Every test below fakes the supplier's reply -
# no network is ever touched.


def _supplier_row(slug, name, qty=10, image_url=""):
    return {"slug": slug, "name": name, "qty": qty,
            "detail": f"{qty} in stock", "image_url": image_url, "permalink": ""}


def _padded_catalog(rows, minimum=None):
    """Pad a fake supplier catalog up to MIN_CATALOG_SIZE with filler rows,
    so a crawl is trusted for discovery/discontinuation in the test."""
    minimum = sync_mod.MIN_CATALOG_SIZE if minimum is None else minimum
    out = list(rows)
    i = 0
    while len(out) < minimum:
        out.append(_supplier_row(f"filler-item-{i}", f"Filler Item {i}"))
        i += 1
    return out


def _install_ref_based_image_hash(monkeypatch):
    """Stand in for the real dHash: two rows that share the EXACT SAME
    image_url/image reference hash identically (Hamming distance 0 - "an
    exact image-to-image check"); any two different references hash to a
    value that, for all practical purposes, is always far enough apart to
    fail IMAGE_EXACT_SCORE_FLOOR. This lets a test verify the auto-link
    gate is driven by the PHOTO, deterministically, with no real network
    access or image bytes needed."""
    import zlib

    def _fake(ref, cache, cache_key):
        if not ref:
            return None
        return zlib.crc32(str(ref).encode("utf-8"))
    monkeypatch.setattr(sync_mod, "_image_hash_for_ref", _fake)


# --------------------------------------------------------------- crawling


def test_crawl_paginates_until_a_short_page(monkeypatch):
    pages = {
        1: [{"slug": f"p{i}", "name": f"P{i}", "is_in_stock": True,
             "stock_availability": {"text": "5 in stock"}} for i in range(100)],
        2: [{"slug": "p100", "name": "P100", "is_in_stock": True,
             "stock_availability": {"text": "5 in stock"}}],
    }

    def _fake_get_json(url):
        page = int(dict(x.split("=") for x in url.split("?", 1)[1].split("&"))["page"])
        return pages.get(page, [])
    monkeypatch.setattr(sync_mod, "_http_get_json", _fake_get_json)

    rows = sync_mod.crawl_supplier_catalog(per_page=100)
    assert rows is not None
    assert len(rows) == 101
    assert {r["slug"] for r in rows} == {f"p{i}" for i in range(101)}


def test_crawl_aborts_and_returns_none_on_any_page_failure(monkeypatch):
    calls = {"n": 0}

    def _fake_get_json(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"slug": "p0", "name": "P0", "is_in_stock": True,
                      "stock_availability": {"text": "5 in stock"}}] * 100
        raise sync_mod.SupplierFetchError("simulated failure")
    monkeypatch.setattr(sync_mod, "_http_get_json", _fake_get_json)

    assert sync_mod.crawl_supplier_catalog(per_page=100) is None


# --------------------------------------------------------------- matching


def test_discover_matches_never_auto_links_on_name_alone_no_matter_how_strong(iso_catalog, monkeypatch):
    """Owner request 2026-09-28: "never match ... based on generic names".
    An excellent name match with NO verified photo must be queued for
    review, never auto-linked."""
    monkeypatch.setattr(sync_mod, "_image_hash_for_ref", lambda *a, **k: None)
    product = _make(iso_catalog, name="5in1 Mini Towel", stock=10)
    supplier_rows = [_supplier_row("5in1-mini-towel", "5in1 mini towel", qty=7)]

    auto, review = sync_mod.discover_matches([product], supplier_rows, {})
    assert not auto, "a name match with no photo verification must never auto-link"
    assert len(review) == 1
    assert review[0][0]["id"] == product["id"]


def test_discover_matches_auto_links_only_on_a_verified_near_identical_photo(iso_catalog, monkeypatch):
    """The auto-link gate is the PHOTO, not the name: a plain, generic name
    that only shares a recurring word like "bag" still auto-links once the
    exact same image is confirmed on both sides - and a close-but-not-
    identical photo, or no photo pair at all, never does, regardless of the
    name."""
    _install_ref_based_image_hash(monkeypatch)
    same_photo = "https://cdn.example.test/photos/tote-001.jpg"
    product = _make(iso_catalog, name="Tote Bag", stock=10, image_url=same_photo)
    matching_row = _supplier_row("rita-bag", "Large Shopping Bag", qty=7, image_url=same_photo)

    auto, review = sync_mod.discover_matches([product], [matching_row], {})
    assert len(auto) == 1
    assert auto[0][0]["id"] == product["id"]
    assert auto[0][1]["slug"] == "rita-bag"
    assert not review


def test_discover_matches_rejects_a_similar_but_not_exact_photo_for_auto_link(iso_catalog, monkeypatch):
    """A photo that merely resembles another product's (different image
    reference, so a different fake hash) must fail the exact-match gate,
    even with a decent name overlap - it can only ever reach review."""
    _install_ref_based_image_hash(monkeypatch)
    product = _make(iso_catalog, name="Mini Travel Towel Set", stock=10,
                     image_url="https://cdn.example.test/photos/towel-a.jpg")
    row = _supplier_row("2in1-towel", "2in1 Travel Towel", qty=3,
                        image_url="https://cdn.example.test/photos/towel-b.jpg")

    auto, review = sync_mod.discover_matches([product], [row], {})
    assert not auto
    assert len(review) == 1


def test_discover_matches_never_considers_a_protected_product(iso_catalog, monkeypatch):
    monkeypatch.setattr(sync_mod, "_image_hash_for_ref", lambda *a, **k: None)
    ankara = _make(iso_catalog, name="Ankara Waist Piece", stock=10)
    supplier_rows = [_supplier_row("ankara-waist-piece", "Ankara Waist Piece", qty=7)]

    auto, review = sync_mod.discover_matches([ankara], supplier_rows, {})
    assert not auto
    assert not review


def test_discover_matches_a_protected_product_is_skipped_even_with_an_identical_photo(iso_catalog, monkeypatch):
    """The protected-keyword denylist is checked BEFORE any photo work -
    even a byte-identical image can never pull an Ankara item into scope."""
    _install_ref_based_image_hash(monkeypatch)
    same_photo = "https://cdn.example.test/photos/ankara-001.jpg"
    ankara = _make(iso_catalog, name="Ankara Waist Piece", stock=10, image_url=same_photo)
    supplier_rows = [_supplier_row("ankara-waist-piece", "Ankara Waist Piece", qty=7, image_url=same_photo)]

    auto, review = sync_mod.discover_matches([ankara], supplier_rows, {})
    assert not auto
    assert not review


def test_discover_matches_leaves_dissimilar_products_unmatched(iso_catalog, monkeypatch):
    monkeypatch.setattr(sync_mod, "_image_hash_for_ref", lambda *a, **k: None)
    product = _make(iso_catalog, name="Wooden Hair Comb Set", stock=10)
    supplier_rows = [_supplier_row("rita-bag", "Rita bag", qty=7),
                      _supplier_row("2in1-towel", "2in1 towel", qty=3)]

    auto, review = sync_mod.discover_matches([product], supplier_rows, {})
    assert not auto
    assert not review


def test_discover_matches_queues_a_medium_confidence_candidate_for_review(iso_catalog, monkeypatch):
    monkeypatch.setattr(sync_mod, "_image_hash_for_ref", lambda *a, **k: None)
    # Deliberately a partial/ambiguous name overlap: similar enough to flag,
    # not similar enough to trust unattended.
    product = _make(iso_catalog, name="Mini Travel Towel Set", stock=10)
    supplier_rows = [_supplier_row("2in1-towel", "2in1 Travel Towel", qty=3)]

    auto, review = sync_mod.discover_matches([product], supplier_rows, {})
    assert not auto
    assert len(review) == 1
    assert review[0][0]["id"] == product["id"]


def test_apply_auto_match_only_changes_supplier_fields_price_untouched(iso_catalog):
    product = _make(iso_catalog, name="Splendall Bag", stock=10, priceNgn=12345)
    row = _supplier_row("rita-bag", "Rita bag")

    linked = sync_mod.apply_auto_match(product, row, actor="test")
    assert linked["supplierId"] == "splendall"
    assert linked["supplierSku"] == "https://www.splendall.com/product/rita-bag/"
    assert linked["priceNgn"] == 12345
    assert linked["name"] == "Splendall Bag"
    assert catalog_mod.stock_of(linked) == 10          # stock is untouched by linking alone


# ------------------------------------------------------------ discontinued


def test_discontinued_item_is_zeroed_out_but_never_hidden(iso_catalog):
    """Owner's explicit rule (2026-09-28): a linked item that disappears from
    the supplier gets marked OUT OF STOCK only - it stays online/visible,
    exactly like any other item that sells out, until the owner hides or
    deletes it by hand."""
    present = _make(iso_catalog, name="Still Selling Item", stock=8,
                     supplierId="splendall", supplierSku="still-here", online=True)
    gone = _make(iso_catalog, name="Discontinued Item", stock=8,
                 supplierId="splendall", supplierSku="no-longer-there", online=True)
    ankara = _make(iso_catalog, name="Ankara Waist Piece", stock=8)  # unmapped

    supplier_rows = [_supplier_row("still-here", "Still Selling Item")]
    counts = sync_mod.mark_discontinued_out_of_stock(supplier_rows)
    assert counts.get("zeroed") == 1

    refreshed_present = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == present["id"])
    refreshed_gone = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == gone["id"])
    refreshed_ankara = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == ankara["id"])

    assert refreshed_present["online"] is True
    assert catalog_mod.stock_of(refreshed_present) == 8

    # Zeroed, but NEVER hidden - `online` is completely untouched by this script.
    assert refreshed_gone["online"] is True
    assert catalog_mod.stock_of(refreshed_gone) == 0

    # An unmapped product is never even considered by the discontinued check.
    assert refreshed_ankara["online"] is True
    assert catalog_mod.stock_of(refreshed_ankara) == 8


def test_discontinued_check_dry_run_never_writes(iso_catalog):
    gone = _make(iso_catalog, name="Discontinued Item", stock=8,
                 supplierId="splendall", supplierSku="no-longer-there", online=True)
    counts = sync_mod.mark_discontinued_out_of_stock([], dry_run=True)
    assert counts.get("would-zero") == 1
    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == gone["id"])
    assert refreshed["online"] is True
    assert catalog_mod.stock_of(refreshed) == 8


# ------------------------------------------------------------ end-to-end


def test_main_end_to_end_autonomous_discovery_links_and_syncs_in_one_run(iso_catalog, monkeypatch):
    """Zero manual mapping: an unmapped product is discovered (by a
    VERIFIED, near-identical photo, not by name), linked, AND has its
    stock mirrored, all in a single unattended run."""
    _install_ref_based_image_hash(monkeypatch)
    same_photo = "https://cdn.example.test/photos/5in1-mini-towel.jpg"
    product = _make(iso_catalog, name="5in1 Mini Towel", stock=99, image_url=same_photo)
    ankara = _make(iso_catalog, name="Ankara Waist Piece", stock=41)

    supplier_rows = _padded_catalog([_supplier_row("5in1-mini-towel", "5in1 mini towel", qty=6, image_url=same_photo)])
    monkeypatch.setattr(sync_mod, "crawl_supplier_catalog", lambda *a, **k: supplier_rows)
    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity", lambda sku: (6, "6 in stock"))

    rc = sync_mod.main(["supplier_stock_sync.py"])
    assert rc == 0

    linked = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    assert linked["supplierId"] == "splendall"
    assert linked["supplierSku"] == "https://www.splendall.com/product/5in1-mini-towel/"
    assert catalog_mod.stock_of(linked) == 6

    untouched_ankara = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == ankara["id"])
    assert untouched_ankara["supplierId"] == ""
    assert catalog_mod.stock_of(untouched_ankara) == 41


def test_main_skips_discovery_when_crawl_is_too_small(iso_catalog, monkeypatch):
    monkeypatch.setattr(sync_mod, "_image_hash_for_ref", lambda *a, **k: None)
    product = _make(iso_catalog, name="5in1 Mini Towel", stock=99)
    tiny_catalog = [_supplier_row("5in1-mini-towel", "5in1 mini towel", qty=6)]
    monkeypatch.setattr(sync_mod, "crawl_supplier_catalog", lambda *a, **k: tiny_catalog)

    rc = sync_mod.main(["supplier_stock_sync.py"])
    assert rc == 0

    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    assert refreshed["supplierId"] == ""            # never auto-linked from a too-small crawl
    assert catalog_mod.stock_of(refreshed) == 99


def test_main_skips_discovery_when_crawl_fails(iso_catalog):
    """crawl_supplier_catalog defaults to None (see _no_real_crawl) - a
    perfectly matching product must stay unlinked when the crawl fails."""
    product = _make(iso_catalog, name="5in1 Mini Towel", stock=99)
    rc = sync_mod.main(["supplier_stock_sync.py"])
    assert rc == 0
    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    assert refreshed["supplierId"] == ""
    assert catalog_mod.stock_of(refreshed) == 99


def test_main_writes_a_review_file_for_medium_confidence_matches(iso_catalog, monkeypatch, tmp_path):
    monkeypatch.setattr(sync_mod, "_image_hash_for_ref", lambda *a, **k: None)
    _make(iso_catalog, name="Mini Travel Towel Set", stock=10)
    supplier_rows = _padded_catalog([_supplier_row("2in1-towel", "2in1 Travel Towel", qty=3)])
    monkeypatch.setattr(sync_mod, "crawl_supplier_catalog", lambda *a, **k: supplier_rows)

    sync_mod.main(["supplier_stock_sync.py"])

    import json
    with open(sync_mod.REVIEW_FILE, "r", encoding="utf-8") as fh:
        report = json.load(fh)
    assert report["candidates"], "a medium-confidence candidate should be reported for review"


def test_main_emails_the_shop_when_a_match_needs_review(iso_catalog, monkeypatch):
    """Owner request 2026-09-28: an uncertain match must not just sit in a
    JSON file or a CI log - the owner is emailed the instant one is found,
    with no log-checking required.

    discover_matches is stubbed directly so the assertion is exact and
    immune to the ambient seed catalogue (250+ real products also get
    scored against any supplier crawl, which is why the review COUNT from a
    live discover_matches() call is deliberately never pinned elsewhere in
    this file - only whether OUR candidate is reported)."""
    product = _make(iso_catalog, name="Mini Travel Towel Set", stock=10)
    row = _supplier_row("2in1-towel", "2in1 Travel Towel", qty=3)
    supplier_rows = _padded_catalog([row])
    monkeypatch.setattr(sync_mod, "crawl_supplier_catalog", lambda *a, **k: supplier_rows)
    monkeypatch.setattr(sync_mod, "discover_matches", lambda *a, **k: ([], [(product, row, 0.62)]))

    import mailer
    sent = []
    monkeypatch.setattr(mailer, "send_mail", lambda subject, html: sent.append((subject, html)) or (True, "ok"))

    rc = sync_mod.main(["supplier_stock_sync.py"])

    assert rc == 0
    assert len(sent) == 1
    subject, html = sent[0]
    assert "1" in subject and "review" in subject.lower()
    assert "Mini Travel Towel Set" in html
    assert "2in1 Travel Towel" in html


def test_dry_run_never_emails_a_review_alert(iso_catalog, monkeypatch):
    product = _make(iso_catalog, name="Mini Travel Towel Set", stock=10)
    row = _supplier_row("2in1-towel", "2in1 Travel Towel", qty=3)
    supplier_rows = _padded_catalog([row])
    monkeypatch.setattr(sync_mod, "crawl_supplier_catalog", lambda *a, **k: supplier_rows)
    monkeypatch.setattr(sync_mod, "discover_matches", lambda *a, **k: ([], [(product, row, 0.62)]))

    import mailer
    sent = []
    monkeypatch.setattr(mailer, "send_mail", lambda subject, html: sent.append((subject, html)) or (True, "ok"))

    sync_mod.main(["supplier_stock_sync.py", "--dry-run"])

    assert not sent, "a dry run must never send a real email"


def test_no_review_candidates_means_no_email(iso_catalog, monkeypatch):
    supplier_rows = _padded_catalog([_supplier_row("5in1-mini-towel", "5in1 mini towel", qty=7)])
    monkeypatch.setattr(sync_mod, "crawl_supplier_catalog", lambda *a, **k: supplier_rows)
    monkeypatch.setattr(sync_mod, "discover_matches", lambda *a, **k: ([], []))

    import mailer
    sent = []
    monkeypatch.setattr(mailer, "send_mail", lambda subject, html: sent.append((subject, html)) or (True, "ok"))

    sync_mod.main(["supplier_stock_sync.py"])

    assert not sent


def test_a_broken_mail_transport_never_fails_the_sync_run(iso_catalog, monkeypatch):
    """The mailer's own contract is "never raise", but this script must stay
    safe even if that contract is somehow violated - a notification problem
    can never take down the nightly sync."""
    product = _make(iso_catalog, name="Mini Travel Towel Set", stock=10)
    row = _supplier_row("2in1-towel", "2in1 Travel Towel", qty=3)
    supplier_rows = _padded_catalog([row])
    monkeypatch.setattr(sync_mod, "crawl_supplier_catalog", lambda *a, **k: supplier_rows)
    monkeypatch.setattr(sync_mod, "discover_matches", lambda *a, **k: ([], [(product, row, 0.62)]))

    import mailer

    def _boom(subject, html):
        raise RuntimeError("provider is down")
    monkeypatch.setattr(mailer, "send_mail", _boom)

    rc = sync_mod.main(["supplier_stock_sync.py"])
    assert rc == 0


def test_only_id_mode_never_triggers_discovery(iso_catalog, monkeypatch):
    """python3 tools/supplier_stock_sync.py <id> is a diagnostic single-item
    mode - it must never crawl or auto-link anything else."""
    def _boom(*a, **k):
        raise AssertionError("crawl_supplier_catalog must not run in single-id mode")
    monkeypatch.setattr(sync_mod, "crawl_supplier_catalog", _boom)
    product = _make(iso_catalog, name="Splendall Bag", stock=10,
                     supplierId="splendall", supplierSku="rita-bag")
    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity", lambda sku: (4, "4 in stock"))
    rc = sync_mod.main(["supplier_stock_sync.py", product["id"]])
    assert rc == 0


# -------------------------------------------------------------- fingerprint


# --------------------------------------------------- scheduled workflow


def test_supplier_sync_workflow_stays_safe():
    """Follows tests/test_catalog_watchdog.py's workflow-safety pin: a
    twice-daily (morning + evening) schedule, secrets-only credentials,
    bounded permissions and a single-flight concurrency group so two runs
    can never race each other."""
    yaml = pytest.importorskip("yaml")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".github", "workflows", "supplier-stock-sync.yml")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    data = yaml.safe_load(text)
    triggers = data.get("on", data.get(True))
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    # Twice a day, West Africa Time (UTC+1, no DST): 08:00 and 20:00 WAT.
    assert triggers["schedule"] == [{"cron": "0 7 * * *"}, {"cron": "0 19 * * *"}]
    perms = data["permissions"]
    assert perms == {"contents": "read", "issues": "write"}, perms
    assert data["concurrency"]["group"] == "supplier-stock-sync"
    assert data["concurrency"]["cancel-in-progress"] is False
    job = data["jobs"]["sync"]
    assert job["timeout-minutes"], "the job must have a timeout"
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert "secrets.SUPABASE_URL" in code and "secrets.SUPABASE_SERVICE_ROLE_KEY" in code
    for line in code.splitlines():
        if "SUPABASE_SERVICE_ROLE_KEY" in line and ":" in line:
            assert line.strip().endswith("${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}"), line
    assert "tools/supplier_stock_sync.py" in code


def test_dhash_and_hamming_are_stable_and_meaningful():
    from PIL import Image
    buf1 = io.BytesIO()
    Image.new("RGB", (32, 32), color=(200, 50, 50)).save(buf1, format="PNG")
    buf2 = io.BytesIO()
    Image.new("RGB", (32, 32), color=(10, 10, 200)).save(buf2, format="PNG")

    h1 = sync_mod._dhash(buf1.getvalue())
    h1_again = sync_mod._dhash(buf1.getvalue())
    h2 = sync_mod._dhash(buf2.getvalue())

    assert h1 == h1_again                       # deterministic
    assert isinstance(h1, int) and 0 <= h1 < (1 << 64)
    assert sync_mod._hamming(h1, h1) == 0
    assert sync_mod._hamming(h1, h2) >= 0

# ------------------------------------------------ precision variant auditing


def test_exact_variant_audit_keeps_names_color_codes_shades_and_quantities(monkeypatch):
    product = {
        "id": "jau-shades", "name": "Shade Set", "supplierSku": "shade-set",
        "options": [
            {"title": "Colour", "values": ["#A52A2A", "#101010"]},
            {"title": "Shade", "values": ["01", "02"]},
        ],
    }
    variations = [
        {"id": 1, "attributes": [{"name": "Color", "value": "#A52A2A"}, {"name": "Shade", "value": "01"}], "is_in_stock": True, "stock_availability": {"text": "3 in stock"}},
        {"id": 2, "attributes": [{"name": "Color", "value": "#A52A2A"}, {"name": "Shade", "value": "02"}], "is_in_stock": False},
        {"id": 3, "attributes": [{"name": "Color", "value": "#101010"}, {"name": "Shade", "value": "01"}], "is_in_stock": True, "stock_availability": {"text": "7 in stock"}},
        {"id": 4, "attributes": [{"name": "Color", "value": "#101010"}, {"name": "Shade", "value": "02"}], "is_in_stock": True, "stock_availability": {"text": "2 in stock"}},
    ]
    monkeypatch.setattr(sync_mod, "_supplier_product_detail", lambda sku: {
        "id": 99, "name": "Shade Set", "variations": variations,
        "images": [{"src": "https://splendall.test/shades.jpg"}],
    })

    stock, audit = sync_mod.fetch_supplier_variant_stock(product)

    assert stock == {
        "Colour: #A52A2A · Shade: 01": 3,
        "Colour: #A52A2A · Shade: 02": 0,
        "Colour: #101010 · Shade: 01": 7,
        "Colour: #101010 · Shade: 02": 2,
    }
    assert audit[0]["options"] == [
        {"name": "Color", "value": "#A52A2A"},
        {"name": "Shade", "value": "01"},
    ]
    assert {row["status"] for row in audit} == {"in_stock", "out_of_stock"}


def test_variant_audit_rejects_any_non_bijective_option_mapping(monkeypatch):
    product = {
        "id": "jau-sizes", "supplierSku": "sizes",
        "options": [{"title": "Size", "values": ["S", "M"]}],
    }
    monkeypatch.setattr(sync_mod, "_supplier_product_detail", lambda sku: {
        "id": 10, "variations": [
            {"id": 1, "attributes": [{"name": "Size", "value": "S"}],
             "is_in_stock": True, "stock_availability": {"text": "2 in stock"}},
        ], "images": [{"src": "https://splendall.test/sizes.jpg"}],
    })

    with pytest.raises(sync_mod.SupplierFetchError) as caught:
        sync_mod.fetch_supplier_variant_stock(product)
    assert caught.value.code == "option_mapping_ambiguous"


def test_variant_uncertainty_leaves_every_option_unchanged_and_creates_warning(iso_catalog, monkeypatch):
    product = _make(
        iso_catalog, name="Mapped shades", supplierId="splendall", supplierSku="shades",
        options=[{"title": "Shade", "values": ["01", "02"]}],
        optionStock={"01": 4, "02": 5}, stock=9,
    )
    warning_map = {}

    def _ambiguous(_product):
        raise sync_mod.SupplierFetchError("Shade 02 missing from supplier reply",
                                          code="option_mapping_ambiguous",
                                          variants=[{"options": [{"name": "Shade", "value": "01"}], "quantity": 2}])
    monkeypatch.setattr(sync_mod, "fetch_supplier_variant_stock", _ambiguous)

    outcome = sync_mod.sync_one(product, warnings=warning_map)

    assert outcome == "uncertain"
    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    assert refreshed["optionStock"] == {"01": 4, "02": 5}
    assert warning_map[product["id"]]["code"] == "option_mapping_ambiguous"
    assert warning_map[product["id"]]["variants"][0]["quantity"] == 2


def test_successful_variant_audit_updates_all_options_atomically_and_clears_warning(iso_catalog, monkeypatch):
    product = _make(
        iso_catalog, name="Mapped sizes", supplierId="splendall", supplierSku="sizes",
        options=[{"title": "Size", "values": ["S", "M"]}],
        optionStock={"S": 8, "M": 8}, stock=16,
    )
    warning_map = {product["id"]: sync_mod._warning_row(product, "old", "old warning")}
    monkeypatch.setattr(sync_mod, "fetch_supplier_variant_stock", lambda p: (
        {"S": 1, "M": 0}, [{"options": [{"name": "Size", "value": "S"}], "quantity": 1}]))

    assert sync_mod.sync_one(product, warnings=warning_map) == "updated"
    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    assert refreshed["optionStock"] == {"S": 1, "M": 0}
    assert refreshed["stock"] == 1
    assert product["id"] not in warning_map


def test_missing_supplier_image_is_an_explicit_uncertainty(monkeypatch):
    monkeypatch.setattr(sync_mod, "_http_get_json", lambda url: [{
        "id": 1, "slug": "no-photo", "name": "No photo", "is_in_stock": True,
        "stock_availability": {"text": "3 in stock"}, "images": [],
    }] if "?" in url else {})
    with pytest.raises(sync_mod.SupplierFetchError) as caught:
        sync_mod.fetch_supplier_quantity("no-photo")
    assert caught.value.code == "missing_supplier_image"


def test_checkout_maximum_is_not_guessed_as_stock_quantity():
    with pytest.raises(sync_mod.SupplierFetchError) as caught:
        sync_mod._quantity_from_row({
            "is_in_stock": True, "is_purchasable": True,
            "stock_availability": {"text": "In stock"},
            "add_to_cart": {"maximum": 5},
        })
    assert caught.value.code == "exact_quantity_unavailable"

def test_official_splendall_target_and_audit_report_are_pinned():
    assert sync_mod.BASE_URL == "https://www.splendall.com"
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "supplier-stock-sync.yml").read_text()
    assert "SPLENDALL_BASE_URL: https://www.splendall.com" in workflow
    assert "--report supplier-sync-report.json" in workflow


# ------------------------------------------------ per-option supplier links


def test_per_option_links_mirror_each_component_independently(iso_catalog, monkeypatch):
    """A variant product whose options each carry their OWN Splendall link
    tracks stock per option: a sold-out component zeroes only itself while the
    other options stay available."""
    product = _make(
        iso_catalog, name="Hair Care Kit", stock=30,
        options=[{"title": "Type", "values": ["Serum", "Shampoo", "Conditioner"]}],
        optionStock={"Serum": 10, "Shampoo": 10, "Conditioner": 10},
        supplierId="splendall", supplierSku="hair-care-kit",
        optionSupplierSku={
            "Type: Serum": "https://www.splendall.com/product/serum/",
            "Type: Shampoo": "https://www.splendall.com/product/shampoo/",
            "Type: Conditioner": "https://www.splendall.com/product/conditioner/",
        })

    def _fetch(url):
        if "shampoo" in url:
            return (0, "supplier reports out of stock")
        if "serum" in url:
            return (4, "4 in stock")
        return (7, "7 in stock")
    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity", _fetch)

    rc = sync_mod.main(["supplier_stock_sync.py", product["id"]])
    assert rc == 0

    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    os_map = refreshed.get("optionStock") or {}
    assert os_map["Serum"] == 4
    assert os_map["Shampoo"] == 0          # only this component went out of stock
    assert os_map["Conditioner"] == 7
    assert catalog_mod.stock_of(refreshed) == 11  # 4 + 0 + 7


def test_unlinked_option_is_left_untouched_and_warned(iso_catalog, monkeypatch):
    """An option with no Splendall link keeps its current quantity and raises a
    non-fatal 'missing supplier link' warning instead of being zeroed."""
    product = _make(
        iso_catalog, name="Partial Kit", stock=20,
        options=[{"title": "Type", "values": ["Serum", "Conditioner"]}],
        optionStock={"Serum": 8, "Conditioner": 5},
        supplierId="splendall", supplierSku="partial-kit",
        optionSupplierSku={"Type: Serum": "https://www.splendall.com/product/serum/"})

    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity",
                        lambda url: (3, "3 in stock"))

    warnings = {}
    result = sync_mod.sync_one(product, warnings=warnings)
    assert result in ("updated", "updated-out-of-stock")

    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    os_map = refreshed.get("optionStock") or {}
    assert os_map["Serum"] == 3           # confirmed from supplier
    assert os_map["Conditioner"] == 5     # unlinked -> preserved

    warn = warnings.get(product["id"])
    assert warn and warn["code"] == "option_link_missing"
    assert "Conditioner" in warn["reason"]


def test_fetch_per_option_helper_reports_all_missing(iso_catalog):
    """When no option has a link the helper fails closed rather than zeroing
    every component."""
    product = {
        "id": "kit", "name": "Kit",
        "options": [{"title": "Type", "values": ["A", "B"]}],
        "optionSupplierSku": {},
    }
    with pytest.raises(sync_mod.SupplierFetchError) as caught:
        sync_mod.fetch_per_option_supplier_stock(product, fetch=lambda u: (1, ""))
    assert caught.value.code == "option_links_missing"


# ------------------------------- consolidated multi-colour variant matching


def test_consolidated_colours_mirror_per_option_link(iso_catalog, monkeypatch):
    """Multiple distinct Splendall URLs (Black, Brown) map into ONE storefront
    product with Colour options: a sold-out colour zeroes only itself."""
    product = _make(
        iso_catalog, name="Men Gift Set", stock=20,
        options=[{"title": "Color", "values": ["Black", "Brown"]}],
        optionStock={"Black": 8, "Brown": 12},
        supplierId="splendall", supplierSku="men-gift-set-black",
        optionSupplierSku={
            "Color: Black": "https://www.splendall.com/product/men-gift-set-black/",
            "Color: Brown": "https://www.splendall.com/product/men-gift-set-brown/",
        })

    def _fetch(url):
        return (0, "supplier reports out of stock") if "black" in url else (5, "5 in stock")
    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity", _fetch)

    rc = sync_mod.main(["supplier_stock_sync.py", product["id"]])
    assert rc == 0
    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    os_map = refreshed.get("optionStock") or {}
    assert os_map["Black"] == 0     # only Black went out of stock
    assert os_map["Brown"] == 5     # Brown stays active/purchasable


def test_custom_option_labels_are_never_overwritten_by_sync(iso_catalog, monkeypatch):
    """A merchant custom colour name ("Mustard Yellow") stays intact after a
    sync run - only its stock number changes, never the label."""
    product = _make(
        iso_catalog, name="Ankara Set", stock=10,
        options=[{"title": "Color", "values": ["Mustard Yellow", "Burnt Orange"]}],
        optionStock={"Mustard Yellow": 5, "Burnt Orange": 5},
        supplierId="splendall", supplierSku="ankara-set",
        optionSupplierSku={
            "Mustard Yellow": "https://www.splendall.com/product/ankara-yellow/",
            "Burnt Orange": "https://www.splendall.com/product/ankara-orange/",
        })
    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity", lambda url: (3, "3 in stock"))

    rc = sync_mod.main(["supplier_stock_sync.py", product["id"]])
    assert rc == 0
    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    labels = (refreshed.get("options") or [{}])[0].get("values") or []
    assert "Mustard Yellow" in labels and "Burnt Orange" in labels
    assert set(refreshed.get("optionStock", {})) == {"Mustard Yellow", "Burnt Orange"}


def test_supplier_basic_colour_infers_onto_custom_storefront_label(iso_catalog, monkeypatch):
    """A link keyed by the supplier's basic colour ("Yellow") is inferred onto
    the storefront's custom "Mustard Yellow" option WITHOUT renaming it."""
    product = _make(
        iso_catalog, name="Scarf", stock=6,
        options=[{"title": "Color", "values": ["Mustard Yellow"]}],
        optionStock={"Mustard Yellow": 6},
        supplierId="splendall", supplierSku="scarf-yellow",
        optionSupplierSku={"Yellow": "https://www.splendall.com/product/scarf-yellow/"})
    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity", lambda url: (0, "out of stock"))

    warnings = {}
    result = sync_mod.sync_one(product, warnings=warnings)
    assert result in ("updated", "updated-out-of-stock")
    refreshed = next(p for p in catalog_mod.merged(include_hidden=True) if p["id"] == product["id"])
    assert refreshed["optionStock"]["Mustard Yellow"] == 0   # inferred + mirrored
    labels = (refreshed.get("options") or [{}])[0].get("values") or []
    assert labels == ["Mustard Yellow"]                       # label untouched
    assert product["id"] not in warnings                      # confident match, no alert


def test_unmatched_supplier_colour_flags_variant_color_mapping(iso_catalog, monkeypatch):
    """A Splendall colour/link that cannot be confidently matched raises the
    ⚠️ Check Variant Color Mapping alert instead of guessing."""
    product = _make(
        iso_catalog, name="Tote", stock=10,
        options=[{"title": "Color", "values": ["Black", "Brown"]}],
        optionStock={"Black": 5, "Brown": 5},
        supplierId="splendall", supplierSku="tote-black",
        optionSupplierSku={
            "Color: Black": "https://www.splendall.com/product/tote-black/",
            "Teal": "https://www.splendall.com/product/tote-teal/",
        })
    monkeypatch.setattr(sync_mod, "fetch_supplier_quantity", lambda url: (4, "4 in stock"))

    warnings = {}
    result = sync_mod.sync_one(product, warnings=warnings)
    assert result in ("updated", "updated-out-of-stock", "unchanged")
    warn = warnings.get(product["id"])
    assert warn and warn["code"] == "variant_color_mapping"
    assert "Check Variant Color Mapping" in warn["reason"]


# ------------------------------------------------ pure inference/match helpers


def test_labels_match_exact_partial_and_none():
    assert sync_mod._labels_match("Black", "black") == "exact"
    assert sync_mod._labels_match("Mustard Yellow", "Yellow") == "partial"
    assert sync_mod._labels_match("Yellow", "Mustard Yellow") == "partial"
    assert sync_mod._labels_match("Black", "Brown") is None
    assert sync_mod._labels_match("", "Black") is None


def test_infer_remaining_option_links_by_token_and_elimination():
    store = ["Black", "Brown", "Mustard Yellow"]
    cand = {"Black": "u_black", "Yellow": "u_yellow", "Brown": "u_brown"}
    out = sync_mod.infer_remaining_option_links(store, cand)
    assert out["links"] == {"Black": "u_black", "Brown": "u_brown",
                            "Mustard Yellow": "u_yellow"}
    assert out["review"] == []


def test_infer_flags_review_when_ambiguous():
    # Two storefront colours, one leftover supplier link that fits neither.
    out = sync_mod.infer_remaining_option_links(
        ["Brown", "Mustard Yellow"], {"Teal": "u_teal"})
    assert out["links"] == {}
    assert set(out["review"]) == {"Brown", "Mustard Yellow"}


def test_infer_does_not_force_match_a_named_leftover_colour():
    # A single leftover NAMED colour that clearly differs is not auto-paired.
    out = sync_mod.infer_remaining_option_links(["Brown"], {"Teal": "u_teal"})
    assert out["links"] == {}
    assert out["review"] == ["Brown"]


def test_infer_positional_elimination_only_for_unlabelled_supplier():
    # An unlabelled/ambiguous leftover ("Variation 2") IS filled in by position.
    out = sync_mod.infer_remaining_option_links(["Brown"], {"Variation 2": "u2"})
    assert out["links"] == {"Brown": "u2"}
    assert out["review"] == []


def test_resolve_option_links_new_supplier_colour_needs_review():
    rows = sync_mod._store_variant_rows(
        {"options": [{"title": "Color", "values": ["Black", "Brown", "Mustard Yellow"]}]})
    links = {"Color: Black": "u_black", "Teal": "u_teal"}
    out = sync_mod.resolve_option_links(rows, links)
    assert out["links"]["Black"] == "u_black"
    assert "Teal" in out["review"]        # new supplier colour with no home
