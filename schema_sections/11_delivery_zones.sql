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
create index if not exists delivery_zones_active on delivery_zones(active, sort_order);

