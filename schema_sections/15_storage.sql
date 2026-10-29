-- SECTION: storage
-- Catalogue assets use uploads; payment evidence uses private receipts.
-- Existing uploads bucket visibility is intentionally preserved here. Run
-- `tools/repair_supabase_storage.py --apply` AFTER this section: it migrates
-- every legacy uploads/proofs object and reference, then safely makes uploads
-- public for product/category/email image URLs.
insert into storage.buckets (id, name, public)
values ('uploads', 'uploads', true)
on conflict (id) do nothing;
update storage.buckets set
  file_size_limit = 52428800,
  allowed_mime_types = array['image/jpeg','image/png','image/webp','image/gif','image/avif','image/heic','video/mp4','video/webm','video/quicktime','application/pdf','application/msword','application/vnd.openxmlformats-officedocument.wordprocessingml.document']
where id = 'uploads';

insert into storage.buckets (id, name, public)
values ('receipts', 'receipts', false)
on conflict (id) do nothing;
update storage.buckets set
  public = false,
  file_size_limit = 52428800,
  allowed_mime_types = array['image/jpeg','image/png','image/webp','image/gif','image/avif','image/heic','application/pdf','application/msword','application/vnd.openxmlformats-officedocument.wordprocessingml.document']
where id = 'receipts';

-- Explicit anonymous read for catalogue assets only. Legacy proofs are
-- excluded until the repair script moves them into the private bucket.
do $$ begin
  create policy "public read uploads" on storage.objects for select to anon, authenticated using (bucket_id = 'uploads' and name not like 'proofs/%');
exception when duplicate_object then null;
end $$;
alter policy "public read uploads" on storage.objects to anon, authenticated
  using (bucket_id = 'uploads' and name not like 'proofs/%');
do $$ begin
  create policy "service role writes uploads" on storage.objects for all to service_role using (bucket_id = 'uploads') with check (bucket_id = 'uploads');
exception when duplicate_object then null;
end $$;
alter policy "service role writes uploads" on storage.objects to service_role
  using (bucket_id = 'uploads') with check (bucket_id = 'uploads');
do $$ begin
  create policy "service role manages receipts" on storage.objects for all to service_role using (bucket_id = 'receipts') with check (bucket_id = 'receipts');
exception when duplicate_object then null;
end $$;
alter policy "service role manages receipts" on storage.objects to service_role
  using (bucket_id = 'receipts') with check (bucket_id = 'receipts');
-- No anon/authenticated receipt-read policy. New proof uploads use
-- receipts/proofs/ and are accessible only through expiring signed URLs.
