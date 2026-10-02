"""Readable public product links without changing internal product IDs."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import catalog  # noqa: E402


def test_import_slug_is_replaced_but_product_id_and_legacy_alias_are_preserved():
    row = catalog.normalize({
        "id": "jau-canonical-tote-1",
        "legacyId": "wix-old-tote-003",
        "slug": "wix-old-tote-003",
        "name": "Coastal Tote Bag",
        "priceNgn": 2500,
        "stock": 2,
    })

    assert row["id"] == "jau-canonical-tote-1"
    assert row["legacyId"] == "wix-old-tote-003"
    assert row["slug"] == "coastal-tote-bag"
    assert "wix" not in catalog.public_slug(row).lower()
    index = catalog.product_index(products=[row])
    assert index["jau-canonical-tote-1"] is row
    assert index["wix-old-tote-003"] is row


def test_seed_public_slugs_are_readable_unique_and_never_the_internal_id():
    rows = json.loads((__import__("pathlib").Path(ROOT) / "data/seed.json").read_text(encoding="utf-8"))
    slugs = [catalog.public_slug(row) for row in rows]
    assert len(slugs) == len(set(slugs))
    for row, slug in zip(rows, slugs):
        assert slug
        assert slug != str(row.get("id") or "").lower()
        assert not slug.startswith(("wix-", "shopify-", "import-"))
        assert all(ch.isalnum() or ch == "-" for ch in slug)


def test_public_slug_collision_fallback_never_exposes_an_internal_wix_id():
    slug = catalog._free_slug("same-name", "wix-secret-123", {"same-name", *{
        f"same-name-{n}" for n in range(2, 42)
    }})
    assert slug.startswith("same-name-jau-")
    assert "wix" not in slug.lower()
