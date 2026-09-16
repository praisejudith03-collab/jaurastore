-- Record the public URLs for the six files published under brand/ in the
-- public-assets bucket. favicon-16.png is intentionally not represented in
-- site_settings: the five columns below cover the logo and the browser/iOS
-- icon ladder that the application reads.
--
-- Safe to run repeatedly. It updates only the five brand URL columns on the
-- canonical site_settings row.

update public.site_settings
set
  brand_logo_url = 'https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/public-assets/brand/logo-square.png',
  favicon_32_url = 'https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/public-assets/brand/favicon-32.png',
  favicon_48_url = 'https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/public-assets/brand/favicon-48.png',
  apple_touch_icon_url = 'https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/public-assets/brand/apple-touch-180.png',
  icon_192_url = 'https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/public-assets/brand/icon-192.png'
where id = 1;
