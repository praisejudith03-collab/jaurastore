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
create index if not exists coupon_uses_code on coupon_uses(code, used_at desc);

