"""Regression coverage for the public catalogue's write/read sync."""
import threading

import product_merge
import supabase_store


def test_inflight_read_cannot_repopulate_cache_after_write(monkeypatch):
    monkeypatch.setattr(supabase_store, "_read_cache_enabled", lambda: True)
    supabase_store.invalidate_read_cache()
    started = threading.Event()
    release = threading.Event()

    def old_read():
        started.set()
        assert release.wait(5)
        return [{"id": "old"}]

    thread = threading.Thread(target=lambda: supabase_store.cached_read("products_rows", old_read))
    thread.start()
    try:
        assert started.wait(5)
        supabase_store.invalidate_read_cache("products_rows")
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert supabase_store.cached_read("products_rows", lambda: [{"id": "new"}]) == [{"id": "new"}]
    finally:
        release.set()
        thread.join(5)
        supabase_store.invalidate_read_cache()


def test_category_id_is_merged_with_concurrent_category_changes():
    base = {"id": "a", "category": "beauty", "category_id": "beauty", "name": "Item"}
    stored = dict(base, category="bags", category_id="bags")
    edited = dict(base, name="New name")
    merged, _ = product_merge.merge_product_edit(base, edited, stored, dirty=["name"])
    assert merged["category"] == merged["category_id"] == "bags"
    edited = dict(base, category="shoes", category_id="shoes")
    merged, _ = product_merge.merge_product_edit(base, edited, stored, dirty=["category", "category_id"])
    assert merged["category"] == merged["category_id"] == "shoes"
