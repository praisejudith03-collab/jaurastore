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
  emailed    boolean default false,
  email_info text,
  created_at timestamptz default now()
);
create index if not exists idx_receipts_order on receipts (order_id);

