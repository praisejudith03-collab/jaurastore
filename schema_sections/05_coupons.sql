-- SECTION: coupons
-- ------------------------------------------------------------- coupons
create table if not exists coupons (
  code       text primary key,
  percent    integer not null,
  kind       text default 'manual',       -- manual | reward
  email      text,
  note       text,
  active     integer default 1,
  max_uses   integer,
  uses       integer default 0,
  expires_at text,
  created_at timestamptz default now()
);
-- Repair older coupons tables. Add-only, idempotent, preserves existing rows.
alter table coupons add column if not exists percent    integer;
alter table coupons add column if not exists kind       text default 'manual';
alter table coupons add column if not exists email      text;
alter table coupons add column if not exists note       text;
alter table coupons add column if not exists active     integer default 1;
alter table coupons add column if not exists max_uses   integer;
alter table coupons add column if not exists uses       integer default 0;
alter table coupons add column if not exists expires_at text;
alter table coupons add column if not exists created_at timestamptz default now();

