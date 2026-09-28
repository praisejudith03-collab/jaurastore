-- Run once in the Supabase SQL editor. Idempotent and add-only.
-- SECTION: site_settings_social
-- Owner-editable social media links (Admin -> Settings -> "Social media links").
-- Empty values keep the storefront's built-in default for that platform.
alter table site_settings add column if not exists social_whatsapp_url  text not null default '';
alter table site_settings add column if not exists social_instagram_url text not null default '';
alter table site_settings add column if not exists social_tiktok_url    text not null default '';
alter table site_settings add column if not exists social_facebook_url  text not null default '';
