-- SECTION: coupon_redemptions
-- Coupon redemption log. `coupons.uses` is a counter and stays the fast path
-- for the max_uses check, but a counter cannot answer "which order used this
-- code" and cannot stop a retried order from counting twice. The unique pair
-- (code, order_id) makes a redemption idempotent, so a confirm/retry loop
-- cannot inflate the usage count.
create table if not exists coupon_uses (
  id        bigint generated always as identity primary key,
  code      text not null,
  email     text,
  order_id  text not null,
  percent   integer,
  used_at   timestamptz not null default now(),
  unique (code, order_id)
);
-- REPAIR an older coupon_uses table that predates required columns. An
-- existing table hand-built with fewer columns would otherwise fail on
-- inserts and on the indexes below with "column does not exist". Every
-- statement is add-only and idempotent: existing rows keep their ids and
-- values, no row is rewritten, dropped or given an invented coupon code or
-- order id. code and order_id are added nullable so legacy rows without them
-- are preserved and can be left for human review rather than being given
-- fake values here.
alter table coupon_uses add column if not exists code     text;
alter table coupon_uses add column if not exists email    text;
alter table coupon_uses add column if not exists order_id text;
alter table coupon_uses add column if not exists percent  integer;
alter table coupon_uses add column if not exists used_at  timestamptz default now();
-- Preserve idempotency for any table that already exists without the unique
-- pair. The constraint in the create-table above only applies to fresh tables;
-- an older table needs this index added separately. It is a unique index on
-- (code, order_id) so a retry cannot insert the same order twice.
create unique index if not exists coupon_uses_code_order on coupon_uses(code, order_id);
create index if not exists coupon_uses_code on coupon_uses(code, used_at desc);

