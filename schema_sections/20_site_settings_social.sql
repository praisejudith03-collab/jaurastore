-- SECTION: site_settings_social
-- Owner-editable social media links (Admin -> Settings -> "Social media
-- links"). Add-only and idempotent, like every other section here.
-- An empty value means "keep the storefront's built-in default for that
-- platform"; the logo beside each link is detected from the address itself,
-- so Facebook is rendered exactly like Instagram, TikTok and WhatsApp.
alter table site_settings add column if not exists social_whatsapp_url  text not null default '';
alter table site_settings add column if not exists social_instagram_url text not null default '';
alter table site_settings add column if not exists social_tiktok_url    text not null default '';
alter table site_settings add column if not exists social_facebook_url  text not null default '';
