-- SECTION: receipts
-- ------------------------------------------------------------ receipts
create table if not exists receipts (
  id         text primary key,
  order_id   text,
  name       text,
  phone      text,
  email      text,
  method     text,
  items      text,
  quantity   text,
  amount     text,
  note       text,
  file_url   text,
  file_name  text,
  file_size  bigint,
  file_type  text,
  created_at timestamptz default now()
);
-- Repair an older receipts table. Add-only, preserves all existing receipt
-- rows. Added before the index that uses order_id.
alter table receipts add column if not exists order_id   text;
alter table receipts add column if not exists name       text;
alter table receipts add column if not exists phone      text;
alter table receipts add column if not exists email      text;
alter table receipts add column if not exists method     text;
alter table receipts add column if not exists items      text;
alter table receipts add column if not exists quantity   text;
alter table receipts add column if not exists amount     text;
alter table receipts add column if not exists note       text;
alter table receipts add column if not exists file_url   text;
alter table receipts add column if not exists file_name  text;
alter table receipts add column if not exists file_size  bigint;
alter table receipts add column if not exists file_type  text;
alter table receipts add column if not exists created_at timestamptz default now();
create index if not exists idx_receipts_order on receipts (order_id);

