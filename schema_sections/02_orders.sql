-- SECTION: orders
-- -------------------------------------------------------------- orders
create table if not exists orders (
  id            text primary key,
  email         text,
  customer_name text,
  phone         text,
  country       text,
  city          text,
  zone          text,
  address       text,
  note          text,
  payment       text,
  proof_url     text,
  items_count   integer default 0,
  total         numeric,
  currency      text,
  source        text default 'web',
  status        text default 'pending',
  payload       jsonb,
  at            timestamptz,
  updated_at    timestamptz default now(),
  customer_user_id text
);
-- Repair an older orders table that may be narrower. Add-only, idempotent,
-- preserves every existing order row, ids and values. Columns are added
-- nullable (or with a harmless default) so legacy rows are not given invented
-- data, and they are added before the indexes that reference them.
alter table orders add column if not exists email         text;
alter table orders add column if not exists customer_name text;
alter table orders add column if not exists phone         text;
alter table orders add column if not exists country       text;
alter table orders add column if not exists city          text;
alter table orders add column if not exists zone          text;
alter table orders add column if not exists address       text;
alter table orders add column if not exists note          text;
alter table orders add column if not exists payment       text;
alter table orders add column if not exists proof_url     text;
alter table orders add column if not exists items_count   integer default 0;
alter table orders add column if not exists total         numeric;
alter table orders add column if not exists currency      text;
alter table orders add column if not exists source        text default 'web';
alter table orders add column if not exists status        text default 'pending';
alter table orders add column if not exists payload       jsonb;
alter table orders add column if not exists at            timestamptz;
alter table orders add column if not exists updated_at    timestamptz default now();
alter table orders add column if not exists customer_user_id text;
create index if not exists idx_orders_at on orders (at desc);
create index if not exists idx_orders_status on orders (status);
create index if not exists idx_orders_customer on orders (customer_user_id);

