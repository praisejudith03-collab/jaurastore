-- SECTION: site_settings
-- ===================================================== Jaura production tables
-- These are the source of truth for runtime configuration.
create table if not exists site_settings (
  id bigint primary key check (id = 1),
  bank_name text not null default '',
  account_number text not null default '',
  account_name text not null default '',
  referral_commission_percentage numeric(5,2) not null default 0 check (referral_commission_percentage between 0 and 100),
  hero_banner_title text not null default '',
  hero_banner_subtitle text not null default '',
  contact_email text not null default '',
  contact_phone text not null default '',
  site_logo_url text not null default '',
  hero_video_url text not null default '',
  hero_poster_url text not null default '',
  hero_doc_url text not null default '',
  shop_banner_url text not null default '',
  shipping_note text not null default '',
  banner_from text not null default '',
  banner_to text not null default '',
  conv_banner text not null default '',
  conv_bold text not null default '',
  updated_at timestamptz not null default now()
);
insert into site_settings (id) values (1) on conflict (id) do nothing;
-- Repair an older site_settings table that predates newer columns. Add-only,
-- preserves the single id=1 row and all Admin-edited settings.
alter table site_settings add column if not exists bank_name text not null default '';
alter table site_settings add column if not exists account_number text not null default '';
alter table site_settings add column if not exists account_name text not null default '';
alter table site_settings add column if not exists referral_commission_percentage numeric(5,2) not null default 0;
alter table site_settings add column if not exists hero_banner_title text not null default '';
alter table site_settings add column if not exists hero_banner_subtitle text not null default '';
alter table site_settings add column if not exists contact_email text not null default '';
alter table site_settings add column if not exists contact_phone text not null default '';
alter table site_settings add column if not exists site_logo_url text not null default '';
alter table site_settings add column if not exists hero_video_url text not null default '';
alter table site_settings add column if not exists hero_poster_url text not null default '';
alter table site_settings add column if not exists hero_doc_url text not null default '';
alter table site_settings add column if not exists shop_banner_url text not null default '';
alter table site_settings add column if not exists shipping_note text not null default '';
alter table site_settings add column if not exists banner_from text not null default '';
alter table site_settings add column if not exists banner_to text not null default '';
alter table site_settings add column if not exists conv_banner text not null default '';
alter table site_settings add column if not exists conv_bold text not null default '';
alter table site_settings add column if not exists updated_at timestamptz not null default now();

-- Payment details shown at checkout. These were hardcoded in checkout.html /
-- js/app.js (bank "UBA", account 23474678931, the MoMo Benin and Moov Togo
-- numbers) which meant changing an account number needed a redeploy and the
-- values were visible in the shipped bundle. They are now columns on the
-- id=1 site_settings row, served by GET /api/site and edited from the Admin
-- Portal, so the storefront never carries a payment fallback.
alter table site_settings add column if not exists cfa_payment_provider     text not null default '';
alter table site_settings add column if not exists cfa_payment_name         text not null default '';
alter table site_settings add column if not exists cfa_payment_account      text not null default '';
alter table site_settings add column if not exists cfa_payment_instructions text not null default '';
alter table site_settings add column if not exists togo_payment_provider     text not null default '';
alter table site_settings add column if not exists togo_payment_name         text not null default '';
alter table site_settings add column if not exists togo_payment_account      text not null default '';
alter table site_settings add column if not exists togo_payment_instructions text not null default '';
alter table site_settings add column if not exists naira_payment_bank         text not null default '';
alter table site_settings add column if not exists naira_payment_name         text not null default '';
alter table site_settings add column if not exists naira_payment_account      text not null default '';
alter table site_settings add column if not exists naira_payment_instructions text not null default '';

