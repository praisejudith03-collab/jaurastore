-- SECTION: referrals
-- ------------------------------------------------- referral programme
create table if not exists referral_codes (
  code          text primary key,
  email         text not null,
  name          text,
  uses          integer default 0,
  reward_issued integer default 0,
  reward_coupon text,
  created_at    timestamptz default now()
);
-- Repair older referral_codes tables. Add-only, preserves existing codes.
alter table referral_codes add column if not exists email         text;
alter table referral_codes add column if not exists name          text;
alter table referral_codes add column if not exists uses          integer default 0;
alter table referral_codes add column if not exists reward_issued integer default 0;
alter table referral_codes add column if not exists reward_coupon text;
alter table referral_codes add column if not exists created_at    timestamptz default now();
create index if not exists idx_referral_email on referral_codes (email);

-- one row per successful purchase made with a referral code
create table if not exists referral_uses (
  id          bigint generated always as identity primary key,
  code        text not null,
  order_id    text,
  buyer_email text,
  at          timestamptz default now()
);
-- Repair older referral_uses tables. Add-only, before the index.
alter table referral_uses add column if not exists code        text;
alter table referral_uses add column if not exists order_id    text;
alter table referral_uses add column if not exists buyer_email text;
alter table referral_uses add column if not exists at          timestamptz default now();
create index if not exists idx_referral_uses_code on referral_uses (code);

