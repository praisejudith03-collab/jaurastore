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

