-- SECTION: delivery_zones
-- Delivery zones and their fare ranges. Admin-editable, served to the
-- storefront by GET /api/site so checkout no longer hardcodes the list.
-- The fare is a RANGE because transport varies with weight; the exact figure
-- is agreed with the customer after payment. `kind`:
--   delivery = a published min..max range in `currency`
--   pickup   = free collection, no fare
--   quote    = fare agreed per order, no range published
create table if not exists delivery_zones (
  id          text primary key,
  name        text not null unique,
  currency    text not null default 'CFA' check (currency in ('CFA','NGN')),
  fare_min    integer not null default 0 check (fare_min >= 0),
  fare_max    integer not null default 0 check (fare_max >= 0),
  kind        text not null default 'delivery'
                check (kind in ('delivery','pickup','quote')),
  active      boolean not null default true,
  sort_order  integer not null default 0,
  note        text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
-- REPAIR an older delivery_zones table that predates these columns. A hand-made
-- or earlier table can be missing `active` / `sort_order`, and then the index
-- and the seed below fail with "column does not exist". Every statement is
-- add-only and idempotent: existing rows keep their ids, names, fares, active
-- and sort_order values, and no row is ever rewritten, dropped or removed.
alter table delivery_zones add column if not exists name        text;
alter table delivery_zones add column if not exists currency    text not null default 'CFA';
alter table delivery_zones add column if not exists fare_min    integer not null default 0;
alter table delivery_zones add column if not exists fare_max    integer not null default 0;
alter table delivery_zones add column if not exists kind        text not null default 'delivery';
alter table delivery_zones add column if not exists active      boolean not null default true;
alter table delivery_zones add column if not exists sort_order  integer not null default 0;
alter table delivery_zones add column if not exists note        text;
alter table delivery_zones add column if not exists created_at  timestamptz not null default now();
alter table delivery_zones add column if not exists updated_at  timestamptz not null default now();
-- `name` is added nullable on purpose: a legacy row must keep whatever name it
-- already has, and a row without one is left for a human to name rather than
-- being given an invented value here.
create unique index if not exists delivery_zones_name
  on delivery_zones(name) where name is not null;
create index if not exists delivery_zones_active on delivery_zones(active, sort_order);

