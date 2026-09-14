-- SECTION: storage
-- Storage is provisioned once in Dashboard or with this statement. The service
-- role is used only server-side; public objects are safe to render directly.
-- The application uses exactly one bucket: uploads. Never a receipts bucket.
-- This statement is idempotent and never changes an existing bucket's
-- visibility: if uploads already exists its public flag is preserved, and no
-- existing bucket or object is deleted or modified. Receipts are rows in the
-- receipts table and their files live under uploads/proofs/... . No
-- SUPABASE_PRIVATE_BUCKET handling is required.
insert into storage.buckets (id, name, public)
values ('uploads', 'uploads', true)
on conflict (id) do nothing;
-- Raise an existing or newly-created bucket to the application's advertised
-- 50 MB video limit while retaining its existing public/private choice.
update storage.buckets set
  file_size_limit = 52428800,
  allowed_mime_types = array['image/jpeg','image/png','image/webp','image/gif','image/avif','image/heic','video/mp4','video/webm','video/quicktime','application/pdf','application/msword','application/vnd.openxmlformats-officedocument.wordprocessingml.document']
where id = 'uploads';
-- Idempotent policies: do not create duplicates if they already exist.
do $$ begin
  create policy "public read uploads" on storage.objects for select using (bucket_id = 'uploads');
exception when duplicate_object then null;
end $$;
do $$ begin
  create policy "service role writes uploads" on storage.objects for all to service_role using (bucket_id = 'uploads') with check (bucket_id = 'uploads');
exception when duplicate_object then null;
end $$;
alter policy "service role writes uploads" on storage.objects to service_role
  using (bucket_id = 'uploads') with check (bucket_id = 'uploads');

-- Proofs share uploads/proofs/<date>/<hash>-<128-bit-token>.<ext>.
-- uploads is PUBLIC: signed links do not make objects private.
-- This schema does not delete any existing bucket or stored object.

