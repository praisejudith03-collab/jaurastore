"""Hot-path indexes for the storefront and the owner dashboards."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_products_read_path_is_indexed():
    schema = (ROOT / "supabase_schema.sql").read_text(encoding="utf-8")
    for name in ("products_online_live_idx", "products_category_live_idx",
                 "products_deleted_idx", "products_updated_idx"):
        assert name in schema, name
    # The partial index must match merged()'s predicate, or Postgres ignores it.
    assert "where is_deleted is not true" in schema


def test_owner_order_lists_have_a_status_and_date_composite_index():
    schema = (ROOT / "supabase_schema.sql").read_text(encoding="utf-8")
    assert "idx_orders_status_at on orders (status, at desc)" in schema


def test_the_mobile_pasteable_product_sections_stay_under_the_size_limit():
    for name in ("09_product_compatibility.sql",):
        path = ROOT / "schema_sections" / name
        assert path.stat().st_size < 6000, path
