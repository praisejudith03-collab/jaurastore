-- Create the public, read-only bucket used by the crawlable logo and favicon
-- ladder. The service-role-only upload workflow is the only writer.
--
-- Safe to run repeatedly in the Supabase SQL editor. Existing bucket
-- contents are never removed.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'public-assets',
  'public-assets',
  true,
  10485760,
  array['image/png']::text[]
)
on conflict (id) do update set
  name = excluded.name,
  public = true,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;
