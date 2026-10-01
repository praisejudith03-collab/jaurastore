-- Run once in the Supabase SQL editor. Idempotent and add-only.
-- SECTION: site_settings_welcome
-- Owner-editable storefront welcome pop-up. Empty values retain built-in defaults.
-- The boolean is the canonical ON/OFF switch. welcome_enabled is retained for
-- a safe rollout to storefront bundles that predate popup_banner_active.
alter table site_settings add column if not exists popup_banner_active boolean not null default true;
alter table site_settings add column if not exists welcome_enabled      text not null default '';
alter table site_settings add column if not exists welcome_title        text not null default '';
alter table site_settings add column if not exists welcome_title_fr     text not null default '';
alter table site_settings add column if not exists welcome_body         text not null default '';
alter table site_settings add column if not exists welcome_body_fr      text not null default '';
alter table site_settings add column if not exists welcome_image_url    text not null default '';
alter table site_settings add column if not exists welcome_cta_label    text not null default '';
alter table site_settings add column if not exists welcome_cta_label_fr text not null default '';
alter table site_settings add column if not exists welcome_cta_href     text not null default '';
