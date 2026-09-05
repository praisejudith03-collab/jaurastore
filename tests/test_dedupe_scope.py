"""Dedupe scope: a Supabase row must never hide a DIFFERENT product.

The live defect (admin catalogue count 228 instead of 258): _dedupe_products
matched by id, then slug, then sku across ALL sources, so ~30 seed products
that share a slug or sku with a mirrored Supabase row of a different product
went invisible. Reconciliation is id-only now; the only remaining collapse is
a re-created product - different id, SAME name plus the same slug or sku -
which must still render exactly once.

Run with:  python3 -m pytest tests/test_dedupe_scope.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import catalog as catalog_mod  # noqa: E402


def _sb_row(**over):
    row = {"id": "jau-sb-other", "sku": "JAUBOTH", "slug": "some-other-piece",
           "name": "A Different Piece Entirely", "category": "beauty",
           "priceNgn": 2000, "online": True}
    row.update(over)
    return row


def test_seed_product_visible_despite_slug_clash_with_different_supabase_row(monkeypatch):
    """(a) slug clash, different product: the seed row stays visible and the
    catalogue count is preserved (seed count + the one Supabase row)."""
    seed = catalog_mod._seed_products()
    victim = seed[0]
    sb = [_sb_row(id="jau-sb-other", slug=victim["slug"])]
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: sb)
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [], "deleted": []})
    merged = catalog_mod.merged(include_hidden=True)
    ids = [p["id"] for p in merged]
    assert victim["id"] in ids, \
        f"a slug clash with a DIFFERENT Supabase product hid {victim['name']!r}"
    assert "jau-sb-other" in ids, "the Supabase row itself must stay visible"
    assert len(merged) == len(seed) + 1, \
        f"catalogue count lost to a slug clash: expected {len(seed) + 1}, got {len(merged)}"


def test_seed_product_visible_despite_sku_clash_with_different_supabase_row(monkeypatch):
    """(a) sku clash, different product: same guarantee via the sku path."""
    seed = catalog_mod._seed_products()
    victim = next(p for p in seed if p.get("sku"))
    sb = [_sb_row(id="jau-sb-other", sku=victim["sku"])]
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: sb)
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [], "deleted": []})
    merged = catalog_mod.merged(include_hidden=True)
    ids = [p["id"] for p in merged]
    assert victim["id"] in ids, \
        f"a sku clash with a DIFFERENT Supabase product hid {victim['name']!r}"
    assert "jau-sb-other" in ids
    assert len(merged) == len(seed) + 1


def test_recreated_product_renders_exactly_once(monkeypatch):
    """(b) re-created product: different id, same name+slug (and sku) - the
    old collapse the shop relies on. It must render exactly once, and the
    live Supabase copy must be the one kept."""
    seed = catalog_mod._seed_products()
    victim = seed[0]
    sb = [_sb_row(id="jau-recreated", name=victim["name"], slug=victim["slug"],
                  sku=victim["sku"])]
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: sb)
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": [], "deleted": []})
    merged = catalog_mod.merged(include_hidden=True)
    same = [p for p in merged if p.get("slug") == victim["slug"]]
    assert len(same) == 1, \
        f"a re-created product must render exactly once, rendered {len(same)} times"
    assert same[0]["id"] == "jau-recreated", "the live Supabase copy must win"
    assert len(merged) == len(seed), \
        f"a recreation is not a new product: expected {len(seed)}, got {len(merged)}"


def test_recreated_product_in_local_overrides_renders_exactly_once(monkeypatch):
    """The same guarantee for the local-override union (a phone save whose
    recreation has not reached Supabase yet)."""
    seed = catalog_mod._seed_products()
    victim = seed[0]
    sb = [_sb_row()]
    rec = dict(victim)
    rec["id"] = "jau-recreated-local"
    local = [rec]
    monkeypatch.setattr(catalog_mod, "_supabase_products", lambda: sb)
    monkeypatch.setattr(catalog_mod, "overrides",
                        lambda: {"products": local, "deleted": []})
    merged = catalog_mod.merged(include_hidden=True)
    same = [p for p in merged if p.get("slug") == victim["slug"]]
    assert len(same) == 1, \
        f"a re-created product must render exactly once, rendered {len(same)} times"
    assert len(merged) == len(seed) + 1, \
        "the re-creation must not add a second copy to the catalogue count"


# ------------------------------------------------ _dedupe_products, directly
def _row(pid, name, slug, sku=""):
    return {"id": pid, "name": name, "slug": slug, "sku": sku}


def test_dedupe_slug_clash_different_name_keeps_both():
    a = _row("1", "Foam roller", "foam-roller", "F1")
    b = _row("2", "Foam roller bag", "foam-roller", "F2")
    out = catalog_mod._dedupe_products([b], [a])
    assert {p["id"] for p in out} == {"1", "2"}, \
        "a slug clash with a different product must not hide either row"


def test_dedupe_sku_clash_different_name_keeps_both():
    a = _row("1", "Ankara dress", "ankara-dress", "A1")
    b = _row("2", "Ankara fabric", "ankara-fabric", "A1")
    out = catalog_mod._dedupe_products([b], [a])
    assert {p["id"] for p in out} == {"1", "2"}, \
        "a sku clash with a different product must not hide either row"


def test_dedupe_same_name_and_slug_collapse_to_primary():
    a = _row("1", "Heels", "heels", "H1")
    b = _row("2", "Heels", "heels", "H1")
    out = catalog_mod._dedupe_products([b], [a])
    assert [p["id"] for p in out] == ["2"], \
        "a re-created product (same name+slug, new id) renders exactly once"


def test_dedupe_same_name_and_sku_collapse_to_primary():
    a = _row("1", "Skull cap", "skull-cap-1", "S1")
    b = _row("2", "Skull cap", "skull-cap-2", "S1")
    out = catalog_mod._dedupe_products([b], [a])
    assert [p["id"] for p in out] == ["2"]


def test_dedupe_same_id_primary_wins():
    a = _row("1", "Heels", "heels")
    b = _row("1", "Heels (edited)", "heels")
    out = catalog_mod._dedupe_products([b], [a])
    assert [p["id"] for p in out] == ["1"]
    assert out[0]["name"] == "Heels (edited)"


def test_dedupe_seed_internal_collapse_scoped_to_same_name():
    """The seed's own internal collapse is preserved, scoped to rows sharing
    the same NAME: two seed rows that are a re-creation of each other render
    once; different products with a shared slug/sku do not."""
    seed_rows = [
        _row("wix-a", "Press on nail", "press-on-nail", "P1"),
        _row("wix-b", "Press on nail", "press-on-nail", "P1"),   # re-creation
        _row("wix-c", "Press on nail glue", "press-on-nail", "P2"),  # different piece
    ]
    out = catalog_mod._dedupe_products([], seed_rows)
    ids = [p["id"] for p in out]
    assert ids.count("wix-a") + ids.count("wix-b") == 1, \
        "the re-created seed rows must collapse to one"
    assert "wix-c" in ids, \
        "a different seed product must not be hidden by a shared slug"
