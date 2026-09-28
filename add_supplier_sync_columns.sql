-- Run once in the Supabase SQL editor. Idempotent and add-only.
-- SECTION: products
-- Supplier stock sync columns (owner request 2026-09-28, splendall.com).
-- Optional supplier mapping used by tools/supplier_stock_sync.py to mirror a
-- named supplier's exact stock quantity onto a product. Both columns default
-- to '' (blank), which is how every existing/product-added-by-hand row stays
-- forever: the sync script only ever touches a row where "supplierId" is a
-- known supplier name (e.g. 'splendall') AND "supplierSku" is non-empty, so
-- leaving these blank is what keeps a hand-stocked item (an Ankara waist
-- piece, or anything else the admin didn't explicitly map) untouched.
alter table products add column if not exists "supplierId"  text not null default '';
alter table products add column if not exists "supplierSku" text not null default '';
