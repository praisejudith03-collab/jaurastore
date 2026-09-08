-- SECTION: storage
-- Storage is provisioned once in Dashboard or with this statement. The service
-- role is used only server-side; public objects are safe to render directly.
insert into storage.buckets (id, name, public)
values ('uploads', 'uploads', true)
on conflict (id) do nothing;
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

