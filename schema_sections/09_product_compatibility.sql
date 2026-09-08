-- SECTION: product_compatibility
-- Legacy id alias. A product's `id` is its primary key and is NEVER renamed
-- while orders, reviews, carts or analytics still reference it. When a row is
-- eventually given a canonical jau-* id, the previous wix-* id is copied here
-- so old product links, order lines, reviews and cart entries keep resolving
-- through catalog.product_index() / supabase_store.product_by_id(). It stays
-- NULL for rows that were created with a canonical id and have no history.
alter table products add column if not exists "legacyId" text;
create unique index if not exists products_legacy_id_key
  on products ("legacyId") where "legacyId" is not null;

alter table products add column if not exists image_url text;
alter table products add column if not exists stock_quantity integer not null default 0;
do $$ begin
  alter table products add constraint products_stock_nonnegative check (stock_quantity >= 0) not valid;
exception when duplicate_object then null;
end $$;

