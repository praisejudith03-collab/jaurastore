-- SECTION: site_settings_welcome
-- Owner-editable storefront welcome pop-up. Empty values retain built-in defaults.
alter table site_settings add column if not exists welcome_enabled      text not null default '';
alter table site_settings add column if not exists welcome_title        text not null default '';
alter table site_settings add column if not exists welcome_title_fr     text not null default '';
alter table site_settings add column if not exists welcome_body         text not null default '';
alter table site_settings add column if not exists welcome_body_fr      text not null default '';
alter table site_settings add column if not exists welcome_image_url    text not null default '';
alter table site_settings add column if not exists welcome_cta_label    text not null default '';
alter table site_settings add column if not exists welcome_cta_label_fr text not null default '';
alter table site_settings add column if not exists welcome_cta_href     text not null default '';
