-- SECTION: brand_assets
-- -------------------------------------------------------- brand assets
-- The brand logo and the favicon ladder (32, 48, 180, 192) published to the
-- Supabase Storage bucket `public-assets` by tools/upload_brand_assets.py.
--
-- The HTML <head> carries the same public URLs as literals, because an icon
-- has to resolve before any JavaScript runs and Google's site-icon crawler
-- does not execute scripts. These columns are the DATABASE RECORD of what is
-- live: what the Admin portal reads, what a later re-upload compares against,
-- and what js/store.js prefers for the Organization / og:logo mark.
--
-- Add-only and idempotent, like every other repair in this file: an existing
-- row keeps every value it already has.
alter table site_settings add column if not exists brand_logo_url       text not null default '';
alter table site_settings add column if not exists favicon_32_url       text not null default '';
alter table site_settings add column if not exists favicon_48_url       text not null default '';
alter table site_settings add column if not exists apple_touch_icon_url text not null default '';
alter table site_settings add column if not exists icon_192_url         text not null default '';

-- The `public-assets` bucket itself is NOT created here. This file owns
-- exactly one Storage bucket - `uploads`, for user content (section 15) - and
-- that is an invariant the tests enforce, because a second content bucket is
-- what the retired `receipts` bucket was and how a payment proof once ended
-- up unreachable. The brand bucket holds no user content and is created,
-- public and idempotently, by tools/upload_brand_assets.py on its first run:
--
--   export SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=...
--   python3 tools/upload_brand_assets.py
--
-- Public read is the point (browsers and Googlebot fetch a favicon
-- anonymously); writes need the service-role key, which only the server and
-- that tool hold.
